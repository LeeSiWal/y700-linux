#!/usr/bin/env python3
"""Read-only: compare the current DRM state with this boot's native state (registry) and show clients + underruns.
Reads only /sys/kernel/debug/dri/0/{state,clients} and the registered encoder status node. Opens no DRM device.
Usage: sudo python3 -B drmdiff.py"""
import difflib, hashlib, json, re
from pathlib import Path

reg = json.loads(Path('/home/siwal/y700-agent/registry-3a1d6dac.json').read_text())
D = Path('/sys/kernel/debug/dri/0')
native = Path(reg['native_state_file']).read_text()
assert hashlib.sha256(native.encode()).hexdigest() == reg['native_state_sha256'], 'native state file changed'
now = (D/'state').read_text()
print('CLIENTS'); print((D/'clients').read_text().rstrip())
st = Path(reg['encoder_status']).read_text()
print('UNDERRUN', dict(re.findall(r'intf:(\d+)\s+vsync:\s+\d+\s+underrun:\s+(\d+)', st)))
print('STATE_EQUAL', now == native, 'lines native=%d now=%d' % (len(native.splitlines()), len(now.splitlines())))
d = list(difflib.unified_diff(native.splitlines(), now.splitlines(), 'native', 'now', n=2, lineterm=''))
print('\n'.join(d[:200])); print('DIFF_LINES', len(d))
out = Path('/home/siwal/y700-design/suspend-20260919/drm-state-after-s2a.txt')
if not out.exists(): out.write_text(now); out.chmod(0o644); print('saved', out)
