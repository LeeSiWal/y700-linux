#!/usr/bin/env python3
"""y700-desktop.service main process (root). Long-running form of the r5..r8 presenter (desktop-20260919/presenter-run.py):
labwc (siwal) on a HEADLESS output, shown on the panel by a two-plane presenter on a BORROWED duplicate of the display
owner's DRM file (pidfd_getfd; no new DRM master, the owner keeps its fd). See DESIGN.md.
Exit codes: 0 stopped by SIGTERM/SIGINT (native layout restored); 1 error (systemd restarts); 3 precondition not met
(bring-up not finished, unknown screen state) -> no restart."""
import collections, ctypes, fcntl, importlib.util, json, os, select, signal, stat, struct, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; DESK = Path('/home/siwal/y700-design/desktop-20260919'); IMPL = Path('/home/siwal/y700-design/nextboot-impl')
sys.path.insert(0, str(IMPL)); sys.path.insert(0, str(DESK))
RUN = Path(os.environ.get('Y700_DESKTOP_RUN', '/run/y700-desktop'))            # siwal 0700: wayland socket, seat socket
STATE = Path(os.environ.get('Y700_DESKTOP_STATE', '/run/y700-desktop-state'))  # root 0700: fbs.json, status.json, lock
AGENT = Path('/home/siwal/y700-agent')
W, H = 1904, 3040
RMFB = (3 << 30) | (4 << 16) | (ord('d') << 8) | 0xAF            # DRM_IOWR(0xAF, unsigned int)
DESTROY_DUMB = (3 << 30) | (4 << 16) | (ord('d') << 8) | 0xB4    # DRM_IOWR(0xB4, struct drm_mode_destroy_dumb)
BRINGUP_WAIT_S = 1800; STATUS_EVERY_S = 60
EXIT_PRECONDITION = 3

class Precondition(Exception): pass

# ---------- pure decision helpers (unit-tested in test_desktop_service.py) ----------
def bringup_ready(doc, boot_id, need=('owner', 'gpu')):   # the caller adds 'touch' when the touch proxy is on
    """True when bringup-<boot8>.json of THIS boot has an ok step for every stage in need."""
    if not doc or doc.get('boot_id') != boot_id: return False
    ok = {s['stage'] for r in doc.get('runs', []) for s in r.get('steps', []) if s.get('result') == 'ok'}
    return all(n in ok for n in need)

def recovery_action(rec, boot_id, owner, plane_fbs, native_fb, existing_fbs):
    """What to do with the screen before starting. plane_fbs = FB ids now on the left/right planes.
    -> ('none', []) | ('cleanup', ids) | ('restore', ids) | ('refuse', reason)
    Only FB ids recorded by THIS service for THIS boot and owner are ever restored away from or removed."""
    ours = rec is not None and rec.get('boot_id') == boot_id and rec.get('owner') == {'pid': owner['pid'], 'starttime': owner['starttime']}
    if rec is not None and not ours: return ('refuse', 'fbs record of another boot/owner')
    rec_fbs = set(rec['fbs']) if ours else set()
    leftover = sorted(f for f in rec_fbs if f in existing_fbs)
    if native_fb not in existing_fbs: return ('refuse', 'native FB %d gone' % native_fb)
    if all(f == native_fb for f in plane_fbs):
        return ('cleanup', leftover) if ours else ('none', [])
    if ours and all(f == native_fb or f in rec_fbs for f in plane_fbs):
        return ('restore', leftover)
    return ('refuse', 'planes show FB %r, not native %d or ours %r' % (plane_fbs, native_fb, sorted(rec_fbs)))

# ---------- runtime ----------
def _load_modules():
    global c, rg, bo, k, drmabi, bg, wl, wlcapture, dr
    import registry as rg, borrow_owner as bo, drmkms as k, drmabi, bootguard as bg, wl, wlcapture
    s = importlib.util.spec_from_file_location('desktop_run', DESK/'desktop-run.py'); dr = importlib.util.module_from_spec(s); s.loader.exec_module(dr)
    c = dr.c
    dr.LOG = collections.deque(maxlen=200)                         # dr.log appends here; bounded for a long run
    dr.RUN = RUN; dr.SOCK = RUN/'y700-seat.sock'                    # the seat shim reads these module globals at call time

LOGF = None
def log(*a):
    """journal (stdout) + ~/y700-agent/desktop-<boot8>.log (siwal cannot read the journal)"""
    line = ' '.join(str(x) for x in a); print(line, flush=True)
    if LOGF:
        try: LOGF.write(time.strftime('%F %T ') + line + '\n'); LOGF.flush()
        except OSError: pass

def open_logs(boot):
    global LOGF
    p = AGENT/('desktop-%s.log' % boot[:8]); LOGF = open(p, 'a'); os.chown(p, 1000, 1000)

def write_json(p, obj, uid=None):
    tmp = p.with_suffix('.tmp'); tmp.write_text(json.dumps(obj, indent=1)); os.replace(tmp, p)

def planes_for(fd, reg, fb, sw=W, sh=H):
    """left/right halves of an sw x sh framebuffer onto the two panel halves (the plane scalers upscale when sw < W)"""
    t = reg['topology']; half = W // 2; shalf = sw // 2
    lp = k.properties(fd, t['left'], k.OBJECT_PLANE); rp = k.properties(fd, t['right'], k.OBJECT_PLANE)
    def pl(p, sx, x): return [(p['FB_ID'][0], fb), (p['CRTC_ID'][0], t['crtc']), (p['SRC_X'][0], sx << 16), (p['SRC_Y'][0], 0),
                              (p['SRC_W'][0], shalf << 16), (p['SRC_H'][0], sh << 16), (p['CRTC_X'][0], x), (p['CRTC_Y'][0], 0),
                              (p['CRTC_W'][0], half), (p['CRTC_H'][0], H)]
    return {t['left']: pl(lp, 0, 0), t['right']: pl(rp, shalf, half)}

def connector_modes(fd, connector_id):
    """Every mode the panel offers (this one: 1904x3040 at 30/60/90/120/144/165 Hz), as (fields, raw) pairs.
    Two-call pattern: ask for the counts, then hand the kernel a buffer for the mode array."""
    r = drmabi.ioctl(fd, 'GETCONNECTOR', k.GETCONNECTOR, 0, 0, 0, 0, 0, 0, 0, 0, connector_id, 0, 0, 0, 0, 0, 0, 0)
    n = r[4]
    if not n: return []
    buf = ctypes.create_string_buffer(n * k.MODEINFO.size)
    drmabi.ioctl(fd, 'GETCONNECTOR', k.GETCONNECTOR, 0, ctypes.addressof(buf), 0, 0, n, 0, 0, 0, connector_id,
                 0, 0, 0, 0, 0, 0, 0)
    out = []
    for i in range(n):
        raw = bytes(buf.raw[i * k.MODEINFO.size:(i + 1) * k.MODEINFO.size])
        out.append((k.mode_fields(raw), raw))
    return out


def blank_props(fd, reg, on):
    """Panel off/on for display sleep: the planes are detached and the CRTC deactivated (a modeset), which stops the
    DPU and the DSI link - the backlight alone leaves ~3 W of scanout running. on=True restores the CRTC; the caller
    commits the planes again with the current framebuffer."""
    t = reg['topology']
    cp = k.properties(fd, t['crtc'], k.OBJECT_CRTC)
    props = {t['crtc']: [(cp['ACTIVE'][0], 1 if on else 0)]}
    if not on:
        for side in ('left', 'right'):
            pp = k.properties(fd, t[side], k.OBJECT_PLANE)
            props[t[side]] = [(pp['FB_ID'][0], 0), (pp['CRTC_ID'][0], 0)]
    return props

# render resolution (percent of the panel) -> framebuffer size; the logical desktop stays 952x1520 (output scale = w/952)
RESOLUTIONS = {100: (1904, 3040), 90: (1712, 2736), 80: (1524, 2432), 67: (1276, 2036), 50: (952, 1520)}
LOGICAL_W = 952

def resolution_config():
    pct = int(desktop_conf().get('resolution', 100))
    return (pct,) + RESOLUTIONS[pct] if pct in RESOLUTIONS else (100,) + RESOLUTIONS[100]

GEM_CLOSE = (1 << 30) | (8 << 16) | (ord('d') << 8) | 0x09    # DRM_IOW(0x09, struct drm_gem_close {u32 handle, pad})
PRIME_FD_TO_HANDLE = (3 << 30) | (12 << 16) | (ord('d') << 8) | 0x2E   # DRM_IOWR(0x2E, struct drm_prime_handle)
DMA_HEAP_ALLOC = 0xC0184800                                   # _IOWR('H', 0, struct dma_heap_allocation_data)
DMA_BUF_SYNC = (1 << 30) | (8 << 16) | (ord('b') << 8) | 0    # _IOW('b', 0, struct dma_buf_sync {u64 flags})
DMA_BUF_SYNC_WRITE, DMA_BUF_SYNC_END = 2, 4
HEAP = Path('/dev/dma_heap/system')

def heap_fb(drm_fd, w, h, pitch=None):
    """A scanout buffer the compositor can paint into directly: allocate from the vendor dma-buf heap, import it into
    DRM (PRIME) for the framebuffer, and keep the dma-buf fd so it can also be handed to wl_shm. Same dict shape as
    drmkms.dumb_fb plus 'dmabuf'."""
    import mmap as _mmap
    pitch = pitch or w * 4
    size = pitch * h
    hfd = os.open(HEAP, os.O_RDWR | os.O_CLOEXEC)
    try:
        req = bytearray(struct.pack('<QIIQ', size, 0, os.O_RDWR | os.O_CLOEXEC, 0))
        fcntl.ioctl(hfd, DMA_HEAP_ALLOC, req, True)
    finally:
        os.close(hfd)
    dfd = struct.unpack_from('<I', req, 8)[0]
    try:
        ph = bytearray(struct.pack('<IIi', 0, 0, dfd))
        fcntl.ioctl(drm_fd, PRIME_FD_TO_HANDLE, ph, True)
        handle = struct.unpack_from('<I', ph, 0)[0]
        m = _mmap.mmap(dfd, size, _mmap.MAP_SHARED, _mmap.PROT_READ | _mmap.PROT_WRITE)
    except OSError:
        os.close(dfd); raise
    return {'handle': handle, 'pitch': pitch, 'size': size, 'map': m, 'dmabuf': dfd,
            'addr': ctypes.addressof(ctypes.c_char.from_buffer(m))}

def dma_flush(dfd):
    """the compositor wrote into this cached buffer with the CPU; flush before the display reads it by DMA"""
    fcntl.ioctl(dfd, DMA_BUF_SYNC, struct.pack('<Q', DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE))

def add_fb_noleak(fd, w, h, handle, pitch):
    """drmkms.add_fb without its handle leak: GETFB2 called by root on a master file creates a NEW GEM handle for the
    buffer (handles[0] of the readback) that add_fb never closes -> the 23 MB buffer outlives DESTROY_DUMB for the
    whole boot (seen as the 2nd handle growing 15 -> 17 -> 19 over service starts). drmkms is shared with the reviewed
    bundles and stays unchanged; here the readback handle is closed right away."""
    fb = drmabi.ioctl(fd, 'ADDFB2', drmabi.FB_CMD2, 0, w, h, drmabi.XRGB8888, 0, handle, 0, 0, 0, pitch, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)[0]
    g = drmabi.ioctl(fd, 'GETFB2', drmabi.FB_CMD2, fb, *([0] * 20))
    extra = g[5]
    if extra and extra != handle: fcntl.ioctl(fd, GEM_CLOSE, struct.pack('<II', extra, 0))
    if not (g[1:4] == (w, h, drmabi.XRGB8888) and g[9] == pitch and g[13] == 0 and g[17] == 0):
        raise RuntimeError('framebuffer readback %r' % (g,))
    return fb, extra

def desktop_conf():
    try: return json.loads((HERE/'desktop.json').read_text())
    except FileNotFoundError: return {}

ROTATIONS = {0: (0, '1 0 0 0 1 0'), 90: (1, '0 1 0 -1 0 1'), 180: (2, '-1 0 1 0 -1 1'), 270: (3, '0 -1 1 1 0 0')}
ROTATE_CYCLE = [270, 90, 0]                                   # landscape, other landscape, portrait
BACKLIGHT = Path('/sys/class/backlight/panel0-backlight')     # aw99706a, 0..4095 (power-20260919 README)
BL_MIN = 200                                                  # never fully dark from the bar (1024 ~ 3.1 W, 4095 ~ 6 W)
CTL_MAX = 64

def compose(outer, inner):
    """normalized affine matrices 'a b c d e f' (x' = a u + b v + c, y' = d u + e v + f): apply inner, then outer"""
    a, b, c, d, e, f = map(float, outer.split()); A, B, C, D, E, F = map(float, inner.split())
    m = (a * A + b * D, a * B + b * E, a * C + b * F + c, d * A + e * D, d * B + e * E, d * C + e * F + f)
    return ' '.join('%g' % (0.0 + x) for x in m)

PHOSH_TOUCH_OFFSETS = {0: 0, 90: 180, 180: 0, 270: 180}      # guess from the 270 observation; adjustable live (y700-ctl touch)

def touch_offset(rot, shell):
    """extra touch rotation for this output rotation: desktop.json touch_offsets {"270": 180, ...} or the shell default"""
    saved = desktop_conf().get('touch_offsets', {})
    if str(rot) in saved: return int(saved[str(rot)])
    return PHOSH_TOUCH_OFFSETS[rot] if shell == 'phosh' else 0

def touch_matrix(rot, offset):
    """touch-proxy matrix: the rotation matrix verified under labwc, then an extra rotation 'touch_offset' for shells
    that rotate touch themselves (phoc: 180 degrees off at rotation 270 on the panel, 2026-09-19 22:32)"""
    return compose(ROTATIONS[offset][1], ROTATIONS[rot][1])

def parse_ctl(line, cur_bl, bl_max, cur_rot):
    """One control request from the siwal bar -> ('brightness', raw) | ('rotate', deg) | ('get', text) | ('error', text).
    Only these two knobs exist; values are clamped."""
    a = line.strip().split()
    if not a or len(line) > CTL_MAX: return ('error', 'empty or too long')
    if a[0] == 'brightness' and len(a) == 2:
        pct = round(cur_bl * 100 / bl_max)
        if a[1] == 'get': return ('get', '%d' % pct)
        if a[1] in ('up', 'down'): pct += 10 if a[1] == 'up' else -10
        elif a[1].isdigit(): pct = int(a[1])
        else: return ('error', 'brightness up|down|get|0..100')
        return ('brightness', max(BL_MIN, min(bl_max, round(max(0, min(100, pct)) * bl_max / 100))))
    if a[0] == 'resolution' and len(a) == 2:
        if a[1] == 'get': return ('get', 'resolution')
        if a[1].isdigit() and int(a[1]) in RESOLUTIONS: return ('resolution', int(a[1]))
        return ('error', 'resolution get|%s' % '|'.join(str(x) for x in sorted(RESOLUTIONS, reverse=True)))
    if a[0] == 'refresh' and len(a) == 2:
        if a[1] == 'get': return ('get', 'refresh')
        if a[1].isdigit(): return ('refresh', int(a[1]))
        return ('error', 'refresh get|<hz>')
    if a[0] == 'sleep' and len(a) in (1, 2):
        if len(a) == 1 or a[1] == 'on': return ('sleep', True)
        if a[1] == 'off': return ('sleep', False)
        if a[1] == 'get': return ('get', 'sleep')
        return ('error', 'sleep [on|off|get]')
    if a[0] == 'wake' and len(a) == 1: return ('sleep', False)
    if a[0] == 'touch' and len(a) == 2:
        if a[1].isdigit() and int(a[1]) in ROTATIONS: return ('touch', int(a[1]))
        return ('error', 'touch 0|90|180|270 (extra touch rotation)')
    if a[0] == 'rotate' and len(a) == 2:
        if a[1] == 'get': return ('get', '%d' % cur_rot)
        if a[1] == 'next':
            return ('rotate', ROTATE_CYCLE[(ROTATE_CYCLE.index(cur_rot) + 1) % len(ROTATE_CYCLE)] if cur_rot in ROTATE_CYCLE else ROTATE_CYCLE[0])
        if a[1].isdigit() and int(a[1]) in ROTATIONS: return ('rotate', int(a[1]))
        return ('error', 'rotate next|get|0|90|180|270')
    return ('error', 'unknown request')

class Control:
    """Unix socket RUN/ctl.sock for the siwal session (uid checked with SO_PEERCRED): brightness and rotation."""
    def __init__(self):
        import socket as so
        self.so = so; p = RUN/'ctl.sock'
        if p.exists() or p.is_symlink(): p.unlink()
        self.srv = so.socket(so.AF_UNIX, so.SOCK_STREAM); self.srv.bind(str(p)); os.chown(p, dr.UID, dr.GID); os.chmod(p, 0o600)
        self.srv.listen(4); self.srv.setblocking(False)
    def poll(self, handler):
        import select
        while select.select([self.srv], [], [], 0)[0]:
            conn, _ = self.srv.accept(); conn.settimeout(1.0)
            try:
                pid, uid, gid = struct.unpack('3i', conn.getsockopt(self.so.SOL_SOCKET, self.so.SO_PEERCRED, 12))
                if uid != dr.UID: conn.sendall(b'error uid\n'); continue
                req = conn.recv(CTL_MAX + 1).decode('ascii', 'replace')
                conn.sendall((handler(req) + '\n').encode())
            except OSError as e: log('CTL error', repr(e))
            finally: conn.close()
    def close(self):
        self.srv.close(); (RUN/'ctl.sock').unlink(missing_ok=True)

def labwc_pid(pgid):
    for d in Path('/proc').iterdir():
        if d.name.isdigit():
            try:
                if (d/'comm').read_text().strip() == 'labwc' and os.getpgid(int(d.name)) == pgid: return int(d.name)
            except (OSError, ProcessLookupError): pass
    return None

SHELLS = ('labwc', 'phosh')

def shell_config():
    """desktop.json {"shell": "labwc"|"phosh"} (default labwc: the reviewed desktop; phosh = touch shell on phoc)."""
    sh = desktop_conf().get('shell', 'labwc')
    if sh not in SHELLS: raise Precondition('desktop.json shell must be one of %r, not %r' % (SHELLS, sh))
    if sh == 'phosh' and not (Path('/usr/bin/phoc').exists() and Path('/usr/libexec/phosh').exists()): raise Precondition('phosh/phoc not installed')
    return sh

def session_env():
    """KEY=VALUE lines of labwc-config/environment (shared session environment: FEX PATH, turnip ICD, WSI, XDG_DATA_DIRS)."""
    out = {}
    for line in (HERE/'labwc-config'/'environment').read_text().splitlines():
        k, sep, v = line.partition('=')
        if sep and k.isupper() and k.replace('_', '').isalnum(): out[k] = v
    return out

def phoc_config(rw=W, rh=H):
    d = RUN/'phoc'; d.mkdir(mode=0o700, exist_ok=True); p = d/'phoc.ini'
    p.write_text('[core]\nxwayland=true\n\n[output:HEADLESS-1]\nmode = %dx%d\nscale = %g\n' % (rw, rh, rw / LOGICAL_W))
    for f in (d, p): os.chown(f, dr.UID, dr.GID)
    return p

def rotation_config():
    """desktop.json {"rotation": 0|90|180|270} (default 90 = landscape, device turned clockwise).
    -> (wl_output transform, libinput touch calibration matrix mapping the portrait panel to the rotated output)"""
    try: rot = int(json.loads((HERE/'desktop.json').read_text()).get('rotation', 90))
    except FileNotFoundError: rot = 90
    # 90/270 matrices verified on the panel 2026-09-19: the first derivation had them swapped (touch 180 degrees off)
    if rot not in ROTATIONS: raise Precondition('desktop.json rotation must be 0/90/180/270, not %r' % rot)
    return (rot,) + ROTATIONS[rot]

def session_config(rot, matrix):
    """labwc -C dir for this start: the reviewed environment/autostart plus a generated rc.xml (touch rotation)."""
    d = RUN/'labwc-config'; d.mkdir(mode=0o700, exist_ok=True)
    for n in ('environment', 'autostart'): (d/n).write_text((HERE/'labwc-config'/n).read_text())
    (d/'rc.xml').write_text('<?xml version="1.0"?>\n<labwc_config>\n'
        '  <!-- generated by desktop-service.py: rotation %d -->\n'
        '  <libinput>\n    <device category="touch"><calibrationMatrix>%s</calibrationMatrix></device>\n  </libinput>\n'
        '  <touch deviceName="NVTCapacitiveTouchScreen" mapToOutput="HEADLESS-1" mouseEmulation="no"/>\n'
        '</labwc_config>\n' % (rot, matrix))
    for f in [d] + list(d.iterdir()): os.chown(f, dr.UID, dr.GID)
    return d

def restore_boot_mode(fd, reg):
    """Put the panel back on the mode the owner recorded at boot. The refresh rate is ours to change while the
    service runs (y700-ctl refresh), but the next start compares the whole DRM state with that record, so leaving the
    panel at 60 Hz made the service refuse to start with 'display not in the native state'."""
    cp = k.properties(fd, reg['topology']['crtc'], k.OBJECT_CRTC)
    if not reg.get('mode_blob') or cp['MODE_ID'][1] == reg['mode_blob']: return False
    props = {reg['topology']['crtc']: [(cp['MODE_ID'][0], k.create_blob(fd, k.read_mode(fd, reg['mode_blob']))),
                                       (cp['ACTIVE'][0], 1)]}
    props.update(planes_for(fd, reg, reg['native_fb'])); objs = list(props)
    k.atomic(fd, k.ATOMIC_TEST_ONLY | k.ATOMIC_ALLOW_MODESET, objs, props)
    k.atomic(fd, k.ATOMIC_ALLOW_MODESET, objs, props)
    return True

def commit_native(fd, reg):
    if restore_boot_mode(fd, reg): return                     # that commit already put the native fb on both planes
    props = planes_for(fd, reg, reg['native_fb']); objs = list(props)
    k.atomic(fd, k.ATOMIC_TEST_ONLY, objs, props); k.atomic(fd, 0, objs, props)

def remove_fbs(fd, fbs, handles, errors):
    for f in fbs:
        try: fcntl.ioctl(fd, RMFB, struct.pack('<I', f))
        except OSError as e: errors.append('RMFB %d: %r' % (f, e))
    for h in handles:
        try: fcntl.ioctl(fd, DESTROY_DUMB, struct.pack('<I', h))
        except OSError as e: errors.append('DESTROY_DUMB %d: %r' % (h, e))

def wait_bringup(boot, need=('owner', 'gpu')):
    """'touch' belongs in need when the touch proxy is on: the touch stage runs after gpu, and starting before it
    leaves the proxy with no touchscreen to open (seen on boot 4f44e566: the service started 2.5 min too early)."""
    p = AGENT/('bringup-%s.json' % boot[:8]); t0 = time.monotonic(); said = False
    while True:
        try: doc = json.loads(p.read_text())
        except (OSError, ValueError): doc = None
        if bringup_ready(doc, boot, need): return
        if time.monotonic() - t0 > BRINGUP_WAIT_S:
            raise Precondition('bring-up %s not ok after %d s (%s)' % ('+'.join(need), BRINGUP_WAIT_S, p))
        if not said: log('WAIT bring-up %s (%s)' % ('+'.join(need), p.name)); said = True
        time.sleep(5)

def prepare_gpu_node():
    p = Path('/dev/kgsl-3d0'); st = os.stat(p)
    if not stat.S_ISCHR(st.st_mode): raise Precondition('/dev/kgsl-3d0 is not a character device')
    if stat.S_IMODE(st.st_mode) != 0o666: os.chmod(p, 0o666); log('GPU /dev/kgsl-3d0 -> 0666')
    # the vendor dma-buf heaps are registered by the kernel but nothing creates/opens their nodes for the user here
    # (no ueventd): with /dev/dma_heap/system unreadable, turnip drops VK_KHR_external_memory_fd and
    # VK_EXT_external_memory_dma_buf ("Unable to open neither /dev/dma_heap/system nor /dev/ion")
    h = Path('/dev/dma_heap/system'); sysfs = Path('/sys/class/dma_heap/system/dev')
    try:
        if not h.exists() and sysfs.exists():
            ma, mi = (int(x) for x in sysfs.read_text().strip().split(':'))
            h.parent.mkdir(parents=True, exist_ok=True)
            os.mknod(h, 0o666 | stat.S_IFCHR, os.makedev(ma, mi)); log('DMA-BUF heap node created', h)
        if h.exists() and stat.S_IMODE(os.stat(h).st_mode) != 0o666:
            os.chmod(h, 0o666); log('DMA-BUF /dev/dma_heap/system -> 0666')
    except OSError as e:
        log('DMA-BUF heap setup failed (not fatal)', repr(e))

def recover(reg, boot):
    """Bring the screen back to the native layout if a previous instance of this service left its FBs on it."""
    recp = STATE/'fbs.json'; rec = json.loads(recp.read_text()) if recp.exists() else None
    fd = bo.borrow_fd(c, reg)
    try:
        t = reg['topology']; plane_fbs = [k.get_plane(fd, t['left'])['fb_id'], k.get_plane(fd, t['right'])['fb_id']]
        act, arg = recovery_action(rec, boot, reg['owner'], plane_fbs, reg['native_fb'], set(drmabi.fb_ids(fd)))
        log('RECOVERY', act, arg, 'planes', plane_fbs)
        if act == 'refuse': raise Precondition('screen state not ours: ' + arg)
        try:
            if restore_boot_mode(fd, reg):
                time.sleep(1.5)                      # the modeset lands asynchronously; the state check reads it next
                log('RECOVERY panel back on the boot mode')
        except OSError as e: log('RECOVERY boot mode restore failed', repr(e))
        if act in ('restore', 'cleanup'):
            if act == 'restore': commit_native(fd, reg); log('RECOVERY native layout committed')
            # all recorded handles are ours (a handle is recorded before its FB exists); a handle already gone is fine
            errs = []; remove_fbs(fd, arg, rec['handles'] if rec else [], errs)
            errs = [e for e in errs if not (e.startswith('DESTROY_DUMB') and ('EINVAL' in e or 'ENOENT' in e or 'Errno 22' in e or 'Errno 2]' in e))]
            if errs: raise RuntimeError('recovery cleanup: %r' % errs)
            recp.unlink(); log('RECOVERY removed', arg)
    finally:
        os.close(fd)

def fix_run_ownership():
    """RUN is siwal's XDG_RUNTIME_DIR: everything under it must belong to siwal (root-owned dconf/ and dbus-1/ appeared
    there on 2026-09-19 and broke GTK settings storage). lchown only, symlinks are not followed."""
    fixed = []
    for root, dirs, files in os.walk(RUN):
        for n in dirs + files:
            q = os.path.join(root, n); st = os.lstat(q)
            if st.st_uid != dr.UID: os.lchown(q, dr.UID, dr.GID); fixed.append(q)
    if fixed: log('RUN ownership fixed', fixed[:10], len(fixed))

EXTERNAL_BUSES = {0x0003, 0x0005}                              # BUS_USB, BUS_BLUETOOTH: gamepads, keyboards, mice

def external_input_devices(sysroot='/sys/class/input', exclude=()):
    """{eventN: (major, minor, name)} for input devices on USB/Bluetooth (the internal gpio-keys/touch/pen and our
    uinput touch proxy are virtual or on the SoC bus and are left to the seat shim). exclude: (vendor, product) pairs
    whose interfaces stay hidden (pads re-emitted by padproxy as an Xbox 360 pad)."""
    out = {}
    for d in sorted(Path(sysroot).glob('event*')):
        try:
            bus = int((d/'device/id/bustype').read_text(), 16)
            if bus not in EXTERNAL_BUSES: continue
            if exclude and (int((d/'device/id/vendor').read_text(), 16), int((d/'device/id/product').read_text(), 16)) in exclude: continue
            ma, mi = map(int, (d/'dev').read_text().split(':')); out[d.name] = (ma, mi, (d/'device/name').read_text().strip())
        except (OSError, ValueError): continue
    return out

def external_hidraw_devices(sysroot='/sys/class/hidraw', exclude=()):
    """{hidrawN: (major, minor, name)} for HID devices on USB/Bluetooth. Steam (Big Picture, Steam Input) reads
    controllers through hidraw, games mostly through evdev."""
    out = {}
    for d in sorted(Path(sysroot).glob('hidraw*')):
        try:
            ue = dict(l.split('=', 1) for l in (d/'device/uevent').read_text().splitlines() if '=' in l)
            hid = ue.get('HID_ID', '0:0:0').split(':')
            if int(hid[0], 16) not in EXTERNAL_BUSES: continue
            if exclude and (int(hid[1], 16), int(hid[2], 16)) in exclude: continue
            ma, mi = map(int, (d/'dev').read_text().split(':')); out[d.name] = (ma, mi, ue.get('HID_NAME', '?'))
        except (OSError, ValueError): continue
    return out

def uinput_for_session(node='/dev/uinput'):
    """Steam Input creates its virtual gamepad/keyboard/mouse through uinput (Ubuntu's steam-devices udev rule gives the
    seat user access). Without it Big Picture loses D-pad, View, Menu and Guide (2026-09-20). Owner siwal, 0600."""
    p = Path(node)
    try:
        if not p.exists():
            ma, mi = map(int, Path('/sys/class/misc/uinput/dev').read_text().split(':')); os.mknod(p, 0o600 | stat.S_IFCHR, os.makedev(ma, mi))
        st = os.lstat(p)
        if stat.S_ISCHR(st.st_mode) and st.st_uid != dr.UID: os.chown(p, dr.UID, dr.GID); os.chmod(p, 0o600); log('UINPUT owner ->', dr.UID)
    except OSError as e: log('UINPUT error', repr(e))

class InputNodes:
    """/dev is a plain tmpfs here (no devtmpfs): nobody creates nodes for hot-plugged devices. Create /dev/input/eventN
    and /dev/hidrawN for external USB/BT input devices (Kishi, keyboards, pads) owned by the session user (0600);
    remove them on unplug."""
    def __init__(self, dev='/dev'): self.made = {}; self.dev = Path(dev)
    def scan(self):
        import padproxy; hide = set(padproxy.REMAPS)
        cur = {self.dev/'input'/k: v for k, v in external_input_devices(exclude=hide).items()}
        cur.update({self.dev/k: v for k, v in external_hidraw_devices(exclude=hide).items()})
        every = {self.dev/'input'/k: v for k, v in external_input_devices().items()}
        every.update({self.dev/k: v for k, v in external_hidraw_devices().items()})
        self.hidden = {n: v for n, v in every.items() if n not in cur}
        return cur
    def unhide(self):
        """remove nodes of remapped pads left by an earlier instance (only when the node is exactly that device)"""
        for node, (ma, mi, name) in getattr(self, 'hidden', {}).items():
            try:
                st = os.lstat(node)
                if stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(ma, mi): node.unlink(); log('INPUT node hidden (remapped pad)', node, repr(name))
            except FileNotFoundError: pass
            except OSError as e: log('INPUT hide error', node, repr(e))
    def sync(self, cur=None):
        if cur is None: cur = self.scan(); self.unhide()
        for node, (ma, mi, name) in cur.items():
            if self.made.get(node) == (ma, mi): continue
            try:
                if node.exists():
                    st = os.stat(node)
                    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(ma, mi)): log('INPUT node mismatch, left alone', node); continue
                else:
                    os.mknod(node, 0o600 | stat.S_IFCHR, os.makedev(ma, mi))
                os.chown(node, dr.UID, dr.GID); os.chmod(node, 0o600); self.made[node] = (ma, mi); log('INPUT node', node, repr(name))
            except OSError as e: log('INPUT node error', node, repr(e))
        for node in [n for n in self.made if n not in cur]:
            try: node.unlink(missing_ok=True)
            except OSError: pass
            log('INPUT node removed', node); del self.made[node]
    def close(self):
        for node in list(self.made): node.unlink(missing_ok=True)
        self.made.clear()

STOP = []
def _on_signal(sig, _frm): STOP.append(sig)

def main():
    if os.geteuid() != 0: raise Precondition('must run as root (systemd unit)')
    _load_modules()
    for s in (signal.SIGTERM, signal.SIGINT): signal.signal(s, _on_signal)
    for d, uid in ((RUN, dr.UID), (STATE, 0)):
        d.mkdir(mode=0o700, exist_ok=True); os.chown(d, uid, dr.GID if uid else 0); os.chmod(d, 0o700)
    lock = open(STATE/'lock', 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise Precondition('another desktop presenter holds the lock')
    boot = c.read('/proc/sys/kernel/random/boot_id').strip(); open_logs(boot); log('START pid', os.getpid())
    need = ('owner', 'gpu') + (('touch',) if desktop_conf().get('touch_proxy', shell_config() == 'phosh') else ())
    wait_bringup(boot, need)
    reg, _ = rg.load(c, boot)
    prepare_gpu_node()
    recover(reg, boot)
    native = c.read(reg['native_state_file']); now = c.read(rg.DEBUG/'state')
    if not (now == native or now in rg.rebased_states(c, reg, native)):
        time.sleep(2); now = c.read(rg.DEBUG/'state')        # one retry: a modeset we just undid may still be settling
    if not (now == native or now in rg.rebased_states(c, reg, native)):
        import difflib
        d = [l for l in difflib.unified_diff(native.splitlines(), now.splitlines(), 'native', 'now', lineterm='', n=1)][:14]
        for l in d: log('STATE', l)
        raise Precondition('display not in the native state')
    v0 = dr.drm_view(reg)
    if not (v0['master_pid_ok'] and v0['clients'] == 1): raise Precondition('owner is not the sole DRM master')
    for p in RUN.glob('wayland-*'): p.unlink()                      # stale sockets of a crashed instance (siwal dir)
    fix_run_ownership()
    dm_seen = set(c.dmesg().splitlines())
    fd = bo.borrow_fd(c, reg); allfbs = []; lab = None; shim = None; ctl = None; tproxy = None; rc = 0; errs = []
    # presenter identity: registry.desktop_ok (bring-up stages running while the desktop is up) accepts the display state
    # only while this very process is alive and every desktop plane shows one of the fbs listed here
    rec = {'boot_id': boot, 'owner': {'pid': reg['owner']['pid'], 'starttime': reg['owner']['starttime']}, 'fbs': [], 'handles': [], 'started': time.time(),
           'presenter': {'pid': os.getpid(), 'starttime': c.task_stat(c.read('/proc/self/stat'))['starttime']}}
    # zero copy: the framebuffers come from the dma-buf heap so the same memory can be handed to wl_shm and the
    # compositor paints the frame straight into the buffer the panel scans out (no per-frame copy here)
    zc = {'on': bool(desktop_conf().get('presenter_zerocopy', False)) and HEAP.exists(), 'target': None, 'used': False}
    def alloc(rw, rh):
        """two rw x rh XR24 framebuffers, recorded in fbs.json before they can reach the screen"""
        out = []
        for _ in range(2):
            d = None
            if zc['on']:
                # the heap buffer must also be acceptable as a framebuffer (pitch/alignment): if anything on that path
                # fails, drop back to a dumb buffer and to copying, rather than leaving the screen without one
                try:
                    d = heap_fb(fd, rw, rh)
                    rec['handles'].append(d['handle']); write_json(STATE/'fbs.json', rec)
                    d['fb'], extra = add_fb_noleak(fd, rw, rh, d['handle'], d['pitch'])
                except (OSError, RuntimeError) as e:
                    zc['on'] = False; log('ZEROCOPY unavailable, using dumb buffers:', repr(e))
                    if d is not None:
                        if d['handle'] in rec['handles']: rec['handles'].remove(d['handle'])
                        try: d['map'].close(); os.close(d['dmabuf'])
                        except OSError: pass
                        d = None
            if d is None:
                d = k.dumb_fb(fd, rw, rh)
                rec['handles'].append(d['handle']); write_json(STATE/'fbs.json', rec)
                d['fb'], extra = add_fb_noleak(fd, rw, rh, d['handle'], d['pitch'])
            d['w'], d['h'] = rw, rh; out.append(d); allfbs.append(d)
            rec['fbs'].append(d['fb']); write_json(STATE/'fbs.json', rec)
            ctypes.memset(d['addr'], 0, d['size'])
        log('FBS', [(d['fb'], d['handle'], '%dx%d' % (rw, rh)) for d in out]); return out
    def free(ds):
        """only for framebuffers that are no longer on a plane"""
        e = []; remove_fbs(fd, [d['fb'] for d in ds if 'fb' in d], [d['handle'] for d in ds], e)
        for d in ds:
            if d.get('wlbuf'): conn.send(d['wlbuf'], 0)                       # wl_buffer.destroy
            if d.get('dmabuf') is not None:
                try: d['map'].close(); os.close(d['dmabuf'])
                except OSError as ex: e.append(repr(ex))
            if d in allfbs: allfbs.remove(d)
            if d.get('fb') in rec['fbs']: rec['fbs'].remove(d['fb'])
            if d['handle'] in rec['handles']: rec['handles'].remove(d['handle'])
        write_json(STATE/'fbs.json', rec)
        if e: log('FREE errors', e)
    try:
        pct0, rw0, rh0 = resolution_config()
        S = {'fbs': alloc(rw0, rh0), 'w': rw0, 'h': rh0, 'pct': pct0, 'old': None, 'first': True, 'watch': None,
             'asleep': False, 'panel_off': False, 'bl': None, 'wakefds': []}
        wake = {'req': False}                  # set from the touch proxy thread (single assignment, no lock needed)
        shim = dr.Shim(reg); shim.device_fd_orig = shim.device_fd
        shim.device_fd = lambda p: (_ for _ in ()).throw(PermissionError(p)) if p.startswith('/dev/dri/') else shim.device_fd_orig(p)
        rot0 = rotation_config()[0]; shell0 = shell_config()
        if desktop_conf().get('touch_proxy', shell0 == 'phosh'):
            # phoc has no touch calibration setting: the compositor gets a uinput touchscreen with rotated positions,
            # the seat shim hands out only that device (the real NVT touchscreen is read here, not by the compositor)
            import touchproxy
            toff = touch_offset(rot0, shell0)
            tproxy = touchproxy.TouchProxy(matrix=touch_matrix(rot0, toff), log=log,
                                           on_input=lambda: wake.__setitem__('req', True)); log('TOUCH offset', toff)
            dr.INPUTS.pop(tproxy.src_path, None); dr.INPUTS[tproxy.path] = touchproxy.NAME
        env = {'PATH': '/usr/bin:/bin', 'HOME': '/home/siwal', 'USER': 'siwal', 'XDG_RUNTIME_DIR': str(RUN), 'LIBSEAT_BACKEND': 'seatd',
               'SEATD_SOCK': str(dr.SOCK), 'LANG': 'C.UTF-8', 'WLR_BACKENDS': 'headless,libinput', 'WLR_RENDERER': 'pixman',
               'WLR_LIBINPUT_NO_DEVICES': '1'}
        rot, transform, matrix = rotation_config(); shell = shell_config()
        # session D-Bus (no login session here); the compositor and its children share one process group
        if shell == 'phosh':
            env.update(session_env()); env.update({'XDG_CURRENT_DESKTOP': 'Phosh:GNOME', 'XDG_SESSION_DESKTOP': 'phosh', 'XDG_SESSION_TYPE': 'wayland'})
            cmd = ['dbus-run-session', '--', 'phoc', '-S', '-C', str(phoc_config(rw0, rh0)), '-E', str(HERE/'phosh-session.sh')]; out = None
        else:
            cfg = session_config(rot, matrix)
            cmd = ['dbus-run-session', '--', 'labwc', '-C', str(cfg)]; out = None
        if desktop_conf().get('labwc_debug'):                         # diagnostics: compositor debug log to ~/y700-agent/<shell>-<boot8>.log
            lp = AGENT/('%s-%s.log' % (shell, boot[:8])); out = open(lp, 'ab'); os.chown(lp, dr.UID, dr.GID); cmd.insert(3, '-d' if shell == 'labwc' else '-v')
        log('SHELL', shell)
        lab = subprocess.Popen(['runuser', '-u', 'siwal', '--', 'env'] + ['%s=%s' % kv for kv in env.items()] + cmd,
                               stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
        # the seat shim answers the compositor from its own thread: the main thread blocks in Wayland round trips
        # while the compositor may be waiting for an OPEN_DEVICE answer (deadlock seen 2026-09-20 00:36 with the
        # hot-plugged Kishi devices opened after the socket existed)
        import threading
        shim_stop = threading.Event()
        def shim_loop():
            while not shim_stop.is_set():
                try: shim.poll(0.2)
                except Exception as e: log('SEAT thread error', repr(e)); time.sleep(0.5)
        shim_thread = threading.Thread(target=shim_loop, name='seat-shim', daemon=True); shim_thread.start()
        t0 = time.monotonic(); socks = []
        while time.monotonic() - t0 < 15 and not socks and not STOP:
            time.sleep(0.1); socks = [f for f in os.listdir(RUN) if f.startswith('wayland-') and not f.endswith('.lock')]
        if not socks: raise RuntimeError('compositor socket did not appear')
        time.sleep(1.0)
        os.environ['XDG_RUNTIME_DIR'] = str(RUN); os.environ['WAYLAND_DISPLAY'] = socks[0]
        conn = wl.Conn(); reg_id, globs = wl.registry(conn)
        outs = wlcapture.Outputs(conn, reg_id, globs)
        mode = outs.set_custom('HEADLESS-1', rw0, rh0, 60000, rw0 / LOGICAL_W, transform=transform)
        log('LABWC', socks[0], 'mode', mode, 'rotation', rot, 'resolution', pct0, '%dx%d' % (rw0, rh0))
        # labwc: the libinput touch calibration from rc.xml only takes effect after a reconfigure (panel 2026-09-19: fresh
        # start -> touch unrotated, SIGHUP -> correct); reconfigure once the input devices are up
        time.sleep(1.0)
        if shell == 'labwc':
            lp = labwc_pid(lab.pid)
            if lp: os.kill(lp, signal.SIGHUP); log('LABWC reconfigure (touch calibration) pid', lp)
        cap = wlcapture.Capturer(conn, reg_id, globs)
        def zc_provider(w, h_, stride, fmt):
            """destination for the next screencopy: the back framebuffer itself, or None to let the capturer use its
            own buffer (then the loop copies as before)"""
            d = zc['target']
            if not zc['on'] or d is None or (w, h_) != (d['w'], d['h']) or stride != d['pitch'] \
                    or fmt != wlcapture.WL_SHM_FORMAT_XRGB8888 or d.get('dmabuf') is None:
                zc['used'] = False; return None
            if not d.get('wlbuf'):
                pool = conn.new_id(); conn.send(cap.shm, 0, struct.pack('<Ii', pool, d['size']), fds=[d['dmabuf']])
                b = conn.new_id(); conn.send(pool, 0, struct.pack('<Iiiiii', b, 0, w, h_, d['pitch'], fmt))
                conn.send(pool, 1); d['wlbuf'] = b
                log('ZEROCOPY wl_shm buffer', b, 'on fb', d['fb'], '%dx%d pitch %d' % (w, h_, d['pitch']))
            zc['used'] = True; return d['wlbuf'], d['map']
        if zc['on']: cap.provider = zc_provider
        state = {'rot': rot}
        T2DEG = {0: 0, 1: 90, 2: 180, 3: 270}
        head = [h for h in outs.heads.values() if h.get('name') == 'HEADLESS-1'][0]
        def follow_rotation(deg):
            """the output rotation changed (our ctl or the shell's own rotate button): touch + saved default follow"""
            if tproxy: tproxy.set_matrix(touch_matrix(deg, touch_offset(deg, shell)))
            state['rot'] = deg; write_json(HERE/'desktop.json', dict(desktop_conf(), rotation=deg)); os.chown(HERE/'desktop.json', dr.UID, dr.GID)
        def switch_resolution(pct, reason='ctl'):
            """new framebuffers at pct; TEST_ONLY first, then the compositor output mode; the old FBs go after the first
            commit on the new ones; underruns within 6 s -> automatic return to the previous resolution"""
            if pct == S['pct'] or S['old']: return 'ok %d' % S['pct'] if pct == S['pct'] else 'error switch in progress'
            nw, nh = RESOLUTIONS[pct]; new = alloc(nw, nh)
            try:
                props = planes_for(fd, reg, new[0]['fb'], nw, nh); k.atomic(fd, k.ATOMIC_TEST_ONLY, list(props), props)
            except OSError as e:
                free(new); log('RESOLUTION', pct, 'rejected by TEST_ONLY', repr(e)); return 'error display rejected %d%%' % pct
            res = outs.set_custom('HEADLESS-1', nw, nh, 60000, nw / LOGICAL_W, transform=ROTATIONS[state['rot']][0])
            if res != 'succeeded': free(new); return 'error output %s' % res
            prev = S['pct']; S.update(old=S['fbs'], fbs=new, w=nw, h=nh, pct=pct, first=True,
                                      watch=(time.monotonic() + 6, rg.underruns(c, reg['encoder_status']), prev, reason))
            log('RESOLUTION', prev, '->', pct, '%dx%d' % (nw, nh), 'scale %.3f' % (nw / LOGICAL_W), reason); return 'ok %d' % pct
        def wake_sources():
            """Devices that still deliver input while the panel is off. The touchscreen is not one of them: the NVT
            driver subscribes to the panel notifier (supplier link to 9800000.qcom,mdss_mdp) and suspends with the
            display, IRQ included - so the volume key and any external pad/keyboard are what can wake the screen."""
            out = []
            for d in sorted(Path('/sys/class/input').glob('event*')):
                node = Path('/dev/input')/d.name
                if not node.exists(): continue
                try: name = (d/'device/name').read_text().strip()
                except OSError: continue
                if name == 'gpio-keys' or node in getattr(inodes, 'made', {}):
                    try: out.append((name, os.open(str(node), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)))
                    except OSError as e: log('SLEEP cannot watch', node, repr(e))
            return out

        def current_refresh():
            cp = k.properties(fd, reg['topology']['crtc'], k.OBJECT_CRTC)
            return k.mode_fields(k.read_mode(fd, cp['MODE_ID'][1]))['vrefresh']

        def set_refresh(hz):
            """Panel refresh rate. The compositor output runs at 60 Hz, so scanning out at 120 costs DPU and DSI power
            for frames nobody produced; 120 (or more) is still one command away for when it is wanted. The modeset is
            tested first, then committed with the planes, and reverted if the encoder starts reporting underruns."""
            modes = [(f, raw) for f, raw in connector_modes(fd, reg['topology']['connector'])
                     if (f['hdisplay'], f['vdisplay']) == (W, H)]
            if not modes: return 'error no modes on the connector'
            have = sorted({f['vrefresh'] for f, _ in modes})
            pick = [(f, raw) for f, raw in modes if f['vrefresh'] == hz]
            if not pick: return 'error %d Hz not offered (have %s)' % (hz, have)
            before = current_refresh()
            if before == hz: return 'ok %d' % hz
            cp = k.properties(fd, reg['topology']['crtc'], k.OBJECT_CRTC)
            old_blob = cp['MODE_ID'][1]
            u0 = rg.underruns(c, reg['encoder_status'])
            def commit(raw):
                blob = k.create_blob(fd, raw)
                d = S['fbs'][0]
                props = {reg['topology']['crtc']: [(cp['MODE_ID'][0], blob), (cp['ACTIVE'][0], 1)]}
                props.update(planes_for(fd, reg, d['fb'], S['w'], S['h']))
                objs = list(props)
                k.atomic(fd, k.ATOMIC_TEST_ONLY | k.ATOMIC_ALLOW_MODESET, objs, props)
                k.atomic(fd, k.ATOMIC_ALLOW_MODESET, objs, props)
            try: commit(pick[0][1])
            except OSError as e: log('REFRESH %d Hz refused' % hz, repr(e)); return 'error modeset refused %r' % (e,)
            time.sleep(4)
            u1 = rg.underruns(c, reg['encoder_status'])
            if any(u1[i] > u0[i] for i in u1):
                log('REFRESH %d Hz caused underruns %s -> %s, going back to %d' % (hz, u0, u1, before))
                try:
                    back_raw = [raw for f, raw in modes if f['vrefresh'] == before]
                    commit(back_raw[0] if back_raw else k.read_mode(fd, old_blob))
                except OSError as e: log('REFRESH revert failed', repr(e))
                return 'error %d Hz underruns, reverted' % hz
            S['first'] = True                                        # repaint with a TEST commit on the new mode
            log('REFRESH %d -> %d Hz (underruns %s)' % (before, hz, u1))
            return 'ok %d' % hz

        def set_sleep(on):
            """Display sleep. The backlight is the obvious part (measured: 4.1 W -> 3.0 W at this brightness), but the
            panel keeps scanning out at 120 Hz, so with sleep_display_off the planes are detached and the CRTC is
            deactivated as well. Any input through the touch proxy wakes it; a bring-up stage would refuse to run while
            the panel is off (its display guard checks the planes), which is fine after boot."""
            if on == S['asleep']: return 'ok %s' % ('asleep' if on else 'awake')
            if on:
                S['bl'] = int(c.read(BACKLIGHT/'brightness')); (BACKLIGHT/'brightness').write_text('0')
                S['asleep'] = True; S['panel_off'] = False
                if desktop_conf().get('sleep_display_off', True):
                    props = blank_props(fd, reg, False); objs = list(props)
                    try:
                        k.atomic(fd, k.ATOMIC_TEST_ONLY | k.ATOMIC_ALLOW_MODESET, objs, props)
                        k.atomic(fd, k.ATOMIC_ALLOW_MODESET, objs, props); S['panel_off'] = True
                    except OSError as e: log('SLEEP panel off refused, backlight only:', repr(e))
                S['wakefds'] = wake_sources()
                log('SLEEP on panel_off=%s bl=%s wake on %s' % (S['panel_off'], S['bl'], [n for n, _ in S['wakefds']] or 'ctl only'))
            else:
                if S['panel_off']:
                    d = S['fbs'][0]
                    props = blank_props(fd, reg, True); props.update(planes_for(fd, reg, d['fb'], S['w'], S['h']))
                    objs = list(props)
                    k.atomic(fd, k.ATOMIC_TEST_ONLY | k.ATOMIC_ALLOW_MODESET, objs, props)
                    k.atomic(fd, k.ATOMIC_ALLOW_MODESET, objs, props); S['panel_off'] = False
                if S['bl']: (BACKLIGHT/'brightness').write_text('%d' % S['bl'])
                for _, f in S.get('wakefds', []):
                    try: os.close(f)
                    except OSError: pass
                S['wakefds'] = []
                S['asleep'] = False; S['first'] = True            # next capture is a full frame, with a TEST commit
                log('SLEEP off')
            return 'ok %s' % ('asleep' if on else 'awake')

        def on_ctl(req):
            bl_max = int(c.read(BACKLIGHT/'max_brightness')); cur = int(c.read(BACKLIGHT/'brightness'))
            act, arg = parse_ctl(req, cur, bl_max, state['rot'])
            if act == 'brightness':
                (BACKLIGHT/'brightness').write_text('%d' % arg); log('CTL brightness', cur, '->', arg); return 'ok %d' % round(arg * 100 / bl_max)
            if act == 'get' and arg == 'resolution': return 'ok %d' % S['pct']
            if act == 'get' and arg == 'sleep': return 'ok %s' % ('asleep' if S['asleep'] else 'awake')
            if act == 'get' and arg == 'refresh':
                try: return 'ok %d' % current_refresh()
                except OSError as e: return 'error %r' % (e,)
            if act == 'refresh':
                if S['asleep']: return 'error asleep'
                try: return set_refresh(arg)
                except OSError as e: log('REFRESH error', repr(e)); return 'error %r' % (e,)
            if act == 'sleep':
                try: return set_sleep(arg)
                except OSError as e: log('SLEEP error', repr(e)); return 'error %r' % (e,)
            if act == 'resolution': return switch_resolution(arg)
            if act == 'touch':
                if not tproxy: return 'error no touch proxy in this shell'
                conf = desktop_conf(); offs = dict(conf.get('touch_offsets', {})); offs[str(state['rot'])] = arg
                write_json(HERE/'desktop.json', dict(conf, touch_offsets=offs)); os.chown(HERE/'desktop.json', dr.UID, dr.GID)
                tproxy.set_matrix(touch_matrix(state['rot'], arg)); log('CTL touch offset rotation', state['rot'], '->', arg); return 'ok %d' % arg
            if act == 'rotate':
                t2, m2 = ROTATIONS[arg]; res = outs.set_custom('HEADLESS-1', S['w'], S['h'], 60000, S['w'] / LOGICAL_W, transform=t2)
                if res != 'succeeded': return 'error output %s' % res
                lp = None
                if shell == 'labwc':
                    session_config(arg, m2); lp = labwc_pid(lab.pid)
                    if lp: os.kill(lp, signal.SIGHUP)                 # labwc re-reads rc.xml: touch matrix follows
                follow_rotation(arg); log('CTL rotate ->', arg, 'labwc', lp); return 'ok %d' % arg
            return '%s %s' % (act, arg)
        import padproxy; pads = padproxy.PadProxies(log=log); pads.sync()
        ctl = Control(); inodes = InputNodes(); inodes.sync(); t_in = time.monotonic()
        back = 0; n = n0 = 0; copy_ms = 0.0; u_base = rg.underruns(c, reg['encoder_status']); t_stat = time.monotonic()
        hz = desktop_conf().get('panel_hz')
        if hz:
            # the panel defaults to its highest mode; at 60 Hz it costs 0.56 W less and shows exactly the same frames
            # (the compositor output is 60 Hz and the presenter tops out around 53 fps). y700-ctl refresh <hz> changes
            # it live - 120 and 165 are one command away.
            try: log('PANEL_HZ', set_refresh(int(hz)))
            except OSError as e: log('PANEL_HZ %s failed' % hz, repr(e))

        while not STOP:
            if lab.poll() is not None: raise RuntimeError('compositor exited rc=%s' % lab.returncode)
            ctl.poll(on_ctl)
            if time.monotonic() - t_in >= 1.0: pads.sync(); inodes.sync(); t_in = time.monotonic()
            deg = T2DEG.get(head.get('transform'))
            if deg is not None and deg != state['rot']: follow_rotation(deg); log('ROTATION by the shell ->', deg)
            if wake['req']:
                wake['req'] = False
                if S['asleep']:
                    try: set_sleep(False)
                    except OSError as e: log('WAKE failed', repr(e))
            if S['asleep']:
                fds = [f for _, f in S.get('wakefds', [])]
                ready = select.select(fds, [], [], 0.2)[0] if fds else (time.sleep(0.2) or [])
                for f in ready:
                    try: os.read(f, 4096)                                    # drain; any event means "wake up"
                    except OSError: pass
                if ready:
                    try: set_sleep(False)
                    except OSError as e: log('WAKE failed', repr(e))
                continue                                                     # no capture, no flips while asleep
            zc['target'] = S['fbs'][back]                                    # where the compositor should paint next
            try: buf, info = cap.capture(damage=not S['first'], timeout=0.25)
            except TimeoutError: buf = None
            if buf is not None and (info['w'], info['h']) == (S['w'], S['h']):   # frames of a previous mode are skipped
                rw, rh = S['w'], S['h']; d = S['fbs'][back]; st, pitch = info['stride'], d['pitch']; tc = time.perf_counter()
                if zc['used']:
                    dma_flush(d['dmabuf'])                                   # cached heap memory: flush before scanout
                else:
                    dst = memoryview(d['map']); rowb = rw * 4
                    for y in range(rh): dst[y * pitch:y * pitch + rowb] = buf[y * st:y * st + rowb]
                copy_ms += (time.perf_counter() - tc) * 1e3
                props = planes_for(fd, reg, d['fb'], rw, rh); objs = list(props)
                if S['first']: k.atomic(fd, k.ATOMIC_TEST_ONLY, objs, props)
                k.atomic(fd, 0, objs, props); S['first'] = False; back ^= 1; n += 1
                if S['old']: free(S['old']); S['old'] = None                  # blocking commit: the old FBs are off the planes
            if S['watch'] and time.monotonic() >= S['watch'][0] and not S['old']:
                _, u_before, prev, reason = S['watch']; S['watch'] = None; u_now = rg.underruns(c, reg['encoder_status'])
                if any(u_now[i] > u_before[i] for i in u_now):
                    log('WARNING underruns after resolution', S['pct'], u_before, '->', u_now, '- returning to', prev)
                    if reason != 'revert': switch_resolution(prev, 'revert')
                else:
                    write_json(HERE/'desktop.json', dict(desktop_conf(), resolution=S['pct'])); os.chown(HERE/'desktop.json', dr.UID, dr.GID)
                    log('RESOLUTION', S['pct'], 'kept (no underruns in 6 s)')
            if time.monotonic() - t_stat >= STATUS_EVERY_S:
                el = time.monotonic() - t_stat; u = rg.underruns(c, reg['encoder_status'])
                lines = c.dmesg().splitlines(); new = [l for l in lines if l not in dm_seen]; dm_seen.update(new)
                faults = [l for l in new if bg.FAULT.search(l)]
                status = {'t': time.time(), 'frames': n, 'fps': round((n - n0) / el, 1), 'zerocopy': bool(zc['on'] and zc['used']),
                          'copy_ms_avg': round(copy_ms / max(n - n0, 1), 2),
                          'underruns_total': {i: u[i] - u_base[i] for i in u}, 'new_faults': faults[:10]}
                write_json(STATE/'status.json', status); sp = AGENT/('desktop-status-%s.json' % boot[:8]); write_json(sp, status); os.chown(sp, 1000, 1000)
                log('STATUS', json.dumps(status) if not faults else 'WARNING kernel fault lines: ' + json.dumps(status))
                if any(status['underruns_total'].values()): log('WARNING display underruns since start', status['underruns_total'])
                t_stat = time.monotonic(); n0 = n; copy_ms = 0.0
        log('STOP signal', STOP[0])
    except Exception as e:
        rc = 1; log('ERROR', repr(e))
    finally:
        if lab:
            for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):   # whole group: runuser, dbus-run-session, labwc, bar...
                try: os.killpg(lab.pid, sig)
                except ProcessLookupError: break
                try: lab.wait(wait)
                except subprocess.TimeoutExpired: continue
                t_end = time.monotonic() + wait
                while time.monotonic() < t_end:
                    try: os.killpg(lab.pid, 0); time.sleep(0.2)
                    except ProcessLookupError: break
                else: continue
                break
        if shim:
            try: shim_stop.set(); shim_thread.join(2)
            except NameError: pass
            shim.close()
        if ctl: ctl.close()
        try: pads.close()
        except NameError: pass
        try: inodes.close()
        except NameError: pass
        if tproxy: tproxy.close(); log('TOUCH proxy closed after', tproxy.frames, 'frames')
        restored = False
        try: commit_native(fd, reg); restored = True; log('RESTORED native layout')
        except Exception as e: rc = 1; log('RESTORE FAILED', repr(e))
        if restored:                                                  # FBs may only go once they are off the screen
            remove_fbs(fd, [d['fb'] for d in allfbs if 'fb' in d], [d['handle'] for d in allfbs], errs)
            if errs: rc = 1; log('CLEANUP errors', errs)
            else: (STATE/'fbs.json').unlink(missing_ok=True)
        os.close(fd)
        now = c.read(rg.DEBUG/'state'); ok = now == native or now in rg.rebased_states(c, reg, native)
        log('EXIT state_native=%s rc=%d' % (ok, rc))
    return rc

if __name__ == '__main__':
    try: sys.exit(main())
    except Precondition as e: log('PRECONDITION:', e); sys.exit(EXIT_PRECONDITION)
    except Exception as e: log('STOP:', repr(e)); sys.exit(1)
