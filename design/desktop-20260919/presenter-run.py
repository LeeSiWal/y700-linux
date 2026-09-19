#!/usr/bin/env python3
"""r5: labwc on a HEADLESS output, shown on the panel by a two-plane presenter (the only layout without underruns).
- seat shim (from desktop-run.py) with card0 DENIED: only the reviewed input devices are handed to labwc.
- labwc as siwal: WLR_BACKENDS=headless,libinput, pixman; demo clients weston-simple-touch / weston-flower / weston-terminal.
- presenter (this process, root): Wayland client of labwc: HEADLESS-1 -> 1904x3040@60, scale SCALE; two dumb FBs on a
  borrowed duplicate of the owner's DRM file; loop: screencopy (damage paced) -> row copy (stride -> pitch) into the back
  FB -> blocking atomic commit of planes 95 (left half) + 128 (right half) -> swap.
- exit: labwc stopped, native layout restored (FB 335 halves), our two FBs removed (RMFB) and dumb buffers destroyed.
Report presenter-run-<boot8>-<tag>.json: frames, fps, copy ms, underrun delta, state back to native, kernel faults.
Usage: sudo env Y700_RUN_TAG=r5 python3 -B presenter-run.py [SECS] [SCALE]"""
import ctypes, fcntl, hashlib, importlib.util, json, os, struct, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; IMPL = Path('/home/siwal/y700-design/nextboot-impl')
sys.path.insert(0, str(IMPL)); sys.path.insert(0, str(HERE))
import registry as rg, borrow_owner as bo, drmkms as k, drmabi, bootguard as bg, wl, wlcapture
_s = importlib.util.spec_from_file_location('desktop_run', HERE/'desktop-run.py'); dr = importlib.util.module_from_spec(_s); _s.loader.exec_module(dr)
c = dr.c; log = dr.log; W, H = 1904, 3040
RMFB = (3 << 30) | (4 << 16) | (ord('d') << 8) | 0xAF            # DRM_IOWR(0xAF, unsigned int)
DESTROY_DUMB = (3 << 30) | (4 << 16) | (ord('d') << 8) | 0xB4    # DRM_IOWR(0xB4, struct drm_mode_destroy_dumb)

def planes_for(fd, reg, fb):
    t = reg['topology']; half = W // 2
    lp = k.properties(fd, t['left'], k.OBJECT_PLANE); rp = k.properties(fd, t['right'], k.OBJECT_PLANE)
    def pl(p, x): return [(p['FB_ID'][0], fb), (p['CRTC_ID'][0], t['crtc']), (p['SRC_X'][0], x << 16), (p['SRC_Y'][0], 0),
                          (p['SRC_W'][0], half << 16), (p['SRC_H'][0], H << 16), (p['CRTC_X'][0], x), (p['CRTC_Y'][0], 0),
                          (p['CRTC_W'][0], half), (p['CRTC_H'][0], H)]
    return {t['left']: pl(lp, 0), t['right']: pl(rp, half)}

def main():
    c.need(os.geteuid() == 0, 'run with sudo')
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 120; scale = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    boot = c.read('/proc/sys/kernel/random/boot_id').strip(); reg, _ = rg.load(c, boot)
    tag = os.environ.get('Y700_RUN_TAG', 'r5'); out = HERE/('presenter-run-%s-%s.json' % (boot[:8], tag)); c.need(not out.exists(), 'already run: ' + out.name)
    native = c.read(reg['native_state_file']); now = c.read(rg.DEBUG/'state')
    c.need(now == native or now in rg.rebased_states(c, reg, native), 'display not in the native state')
    v0 = dr.drm_view(reg); c.need(v0['master_pid_ok'] and v0['clients'] == 1, 'owner is not the sole DRM master')
    out.write_text(json.dumps({'boot_id': boot, 'attempt': time.time()})); os.chown(out, dr.UID, dr.GID)
    dm0 = set(c.dmesg().splitlines()); rep = {'boot_id': boot, 'secs': secs, 'scale': scale, 'before': v0}
    fd = bo.borrow_fd(c, reg); fbs = []; lab = None; shim = None
    try:
        for i in range(2):
            d = k.dumb_fb(fd, W, H); d['fb'] = k.add_fb(fd, W, H, d['handle'], d['pitch']); ctypes.memset(d['addr'], 0, d['size']); fbs.append(d)
        log('FBS', [(d['fb'], d['pitch']) for d in fbs])
        shim = dr.Shim(reg); shim.device_fd_orig = shim.device_fd
        shim.device_fd = lambda p: (_ for _ in ()).throw(PermissionError(p)) if p.startswith('/dev/dri/') else shim.device_fd_orig(p)
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/home/siwal', 'XDG_RUNTIME_DIR': str(dr.RUN), 'LIBSEAT_BACKEND': 'seatd',
               'SEATD_SOCK': str(dr.SOCK), 'LANG': 'C.UTF-8', 'WLR_BACKENDS': 'headless,libinput', 'WLR_RENDERER': 'pixman',
               'WLR_LIBINPUT_NO_DEVICES': '1', 'WAYLAND_DISPLAY': 'wayland-y700'}
        lf = open(HERE/('labwc-%s-%s.log' % (boot[:8], tag)), 'ab')
        sockp = dr.RUN/'wayland-y700'
        for p in (sockp, Path(str(sockp) + '.lock')):
            if p.exists(): p.unlink()
        lab = subprocess.Popen(['runuser', '-u', 'siwal', '--', 'env'] + ['%s=%s' % kv for kv in env.items() if kv[0] != 'WAYLAND_DISPLAY'] +
                               ['labwc', '-C', str(HERE/'labwc-config'), '-d', '-s', os.environ.get('Y700_STARTUP', 'sh -c "weston-simple-touch & weston-flower & weston-terminal &"')],
                               stdin=subprocess.DEVNULL, stdout=lf, stderr=lf)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 10:
            shim.poll(0.1)
            socks = [f for f in os.listdir(dr.RUN) if f.startswith('wayland-') and not f.endswith('.lock')]
            if socks: break
        c.need(socks, 'labwc socket did not appear'); os.environ['XDG_RUNTIME_DIR'] = str(dr.RUN); os.environ['WAYLAND_DISPLAY'] = socks[0]
        for _ in range(10): shim.poll(0.1)
        conn = wl.Conn(); reg_id, globs = wl.registry(conn)
        outs = wlcapture.Outputs(conn, reg_id, globs); rep['mode_set'] = outs.set_custom('HEADLESS-1', W, H, 60000, scale); log('MODE', rep['mode_set'])
        cap = wlcapture.Capturer(conn, reg_id, globs)
        first = True; n = 0; copy_ms = 0.0; back = 0; u0 = rg.underruns(c, reg['encoder_status']); t0 = time.monotonic()
        while time.monotonic() - t0 < secs and lab.poll() is None:
            shim.poll(0)
            try: buf, info = cap.capture(damage=not first, timeout=1.0)
            except TimeoutError: continue
            d = fbs[back]; st, pitch = info['stride'], d['pitch']; tc = time.perf_counter()
            dst = memoryview(d['map']); rowb = W * 4
            for y in range(H): dst[y * pitch:y * pitch + rowb] = buf[y * st:y * st + rowb]
            copy_ms += (time.perf_counter() - tc) * 1e3
            props = planes_for(fd, reg, d['fb']); objs = list(props)
            if first: k.atomic(fd, k.ATOMIC_TEST_ONLY, objs, props)
            k.atomic(fd, 0, objs, props); first = False; back ^= 1; n += 1
        el = time.monotonic() - t0
        rep.update(frames=n, fps=round(n / el, 1), copy_ms_avg=round(copy_ms / max(n, 1), 2),
                   underruns={i: rg.underruns(c, reg['encoder_status'])[i] - u0[i] for i in u0})
        log('PRESENTED', n, 'frames', rep['fps'], 'fps, copy', rep['copy_ms_avg'], 'ms, underruns', rep['underruns'])
    except Exception as e:
        rep['error'] = repr(e); log('ERROR', repr(e))
    finally:
        if lab and lab.poll() is None:
            lab.terminate()
            try: lab.wait(10)
            except subprocess.TimeoutExpired: lab.kill(); lab.wait()
        if shim: shim.close()
        try:
            props = planes_for(fd, reg, reg['native_fb']); objs = list(props)
            k.atomic(fd, k.ATOMIC_TEST_ONLY, objs, props); k.atomic(fd, 0, objs, props); log('RESTORED native layout')
        except Exception as e: rep['restore_error'] = repr(e); log('RESTORE FAILED', repr(e))
        for d in fbs:
            try:
                fcntl.ioctl(fd, RMFB, struct.pack('<I', d['fb'])); fcntl.ioctl(fd, DESTROY_DUMB, struct.pack('<I', d['handle']))
                # the mapping is still exported to ctypes/memoryview: it is released with the process, not closed here
            except Exception as e: rep.setdefault('cleanup_errors', []).append(repr(e))
        os.close(fd)
        time.sleep(1); rep['after'] = dr.drm_view(reg); now = c.read(rg.DEBUG/'state')
        rep['state_native'] = now == native; rep['state_rebased'] = now in rg.rebased_states(c, reg, native)
        new = [l for l in c.dmesg().splitlines() if l not in dm0]; rep['dmesg_faults'] = [l for l in new if bg.FAULT.search(l)][:20]
        rep['log'] = dr.LOG; out.write_text(json.dumps(rep, indent=1)); os.chown(out, dr.UID, dr.GID)
        print('PRESENTER_RUN_DONE', json.dumps({x: rep.get(x) for x in ('mode_set', 'frames', 'fps', 'copy_ms_avg', 'underruns', 'error', 'restore_error', 'cleanup_errors', 'state_native')}),
              'faults', len(rep['dmesg_faults']))

if __name__ == '__main__':
    try: main()
    except Exception as exc: sys.exit('STOP: %r' % exc)
