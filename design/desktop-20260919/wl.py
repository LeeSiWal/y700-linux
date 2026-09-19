"""Minimal Wayland client wire protocol (no libwayland): enough for the Y700 presenter.
Messages: header = object id (u32) + (size << 16 | opcode) (u32); args: int/uint/fixed 4 bytes, string/array = u32 length
(string length includes NUL) + data padded to 4, new_id = u32, fd = SCM_RIGHTS ancillary data."""
import os, socket, struct

class Conn:
    def __init__(self, path=None):
        path = path or os.path.join(os.environ['XDG_RUNTIME_DIR'], os.environ.get('WAYLAND_DISPLAY', 'wayland-0'))
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.s.connect(path)
        self.next_id = 2; self.handlers = {}; self.buf = b''; self.fds = []
    def new_id(self): i = self.next_id; self.next_id += 1; return i
    def send(self, obj, op, payload=b'', fds=()):
        msg = struct.pack('<II', obj, ((8 + len(payload)) << 16) | op) + payload
        if fds: socket.send_fds(self.s, [msg], list(fds))
        else: self.s.sendall(msg)
    def on(self, obj, fn): self.handlers[obj] = fn
    def dispatch(self, block=True, timeout=1.0):
        """Read what is available (wait up to timeout s for the first chunk if block) and call handlers(obj, opcode, payload)."""
        import select
        if block and not select.select([self.s], [], [], timeout)[0]: return 0
        self.s.setblocking(False)
        try:
            data, fds, _, _ = socket.recv_fds(self.s, 65536, 16)
        except BlockingIOError:
            return 0
        finally:
            self.s.setblocking(True)
        if not data: raise ConnectionError('compositor closed the connection')
        self.buf += data; self.fds += fds; n = 0
        while len(self.buf) >= 8:
            obj, so = struct.unpack_from('<II', self.buf); size = so >> 16
            if len(self.buf) < size: break
            payload = self.buf[8:size]; self.buf = self.buf[size:]; n += 1
            h = self.handlers.get(obj)
            if h: h(obj, so & 0xffff, payload)
        return n
    def roundtrip(self):
        cb = self.new_id(); done = []
        self.on(cb, lambda o, op, p: done.append(1)); self.send(1, 0, struct.pack('<I', cb))   # wl_display.sync
        while not done: self.dispatch()

def string(s):
    b = s.encode() + b'\0'; return struct.pack('<I', len(b)) + b + b'\0' * (-len(b) % 4)

def read_string(p, off):
    n = struct.unpack_from('<I', p, off)[0]; s = p[off + 4:off + 4 + n - 1].decode(); return s, off + 4 + n + (-n % 4)

def registry(conn):
    """{interface: (name, version)} of all globals (wl_registry.global events after a roundtrip)."""
    reg = conn.new_id(); globs = {}
    def h(o, op, p):
        if op == 0:
            name = struct.unpack_from('<I', p)[0]; iface, off = read_string(p, 4); ver = struct.unpack_from('<I', p, off)[0]
            globs.setdefault(iface, []).append((name, ver))
    conn.on(reg, h); conn.send(1, 1, struct.pack('<I', reg)); conn.roundtrip()
    return reg, globs

def bind(conn, reg, name, iface, version):
    oid = conn.new_id(); conn.send(reg, 0, struct.pack('<I', name) + string(iface) + struct.pack('<II', version, oid)); return oid
