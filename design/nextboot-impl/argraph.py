"""AudioReach (SPF) APM packet builder for a minimal playback graph, ported from upstream Linux v6.12
sound/soc/qcom/qdsp6/audioreach.{c,h}, q6apm.c and AudioReach graphservices spf/api headers (ref/ in audio-20260919).

Graph (two sub-graphs, like the upstream stream/device split):
  SG 0x7001 (stream, RX, playback)  container 0x7001 (PP, STREAM):  WR_SHARED_MEM_EP 0x7101 --o1->i2--> PCM_CNV 0x7102
  SG 0x7002 (device, RX, playback)  container 0x7002 (EP, GLOBAL_DEV): I2S_SINK 0x7103
  PCM_CNV 0x7102 --o1->i2--> I2S_SINK 0x7103
Everything here only builds bytes; sending is done by gprclient."""
import struct

# ---- ids (audioreach.h / spf api) ----
APM_IID = 0x00000001
APM_CMD_GRAPH_OPEN, APM_CMD_GRAPH_PREPARE, APM_CMD_GRAPH_START = 0x01001000, 0x01001001, 0x01001002
APM_CMD_GRAPH_STOP, APM_CMD_GRAPH_CLOSE, APM_CMD_SET_CFG = 0x01001003, 0x01001004, 0x01001006
APM_CMD_SHARED_MEM_MAP_REGIONS, APM_CMD_SHARED_MEM_UNMAP_REGIONS = 0x0100100C, 0x0100100D
APM_CMD_RSP_SHARED_MEM_MAP_REGIONS, GPR_IBASIC_RSP_RESULT = 0x02001001, 0x02001005
DATA_CMD_WR_SH_MEM_EP_DATA_BUFFER_V2, DATA_CMD_RSP_WR_SH_MEM_EP_DATA_BUFFER_DONE_V2 = 0x0400100A, 0x05001004
DATA_CMD_WR_SH_MEM_EP_EOS, DATA_CMD_WR_SH_MEM_EP_EOS_RENDERED = 0x04001002, 0x05001001
MODULE_ID_WR_SHARED_MEM_EP, MODULE_ID_PCM_CNV, MODULE_ID_I2S_SINK = 0x07001000, 0x07001003, 0x0700100A
APM_PARAM_ID_CONTAINER_CONFIG, APM_PARAM_ID_SUB_GRAPH_CONFIG = 0x08001000, 0x08001001
APM_PARAM_ID_MODULE_LIST, APM_PARAM_ID_MODULE_PROP, APM_PARAM_ID_MODULE_CONN = 0x08001002, 0x08001003, 0x08001004
APM_PARAM_ID_SUB_GRAPH_LIST = 0x08001005
PARAM_ID_PCM_OUTPUT_FORMAT_CFG, PARAM_ID_MEDIA_FORMAT = 0x08001008, 0x0800100C
PARAM_ID_I2S_INTF_CFG, PARAM_ID_HW_EP_MF_CFG, PARAM_ID_HW_EP_FRAME_SIZE_FACTOR = 0x08001019, 0x08001017, 0x08001018
SG_PROP_PERF, SG_PROP_DIR, SG_PROP_SID = 0x0800100E, 0x0800100F, 0x08001010
CONT_PROP_CAP, CONT_PROP_POS, CONT_PROP_STACK, CONT_PROP_DOMAIN = 0x08001011, 0x08001012, 0x08001013, 0x08001014
MOD_PROP_PORT_INFO = 0x08001015
PERF_LOW_POWER, DIR_RX, SID_PLAYBACK = 1, 2, 1
CAP_PP, CAP_EP = 1, 3                                        # legacy capability ids (upstream v6.12)
TYPE_SC, TYPE_GC = 0x0B001000, 0x0B001001                   # apm_graph_properties.h container types (what this ACDB uses)
CONT_PROP_HEAP, CONT_PROP_PARENT, CONT_PROP_PEER_HEAP = 0x08001174, 0x080010CB, 0x0800124D
HEAP_DEFAULT, NO_PARENT = 1, 0xFFFFFFFF
POS_STREAM, POS_GLOBAL_DEV = 1, 4
DOMAIN_ADSP = 2
MEDIA_FMT_ID_PCM, DATA_FORMAT_FIXED_POINT = 0x09001000, 1
PCM_LSB_ALIGNED, PCM_LITTLE_ENDIAN = 1, 1
PCM_INTERLEAVED, PCM_DEINTERLEAVED_UNPACKED = 1, 3
PCM_CHANNEL_FL, PCM_CHANNEL_FR = 1, 2
LPAIF, I2S_PRIMARY, I2S_SD0, I2S_SD1, WS_INTERNAL = 0, 0, 1, 2, 1
SPEAKER_SD = I2S_SD1                      # 9cffcffd (3a1d6dac): only the SD1 segment was heard on the speakers
MMAP_POOL_SHMEM8_4K, MMAP_OFFSET_MODE = 3, 0x4                   # apm_memmap_api.h: IS_OFFSET_MODE bit 2
OUT_PORT, IN_PORT = 1, 2

# id ranges as in QRD_acdb_cal.acdb: sub-graphs 0xB00000xx, containers 0xE00000xx, module instances 0x40xx
SG_STREAM, SG_DEVICE = 0xB0007001, 0xB0007002
CONT_STREAM, CONT_DEVICE = 0xE0007001, 0xE0007002
IID_SHM, IID_CNV, IID_I2S = 0x4F01, 0x4F02, 0x4F03

def align(n, a=8): return (n + a - 1) // a * a
def pad(b, a=8): return b + bytes(align(len(b), a) - len(b))

def param(iid, pid, data):
    """apm_module_param_data (iid, pid, size, error_code) + data padded to 8; like upstream (ALIGN(struct_size, 8) -
    APM_MODULE_PARAM_DATA_SIZE) the param_size includes the padding."""
    data = pad(data)
    return struct.pack('<IIII', iid, pid, len(data), 0) + data

def apm_cmd(payload):
    """apm_cmd_header (payload in-band: address 0/0, map handle 0, size) + payload."""
    return struct.pack('<IIII', 0, 0, 0, len(payload)) + payload

def apm_cmd_oob(offset, handle, size):
    """apm_cmd_header for an out-of-band payload in mapped shared memory (offset mode; 32-byte aligned); SPF writes the
    per-parameter error_code back into the payload, which is how a failing parameter is identified."""
    assert offset % 32 == 0
    return struct.pack('<IIII', offset, 0, handle, size)

def param_errors(payload):
    out, i = [], 0
    while i + 16 <= len(payload):
        iid, pid, size, err = struct.unpack_from('<IIII', payload, i); out.append((iid, pid, err)); i += 16 + size
    return out

# ---- graph description ----
GRAPH = {
    'subgraphs': [
        {'id': SG_STREAM, 'perf': PERF_LOW_POWER, 'dir': DIR_RX, 'sid': SID_PLAYBACK,
         'containers': [{'id': CONT_STREAM, 'type': TYPE_GC, 'pos': POS_STREAM, 'stack': 8192,
                         'modules': [(MODULE_ID_WR_SHARED_MEM_EP, IID_SHM, 0, 1), (MODULE_ID_PCM_CNV, IID_CNV, 1, 1)]}]},
        {'id': SG_DEVICE, 'perf': PERF_LOW_POWER, 'dir': DIR_RX, 'sid': SID_PLAYBACK,
         'containers': [{'id': CONT_DEVICE, 'type': TYPE_GC, 'pos': POS_GLOBAL_DEV, 'stack': 8192,
                         'modules': [(MODULE_ID_I2S_SINK, IID_I2S, 1, 0)]}]}],
    'connections': [(IID_SHM, OUT_PORT, IID_CNV, IN_PORT), (IID_CNV, OUT_PORT, IID_I2S, IN_PORT)]}

def graph_open_payload(g=GRAPH):
    sgs = g['subgraphs']; conts = [(sg['id'], c) for sg in sgs for c in sg['containers']]
    mods = [m for _, c in conts for m in c['modules']]
    sg = struct.pack('<I', len(sgs)) + b''.join(
        struct.pack('<II', s['id'], 3) + struct.pack('<III', SG_PROP_PERF, 4, s['perf']) +
        struct.pack('<III', SG_PROP_DIR, 4, s['dir']) + struct.pack('<III', SG_PROP_SID, 4, s['sid']) for s in sgs)
    # 7 properties in the same order as the ACDB containers: type {version 1, type id}, graph pos, stack, domain, heap,
    # parent container (none), peer heap
    ct = struct.pack('<I', len(conts)) + b''.join(
        struct.pack('<II', c['id'], 7) + struct.pack('<IIII', CONT_PROP_CAP, 8, 1, c['type']) +
        struct.pack('<III', CONT_PROP_POS, 4, c['pos']) + struct.pack('<III', CONT_PROP_STACK, 4, c['stack']) +
        struct.pack('<III', CONT_PROP_DOMAIN, 4, DOMAIN_ADSP) + struct.pack('<III', CONT_PROP_HEAP, 4, HEAP_DEFAULT) +
        struct.pack('<III', CONT_PROP_PARENT, 4, NO_PARENT) + struct.pack('<III', CONT_PROP_PEER_HEAP, 4, HEAP_DEFAULT) for _, c in conts)
    ml = struct.pack('<I', len(conts)) + b''.join(
        struct.pack('<III', sgid, c['id'], len(c['modules'])) + b''.join(struct.pack('<II', mid, iid) for mid, iid, _, _ in c['modules'])
        for sgid, c in conts)
    mp = struct.pack('<I', len(mods)) + b''.join(
        struct.pack('<II', iid, 1) + struct.pack('<IIII', MOD_PROP_PORT_INFO, 8, nin, nout) for _, iid, nin, nout in mods)
    mc = struct.pack('<I', len(g['connections'])) + b''.join(struct.pack('<IIII', *cn) for cn in g['connections'])
    return (param(APM_IID, APM_PARAM_ID_SUB_GRAPH_CONFIG, sg) + param(APM_IID, APM_PARAM_ID_CONTAINER_CONFIG, ct) +
            param(APM_IID, APM_PARAM_ID_MODULE_LIST, ml) + param(APM_IID, APM_PARAM_ID_MODULE_PROP, mp) +
            param(APM_IID, APM_PARAM_ID_MODULE_CONN, mc))

def mgmt_payload(sg_ids):
    return param(APM_IID, APM_PARAM_ID_SUB_GRAPH_LIST, struct.pack('<I', len(sg_ids)) + b''.join(struct.pack('<I', s) for s in sg_ids))

def pcm_media_fmt(rate, bits, ch):
    """media_format header + payload_media_fmt_pcm (PARAM_ID_MEDIA_FORMAT, shared memory EP input)."""
    body = struct.pack('<IHHHHHH', rate, bits, PCM_LSB_ALIGNED, bits, bits - 1, PCM_LITTLE_ENDIAN, ch) + bytes([PCM_CHANNEL_FL, PCM_CHANNEL_FR][:ch])
    body = pad(body, 4)
    return struct.pack('<III', DATA_FORMAT_FIXED_POINT, MEDIA_FMT_ID_PCM, len(body)) + body

def pcm_out_fmt(bits, ch, interleave):
    body = struct.pack('<HHHHHHHH', bits, PCM_LSB_ALIGNED, bits, bits - 1, PCM_LITTLE_ENDIAN, interleave, 0, ch) + bytes([PCM_CHANNEL_FL, PCM_CHANNEL_FR][:ch])
    body = pad(body, 4)
    return struct.pack('<III', DATA_FORMAT_FIXED_POINT, MEDIA_FMT_ID_PCM, len(body)) + body

def set_cfg_payload(rate=48000, bits=16, ch=2, interleave=PCM_DEINTERLEAVED_UNPACKED, sd_line=SPEAKER_SD, ws_src=WS_INTERNAL, dev_bits=None):
    """bits: shared-memory input sample width; dev_bits: PCM converter output and I2S endpoint width (default = bits)."""
    dev_bits = dev_bits or bits
    return (param(IID_SHM, PARAM_ID_MEDIA_FORMAT, pcm_media_fmt(rate, bits, ch)) +
            param(IID_CNV, PARAM_ID_PCM_OUTPUT_FORMAT_CFG, pcm_out_fmt(dev_bits, ch, interleave)) +
            param(IID_I2S, PARAM_ID_I2S_INTF_CFG, struct.pack('<IIHH', LPAIF, I2S_PRIMARY, sd_line, ws_src)) +
            param(IID_I2S, PARAM_ID_HW_EP_MF_CFG, struct.pack('<IHHI', rate, dev_bits, ch, DATA_FORMAT_FIXED_POINT)) +
            param(IID_I2S, PARAM_ID_HW_EP_FRAME_SIZE_FACTOR, struct.pack('<I', 1)))

def mmap_payload(fd, size):
    return struct.pack('<HHI', MMAP_POOL_SHMEM8_4K, 1, MMAP_OFFSET_MODE) + struct.pack('<III', fd, 0, align(size, 4096))

def wr_buffer(offset, handle, size, flags=0):
    return struct.pack('<IIIIIII', offset, 0, handle, size, 0, 0, flags) + struct.pack('<IIII', 0, 0, 0, 0)

def sine_s16_stereo(freq, secs, rate=48000, dbfs=-30.0):
    import math
    amp = int(32767 * 10 ** (dbfs / 20)); n = int(secs * rate); out = bytearray(n * 4)
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * freq * i / rate)); struct.pack_into('<hh', out, i * 4, v, v)
    return bytes(out), amp

def pulses_s16_stereo(freq, n, on, off, rate=48000, dbfs=-18.0):
    """n beeps of `on` seconds separated by `off` seconds of silence (5 ms ramps to avoid clicks)."""
    import math
    amp = int(32767 * 10 ** (dbfs / 20)); per_on, per_off = int(on * rate), int(off * rate); out = bytearray()
    ramp = int(0.005 * rate)
    for _ in range(n):
        for i in range(per_on):
            g = min(1.0, i / ramp, (per_on - 1 - i) / ramp)
            v = int(amp * g * math.sin(2 * math.pi * freq * i / rate)); out += struct.pack('<hh', v, v)
        out += bytes(per_off * 4)
    return bytes(out), amp
