import unittest
from pathlib import Path
import registry as rg, bootguard as bg

NATIVE = Path('/home/siwal/y700-agent/boot-ownerfinish-2c8f7f88ad0281db/native-state.txt')
AFTER = Path('/home/siwal/y700-design/suspend-20260919/drm-state-after-s2a.txt')

class FlagDiff(unittest.TestCase):
    def test_real_s2a_state(self):
        self.assertTrue(rg.flag_only_diff(NATIVE.read_text(), AFTER.read_text()))
    def test_identical_is_not_rebase(self):
        n = NATIVE.read_text(); self.assertFalse(rg.flag_only_diff(n, n))
    def test_other_change_rejected(self):
        a = AFTER.read_text().replace('fb=', 'fb=9', 1); self.assertFalse(rg.flag_only_diff(NATIVE.read_text(), a))
    def test_partial_flags_rejected(self):
        a = AFTER.read_text().replace('connectors_changed=1', 'connectors_changed=0', 1)
        self.assertFalse(rg.flag_only_diff(NATIVE.read_text(), a))

class ExactLine(unittest.TestCase):
    L = '[48875.302039] [   T1380] synx: warn: synx_recover: 144: Subsystem restart for core_id: 1216'
    def rv(self):
        return bg.Review({'spmi': {'max_count': 0, 'max_uptime_s': 0, 'blocks': []}, 'waits': {}, 'panel_messages': []}, {})
    def test_flagged_without_review(self):
        self.assertEqual(self.rv().unexpected(self.L), [self.L])
    def test_exact_line_accepted(self):
        r = self.rv(); r.exact_ok = {self.L}; self.assertEqual(r.unexpected(self.L), [])
    def test_same_text_new_timestamp_still_flagged(self):
        r = self.rv(); r.exact_ok = {self.L}; l2 = self.L.replace('48875.302039', '49999.000001')
        self.assertEqual(r.unexpected(self.L + '\n' + l2), [l2])
if __name__ == '__main__': unittest.main()
