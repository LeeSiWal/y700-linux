"""Minimal FastRPC client over the vendor (upstream-compatible) uapi, ported from linux-msm/hexagonrpc (libhexagonrpc).
Marshalling of one method call:
  prim_in  = [u32 method id if msg_id > 30] + for each arg: WORD value | BLOB_SEQ element count | OUT_BLOB_SEQ capacity
  inbufs   = [prim_in (if non-empty)] + one buffer per BLOB_SEQ
  prim_out = concatenated OUT_BLOB values; outbufs = [prim_out (if non-empty)] + one buffer per OUT_BLOB_SEQ
  sc       = REMOTE_SCALARS_MAKE(min(msg_id, 31), n_inbufs, n_outbufs)"""
import ctypes, fcntl, struct

FASTRPC_IOCTL_INVOKE = 0xc0105203            # _IOWR('R', 3, struct fastrpc_invoke) (16 bytes)
FASTRPC_IOCTL_INIT_ATTACH = 0x5204           # _IO('R', 4)
FASTRPC_IOCTL_INIT_ATTACH_SNS = 0x5208       # _IO('R', 8)
FASTRPC_IOCTL_MMAP = 0xc0205206              # _IOWR('R', 6, struct fastrpc_req_mmap) (32 bytes)
REMOTECTL, ADSP_LISTENER = 0, 3

WORD4, WORD8, BLOB_SEQ, OUT4, OUT8, OUT_SEQ = 'w4', 'w8', 'seq', 'o4', 'o8', 'oseq'

class Arg(ctypes.Structure):
    _fields_ = [('ptr', ctypes.c_uint64), ('length', ctypes.c_uint64), ('fd', ctypes.c_int32), ('attr', ctypes.c_uint32)]

def sc_make(method, nin, nout): return (method & 0x1f) << 24 | (nin & 0xff) << 16 | (nout & 0xff) << 8

def invoke(fd, handle, msg_id, spec, values):
    """spec: list of arg kinds; values: inputs for WORD/BLOB_SEQ (bytes)/OUT_SEQ (capacity) in spec order.
    Returns (outs list: ints for OUT4/OUT8, bytes for OUT_SEQ)."""
    prim_in = bytearray(struct.pack('<I', msg_id) if msg_id > 30 else b''); inbufs = []; out_caps = []; prim_out_fmt = '<'
    it = iter(values)
    for k in spec:
        if k == WORD4: prim_in += struct.pack('<I', next(it))
        elif k == WORD8: prim_in += struct.pack('<Q', next(it))
        elif k == BLOB_SEQ: b = next(it); prim_in += struct.pack('<I', len(b)); inbufs.append(bytes(b))
        elif k == OUT_SEQ: cap = next(it); prim_in += struct.pack('<I', cap); out_caps.append(cap)
        elif k == OUT4: prim_out_fmt += 'I'
        elif k == OUT8: prim_out_fmt += 'Q'
    ins = ([bytes(prim_in)] if prim_in else []) + inbufs
    prim_out_size = struct.calcsize(prim_out_fmt)
    outs = ([prim_out_size] if prim_out_size else []) + out_caps
    keep = [ctypes.create_string_buffer(b, len(b) or 1) for b in ins] + [ctypes.create_string_buffer(max(n, 1)) for n in outs]
    args = (Arg * max(len(keep), 1))()
    for i, (buf, n) in enumerate(zip(keep, [len(b) for b in ins] + outs)):
        args[i].ptr = ctypes.addressof(buf); args[i].length = n; args[i].fd = -1; args[i].attr = 0
    inv = struct.pack('<IIQ', handle, sc_make(min(msg_id, 31), len(ins), len(outs)), ctypes.addressof(args))
    fcntl.ioctl(fd, FASTRPC_IOCTL_INVOKE, inv)
    ob = keep[len(ins):]; res = []
    if prim_out_size: res += list(struct.unpack(prim_out_fmt, ob[0].raw[:prim_out_size])); ob = ob[1:]
    res += [b.raw[:n] for b, n in zip(ob, out_caps)]
    return res

def remote_open(fd, name):
    """remotectl open (handle 0, method 0): name -> remote handle"""
    h, dlerr, err = invoke(fd, REMOTECTL, 0, [BLOB_SEQ, OUT4, OUT_SEQ, OUT4], [name.encode() + b'\0', 256])
    if dlerr: raise RuntimeError('remotectl open %s: %s' % (name, err.split(b'\0')[0].decode(errors='replace')))
    return h

def remote_close(fd, h):
    invoke(fd, REMOTECTL, 1, [WORD4, OUT_SEQ, OUT4], [h, 0])
