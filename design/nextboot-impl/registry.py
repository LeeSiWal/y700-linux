"""Boot registry helpers: locate and verify the display-owner (and gpu-keeper) of THIS boot. Used by every later bundle."""
import hashlib, json, re
from pathlib import Path
AGENT = Path('/home/siwal/y700-agent')
DEBUG = Path('/sys/kernel/debug/dri/0')

def load(c, boot_id, kind='display'):
    name = 'registry-%s.json' if kind == 'display' else 'registry-' + kind + '-%s.json'
    p = AGENT/(name % boot_id[:8])
    c.need(p.is_file(), 'no %s registry for this boot: %s' % (kind, p))
    r = json.loads(c.read(p)); c.need(r['boot_id'] == boot_id, 'registry boot mismatch')
    return r, hashlib.sha256(c.read(p, True)).hexdigest()

def process_ok(c, proc):
    p = Path('/proc')/str(proc['pid'])
    c.need(p.exists() and c.task_stat(c.read(p/'stat'))['starttime'] == proc['starttime'], 'registered process gone/changed: %r' % proc['pid'])
    c.need(c.read(p/'cmdline', True).split(b'\0')[:len(proc['cmd'])] == [x.encode() for x in proc['cmd']], 'registered process command changed')

def underruns(c, path):
    return {int(i): int(n) for i, n in re.findall(r'intf:(\d+)\s+vsync:\s+\d+\s+underrun:\s+(\d+)', c.read(path))}

# A system-suspend device pass (S-2a) re-commits the saved atomic state on resume; the only difference it may leave in the
# state text is these three last-commit flags of the CRTC going 0 -> 1 (fb, planes, mode unchanged).
REBASE_FLAGS = ('mode_changed', 'active_changed', 'connectors_changed')

def flag_only_diff(native, now):
    """True when now differs from native exactly by REBASE_FLAGS lines going =0 -> =1 (same line positions)."""
    a, b = native.splitlines(), now.splitlines()
    if len(a) != len(b): return False
    diff = [(x, y) for x, y in zip(a, b) if x != y]
    return sorted(x.strip() for x, _ in diff) == sorted(f + '=0' for f in REBASE_FLAGS) and \
        all(y.strip() == x.strip()[:-1] + '1' and x[:len(x) - len(x.lstrip())] == y[:len(y) - len(y.lstrip())] for x, y in diff)

def rebased_states(c, reg, native_text):
    """Reviewed display states recorded after a suspend test of this boot (registry-rebase-<boot8>-*.json)."""
    out = []
    if not reg.get('boot_id'): return out   # fixtures without a boot have no rebase records
    for p in sorted(AGENT.glob('registry-rebase-%s-*.json' % reg['boot_id'][:8])):
        r = json.loads(c.read(p)); st = c.read(r['state_file'])
        c.need(r['boot_id'] == reg['boot_id'] and r['owner'] == {'pid': reg['owner']['pid'], 'starttime': reg['owner']['starttime']}, 'rebase record mismatch: ' + p.name)
        c.need(hashlib.sha256(st.encode()).hexdigest() == r['state_sha256'] and flag_only_diff(native_text, st), 'rebase state invalid: ' + p.name)
        out.append(st)
    return out

def display_ok(c, reg, state_text):
    """Owner alive, the only DRM client and master, native (or reviewed rebased) state unchanged, underrun counters unchanged."""
    process_ok(c, reg['owner'])
    lines = c.read(DEBUG/'clients').splitlines()
    c.need(len(lines) == 2 and lines[1].split()[1] == str(reg['owner']['pid']) and lines[1].split()[3] == 'y', 'DRM client/master changed')
    now = c.read(DEBUG/'state')
    c.need(now == state_text or now in rebased_states(c, reg, state_text), 'native DRM state changed')
    c.need(underruns(c, reg['encoder_status']) == {int(k): v for k, v in reg['underrun_base'].items()}, 'display underrun counter changed')
