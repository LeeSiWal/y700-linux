"""Minimal protobuf (proto2) encoder/decoder for the SSC messages (libssc data/*.proto)."""
import struct

def varint(n):
    n &= (1 << 64) - 1; out = bytearray()
    while True:
        b = n & 0x7f; n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n: return bytes(out)

def key(field, wt): return varint(field << 3 | wt)
def f_varint(field, v): return key(field, 0) + varint(v)
def f_fixed64(field, v): return key(field, 1) + struct.pack('<Q', v)
def f_fixed32(field, v): return key(field, 5) + struct.pack('<I', v)
def f_float(field, v): return key(field, 5) + struct.pack('<f', v)
def f_bytes(field, b): return key(field, 2) + varint(len(b)) + b

def decode(buf):
    """-> list of (field, wiretype, value) ; value: int for varint, bytes for len-delimited, int for fixed32/64."""
    out, i = [], 0
    while i < len(buf):
        k, i = _rv(buf, i); f, wt = k >> 3, k & 7
        if wt == 0: v, i = _rv(buf, i)
        elif wt == 1: v = struct.unpack_from('<Q', buf, i)[0]; i += 8
        elif wt == 5: v = struct.unpack_from('<I', buf, i)[0]; i += 4
        elif wt == 2: n, i = _rv(buf, i); v = bytes(buf[i:i + n]); i += n
        else: raise ValueError('wire type %d' % wt)
        out.append((f, wt, v))
    return out

def _rv(b, i):
    n = s = 0
    while True:
        c = b[i]; i += 1; n |= (c & 0x7f) << s; s += 7
        if not c & 0x80: return n, i

def f32(v): return struct.unpack('<f', struct.pack('<I', v))[0]
def fields(buf, n): return [v for f, wt, v in decode(buf) if f == n]
