#!/usr/bin/env python3
"""Build the KGSL stage-1 bundle from the live-verified rate1200 manifest. Output: a new ~/y700-agent/gpu-load-<hash16>/."""
from pathlib import Path
import hashlib, json, shutil, struct, sys

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
BASE = AGENT/'screen-rate1200-b452a79e83e608a1'
BASE_MANIFEST_SHA = 'b452a79e83e608a1124ff075ce463a4203c06fa1c937bd85b037681b508f8240'
ORDER = ['coresight.ko', 'msm_sysstats.ko', 'msm_performance.ko', 'governor_msm_adreno_tz.ko',
         'governor_gpubw_mon.ko', 'governor_msm_adreno_ro.ko', 'msm_kgsl.ko']
FIRMWARE = {'gen80200_sqe.fw': 'e2d4e74282f50e40788b83b9ba4a5f5894c7975ed356c03a0161372d78f12932',
            'gen80200_aqe.fw': 'ed7916ca84e663d63fa94b0d40cc8c222e362e6cff860c539a8cca30d3721b68',
            'gen80200_gmu.bin': '06f4484fa06f91638aa4ae2412051bcddffdad69a7e2f83849c72412e6daa44d',
            'gen80200_zap.mbn': '25e836c603d52107a2cbb2f1a7eb46997837e94c2fb70bb5cc44b5e700d8936a'}
MODULE_SHA = {'coresight.ko': '0c46d0a4fe3b03325ca4419b9d032cc04f87b4d7ade2d83de122c02c8219945d',
              'msm_sysstats.ko': '70c14e9a14335cb9927bb28284d169747d50a4ed52c0b0dd235a28906a39adbb',
              'msm_performance.ko': '8ba598bb1da788dba4ad385823f52e5bce04a009fed99907bcefa959378e97ac',
              'governor_msm_adreno_tz.ko': '74c333aaf377d94beef9acd86e995a4cf139206ddeaf4acecc11fac548897876',
              'governor_gpubw_mon.ko': '170ae71a1589f0717ce9c3841f23759a9bdca58e8375dfa10c0df9b6c6ad0605',
              'governor_msm_adreno_ro.ko': '337ab1ae63e5a285f6b9e282faf609a49fe40b5fde1b847770ff6be9cddc220f',
              'msm_kgsl.ko': '12cb8a0d983357cb080748e66efc536671898af60228d76654cdd407f8ee9ae3'}
CODE = ['prepare-gpu.py', 'gpuguard.py', 'health_guard.py', 'runtime-readers.py', 'y700lib.py', 'expected-state.txt']

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok, msg):
    if not ok: sys.exit('STOP: '+msg)
def note(path):
    d = Path(path).read_bytes()
    shoff = struct.unpack_from('<Q', d, 40)[0]; shes, shn, shstr = struct.unpack_from('<HHH', d, 58)
    sh = [struct.unpack_from('<IIQQQQIIQQ', d, shoff+i*shes) for i in range(shn)]
    names = d[sh[shstr][4]:sh[shstr][4]+sh[shstr][5]]
    for s in sh:
        if names[s[0]:names.index(b'\0', s[0])] == b'.note.gnu.build-id': return d[s[4]:s[4]+s[5]].hex()
    need(False, 'no build-id: '+str(path))

need(sha(BASE/'manifest.json') == BASE_MANIFEST_SHA, 'base manifest changed')
need(sha(B/'expected-state.txt') == sha(BASE/'expected-state.txt'), 'expected state differs from base')
for f, h in MODULE_SHA.items(): need(sha(B/f) == h, 'module hash differs: '+f)
for f, h in FIRMWARE.items(): need(sha(B/'firmware'/f) == h, 'firmware hash differs: '+f)
base = json.loads((BASE/'manifest.json').read_text())
m = {k: base[k] for k in ('boot_id', 'kernel', 'root', 'module_notes', 'slot', 'module_names', 'reviewed_waits',
                         'adci_log_tail', 'reviewed_fault_records', 'listener_templates', 'panel_messages')}
w = json.loads((AGENT/'screen-rate1200-b452a79e83e608a1/worker.json').read_text())
m['retained_workers'] = base['retained_workers']+[{'pid': w['pid'], 'starttime': w['starttime']}]
m['load_order'] = ORDER
m['load_notes'] = {f[:-3].replace('-', '_'): note(B/f) for f in ORDER}
m['files'] = {**{f: sha(B/f) for f in ORDER+CODE}, **{'firmware/'+f: h for f, h in FIRMWARE.items()}}
m['gpu_review'] = {
    'scope': 'Stage 1 only: insmod the 7-module closure in order (firmware pre-installed in /lib/firmware by a separate reviewed '
             'command; firmware_class.path is unreadable here, so it is not used), then observe probe/bind for 60s. /dev/kgsl-3d0 is '
             'never opened; no command submission, no clock/ICC/KMS writes, nothing unloaded on any outcome.',
    'firmware': FIRMWARE,
    'superseded_bundle': 'gpu-load-fd48db47d4fd099c (stopped at firmware_class.path read EPERM before attempt.json; nothing loaded)',
    'basis': 'All suppliers bound; closure unresolved symbols 0; CRC 550/811 cross-checked (0 mismatch), rest enforced by the kernel at load; '
             'firmware from stock vendor image (debugfs read-only). gcc/ICC sync_state not expected (other unbound consumers); gpucc sync_state expected.',
    'expected_before': {'kgsl': None, 'gmu': None, 'iommu': None, 'gpucc_state_synced': '0', 'dev_kgsl': False, 'devfreq': ['1d84000.ufshc']},
    'underrun_base': {'1': 13, '2': 13},
    'vendor_image_sha256': 'e385e71d6e8d152765980990ade4eaf66c467e1533eccf0891ba3390954672e2',
    'base_manifest_sha256': BASE_MANIFEST_SHA}
text = json.dumps(m, indent=2).encode()
out = AGENT/('gpu-load-'+hashlib.sha256(text).hexdigest()[:16])
need(not out.exists(), 'bundle directory already exists')
out.mkdir(); (out/'firmware').mkdir()
for f in ORDER+CODE: shutil.copy2(B/f, out/f)
for f in FIRMWARE: shutil.copy2(B/'firmware'/f, out/'firmware'/f)
(out/'manifest.json').write_bytes(text)
for p in list(out.iterdir())+list((out/'firmware').iterdir()):
    if p.is_file(): p.chmod(0o644)
print(out, sha(out/'manifest.json'))
