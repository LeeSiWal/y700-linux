"""Minimal ALSA PCM ioctl client (tinyalsa style) - uapi <sound/asound.h> for 64-bit, kernel 6.12:
struct snd_pcm_hw_params = flags, masks[3], mres[5], intervals[12], ires[9], rmask, cmask, info, msbits, rate_num, rate_den,
fifo_size(unsigned long), sync[16], reserved[48] = 608 bytes."""
import fcntl, os, stat, struct
from pathlib import Path

HW_PARAMS_SIZE = 608
IOCTL_HW_REFINE, IOCTL_HW_PARAMS = 0xc2604110, 0xc2604111
IOCTL_HW_FREE, IOCTL_PREPARE, IOCTL_DROP = 0x4112, 0x4140, 0x4143
IOCTL_PVERSION = 0x80044100
ACCESS_RW_INTERLEAVED = 3
FORMAT = {'S16_LE': 2, 'S24_LE': 6, 'S32_LE': 10}
M_ACCESS, M_FORMAT, M_SUBFORMAT = 0, 1, 2
I_SAMPLE_BITS, I_FRAME_BITS, I_CHANNELS, I_RATE, I_PERIOD_TIME, I_PERIOD_SIZE, I_PERIOD_BYTES, I_PERIODS = range(8, 16)
I_BUFFER_TIME, I_BUFFER_SIZE, I_BUFFER_BYTES = 16, 17, 18
MASK_OFF, INTERVAL_OFF = 4, 4 + 32 * 8        # masks[3] + mres[5] = 8 masks of 32 bytes
RMASK_OFF = INTERVAL_OFF + 12 * 21            # intervals[12] + ires[9]

def node(name):
    mj, mn = (int(x) for x in Path('/sys/class/sound', name, 'dev').read_text().split(':'))
    p = Path('/dev/snd', name); p.parent.mkdir(exist_ok=True)
    if not p.exists(): os.mknod(p, stat.S_IFCHR | 0o600, os.makedev(mj, mn))
    st = os.lstat(p)
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn)): raise RuntimeError('node mismatch ' + str(p))
    return str(p), '%d:%d' % (mj, mn)

def any_params():
    b = bytearray(HW_PARAMS_SIZE)
    for i in range(3): b[MASK_OFF + 32 * i:MASK_OFF + 32 * i + 32] = b'\xff' * 32
    for i in range(12): struct.pack_into('<III', b, INTERVAL_OFF + 12 * i, 0, 0xffffffff, 0)
    struct.pack_into('<II', b, RMASK_OFF, 0xffffffff, 0)
    return b

def set_mask(b, idx, bit):
    off = MASK_OFF + 32 * (idx - 0); b[off:off + 32] = bytes(32); b[off + bit // 8] = 1 << (bit % 8)

def set_int(b, idx, v):
    struct.pack_into('<III', b, INTERVAL_OFF + 12 * (idx - 8), v, v, 1 << 2)      # integer

def get_int(b, idx):
    lo, hi, fl = struct.unpack_from('<III', b, INTERVAL_OFF + 12 * (idx - 8)); return lo, hi

def params(fmt, channels, rate, period, periods):
    b = any_params()
    set_mask(b, M_ACCESS, ACCESS_RW_INTERLEAVED); set_mask(b, M_FORMAT, FORMAT[fmt]); set_mask(b, M_SUBFORMAT, 0)
    set_int(b, I_CHANNELS, channels); set_int(b, I_RATE, rate); set_int(b, I_PERIOD_SIZE, period); set_int(b, I_PERIODS, periods)
    return b

def summary(b):
    return {n: get_int(b, i) for n, i in (('channels', I_CHANNELS), ('rate', I_RATE), ('sample_bits', I_SAMPLE_BITS),
                                          ('period_size', I_PERIOD_SIZE), ('periods', I_PERIODS), ('buffer_size', I_BUFFER_SIZE))}

def get_mask(b, idx):
    off = MASK_OFF + 32 * idx; return int.from_bytes(b[off:off + 32], 'little')

def dump(b):
    d = summary(b)
    d.update(access=hex(get_mask(b, M_ACCESS)), formats=[n for n, v in FORMAT.items() if get_mask(b, M_FORMAT) >> v & 1],
             format_mask=hex(get_mask(b, M_FORMAT)), period_bytes=get_int(b, I_PERIOD_BYTES), buffer_bytes=get_int(b, I_BUFFER_BYTES))
    return d

def loose(fmt, channels, rate):
    """Only access/format/channels/rate fixed; the kernel chooses period/buffer in HW_PARAMS (snd_pcm_hw_params_choose)."""
    b = any_params()
    set_mask(b, M_ACCESS, ACCESS_RW_INTERLEAVED); set_mask(b, M_FORMAT, FORMAT[fmt]); set_mask(b, M_SUBFORMAT, 0)
    set_int(b, I_CHANNELS, channels); set_int(b, I_RATE, rate)
    return b
