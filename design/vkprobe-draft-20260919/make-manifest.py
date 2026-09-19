#!/usr/bin/env python3
"""Build the Vulkan probe bundle from the stage-2 (gpu-open) bundle. Output: ~/y700-agent/vkprobe-<hash16>/."""
from pathlib import Path
import hashlib, json, shutil, sys
B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
S2 = AGENT/'gpu-open-c1810de27aec2d85'
S2_MANIFEST_SHA = 'c1810de27aec2d85c24267a232b98755a6d3b7ed0676d975e843564ba1a247fd'
ICD_ROOT = Path('/home/siwal/y700-gpu/turnip-kgsl-26.0.8')
ICD = {'lib/libvulkan_freedreno.so': 'bc22e36b3840f15bd6cf9b74507b9b8694d4229f00b19081b052e06b5276f4a6',
       'share/vulkan/icd.d/freedreno_icd.aarch64.json': '73b071ae35ca59510b53fd351b9e80701ec17cc45d080a97020c5439a99ee68c',
       'share/drirc.d/00-mesa-defaults.conf': 'bdafaa303ab0a99116223d21b73034852fdfa1f55120d7e8567d0f7aaa5758f1'}
FILES = ['run-vkprobe.py', 'vkprobe.py', 'vkabi.py', 'probelog.py', 'gpuguard.py', 'health_guard.py', 'runtime-readers.py', 'y700lib.py', 'expected-state.txt']
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok, msg):
    if not ok: sys.exit('STOP: '+msg)
need(sha(S2/'manifest.json') == S2_MANIFEST_SHA, 'stage-2 manifest changed')
pc = json.loads((S2/'postcheck-result.json').read_text())
need(pc['drm_state_equal'] and pc['retained_workers_ok'] and pc['kgsl_worker_ok'] and '/dev/kgsl-3d0' in pc['kgsl_worker_fds'], 'stage-2 postcheck not clean')
for rel, h in ICD.items(): need(sha(ICD_ROOT/rel) == h, 'ICD file differs: '+rel)
s2 = json.loads((S2/'manifest.json').read_text()); w = json.loads((S2/'worker.json').read_text())
m = {k: s2[k] for k in ('boot_id', 'kernel', 'root', 'slot', 'reviewed_waits', 'adci_log_tail', 'reviewed_fault_records',
                       'listener_templates', 'panel_messages', 'module_notes', 'module_names')}
m['retained_workers'] = s2['retained_workers'] + [{'pid': w['pid'], 'starttime': w['starttime']}]
m['files'] = {f: sha(B/f) for f in FILES}
m['vk_review'] = {
    'scope': 'Loader-less ctypes probe of turnip 26.0.8 (KGSL backend): negotiate ICD v5, vkCreateInstance(API 1.3), '
             'vkEnumeratePhysicalDevices (opens a new /dev/kgsl-3d0 fd; GETPROPERTY x~8; GPUMEM_ALLOC_ID/FREE_ID 4K x2; '
             'GPUOBJ_ALLOC VBO 8K + BIND_RANGES + frees), vkGetPhysicalDeviceProperties. No VkDevice/context/submit. '
             'Instance not destroyed; worker holds. TU_DEBUG=startup, shader cache disabled.',
    'basis': 'Stage 2 first open OK (chip 0x44050a31), root postcheck clean; turnip source reviewed (tu_knl_kgsl.cc load path); '
             'kgsl enumerate success skips DRM enumeration; DRM client list checked every tick.',
    'stage2_manifest_sha256': S2_MANIFEST_SHA, 'icd_root': str(ICD_ROOT), 'icd_files': ICD,
    'firmware': s2['gpu_review']['firmware'],
    'expected_before': {'kgsl': 'kgsl-3d', 'gmu': 'adreno-gen8-gmu', 'iommu': 'kgsl-iommu', 'gpucc_state_synced': '1',
                        'dev_kgsl': True, 'devfreq': ['1d84000.ufshc', '3d00000.qcom,kgsl-3d0']},
    'underrun_base': {'1': 13, '2': 13}}
text = json.dumps(m, indent=2).encode()
out = AGENT/('vkprobe-'+hashlib.sha256(text).hexdigest()[:16])
need(not out.exists(), 'bundle exists')
out.mkdir()
for f in FILES: shutil.copy2(B/f, out/f); (out/f).chmod(0o644)
(out/'manifest.json').write_bytes(text); (out/'manifest.json').chmod(0o644)
print(out, sha(out/'manifest.json'))
