"""Host test (no DRM, user only): labwc headless + demo clients -> set HEADLESS-1 to 1904x3040@60 -> capture frames."""
import os, subprocess, sys, time, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wl, wlcapture
RUN = os.environ['XDG_RUNTIME_DIR']; OUT = sys.argv[1]
env = dict(os.environ, WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_LIBINPUT_NO_DEVICES='1')
lab = subprocess.Popen(['labwc', '-C', os.path.dirname(os.path.abspath(__file__)) + '/labwc-config', '-s',
                        'sh -c "weston-flower & weston-simple-shm &"'], env=env, stdout=open(OUT + '/labwc-test.log', 'w'), stderr=subprocess.STDOUT)
try:
    for i in range(50):
        socks = [f for f in os.listdir(RUN) if f.startswith('wayland-') and not f.endswith('.lock')]
        if socks: break
        time.sleep(0.1)
    time.sleep(1.5); os.environ['WAYLAND_DISPLAY'] = socks[0]
    c = wl.Conn(); reg, g = wl.registry(c)
    outs = wlcapture.Outputs(c, reg, g); print('heads', [(v.get('name'), [(m.get('w'), m.get('h'), m.get('refresh')) for m in v['modes'].values()]) for v in outs.heads.values()])
    print('set_custom', outs.set_custom('HEADLESS-1', 1904, 3040, 60000)); time.sleep(1)
    cap = wlcapture.Capturer(c, reg, g)
    buf, info = cap.capture(damage=False); print('first', {k: info[k] for k in ('w', 'h', 'stride', 'format')})
    w, h, st = info['w'], info['h'], info['stride']
    nz = sum(1 for y in range(0, h, 97) for x in range(0, w, 97) if bytes(buf[y * st + x * 4:y * st + x * 4 + 3]) != b'\0\0\0')
    print('non-black samples', nz, 'of', len(range(0, h, 97)) * len(range(0, w, 97)))
    with open(OUT + '/frame.ppm', 'wb') as f:                  # 1/4 scale preview
        f.write(b'P6 %d %d 255\n' % (w // 4, h // 4))
        for y in range(0, h - 3, 4):
            row = buf[y * st:(y + 1) * st]
            f.write(b''.join(bytes((row[x * 4 + 2], row[x * 4 + 1], row[x * 4])) for x in range(0, w - 3, 4)))
    t0 = time.monotonic(); n = 0; dmg = 0
    while time.monotonic() - t0 < 5:
        buf, info = cap.capture(damage=True); n += 1; dmg += len(info['damage'])
    print('damage-paced captures in 5 s: %d (%.1f fps), damage rects %d' % (n, n / 5, dmg))
finally:
    lab.terminate(); lab.wait(5)
