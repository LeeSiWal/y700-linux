"""Unit tests for xguard.decide() and one fake-display pass through xguard.run() (no X server needed)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xguard as xg

A, B, C = 0x100, 0x200, 0x300


class TestDecide(unittest.TestCase):
    def test_single_window_is_never_touched(self):
        self.assertEqual(xg.decide(A, [A], 99, 2.0), (None, 0))

    def test_active_on_top_is_fine(self):
        self.assertEqual(xg.decide(A, [B, A], 99, 2.0), (None, 0))

    def test_raise_first_then_close(self):
        self.assertEqual(xg.decide(A, [A, B], 0.0, 2.0), ('raise', B))
        self.assertEqual(xg.decide(A, [A, B], 1.9, 2.0), ('raise', B))
        self.assertEqual(xg.decide(A, [A, B], 2.0, 2.0), ('close', B))

    def test_only_the_window_on_top_is_closed(self):
        self.assertEqual(xg.decide(A, [A, B, C], 5.0, 2.0), ('close', C))

    def test_no_active_window_means_no_action(self):
        self.assertEqual(xg.decide(0, [A, B], 5.0, 2.0), (None, 0))

    def test_active_window_not_mapped_means_no_action(self):
        # phosh shows a wayland app: the active X window is gone from the toplevel list
        self.assertEqual(xg.decide(C, [A, B], 5.0, 2.0), (None, 0))


class FakeDisplay:
    def __init__(self, active, tops, classes):
        self.active, self.tops, self.classes = active, tops, classes
        self.raised, self.closed = [], []

    def active_window(self):
        return self.active

    def toplevels(self):
        return list(self.tops)

    def wm_class(self, w):
        return self.classes.get(w, '')

    def title(self, w):
        return 'w%x' % w

    def raise_window(self, w):
        self.raised.append(w)

    def close_window(self, w):
        self.closed.append(w)
        self.tops = [t for t in self.tops if t != w]       # a polite close that the app honours


class TestRun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.old_here, xg.HERE = xg.HERE, self.dir.name
        self.addCleanup(lambda: setattr(xg, 'HERE', self.old_here))
        self.old_log, xg.log = xg.log, lambda *a: None
        self.addCleanup(lambda: setattr(xg, 'log', self.old_log))

    def write_conf(self, **kw):
        with open(os.path.join(self.dir.name, 'desktop.json'), 'w') as f:
            json.dump(kw, f)

    def drive(self, dpy, ticks):
        """run() without sleeping: a fake clock that jumps 1 s per tick, stopped by StopIteration"""
        t = [0.0]
        def now():
            t[0] += 1.0
            return t[0]
        seq = iter(range(ticks))
        def sleep(_):
            next(seq)
        old_sleep, xg.time.sleep = xg.time.sleep, sleep
        try:
            xg.run(dpy, sleep=0, now=now)
        except StopIteration:
            pass
        finally:
            xg.time.sleep = old_sleep

    def test_steam_window_on_top_is_closed(self):
        self.write_conf(x_guard='close', x_guard_classes=['steam'], x_guard_grace=2.0)
        dpy = FakeDisplay(A, [A, B], {A: 'steam', B: 'steam'})
        self.drive(dpy, 6)
        self.assertEqual(dpy.closed, [B])
        self.assertEqual(dpy.tops, [A])

    def test_other_apps_are_left_alone(self):
        self.write_conf(x_guard='close', x_guard_classes=['steam'], x_guard_grace=2.0)
        dpy = FakeDisplay(A, [A, B], {A: 'steam', B: 'foot'})
        self.drive(dpy, 6)
        self.assertEqual(dpy.closed, [])

    def test_warn_mode_never_closes(self):
        self.write_conf(x_guard='warn', x_guard_classes=['steam'], x_guard_grace=2.0)
        dpy = FakeDisplay(A, [A, B], {A: 'steam', B: 'steam'})
        self.drive(dpy, 6)
        self.assertEqual(dpy.closed, [])

    def test_off_mode_does_nothing_at_all(self):
        self.write_conf(x_guard='off')
        dpy = FakeDisplay(A, [A, B], {A: 'steam', B: 'steam'})
        self.drive(dpy, 4)
        self.assertEqual((dpy.closed, dpy.raised), ([], []))

    def test_missing_config_defaults_to_close_steam(self):
        dpy = FakeDisplay(A, [A, B], {A: 'steam', B: 'steam'})
        self.drive(dpy, 6)
        self.assertEqual(dpy.closed, [B])


if __name__ == '__main__':
    unittest.main(verbosity=1)
