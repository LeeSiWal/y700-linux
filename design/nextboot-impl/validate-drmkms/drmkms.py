"""KMS uAPI additions for display-owner (arm64): VERSION, SET_MASTER, SET_CLIENT_CAP, GETPLANERESOURCES, GETPLANE,
GETPROPBLOB, CREATEPROPBLOB and struct drm_mode_modeinfo. Same calls as the reviewed screen-small kms-held.c."""
import ctypes as C, fcntl, struct
from drmabi import iowr, ioctl, IOCTL, properties, atomic, CREATE_DUMB, MAP_DUMB, FB_CMD2, XRGB8888, ATOMIC_TEST_ONLY, \
    OBJECT_PLANE, OBJECT_CRTC, OBJECT_CONNECTOR

def iow(nr, size): return (1 << 30) | (size << 16) | (ord('d') << 8) | nr
def io(nr): return (ord('d') << 8) | nr
VERSION = struct.Struct('<iii4xQQQQQQ')        # major minor patch | name_len name date_len date desc_len desc
CAP = struct.Struct('<QQ')                      # capability value
PLANE_RES = struct.Struct('<QI4x')              # plane_id_ptr count_planes
GETPLANE = struct.Struct('<6IQ')                # plane_id crtc_id fb_id possible_crtcs gamma_size count_format_types | format_type_ptr
GETBLOB = struct.Struct('<IIQ')                 # blob_id length data
CREATEBLOB = struct.Struct('<QII')              # data length blob_id
MODEINFO = struct.Struct('<I10H3I32s')          # clock hdisplay hsync_start hsync_end htotal hskew vdisplay vsync_start vsync_end vtotal vscan vrefresh flags type name
IOCTL.update({'VERSION': iowr(0x00, VERSION.size), 'GET_CAP': iowr(0x0c, CAP.size), 'SET_CLIENT_CAP': iow(0x0d, CAP.size),
              'SET_MASTER': io(0x1e), 'GETPLANERESOURCES': iowr(0xB5, PLANE_RES.size), 'GETPLANE': iowr(0xB6, GETPLANE.size),
              'GETPROPBLOB': iowr(0xAC, GETBLOB.size), 'CREATEPROPBLOB': iowr(0xBD, CREATEBLOB.size)})
CAP_DUMB_BUFFER, CLIENT_CAP_ATOMIC = 1, 3
ATOMIC_ALLOW_MODESET = 0x0400
FIELDS = ('clock', 'hdisplay', 'hsync_start', 'hsync_end', 'htotal', 'hskew', 'vdisplay', 'vsync_start', 'vsync_end', 'vtotal',
          'vscan', 'vrefresh', 'flags', 'type')
REVIEWED_TIMING = {'clock': 915552, 'hdisplay': 1904, 'hsync_start': 2084, 'hsync_end': 2116, 'htotal': 2244, 'vdisplay': 3040,
                   'vsync_start': 3066, 'vsync_end': 3070, 'vtotal': 3400, 'vrefresh': 120, 'flags': 0}

def driver_name(fd):
    name = C.create_string_buffer(128)
    r = ioctl(fd, 'VERSION', VERSION, 0, 0, 0, 127, C.addressof(name), 0, 0, 0, 0)
    return name.value.decode()

def get_cap(fd, cap): return ioctl(fd, 'GET_CAP', CAP, cap, 0)[1]
def set_client_cap(fd, cap, value):
    fcntl.ioctl(fd, IOCTL['SET_CLIENT_CAP'], CAP.pack(cap, value))
def set_master(fd): fcntl.ioctl(fd, IOCTL['SET_MASTER'], 0)

def plane_ids(fd):
    _, n = ioctl(fd, 'GETPLANERESOURCES', PLANE_RES, 0, 0)
    if n > 64: raise RuntimeError('plane capacity')
    ids = (C.c_uint32 * n)(); _, n2 = ioctl(fd, 'GETPLANERESOURCES', PLANE_RES, C.addressof(ids), n)
    if n2 != n: raise RuntimeError('plane list changed')
    return list(ids)

def get_plane(fd, pid):
    r = ioctl(fd, 'GETPLANE', GETPLANE, pid, 0, 0, 0, 0, 0, 0)
    return {'plane_id': r[0], 'crtc_id': r[1], 'fb_id': r[2], 'possible_crtcs': r[3]}

def read_mode(fd, blob_id):
    buf = C.create_string_buffer(MODEINFO.size)
    _, length, _ = ioctl(fd, 'GETPROPBLOB', GETBLOB, blob_id, MODEINFO.size, C.addressof(buf))
    if length != MODEINFO.size: raise RuntimeError('mode blob size %d' % length)
    return buf.raw

def mode_fields(raw):
    v = MODEINFO.unpack(raw); d = dict(zip(FIELDS, v[:14])); d['name'] = v[14].split(b'\0', 1)[0].decode(errors='replace'); return d

def create_blob(fd, raw):
    buf = C.create_string_buffer(raw, len(raw))
    return ioctl(fd, 'CREATEPROPBLOB', CREATEBLOB, C.addressof(buf), len(raw), 0)[2]

def dumb_fb(fd, w, h):
    """Cached dumb buffer + XR24 framebuffer (same path as the reviewed workers). Returns dict with fb, handle, pitch, size, map."""
    import mmap
    _, _, _, _, handle, pitch, size = ioctl(fd, 'CREATE_DUMB', CREATE_DUMB, h, w, 32, 0, 0, 0, 0)
    _, off = ioctl(fd, 'MAP_DUMB', MAP_DUMB, handle, 0)
    m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=off)
    return {'handle': handle, 'pitch': pitch, 'size': size, 'map': m, 'addr': C.addressof(C.c_char.from_buffer(m))}

def add_fb(fd, w, h, handle, pitch):
    fb = ioctl(fd, 'ADDFB2', FB_CMD2, 0, w, h, XRGB8888, 0, handle, 0, 0, 0, pitch, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)[0]
    g = ioctl(fd, 'GETFB2', FB_CMD2, fb, *([0] * 20))
    if not (g[1:4] == (w, h, XRGB8888) and g[9] == pitch and g[13] == 0 and g[17] == 0): raise RuntimeError('framebuffer readback %r' % (g,))
    return fb
