"""Gamepads without a good SDL/Steam mapping re-emitted as a virtual Xbox 360 pad (what Steam, SDL and most games map out
of the box). The root desktop service grabs the real pad (EVIOCGRAB on a private root-only node /dev/y700-pad-eventN, so nothing else sees it; its
/dev/input and /dev/hidraw nodes are not created either) and writes the translated events to a uinput device
'Microsoft X-Box 360 pad' (USB 045e:028e, the xpad driver's layout).

Razer Kishi V3 Pro/Ultra (1532:0724, hid-generic), recorded 2026-09-20: A/B/X/Y = BTN_SOUTH/EAST/NORTH/WEST (304/305/307/308),
LB/RB = 310/311, digital LT/RT = 312/313 (dropped: the analog BRAKE/GAS carry the triggers), View/Menu/Home = 314/315/316,
L3/R3 = 317/318; left stick ABS_X/Y, right stick ABS_Z/RZ (+-32767), LT = ABS_BRAKE, RT = ABS_GAS (0..255), D-pad ABS_HAT0X/Y."""
import fcntl, os, select, stat, struct, threading, time
from pathlib import Path
from touchproxy import EV_SYN, EV_KEY, EV_ABS, SYN_REPORT, SYN_DROPPED, EVENT, ABSINFO, _ioc, \
    UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_DEV_SETUP, UI_ABS_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY, UI_GET_SYSNAME

EVIOCGRAB = _ioc(1, 'E', 0x90, 4)
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_GAS, ABS_BRAKE, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 9, 10, 16, 17
BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = \
    0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x13a, 0x13b, 0x13c, 0x13d, 0x13e
XBOX_NAME, XBOX_ID = 'Microsoft X-Box 360 pad', (0x03, 0x045e, 0x028e, 0x0110)      # BUS_USB, vendor, product, version
XBOX_KEYS = (BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR)
XBOX_ABS = {ABS_X: (-32768, 32767, 16, 128), ABS_Y: (-32768, 32767, 16, 128), ABS_RX: (-32768, 32767, 16, 128),
            ABS_RY: (-32768, 32767, 16, 128), ABS_Z: (0, 255, 0, 0), ABS_RZ: (0, 255, 0, 0), ABS_HAT0X: (-1, 1, 0, 0), ABS_HAT0Y: (-1, 1, 0, 0)}

KISHI = {'keys': {0x130: BTN_A, 0x131: BTN_B, 0x133: BTN_X, 0x134: BTN_Y, 0x136: BTN_TL, 0x137: BTN_TR, 0x13a: BTN_SELECT,
                  0x13b: BTN_START, 0x13c: BTN_MODE, 0x13d: BTN_THUMBL, 0x13e: BTN_THUMBR},
         'abs': {ABS_X: ABS_X, ABS_Y: ABS_Y, ABS_Z: ABS_RX, ABS_RZ: ABS_RY, ABS_BRAKE: ABS_Z, ABS_GAS: ABS_RZ,
                 ABS_HAT0X: ABS_HAT0X, ABS_HAT0Y: ABS_HAT0Y}}
# (USB vendor, product) -> map; all interfaces of such a device are hidden. Empty = pads are used natively.
# 2026-09-20: {(0x1532, 0x0724): KISHI} worked (virtual Xbox 360 pad) but did not fix the Big Picture D-pad either -> off
REMAPS = {}

class PadMapper:
    """Pure event translator (unit-tested): feed(type, code, value) -> list of events for the virtual pad."""
    def __init__(self, m): self.keys, self.abs = m['keys'], m['abs']
    def feed(self, t, code, value):
        if t == EV_KEY and code in self.keys: return [(EV_KEY, self.keys[code], value)]
        if t == EV_ABS and code in self.abs:
            v = self.abs[code]; lo, hi = XBOX_ABS[v][:2]
            return [(EV_ABS, v, max(lo, min(hi, value)))]
        if t == EV_SYN and code == SYN_REPORT: return [(EV_SYN, SYN_REPORT, 0)]
        return []

def usb_id(sysdev):
    """(vendor, product) of an input device from /sys/class/input/eventN/device/id, or None."""
    try: return int((sysdev/'id/vendor').read_text(), 16), int((sysdev/'id/product').read_text(), 16)
    except (OSError, ValueError): return None

def hid_usb_id(hid_id):
    """HID_ID=0003:00001532:00000724 -> (0x1532, 0x0724)"""
    try: _, v, p = hid_id.split(':'); return int(v, 16), int(p, 16)
    except ValueError: return None

def remap_sources(sysroot='/sys/class/input'):
    """{eventN: (major, minor, map)} for the gamepad interface (has BTN_SOUTH) of devices listed in REMAPS."""
    out = {}
    for d in sorted(Path(sysroot).glob('event*')):
        m = REMAPS.get(usb_id(d/'device'))
        if not m: continue
        try:
            words = (d/'device/capabilities/key').read_text().split()
            bits = int(''.join('%016x' % int(w, 16) for w in words), 16)
            if not (bits >> 0x130) & 1: continue
            ma, mi = map(int, (d/'dev').read_text().split(':')); out[d.name] = (ma, mi, m)
        except (OSError, ValueError): continue
    return out

class PadProxy:
    def __init__(self, ev, ma, mi, m, node_dir='/dev', log=print):
        # private root-only node for the grab: /run (state dir) is mounted nodev, device nodes only open under /dev
        self.log = log; self.ev = ev; self.node = Path(node_dir)/('y700-pad-%s' % ev)
        self.node.unlink(missing_ok=True); os.mknod(self.node, 0o600 | stat.S_IFCHR, os.makedev(ma, mi))
        self.src = self.ui = None
        try: self._open(m)
        except BaseException:
            for fd in (self.ui, self.src):
                if fd is not None: os.close(fd)                     # closing the uinput fd also destroys a created device
            self.node.unlink(missing_ok=True); raise
        self.alive = True; self.stop = threading.Event(); self.events = 0
        self.thread = threading.Thread(target=self._run, name='padproxy-' + ev, daemon=True); self.thread.start()
        log('PAD proxy', ev, '->', XBOX_NAME, self.sysname)
    def _open(self, m):
        self.src = os.open(self.node, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        fcntl.ioctl(self.src, EVIOCGRAB, 1)
        self.map = PadMapper(m)
        ui = Path('/dev/uinput')
        if not ui.exists():
            a, b = map(int, Path('/sys/class/misc/uinput/dev').read_text().split(':')); os.mknod(ui, 0o600 | stat.S_IFCHR, os.makedev(a, b))
        self.ui = os.open(ui, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        for e in (EV_KEY, EV_ABS): fcntl.ioctl(self.ui, UI_SET_EVBIT, e)
        for k in XBOX_KEYS: fcntl.ioctl(self.ui, UI_SET_KEYBIT, k)
        for a, (lo, hi, fuzz, flat) in XBOX_ABS.items():
            fcntl.ioctl(self.ui, UI_SET_ABSBIT, a); fcntl.ioctl(self.ui, UI_ABS_SETUP, struct.pack('<Hxx', a) + ABSINFO.pack(0, lo, hi, fuzz, flat, 0))
        fcntl.ioctl(self.ui, UI_DEV_SETUP, struct.pack('<HHHH80sI', *XBOX_ID, XBOX_NAME.encode(), 0))
        fcntl.ioctl(self.ui, UI_DEV_CREATE)
        buf = bytearray(64); fcntl.ioctl(self.ui, UI_GET_SYSNAME(64), buf); self.sysname = bytes(buf).split(b'\0')[0].decode()
    def _run(self):
        while not self.stop.is_set():
            if not select.select([self.src], [], [], 0.5)[0]: continue
            try: data = os.read(self.src, EVENT.size * 64)
            except BlockingIOError: continue
            except OSError as e: self.log('PAD proxy', self.ev, 'gone', repr(e)); self.alive = False; return
            out = []
            for i in range(0, len(data) - len(data) % EVENT.size, EVENT.size):
                _, _, t, code, value = EVENT.unpack_from(data, i); out += self.map.feed(t, code, value)
            if out:
                try: os.write(self.ui, b''.join(EVENT.pack(0, 0, t, code, value) for t, code, value in out)); self.events += len(out)
                except OSError as e: self.log('PAD proxy write error', repr(e))
    def close(self):
        self.stop.set(); self.thread.join(2)
        try: fcntl.ioctl(self.ui, UI_DEV_DESTROY)
        except OSError: pass
        os.close(self.ui); os.close(self.src); self.node.unlink(missing_ok=True)
        self.log('PAD proxy closed', self.ev, 'events', self.events)

class PadProxies:
    """Keeps one PadProxy per connected remapped pad (called every second from the service loop)."""
    def __init__(self, node_dir='/dev', log=print): self.node_dir = node_dir; self.log = log; self.px = {}; self.failed = {}
    def sync(self):
        cur = remap_sources()
        for ev in [e for e, p in self.px.items() if e not in cur or not p.alive or cur[e][:2] != p.dev]:
            self.px.pop(ev).close()
        for ev, (ma, mi, m) in cur.items():
            if ev in self.px or time.monotonic() < self.failed.get((ev, ma, mi), 0): continue
            try: p = PadProxy(ev, ma, mi, m, self.node_dir, self.log); p.dev = (ma, mi); self.px[ev] = p
            except OSError as e: self.log('PAD proxy error (retry in 30 s)', ev, repr(e)); self.failed[(ev, ma, mi)] = time.monotonic() + 30
    def close(self):
        for ev in list(self.px): self.px.pop(ev).close()
