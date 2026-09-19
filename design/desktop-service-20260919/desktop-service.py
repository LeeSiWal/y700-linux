#!/usr/bin/env python3
"""y700-desktop.service main process (root). Long-running form of the r5..r8 presenter (desktop-20260919/presenter-run.py):
labwc (siwal) on a HEADLESS output, shown on the panel by a two-plane presenter on a BORROWED duplicate of the display
owner's DRM file (pidfd_getfd; no new DRM master, the owner keeps its fd). See DESIGN.md.
Exit codes: 0 stopped by SIGTERM/SIGINT (native layout restored); 1 error (systemd restarts); 3 precondition not met
(bring-up not finished, unknown screen state) -> no restart."""
import collections, ctypes, fcntl, importlib.util, json, os, signal, stat, struct, subprocess, sys, time
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
def bringup_ready(doc, boot_id, need=('owner', 'gpu')):
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

# render resolution (percent of the panel) -> framebuffer size; the logical desktop stays 952x1520 (output scale = w/952)
RESOLUTIONS = {100: (1904, 3040), 90: (1712, 2736), 80: (1524, 2432), 67: (1276, 2036), 50: (952, 1520)}
LOGICAL_W = 952

def resolution_config():
    pct = int(desktop_conf().get('resolution', 100))
    return (pct,) + RESOLUTIONS[pct] if pct in RESOLUTIONS else (100,) + RESOLUTIONS[100]

GEM_CLOSE = (1 << 30) | (8 << 16) | (ord('d') << 8) | 0x09    # DRM_IOW(0x09, struct drm_gem_close {u32 handle, pad})

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

def commit_native(fd, reg):
    props = planes_for(fd, reg, reg['native_fb']); objs = list(props)
    k.atomic(fd, k.ATOMIC_TEST_ONLY, objs, props); k.atomic(fd, 0, objs, props)

def remove_fbs(fd, fbs, handles, errors):
    for f in fbs:
        try: fcntl.ioctl(fd, RMFB, struct.pack('<I', f))
        except OSError as e: errors.append('RMFB %d: %r' % (f, e))
    for h in handles:
        try: fcntl.ioctl(fd, DESTROY_DUMB, struct.pack('<I', h))
        except OSError as e: errors.append('DESTROY_DUMB %d: %r' % (h, e))

def wait_bringup(boot):
    p = AGENT/('bringup-%s.json' % boot[:8]); t0 = time.monotonic(); said = False
    while True:
        try: doc = json.loads(p.read_text())
        except (OSError, ValueError): doc = None
        if bringup_ready(doc, boot): return
        if time.monotonic() - t0 > BRINGUP_WAIT_S: raise Precondition('bring-up owner+gpu not ok after %d s (%s)' % (BRINGUP_WAIT_S, p))
        if not said: log('WAIT bring-up owner+gpu (%s)' % p.name); said = True
        time.sleep(5)

def prepare_gpu_node():
    p = Path('/dev/kgsl-3d0'); st = os.stat(p)
    if not stat.S_ISCHR(st.st_mode): raise Precondition('/dev/kgsl-3d0 is not a character device')
    if stat.S_IMODE(st.st_mode) != 0o666: os.chmod(p, 0o666); log('GPU /dev/kgsl-3d0 -> 0666')

def recover(reg, boot):
    """Bring the screen back to the native layout if a previous instance of this service left its FBs on it."""
    recp = STATE/'fbs.json'; rec = json.loads(recp.read_text()) if recp.exists() else None
    fd = bo.borrow_fd(c, reg)
    try:
        t = reg['topology']; plane_fbs = [k.get_plane(fd, t['left'])['fb_id'], k.get_plane(fd, t['right'])['fb_id']]
        act, arg = recovery_action(rec, boot, reg['owner'], plane_fbs, reg['native_fb'], set(drmabi.fb_ids(fd)))
        log('RECOVERY', act, arg, 'planes', plane_fbs)
        if act == 'refuse': raise Precondition('screen state not ours: ' + arg)
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
    wait_bringup(boot)
    reg, _ = rg.load(c, boot)
    prepare_gpu_node()
    recover(reg, boot)
    native = c.read(reg['native_state_file']); now = c.read(rg.DEBUG/'state')
    if not (now == native or now in rg.rebased_states(c, reg, native)): raise Precondition('display not in the native state')
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
    def alloc(rw, rh):
        """two rw x rh XR24 framebuffers, recorded in fbs.json before they can reach the screen"""
        out = []
        for _ in range(2):
            d = k.dumb_fb(fd, rw, rh); d['w'], d['h'] = rw, rh; out.append(d); allfbs.append(d)
            rec['handles'].append(d['handle']); write_json(STATE/'fbs.json', rec)
            d['fb'], extra = add_fb_noleak(fd, rw, rh, d['handle'], d['pitch']); rec['fbs'].append(d['fb']); write_json(STATE/'fbs.json', rec)
            ctypes.memset(d['addr'], 0, d['size'])
        log('FBS', [(d['fb'], d['handle'], '%dx%d' % (rw, rh)) for d in out]); return out
    def free(ds):
        """only for framebuffers that are no longer on a plane"""
        e = []; remove_fbs(fd, [d['fb'] for d in ds if 'fb' in d], [d['handle'] for d in ds], e)
        for d in ds:
            if d in allfbs: allfbs.remove(d)
            if d.get('fb') in rec['fbs']: rec['fbs'].remove(d['fb'])
            if d['handle'] in rec['handles']: rec['handles'].remove(d['handle'])
        write_json(STATE/'fbs.json', rec)
        if e: log('FREE errors', e)
    try:
        pct0, rw0, rh0 = resolution_config()
        S = {'fbs': alloc(rw0, rh0), 'w': rw0, 'h': rh0, 'pct': pct0, 'old': None, 'first': True, 'watch': None}
        shim = dr.Shim(reg); shim.device_fd_orig = shim.device_fd
        shim.device_fd = lambda p: (_ for _ in ()).throw(PermissionError(p)) if p.startswith('/dev/dri/') else shim.device_fd_orig(p)
        rot0 = rotation_config()[0]; shell0 = shell_config()
        if desktop_conf().get('touch_proxy', shell0 == 'phosh'):
            # phoc has no touch calibration setting: the compositor gets a uinput touchscreen with rotated positions,
            # the seat shim hands out only that device (the real NVT touchscreen is read here, not by the compositor)
            import touchproxy
            toff = touch_offset(rot0, shell0)
            tproxy = touchproxy.TouchProxy(matrix=touch_matrix(rot0, toff), log=log); log('TOUCH offset', toff)
            dr.INPUTS.pop('/dev/input/event1', None); dr.INPUTS[tproxy.path] = touchproxy.NAME
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
        def on_ctl(req):
            bl_max = int(c.read(BACKLIGHT/'max_brightness')); cur = int(c.read(BACKLIGHT/'brightness'))
            act, arg = parse_ctl(req, cur, bl_max, state['rot'])
            if act == 'brightness':
                (BACKLIGHT/'brightness').write_text('%d' % arg); log('CTL brightness', cur, '->', arg); return 'ok %d' % round(arg * 100 / bl_max)
            if act == 'get' and arg == 'resolution': return 'ok %d' % S['pct']
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
        while not STOP:
            if lab.poll() is not None: raise RuntimeError('compositor exited rc=%s' % lab.returncode)
            ctl.poll(on_ctl)
            if time.monotonic() - t_in >= 1.0: pads.sync(); inodes.sync(); t_in = time.monotonic()
            deg = T2DEG.get(head.get('transform'))
            if deg is not None and deg != state['rot']: follow_rotation(deg); log('ROTATION by the shell ->', deg)
            try: buf, info = cap.capture(damage=not S['first'], timeout=0.25)
            except TimeoutError: buf = None
            if buf is not None and (info['w'], info['h']) == (S['w'], S['h']):   # frames of a previous mode are skipped
                rw, rh = S['w'], S['h']; d = S['fbs'][back]; st, pitch = info['stride'], d['pitch']; tc = time.perf_counter()
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
                status = {'t': time.time(), 'frames': n, 'fps': round((n - n0) / el, 1), 'copy_ms_avg': round(copy_ms / max(n - n0, 1), 2),
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
