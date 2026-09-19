"""Host tests for display-owner pieces. No device access."""
import importlib.util, json, sys, unittest
from pathlib import Path
import drmkms as k
from ownerlog import OwnerLog
spec = importlib.util.spec_from_file_location('owner', Path(__file__).with_name('display-owner.py'))
owner = importlib.util.module_from_spec(spec); spec.loader.exec_module(owner)
class Hold(Exception): pass

class FakeK:
    def __init__(self, planes):
        self.planes = planes
        for name in ('OBJECT_CONNECTOR', 'OBJECT_CRTC', 'OBJECT_PLANE'): setattr(self, name, getattr(k, name))
    def properties(self, fd, obj, typ):
        if typ == k.OBJECT_CONNECTOR: return {'CRTC_ID': (1, 205)}
        if typ == k.OBJECT_CRTC: return {'ACTIVE': (2, 1), 'MODE_ID': (3, 340)}
        p = self.planes[obj]; return {'FB_ID': (10, p['fb']), 'type': (11, p['type'])}
    def plane_ids(self, fd): return list(self.planes)
    def get_plane(self, fd, pid): p = self.planes[pid]; return {'plane_id': pid, 'crtc_id': p['crtc'], 'fb_id': p['fb'], 'possible_crtcs': 1}

def splash(**over):
    planes = {pid: {'crtc': 0, 'fb': 0, 'type': 0} for pid in [95, 128] + list(range(132, 204, 4))[:18]}
    planes[95] = {'crtc': 205, 'fb': 0, 'type': 1}; planes[128] = {'crtc': 205, 'fb': 0, 'type': 1}
    for pid, v in over.items(): planes[int(pid[1:])] = v
    return planes

class T(unittest.TestCase):
    def test_ioctls(self):
        self.assertEqual((k.IOCTL['VERSION'], k.IOCTL['SET_MASTER'], k.IOCTL['SET_CLIENT_CAP'], k.IOCTL['CREATEPROPBLOB'], k.IOCTL['GETPROPBLOB']),
                         (0xC0406400, 0x641E, 0x4010640D, 0xC01064BD, 0xC01064AC))
        self.assertEqual(k.MODEINFO.size, 68)
        raw = k.MODEINFO.pack(915552, 1904, 2084, 2116, 2244, 0, 3040, 3066, 3070, 3400, 0, 120, 0, 0x48, b'1904x3040')
        self.assertTrue(all(k.mode_fields(raw)[f] == v for f, v in k.REVIEWED_TIMING.items()))
    def test_pattern_matches_kms_held(self):
        p = owner.pattern
        self.assertEqual(p(1904, 3040, 0, 0), 0xffffff); self.assertEqual(p(1904, 3040, 300, 100), 0xffff00)
        self.assertEqual(p(1904, 3040, 1800, 2000), 0x404040); self.assertEqual(p(1904, 3040, 100, 2300), 0x101010)
        self.assertEqual(p(1904, 3040, 200, 2300), 0xffffff)
    def discover(self, planes):
        owner.k = FakeK(planes); owner.open = lambda *a, **kw: __import__('io').StringIO('69\n')
        def hold(reason): raise Hold(reason)
        owner.hold = hold
        try: return owner.discover(0)
        finally: owner.k = k; del owner.open
    def test_discover_reviewed(self):
        topo, _, _ = self.discover(splash()); self.assertEqual(topo, {'connector': 69, 'crtc': 205, 'left': 95, 'right': 128, 'planes': 20})
    def test_discover_rejects_fb_present(self):
        with self.assertRaises(Hold): self.discover(splash(p95={'crtc': 205, 'fb': 78, 'type': 1}))
    def test_discover_rejects_third_plane(self):
        with self.assertRaises(Hold): self.discover(splash(p132={'crtc': 205, 'fb': 0, 'type': 1}))
    def test_discover_rejects_overlay(self):
        with self.assertRaises(Hold): self.discover(splash(p128={'crtc': 205, 'fb': 0, 'type': 0}))
    def test_log_good(self):
        l = OwnerLog(); topo = json.dumps({'connector': 69, 'crtc': 205, 'left': 95, 'right': 128, 'planes': 20})
        seq = ['FIRST_DRM_OPEN fd=3 (x)', 'TOPOLOGY ' + topo, 'SPLASH_MODE {}', 'COMMIT_A_TEST_ONLY_PASS small_fb=78 mode_blob=340',
               'READY_FOR_COMMIT_A', 'COMMIT_A_ENTER', 'COMMIT_A_RETURNED', 'COMMIT_B_TEST_ONLY_PASS native_fb=79', 'READY_FOR_COMMIT_B',
               'COMMIT_B_ENTER', 'COMMIT_B_RETURNED', 'OWNER_READY ' + json.dumps({'fd': 3, 'topology': json.loads(topo), 'mode_blob': 340, 'small_fb': 78, 'native_fb': 79}),
               'HELD pid=1 reason=DISPLAY_OWNER_HOLDING']
        for s in seq:
            ev = l.feed(s)
            if ev == 'gate1': l.gates = 1
            if ev == 'gate2': l.gates = 2
        self.assertEqual(l.info['native_fb'], 79)
    def test_log_no_gate(self):
        l = OwnerLog(); topo = json.dumps({'connector': 69, 'crtc': 205, 'left': 95, 'right': 128, 'planes': 20})
        for s in ['FIRST_DRM_OPEN fd=3 (x)', 'TOPOLOGY ' + topo, 'SPLASH_MODE {}', 'COMMIT_A_TEST_ONLY_PASS x', 'READY_FOR_COMMIT_A']: l.feed(s)
        with self.assertRaises(RuntimeError): l.feed('COMMIT_A_ENTER')
    def test_log_topology_mismatch(self):
        l = OwnerLog(); l.feed('FIRST_DRM_OPEN fd=3 (x)')
        with self.assertRaises(RuntimeError): l.feed('TOPOLOGY ' + json.dumps({'connector': 70, 'crtc': 205, 'left': 95, 'right': 128, 'planes': 20}))

if __name__ == '__main__': unittest.main()
