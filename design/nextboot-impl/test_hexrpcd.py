"""Host tests for hexrpcd framing, VFS and apps_std (no device)."""
import importlib.util, os, struct, tempfile, unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('hx', Path(__file__).with_name('hexrpcd.py')); hx = importlib.util.module_from_spec(spec); spec.loader.exec_module(hx)
import fastrpc as f

class H(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); (self.d/'reg').mkdir(); (self.d/'reg'/'a.json').write_bytes(b'x' * 300)
        self.vfs = hx.VFS({'/mnt/vendor/persist/sensors/registry': str(self.d/'reg'), '/vendor/etc/sensors': str(self.d)})
    def test_framing_roundtrip(self):
        bufs = [b'\x01\x02\x03\x04', b'', b'hello world', b'z' * 9]
        enc = hx.encode_outbufs(bufs); self.assertEqual(hx.decode_inbufs(enc, 4), bufs)
        self.assertEqual(len(enc) % 1, 0); self.assertEqual(enc[:4], struct.pack('<I', 4)); self.assertEqual(enc[4:8], b'\0' * 4)   # size then pad to 8
    def test_vfs(self):
        self.assertEqual(self.vfs.local('/mnt/vendor/persist/sensors/registry/a.json'), str(self.d/'reg'/'a.json'))
        self.assertEqual(self.vfs.listdir('/mnt/vendor'), ['persist']); self.assertIn('a.json', self.vfs.listdir('/mnt/vendor/persist/sensors/registry'))
        self.assertIsNone(self.vfs.local('/etc/passwd')); self.assertIsNone(self.vfs.local('/mnt/vendor/persist/sensors/registry/../../../../etc/passwd'))
    def test_apps_std_read_cycle(self):
        s = hx.Server(-1, self.vfs)
        name = b'/mnt/vendor/persist/sensors/registry/a.json\0'
        seqs = [b'ADSP_LIBRARY_PATH\0', b';\0', name, b'r\0']
        prim = b''.join(struct.pack('<I', len(x)) for x in seqs)
        sc = f.sc_make(19, 5, 1)
        r, out = s.dispatch(1, sc, [prim] + seqs); self.assertEqual(r, 0); fd, = struct.unpack('<I', out[0])
        r, out = s.dispatch(1, f.sc_make(4, 1, 2), [struct.pack('<II', fd, 256)]); self.assertEqual(struct.unpack('<II', out[0]), (256, 0)); self.assertEqual(len(out[1]), 256)
        r, out = s.dispatch(1, f.sc_make(4, 1, 2), [struct.pack('<II', fd, 256)]); self.assertEqual(struct.unpack('<II', out[0]), (44, 1)); self.assertEqual(len(out[1]), 256)   # padded to capacity
        self.assertEqual(s.dispatch(1, f.sc_make(3, 1, 0), [struct.pack('<I', fd)])[0], 0)
    def test_write_refused_and_stat(self):
        s = hx.Server(-1, self.vfs); seqs = [b'X\0', b';\0', b'/vendor/etc/sensors/n\0', b'w\0']
        r, _ = s.dispatch(1, f.sc_make(19, 5, 1), [b''.join(struct.pack('<I', len(x)) for x in seqs)] + seqs); self.assertEqual(r, hx.AEE_EUNSUPPORTED)
        n = b'/mnt/vendor/persist/sensors/registry/a.json\0'
        r, out = s.dispatch(1, f.sc_make(31, 2, 1), [struct.pack('<II', 31, len(n)), n]); self.assertEqual(r, 0); self.assertEqual(len(out[0]), 96)
        self.assertEqual(struct.unpack_from('<Q', out[0], 40)[0], 300)                     # tsz dev ino mode nlink rdev | size
    def test_readdir(self):
        s = hx.Server(-1, self.vfs); n = b'/mnt/vendor/persist/sensors/registry\0'
        r, out = s.dispatch(1, f.sc_make(26, 2, 1), [struct.pack('<I', len(n)), n]); d, = struct.unpack('<Q', out[0])
        r, out = s.dispatch(1, f.sc_make(28, 1, 1), [struct.pack('<Q', d)]); self.assertEqual(out[0][4:4 + 6], b'a.json'); self.assertEqual(len(out[0]), 264)
        r, out = s.dispatch(1, f.sc_make(28, 1, 1), [struct.pack('<Q', d)]); self.assertEqual(struct.unpack_from('<I', out[0], 260)[0], 1)
    def test_remotectl(self):
        s = hx.Server(-1, self.vfs); n = b'apps_std\0'
        r, out = s.dispatch(0, f.sc_make(0, 2, 2), [struct.pack('<II', len(n), 64), n]); self.assertEqual(struct.unpack('<II', out[0]), (1, 0)); self.assertEqual(len(out[1]), 64)

class RW(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); (self.d/'rw').mkdir(); (self.d/'ro').mkdir(); (self.d/'ro'/'x').write_bytes(b'ro')
        self.vfs = hx.VFS({'/mnt/vendor/persist/sensors': {'dir': str(self.d/'rw'), 'rw': True}, '/vendor/etc/sensors': str(self.d/'ro')})
        self.s = hx.Server(-1, self.vfs)
    def open(self, name, mode):
        seqs = [b'ADSP_LIBRARY_PATH\0', b';\0', name.encode() + b'\0', mode.encode() + b'\0']
        return self.s.dispatch(1, f.sc_make(19, 5, 1), [b''.join(struct.pack('<I', len(x)) for x in seqs)] + seqs)
    def test_write_only_in_rw(self):
        r, out = self.open('/mnt/vendor/persist/sensors/registry/registry/DIR', 'w'); self.assertEqual(r, 0); fd, = struct.unpack('<I', out[0])
        r, out = self.s.dispatch(1, f.sc_make(5, 2, 1), [struct.pack('<II', fd, 3), b'abc']); self.assertEqual(struct.unpack('<II', out[0]), (3, 0))
        self.s.dispatch(1, f.sc_make(3, 1, 0), [struct.pack('<I', fd)])
        self.assertEqual((self.d/'rw'/'registry'/'registry'/'DIR').read_bytes(), b'abc')
        r, _ = self.open('/vendor/etc/sensors/x', 'w'); self.assertEqual(r, hx.AEE_EUNSUPPORTED); self.assertEqual((self.d/'ro'/'x').read_bytes(), b'ro')
        n = b'/vendor/etc/sensors/x\0'; r, _ = self.s.dispatch(1, f.sc_make(24, 2, 0), [struct.pack('<I', len(n)), n]); self.assertEqual(r, hx.AEE_EUNSUPPORTED)
        self.assertTrue((self.d/'ro'/'x').exists())
    def test_rename_and_exists(self):
        (self.d/'rw'/'a').write_bytes(b'1'); o, n = b'/mnt/vendor/persist/sensors/a\0', b'/mnt/vendor/persist/sensors/b\0'
        r, _ = self.s.dispatch(1, f.sc_make(31, 3, 0), [struct.pack('<III', 33, len(o), len(n)), o, n]); self.assertEqual(r, 0)
        r, out = self.s.dispatch(1, f.sc_make(22, 2, 1), [struct.pack('<I', len(n)), n]); self.assertEqual(out[0], b'\x01')

if __name__ == '__main__': unittest.main()

class LargeInbufs(unittest.TestCase):
    """get_in_bufs2 (listener method 5) for >256-byte reverse-call inputs (boot 8e18ad1f: registry writes of 258..1267 B)."""
    def test_rest_fetched(self):
        import fastrpc as f
        full = bytes(range(256)) * 4 + b'tail'; calls = []
        def fake(fd, handle, mid, spec, vals):
            calls.append((handle, mid, spec, vals)); off, cap = vals[1], vals[2]
            return [cap, full[off:off + cap]]
        got = hx.get_rest(3, 0x77, full[:256], len(full), invoke=fake)
        self.assertEqual(got, full)
        self.assertEqual(calls, [(f.ADSP_LISTENER, 5, [f.WORD4, f.WORD4, f.OUT_SEQ, f.OUT4], [0x77, 256, len(full) - 256])])
    def test_short_reply_rejected(self):
        with self.assertRaises(RuntimeError):
            hx.get_rest(3, 1, b'x' * 256, 400, invoke=lambda *a: [10, b'y' * 10])
