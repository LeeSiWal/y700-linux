"""blescan stage: bring hci0 up (HCIDEVUP), run one 10 s LE active scan through a raw HCI socket, bring hci0 down again.
Legacy LE scan commands first; if the controller disallows them, the LE extended scan commands. No connections, no pairing,
no advertising, no writes to the controller beyond scan parameters/enable. Results stay in the bundle's result.json."""
import fcntl, select, socket, struct, time

HCIDEVUP, HCIDEVDOWN, HCIGETDEVINFO = 0x400448c9, 0x400448ca, 0x800448d3
SOL_HCI, HCI_FILTER = 0, 2

def devinfo(dev):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    try:
        buf = bytearray(struct.pack('<H', dev) + bytes(200)); fcntl.ioctl(s, HCIGETDEVINFO, buf)
        return {'name': bytes(buf[2:10]).split(b'\0')[0].decode(), 'addr': ':'.join('%02x' % x for x in reversed(buf[10:16])),
                'flags': struct.unpack_from('<I', buf, 16)[0], 'up': bool(struct.unpack_from('<I', buf, 16)[0] & 1)}
    finally: s.close()

class Hci:
    def __init__(self, dev):
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI); self.s.bind((dev,))
        self.s.setsockopt(SOL_HCI, HCI_FILTER, struct.pack('<IIIHxx', 1 << 4, 0xffffffff, 0xffffffff, 0))   # all events; struct hci_ufilter is 16 B (6.12 bt_copy_from_sockptr rejects 14)
        self.pending = []
    def cmd(self, opcode, params=b'', secs=3.0):
        self.s.send(b'\x01' + struct.pack('<HB', opcode, len(params)) + params); end = time.monotonic() + secs
        while time.monotonic() < end:
            r, _, _ = select.select([self.s], [], [], 0.1)
            if not r: continue
            p = self.s.recv(300)
            if p[:2] == b'\x04\x0e' and struct.unpack_from('<H', p, 4)[0] == opcode: return p[6]
            if p[:2] == b'\x04\x0f' and struct.unpack_from('<H', p, 5)[0] == opcode and p[3] != 0: return p[3]   # Command Status error
            self.pending.append(p)
        raise RuntimeError('no completion for opcode 0x%04x' % opcode)
    def recv(self, secs):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            r, _, _ = select.select([self.s], [], [], 0.1)
            if r: yield self.s.recv(300)

def parse_ad(data):
    out = {}; i = 0
    while i < len(data) and data[i]:
        n = data[i]; t = data[i + 1] if i + 1 < len(data) else 0; v = data[i + 2:i + 1 + n]
        if t in (0x08, 0x09): out['name'] = v.decode('utf-8', 'replace')
        if t == 0xff and len(v) >= 2: out['company'] = struct.unpack_from('<H', v)[0]
        i += 1 + n
    return out

def run(mon, report, dev=0, secs=10):
    r = report['blescan'] = {'before': devinfo(dev)}
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    up = False; seen = {}; mode = None
    try:
        t0 = time.monotonic(); fcntl.ioctl(s, HCIDEVUP, dev); up = True
        r['devup_ms'] = round((time.monotonic() - t0) * 1e3, 1); r['after_up'] = devinfo(dev); print('HCI_UP', r['after_up'], flush=True)
        mon.observe(1, quiet=True)
        h = Hci(dev)
        st = h.cmd(0x200b, struct.pack('<BHHBB', 1, 0x0030, 0x0030, 0, 0))        # legacy: active, 30 ms interval/window, public, accept all
        if st == 0:
            mode = 'legacy'; r['enable_status'] = h.cmd(0x200c, b'\x01\x00')
        else:
            r['legacy_status'] = st; mode = 'extended'
            st = h.cmd(0x2041, struct.pack('<BBBBHH', 0, 0, 0x01, 1, 0x0030, 0x0030))   # own public, accept all, 1M PHY: active 30/30 ms
            if st != 0: raise RuntimeError('extended scan parameters status 0x%02x' % st)
            r['enable_status'] = h.cmd(0x2042, struct.pack('<BBHH', 1, 0, 0, 0))
        r['mode'] = mode; print('SCAN_START mode=%s status=%s' % (mode, r['enable_status']), flush=True)
        if r['enable_status'] != 0: raise RuntimeError('scan enable status 0x%02x' % r['enable_status'])
        end = time.monotonic() + secs; reports = 0
        while time.monotonic() < end:
            for p in h.pending + list(h.recv(0.5)):
                if p[:2] != b'\x04\x3e': continue
                sub = p[3]
                if sub == 0x02:                                                  # legacy advertising report (num_reports = 1 in practice)
                    off = 5; etype, atype = p[off], p[off + 1]; addr = p[off + 2:off + 8]; dl = p[off + 8]; data = p[off + 9:off + 9 + dl]; rssi = struct.unpack('b', p[off + 9 + dl:off + 10 + dl])[0]
                elif sub == 0x0d:                                                # extended advertising report
                    off = 5; atype = p[off + 2]; addr = p[off + 3:off + 9]; rssi = struct.unpack('b', p[off + 13:off + 14])[0]; dl = p[off + 23]; data = p[off + 24:off + 24 + dl]
                else: continue
                reports += 1; key = ':'.join('%02x' % x for x in reversed(addr))
                e = seen.setdefault(key, {'addr_type': atype, 'rssi_max': rssi, 'count': 0}); e['count'] += 1; e['rssi_max'] = max(e['rssi_max'], rssi)
                e.update(parse_ad(data))
            h.pending = []; mon.tick()
        r['reports'] = reports
        r['disable_status'] = h.cmd(0x200c, b'\x00\x00') if mode == 'legacy' else h.cmd(0x2042, struct.pack('<BBHH', 0, 0, 0, 0))
        h.s.close()
    finally:
        r['devices'] = seen
        if up:
            try: fcntl.ioctl(s, HCIDEVDOWN, dev); r['after_down'] = devinfo(dev)
            except OSError as e: r['down_error'] = str(e)
        s.close()
    print('SCAN_DONE mode=%s reports=%d devices=%d named=%d' % (mode, r.get('reports', 0), len(seen), sum(1 for v in seen.values() if 'name' in v)), flush=True)
    mon.observe(3, quiet=True)
    return r
