#!/usr/bin/env python3
"""y700 speaker daemon: plays a continuous PCM stream (s16le, 2 ch, 48 kHz, interleaved) from a FIFO on the speakers.
The session's PipeWire writes into the FIFO through libpipewire-module-pipe-tunnel (sink "Y700 Speakers"); games reach
PipeWire through pipewire-pulse (x86 libpulse under FEX) or the native protocol.

DSP path = the verified audio-d5 sequence (boot 3a1d6dac / 8e18ad1f):
  BE pcmC0D14p (MI2S-LPAIF-RX-PRIMARY) S32_LE/2ch/48k open + HW_PARAMS -> dma-buf + ION MAP + APM SHARED_MEM_MAP (offset mode)
  -> GRAPH_OPEN / SET_CFG (out-of-band) [WR_SHARED_MEM_EP -> PCM_CNV (32 bit) | I2S_SINK LPAIF primary SD1]
  -> PREPARE -> START -> data -> BE PREPARE after the first buffers (aw882xx start_pa needs clocks and signal)
The graph is opened when data arrives and closed after IDLE_SECS without data (or SILENT_SECS of digital silence).
Gain: fixed software attenuation GAIN (no Awinic speaker protection algorithm in this graph -> keep headroom).

Needs root (audio-pkt / msm_audio_ion / dma_heap / PCM nodes). Requires the audio stages (qrtr-smd, adsp, audio-c1, audio-c).
Usage: sudo python3 -B speakerd.py [--gain 0.5] [--fifo /run/y700-desktop/y700-speaker.fifo]
       speakerd.py --test   (host test of the FIFO reader / gain / backlog logic, no device access)"""
import array, fcntl, mmap, os, select, signal, stat, struct, sys, termios, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

FIFO = Path('/run/y700-desktop/y700-speaker.fifo')              # created by the session's pipe-tunnel sink (siwal)
OWNER = 'siwal'
LOG = Path('/home/siwal/y700-agent')
PCM = 'pcmC0D14p'
BE_FMT, DEV_BITS = 'S32_LE', 32                       # 6a1d59ef: amps start only with 32-bit slots (64 fs)
SIZE, CMD_AREA = 64 * 1024, 8192                      # shared memory: 8 KiB command area, then audio slots
PERIOD, INFLIGHT = 4096, 4                            # 1024 frames = 21.3 ms per buffer, <= 85 ms queued in the DSP
BYTES_PER_SEC = 48000 * 4
IDLE_SECS, SILENT_SECS = 2.0, 30.0
BACKLOG_MAX, BACKLOG_KEEP = 32768, 8192               # FIFO backlog above ~170 ms is dropped down to ~43 ms

def log(*a):
    line = '%s %s' % (time.strftime('%H:%M:%S'), ' '.join(str(x) for x in a)); print(line, flush=True)
    try:
        with (LOG/('speakerd-%s.log' % Path('/proc/sys/kernel/random/boot_id').read_text()[:8])).open('a') as f: f.write(line + '\n')
    except OSError: pass

def scale(chunk, gain):
    """s16le samples * gain (0..1), truncating toward zero; gain 1 returns the input."""
    if gain >= 1.0: return chunk
    a = array.array('h'); a.frombytes(chunk); g = int(gain * 32768)
    return array.array('h', [x * g >> 15 for x in a]).tobytes()

class Fifo:
    """Reader side of the PipeWire pipe tunnel (the FIFO lives in siwal's runtime dir and is created by PipeWire).
    Opened without following symlinks and checked to be a FIFO owned by owner_uid. A second (write) descriptor keeps
    the pipe from ever reporting EOF when the writer goes away, so select() only wakes for real data."""
    def __init__(self, path, owner_uid=None):
        self.path = Path(path)
        self.rfd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        st = os.fstat(self.rfd)
        if not stat.S_ISFIFO(st.st_mode) or (owner_uid is not None and st.st_uid != owner_uid):
            os.close(self.rfd); raise RuntimeError('%s is not a FIFO owned by uid %s' % (self.path, owner_uid))
        self.ino = (st.st_dev, st.st_ino)
        self.wfd = os.open('/proc/self/fd/%d' % self.rfd, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.odd = b''                                                 # keeps reads frame aligned (4 bytes)
    def stale(self):
        """True when the path no longer names the FIFO we hold (the session re-created it)."""
        try: st = os.lstat(self.path); return (st.st_dev, st.st_ino) != self.ino
        except FileNotFoundError: return True
    def close(self): os.close(self.rfd); os.close(self.wfd)
    def pending(self):
        b = bytearray(4); fcntl.ioctl(self.rfd, termios.FIONREAD, b, True); return struct.unpack('<i', b)[0] + len(self.odd)
    def wait(self, secs): return bool(select.select([self.rfd], [], [], max(0.0, secs))[0])
    def read(self, n):
        try: d = self.odd + os.read(self.rfd, n - len(self.odd))
        except BlockingIOError: d = self.odd
        k = len(d) - len(d) % 4; self.odd = d[k:]; return d[:k]
    def trim(self, keep=BACKLOG_KEEP, limit=BACKLOG_MAX):
        """Drop the oldest audio when the backlog exceeds limit (clock drift, or data queued while the graph opened)."""
        p = self.pending()
        if p <= limit: return 0
        drop = (p - keep) // 4 * 4; got = 0
        while got < drop:
            d = self.read(min(65536, drop - got))
            if not d: break
            got += len(d)
        return got
    def fill(self, n, deadline):
        """Up to n bytes, waiting for more until deadline; the caller pads the rest with silence."""
        out = self.read(n)
        while len(out) < n and time.monotonic() < deadline:
            if self.wait(deadline - time.monotonic()): out += self.read(n - len(out))
        return out

class Player:
    """One open speaker graph (BE + shared memory + DSP graph). close() undoes whatever open() got to."""
    def __init__(self):
        import gprclient as g, audioshm as s, alsapcm as a, argraph as ar
        self.g, self.s, self.a, self.ar = g, s, a, ar
        self.st = {'pcm': None, 'hw': False, 'prepared': False, 'buf': None, 'mm': None, 'ion': None, 'ion_mapped': False,
                   'cl': None, 'handle': None, 'opened': False, 'started': False}
        self.tok = 0x5A5B1000; self.free = []; self.busy = {}; self.done = 0; self.errors = 0
    def cmd(self, opcode, payload, dst=None):
        g, ar, st = self.g, self.ar, self.st; self.tok += 1
        m, seen = st['cl'].call(opcode, payload, token=self.tok, dst_port=dst or g.APM_PORT, secs=3.0)
        for x in seen:
            if x is not m: self.on_async(x)
        if m is None: raise RuntimeError('no reply to 0x%08x' % opcode)
        if m['opcode'] == ar.GPR_IBASIC_RSP_RESULT:
            op, status = struct.unpack_from('<II', m['payload'])
            if op != opcode or status != 0: raise RuntimeError('0x%08x failed: status 0x%x' % (opcode, status))
        return m
    def oob(self, opcode, body):
        s, ar, st = self.s, self.ar, self.st
        s.cpu_write(st['buf'], st['mm'], 0, body + bytes(CMD_AREA - len(body)))
        try: return self.cmd(opcode, ar.apm_cmd_oob(0, st['handle'], len(body)))
        except RuntimeError:
            errs = [(hex(i), hex(p), e) for i, p, e in ar.param_errors(s.cpu_read(st['buf'], st['mm'], 0, len(body))) if e]
            log('OOB param errors', errs); raise
    def open(self):
        g, s, a, ar, st = self.g, self.s, self.a, self.ar, self.st
        path, dev = a.node(PCM); st['pcm'] = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
        b = a.loose(BE_FMT, 2, 48000); fcntl.ioctl(st['pcm'], a.IOCTL_HW_PARAMS, b, True); st['hw'] = True
        g.node(); s.chrnode('/dev/msm_audio_ion', '/sys/class/msm_audio_ion/msm_audio_ion/dev'); s.chrnode('/dev/dma_heap/system', '/sys/class/dma_heap/system/dev')
        st['buf'] = s.alloc(SIZE); st['mm'] = mmap.mmap(st['buf'], SIZE); s.cpu_write(st['buf'], st['mm'], 0, bytes(SIZE))
        st['ion'] = os.open('/dev/msm_audio_ion', os.O_RDWR | os.O_CLOEXEC); fcntl.ioctl(st['ion'], s.IOCTL_MAP_PHYS_ADDR, st['buf']); st['ion_mapped'] = True
        st['cl'] = g.Client()
        m = self.cmd(ar.APM_CMD_SHARED_MEM_MAP_REGIONS, ar.mmap_payload(st['buf'], SIZE))
        if m['opcode'] != ar.APM_CMD_RSP_SHARED_MEM_MAP_REGIONS: raise RuntimeError('map failed: 0x%08x' % m['opcode'])
        st['handle'] = struct.unpack_from('<I', m['payload'])[0]
        self.oob(ar.APM_CMD_GRAPH_OPEN, ar.graph_open_payload()); st['opened'] = True
        self.oob(ar.APM_CMD_SET_CFG, ar.set_cfg_payload(dev_bits=DEV_BITS, sd_line=ar.SPEAKER_SD))
        sgs = ar.apm_cmd(ar.mgmt_payload([ar.SG_STREAM, ar.SG_DEVICE]))
        self.cmd(ar.APM_CMD_GRAPH_PREPARE, sgs); self.cmd(ar.APM_CMD_GRAPH_START, sgs); st['started'] = True
        self.free = list(range(CMD_AREA // PERIOD, SIZE // PERIOD)); self.busy = {}; self.done = 0
    def can_submit(self): return bool(self.free) and len(self.busy) < INFLIGHT
    def submit(self, chunk):
        s, g, ar, st = self.s, self.g, self.ar, self.st
        k = self.free.pop(0); s.cpu_write(st['buf'], st['mm'], k * PERIOD, chunk)
        st['cl'].send(g.pack(ar.DATA_CMD_WR_SH_MEM_EP_DATA_BUFFER_V2, ar.wr_buffer(k * PERIOD, st['handle'], len(chunk)), token=0x100 + k, dst_port=ar.IID_SHM))
        self.busy[0x100 + k] = time.monotonic()
    def on_async(self, m):
        ar = self.ar
        if m['opcode'] == ar.DATA_CMD_RSP_WR_SH_MEM_EP_DATA_BUFFER_DONE_V2 and m['token'] in self.busy:
            if struct.unpack_from('<I', m['payload'], 12)[0]: self.errors += 1
            del self.busy[m['token']]; self.free.append(m['token'] - 0x100); self.done += 1
        elif m['opcode'] == ar.GPR_IBASIC_RSP_RESULT and struct.unpack_from('<II', m['payload'])[1]:
            raise RuntimeError('async error %s' % m['payload'][:8].hex())
    def pump(self, secs):
        """Handle DSP replies for up to secs; BE PREPARE (amplifier start) once two buffers have played."""
        m = self.st['cl'].recv(secs)
        if m is not None: self.on_async(m)
        if not self.st['prepared'] and self.done >= 2:
            fcntl.ioctl(self.st['pcm'], self.a.IOCTL_PREPARE); self.st['prepared'] = True
        if self.busy and time.monotonic() - min(self.busy.values()) > 2.0: raise RuntimeError('buffer-done timeout')
    def close(self):
        ar, st, notes = self.ar, self.st, []
        sgs = lambda: ar.apm_cmd(ar.mgmt_payload([ar.SG_STREAM, ar.SG_DEVICE]))
        for key, fn in (('started', lambda: self.cmd(ar.APM_CMD_GRAPH_STOP, sgs())), ('opened', lambda: self.cmd(ar.APM_CMD_GRAPH_CLOSE, sgs())),
                        ('handle', lambda: self.cmd(ar.APM_CMD_SHARED_MEM_UNMAP_REGIONS, struct.pack('<I', st['handle'])))):
            if st[key] not in (None, False):
                try: fn()
                except Exception as e: notes.append('%s: %s' % (key, e))
        if st['ion_mapped']:
            try: fcntl.ioctl(st['ion'], self.s.IOCTL_UNMAP_PHYS_ADDR, st['buf'])
            except OSError as e: notes.append('ion unmap: %s' % e)
        for key in ('cl',):
            if st[key]: st[key].close()
        for key in ('ion', 'buf'):
            if st[key] is not None: os.close(st[key])
        if st['mm'] is not None: st['mm'].close()
        if st['pcm'] is not None:
            for key, io in (('prepared', self.a.IOCTL_DROP), ('hw', self.a.IOCTL_HW_FREE)):
                if st[key]:
                    try: fcntl.ioctl(st['pcm'], io)
                    except OSError as e: notes.append('be %s: %s' % (key, e))
            os.close(st['pcm'])
        for k in st: st[k] = None if k in ('pcm', 'buf', 'mm', 'ion', 'cl', 'handle') else False
        return notes

def play(fifo, gain, stop):
    """One session: open the graph, stream until idle, close. Returns the number of buffers played."""
    p = Player(); t0 = time.monotonic()
    try:
        p.open(); dropped = fifo.trim(); log('OPEN graph in %.0f ms, dropped %d stale bytes' % ((time.monotonic() - t0) * 1000, dropped))
        last_data = last_sound = time.monotonic(); underruns = 0
        while not stop[0]:
            while p.can_submit():
                chunk = fifo.fill(PERIOD, time.monotonic() + 0.015)
                now = time.monotonic()
                if chunk: last_data = now
                if chunk.strip(b'\0'): last_sound = now
                if len(chunk) < PERIOD:
                    if chunk: underruns += 1
                    chunk += bytes(PERIOD - len(chunk))
                p.submit(scale(chunk, gain))
            p.pump(0.05)
            dropped = fifo.trim()
            if dropped: log('backlog: dropped %d bytes' % dropped)
            now = time.monotonic()
            if now - last_data > IDLE_SECS or now - last_sound > SILENT_SECS: break
        log('STOP after %.1f s: buffers %d, short reads %d, buffer errors %d' % (time.monotonic() - t0, p.done, underruns, p.errors))
        return p.done
    finally:
        notes = p.close()
        if notes: log('CLOSE notes', notes)

def audio_ready():
    try:
        return Path('/sys/class/remoteproc/remoteproc1/state').read_text().strip() == 'running' and 'canoe' in Path('/proc/asound/cards').read_text()
    except OSError: return False

def main(argv):
    if '--test' in argv: return selftest()
    gain = float(argv[argv.index('--gain') + 1]) if '--gain' in argv else 0.5
    path = argv[argv.index('--fifo') + 1] if '--fifo' in argv else FIFO
    if not 0.0 < gain <= 1.0: sys.exit('--gain must be in (0, 1]')
    if os.geteuid() != 0: sys.exit('run as root')
    # at boot the service starts long before the audio stages of bring-up are done: wait for them (no timeout)
    said = False
    while not audio_ready():
        if not said: log('WAIT for the audio stages (ADSP running + canoe sound card)'); said = True
        time.sleep(10)
    import pwd; uid = pwd.getpwnam(OWNER).pw_uid
    fifo = None; stop = [False]
    def on_signal(sig, frm): stop[0] = True
    signal.signal(signal.SIGTERM, on_signal); signal.signal(signal.SIGINT, on_signal)
    log('READY fifo=%s gain=%.2f period=%d inflight=%d pid=%d' % (path, gain, PERIOD, INFLIGHT, os.getpid()))
    fails = 0; t_check = 0
    try:
        while not stop[0]:
            if fifo is None or (time.monotonic() - t_check > 2 and fifo.stale()):
                t_check = time.monotonic()
                if fifo: fifo.close(); fifo = None; log('FIFO re-created by the session, reopening')
                try: fifo = Fifo(path, uid); log('FIFO open', path)
                except FileNotFoundError: time.sleep(1); continue
            if not fifo.wait(0.5) or not fifo.pending(): continue
            try: play(fifo, gain, stop); fails = 0
            except Exception as e:
                fails += 1; log('ERROR', repr(e)); time.sleep(min(30, 2 * fails))
                fifo.trim(0, 0)
    finally:
        if fifo: fifo.close()
        log('EXIT')

def selftest():
    import tempfile
    assert scale(struct.pack('<4h', 32767, -32768, 100, -1), 0.5) == struct.pack('<4h', 16383, -16384, 50, -1)
    assert scale(b'\1\2\3\4', 1.0) == b'\1\2\3\4'
    d = tempfile.mkdtemp(); os.mkfifo(Path(d)/'s.fifo', 0o600); f = Fifo(Path(d)/'s.fifo', os.getuid())
    assert not f.stale()
    w = os.open(Path(d)/'s.fifo', os.O_WRONLY | os.O_NONBLOCK)
    assert not f.wait(0.05) and f.pending() == 0                      # no writer data: no wake, no EOF spin
    os.write(w, b'\1' * 6); assert f.read(4096) == b'\1' * 4 and f.pending() == 2   # partial frame kept back
    os.write(w, b'\2' * 2); assert f.read(4096) == b'\1\1\2\2'
    t = time.monotonic(); got = f.fill(PERIOD, time.monotonic() + 0.05)
    assert got == b'' and time.monotonic() - t >= 0.045
    os.write(w, bytes(40000)); assert f.trim() == 40000 - BACKLOG_KEEP and f.pending() == BACKLOG_KEEP
    os.write(w, b'\3' * 100); assert f.fill(PERIOD, time.monotonic() + 0.01) == bytes(PERIOD)   # oldest first
    assert len(f.read(65536)) == BACKLOG_KEEP - PERIOD + 100 and f.pending() == 0
    os.close(w)
    assert not f.wait(0.05)                                           # writer gone: still no EOF wake-ups
    os.unlink(Path(d)/'s.fifo'); assert f.stale()
    f.close(); os.rmdir(d); print('speakerd selftest OK'); return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
