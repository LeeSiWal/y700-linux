#!/usr/bin/env python3
"""Host tests for touchproxy.Rotator (no devices)."""
import unittest, touchproxy as tp
A, S, K = tp.EV_ABS, tp.EV_SYN, tp.EV_KEY
X, Y, SLOT, TID = tp.ABS_MT_POSITION_X, tp.ABS_MT_POSITION_Y, tp.ABS_MT_SLOT, tp.ABS_MT_TRACKING_ID
XR, YR = (0, 1903), (0, 3039)
M270 = '0 -1 1 1 0 0'

def run(r, evs):
    out = []
    for e in evs: out += r.feed(*e)
    return out

def positions(out):
    cur = 0; pos = {}
    for t, c, v in out:
        if t == A and c == SLOT: cur = v
        elif t == A and c in (X, Y): pos.setdefault(cur, {})[c] = v
    return pos

class RotatorTest(unittest.TestCase):
    def test_identity(self):
        r = tp.Rotator(XR, YR, '1 0 0 0 1 0')
        out = run(r, [(A, TID, 5), (A, X, 100), (A, Y, 200), (K, tp.BTN_TOUCH, 1), (S, 0, 0)])
        self.assertEqual(positions(out)[0], {X: 100, Y: 200}); self.assertEqual(out[-1], (S, 0, 0))
        self.assertIn((A, TID, 5), out); self.assertIn((K, tp.BTN_TOUCH, 1), out)
    def test_270_corners(self):
        # matrix verified on the panel under labwc: x' = 1 - v, y' = u (normalized)
        r = tp.Rotator(XR, YR, M270)
        self.assertEqual(positions(run(r, [(A, X, 0), (A, Y, 0), (S, 0, 0)]))[0], {X: 1903, Y: 0})
        self.assertEqual(positions(run(r, [(A, X, 1903), (A, Y, 3039), (S, 0, 0)]))[0], {X: 0, Y: 3039})
        self.assertEqual(positions(run(r, [(A, X, 1903), (A, Y, 0), (S, 0, 0)]))[0], {X: 1903, Y: 3039})
    def test_only_changed_axis_keeps_other(self):
        r = tp.Rotator(XR, YR, '1 0 0 0 1 0')
        run(r, [(A, X, 10), (A, Y, 20), (S, 0, 0)])
        self.assertEqual(positions(run(r, [(A, X, 30), (S, 0, 0)]))[0], {X: 30, Y: 20})
    def test_two_slots_and_slot_select(self):
        r = tp.Rotator(XR, YR, '1 0 0 0 1 0')
        out = run(r, [(A, SLOT, 0), (A, TID, 1), (A, X, 1), (A, Y, 2), (A, SLOT, 1), (A, TID, 2), (A, X, 3), (A, Y, 4), (S, 0, 0)])
        self.assertEqual(positions(out), {0: {X: 1, Y: 2}, 1: {X: 3, Y: 4}})
        # tracking ids land on the right slot
        cur = 0; tids = {}
        for t, c, v in out:
            if t == A and c == SLOT: cur = v
            if t == A and c == TID: tids[cur] = v
        self.assertEqual(tids, {0: 1, 1: 2})
        # next frame: slot 1 lifts without a SLOT event from the source (source current slot is still 1)
        out2 = run(r, [(A, TID, -1), (S, 0, 0)])
        cur = r.vslot if not any(c == SLOT for _, c, _ in out2) else None
        self.assertEqual([e for e in out2 if e[1] == TID], [(A, TID, -1)]); self.assertEqual(r.vslot, 1)
    def test_gesture_keys_dropped(self):
        r = tp.Rotator(XR, YR, M270)
        self.assertEqual(run(r, [(K, 116, 1), (K, 143, 1)]), [])
    def test_live_matrix_change(self):
        r = tp.Rotator(XR, YR, '1 0 0 0 1 0'); r.set_matrix(M270)
        self.assertEqual(positions(run(r, [(A, X, 0), (A, Y, 0), (S, 0, 0)]))[0], {X: 1903, Y: 0})
    def test_clamp(self):
        r = tp.Rotator(XR, YR, M270)
        p = positions(run(r, [(A, X, 5000), (A, Y, -50), (S, 0, 0)]))[0]
        self.assertTrue(0 <= p[X] <= 1903 and 0 <= p[Y] <= 3039)

if __name__ == '__main__': unittest.main(verbosity=1)
