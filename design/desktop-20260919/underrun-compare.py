#!/usr/bin/env python3
"""D-3a: does a SINGLE full-width plane underrun even for a static picture? No compositor involved.
Phases on a borrowed duplicate of the owner's DRM file (the owner keeps its fd), all showing the native colour-bar FB:
  A  10 s  native layout (planes 95+128 halves)            -> underrun delta A
  B  10 s  single plane (95 = full 1904x3040, 128 off)      -> underrun delta B   (TEST_ONLY first, then commit)
  C   5 s  native layout restored (TEST_ONLY, commit)       -> underrun delta C
Result: underrun deltas, DRM state back to native, new kernel fault lines. One run per boot+tag.
Usage: sudo python3 -B underrun-compare.py"""
import hashlib, importlib.util, json, os, sys, time
from pathlib import Path
IMPL = Path('/home/siwal/y700-design/nextboot-impl'); sys.path.insert(0, str(IMPL))
import registry as rg, borrow_owner as bo, drmkms as k, bootguard as bg
_spec = importlib.util.spec_from_file_location('readers', IMPL/'runtime-readers.py')
c = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(c)
HERE = Path(__file__).resolve().parent
W, H = 1904, 3040

def layout(fd, reg, single):
    t = reg['topology']; fb = reg['native_fb']; half = W // 2
    lp = k.properties(fd, t['left'], k.OBJECT_PLANE); rp = k.properties(fd, t['right'], k.OBJECT_PLANE)
    def pl(p, sx, sw): return [(p['FB_ID'][0], fb), (p['CRTC_ID'][0], t['crtc']), (p['SRC_X'][0], sx << 16), (p['SRC_Y'][0], 0),
                               (p['SRC_W'][0], sw << 16), (p['SRC_H'][0], H << 16), (p['CRTC_X'][0], sx), (p['CRTC_Y'][0], 0),
                               (p['CRTC_W'][0], sw), (p['CRTC_H'][0], H)]
    props = {t['left']: pl(lp, 0, W), t['right']: [(rp['FB_ID'][0], 0), (rp['CRTC_ID'][0], 0)]} if single else \
            {t['left']: pl(lp, 0, half), t['right']: pl(rp, half, half)}
    objs = list(props); k.atomic(fd, k.ATOMIC_TEST_ONLY, objs, props); k.atomic(fd, 0, objs, props)

def ur(reg): return rg.underruns(c, reg['encoder_status'])
def delta(a, b): return {i: b[i] - a[i] for i in a}

def main():
    c.need(os.geteuid() == 0, 'run with sudo')
    boot = c.read('/proc/sys/kernel/random/boot_id').strip(); reg, _ = rg.load(c, boot)
    tag = os.environ.get('Y700_RUN_TAG', 'r1'); out = HERE/('underrun-compare-%s-%s.json' % (boot[:8], tag))
    c.need(not out.exists(), 'already run: ' + out.name)
    native = c.read(reg['native_state_file']); now = c.read(rg.DEBUG/'state')
    c.need(now == native or now in rg.rebased_states(c, reg, native), 'display not in the native state')
    cl = c.read(rg.DEBUG/'clients').splitlines(); rg.process_ok(c, reg['owner'])
    c.need(len(cl) == 2 and cl[1].split()[1] == str(reg['owner']['pid']) and cl[1].split()[3] == 'y', 'owner is not the sole DRM master')
    out.write_text(json.dumps({'boot_id': boot, 'attempt': time.time()})); os.chown(out, 1000, 1000)
    dm0 = set(c.dmesg().splitlines()); r = {'boot_id': boot, 'phases': {}}
    fd = bo.borrow_fd(c, reg)
    try:
        u = ur(reg); print('A native two planes 10 s', flush=True); time.sleep(10); u2 = ur(reg); r['phases']['A_native'] = delta(u, u2)
        layout(fd, reg, single=True); print('B single plane 10 s (colour bars should look the same)', flush=True)
        u = ur(reg); time.sleep(10); u2 = ur(reg); r['phases']['B_single'] = delta(u, u2)
    finally:
        try: layout(fd, reg, single=False); r['restored'] = True
        except Exception as e: r['restore_error'] = repr(e)
        os.close(fd)
    u = ur(reg); print('C native restored 5 s', flush=True); time.sleep(5); r['phases']['C_native'] = delta(u, ur(reg))
    now = c.read(rg.DEBUG/'state'); r['state_native'] = now == native; r['state_rebased'] = now in rg.rebased_states(c, reg, native)
    new = [l for l in c.dmesg().splitlines() if l not in dm0]; r['dmesg_faults'] = [l for l in new if bg.FAULT.search(l)][:20]
    r['dmesg_display'] = [l for l in new if any(x in l.lower() for x in ('sde', 'underrun', 'dsi', 'mdp', 'drm'))][-30:]
    out.write_text(json.dumps(r, indent=1)); os.chown(out, 1000, 1000)
    print('UNDERRUN_COMPARE', json.dumps(r['phases']), 'restored', r.get('restored'), r.get('restore_error'),
          'state_native', r['state_native'], 'faults', len(r['dmesg_faults']))

if __name__ == '__main__':
    try: main()
    except Exception as exc: sys.exit('STOP: %r' % exc)
