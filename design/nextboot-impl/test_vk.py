"""Host tests for run-vk generalisation (planes_on with real b4e16b26 state text, generic anim parser). No device access."""
import importlib.util, json, unittest
from pathlib import Path
from animlog import AnimLog
spec = importlib.util.spec_from_file_location('rv', Path(__file__).with_name('run-vk.py'))
rv = importlib.util.module_from_spec(spec); spec.loader.exec_module(rv)
BASE = Path('/home/siwal/y700-agent/vkrender-a9156afd13ae757f/state-now.txt').read_text()       # both planes on FB343
FB335 = Path('/home/siwal/y700-agent/screen-rate1200-b452a79e83e608a1/expected-state.txt').read_text()

class T(unittest.TestCase):
    def test_planes_on_real(self):
        self.assertTrue(rv.planes_on(BASE, BASE, 343, 343))
        a = BASE.replace('\tfb=343\n', '\tfb=346\n').replace('start=0010485d', 'start=00200000')
        self.assertTrue(rv.planes_on(a, BASE, 343, 346)); self.assertFalse(rv.planes_on(a, BASE, 343, 347))
        self.assertFalse(rv.planes_on(a.replace('\tfb=346\n', '\tfb=347\n', 1), BASE, 343, 346))
        self.assertFalse(rv.planes_on(a.replace('\tfb=0\n', '\tfb=9\n', 1), BASE, 343, 346))
        self.assertFalse(rv.planes_on(a.replace('allocated by = python3', 'allocated by = x', 1), BASE, 343, 346))
        self.assertFalse(rv.planes_on(a.replace('size=23347200', 'size=5836800', 1), BASE, 343, 346))
    def test_planes_on_other_base(self):          # a different boot's base fb/owner comm/addresses: nothing hardcoded
        a = FB335.replace('\tfb=335\n', '\tfb=400\n').replace('start=00100591', 'start=00abcdef')
        self.assertTrue(rv.planes_on(a, FB335, 335, 400))
    def test_anim_parser_generic(self):
        n = 3; A, Bf = 90, 91
        lines = ['INHERITED_DRM_FILE planes 95/128 on FB80, mode active; frames=3', 'EXISTING_FBS [79, 80]', 'READY_FOR_SETUP',
                 'VULKAN_READY memtype=0 alloc=23347200', 'BUFFER A fb=90 handle=5', 'BUFFER B fb=91 handle=6', 'TEST_ONLY_PASS A B BASE',
                 'READY_FOR_ANIMATION', 'ANIMATION_ENTER'] + \
                ['F %d fb=%d render_ms=2.6 copy_ms=8.8 flip_ms=5.3 t_ms=%.1f' % (k, Bf if k % 2 else A, 16.7 * k) for k in range(1, n + 1)] + \
                ['ANIMATION_SUMMARY frames=3 seconds=0.050 fps=60.00 max_frame_ms=16.70', 'RETURNED_BASE', 'VK_DESTROYED',
                 'HELD pid=1 reason=ANIMATION_DONE_BASE_RETAINED']
        log = AnimLog(n, 80)
        for x in lines:
            ev = log.feed(x)
            if ev == 'gate1': log.gates = 1
            if ev == 'gate2': log.gates = 2
        self.assertEqual(log.fb, {'A': 90, 'B': 91})
        log2 = AnimLog(n, 80)
        for x in lines[:4]:
            if log2.feed(x) == 'gate1': log2.gates = 1
        with self.assertRaises(RuntimeError): log2.feed('BUFFER A fb=79 handle=5')      # reuses an existing fb

if __name__ == '__main__': unittest.main()
