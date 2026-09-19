"""Host-only checks: DRM uAPI layouts, shader artifact, reference pattern, log parser, post-commit state matcher."""
import hashlib, importlib.util, json, unittest
from pathlib import Path
import drmabi as d, pattern
from renderlog import RenderLog

spec = importlib.util.spec_from_file_location('rr', Path(__file__).with_name('run-render.py'))
rr = importlib.util.module_from_spec(spec); spec.loader.exec_module(rr)
EXP = Path(__file__).with_name('expected-state.txt').read_text()

def good(fb=343):
    dev = json.dumps({'deviceID': 1141180977, 'deviceName': 'Adreno (TM) 840', 'vendorID': 20803})
    return ['INHERITED_DRM_FILE planes 95/128 on FB335, mode 340 active; no new DRM open', 'READY_FOR_RENDER', 'ICD_LOADED',
            'TU: info: Created an instance', 'INSTANCE_CREATED', 'MESSENGER_CREATED', 'PHYSICAL_DEVICE ' + dev,
            'QUEUE_FAMILY index=0 flags=0xf count=1', 'DEVICE_CREATED', 'BUFFER_READY size=23347200 alloc=23347200 memtype=0 flags=0x7',
            'PIPELINE_CREATED', 'COMMANDS_RECORDED dispatch=120x190x1 local=16x16', 'SUBMITTED', 'FENCE_SIGNALED ms=3.10',
            'VERIFY sampled=69565 mismatches=0 sentinel_words=0 first_bad=[]', 'VERIFY_PASS',
            'DUMB_COPIED handle=9 offset=4300900000 identical=True', 'VK_DESTROYED',
            'FRAMEBUFFER id=%d 1904x3040 pitch=7680 XR24 linear' % fb, 'TEST_ONLY_PASS', 'READY_FOR_COMMIT', 'COMMIT_ENTER',
            'COMMIT_RETURNED fb=%d' % fb, 'HELD pid=1 reason=GPU_FRAME_DISPLAYED_VISUAL_CONFIRMATION_REQUIRED']
def run(lines):
    log = RenderLog()
    for l in lines:
        ev = log.feed(l)
        if ev == 'gate1': log.gates = 1
        if ev == 'gate2': log.gates = 2
    return log

class T(unittest.TestCase):
    def test_drm_layouts(self):
        self.assertEqual((d.CREATE_DUMB.size, d.MAP_DUMB.size, d.FB_CMD2.size, d.OBJ_GETPROPS.size, d.GETPROPERTY.size, d.ATOMIC.size),
                         (32, 16, 104, 32, 64, 56))
        self.assertEqual(d.IOCTL['ATOMIC'], 0xC03864BC); self.assertEqual(d.IOCTL['CREATE_DUMB'], 0xC02064B2)
        self.assertEqual(d.IOCTL['ADDFB2'], 0xC06864B8); self.assertEqual(d.IOCTL['GETFB2'], 0xC06864CE)
        self.assertEqual(len(d.FB_CMD2.unpack(bytes(104))), 21)
    def test_shader(self):
        self.assertEqual(hashlib.sha256(Path(__file__).with_name('render.spv').read_bytes()).hexdigest(),
                         'b76f0e5f589ac37e7d0de5ccc12de39802e3fbc904def9ff47560086455d2809')
    def test_pattern(self):
        self.assertEqual(pattern.pixel(0, 0), 0x40); self.assertEqual(pattern.pixel(952 + 610, 1520), 0xFFFFFF)
        self.assertEqual(pattern.pixel(1903, 3039), 0xFFFF40)
        self.assertTrue(all(pattern.pixel(x, y) >> 24 == 0 for x, y in pattern.sample_points()[:5000]))
    def test_good(self): log = run(good()); self.assertEqual(log.fb, 343)
    def bad(self, i, new):
        l = good(); l[i] = new
        with self.assertRaises(RuntimeError): run(l)
    def test_verify_mismatch(self): self.bad(14, 'VERIFY sampled=69565 mismatches=3 sentinel_words=0 first_bad=[(0, 0)]')
    def test_sentinel_left(self): self.bad(14, 'VERIFY sampled=69565 mismatches=0 sentinel_words=12 first_bad=[]')
    def test_copy_mismatch(self): self.bad(16, 'DUMB_COPIED handle=9 offset=1 identical=False')
    def test_reused_fb(self): self.bad(18, 'FRAMEBUFFER id=335 1904x3040 pitch=7680 XR24 linear')
    def test_commit_fb(self):
        l = good(); l[22] = 'COMMIT_RETURNED fb=999'
        with self.assertRaises(RuntimeError): run(l)
    def test_commit_error(self): self.bad(22, 'STOP: commit errno=22')
    def test_no_gate2(self):
        log = RenderLog()
        for l in good()[:21]:
            if log.feed(l) == 'gate1': log.gates = 1
        with self.assertRaises(RuntimeError): log.feed('COMMIT_ENTER')
    def test_moved_to(self):
        new = EXP.replace('\tfb=335\n', '\tfb=343\n').replace('start=00100591', 'start=00200000').replace('allocated by = kms-held', 'allocated by = python3')
        self.assertTrue(rr.moved_to(new, EXP, 343))
        self.assertFalse(rr.moved_to(EXP, EXP, 343))
        self.assertTrue(rr.moved_to(Path(__file__).with_name('state-now-20260919.txt').read_text(), EXP, 343))   # real post-commit text
        self.assertFalse(rr.moved_to(new.replace('allocated by = python3', 'allocated by = evil', 1), EXP, 343))
        self.assertFalse(rr.moved_to(new.replace('\tfb=0\n', '\tfb=7\n', 1), EXP, 343))   # another plane changed
        self.assertFalse(rr.moved_to(new.replace('src-pos=952', 'src-pos=951', 1), EXP, 343))
        half = EXP.replace('\tfb=335\n', '\tfb=343\n', 1)
        self.assertFalse(rr.moved_to(half, EXP, 343))

if __name__ == '__main__': unittest.main()
