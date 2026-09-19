#!/usr/bin/env python3
"""Create a boot-independent Vulkan trial bundle (compute | anim120 | anim600) for the CURRENT boot, based on its registries.
Earlier vk bundles of this boot whose workers are held are added to retained_tests (must be alive).
Usage: python3 -B make-vk-bundle.py compute|anim120|anim600 [--out DIR]"""
from pathlib import Path
import hashlib, json, os, re, shutil, sys
HERE = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
ICD_ROOT = Path('/home/siwal/y700-gpu/turnip-kgsl-26.2.3')
ICD = ['lib/libvulkan_freedreno.so', 'share/vulkan/icd.d/freedreno_icd.aarch64.json', 'share/drirc.d/00-mesa-defaults.conf',
       'share/drirc.d/00-turnip-defaults.conf']
FIRMWARE = {'gen80200_sqe.fw': 'e2d4e74282f50e40788b83b9ba4a5f5894c7975ed356c03a0161372d78f12932',
            'gen80200_aqe.fw': 'ed7916ca84e663d63fa94b0d40cc8c222e362e6cff860c539a8cca30d3721b68',
            'gen80200_gmu.bin': '06f4484fa06f91638aa4ae2412051bcddffdad69a7e2f83849c72412e6daa44d',
            'gen80200_zap.mbn': '25e836c603d52107a2cbb2f1a7eb46997837e94c2fb70bb5cc44b5e700d8936a'}
COMMON = ['run-vk.py', 'bootmon.py', 'bootguard.py', 'runtime-readers.py', 'review-templates.json', 'registry.py', 'vkabi.py']
KIND = {'compute': ['vkcompute.py', 'computelog.py', 'fill.spv', 'fill.spvasm'],
        'anim': ['vkanim.py', 'animlog.py', 'animpattern.py', 'anim.spv', 'anim.spvasm', 'drmabi.py', 'borrow_owner.py']}
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok, msg):
    if not ok: sys.exit('STOP: ' + msg)
def rd(p): return Path(p).read_text()

args = sys.argv[1:]
need(args and args[0] in ('compute', 'anim120', 'anim600') and (len(args) == 1 or (len(args) == 3 and args[1] == '--out')), 'usage')
kind = 'compute' if args[0] == 'compute' else 'anim'; frames = {'anim120': 120, 'anim600': 600}.get(args[0])
boot = rd('/proc/sys/kernel/random/boot_id').strip()
for r in ('registry-%s.json', 'registry-gpu-%s.json'): need((AGENT/(r % boot[:8])).is_file(), 'missing registry ' + r % boot[:8])
if kind == 'anim' and frames == 600:
    ok = [d for d in AGENT.glob('vk-anim120-*') if json.loads(rd(d/'manifest.json'))['boot_id'] == boot and (d/'result.json').is_file()
          and json.loads(rd(d/'result.json')).get('status') == 'VK_ANIM_DONE' and (d/'anim-visual.json').is_file()]
    need(ok, 'anim600 requires a completed, visually confirmed anim120 bundle of this boot')
retained = []
for d in sorted(AGENT.glob('vk-*')):
    try: mm = json.loads(rd(d/'manifest.json'))
    except Exception: continue
    if mm.get('boot_id') != boot or not (d/'worker.json').is_file(): continue
    w = json.loads(rd(d/'worker.json')); p = Path('/proc')/str(w['pid'])
    need(p.exists() and rd(p/'stat')[rd(p/'stat').rfind(')') + 2:].split()[19] == w['starttime'], 'held test worker gone: %s %s' % (d.name, w['pid']))
    retained.append(w)
live = sorted(x.split()[0] for x in rd('/proc/modules').splitlines())
root = [x.split() for x in rd('/proc/self/mountinfo').splitlines() if x.split()[4] == '/']
need(re.findall(r'^androidboot\.slot_suffix\s*=\s*"([^"]+)"\s*$', rd('/proc/bootconfig'), re.M) == ['_a'], 'slot A required')
files = COMMON + KIND[kind]
m = {'kind': kind, 'frames': frames, 'boot_id': boot, 'kernel': os.uname().release, 'root': root[0][2], 'slot': '_a',
     'module_names': live, 'module_notes': {n: (Path('/sys/module')/n/'notes/.note.gnu.build-id').read_bytes().hex()[-40:] for n in live},
     'ramoops_required': True, 'retained_tests': retained, 'icd_files': {r: sha(ICD_ROOT/r) for r in ICD}, 'firmware': FIRMWARE,
     'files': {f: sha(HERE/f) for f in files}, 'templates_sha256': sha(HERE/'review-templates.json')}
sys.path.insert(0, str(HERE)); import reviewed_lines
m['reviewed_lines'] = reviewed_lines.collect(AGENT, boot)
need(len(m['reviewed_lines']) == 2, 'expected the two panel lines reviewed by this boot\'s display stage')
text = json.dumps(m, indent=2).encode()
out = Path(args[2]) if len(args) == 3 else AGENT/('vk-%s-%s' % (args[0], hashlib.sha256(text).hexdigest()[:16]))
need(not out.exists(), 'bundle exists')
out.mkdir(parents=True)
for f in files: shutil.copy2(HERE/f, out/f)
(out/'manifest.json').write_bytes(text)
for p in out.iterdir(): p.chmod(0o644)
print(out, sha(out/'manifest.json'))
