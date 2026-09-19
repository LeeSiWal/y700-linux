"""Read-only FAT12 reader (bluetooth_a image): reuses the FAT16 reader's directory/LFN code, 12-bit FAT chains."""
import struct, sys
sys.path.insert(0, '/home/siwal/y700-design/wifi-20260919')
import fat16

class Fat12(fat16.Fat16):
    def __init__(self, path):
        f = open(path, 'rb'); bs = f.read(512); f.close()
        assert bs[510:512] == b'\x55\xaa' and bs[54:62] == b'FAT12   '
        patched = bs[:54] + b'FAT16   ' + bs[62:]
        real_open = open
        class _F:                                   # present the boot sector as FAT16 only to the base constructor
            def __init__(s): s.f = real_open(path, 'rb')
            def read(s, n): d = s.f.read(n); return patched if n == 512 and d == bs else d
            def seek(s, o): s.f.seek(o)
        fat16.open = lambda p, m: _F()
        try: super().__init__(path)
        finally: del fat16.open
    def chain(self, c):
        out = []
        while 2 <= c < 0xFF8:
            out.append(c); v = struct.unpack_from('<H', self.fat, c * 3 // 2)[0]
            c = (v >> 4) if c & 1 else (v & 0xFFF)
            if len(out) > 100000: raise RuntimeError('FAT loop')
        return out

if __name__ == '__main__':
    import hashlib
    fs = Fat12(sys.argv[1])
    def walk(p):
        for name, d, c, size in fs.listdir(p):
            q = (p.rstrip('/') + '/' + name)
            if d: walk(q)
            else: print('%-45s %8d %s' % (q, size, hashlib.sha256(fs.read(q)).hexdigest()))
    walk('/')
