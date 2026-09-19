"""padproxy host tests: Kishi -> Xbox 360 event translation and device selection (no device access)."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import padproxy as pp
from touchproxy import EV_KEY, EV_ABS, EV_SYN

class Mapper(unittest.TestCase):
    def setUp(self): self.m = pp.PadMapper(pp.KISHI)
    def test_buttons(self):
        rec = {0x130: pp.BTN_A, 0x131: pp.BTN_B, 0x133: pp.BTN_X, 0x134: pp.BTN_Y, 0x136: pp.BTN_TL, 0x137: pp.BTN_TR,
               0x13a: pp.BTN_SELECT, 0x13b: pp.BTN_START, 0x13c: pp.BTN_MODE, 0x13d: pp.BTN_THUMBL, 0x13e: pp.BTN_THUMBR}
        for src, dst in rec.items(): self.assertEqual(self.m.feed(EV_KEY, src, 1), [(EV_KEY, dst, 1)])
        self.assertEqual(self.m.feed(EV_KEY, 0x138, 1), []); self.assertEqual(self.m.feed(EV_KEY, 0x139, 1), [])   # digital LT/RT dropped
    def test_axes(self):
        self.assertEqual(self.m.feed(EV_ABS, pp.ABS_Z, -32767), [(EV_ABS, pp.ABS_RX, -32767)])       # right stick x
        self.assertEqual(self.m.feed(EV_ABS, pp.ABS_RZ, 32767), [(EV_ABS, pp.ABS_RY, 32767)])        # right stick y
        self.assertEqual(self.m.feed(EV_ABS, pp.ABS_BRAKE, 255), [(EV_ABS, pp.ABS_Z, 255)])          # LT
        self.assertEqual(self.m.feed(EV_ABS, pp.ABS_GAS, 128), [(EV_ABS, pp.ABS_RZ, 128)])           # RT
        self.assertEqual(self.m.feed(EV_ABS, pp.ABS_HAT0Y, -1), [(EV_ABS, pp.ABS_HAT0Y, -1)])        # D-pad up
        self.assertEqual(self.m.feed(EV_SYN, 0, 0), [(EV_SYN, 0, 0)])
    def test_xbox_layout_complete(self):
        self.assertEqual(sorted(pp.KISHI['keys'].values()), sorted(pp.XBOX_KEYS))
        self.assertEqual(sorted(pp.KISHI['abs'].values()), sorted(pp.XBOX_ABS))

class Sources(unittest.TestCase):
    def test_only_gamepad_interface_of_listed_device(self):
        with tempfile.TemporaryDirectory() as t:
            def mk(ev, vid, pid, keywords, dev):
                d = Path(t)/ev/'device'; (d/'id').mkdir(parents=True); (d/'capabilities').mkdir()
                (d/'id'/'vendor').write_text(vid); (d/'id'/'product').write_text(pid)
                (d/'capabilities'/'key').write_text(keywords + '\n'); (Path(t)/ev/'dev').write_text(dev + '\n')
            mk('event7', '1532', '0724', '3 0 0 0 0 0 0 ffff000000000000 0 0 0 0', '13:71')     # recorded caps (gamepad)
            mk('event9', '1532', '0724', '80 0 0 0 0 0 0 101040000000 1c000000000000 0', '13:73')  # consumer control
            mk('event4', '046d', 'c52b', '3 0 0 0 0 0 0 ffff000000000000 0 0 0 0', '13:68')     # another vendor
            saved = dict(pp.REMAPS); pp.REMAPS[(0x1532, 0x0724)] = pp.KISHI
            try: got = pp.remap_sources(t)
            finally: pp.REMAPS.clear(); pp.REMAPS.update(saved)
            self.assertEqual(list(got), ['event7']); self.assertEqual(got['event7'][:2], (13, 71))
    def test_hid_id(self): self.assertEqual(pp.hid_usb_id('0003:00001532:00000724'), (0x1532, 0x0724))

if __name__ == '__main__': unittest.main(verbosity=1)
