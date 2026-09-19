"""SSC (Snapdragon Sensor Core) client over QRTR/QMI, following libqmi qmi-service-ssc.json and libssc protos.
QMI service 400 (0x190). Control 0x0020: TLV 0x01 data(u16 len), 0x10 report type(u8, 1 = large).
Response TLVs 0x02 result, 0x10 client id(u64), 0x11 response(u32). Indications 0x0021/0x0022: 0x01 client id, 0x02 data."""
import select, socket, struct, time
import pb, qrtrns

SVC_SSC = 400
SUID_SENSOR = (0xABABABABABABABAB, 0xABABABABABABABAB)        # (low, high)
MSG_SUID_REQ, MSG_SUID_RESP = 512, 768
MSG_ATTR_REQ, MSG_ATTR_RESP = 1, 128
MSG_ENABLE_CONT, MSG_ENABLE_ONCHANGE, MSG_CONFIG_RESP, MSG_DISABLE = 513, 514, 768, 10
MSG_REPORT, MSG_REPORT_PROX = 1025, 769
ATTR = {0: 'name', 1: 'vendor', 2: 'type', 3: 'available', 4: 'version', 6: 'rates', 11: 'ranges', 16: 'stream_type', 18: 'hw_id', 20: 'mount_matrix', 21: 'physical'}

def tlv(t, v): return struct.pack('<BH', t, len(v)) + v
def tlvs(b):
    out, i = {}, 0
    while i + 3 <= len(b):
        t, n = struct.unpack_from('<BH', b, i); out[t] = b[i + 3:i + 3 + n]; i += 3 + n
    return out

def request(uid, msg_id, body, passive=False):
    low, high = uid
    return (pb.f_bytes(1, pb.f_fixed64(1, low) + pb.f_fixed64(2, high)) + pb.f_fixed32(2, msg_id) +
            pb.f_bytes(3, pb.f_varint(1, 1) + pb.f_varint(2, 0)) +
            pb.f_bytes(4, pb.f_bytes(2, body) + (pb.f_varint(3, 1) if passive else b'')))

def parse_event(data):
    """SscClientResponse -> (uid(low,high), [(msg_id, timestamp, msg bytes)])"""
    uidb = pb.fields(data, 1)[0]; u = pb.decode(uidb); uid = (u[0][2], u[1][2])
    evs = []
    for body in pb.fields(data, 2):
        d = {f: v for f, wt, v in pb.decode(body)}; evs.append((d.get(1), d.get(2), d.get(3, b'')))
    return uid, evs

def parse_attrs(msg):
    out = {}
    for a in pb.fields(msg, 1):
        d = pb.decode(a); aid = [v for f, _, v in d if f == 1][0]; arr = [v for f, _, v in d if f == 2]
        vals = []
        for av in (pb.fields(arr[0], 1) if arr else []):
            for f, wt, v in pb.decode(av):
                vals.append(v.decode(errors='replace') if f == 2 else pb.f32(v) if f == 3 else v if f in (4, 5) else ('array', v.hex()[:40]))
        out[ATTR.get(aid, aid)] = vals if len(vals) != 1 else vals[0]
    return out

class Client:
    def __init__(self):
        svc = [s for s in qrtrns.lookup() if s['service'] == SVC_SSC]
        if len(svc) != 1: raise RuntimeError('SSC service not unique: %r' % svc)
        self.addr = (svc[0]['node'], svc[0]['port']); self.s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM); self.txn = 0
        self.inds = []
    def close(self): self.s.close()
    def control(self, proto, timeout=3.0):
        self.txn += 1; body = tlv(0x01, struct.pack('<H', len(proto)) + proto) + tlv(0x10, b'\x01')
        self.s.sendto(struct.pack('<BHHH', 0, self.txn, 0x20, len(body)) + body, self.addr); end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self.s], [], [], 0.1)
            if not r: continue
            d = self.s.recv(65536); typ, txn, mid, ln = struct.unpack_from('<BHHH', d); t = tlvs(d[7:7 + ln])
            if typ == 2 and txn == self.txn:
                res = struct.unpack('<HH', t[2]) if 2 in t else None
                return {'result': res, 'client_id': struct.unpack('<Q', t[0x10])[0] if 0x10 in t else None,
                        'response': struct.unpack('<I', t[0x11])[0] if 0x11 in t else None}
            if typ == 4: self.inds.append(t)
        raise RuntimeError('no QMI response')
    def events(self, secs):
        end = time.monotonic() + secs; out = []
        for t in self.inds: out.append(t)
        self.inds = []
        while time.monotonic() < end:
            r, _, _ = select.select([self.s], [], [], 0.1)
            if not r: continue
            d = self.s.recv(65536); typ, txn, mid, ln = struct.unpack_from('<BHHH', d)
            if typ == 4: out.append(tlvs(d[7:7 + ln]))
        res = []
        for t in out:
            if 2 in t:
                n = struct.unpack_from('<H', t[2])[0]; res.append(parse_event(t[2][2:2 + n]))
        return res
