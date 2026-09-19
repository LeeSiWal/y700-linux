#!/usr/bin/env python3
"""hexrpcd: Python port of linux-msm/hexagonrpc hexagonrpcd (reverse tunnel) for the ADSP sensors PD.
  open /dev/fastrpc-* -> INIT_ATTACH_SNS -> register adsp_default_listener -> adsp_listener init2 -> next2 loop
Local interfaces the DSP may open via remotectl: 0 remotectl, 1 apps_std (read-only files), 2 apps_mem (ADD_PAGES mmap).
Files are served READ-ONLY from a virtual tree (MAP) of local directories; writes are refused (AEE_EUNSUPPORTED).
Every request is logged. Runs until killed (held like the other keepers). Usage: hexrpcd.py DEVICE ROOTMAP.json"""
import fcntl, json, os, stat, struct, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fastrpc as f

AEE_EFAILED, AEE_EBADPARM, AEE_EUNSUPPORTED, AEE_ENOSUCH = 1, 14, 20, 39
ADSP_MMAP_ADD_PAGES = 0x1000
T0 = time.monotonic()
def out(*a): print('%8.3f' % (time.monotonic() - T0), *a, flush=True)

class VFS:
    """virtual path prefix -> local directory (longest prefix wins); directories along the way are synthesised.
    A mapping value may be {"dir": path, "rw": true}: only such prefixes accept writes (a scratch copy, never a device partition)."""
    def __init__(self, mapping):
        items = [(k.rstrip('/') or '/', v if isinstance(v, str) else v['dir'], (not isinstance(v, str)) and bool(v.get('rw'))) for k, v in mapping.items()]
        self.map = sorted(((k, d) for k, d, _ in items), key=lambda x: -len(x[0])); self.rw = [k for k, _, w in items if w]
    def writable(self, vpath):
        v = self.norm(vpath); return any(v == k or v.startswith(k + '/') for k in self.rw)
    def norm(self, p):
        parts = []
        for s in p.split('/'):
            if s in ('', '.'): continue
            if s == '..': parts and parts.pop(); continue
            parts.append(s)
        return '/' + '/'.join(parts)
    def local(self, vpath):
        v = self.norm(vpath)
        for pre, loc in self.map:
            if v == pre or v.startswith(pre + '/'):
                return os.path.join(loc, v[len(pre):].lstrip('/'))
        return None
    def listdir(self, vpath):
        v = self.norm(vpath); names = set(); loc = self.local(v)
        if loc and os.path.isdir(loc): names |= set(os.listdir(loc))
        for pre, _ in self.map:                                      # synthetic parents of mapped prefixes
            if pre != v and pre.startswith(v.rstrip('/') + '/'): names.add(pre[len(v.rstrip('/')) + 1:].split('/')[0])
        return sorted(names) if (names or (loc and os.path.isdir(loc))) else None

class AppsStd:
    def __init__(self, vfs): self.vfs = vfs; self.fds = {}; self.next = 1
    def _new(self, obj): n = self.next; self.next += 1; self.fds[n] = obj; return n
    def open_path(self, name, env=None):
        p = self.vfs.local(name if name.startswith('/') else '/' + (env or '') + '/' + name)
        return p
    # --- methods: (msg_id) -> (spec in, handler) ; handler(inbufs) -> (result, outbufs list)
    def fopen_with_env(self, prim, seqs):
        env, delim, name, mode = [s.rstrip(b'\0').decode(errors='replace') for s in seqs]
        base = {'ADSP_LIBRARY_PATH': '/usr/lib/qcom/adsp', 'ADSP_AVS_CFG_PATH': '/vendor/etc/acdbdata'}.get(env)
        v = name if name.startswith('/') else (base + '/' + name if base else None)
        p = self.vfs.local(v) if v else None
        if mode[:1] in ('w', 'a') or '+' in mode:
            if not (v and p and self.vfs.writable(v)):
                out('APPS_STD fopen_with_env(%s,%s,%s) REFUSED (write outside rw)' % (env, name, mode)); return AEE_EUNSUPPORTED, [struct.pack('<I', 0)]
            os.makedirs(os.path.dirname(p), exist_ok=True)
            pm = {'w': 'wb', 'a': 'ab', 'r+': 'r+b', 'w+': 'w+b', 'a+': 'a+b'}.get(mode.replace('b', ''), 'wb')
            n = self._new(open(p, pm)); out('APPS_STD fopen_with_env(%s,%s,%s) -> %d RW (%s)' % (env, name, mode, n, p))
            return 0, [struct.pack('<I', n)]
        if not p or not os.path.isfile(p):
            out('APPS_STD fopen_with_env(%s,%s,%s) -> ENOENT (%s)' % (env, name, mode, v)); return AEE_EFAILED, [struct.pack('<I', 0)]
        n = self._new(open(p, 'rb')); out('APPS_STD fopen_with_env(%s,%s,%s) -> %d (%s)' % (env, name, mode, n, p))
        return 0, [struct.pack('<I', n)]
    def fread(self, prim, seqs, caps):
        fd, = struct.unpack_from('<I', prim); fo = self.fds.get(fd)
        if not hasattr(fo, 'read'): return AEE_EFAILED, [struct.pack('<II', 0, 1), bytes(caps[0])]
        # 9a582621: the reply buffer must be exactly the capacity the DSP asked for (hexagonrpcd mallocs cap bytes);
        # the number of valid bytes goes in prim_out.
        d = fo.read(caps[0]); out('APPS_STD fread(%d, cap %d) -> %d' % (fd, caps[0], len(d)))
        return 0, [struct.pack('<II', len(d), int(len(d) < caps[0])), d.ljust(caps[0], b'\0')]
    def fclose(self, prim, seqs):
        fd, = struct.unpack_from('<I', prim); fo = self.fds.pop(fd, None)
        if fo is None: return AEE_EFAILED, []
        if hasattr(fo, 'close'): fo.close()
        out('APPS_STD fclose(%d)' % fd); return 0, []
    def fflush(self, prim, seqs): return 0, []
    def fseek(self, prim, seqs):
        fd, pos, whence = struct.unpack_from('<III', prim); fo = self.fds.get(fd)
        if not hasattr(fo, 'seek'): return AEE_EFAILED, []
        fo.seek(struct.unpack('<i', struct.pack('<I', pos))[0], whence); out('APPS_STD fseek(%d, %d, %d)' % (fd, pos, whence)); return 0, []
    def opendir(self, prim, seqs):
        name = seqs[0].rstrip(b'\0').decode(errors='replace'); ents = self.vfs.listdir(name)
        if ents is None: out('APPS_STD opendir(%s) -> ENOENT' % name); return AEE_EFAILED, [struct.pack('<Q', 0)]
        n = self._new(list(ents)); out('APPS_STD opendir(%s) -> %d (%d entries)' % (name, n, len(ents))); return 0, [struct.pack('<Q', n)]
    def closedir(self, prim, seqs):
        d, = struct.unpack_from('<Q', prim); return (0 if self.fds.pop(d, None) is not None else AEE_EFAILED), []
    def readdir(self, prim, seqs):
        d, = struct.unpack_from('<Q', prim); ents = self.fds.get(d)
        if not isinstance(ents, list): return AEE_EFAILED, [bytes(264)]
        name = ents.pop(0).encode()[:255] if ents else b''
        return 0, [struct.pack('<I', 0) + name.ljust(256, b'\0') + struct.pack('<I', int(not name))]
    def fwrite(self, prim, seqs):
        fd, = struct.unpack_from('<I', prim); fo = self.fds.get(fd)
        if not hasattr(fo, 'write') or fo.mode.startswith('r') and '+' not in fo.mode: return AEE_EFAILED, [struct.pack('<II', 0, 0)]
        n = fo.write(seqs[0]); out('APPS_STD fwrite(%d, %d) -> %d' % (fd, len(seqs[0]), n)); return 0, [struct.pack('<II', n, 0)]
    def fsync(self, prim, seqs):
        fd, = struct.unpack_from('<I', prim); fo = self.fds.get(fd)
        if hasattr(fo, 'flush'): fo.flush(); os.fsync(fo.fileno())
        return 0, []
    def ftell(self, prim, seqs):
        fd, = struct.unpack_from('<I', prim); fo = self.fds.get(fd)
        return (0, [struct.pack('<I', fo.tell())]) if hasattr(fo, 'tell') else (AEE_EFAILED, [bytes(4)])
    def flen(self, prim, seqs):
        fd, = struct.unpack_from('<I', prim); fo = self.fds.get(fd)
        return (0, [struct.pack('<Q', os.fstat(fo.fileno()).st_size)]) if hasattr(fo, 'fileno') else (AEE_EFAILED, [bytes(8)])
    def _rwpath(self, seq, op):
        v = seq.rstrip(b'\0').decode(errors='replace'); p = self.vfs.local(v)
        if not (p and self.vfs.writable(v)): out('APPS_STD %s(%s) REFUSED (outside rw)' % (op, v)); return None, v
        return p, v
    def fremove(self, prim, seqs):
        p, v = self._rwpath(seqs[0], 'fremove')
        if p is None: return AEE_EUNSUPPORTED, []
        try: os.remove(p)
        except FileNotFoundError: out('APPS_STD fremove(%s) -> ENOENT' % v); return AEE_EFAILED, []
        out('APPS_STD fremove(%s)' % v); return 0, []
    def frename(self, prim, seqs):
        a, va = self._rwpath(seqs[0], 'frename'); b, vb = self._rwpath(seqs[1], 'frename')
        if a is None or b is None: return AEE_EUNSUPPORTED, []
        try: os.replace(a, b)
        except OSError as e: out('APPS_STD frename(%s,%s) -> %s' % (va, vb, e)); return AEE_EFAILED, []
        out('APPS_STD frename(%s -> %s)' % (va, vb)); return 0, []
    def mkdir(self, prim, seqs):
        p, v = self._rwpath(seqs[0], 'mkdir')
        if p is None: return AEE_EUNSUPPORTED, []
        os.makedirs(p, exist_ok=True); out('APPS_STD mkdir(%s)' % v); return 0, []
    def ftrunc(self, prim, seqs):
        fd, off = struct.unpack_from('<Ii', prim); fo = self.fds.get(fd)
        if not hasattr(fo, 'truncate') or fo.mode.startswith('r') and '+' not in fo.mode: return AEE_EFAILED, []
        fo.truncate(off); out('APPS_STD ftrunc(%d, %d)' % (fd, off)); return 0, []
    def fileExists(self, prim, seqs):
        v = seqs[0].rstrip(b'\0').decode(errors='replace'); p = self.vfs.local(v)
        e = bool(p and os.path.exists(p)) or self.vfs.listdir(v) is not None; out('APPS_STD fileExists(%s) -> %d' % (v, e))
        return 0, [bytes([int(e)])]
    def stat(self, prim, seqs):
        name = seqs[0].rstrip(b'\0').decode(errors='replace')
        p = self.vfs.local(name if name.startswith('/') else '/usr/lib/qcom/adsp/' + name)
        if not p or not os.path.exists(p):
            if self.vfs.listdir(name) is not None: st = os.stat('/')
            else: out('APPS_STD stat(%s) -> ENOENT' % name); return AEE_EFAILED, [bytes(96)]
        else: st = os.stat(p)
        out('APPS_STD stat(%s) size=%d' % (name, st.st_size))
        return 0, [struct.pack('<QQQIIQQqqqqqq', 0, st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_rdev, st.st_size,
                               int(st.st_atime), 0, int(st.st_mtime), 0, int(st.st_ctime), 0)]

# method tables: msg_id -> (name, input spec, handler kind)
# input spec lists what is in prim_in for the DSP->AP call: W4/W8 words, S = seq length (+inbuf), O = out seq capacity
APPS_STD = {2: ('fflush', ['W4'], 'fflush'), 3: ('fclose', ['W4'], 'fclose'), 4: ('fread', ['W4', 'O'], 'fread'),
            9: ('fseek', ['W4', 'W4', 'W4'], 'fseek'), 19: ('fopen_with_env', ['S', 'S', 'S', 'S'], 'fopen_with_env'),
            26: ('opendir', ['S'], 'opendir'), 27: ('closedir', ['W8'], 'closedir'), 28: ('readdir', ['W8'], 'readdir'),
            31: ('stat', ['S'], 'stat'), 5: ('fwrite', ['W4', 'S'], 'fwrite'), 8: ('ftell', ['W4'], 'ftell'),
            10: ('flen', ['W4'], 'flen'), 22: ('fileExists', ['S'], 'fileExists'), 23: ('fsync', ['W4'], 'fsync'),
            24: ('fremove', ['S'], 'fremove'), 29: ('mkdir', ['S', 'W4'], 'mkdir'), 32: ('ftrunc', ['W4', 'W4'], 'ftrunc'),
            33: ('frename', ['S', 'S'], 'frename')}

def decode_inbufs(data, n):
    bufs, off = [], 0
    for _ in range(n):
        size, = struct.unpack_from('<I', data, off); off += 4
        if size:
            off += (8 - off % 8) % 8
        bufs.append(bytes(data[off:off + size])); off += size
    return bufs

def encode_outbufs(bufs):
    out_, off = bytearray(), 0
    for b in bufs:
        out_ += struct.pack('<I', len(b))
        if b: out_ += bytes((8 - len(out_) % 8) % 8); out_ += b
    return bytes(out_)

class Server:
    def __init__(self, fd, vfs):
        self.fd = fd; self.std = AppsStd(vfs); self.ifaces = ['remotectl', 'apps_std', 'apps_mem']
    def dispatch(self, handle, sc, inbufs):
        method = (sc >> 24) & 0x1f; nin, nout = (sc >> 16) & 0xff, (sc >> 8) & 0xff
        prim = inbufs[0] if inbufs else b''
        if method == 31 and len(prim) >= 4: method, = struct.unpack_from('<I', prim); prim = prim[4:]
        if handle == 0:                                                  # remotectl served locally
            if method == 0:
                name = inbufs[1].rstrip(b'\0').decode(errors='replace'); cap = struct.unpack_from('<I', prim, 4)[0]
                if name in self.ifaces:
                    out('REMOTECTL open(%s) -> %d' % (name, self.ifaces.index(name))); return 0, [struct.pack('<II', self.ifaces.index(name), 0), bytes(cap)]
                out('REMOTECTL open(%s) -> not found' % name); return (-5) & 0xffffffff, [struct.pack('<II', 0, (-5) & 0xffffffff), bytes(cap)]
            if method == 1:
                cap = struct.unpack_from('<I', prim, 4)[0]; return 0, [struct.pack('<I', 0), bytes(cap)]
        if handle == 1 and method in APPS_STD:
            name, spec, h = APPS_STD[method]
            words = []; caps = []; pos = 0
            for k in spec:
                if k == 'W4': words.append(struct.unpack_from('<I', prim, pos)[0]); pos += 4
                elif k == 'W8': words.append(struct.unpack_from('<Q', prim, pos)[0]); pos += 8
                elif k in ('S', 'O'):
                    v = struct.unpack_from('<I', prim, pos)[0]; pos += 4
                    if k == 'O': caps.append(v)
            fn = getattr(self.std, h)
            return fn(prim, inbufs[1:], caps) if h == 'fread' else fn(prim, inbufs[1:])
        if handle == 2 and method == 2:                                  # apps_mem request_map64
            heap, lflags, rflags, pad, vin, ln = struct.unpack_from('<IIIIQQ', prim)
            if not rflags & ADSP_MMAP_ADD_PAGES:
                out('APPS_MEM map rflags=%x unsupported' % rflags); return AEE_EUNSUPPORTED, [bytes(16)]
            req = bytearray(struct.pack('<iIQQQ', -1, rflags, 0, ln, 0)); fcntl.ioctl(self.fd, f.FASTRPC_IOCTL_MMAP, req, True)
            vout = struct.unpack_from('<Q', req, 24)[0]; out('APPS_MEM map len=%d rflags=%x -> 0x%x' % (ln, rflags, vout))
            return 0, [struct.pack('<QQ', 0, vout & 0xffffffff)]
        out('UNSUPPORTED handle=%d method=%d sc=%08x inbufs=%s' % (handle, method, sc, [b[:32].hex() for b in inbufs])); return AEE_EUNSUPPORTED, [bytes(0)] * nout

    def run(self):
        f.invoke(self.fd, f.ADSP_LISTENER, 3, [], [])                         # adsp_listener_init2
        out('LISTENER_READY')
        rctx, result, outb = 0, 0xffffffff, b''
        while True:
            rctx, handle, sc, inlen, inraw = f.invoke(self.fd, f.ADSP_LISTENER, 4,
                [f.WORD4, f.WORD4, f.BLOB_SEQ, f.OUT4, f.OUT4, f.OUT4, f.OUT4, f.OUT_SEQ], [rctx, result, outb, 256])
            if inlen > 256:
                # quic/fastrpc listener_android.c: fetch the rest with adsp_listener_get_in_bufs2 (method 5:
                # in ctx, in offset, rout sequence<octet> bufs, rout bufsLenReq) at offset = bytes already received
                try: inraw = get_rest(self.fd, rctx, inraw[:256], inlen)
                except Exception as e: out('STOP: get_in_bufs2 %d failed %r' % (inlen, e)); result, outb = AEE_EFAILED, b''; continue
                out('LARGE_INBUFS %d fetched' % inlen)
            nin, nout = (sc >> 16) & 0xff, (sc >> 8) & 0xff
            try:
                result, bufs = self.dispatch(handle, sc, decode_inbufs(inraw[:inlen], nin))
            except Exception as e:
                out('ERROR handle=%d sc=%08x %r' % (handle, sc, e)); result, bufs = AEE_EFAILED, []
            bufs = (bufs + [b''] * nout)[:nout]
            outb = encode_outbufs(bufs)

def get_rest(fd, rctx, head, inlen, invoke=None):
    """head (the first 256 bytes from next2) + the remaining inlen-256 bytes via get_in_bufs2."""
    invoke = invoke or f.invoke
    req, rest = invoke(fd, f.ADSP_LISTENER, 5, [f.WORD4, f.WORD4, f.OUT_SEQ, f.OUT4], [rctx, len(head), inlen - len(head)])
    if req != inlen - len(head): raise RuntimeError('get_in_bufs2 returned %d, wanted %d' % (req, inlen - len(head)))
    return bytes(head) + bytes(rest[:req])

def main():
    dev, rootmap = sys.argv[1], json.load(open(sys.argv[2]))
    vfs = VFS(rootmap)
    fd = os.open(dev, os.O_RDWR); out('OPEN %s' % dev)
    for i in range(10):                      # a previous listener's session may still be releasing
        try: fcntl.ioctl(fd, f.FASTRPC_IOCTL_INIT_ATTACH_SNS); break
        except OSError as e:
            out('ATTACH retry %d: %s' % (i, e)); time.sleep(1)
    else: raise RuntimeError('INIT_ATTACH_SNS failed')
    out('ATTACHED sensorspd')
    h = f.remote_open(fd, 'adsp_default_listener'); f.invoke(fd, h, 0, [], []); f.remote_close(fd, h); out('DEFAULT_LISTENER_REGISTERED')
    Server(fd, vfs).run()

if __name__ == '__main__':
    try: main()
    except BaseException as e:
        out('STOP: exception %r' % (e,))
        while True: time.sleep(1)
