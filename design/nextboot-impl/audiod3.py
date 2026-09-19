"""audio-d3: open the speaker backend PCM (MI2S-LPAIF-RX-PRIMARY, pcmC0D14p), HW_PARAMS 2ch/48k/S24_LE, PREPARE, hold 3 s, DROP,
HW_FREE, close. No samples are written and no DSP graph exists, so nothing is played; this exercises the machine BE
startup (MI2S clock vote via PRM), DAPM and the aw882xx stream path."""
import fcntl, json, os, time
import alsapcm as a

PCM = 'pcmC0D14p'
def run(mon, report):
    r = report['be'] = {'pcm': PCM, 'steps': []}
    step = lambda **k: (r['steps'].append(k), print('STEP', json.dumps(k), flush=True))
    path, dev = a.node(PCM); step(node=path, dev=dev)
    t0 = time.monotonic(); fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
    step(open_ms=round((time.monotonic() - t0) * 1e3, 1))
    prepared = hw = False
    try:
        ver = bytearray(4); fcntl.ioctl(fd, a.IOCTL_PVERSION, ver, True); step(pcm_proto='%d.%d.%d' % (ver[2], ver[1], ver[0]))
        # 219ad00c: HW_REFINE with fixed period 240 x 4 returned EINVAL -> first ask what the BE allows (refine with no constraints)
        b = a.any_params(); fcntl.ioctl(fd, a.IOCTL_HW_REFINE, b, True); space = a.dump(b); step(refine_any=space)
        fmt = next((f for f in ('S24_LE', 'S32_LE', 'S16_LE') if f in space['formats']), None)
        if fmt is None: raise RuntimeError('no usable PCM format: %r' % space)
        b = a.loose(fmt, 2, 48000)
        fcntl.ioctl(fd, a.IOCTL_HW_REFINE, b, True); step(refine_choice=a.dump(b))
        b = a.loose(fmt, 2, 48000)
        fcntl.ioctl(fd, a.IOCTL_HW_PARAMS, b, True); hw = True; step(hw_params=a.dump(b), format=fmt)
        fcntl.ioctl(fd, a.IOCTL_PREPARE); prepared = True; step(prepared=True)
        mon.observe(3, quiet=True)
    finally:
        if prepared:
            try: fcntl.ioctl(fd, a.IOCTL_DROP); step(drop=True)
            except OSError as e: step(drop_error=str(e))
        if hw:
            try: fcntl.ioctl(fd, a.IOCTL_HW_FREE); step(hw_free=True)
            except OSError as e: step(hw_free_error=str(e))
        os.close(fd); step(closed=True)
    return r
