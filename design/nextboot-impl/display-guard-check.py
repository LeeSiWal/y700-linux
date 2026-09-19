#!/usr/bin/env python3
"""Read-only: evaluate the bring-up display guard (registry.display_ok) against the live display right now, e.g. while the
desktop runs. Prints which rule accepts the current state. Usage: sudo python3 -B display-guard-check.py"""
import importlib.util, json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
s = importlib.util.spec_from_file_location('readers', HERE/'runtime-readers.py'); c = importlib.util.module_from_spec(s); s.loader.exec_module(c)
import registry as rg
class Stop(Exception): pass
def need(ok, msg):
    if not ok: raise Stop(msg)
c.need = need
boot = c.read('/proc/sys/kernel/random/boot_id').strip()
reg, _ = rg.load(c, boot); native = c.read(reg['native_state_file']); now = c.read(rg.DEBUG/'state')
planes = {h.split()[0]: [l for l in ls if l.startswith(('\tfb=', '\tsrc-pos=', '\tcrtc-pos='))] for h, ls in rg._blocks(now) if h.split()[0] in rg.DESKTOP_PLANES}
print('planes now', json.dumps(planes))
print('native equal', now == native)
if rg.PRESENTER_REC.exists():
    rec = json.loads(c.read(rg.PRESENTER_REC)); print('presenter record', {k: rec.get(k) for k in ('presenter', 'fbs')})
else: print('presenter record: none')
print('desktop_ok', rg.desktop_ok(c, reg, native, now))
try: rg.display_ok(c, reg, native); print('DISPLAY_GUARD PASS')
except Stop as e: print('DISPLAY_GUARD STOP:', e); sys.exit(1)
