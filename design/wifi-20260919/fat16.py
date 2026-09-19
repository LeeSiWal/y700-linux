"""Minimal read-only FAT16 reader with long-file-name support (for the modem_a NON-HLOS image)."""
import struct

class Fat16:
    def __init__(self, path):
        self.f = open(path, 'rb'); bs = self.f.read(512)
        self.bps = struct.unpack_from('<H', bs, 11)[0]; self.spc = bs[13]; rsvd = struct.unpack_from('<H', bs, 14)[0]
        nfats = bs[16]; self.rootents = struct.unpack_from('<H', bs, 17)[0]; fatsz = struct.unpack_from('<H', bs, 22)[0]
        assert bs[510:512] == b'\x55\xaa' and bs[54:62] == b'FAT16   '
        self.fat_off = rsvd * self.bps
        self.f.seek(self.fat_off); self.fat = self.f.read(fatsz * self.bps)
        self.root_off = (rsvd + nfats * fatsz) * self.bps
        self.data_off = self.root_off + ((self.rootents * 32 + self.bps - 1) // self.bps) * self.bps
        self.csize = self.spc * self.bps
    def chain(self, c):
        out = []
        while 2 <= c < 0xFFF8:
            out.append(c); c = struct.unpack_from('<H', self.fat, c * 2)[0]
            if len(out) > 100000: raise RuntimeError('FAT loop')
        return out
    def read_chain(self, c, size=None):
        data = b''.join(self._cluster(x) for x in self.chain(c))
        return data if size is None else data[:size]
    def _cluster(self, c):
        self.f.seek(self.data_off + (c - 2) * self.csize); return self.f.read(self.csize)
    def entries(self, raw):
        lfn = []
        for i in range(0, len(raw), 32):
            e = raw[i:i + 32]
            if e[0] == 0: break
            if e[0] == 0xE5: lfn = []; continue
            if e[11] == 0x0F:
                part = e[1:11] + e[14:26] + e[28:32]
                lfn.insert(0, part.decode('utf-16le', 'replace').split('\x00')[0].replace('￿', '')); continue
            name = ''.join(lfn) if lfn else (e[0:8].decode('latin1').rstrip() + ('.' + e[8:11].decode('latin1').rstrip() if e[8:11].strip() else ''))
            lfn = []
            if e[11] & 0x08: continue          # volume label
            yield name, bool(e[11] & 0x10), struct.unpack_from('<H', e, 26)[0], struct.unpack_from('<I', e, 28)[0]
    def listdir(self, path='/'):
        if path in ('/', ''):
            self.f.seek(self.root_off); raw = self.f.read(self.rootents * 32)
        else:
            parent, _, leaf = path.rstrip('/').rpartition('/')
            hit = [x for x in self.listdir(parent or '/') if x[0].lower() == leaf.lower() and x[1]]
            if len(hit) != 1: raise FileNotFoundError(path)
            raw = self.read_chain(hit[0][2])
        return [x for x in self.entries(raw) if x[0] not in ('.', '..')]
    def read(self, path):
        parent, _, leaf = path.rpartition('/')
        hit = [x for x in self.listdir(parent or '/') if x[0].lower() == leaf.lower() and not x[1]]
        if len(hit) != 1: raise FileNotFoundError(path)
        return self.read_chain(hit[0][2], hit[0][3])
