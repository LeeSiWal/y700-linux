"""audio-d4: first sound. Order (vendor AGM style):
  1. speaker BE pcmC0D14p open + HW_PARAMS S16_LE/2ch/48k (machine BE startup/hw_params: MI2S clock config via PRM)
  2. dma-buf 64 KiB + ION MAP + APM SHARED_MEM_MAP (offset mode)
  3. GRAPH_OPEN (SHM EP -> PCM_CNV | I2S_SINK LPAIF primary SD0, WS internal) -> SET_CFG -> PREPARE -> START (MI2S clocks run)
  4. BE PREPARE (DAPM, aw882xx start_pa: PLL can lock now)
  5. 3 s 440 Hz sine at -30 dBFS in 8 KiB buffers (<= 4 outstanding) via DATA_CMD_WR_SH_MEM_EP_DATA_BUFFER_V2
  6. STOP -> CLOSE -> UNMAP -> ION UNMAP -> close audio-pkt -> BE DROP/HW_FREE/close
Every GPR reply is recorded. Any non-zero status stops the sequence and runs the cleanup."""
import fcntl, json, mmap, os, struct, time
import gprclient as g, audioshm as s, alsapcm as a, argraph as ar

PCM, SIZE, PERIOD, INFLIGHT = 'pcmC0D14p', 64 * 1024, 8192, 4
# 36cf7746: amps kept SYSST 0x0011 (no switching) even with signal present -> try 32-bit I2S slots (64 fs): BE S32_LE,
# PCM converter output and I2S endpoint 32 bit; shared memory stays 16-bit PCM (converter widens).
BE_FMT, DEV_BITS = 'S32_LE', 32
CMD_AREA = 8192          # offset 0..8 KiB: out-of-band command payloads (error codes come back here); audio slots follow

# 6a1d59ef: with 32-bit slots both amps reported "start success", but the -30 dBFS tone was not clearly heard.
# Now two segments, each a full open/play/close: A = SD0 with 440 Hz, B = SD1 with 880 Hz, both at -18 dBFS for 4 s,
# 3 s apart, so the listener can tell which data line (if any) reaches the speakers.
# 2cad5b66: one beep heard, but the listener could not tell 440 from 880 Hz -> make the two segments differ in rhythm:
# A (SD0) = ONE long beep (3 s), B (SD1) = THREE short beeps (0.4 s on / 0.6 s off), same 660 Hz, 6 s pause between.
SEGMENTS = [('A', ar.I2S_SD0, (1, 3.0, 0.5)), ('B', ar.I2S_SD0 + 1, (3, 0.4, 0.6))]

def run_all(mon, report, dbfs=-18.0):
    report['segments'] = []
    for name, sd, (n, on, off) in SEGMENTS:
        print('SEGMENT %s: sd_line=%d  %s  (660 Hz, %s dBFS)' % (name, sd, 'ONE LONG beep' if n == 1 else 'THREE SHORT beeps', dbfs), flush=True)
        sub = {}
        try: run(mon, sub, dbfs=dbfs, sd_line=sd, pattern=(n, on, off))
        finally: report['segments'].append({'name': name, 'sd_line': sd, 'pattern': [n, on, off], **sub.get('play', {})})
        print('SEGMENT %s finished' % name, flush=True); time.sleep(6)
    return report['segments']

def run(mon, report, secs=5.0, freq=440, dbfs=-30.0, sd_line=ar.SPEAKER_SD, pattern=None, pcm=None, label=None):
    r = report['play'] = {'steps': [], 'events': []}
    step = lambda **k: (r['steps'].append(k), print('STEP', json.dumps(k), flush=True))
    st = {'pcm': None, 'hw': False, 'prepared': False, 'buf': None, 'mm': None, 'ion': None, 'ion_mapped': False,
          'cl': None, 'handle': None, 'opened': False, 'started': False}
    tok = [0x5A5A1000]
    def oob(opcode, body):
        """59e6be67: in-band GRAPH_OPEN only said status 1. Out-of-band: body in shared memory, per-param error codes read back."""
        assert len(body) <= CMD_AREA
        s.cpu_write(st['buf'], st['mm'], 0, body + bytes(CMD_AREA - len(body)))
        try: return cmd(opcode, ar.apm_cmd_oob(0, st['handle'], len(body)))
        except RuntimeError:
            errs = [(hex(i), hex(p), e) for i, p, e in ar.param_errors(s.cpu_read(st['buf'], st['mm'], 0, len(body))) if e]
            step(oob_param_errors=errs); raise
    def cmd(opcode, payload, dst=g.APM_PORT):
        tok[0] += 1; m, seen = st['cl'].call(opcode, payload, token=tok[0], dst_port=dst, secs=3.0)
        r['events'] += [{'opcode': hex(x['opcode']), 'token': hex(x['token']), 'payload': x['payload'][:32].hex()} for x in seen if x is not m]
        if m is None: raise RuntimeError('no reply to 0x%08x' % opcode)
        if m['opcode'] == ar.GPR_IBASIC_RSP_RESULT:
            op, status = struct.unpack_from('<II', m['payload'])
            step(cmd=hex(opcode), reply='basic', status=status)
            if op != opcode or status != 0: raise RuntimeError('0x%08x failed: status 0x%x' % (opcode, status))
        else: step(cmd=hex(opcode), reply=hex(m['opcode']), payload=m['payload'][:16].hex())
        return m
    try:
        path, dev = a.node(PCM); st['pcm'] = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK); step(be_open=dev)
        b = a.loose(BE_FMT, 2, 48000); fcntl.ioctl(st['pcm'], a.IOCTL_HW_PARAMS, b, True); st['hw'] = True; step(be_hw_params=a.dump(b))
        g.node(); s.chrnode('/dev/msm_audio_ion', '/sys/class/msm_audio_ion/msm_audio_ion/dev'); s.chrnode('/dev/dma_heap/system', '/sys/class/dma_heap/system/dev')
        st['buf'] = s.alloc(SIZE); st['mm'] = mmap.mmap(st['buf'], SIZE); s.cpu_write(st['buf'], st['mm'], 0, bytes(SIZE))
        st['ion'] = os.open('/dev/msm_audio_ion', os.O_RDWR | os.O_CLOEXEC); fcntl.ioctl(st['ion'], s.IOCTL_MAP_PHYS_ADDR, st['buf']); st['ion_mapped'] = True
        st['cl'] = g.Client()
        m = cmd(ar.APM_CMD_SHARED_MEM_MAP_REGIONS, ar.mmap_payload(st['buf'], SIZE))
        if m['opcode'] != ar.APM_CMD_RSP_SHARED_MEM_MAP_REGIONS: raise RuntimeError('map failed')
        st['handle'] = struct.unpack_from('<I', m['payload'])[0]; step(map_handle=st['handle'])
        oob(ar.APM_CMD_GRAPH_OPEN, ar.graph_open_payload()); st['opened'] = True
        oob(ar.APM_CMD_SET_CFG, ar.set_cfg_payload(dev_bits=DEV_BITS, sd_line=sd_line))
        cmd(ar.APM_CMD_GRAPH_PREPARE, ar.apm_cmd(ar.mgmt_payload([ar.SG_STREAM, ar.SG_DEVICE])))
        cmd(ar.APM_CMD_GRAPH_START, ar.apm_cmd(ar.mgmt_payload([ar.SG_STREAM, ar.SG_DEVICE]))); st['started'] = True
        # 1d514401: amps got clocks (pll check done) but SYSST stayed 0x0011 (PLLS|CLKS, no SWS/BSTS) while the input was still
        # silent (BE prepared ~2 s before the first sample). Now the tone is already flowing when the BE is prepared.
        if pcm is not None: secs = len(pcm) / 192000; step(clip={'label': label, 'secs': round(secs, 2), 'bytes': len(pcm)})
        else:
            if pattern: pcm, amp = ar.pulses_s16_stereo(660, *pattern, dbfs=dbfs); secs = len(pcm) / 192000
            else: pcm, amp = ar.sine_s16_stereo(freq, secs, dbfs=dbfs)
            step(tone={'pattern': pattern, 'freq': 660 if pattern else freq, 'secs': round(secs, 2), 'dbfs': dbfs, 'amplitude': amp, 'bytes': len(pcm)})
        slots = SIZE // PERIOD; free = list(range(CMD_AREA // PERIOD, slots)); busy = {}; pos = 0; done = 0; t0 = time.monotonic(); errors = []
        while pos < len(pcm) or busy:
            while pos < len(pcm) and free and len(busy) < INFLIGHT:
                k = free.pop(0); chunk = pcm[pos:pos + PERIOD]; s.cpu_write(st['buf'], st['mm'], k * PERIOD, chunk); pos += len(chunk)
                st['cl'].send(g.pack(ar.DATA_CMD_WR_SH_MEM_EP_DATA_BUFFER_V2, ar.wr_buffer(k * PERIOD, st['handle'], len(chunk)), token=0x100 + k, dst_port=ar.IID_SHM))
                busy[0x100 + k] = time.monotonic()
            m = st['cl'].recv(1.0)
            if m is None:
                if time.monotonic() - t0 > secs + 5: raise RuntimeError('buffer-done timeout: busy=%r' % list(busy))
                continue
            if m['opcode'] == ar.DATA_CMD_RSP_WR_SH_MEM_EP_DATA_BUFFER_DONE_V2 and m['token'] in busy:
                status = struct.unpack_from('<I', m['payload'], 12)[0]; del busy[m['token']]; free.append(m['token'] - 0x100); done += 1
                if status: errors.append(status)
            else:
                r['events'].append({'opcode': hex(m['opcode']), 'token': hex(m['token']), 'payload': m['payload'][:32].hex()})
                if m['opcode'] == ar.GPR_IBASIC_RSP_RESULT and struct.unpack_from('<II', m['payload'])[1]: raise RuntimeError('async error %s' % m['payload'][:8].hex())
            if not st['prepared'] and done >= 2:
                fcntl.ioctl(st['pcm'], a.IOCTL_PREPARE); st['prepared'] = True; step(be_prepared=True, after_buffers=done)
            if done % 20 == 0: mon.tick()
        step(buffers_done=done, elapsed=round(time.monotonic() - t0, 2), buffer_errors=errors[:5])
        mon.observe(0.3, quiet=True)
    finally:
        for key, fn in (('started', lambda: cmd(ar.APM_CMD_GRAPH_STOP, ar.apm_cmd(ar.mgmt_payload([ar.SG_STREAM, ar.SG_DEVICE])))),
                        ('opened', lambda: cmd(ar.APM_CMD_GRAPH_CLOSE, ar.apm_cmd(ar.mgmt_payload([ar.SG_STREAM, ar.SG_DEVICE])))),
                        ('handle', lambda: cmd(ar.APM_CMD_SHARED_MEM_UNMAP_REGIONS, struct.pack('<I', st['handle'])))):
            if st[key] not in (None, False):
                try: fn()
                except Exception as e: step(cleanup_error={key: str(e)})
        if st['ion_mapped']:
            try: fcntl.ioctl(st['ion'], s.IOCTL_UNMAP_PHYS_ADDR, st['buf']); step(ion_unmap=True)
            except OSError as e: step(ion_unmap_error=str(e))
        if st['cl']: st['cl'].close()
        if st['ion'] is not None: os.close(st['ion'])
        if st['mm'] is not None: st['mm'].close()
        if st['buf'] is not None: os.close(st['buf'])
        if st['pcm'] is not None:
            for key, io in (('prepared', a.IOCTL_DROP), ('hw', a.IOCTL_HW_FREE)):
                if st[key]:
                    try: fcntl.ioctl(st['pcm'], io)
                    except OSError as e: step(be_cleanup_error={key: str(e)})
            os.close(st['pcm']); step(be_closed=True)
    return r
