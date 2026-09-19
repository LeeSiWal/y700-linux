#!/usr/bin/env python3
"""Build the KGSL stage-2 bundle from the completed stage-1 bundle. Output: ~/y700-agent/gpu-open-<hash16>/."""
from pathlib import Path
import hashlib, json, shutil, sys

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
S1 = AGENT/'gpu-load-4bebd1cff55e9960'
S1_MANIFEST_SHA = '4bebd1cff55e996002f8389262e9a2b6d487b04ada25b051f9bd4d232d881e16'
FILES = ['open-gpu.py', 'kgsl-open.py', 'kgslabi.py', 'openlog.py', 'gpuguard.py', 'health_guard.py',
         'runtime-readers.py', 'y700lib.py', 'expected-state.txt']
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok, msg):
    if not ok: sys.exit('STOP: '+msg)
need(sha(S1/'manifest.json') == S1_MANIFEST_SHA, 'stage-1 manifest changed')
r1 = json.loads((S1/'result.json').read_text())
need(r1.get('status') == 'KGSL_CLOSURE_LOADED_OBSERVED_NOT_OPENED' and r1.get('firmware_unchanged'), 'stage 1 not completed')
need(sha(B/'expected-state.txt') == sha(S1/'expected-state.txt'), 'expected state differs')
s1 = json.loads((S1/'manifest.json').read_text())
m = {k: s1[k] for k in ('boot_id', 'kernel', 'root', 'slot', 'reviewed_waits', 'adci_log_tail', 'reviewed_fault_records',
                       'listener_templates', 'panel_messages', 'retained_workers')}
m['module_notes'] = {**s1['module_notes'], **s1['load_notes']}
m['module_names'] = sorted(set(s1['module_names']) | set(s1['load_notes']))
m['files'] = {f: sha(B/f) for f in FILES}
m['gpu_review'] = {
    'scope': 'Stage 2: mknod /dev/kgsl-3d0 (c 495:0, 0600 root); held worker opens it once after a gate (first open boots GMU, '
             'loads SQE/AQE/GMU fw and zap via TZ) and queries only KGSL_PROP_DEVICE_INFO (chip_id must be 0x44050a01). '
             'No memory/context/submit ioctls. fd never closed; modules never unloaded.',
    'basis': 'Stage 1 bundle result: 7 modules live, kgsl/gmu/iommu bound, gpucc sync_state done, display/net unchanged 60s, 0 faults.',
    'stage1_manifest_sha256': S1_MANIFEST_SHA, 'stage1_result_sha256': sha(S1/'result.json'),
    'firmware': s1['gpu_review']['firmware'],
    'expected_before': {'kgsl': 'kgsl-3d', 'gmu': 'adreno-gen8-gmu', 'iommu': 'kgsl-iommu', 'gpucc_state_synced': '1',
                        'dev_kgsl': False, 'devfreq': ['1d84000.ufshc', '3d00000.qcom,kgsl-3d0']},
    'underrun_base': {'1': 13, '2': 13}}
text = json.dumps(m, indent=2).encode()
out = AGENT/('gpu-open-'+hashlib.sha256(text).hexdigest()[:16])
need(not out.exists(), 'bundle exists')
out.mkdir()
for f in FILES: shutil.copy2(B/f, out/f); (out/f).chmod(0o644)
(out/'manifest.json').write_bytes(text); (out/'manifest.json').chmod(0o644)
print(out, sha(out/'manifest.json'))
