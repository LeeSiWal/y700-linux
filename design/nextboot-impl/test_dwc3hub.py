"""dwc3_msm 'class hub' duplicate template (approved on boot 8e18ad1f): synthetic, boot-independent."""
import json, unittest
import bootguard as bg

T = json.load(open(__import__('pathlib').Path(__file__).with_name('review-templates.json')))
BID = '6ec176985cf8ab043f91b2e3484a424ab99b4b35'
LIVE = {'dwc3_msm': BID}

def dump(ts, tid=78, cpu=6):
    out = []
    for x in T['dwc3_hub']['block']:
        x = x.replace('CPU: # UID: # PID: # Comm: # Tainted: # ', 'CPU: %d UID: 0 PID: %d Comm: kworker/u34:0 Tainted: G        W  O       ' % (cpu, tid))
        x = x.replace('Tainted: [#]', 'Tainted: [W]=WARN, [O]=OOT_MODULE').replace('{buildid:dwc3_msm}', BID)
        out.append('[%12.6f] [%6s] %s' % (ts, 'T%d' % tid, x))
    return out

class Hub(unittest.TestCase):
    def rv(self, live=LIVE): return bg.Review(T, live)
    def test_accepted(self):
        self.assertEqual(self.rv().unexpected('\n'.join(dump(13.01) + dump(13.02, cpu=0))), [])
    def test_late_rejected(self):
        self.assertTrue(self.rv().unexpected('\n'.join(dump(25.0))))
    def test_buildid_rejected(self):
        self.assertTrue(self.rv({'dwc3_msm': '0' * 40}).unexpected('\n'.join(dump(13.01))))
    def test_changed_stack_rejected(self):
        d = dump(13.01); k = next(i for i, x in enumerate(d) if 'class_register+' in x); d[k] = d[k].replace('class_register', 'class_create')
        self.assertTrue(self.rv().unexpected('\n'.join(d)))
    def test_count_limit(self):
        text = '\n'.join(sum((dump(13.0 + i / 1000) for i in range(T['dwc3_hub']['max_count'] + 1)), []))
        self.assertTrue(self.rv().unexpected(text))
if __name__ == '__main__': unittest.main()

class Rotation(unittest.TestCase):
    """Approved extension of the rotated-head rule (boot 8e18ad1f) to the SPMI and dwc3_hub templates."""
    SPMI = [l for l in json.load(open('/home/siwal/y700-agent/boot-pstore-b11e2aca2539c6b1/result.json'))['dmesg_before'].splitlines()]
    def spmi_text(self, cut):
        a = [i for i, l in enumerate(self.SPMI) if 'spmi-pmic-arb.c:352' in l]
        return '\n'.join(self.SPMI[a[0] + cut:a[2] + 60])
    def test_spmi_full(self):
        self.assertEqual([l for l in bg.Review(T, {}).unexpected(self.spmi_text(0)) if 'spmi' in l or 'Call trace' in l], [])
    def test_spmi_first_block_head_cut(self):
        r = bg.Review(T, {}); bad = r.unexpected(self.spmi_text(3))
        self.assertEqual([l for l in bad if 'spmi' in l or 'Call trace' in l], [])
        self.assertTrue(any(e['event'] == 'rotated_template_head' for e in r.audit))
    def test_spmi_two_blocks_dropped(self):
        a = [i for i, l in enumerate(self.SPMI) if 'spmi-pmic-arb.c:352' in l]
        text = '\n'.join(self.SPMI[a[2]:a[2] + 60])
        self.assertEqual([l for l in bg.Review(T, {}).unexpected(text) if 'spmi' in l or 'Call trace' in l], [])
    def test_dwc3_head_cut(self):
        d = dump(13.01)
        for cut in (1, 5, 6, 20):
            self.assertEqual(bg.Review(T, LIVE).unexpected('\n'.join(d[cut:] + dump(13.02))), [], cut)
    def test_dwc3_fragment_not_at_start_rejected(self):
        d = dump(13.01); other = '[   13.000000] [   T99] something else'
        self.assertTrue(bg.Review(T, LIVE).unexpected('\n'.join([other] + d[3:])))
    def test_dwc3_fragment_late_rejected(self):
        self.assertTrue(bg.Review(T, LIVE).unexpected('\n'.join(dump(40.0)[3:])))
    def test_fragment_changed_rejected(self):
        d = dump(13.01)[3:]; k = next(i for i, x in enumerate(d) if 'class_register+' in x); d[k] = d[k].replace('class_register', 'class_create')
        self.assertTrue(bg.Review(T, LIVE).unexpected('\n'.join(d)))
