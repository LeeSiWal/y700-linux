#!/usr/bin/env python3
"""Build the flip bundle manifest from the reviewed native manifest. Refuses without the built worker."""
from pathlib import Path
import hashlib, json, sys

B=Path(__file__).resolve().parent
NATIVE=Path('/home/siwal/y700-agent/screen-native-c0af5e3e2a18d003')
NATIVE_MANIFEST_SHA='c0af5e3e2a18d003142eb4b72d3cc7a3b285d0946230a897093638fbae71104a'
RETAINED=[(2604,'170594'),(3091,'247959'),(3441,'273023'),(3793,'297661'),(4139,'313514'),(4486,'330151'),
          (4847,'347934'),(5205,'363928'),(5558,'392540'),(5794,'415520'),(6153,'446041')]
FILES=['flip-held','flip-held.c','kms_lifecycle.h','run-flip.py','fliplog.py','health_guard.py','borrow.py',
       'runtime-readers.py','y700lib.py','expected-state.txt']

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
    if not ok:sys.exit('STOP: '+msg)

need(sha(NATIVE/'manifest.json')==NATIVE_MANIFEST_SHA,'native manifest changed')
need(not (B/'manifest.json').exists(),'manifest already exists; build a new bundle directory instead')
for f in FILES:need((B/f).is_file(),'missing bundle file: '+f)
need((B/'flip-held').read_bytes()[:20]==b'\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00','flip-held is not a static aarch64 ELF executable')
native=json.loads((NATIVE/'manifest.json').read_text())
native_result=NATIVE/'result.json'
m={k:native[k] for k in ('boot_id','kernel','root','module_notes','slot','module_names','load_order','load_notes',
                         'reviewed_waits','adci_log_tail','reviewed_fault_records','listener_templates','panel_messages',
                         'review_basis','expected_display_loaded','original_worker','original_executable')}
m['retained_workers']=[{'pid':p,'starttime':s} for p,s in RETAINED]
m['files']={f:sha(B/f) for f in FILES}
m['flip_review']={
 'scope':'Two new cached dumb 1904x3040 buffers A/B filled and CPU-verified before any commit, never rewritten. '
         'Eight blocking atomic commits change only FB_ID on plane95+plane128 together (500ms apart), each with PAGE_FLIP_EVENT; '
         'ninth commit returns both planes to user-confirmed FB335. No modeset/clock/ICC/unload/close/restore.',
 'basis':'Native two-plane FB335 visually confirmed; state text stable across snapshots; every retained worker source '
         'verified to read only stdin (gate) and sleep in hold(), so FLIP_COMPLETE events are consumed only by flip-held.',
 'native_manifest_sha256':NATIVE_MANIFEST_SHA,'native_result_sha256':sha(native_result),
 'underrun_base':{'1':13,'2':13},'steps':8,'step_ms':500}
with (B/'manifest.json').open('x') as f:json.dump(m,f,indent=2)
print('manifest.json',sha(B/'manifest.json'))
