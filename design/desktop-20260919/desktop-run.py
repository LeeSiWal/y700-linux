#!/usr/bin/env python3
"""D-2: first Weston desktop on the Y700, time-boxed, with the display owner kept alive.
- Seat shim (root): speaks the seatd protocol on /run/user/1000/y700-seat.sock (uid 1000 only, SO_PEERCRED).
    OPEN_SEAT -> SEAT_OPENED("seat0") + ENABLE_SEAT; PING -> PONG; CLOSE_SEAT -> SEAT_CLOSED
    OPEN_DEVICE /dev/dri/card0      -> a pidfd_getfd duplicate of the owner's DRM file (already master; the owner keeps its fd)
    OPEN_DEVICE /dev/input/event0-2 -> the reviewed gpio-keys / NVT touch / NVT pen, opened through private nodes in
                                       /dev/input (mknod root 0600 if missing, removed afterwards; /run is nodev)
    anything else -> ERROR(EPERM)
- Weston runs as siwal: LIBSEAT_BACKEND=seatd, --backend=drm --renderer=pixman --shell=desktop, idle-time=0, for SECS s.
- Afterwards the native picture is restored on a borrowed fd (TEST_ONLY, then commit: CRTC active + registered mode blob,
  connector -> CRTC, planes 95/128 = halves of the native FB) and the DRM state is compared with the registered one.
One run per boot (desktop-run-<boot8>.json). Usage: sudo python3 -B desktop-run.py [SECS]"""
import array, hashlib, importlib.util, json, os, pwd, re, select, signal, socket, stat, struct, subprocess, sys, time
from pathlib import Path
IMPL = Path('/home/siwal/y700-design/nextboot-impl'); sys.path.insert(0, str(IMPL))
import registry as rg, borrow_owner as bo, drmkms as k, bootguard as bg

_spec = importlib.util.spec_from_file_location('readers', IMPL/'runtime-readers.py')
c = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(c)
HERE = Path(__file__).resolve().parent
UID = pwd.getpwnam('siwal').pw_uid; GID = pwd.getpwnam('siwal').pw_gid
RUN = Path('/run/user/%d' % UID); SOCK = RUN/'y700-seat.sock'; PRIV = Path('/run/y700-seat')
INPUTS = {'/dev/input/event0': 'gpio-keys', '/dev/input/event1': 'NVTCapacitiveTouchScreen', '/dev/input/event2': 'NVTCapacitivePen'}
C_OPEN_SEAT, C_CLOSE_SEAT, C_OPEN_DEV, C_CLOSE_DEV, C_DISABLE, C_SWITCH, C_PING = 1, 2, 3, 4, 5, 6, 7
S = lambda op: op + (1 << 15)
S_SEAT_OPENED, S_SEAT_CLOSED, S_DEV_OPENED, S_DEV_CLOSED, S_ENABLE, S_PONG, S_SEAT_DISABLED, S_ERROR = S(1), S(2), S(3), S(4), S(6), S(7), S(9), S(0x7FFF)
W, H = 1904, 3040
LOG = []

def log(*a):
    line = '%.3f %s' % (time.monotonic(), ' '.join(str(x) for x in a)); LOG.append(line); print(line, flush=True)

def msg(op, payload=b''): return struct.pack('<HH', op, len(payload)) + payload

class Shim:
    def __init__(self, reg):
        self.reg = reg; self.next_id = 1; self.devices = {}; self.opened = []; self.made = []
        PRIV.mkdir(mode=0o700, exist_ok=True)
        if SOCK.exists(): SOCK.unlink()
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.srv.bind(str(SOCK))
        os.chown(SOCK, UID, GID); os.chmod(SOCK, 0o600); self.srv.listen(2); self.clients = {}
    def device_fd(self, path):
        if path == '/dev/dri/card0': return bo.borrow_fd(c, self.reg)
        if path in INPUTS:
            n = path.rsplit('/', 1)[1]; sysd = Path('/sys/class/input')/n
            c.need(c.read(sysd/'device/name').strip() == INPUTS[path], 'input identity changed: ' + path)
            # /run is mounted nodev (r2: EACCES); /dev is the Bootstrap tmpfs without nodev (touch-events.py made event1 there too)
            ma, mi = map(int, c.read(sysd/'dev').split(':')); node = Path(path)
            if node.exists():
                st = os.stat(node); c.need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(ma, mi), 'unexpected node ' + path)
            else:
                node.parent.mkdir(mode=0o755, exist_ok=True); os.mknod(node, 0o600 | stat.S_IFCHR, os.makedev(ma, mi)); self.made.append(node)
            return os.open(node, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        raise PermissionError(path)
    def handle(self, conn):
        buf = self.clients[conn]; data = conn.recv(4096)
        if not data: conn.close(); del self.clients[conn]; log('SEAT client closed'); return
        buf += data
        while len(buf) >= 4:
            op, size = struct.unpack_from('<HH', buf)
            if len(buf) < 4 + size: break
            body = bytes(buf[4:4 + size]); del buf[:4 + size]
            if op == C_OPEN_SEAT:
                name = b'seat0\0'; conn.sendall(msg(S_SEAT_OPENED, struct.pack('<H', len(name)) + name) + msg(S_ENABLE)); log('SEAT opened+enabled')
            elif op == C_PING: conn.sendall(msg(S_PONG))
            elif op == C_OPEN_DEV:
                ln = struct.unpack_from('<H', body)[0]; path = body[2:2 + ln].split(b'\0')[0].decode()
                try:
                    fd = self.device_fd(path); did = self.next_id; self.next_id += 1; self.devices[did] = path
                    socket.send_fds(conn, [msg(S_DEV_OPENED, struct.pack('<i', did))], [fd]); os.close(fd)
                    self.opened.append(path); log('OPEN_DEVICE', path, '-> id', did)
                except Exception as e:
                    err = getattr(e, 'errno', None) or 1; conn.sendall(msg(S_ERROR, struct.pack('<i', err))); log('OPEN_DEVICE', path, 'DENIED', repr(e))
            elif op == C_CLOSE_DEV:
                did = struct.unpack_from('<i', body)[0]; log('CLOSE_DEVICE', did, self.devices.pop(did, '?')); conn.sendall(msg(S_DEV_CLOSED))
            elif op == C_DISABLE: conn.sendall(msg(S_SEAT_DISABLED)); log('DISABLE_SEAT ack')
            elif op == C_CLOSE_SEAT: conn.sendall(msg(S_SEAT_CLOSED)); log('CLOSE_SEAT')
            else: conn.sendall(msg(S_ERROR, struct.pack('<i', 1))); log('UNSUPPORTED opcode', op)
    def poll(self, timeout):
        socks = [self.srv] + list(self.clients)
        r, _, _ = select.select(socks, [], [], timeout)
        for s in r:
            if s is self.srv:
                conn, _ = self.srv.accept()
                pid, uid, gid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != UID: conn.close(); log('SEAT reject uid', uid); continue
                self.clients[conn] = bytearray(); log('SEAT client pid', pid)
            else:
                try: self.handle(s)
                except OSError as e: log('SEAT client error', e); s.close(); self.clients.pop(s, None)
    def close(self):
        for s in list(self.clients): s.close()
        self.srv.close()
        if SOCK.exists(): SOCK.unlink()
        for n in PRIV.glob('event*'): n.unlink()
        for n in self.made:
            if n.exists(): n.unlink()

def drm_view(reg):
    rg.process_ok(c, reg['owner'])
    cl = c.read(rg.DEBUG/'clients').splitlines()
    return {'clients': len(cl) - 1, 'master_pid_ok': len(cl) == 2 and cl[1].split()[1] == str(reg['owner']['pid']) and cl[1].split()[3] == 'y',
            'state_sha256': hashlib.sha256(c.read(rg.DEBUG/'state').encode()).hexdigest(), 'underruns': rg.underruns(c, reg['encoder_status'])}

def single_plane(reg):
    """Before handing the display to a compositor: native FB on the LEFT plane over the full width, RIGHT plane off
    (D-1 TEST_ONLY 'left_full' PASS). Compositors use one primary plane; a leftover plane with the colour bars would
    otherwise sit above/below their output (r3: only a corner of the desktop visible)."""
    t = reg['topology']; fd = bo.borrow_fd(c, reg)
    try:
        lp = k.properties(fd, t['left'], k.OBJECT_PLANE); rp = k.properties(fd, t['right'], k.OBJECT_PLANE); fb = reg['native_fb']
        props = {t['left']: [(lp['FB_ID'][0], fb), (lp['CRTC_ID'][0], t['crtc']), (lp['SRC_X'][0], 0), (lp['SRC_Y'][0], 0),
                             (lp['SRC_W'][0], W << 16), (lp['SRC_H'][0], H << 16), (lp['CRTC_X'][0], 0), (lp['CRTC_Y'][0], 0),
                             (lp['CRTC_W'][0], W), (lp['CRTC_H'][0], H)],
                 t['right']: [(rp['FB_ID'][0], 0), (rp['CRTC_ID'][0], 0)]}
        objs = list(props)
        k.atomic(fd, k.ATOMIC_TEST_ONLY, objs, props); k.atomic(fd, 0, objs, props); log('SINGLE_PLANE committed (95 full, 128 off)')
    finally:
        os.close(fd)

def restore(reg):
    t = reg['topology']; fd = bo.borrow_fd(c, reg)
    try:
        cp = k.properties(fd, t['crtc'], k.OBJECT_CRTC); cn = k.properties(fd, t['connector'], k.OBJECT_CONNECTOR)
        lp = k.properties(fd, t['left'], k.OBJECT_PLANE); rp = k.properties(fd, t['right'], k.OBJECT_PLANE)
        fb, half = reg['native_fb'], W // 2
        import drmabi; c.need(fb in drmabi.fb_ids(fd), 'native FB %d gone' % fb)
        def halfp(p, x): return [(p['FB_ID'][0], fb), (p['CRTC_ID'][0], t['crtc']), (p['SRC_X'][0], x << 16), (p['SRC_Y'][0], 0),
                                 (p['SRC_W'][0], half << 16), (p['SRC_H'][0], H << 16), (p['CRTC_X'][0], x), (p['CRTC_Y'][0], 0),
                                 (p['CRTC_W'][0], half), (p['CRTC_H'][0], H)]
        props = {t['crtc']: [(cp['ACTIVE'][0], 1), (cp['MODE_ID'][0], reg['mode_blob'])], t['connector']: [(cn['CRTC_ID'][0], t['crtc'])],
                 t['left']: halfp(lp, 0), t['right']: halfp(rp, half)}
        objs = list(props)
        k.atomic(fd, k.ATOMIC_TEST_ONLY | k.ATOMIC_ALLOW_MODESET, objs, props); log('RESTORE test-only pass')
        k.atomic(fd, k.ATOMIC_ALLOW_MODESET, objs, props); log('RESTORE committed')
    finally:
        os.close(fd)

def main():
    c.need(os.geteuid() == 0, 'run with sudo')
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 120; c.need(10 <= secs <= 600, 'SECS 10..600')
    boot = c.read('/proc/sys/kernel/random/boot_id').strip(); reg, _ = rg.load(c, boot)
    tag = os.environ.get('Y700_RUN_TAG', 'r1'); out = HERE/('desktop-run-%s-%s.json' % (boot[:8], tag)); c.need(not out.exists(), 'already run: ' + out.name)
    c.need(Path('/usr/bin/weston').exists() and RUN.is_dir(), 'weston or /run/user/1000 missing')
    native = c.read(reg['native_state_file']); before = drm_view(reg)
    c.need(before['master_pid_ok'] and before['clients'] == 1, 'owner is not the sole DRM master')
    c.need(c.read(rg.DEBUG/'state') == native or c.read(rg.DEBUG/'state') in rg.rebased_states(c, reg, native), 'display not in the native state')
    dm0 = set(c.dmesg().splitlines())
    report = {'boot_id': boot, 'secs': secs, 'before': before}
    out.write_text(json.dumps({'boot_id': boot, 'attempt': time.time()})); os.chown(out, UID, GID)
    shim = Shim(reg); wlog = HERE/('%s-%s-%s.log' % (os.environ.get('Y700_COMPOSITOR', 'weston'), boot[:8], os.environ.get('Y700_RUN_TAG', 'r1'))); proc = None
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/home/siwal', 'XDG_RUNTIME_DIR': str(RUN), 'LIBSEAT_BACKEND': 'seatd',
           'SEATD_SOCK': str(SOCK), 'LANG': 'C.UTF-8'}
    try:
        if os.environ.get('Y700_SINGLE_PLANE', '1') == '1': single_plane(reg); report['single_plane'] = True
        comp = os.environ.get('Y700_COMPOSITOR', 'weston')
        if comp == 'labwc':
            # wlroots merges duplicate plane formats (weston 14 asserts on them); pixman renderer, dumb-buffer allocator
            env.update({'WLR_RENDERER': 'pixman', 'WLR_BACKENDS': 'drm,libinput', 'WLR_DRM_DEVICES': '/dev/dri/card0',
                        'WLR_LIBINPUT_NO_DEVICES': '1'})
            cmd = ['labwc', '-C', str(HERE/'labwc-config'), '-d', '-s',
                   'sh -c "weston-simple-touch & weston-flower & weston-terminal &"']
        else:
            cmd = ['weston', '--backend=drm', '--renderer=pixman', '--shell=desktop', '--idle-time=0',
                   '--config=%s' % (HERE/'weston.ini'), '--log=%s' % wlog]
        report['compositor'] = comp; report['cmd'] = cmd
        lf = open(wlog, 'ab') if comp == 'labwc' else subprocess.DEVNULL
        proc = subprocess.Popen(['runuser', '-u', 'siwal', '--', 'env'] + ['%s=%s' % kv for kv in env.items()] + cmd,
                                stdin=subprocess.DEVNULL, stdout=lf, stderr=lf if comp == 'labwc' else None)
        log('WESTON started pid', proc.pid, 'for', secs, 's')
        t0 = time.monotonic(); snap = False
        while time.monotonic() - t0 < secs and proc.poll() is None:
            shim.poll(0.5)
            if not snap and time.monotonic() - t0 > 10:
                snap = True; (HERE/('drm-state-during-%s-%s.txt' % (boot[:8], os.environ.get('Y700_RUN_TAG', 'r1')))).write_text(c.read(rg.DEBUG/'state'))
        report['weston_exited_early'] = proc.poll() is not None; report['weston_rc'] = proc.poll()
        log('WESTON', 'exited rc=%s' % proc.poll() if proc.poll() is not None else 'time box reached')
    finally:
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            t1 = time.monotonic()
            while proc.poll() is None and time.monotonic() - t1 < 10: shim.poll(0.2)
            if proc.poll() is None: proc.kill(); proc.wait()
            log('WESTON stopped rc', proc.returncode)
        shim.close(); report['opened'] = shim.opened
        try: restore(reg)
        except Exception as e: report['restore_error'] = repr(e); log('RESTORE FAILED', repr(e))
        time.sleep(1)
        report['after'] = drm_view(reg); now = c.read(rg.DEBUG/'state')
        report['state_native'] = now == native; report['state_rebased'] = now in rg.rebased_states(c, reg, native)
        if not (report['state_native'] or report['state_rebased']):
            report['state_diff'] = [(a, b) for a, b in zip(native.splitlines(), now.splitlines()) if a != b][:40]
            (HERE/('drm-state-after-desktop-%s.txt' % boot[:8])).write_text(now)
        new = [l for l in c.dmesg().splitlines() if l not in dm0]
        report['dmesg_faults'] = [l for l in new if bg.FAULT.search(l)][:40]; report['dmesg_new'] = new[-120:]
        report['weston_log_tail'] = wlog.read_text().splitlines()[-80:] if wlog.exists() else []
        report['log'] = LOG
        out.write_text(json.dumps(report, indent=1)); os.chown(out, UID, GID)
        print('DESKTOP_RUN_DONE', json.dumps({k2: report.get(k2) for k2 in ('weston_exited_early', 'weston_rc', 'opened', 'restore_error', 'state_native', 'state_rebased')}))
        print('DMESG_FAULTS', len(report['dmesg_faults'])); [print('  ', l) for l in report['dmesg_faults'][:6]]

if __name__ == '__main__':
    try: main()
    except Exception as exc: sys.exit('STOP: %s' % exc)
