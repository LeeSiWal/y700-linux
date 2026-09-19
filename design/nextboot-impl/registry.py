"""Boot registry helpers: locate and verify the display-owner (and gpu-keeper) of THIS boot. Used by every later bundle."""
import hashlib, json, os, re
from pathlib import Path
AGENT = Path('/home/siwal/y700-agent')
DEBUG = Path('/sys/kernel/debug/dri/0')

def load(c, boot_id, kind='display'):
    name = 'registry-%s.json' if kind == 'display' else 'registry-' + kind + '-%s.json'
    p = AGENT/(name % boot_id[:8])
    c.need(p.is_file(), 'no %s registry for this boot: %s' % (kind, p))
    r = json.loads(c.read(p)); c.need(r['boot_id'] == boot_id, 'registry boot mismatch')
    sha = hashlib.sha256(c.read(p, True)).hexdigest()
    if kind == 'display' and 'underrun_base' in r and os.geteuid() == 0:
        # 2026-09-20: the desktop service (render-resolution tests, reverted on underrun) can leave underruns from earlier in
        # the boot. The guard checks that no NEW underrun happens during this stage: base = counters when the stage loads.
        now = underruns(c, r['encoder_status']); old = {int(k): v for k, v in r['underrun_base'].items()}
        if now != old: print('UNDERRUN_BASE earlier this boot %r -> stage baseline %r' % (old, now), flush=True)
        r['underrun_base'] = {str(k): v for k, v in now.items()}
    return r, sha

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

# Desktop mode (2026-09-20): y700-desktop.service shows the session on planes 95/128 through the owner's DRM file
# (pidfd_getfd, same drm_file -> still one client). Its record /run/y700-desktop-state/fbs.json (root 0700) lists the
# framebuffers it created, written before any of them reaches a plane and removed after the native layout is restored.
PRESENTER_REC = Path('/run/y700-desktop-state/fbs.json')
DESKTOP_PLANES = ('plane[95]:', 'plane[128]:')

def _blocks(text):
    out = []
    for l in text.splitlines():
        if l and not l[0].isspace(): out.append((l, []))
        elif out: out[-1][1].append(l)
    return out

def desktop_state_ok(native, now, fbs):
    """now == native except: planes 95/128 may show a framebuffer from fbs (fb= line, its nested details, src-pos = render
    resolution); *_changed commit flags may differ anywhere. Same planes/CRTCs/connectors, same crtc-pos, modes, links."""
    a, b = _blocks(native), _blocks(now)
    if [h for h, _ in a] != [h for h, _ in b]: return False
    flags = lambda ls: [l for l in ls if not l.strip().split('=')[0].endswith('_changed')]
    for (h, la), (_, lb) in zip(a, b):
        if h.split()[0] in DESKTOP_PLANES:
            key = lambda ls: [l for l in flags(ls) if not l.startswith(('\t\t', '\tfb=', '\tsrc-pos='))]
            if key(la) != key(lb): return False
            fb = [l for l in lb if l.startswith('\tfb=')]
            if len(fb) != 1 or int(fb[0][4:]) not in fbs: return False
        elif flags(la) != flags(lb): return False
    return True

def desktop_ok(c, reg, native, now):
    """The display differs from native only by the live desktop presenter of this boot and owner."""
    if not PRESENTER_REC.exists(): return False
    rec = json.loads(c.read(PRESENTER_REC)); pr = rec.get('presenter') or {}
    if rec.get('boot_id') != reg['boot_id'] or rec.get('owner') != {'pid': reg['owner']['pid'], 'starttime': reg['owner']['starttime']}: return False
    p = Path('/proc')/str(pr.get('pid'))
    if not (p.exists() and c.task_stat(c.read(p/'stat'))['starttime'] == pr.get('starttime')): return False
    if not any(a.endswith(b'desktop-service.py') for a in c.read(p/'cmdline', True).split(b'\0')): return False
    fbs = set(rec.get('fbs', []))
    fbs |= {int(l[4:]) for h, ls in _blocks(native) if h.split()[0] in DESKTOP_PLANES for l in ls if l.startswith('\tfb=')}
    return desktop_state_ok(native, now, fbs)

def display_ok(c, reg, state_text):
    """Owner alive, the only DRM client and master, native (or reviewed rebased) state unchanged or changed only by the
    desktop presenter (desktop_ok), underrun counters unchanged."""
    process_ok(c, reg['owner'])
    lines = c.read(DEBUG/'clients').splitlines()
    c.need(len(lines) == 2 and lines[1].split()[1] == str(reg['owner']['pid']) and lines[1].split()[3] == 'y', 'DRM client/master changed')
    now = c.read(DEBUG/'state')
    c.need(now == state_text or now in rebased_states(c, reg, state_text) or desktop_ok(c, reg, state_text, now), 'native DRM state changed')
    c.need(underruns(c, reg['encoder_status']) == {int(k): v for k, v in reg['underrun_base'].items()}, 'display underrun counter changed')
