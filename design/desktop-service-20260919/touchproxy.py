"""Rotated touchscreen for compositors without a touch calibration setting (phoc): the root desktop service reads the real
NVT touchscreen (multitouch protocol B only: ABS_MT_SLOT/TOUCH_MAJOR/POSITION_X/POSITION_Y/TRACKING_ID/PRESSURE +
BTN_TOUCH) and re-emits it through a uinput device 'y700-rotated-touch' with the positions rotated by the same normalized
matrix as the libinput calibration verified under labwc (270 -> '0 -1 1 1 0 0'). The compositor is given only the uinput
device (the seat shim denies the real one). Rotation changes take effect on the next frame."""
import fcntl, os, select, stat, struct, threading
from pathlib import Path

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_DROPPED = 0, 3
BTN_TOUCH = 0x14a
ABS_MT_SLOT, ABS_MT_TOUCH_MAJOR, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID, ABS_MT_PRESSURE = 0x2f, 0x30, 0x35, 0x36, 0x39, 0x3a
ABS_CODES = (ABS_MT_SLOT, ABS_MT_TOUCH_MAJOR, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID, ABS_MT_PRESSURE)
INPUT_PROP_DIRECT = 1
EVENT = struct.Struct('<qqHHi')                     # struct input_event on aarch64: timeval (2 x 64 bit), type, code, value
ABSINFO = struct.Struct('<6i')                      # value minimum maximum fuzz flat resolution

def _ioc(d, t, nr, size): return (d << 30) | (size << 16) | (ord(t) << 8) | nr
EVIOCGABS = lambda a: _ioc(2, 'E', 0x40 + a, ABSINFO.size)
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_SET_PROPBIT = (_ioc(1, 'U', n, 4) for n in (100, 101, 103, 110))
UI_DEV_SETUP = _ioc(1, 'U', 3, 92)                  # struct uinput_setup: input_id(8) name[80] ff_effects_max(4)
UI_ABS_SETUP = _ioc(1, 'U', 4, 28)                  # struct uinput_abs_setup: code(2) pad(2) input_absinfo(24)
UI_DEV_CREATE, UI_DEV_DESTROY = _ioc(0, 'U', 1, 0), _ioc(0, 'U', 2, 0)
UI_GET_SYSNAME = lambda n: _ioc(2, 'U', 44, n)
NAME = 'y700-rotated-touch'

def parse_matrix(m): return tuple(float(x) for x in m.split())

class Rotator:
    """Pure event transformer (unit-tested). feed(type, code, value) -> list of (type, code, value) for the virtual device."""
    def __init__(self, xr, yr, matrix):
        self.xr, self.yr = xr, yr; self.m = parse_matrix(matrix)       # xr/yr = (min, max) of the real (and virtual) axes
        self.cur = 0; self.vslot = 0; self.pos = {}; self.dirty = []
    def set_matrix(self, matrix): self.m = parse_matrix(matrix)
    def _slot(self, out):
        if self.vslot != self.cur: out.append((EV_ABS, ABS_MT_SLOT, self.cur)); self.vslot = self.cur
    def _rot(self, x, y):
        (x0, x1), (y0, y1) = self.xr, self.yr; a, b, c, d, e, f = self.m
        u = (x - x0) / (x1 - x0); v = (y - y0) / (y1 - y0)
        nu = min(1.0, max(0.0, a * u + b * v + c)); nv = min(1.0, max(0.0, d * u + e * v + f))
        return round(x0 + nu * (x1 - x0)), round(y0 + nv * (y1 - y0))
    def feed(self, t, code, value):
        out = []
        if t == EV_ABS:
            if code == ABS_MT_SLOT: self.cur = value
            elif code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y):
                p = self.pos.setdefault(self.cur, [(self.xr[0] + self.xr[1]) // 2, (self.yr[0] + self.yr[1]) // 2])
                p[0 if code == ABS_MT_POSITION_X else 1] = value
                if self.cur not in self.dirty: self.dirty.append(self.cur)
            elif code in ABS_CODES: self._slot(out); out.append((t, code, value))
        elif t == EV_KEY:
            if code == BTN_TOUCH: out.append((t, code, value))                         # gesture keys (power/wakeup) dropped
        elif t == EV_SYN:
            if code == SYN_REPORT:
                for s in self.dirty:                                                   # rotated positions of this frame
                    if self.vslot != s: out.append((EV_ABS, ABS_MT_SLOT, s)); self.vslot = s
                    x, y = self._rot(*self.pos[s]); out += [(EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y)]
                self.dirty = []; out.append((EV_SYN, SYN_REPORT, 0))
            elif code == SYN_DROPPED: self.dirty = []
        return out

class TouchProxy:
    @staticmethod
    def find(src_name='NVTCapacitiveTouchScreen'):
        """the event number is not stable across boots (it depends on the order the drivers registered), so the
        touchscreen is looked up by its device name. /dev here is a plain tmpfs with no devtmpfs or udev, so the node
        usually does not exist yet and is created from the numbers sysfs reports (same as /dev/uinput below)."""
        hits = []
        for d in sorted(Path('/sys/class/input').glob('event*')):
            try: name = (d/'device/name').read_text().strip()
            except OSError: continue
            if name == src_name: hits.append(d)
        if len(hits) != 1: raise RuntimeError('touchscreen %r: %d input devices %r' % (src_name, len(hits), [h.name for h in hits]))
        d = hits[0]; node = Path('/dev/input')/d.name
        if not node.exists():
            ma, mi = (int(x) for x in (d/'dev').read_text().strip().split(':'))
            node.parent.mkdir(parents=True, exist_ok=True)
            os.mknod(node, 0o600 | stat.S_IFCHR, os.makedev(ma, mi))
        return str(node)

    def __init__(self, src=None, src_name='NVTCapacitiveTouchScreen', matrix='1 0 0 0 1 0', log=print, on_input=None):
        self.log = log; self.on_input = on_input          # called from the reader thread on any touch (wake from sleep)
        if src is None: src = self.find(src_name); log('TOUCH source', src)
        n = src.rsplit('/', 1)[1]
        if Path('/sys/class/input/%s/device/name' % n).read_text().strip() != src_name: raise RuntimeError('touch identity changed: ' + src)
        self.src_path = src
        self.src = os.open(src, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        info = {}
        for a in ABS_CODES:
            buf = bytearray(ABSINFO.size); fcntl.ioctl(self.src, EVIOCGABS(a), buf); info[a] = ABSINFO.unpack(buf)
        self.rot = Rotator(info[ABS_MT_POSITION_X][1:3], info[ABS_MT_POSITION_Y][1:3], matrix)
        ui = Path('/dev/uinput')
        if not ui.exists():                                                            # /dev is the Bootstrap tmpfs: no node yet
            ma, mi = map(int, Path('/sys/class/misc/uinput/dev').read_text().split(':')); os.mknod(ui, 0o600 | stat.S_IFCHR, os.makedev(ma, mi))
        self.ui = os.open(ui, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        for ev in (EV_KEY, EV_ABS): fcntl.ioctl(self.ui, UI_SET_EVBIT, ev)
        fcntl.ioctl(self.ui, UI_SET_KEYBIT, BTN_TOUCH); fcntl.ioctl(self.ui, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
        for a in ABS_CODES:
            fcntl.ioctl(self.ui, UI_SET_ABSBIT, a); fcntl.ioctl(self.ui, UI_ABS_SETUP, struct.pack('<Hxx', a) + ABSINFO.pack(*info[a]))
        fcntl.ioctl(self.ui, UI_DEV_SETUP, struct.pack('<HHHH80sI', 0x06, 0x1d6b, 0x0700, 1, NAME.encode(), 0))  # BUS_VIRTUAL
        fcntl.ioctl(self.ui, UI_DEV_CREATE)
        buf = bytearray(64); fcntl.ioctl(self.ui, UI_GET_SYSNAME(64), buf); sysname = bytes(buf).split(b'\0')[0].decode()
        evs = sorted(Path('/sys/devices/virtual/input', sysname).glob('event*'))
        if not evs: raise RuntimeError('uinput device has no event node: ' + sysname)
        self.path = '/dev/input/' + evs[0].name
        self.stop = threading.Event(); self.lock = threading.Lock(); self.frames = 0
        self.thread = threading.Thread(target=self._run, name='touchproxy', daemon=True); self.thread.start()
        log('TOUCH proxy', src, '->', self.path, sysname, 'x', self.rot.xr, 'y', self.rot.yr)
    def set_matrix(self, matrix):
        with self.lock: self.rot.set_matrix(matrix)
    def _run(self):
        while not self.stop.is_set():
            if not select.select([self.src], [], [], 0.5)[0]: continue
            try: data = os.read(self.src, EVENT.size * 64)
            except BlockingIOError: continue
            except OSError as e: self.log('TOUCH proxy read error', repr(e)); return
            if self.on_input:
                try: self.on_input()
                except Exception as e: self.log('TOUCH on_input error', repr(e))
            out = []
            with self.lock:
                for i in range(0, len(data) - len(data) % EVENT.size, EVENT.size):
                    _, _, t, code, value = EVENT.unpack_from(data, i); out += self.rot.feed(t, code, value)
            if out:
                try: os.write(self.ui, b''.join(EVENT.pack(0, 0, t, code, value) for t, code, value in out))
                except OSError as e: self.log('TOUCH proxy write error', repr(e))
                self.frames += sum(1 for t, code, _ in out if t == EV_SYN)
    def close(self):
        self.stop.set(); self.thread.join(2)
        try: fcntl.ioctl(self.ui, UI_DEV_DESTROY)
        except OSError: pass
        os.close(self.ui); os.close(self.src)
