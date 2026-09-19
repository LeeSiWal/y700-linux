#!/usr/bin/env python3
"""Build boot-independent review templates from the reviewed evidence of boot b4e16b26 (and cross-check with 36694bab).
Output: review-templates.json (+ provenance). Read-only on ~/y700-agent."""
import hashlib, json, re, sys
from pathlib import Path
AGENT = Path('/home/siwal/y700-agent')
CUR = AGENT/'display-recovered-resume-44597f2381322784/result.json'   # b4e16b26: SPMI x3, ADCI x9, HFI x1, panel x2
PREV = AGENT/'display-newboot-2e58b35211e46679/result.json'           # 36694bab: SPMI x3, ADCI x10, panel x2
MANIFEST = AGENT/'vkanim120-19cb62f55a45c899/manifest.json'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootguard as bg

def need(ok, msg):
    if not ok: sys.exit('STOP: ' + msg)

cur = json.loads(CUR.read_text())['dmesg_after']; prev = json.loads(PREV.read_text())['dmesg_after']
m = json.loads(MANIFEST.read_text())
spmi_cur, spmi_prev = bg.spmi_blocks(cur), bg.spmi_blocks(prev)
need(len(spmi_cur) == 3 and len(spmi_prev) == 3, 'expected three SPMI warning blocks per boot')
norm_cur = [bg.normalize_block(b) for b in spmi_cur]
need(norm_cur == [bg.normalize_block(b) for b in spmi_prev], 'SPMI blocks differ between boots after normalization')
hung = {}
for comm15, kind in (('adci_thread', 'adci'), ('hfi_core_dbg_cl', 'hfi')):
    blocks = bg.hung_blocks(cur, comm15)
    need(blocks, 'no hung-task report for ' + comm15)
    tails = {tuple(bg.normalize_hung_tail(t)) for _, _, t in blocks}
    need(len(tails) == 1, 'hung-task tails differ for ' + comm15)
    hung[kind] = list(tails.pop())
prev_adci = {tuple(bg.normalize_hung_tail(t)) for _, _, t in bg.hung_blocks(prev, 'adci_thread')}
need(prev_adci == {tuple(hung['adci'])}, 'ADCI hung-task tail differs between boots')
waits = {v['comm']: v['frames'] for v in m['reviewed_waits'].values()}
# Fence listener never printed a report (hung_task_warnings budget exhausted). Derive its tail from the HFI tail
# structure + its own reviewed /proc stack; marked derived so a real occurrence is recorded for review.
hfi = hung['hfi']
fence_frames = [f.replace('[msm_hw_fence]', '[msm_hw_fence {buildid:msm_hw_fence}]') for f in waits['msm_hw_fence_soccp_listener']]
fence_tail = [hfi[0], hfi[1], hfi[2].replace('hfi_core_dbg_cl', 'msm_hw_fence_so'), hfi[3], hfi[4], hfi[5], hfi[6]] + [' ' + f for f in fence_frames]
out = {
    'provenance': {'cur': str(CUR), 'cur_sha256': hashlib.sha256(CUR.read_bytes()).hexdigest(),
                   'prev': str(PREV), 'prev_sha256': hashlib.sha256(PREV.read_bytes()).hexdigest(),
                   'manifest': str(MANIFEST), 'manifest_sha256': hashlib.sha256(MANIFEST.read_bytes()).hexdigest()},
    'spmi': {'blocks': norm_cur, 'max_count': 3, 'max_uptime_s': 5.0},
    'waits': {
        'adci': {'comm': 'adci_thread', 'comm15': 'adci_thread', 'frames': waits['adci_thread'], 'hung_tail': hung['adci'], 'bind': 'baseline'},
        'hfi': {'comm': 'hfi_core_dbg_client_listener', 'comm15': 'hfi_core_dbg_cl', 'frames': waits['hfi_core_dbg_client_listener'],
                'hung_tail': hung['hfi'], 'bind': 'after:msm_hfi_core'},
        'fence': {'comm': 'msm_hw_fence_soccp_listener', 'comm15': 'msm_hw_fence_so', 'frames': waits['msm_hw_fence_soccp_listener'],
                  'hung_tail': fence_tail, 'bind': 'after:msm_hw_fence', 'derived_tail': True}},
    'panel_messages': sorted({bg.payload(l) for l in cur.splitlines() if '[msm-dsi-error]' in l}),
    'suppressed_line': 'Future hung task reports are suppressed, see sysctl kernel.hung_task_warnings',
}
need(len(out['panel_messages']) == 2, 'expected exactly two reviewed panel messages')
Path(__file__).with_name('review-templates.json').write_text(json.dumps(out, indent=1))
print('templates written: spmi=%d adci/hfi tails=%d/%d panel=%d' % (len(norm_cur), len(hung['adci']), len(hung['hfi']), 2))
