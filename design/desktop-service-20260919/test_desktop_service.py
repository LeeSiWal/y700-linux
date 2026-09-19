#!/usr/bin/env python3
"""T-1 host tests for desktop-service.py pure helpers (no DRM, no root)."""
import importlib.util, unittest
from pathlib import Path
s = importlib.util.spec_from_file_location('ds', Path(__file__).resolve().parent/'desktop-service.py'); ds = importlib.util.module_from_spec(s); s.loader.exec_module(ds)
BOOT = 'b' * 36; OWN = {'pid': 100, 'starttime': 5}; NATIVE = 335

def rec(fbs=(340, 342), handles=(1, 2), boot=BOOT, owner=OWN):
    return {'boot_id': boot, 'owner': dict(owner), 'fbs': list(fbs), 'handles': list(handles)}

class Recovery(unittest.TestCase):
    def act(self, r, planes, existing=(335, 340, 342)): return ds.recovery_action(r, BOOT, OWN, planes, NATIVE, set(existing))
    def test_clean_boot(self):            self.assertEqual(self.act(None, [335, 335], (335,)), ('none', []))
    def test_crash_left_our_fbs_on_screen(self): self.assertEqual(self.act(rec(), [340, 340]), ('restore', [340, 342]))
    def test_crash_mid_flip(self):        self.assertEqual(self.act(rec(), [340, 342]), ('restore', [340, 342]))
    def test_one_plane_native(self):      self.assertEqual(self.act(rec(), [335, 342]), ('restore', [340, 342]))
    def test_restored_but_not_removed(self): self.assertEqual(self.act(rec(), [335, 335]), ('cleanup', [340, 342]))
    def test_record_only_handle(self):    self.assertEqual(self.act(rec(fbs=(), handles=(1,)), [335, 335], (335,)), ('cleanup', []))
    def test_unknown_fb_refused(self):    self.assertEqual(self.act(rec(), [999, 340], (335, 340, 342, 999))[0], 'refuse')
    def test_unknown_fb_without_record(self): self.assertEqual(self.act(None, [340, 340])[0], 'refuse')
    def test_other_boot_record(self):     self.assertEqual(self.act(rec(boot='c' * 36), [335, 335])[0], 'refuse')
    def test_other_owner_record(self):    self.assertEqual(self.act(rec(owner={'pid': 100, 'starttime': 6}), [340, 340])[0], 'refuse')
    def test_native_fb_gone(self):        self.assertEqual(self.act(rec(), [340, 340], (340, 342))[0], 'refuse')
    def test_only_existing_fbs_removed(self): self.assertEqual(self.act(rec(), [340, 340], (335, 340)), ('restore', [340]))

class Bringup(unittest.TestCase):
    def doc(self, *steps, boot=BOOT): return {'boot_id': boot, 'runs': [{'steps': [{'stage': s, 'result': r} for s, r in steps]}]}
    def test_ready(self):        self.assertTrue(ds.bringup_ready(self.doc(('display', 'ok'), ('owner', 'ok'), ('gpu', 'ok')), BOOT))
    def test_gpu_missing(self):  self.assertFalse(ds.bringup_ready(self.doc(('owner', 'ok')), BOOT))
    def test_gpu_failed(self):   self.assertFalse(ds.bringup_ready(self.doc(('owner', 'ok'), ('gpu', 'stop')), BOOT))
    def test_other_boot(self):   self.assertFalse(ds.bringup_ready(self.doc(('owner', 'ok'), ('gpu', 'ok'), boot='c' * 36), BOOT))
    def test_no_file(self):      self.assertFalse(ds.bringup_ready(None, BOOT))
    def test_split_runs(self):
        d = self.doc(('owner', 'ok')); d['runs'].append({'steps': [{'stage': 'gpu', 'result': 'ok'}]})
        self.assertTrue(ds.bringup_ready(d, BOOT))
    def test_real_file(self):
        import json
        p = Path('/home/siwal/y700-agent/bringup-684daae1.json')
        if p.exists(): d = json.loads(p.read_text()); self.assertTrue(ds.bringup_ready(d, d['boot_id']))

class Rotation(unittest.TestCase):
    def test_default_and_matrices(self):
        rot, t, m = ds.rotation_config(); self.assertIn(rot, (0, 90, 180, 270)); self.assertEqual(t, {0: 0, 90: 1, 180: 2, 270: 3}[rot])
        a, b, c, d, e, f = map(float, m.split())
        pts = sorted((a * u + b * v + c, d * u + e * v + f) for u, v in ((0, 0), (1, 0), (0, 1), (1, 1)))
        self.assertEqual(pts, [(0, 0), (0, 1), (1, 0), (1, 1)])
    def test_panel_verified_270(self):
        # rotation 270 with '0 1 0 -1 0 1' gave touch 180 degrees off on the panel; the verified matrix is its negation
        rot = {90: '0 1 0 -1 0 1', 270: '0 -1 1 1 0 0'}
        for r, m in rot.items():
            a, b, c, d, e, f = map(float, m.split()); self.assertEqual(sorted((a*u+b*v+c, d*u+e*v+f) for u, v in ((0,0),(1,0),(0,1),(1,1))), [(0,0),(0,1),(1,0),(1,1)])

class Ctl(unittest.TestCase):
    def pc(self, line, bl=2048, rot=270): return ds.parse_ctl(line, bl, 4095, rot)
    def test_get(self):        self.assertEqual(self.pc('brightness get'), ('get', '50'))
    def test_up_down(self):    self.assertEqual(self.pc('brightness up'), ('brightness', 2457)); self.assertEqual(self.pc('brightness down'), ('brightness', 1638))
    def test_clamp_low(self):  self.assertEqual(self.pc('brightness 0'), ('brightness', ds.BL_MIN)); self.assertEqual(self.pc('brightness down', bl=300), ('brightness', ds.BL_MIN))
    def test_clamp_high(self): self.assertEqual(self.pc('brightness 100'), ('brightness', 4095)); self.assertEqual(self.pc('brightness up', bl=4095), ('brightness', 4095))
    def test_bad(self):
        for l in ('', 'brightness', 'brightness -5', 'brightness 1e9x', 'rm -rf /', 'rotate 45', 'rotate', 'x' * 100):
            self.assertEqual(self.pc(l)[0], 'error', l)
    def test_rotate(self):
        self.assertEqual(self.pc('rotate next', rot=270), ('rotate', 90)); self.assertEqual(self.pc('rotate next', rot=90), ('rotate', 0))
        self.assertEqual(self.pc('rotate next', rot=0), ('rotate', 270)); self.assertEqual(self.pc('rotate next', rot=180), ('rotate', 270))
        self.assertEqual(self.pc('rotate 90'), ('rotate', 90)); self.assertEqual(self.pc('rotate get'), ('get', '270'))

class Shell(unittest.TestCase):
    def test_session_env(self):
        e = ds.session_env()
        self.assertIn('VK_DRIVER_FILES', e); self.assertIn('XDG_DATA_DIRS', e); self.assertTrue(e['PATH'].startswith('/home/siwal/y700-fex/root/usr/bin'))
    def test_shell_values(self):
        import json
        orig = ds.desktop_conf
        try:
            for v, ok in (('labwc', True), ('phosh', True), ('gnome', False)):
                ds.desktop_conf = lambda v=v: {'shell': v}
                if ok: self.assertEqual(ds.shell_config(), v)
                else: self.assertRaises(ds.Precondition, ds.shell_config)
            ds.desktop_conf = lambda: {}; self.assertEqual(ds.shell_config(), 'labwc')
        finally: ds.desktop_conf = orig

class Resolution(unittest.TestCase):
    def test_sizes(self):
        for pct, (w, h) in ds.RESOLUTIONS.items():
            self.assertEqual(w % 4, 0); self.assertEqual(h % 2, 0); self.assertLessEqual(w, ds.W); self.assertLessEqual(h, ds.H)
            self.assertAlmostEqual(w / ds.W, h / ds.H, delta=0.01)                 # same aspect as the panel
            self.assertAlmostEqual(h / (w / ds.LOGICAL_W), 1520, delta=3)          # logical desktop stays 952x1520
    def test_ctl(self):
        self.assertEqual(ds.parse_ctl('resolution 80', 100, 4095, 270), ('resolution', 80))
        self.assertEqual(ds.parse_ctl('resolution get', 100, 4095, 270), ('get', 'resolution'))
        self.assertEqual(ds.parse_ctl('resolution 33', 100, 4095, 270)[0], 'error')
        self.assertEqual(ds.parse_ctl('resolution 100x', 100, 4095, 270)[0], 'error')

if __name__ == '__main__': unittest.main(verbosity=1)
