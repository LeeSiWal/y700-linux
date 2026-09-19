#!/usr/bin/env python3
"""One-shot, user-approved (2026-09-19, after the user confirmed the screen is normal): record the display state left by the
S-2a suspend device pass as a reviewed rebased state, plus the single synx line it printed as a reviewed exact line.
Refuses unless: S-2a result = STOP 'native DRM state not back within 10 s'; the current state differs from the native state
ONLY by the three CRTC last-commit flags 0 -> 1; owner alive and sole DRM master; underruns equal the registry base; the
synx line is present exactly once in dmesg and in the S-2a dmesg_after. Writes new files only ('x'); never edits the registry.
Usage: sudo python3 -B rebase-display.py"""
import hashlib, importlib.util, json, os, re, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import registry as rg

_spec = importlib.util.spec_from_file_location('readers', HERE/'runtime-readers.py')
c = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(c)
AGENT = Path('/home/siwal/y700-agent')
S2A = AGENT/'boot-susp-devices-b163f341c094b6d3'
SYNX = re.compile(r'^\[\s*[0-9.]+\] \[\s*T\d+\] synx: warn: synx_recover: 144: Subsystem restart for core_id: 1216$')
TAG = 's2a'

def main():
    c.need(os.geteuid() == 0, 'run with sudo')
    boot = c.read('/proc/sys/kernel/random/boot_id').strip()
    reg, reg_sha = rg.load(c, boot); rg.process_ok(c, reg['owner'])
    out_reg = AGENT/('registry-rebase-%s-%s.json' % (boot[:8], TAG)); out_state = AGENT/('display-rebase-%s-%s.txt' % (boot[:8], TAG))
    c.need(not out_reg.exists() and not out_state.exists(), 'rebase already recorded')
    r = json.loads(c.read(S2A/'result.json')); m = json.loads(c.read(S2A/'manifest.json'))
    c.need(m['boot_id'] == boot and r.get('error') == 'native DRM state not back within 10 s', 'S-2a result is not the reviewed STOP')
    native = c.read(reg['native_state_file']); c.need(hashlib.sha256(native.encode()).hexdigest() == reg['native_state_sha256'], 'native state file changed')
    now = c.read(rg.DEBUG/'state')
    c.need(rg.flag_only_diff(native, now), 'current state differs from native by more than the three CRTC flags')
    lines = c.read(rg.DEBUG/'clients').splitlines()
    c.need(len(lines) == 2 and lines[1].split()[1] == str(reg['owner']['pid']) and lines[1].split()[3] == 'y', 'DRM client/master changed')
    ur = rg.underruns(c, reg['encoder_status']); c.need(ur == {int(k): v for k, v in reg['underrun_base'].items()}, 'underruns changed: %r' % ur)
    cur = [l for l in c.dmesg().splitlines() if SYNX.match(l)]; old = [l for l in r['dmesg_after'].splitlines() if SYNX.match(l)]
    c.need(len(cur) == 1 and cur == old, 'synx line not exactly once / not from S-2a: %r %r' % (cur, old))
    with out_state.open('x') as f: f.write(now)
    os.chmod(out_state, 0o644)
    rec = {'boot_id': boot, 'created': time.time(), 'tag': TAG, 'source_bundle': S2A.name,
           'source_result_sha256': hashlib.sha256(c.read(S2A/'result.json', True)).hexdigest(), 'registry_sha256': reg_sha,
           'owner': {'pid': reg['owner']['pid'], 'starttime': reg['owner']['starttime']}, 'underrun': {str(k): v for k, v in ur.items()},
           'state_file': str(out_state), 'state_sha256': hashlib.sha256(now.encode()).hexdigest(),
           'native_diff': [(a, b) for a, b in zip(native.splitlines(), now.splitlines()) if a != b],
           'reviewed_extra_lines': cur,
           'user': {'screen_normal': True, 'approved_exceptions': True, 'message': '화면 정상이야, 두 예외 승인하고 진행해 줘'}}
    with out_reg.open('x') as f: f.write(json.dumps(rec, indent=2)); f.flush(); os.fsync(f.fileno())
    os.chmod(out_reg, 0o644)
    print('REBASE_RECORDED', out_reg.name, rec['state_sha256'][:16], json.dumps(rec['native_diff']), 'extra_line:', cur[0])

if __name__ == '__main__':
    try: main()
    except Exception as exc: sys.exit('STOP: ' + str(exc))
