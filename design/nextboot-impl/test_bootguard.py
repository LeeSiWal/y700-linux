"""Host tests for bootguard against the real reviewed dmesg of two boots (b4e16b26, 36694bab). Read-only."""
import json, re, unittest
from pathlib import Path
import bootguard as bg

T = json.loads(Path(__file__).with_name('review-templates.json').read_text())
AG = Path('/home/siwal/y700-agent')
CUR = json.loads((AG/'display-recovered-resume-44597f2381322784/result.json').read_text())['dmesg_after']
PREV = json.loads((AG/'display-newboot-2e58b35211e46679/result.json').read_text())['dmesg_after']
def live():
    out = {}
    for mod in ('qcom_scm', 'si_core_module', 'msm_hfi_core', 'msm_hw_fence', 'spmi_pmic_arb'):
        out[mod] = (Path('/sys/module')/mod/'notes/.note.gnu.build-id').read_bytes().hex()[-40:]
    return out
LIVE = live()
def panel_tid(text):
    return next(bg.thread(l) for l in text.splitlines() if '[msm-dsi-error]' in l)
def review(text, bound, module='msm_drm', tid='auto'):
    r = bg.Review(T, LIVE); r.bound = bound; r.load_module = module
    r.insmod_tid = panel_tid(text) if tid == 'auto' else tid
    return r.unexpected(text)

class T1(unittest.TestCase):
    def test_current_boot_clean(self): self.assertEqual(review(CUR, {119: 'adci', 1291: 'hfi'}), [])
    def test_previous_boot_clean(self): self.assertEqual(review(PREV, {118: 'adci'}), [])
    def test_unbound_pid_rejected(self):
        u = review(CUR, {119: 'adci'})
        self.assertTrue(any('hfi_core_dbg_cl:1291' in l for l in u))
    def test_wrong_kind_rejected(self):
        self.assertTrue(review(CUR, {119: 'hfi', 1291: 'hfi'}))
    def test_altered_frame_rejected(self):
        bad = CUR.replace('adci_fn+0x48/0xe8', 'adci_fn+0x4c/0xe8', 1)
        self.assertTrue(review(bad, {119: 'adci', 1291: 'hfi'}))
    def test_buildid_mismatch_rejected(self):
        bad = CUR.replace(LIVE['msm_hfi_core'], '0' * 40, 1)
        self.assertTrue(review(bad, {119: 'adci', 1291: 'hfi'}))
    def test_panel_wrong_thread_rejected(self):
        self.assertTrue(review(CUR, {119: 'adci', 1291: 'hfi'}, tid=1))
    def test_panel_wrong_phase_rejected(self):
        self.assertTrue(review(CUR, {119: 'adci', 1291: 'hfi'}, module='msm_hfi_core'))
    def test_panel_persists_after_load(self):
        r = bg.Review(T, LIVE); r.bound = {119: 'adci', 1291: 'hfi'}; r.load_module = 'msm_drm'; r.insmod_tid = panel_tid(CUR)
        self.assertEqual(r.unexpected(CUR), []); r.load_module = None; r.insmod_tid = None
        self.assertEqual(r.unexpected(CUR), [])
    def test_late_spmi_rejected(self):
        lines = CUR.splitlines(); a, b = bg.spmi_block_spans(lines)[0]
        late = [re.sub(r'^\[\s*[0-9.]+\]', '[    9.000000]', l) for l in lines[a:b + 1]]
        self.assertTrue(review('\n'.join(lines[:a] + late + lines[b + 1:]), {119: 'adci', 1291: 'hfi'}))
    def test_fourth_spmi_rejected(self):
        lines = CUR.splitlines(); a, b = bg.spmi_block_spans(lines)[2]
        self.assertTrue(review('\n'.join(lines[:b + 1] + lines[a:b + 1] + lines[b + 1:]), {119: 'adci', 1291: 'hfi'}))
    def test_new_warning_rejected(self):
        extra = CUR + '\n[ 5000.000000] [  T999] WARNING: CPU: 3 PID: 999 at drivers/foo.c:1 bar+0x0/0x4'
        u = review(extra, {119: 'adci', 1291: 'hfi'}); self.assertEqual(len(u), 1)
    def test_spmi_changed_frame_rejected(self):
        bad = CUR.replace('pmic_arb_wait_for_done+0x144/0x17c [spmi_pmic_arb]\n', 'pmic_arb_wait_for_done+0x148/0x17c [spmi_pmic_arb]\n', 2)
        self.assertTrue(review(bad, {119: 'adci', 1291: 'hfi'}))

def task(pid, comm, frames, start='100', state='D'):
    return {'comm': comm, 'state': state, 'starttime': start, 'identity_stable': True,
            'stack': {'text': '\n'.join('[<0>] ' + f for f in frames)}}

class T2(unittest.TestCase):
    def test_bind_and_check(self):
        w = bg.Waits(T); tasks = {'77': task(77, 'adci_thread', T['waits']['adci']['frames'])}
        self.assertEqual(w.try_bind(tasks, ['adci']), [('adci', '77')])
        self.assertEqual(w.check(tasks, now=0), {})
    def test_no_bind_on_stack_change(self):
        w = bg.Waits(T); fr = list(T['waits']['adci']['frames']); fr[0] = fr[0].replace('0x218', '0x21c')
        self.assertEqual(w.try_bind({'77': task(77, 'adci_thread', fr)}, ['adci']), [])
    def test_ambiguous_no_bind(self):
        w = bg.Waits(T); f = T['waits']['hfi']['frames']
        self.assertEqual(w.try_bind({'5': task(5, 'hfi_core_dbg_client_listener', f), '6': task(6, 'hfi_core_dbg_client_listener', f)}, ['hfi']), [])
    def test_listener_not_before_load(self):
        w = bg.Waits(T); f = T['waits']['hfi']['frames']
        self.assertEqual(w.try_bind({'5': task(5, 'hfi_core_dbg_client_listener', f, start='10')}, ['hfi'], not_before=50), [])
    def test_bound_stack_change_raises(self):
        w = bg.Waits(T); f = T['waits']['fence']['frames']; tasks = {'9': task(9, 'msm_hw_fence_soccp_listener', f)}
        w.try_bind(tasks, ['fence']); tasks['9'] = task(9, 'msm_hw_fence_soccp_listener', f[1:])
        with self.assertRaises(RuntimeError): w.check(tasks, now=0)
    def test_unknown_ten_seconds(self):
        w = bg.Waits(T); tasks = {'3': task(3, 'jbd2/mmcblk1p3-8', ['x'])}
        w.check(tasks, now=0); w.check(tasks, now=9.9)
        with self.assertRaises(RuntimeError): w.check(tasks, now=10.0)
    def test_adci_gone_raises(self):
        w = bg.Waits(T); tasks = {'77': task(77, 'adci_thread', T['waits']['adci']['frames'])}
        w.try_bind(tasks, ['adci'])
        with self.assertRaises(RuntimeError): w.check({}, now=0)

class T3(unittest.TestCase):
    def test_preloaded_reviewed_lines(self):
        lines = [l for l in CUR.splitlines() if '[msm-dsi-error]' in l]
        r = bg.Review(T, LIVE); r.bound = {119: 'adci', 1291: 'hfi'}; r.panel_ok = set(lines)
        self.assertEqual(r.unexpected(CUR), [])
        r2 = bg.Review(T, LIVE); r2.bound = {119: 'adci', 1291: 'hfi'}; r2.panel_ok = {lines[0]}
        self.assertEqual(len(r2.unexpected(CUR)), 1)          # only exact preloaded lines are accepted
        other = CUR.replace(lines[0], lines[0].replace('272.9', '999.9') if '272.9' in lines[0] else lines[0][:1] + '9' + lines[0][2:])
        r3 = bg.Review(T, LIVE); r3.bound = {119: 'adci', 1291: 'hfi'}; r3.panel_ok = set(lines)
        self.assertTrue(r3.unexpected(other))                 # same text at another time/thread is NOT accepted

WB = AG/'boot-wifi-b-74cda8c6b87119a8'
ROT = json.loads((WB/'result.json').read_text())['dmesg_after']        # buffer head = ADCI report without its header (3a1d6dac)
ROT_PANEL = set(json.loads((WB/'manifest.json').read_text())['reviewed_lines'])
ROT_BOUND = {118: 'adci', 1076: 'hfi', 1183: 'fence'}
def rot_review(text, bound=ROT_BOUND):
    r = bg.Review(T, LIVE); r.bound = dict(bound); r.panel_ok = set(ROT_PANEL); return r, r.unexpected(text)

class T4(unittest.TestCase):
    def test_rotated_head_fragment_accepted(self):
        r, bad = rot_review(ROT)
        self.assertEqual(bad, []); self.assertEqual([a['kind'] for a in r.audit if a['event'] == 'rotated_head_fragment'], ['adci'])
    def test_shorter_fragment_accepted(self):             # later rotation: only the last frames survive
        lines = ROT.splitlines(); k = next(i for i, l in enumerate(lines) if 'wait_for_completion' in l)
        self.assertEqual(rot_review('\n'.join(lines[k:]))[1], [])
    def test_unbound_or_wrong_pid_rejected(self):
        self.assertTrue(rot_review(ROT, {1076: 'hfi', 1183: 'fence'})[1])
        self.assertTrue(rot_review(ROT, {119: 'adci', 1076: 'hfi', 1183: 'fence'})[1])
    def test_changed_frame_rejected(self):
        self.assertTrue(rot_review(ROT.replace('wait_for_completion+0x18/0x24', 'wait_for_completion+0x1c/0x24', 1))[1])
    def test_wrong_buildid_rejected(self):
        bid = LIVE['qcom_scm']; self.assertTrue(rot_review(ROT.replace(bid, '0' * 40, 1))[1])
    def test_fragment_not_at_head_rejected(self):
        lines = ROT.splitlines(); self.assertTrue(rot_review('\n'.join(lines[30:31] + lines))[1])
    def test_mixed_thread_rejected(self):
        lines = ROT.splitlines(); lines[2] = lines[2].replace('[     T81]', '[     T82]')
        self.assertTrue(rot_review('\n'.join(lines))[1])

if __name__ == '__main__': unittest.main()
