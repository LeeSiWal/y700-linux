"""Presenter protocol side (Wayland client only, no DRM): set the headless output mode (zwlr_output_manager_v1 v4) and
capture frames of it into a wl_shm buffer (zwlr_screencopy_manager_v1 v3). Opcodes from wlr-protocols
(wlr-output-management-unstable-v1.xml, wlr-screencopy-unstable-v1.xml) and wayland.xml."""
import mmap, os, struct, time
import wl

WL_SHM_FORMAT_XRGB8888 = 1

class Outputs:
    """Heads/modes of zwlr_output_manager_v1 (server-created objects) + one configuration round."""
    def __init__(self, conn, reg, globs):
        self.c = conn; self.heads = {}; self.serial = None
        name, ver = globs['zwlr_output_manager_v1'][0]
        self.mgr = wl.bind(conn, reg, name, 'zwlr_output_manager_v1', min(ver, 4)); conn.on(self.mgr, self._mgr)
        conn.roundtrip(); conn.roundtrip()
    def _mgr(self, o, op, p):
        if op == 0:                                   # head(new_id)
            hid = struct.unpack_from('<I', p)[0]; self.heads[hid] = {'modes': {}}; self.c.on(hid, self._head)
        elif op == 1: self.serial = struct.unpack_from('<I', p)[0]   # done(serial)
    def _head(self, o, op, p):
        h = self.heads[o]
        if op == 0: h['name'] = wl.read_string(p, 0)[0]
        elif op == 3:                                 # mode(new_id)
            mid = struct.unpack_from('<I', p)[0]; h['modes'][mid] = {}; self.c.on(mid, lambda mo, mop, mp, h=h: self._mode(h, mo, mop, mp))
        elif op == 4: h['enabled'] = struct.unpack_from('<i', p)[0]
        elif op == 5: h['current_mode'] = struct.unpack_from('<I', p)[0]
        elif op == 7: h['transform'] = struct.unpack_from('<i', p)[0]                 # transform(wl_output.transform)
    def _mode(self, h, o, op, p):
        m = h['modes'][o]
        if op == 0: m['w'], m['h'] = struct.unpack_from('<ii', p)
        elif op == 1: m['refresh'] = struct.unpack_from('<i', p)[0]
    def set_custom(self, head_name, w, h, refresh_mhz, scale=None, transform=None):
        head = [hid for hid, v in self.heads.items() if v.get('name') == head_name]
        if not head: raise RuntimeError('no head %s in %r' % (head_name, [v.get('name') for v in self.heads.values()]))
        cfg = self.c.new_id(); res = []
        self.c.on(cfg, lambda o, op, p: res.append(('succeeded', 'failed', 'cancelled')[op]))
        self.c.send(self.mgr, 0, struct.pack('<II', cfg, self.serial))                     # create_configuration(id, serial)
        ch = self.c.new_id(); self.c.send(cfg, 0, struct.pack('<II', ch, head[0]))          # enable_head(config_head, head)
        self.c.send(ch, 1, struct.pack('<iii', w, h, refresh_mhz))                           # set_custom_mode
        if transform is not None: self.c.send(ch, 3, struct.pack('<i', transform))           # set_transform(wl_output.transform)
        if scale: self.c.send(ch, 4, struct.pack('<i', int(scale * 256)))                   # set_scale(fixed 24.8)
        self.c.send(cfg, 2)                                                                 # apply
        while not res: self.c.dispatch()
        self.c.send(cfg, 4)                                                                 # destroy
        return res[0]

class Capturer:
    """One reusable wl_shm buffer; capture(damage=True) returns (memoryview of the frame, info)."""
    def __init__(self, conn, reg, globs, output_name='HEADLESS-1', provider=None):
        # provider(w, h, stride, fmt) -> (wl_buffer id, mmap): lets the caller supply the destination buffer, so the
        # compositor can copy straight into a scanout framebuffer instead of into a buffer we then copy again
        self.c = conn
        n, v = globs['zwlr_screencopy_manager_v1'][0]; self.mgr = wl.bind(conn, reg, n, 'zwlr_screencopy_manager_v1', min(v, 3))
        n, v = globs['wl_shm'][0]; self.shm = wl.bind(conn, reg, n, 'wl_shm', 1)
        self.output = None
        for n, v in globs['wl_output']:
            o = wl.bind(conn, reg, n, 'wl_output', min(v, 4)); names = []
            conn.on(o, lambda ob, op, p, names=names: names.append(wl.read_string(p, 0)[0]) if op == 4 else None)
            conn.roundtrip()
            if names and names[0] == output_name: self.output = o
        if self.output is None: raise RuntimeError('output %s not found' % output_name)
        self.buf = None; self.geom = None; self.pending = None; self.provider = provider
    def _alloc(self, w, h, stride, fmt):
        size = stride * h; fd = os.memfd_create('y700-presenter', os.MFD_CLOEXEC); os.ftruncate(fd, size)
        pool = self.c.new_id(); self.c.send(self.shm, 0, struct.pack('<Ii', pool, size), fds=[fd])   # create_pool(id, fd, size)
        b = self.c.new_id(); self.c.send(pool, 0, struct.pack('<Iiiiii', b, 0, w, h, stride, fmt))  # create_buffer
        self.c.send(pool, 1)                                                                     # pool.destroy (buffer keeps it)
        self.map = mmap.mmap(fd, size); os.close(fd); self.buf = b; self.geom = (w, h, stride, fmt)
    def capture(self, damage=True, timeout=5.0):
        """On TimeoutError the copy request stays pending and the next call keeps waiting on it (no new request):
        an idle screen must not pile up one pending copy per timeout."""
        t0 = time.monotonic()
        if self.pending: fr, st, w, h_, stride, fmt = self.pending; self.pending = None
        else:
            fr = self.c.new_id(); st = {'buffers': [], 'damage': []}
            def h(o, op, p):
                if op == 0: st['buffers'].append(struct.unpack_from('<IIII', p))
                elif op == 2: st['ready'] = True
                elif op == 3: st['failed'] = True
                elif op == 4: st['damage'].append(struct.unpack_from('<IIII', p))
                elif op == 6: st['buffer_done'] = True
            self.c.on(fr, h); self.c.send(self.mgr, 0, struct.pack('<IiI', fr, 0, self.output))      # capture_output(frame, no cursor, output)
            while not (st.get('buffer_done') or st.get('failed')): self.c.dispatch()
            if st.get('failed'): raise RuntimeError('capture failed')
            shm = [b for b in st['buffers'] if b[0] == WL_SHM_FORMAT_XRGB8888] or st['buffers']
            fmt, w, h_, stride = shm[0]
            got = self.provider(w, h_, stride, fmt) if self.provider is not None else None
            if got is not None: self.buf, self.map = got; self.geom = (w, h_, stride, fmt)    # straight into a scanout fb
            elif self.geom != (w, h_, stride, fmt) or self.buf is None: self._alloc(w, h_, stride, fmt)
            self.c.send(fr, 2 if damage else 0, struct.pack('<I', self.buf))                        # copy_with_damage / copy
        while not (st.get('ready') or st.get('failed')):
            if time.monotonic() - t0 > timeout:
                self.pending = (fr, st, w, h_, stride, fmt); raise TimeoutError('no frame within %.1f s' % timeout)
            self.c.dispatch()
        self.c.send(fr, 1)                                                                          # frame.destroy
        if st.get('failed'): raise RuntimeError('copy failed')
        return memoryview(self.map), {'w': w, 'h': h_, 'stride': stride, 'format': fmt, 'damage': st['damage']}
