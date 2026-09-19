#!/usr/bin/env python3
"""D-1: can one primary plane scan out the full 1904x3040 frame (what Weston does)? Uses ONLY atomic TEST_ONLY commits
on a borrowed duplicate of the display-owner's DRM file, reusing the registered native FB (no allocation, no real commit).
Cases (all TEST_ONLY):
  native      : planes 95+128 halves of the native FB (current layout, control; must pass)
  left_full   : plane 95 = whole 1904x3040 FB, plane 128 disabled
  right_full  : plane 128 = whole 1904x3040 FB, plane 95 disabled
Also records the property names/values of both planes (read-only). Before and after: owner alive + sole master,
DRM state text unchanged, underruns unchanged. Closes only its own duplicate fd.
Usage: sudo python3 -B plane-probe.py"""
import hashlib, importlib.util, json, os, sys, time
from pathlib import Path
IMPL = Path('/home/siwal/y700-design/nextboot-impl'); sys.path.insert(0, str(IMPL))
import registry as rg, borrow_owner as bo, drmkms as k

_spec = importlib.util.spec_from_file_location('readers', IMPL/'runtime-readers.py')
c = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(c)
OUT = Path('/home/siwal/y700-design/desktop-20260919')
W, H = 1904, 3040

def snapshot(reg):
    rg.process_ok(c, reg['owner'])
    lines = c.read(rg.DEBUG/'clients').splitlines()
    c.need(len(lines) == 2 and lines[1].split()[1] == str(reg['owner']['pid']) and lines[1].split()[3] == 'y', 'DRM client/master changed')
    return {'state_sha256': hashlib.sha256(c.read(rg.DEBUG/'state').encode()).hexdigest(), 'underruns': rg.underruns(c, reg['encoder_status'])}

def plane_props(fd, pid):
    return {n: v for n, (i, v) in k.properties(fd, pid, k.OBJECT_PLANE).items()}

def main():
    c.need(os.geteuid() == 0, 'run with sudo')
    boot = c.read('/proc/sys/kernel/random/boot_id').strip()
    reg, _ = rg.load(c, boot); t = reg['topology']; crtc, L, R = t['crtc'], t['left'], t['right']
    out = OUT/('plane-probe-%s.json' % boot[:8]); c.need(not out.exists(), 'already probed this boot: ' + out.name)
    before = snapshot(reg)
    fd = bo.borrow_fd(c, reg); report = {'boot_id': boot, 'before': before, 'cases': {}}
    try:
        lp = k.properties(fd, L, k.OBJECT_PLANE); rp = k.properties(fd, R, k.OBJECT_PLANE)
        report['plane_props'] = {str(L): plane_props(fd, L), str(R): plane_props(fd, R)}
        fb = lp['FB_ID'][1]; c.need(fb and rp['FB_ID'][1] == fb, 'planes are not on one native FB')
        report['native_fb'] = fb
        def full(p): return [(p['FB_ID'][0], fb), (p['CRTC_ID'][0], crtc), (p['SRC_X'][0], 0), (p['SRC_Y'][0], 0),
                             (p['SRC_W'][0], W << 16), (p['SRC_H'][0], H << 16), (p['CRTC_X'][0], 0), (p['CRTC_Y'][0], 0),
                             (p['CRTC_W'][0], W), (p['CRTC_H'][0], H)]
        def off(p): return [(p['FB_ID'][0], 0), (p['CRTC_ID'][0], 0)]
        half = W // 2
        def halfp(p, x): return [(p['FB_ID'][0], fb), (p['CRTC_ID'][0], crtc), (p['SRC_X'][0], x << 16), (p['SRC_Y'][0], 0),
                                 (p['SRC_W'][0], half << 16), (p['SRC_H'][0], H << 16), (p['CRTC_X'][0], x), (p['CRTC_Y'][0], 0),
                                 (p['CRTC_W'][0], half), (p['CRTC_H'][0], H)]
        cases = {'native': {L: halfp(lp, 0), R: halfp(rp, half)},
                 'left_full': {L: full(lp), R: off(rp)},
                 'right_full': {R: full(rp), L: off(lp)}}
        for name, props in cases.items():
            objs = list(props)
            try: k.atomic(fd, k.ATOMIC_TEST_ONLY, objs, props); res = 'PASS'
            except OSError as e: res = 'FAIL errno %d (%s)' % (e.errno, e.strerror)
            report['cases'][name] = res; print('TEST_ONLY', name, res, flush=True)
    finally:
        os.close(fd)                       # only our duplicate; the owner keeps its file
    time.sleep(0.5); report['after'] = snapshot(reg)
    report['unchanged'] = report['after'] == before
    c.need(report['unchanged'], 'display state changed during a TEST_ONLY probe: %r -> %r' % (before, report['after']))
    new = [l for l in c.dmesg().splitlines()[-200:] if 'TEST_ONLY' in l or 'sde' in l.lower() and ('err' in l.lower() or 'fail' in l.lower())]
    report['dmesg_tail'] = new[-40:]
    out.write_text(json.dumps(report, indent=1)); os.chown(out, 1000, 1000)
    print('PLANE_PROBE_DONE', json.dumps(report['cases']), 'state unchanged', report['unchanged'])
    for l in report['dmesg_tail'][-8:]: print('  ', l)

if __name__ == '__main__':
    try: main()
    except Exception as exc: sys.exit('STOP: %s' % exc)
