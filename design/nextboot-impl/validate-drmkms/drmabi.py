"""Minimal DRM KMS uAPI subset (drm.h / drm_mode.h, arm64) used by the render-to-display worker.
Mirrors the calls of the reviewed kms-held.c: CREATE_DUMB, MAP_DUMB, ADDFB2, GETFB2, OBJ_GETPROPERTIES, GETPROPERTY, ATOMIC."""
import ctypes as C, fcntl, struct

def iowr(nr, size): return (3 << 30) | (size << 16) | (ord('d') << 8) | nr
CREATE_DUMB = struct.Struct('<6IQ')            # height width bpp flags handle pitch | size
MAP_DUMB = struct.Struct('<I4xQ')              # handle | offset
FB_CMD2 = struct.Struct('<5I4I4I4I4x4Q')       # fb_id width height pixel_format flags handles[4] pitches[4] offsets[4] | modifier[4]
OBJ_GETPROPS = struct.Struct('<QQIII4x')       # props_ptr prop_values_ptr count_props obj_id obj_type
GETPROPERTY = struct.Struct('<QQII32sII')      # values_ptr enum_blob_ptr prop_id flags name count_values count_enum_blobs
ATOMIC = struct.Struct('<IIQQQQQQ')            # flags count_objs objs_ptr count_props_ptr props_ptr prop_values_ptr reserved user_data

IOCTL = {'CREATE_DUMB': iowr(0xB2, CREATE_DUMB.size), 'MAP_DUMB': iowr(0xB3, MAP_DUMB.size), 'ADDFB2': iowr(0xB8, FB_CMD2.size),
         'GETFB2': iowr(0xCE, FB_CMD2.size), 'OBJ_GETPROPERTIES': iowr(0xB9, OBJ_GETPROPS.size),
         'GETPROPERTY': iowr(0xAA, GETPROPERTY.size), 'ATOMIC': iowr(0xBC, ATOMIC.size)}
OBJECT_PLANE, OBJECT_CRTC, OBJECT_CONNECTOR = 0xeeeeeeee, 0xcccccccc, 0xc0c0c0c0
XRGB8888 = 0x34325258
ATOMIC_TEST_ONLY = 0x0100

def ioctl(fd, name, st, *values):
    buf = bytearray(st.pack(*values))
    fcntl.ioctl(fd, IOCTL[name], buf, True)
    return st.unpack(bytes(buf))

def properties(fd, obj_id, obj_type):
    """{name: (prop_id, value)} for one KMS object (two-call pattern, same as kms-held.c)."""
    _, _, n, _, _ = ioctl(fd, 'OBJ_GETPROPERTIES', OBJ_GETPROPS, 0, 0, 0, obj_id, obj_type)
    if n > 256: raise RuntimeError('too many properties')
    ids = (C.c_uint32 * n)(); vals = (C.c_uint64 * n)()
    _, _, n2, _, _ = ioctl(fd, 'OBJ_GETPROPERTIES', OBJ_GETPROPS, C.addressof(ids), C.addressof(vals), n, obj_id, obj_type)
    if n2 != n: raise RuntimeError('property list changed')
    out = {}
    for i in range(n):
        name = ioctl(fd, 'GETPROPERTY', GETPROPERTY, 0, 0, ids[i], 0, b'', 0, 0)[4].split(b'\0', 1)[0].decode()
        out[name] = (ids[i], vals[i])
    return out

def atomic(fd, flags, objs, props, user_data=0):
    """objs: [obj_id,...]; props: {obj_id: [(prop_id, value), ...]} in the same order as objs.
    user_data is echoed back in the FLIP_COMPLETE event when ATOMIC_PAGE_FLIP_EVENT is set."""
    o = (C.c_uint32 * len(objs))(*objs)
    counts = (C.c_uint32 * len(objs))(*[len(props[x]) for x in objs])
    flat = [p for x in objs for p in props[x]]
    ids = (C.c_uint32 * len(flat))(*[p[0] for p in flat]); vals = (C.c_uint64 * len(flat))(*[p[1] for p in flat])
    ioctl(fd, 'ATOMIC', ATOMIC, flags, len(objs), C.addressof(o), C.addressof(counts), C.addressof(ids), C.addressof(vals), 0, user_data)

ATOMIC_PAGE_FLIP_EVENT = 0x01
EVENT_FLIP_COMPLETE = 0x02
EVENT_VBLANK = struct.Struct('<IIQIIII')   # type length user_data tv_sec tv_usec sequence crtc_id

def wait_flip(fd, user_data, timeout_s=1.0):
    """Consume exactly one FLIP_COMPLETE for this commit. Retained workers never read this shared drm_file."""
    import select
    r, _, _ = select.select([fd], [], [], timeout_s)
    if not r: raise TimeoutError('flip event timeout')
    import os
    data = os.read(fd, EVENT_VBLANK.size)
    if len(data) != EVENT_VBLANK.size: raise RuntimeError('short DRM event read')
    typ, length, ud, _, _, _, crtc = EVENT_VBLANK.unpack(data)
    if (typ, length, ud, crtc) != (EVENT_FLIP_COMPLETE, EVENT_VBLANK.size, user_data, 205):
        raise RuntimeError('unexpected DRM event %r' % ((typ, length, ud, crtc),))
