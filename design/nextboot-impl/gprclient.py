"""Minimal userspace GPR client over /dev/aud_pasthru_adsp (vendor audio-pkt). Packet layout from audio-kernel-ar
ipc/gpr-lite.h: header word (version 0 | header words <<4 | packet bytes <<8), dst/src domain, client_data, reserved,
src_port, dst_port, token, opcode, payload. Replies addressed to an unregistered port are delivered to audio-pkt."""
import os, select, stat, struct, time
from pathlib import Path

NODE, SYSDEV = '/dev/aud_pasthru_adsp', '/sys/class/aud_pasthru_adsp/aud_pasthru_adsp/dev'
DOMAIN_ADSP, DOMAIN_APPS = 2, 3
APM_PORT = 0x00000001                       # APM_MODULE_INSTANCE_ID
SRC_PORT = 0x2001                           # unregistered in gpr-lite -> routed to audio-pkt (passthrough)
HDR = struct.Struct('<IBBBBIIII')           # 24 bytes = sizeof(struct gpr_pkt), header words 6
GPR_IBASIC_RSP_RESULT = 0x02001005

def node():
    mj, mn = (int(x) for x in Path(SYSDEV).read_text().split(':')); p = Path(NODE)
    if not p.exists(): os.mknod(p, stat.S_IFCHR | 0o600, os.makedev(mj, mn))
    st = os.lstat(p)
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn)): raise RuntimeError('node mismatch ' + NODE)
    return '%d:%d' % (mj, mn)

def pack(opcode, payload=b'', token=0, dst_port=APM_PORT):
    size = HDR.size + len(payload)
    return HDR.pack(0 | (HDR.size // 4) << 4 | size << 8, DOMAIN_ADSP, DOMAIN_APPS, 0, 0, SRC_PORT, dst_port, token, opcode) + payload

def unpack(pkt):
    h = HDR.unpack_from(pkt)
    hdr_bytes = ((h[0] >> 4) & 0xf) * 4; size = h[0] >> 8
    return {'size': size, 'hdr_bytes': hdr_bytes, 'dst_domain': h[1], 'src_domain': h[2], 'src_port': h[5], 'dst_port': h[6],
            'token': h[7], 'opcode': h[8], 'payload': pkt[hdr_bytes:size]}

class Client:
    def __init__(self): self.fd = os.open(NODE, os.O_RDWR | os.O_CLOEXEC)
    def close(self): os.close(self.fd)
    def send(self, pkt): return os.write(self.fd, pkt)
    def recv(self, secs):
        r, _, _ = select.select([self.fd], [], [], secs)
        return unpack(os.read(self.fd, 4096)) if r else None
    def call(self, opcode, payload=b'', token=0, dst_port=APM_PORT, secs=2.0):
        self.send(pack(opcode, payload, token, dst_port)); end = time.monotonic() + secs; seen = []
        while time.monotonic() < end:
            m = self.recv(max(0.0, end - time.monotonic()))
            if m is None: break
            seen.append(m)
            if m['token'] == token: return m, seen
        return None, seen
