#!/usr/bin/env python3
"""Build a rate bundle manifest (120 first; 1200 only after a confirmed 120 run). Output goes to a new directory."""
from pathlib import Path
import argparse, hashlib, json, shutil, sys

B=Path(__file__).resolve().parent
AGENT=Path('/home/siwal/y700-agent')
FLIP=AGENT/'screen-flip-64f7797359c13f1a'
FLIP_MANIFEST_SHA='64f7797359c13f1a2480c81680e6076aafc8445f104b1dfb9c71b1c45e556367'
FLIP_RESULT_SHA='964b4c37529357b6de4f025beb56d49106cfc529ba7f40b6dfb19a2edef9c0ba'
FILES=['rate-held','rate-held.c','kms_lifecycle.h','run-rate.py','ratelog.py','health_guard.py','borrow.py',
       'runtime-readers.py','y700lib.py','expected-state.txt']

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
    if not ok:sys.exit('STOP: '+msg)

a=argparse.ArgumentParser();a.add_argument('--steps',type=int,choices=(120,1200),required=True);a.add_argument('--after')
args=a.parse_args()
need(sha(FLIP/'manifest.json')==FLIP_MANIFEST_SHA and sha(FLIP/'result.json')==FLIP_RESULT_SHA,'flip bundle evidence changed')
need((FLIP/'flip-visual.json').is_file(),'flip test lacks user visual confirmation')
for f in FILES:need((B/f).is_file(),'missing bundle file: '+f)
need((B/'rate-held').read_bytes()[:20]==b'\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00','rate-held is not a static aarch64 ELF executable')
need(sha(B/'expected-state.txt')==sha(FLIP/'expected-state.txt'),'expected state differs from flip bundle')
flip=json.loads((FLIP/'manifest.json').read_text())
m={k:v for k,v in flip.items() if k not in ('files','flip_review')}
fw=json.loads((FLIP/'worker.json').read_text())
m['retained_workers']=flip['retained_workers']+[{'pid':fw['pid'],'starttime':fw['starttime']}]
prior=None
if args.steps==1200:
    need(args.after is not None,'1200 requires --after <completed 120 bundle>')
    prev=Path(args.after).resolve();need(prev.parent==AGENT,'prior bundle outside y700-agent')
    pm=json.loads((prev/'manifest.json').read_text());pr=json.loads((prev/'result.json').read_text())
    need(pm['rate_review']['steps']==120 and pr.get('status')=='RATE_DONE_FB335_HELD_AWAITING_VISUAL_CONFIRMATION','prior 120 run not completed')
    need((prev/'rate-visual.json').is_file(),'prior 120 run lacks user visual confirmation')
    need(pm['files']['rate-held']==sha(B/'rate-held'),'1200 must use the same reviewed binary as 120')
    pw=json.loads((prev/'worker.json').read_text())
    m['retained_workers'].append({'pid':pw['pid'],'starttime':pw['starttime']})
    prior={'bundle':prev.name,'manifest_sha256':sha(prev/'manifest.json'),'result_sha256':sha(prev/'result.json'),'summary':pr['summary']}
else:
    need(args.after is None,'--after only for 1200')
m['files']={f:sha(B/f) for f in FILES}
m['rate_review']={
 'scope':'No allocation, no pixel writes. Back-to-back blocking atomic commits alternate FB_ID of plane95+plane128 together '
         'between retained FB341/FB342 (flip test, CPU-verified, user-confirmed), then return to FB335. Event consumed per commit; '
         'CLOCK_MONOTONIC timing. No modeset/clock/ICC/unload/close/restore.',
 'basis':'Finite flip test 9/9 events, exact state return, underrun 13->13, user confirmed. Single drm_file (client 2604) shared by all workers.',
 'flip_manifest_sha256':FLIP_MANIFEST_SHA,'flip_result_sha256':FLIP_RESULT_SHA,'prior_120':prior,
 'underrun_base':{'1':13,'2':13},'steps':args.steps}
text=json.dumps(m,indent=2).encode()
out=AGENT/('screen-rate%d-%s'%(args.steps,hashlib.sha256(text).hexdigest()[:16]))
need(not out.exists(),'bundle directory already exists')
out.mkdir()
for f in FILES:shutil.copy2(B/f,out/f)
(out/'manifest.json').write_bytes(text)
for f in out.iterdir():f.chmod(0o755 if f.name=='rate-held' else 0o644)
print(out, sha(out/'manifest.json'))
