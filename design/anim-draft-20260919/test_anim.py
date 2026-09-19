"""Host-only checks for the animation bundle. No device access."""
import ctypes as C, hashlib, importlib.util, unittest
from pathlib import Path
import vkabi as v, drmabi as d, animpattern as ap
from animlog import AnimLog
spec = importlib.util.spec_from_file_location('ra', Path(__file__).with_name('run-anim.py'))
ra = importlib.util.module_from_spec(spec); spec.loader.exec_module(ra)
BASE = Path(__file__).with_name('expected-state.txt').read_text()

RES = (0, 78, 335, 341, 342, 343, 344, 345)
def good(n=4, A=346, B=347, start=345):
    l = ['INHERITED_DRM_FILE planes 95/128 on FB%d, mode 340 active; frames=%d' % (start, n), 'READY_FOR_SETUP',
         'TU: info: Created an instance', 'VULKAN_READY memtype=0 alloc=23347200', 'BUFFER A fb=%d handle=20' % A,
         'BUFFER B fb=%d handle=21' % B, 'TEST_ONLY_PASS A B FB343', 'READY_FOR_ANIMATION', 'ANIMATION_ENTER']
    l += ['F %d fb=%d render_ms=6.1 copy_ms=4.0 flip_ms=8.3 t_ms=%.1f' % (k, B if k % 2 else A, 18.4 * k) for k in range(1, n + 1)]
    return l + ['ANIMATION_SUMMARY frames=%d seconds=0.074 fps=54.05 max_frame_ms=18.40' % n, 'RETURNED_FB343', 'VK_DESTROYED',
                'HELD pid=1 reason=ANIMATION_DONE_FB343_RETAINED']
def run(lines, n=4, start=345):
    log = AnimLog(n, start, RES)
    for x in lines:
        ev = log.feed(x)
        if ev == 'gate1': log.gates = 1
        if ev == 'gate2': log.gates = 2
    return log

class T(unittest.TestCase):
    def test_layouts(self):
        self.assertEqual(C.sizeof(v.VkPushConstantRange), 12); self.assertEqual(d.EVENT_VBLANK.size, 32)
        self.assertEqual(hashlib.sha256(Path(__file__).with_name('anim.spv').read_bytes()).hexdigest(),
                         'ccea8b5118002512a9f86a4ee48cc4223ff302f6df8af77a116fc8d5e17d713f')
    def test_pattern(self):
        self.assertEqual(ap.pixel(710, 1520, 0), 0xFFFFFF); self.assertEqual(ap.pixel(1904, 5, 3), 0)
        self.assertTrue(all(0 <= x < 1920 and 0 <= y < 3040 for x, y in ap.points(1100)))
    def test_good(self):
        log = run(good()); self.assertEqual(len(log.done), 4); self.assertEqual(log.summary['fps'], 54.05)
    def bad(self, i, new):
        l = good(); l[i] = new
        with self.assertRaises(RuntimeError): run(l)
    def test_wrong_back_buffer(self): self.bad(9, 'F 1 fb=346 render_ms=6.1 copy_ms=4.0 flip_ms=8.3 t_ms=18.4')
    def test_skipped_frame(self): self.bad(10, 'F 3 fb=347 render_ms=6.1 copy_ms=4.0 flip_ms=8.3 t_ms=18.4')
    def test_reused_fb(self): self.bad(4, 'BUFFER A fb=345 handle=20')
    def test_flip_error(self): self.bad(11, 'STOP: flip frame=3 TimeoutError()')
    def test_early_summary(self):
        l = good(); del l[12]
        with self.assertRaises(RuntimeError): run(l)
    def test_no_gate2(self):
        log = AnimLog(4, 345, RES)
        for x in good()[:8]:
            if log.feed(x) == 'gate1': log.gates = 1
        with self.assertRaises(RuntimeError): log.feed('ANIMATION_ENTER')
    def test_start_line_must_match(self):
        with self.assertRaises(RuntimeError): run(good(start=343))           # log says 343, supervisor expects 345
    def test_atomic_user_data(self):
        seen = {}
        def fake(fd, req, buf, mutate): seen['req'] = req; seen['buf'] = bytes(buf)
        orig = d.fcntl.ioctl; d.fcntl.ioctl = fake
        try: d.atomic(3, d.ATOMIC_PAGE_FLIP_EVENT, [95, 128], {95: [(1, 347)], 128: [(2, 347)]}, 7)
        finally: d.fcntl.ioctl = orig
        f = d.ATOMIC.unpack(seen['buf'])
        self.assertEqual(seen['req'], 0xC03864BC); self.assertEqual((f[0], f[1], f[6], f[7]), (1, 2, 0, 7))
    def test_planes_on(self):
        self.assertTrue(ra.planes_on(BASE, BASE, 343))
        a = BASE.replace('\tfb=343\n', '\tfb=344\n').replace('start=0010485d', 'start=00200000')
        self.assertTrue(ra.planes_on(a, BASE, 344)); self.assertFalse(ra.planes_on(a, BASE, 345))
        self.assertFalse(ra.planes_on(a.replace('\tfb=344\n', '\tfb=345\n', 1), BASE, 344))        # torn: planes on different fbs
        self.assertFalse(ra.planes_on(a.replace('allocated by = python3', 'allocated by = kms-held', 1), BASE, 344))
        self.assertFalse(ra.planes_on(a.replace('\tfb=0\n', '\tfb=9\n', 1), BASE, 344))

if __name__ == '__main__': unittest.main()
