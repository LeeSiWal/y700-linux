import unittest, chgctl as c
CFG = {'start': 70, 'stop': 80}
def st(cap=75, temp=350, v=4300000, vmax=4530000, en=1): return {'capacity': cap, 'temp': temp, 'voltage_now': v, 'voltage_max': vmax, 'charging_enabled': en}
class T(unittest.TestCase):
    def test_band(self):
        self.assertEqual(c.decide(st(cap=80), CFG, False)[0], 0)
        self.assertEqual(c.decide(st(cap=70), CFG, False)[0], 1)
        self.assertIsNone(c.decide(st(cap=75), CFG, False)[0])
    def test_hot_latch(self):
        w, l, _ = c.decide(st(cap=50, temp=455), CFG, False); self.assertEqual((w, l), (0, True))
        w, l, _ = c.decide(st(cap=50, temp=420), CFG, True); self.assertEqual((w, l), (0, True))
        w, l, _ = c.decide(st(cap=50, temp=395), CFG, True); self.assertEqual((w, l), (1, False))
    def test_overvoltage(self):
        self.assertEqual(c.decide(st(cap=50, v=4590000), CFG, False)[:2], (0, True))
        self.assertIsNone(c.decide(st(cap=75, v=4570000), CFG, False)[0])
    def test_unreadable(self):
        s = st(cap=50); s['temp'] = None; self.assertEqual(c.decide(s, CFG, False)[0], 0)


class Settle(unittest.TestCase):
    def test_lagging_readback(self):
        seq = iter([1, 1, 0]); v, _ = c.settle(0, read=lambda: next(seq), secs=2, step=0)
        self.assertEqual(v, 0)
    def test_timeout(self):
        v, secs = c.settle(0, read=lambda: 1, secs=0.05, step=0.01); self.assertEqual(v, 1)
if __name__ == '__main__': unittest.main()
