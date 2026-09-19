#!/usr/bin/env python3
"""pdmapper: minimal Qualcomm servreg locator (PD mapper) on QRTR, the job of Android's userspace pd-mapper.
Publishes SERVREG_LOCATOR (service 0x40, version 1) as instance 0 and 1 and answers GET_DOMAIN_LIST (msg 0x21) from the
reviewed *.jsn files in this bundle (adspr/adspua/adsps: msm/adsp/{root,audio,sensor}_pd, instance 74). Answers only
lookups; never sends anything else. Every request/answer is logged. Runs for the whole boot (like the other keepers).
Usage: pdmapper.py JSN..."""
import json, os, select, signal, socket, struct, sys, time

SERVICE, VERSION, INSTANCES = 0x40, 1, (0, 1)
QRTR_PORT_CTRL, NEW_SERVER = 0xfffffffe, 4
GET_DOMAIN_LIST = 0x21

def out(*a): print(*a, flush=True)

def load(paths):
    table = {}                                                   # 'provider/service' -> [(domain, instance, valid, data)]
    for p in paths:
        j = json.load(open(p)); d = j['sr_domain']
        dom = '%s/%s/%s' % (d['soc'], d['domain'], d['subdomain'])
        for s in j['sr_service']:
            table.setdefault('%s/%s' % (s['provider'], s['service']), []).append(
                (dom, int(d['qmi_instance_id']), int(s.get('service_data_valid', 0)), int(s.get('service_data', 0))))
    return table

def tlvs(buf):
    out_, i = {}, 0
    while i + 3 <= len(buf):
        t, n = buf[i], struct.unpack_from('<H', buf, i + 1)[0]; out_[t] = buf[i + 3:i + 3 + n]; i += 3 + n
    return out_

def tlv(t, v): return struct.pack('<BH', t, len(v)) + v

def answer(req, table):
    t = tlvs(req)
    name = t.get(0x01, b'').split(b'\0')[0].decode(errors='replace')
    offset = struct.unpack('<I', t[0x10])[0] if 0x10 in t and len(t[0x10]) == 4 else 0
    doms = table.get(name, [])
    part = doms[offset:offset + 32]
    lst = bytes([len(part)]) + b''.join(bytes([len(dn)]) + dn.encode() + struct.pack('<IBI', inst, valid, data) for dn, inst, valid, data in part)
    body = tlv(0x02, struct.pack('<HH', 0, 0)) + tlv(0x10, struct.pack('<H', len(doms))) + tlv(0x11, struct.pack('<H', 1)) + tlv(0x12, lst)
    return name, offset, part, body

def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    table = load(sys.argv[1:]); out('TABLE ' + json.dumps(table))
    socks = []
    for inst in INSTANCES:
        s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
        me = s.getsockname()[0]
        s.sendto(struct.pack('<5I', NEW_SERVER, SERVICE, VERSION | inst << 8, 0, 0), (me, QRTR_PORT_CTRL))
        out('PUBLISHED service=0x%x version=%d instance=%d addr=%r' % (SERVICE, VERSION, inst, s.getsockname())); socks.append(s)
    out('READY')
    while True:
        r, _, _ = select.select(socks, [], [])
        for s in r:
            data, addr = s.recvfrom(4096)
            if len(data) < 7: out('SHORT %r %s' % (addr, data.hex())); continue
            typ, txn, mid, mlen = struct.unpack_from('<BHHH', data)
            if typ != 0 or mid != GET_DOMAIN_LIST:
                out('IGNORED from=%r type=%d msg=0x%x %s' % (addr, typ, mid, data[:48].hex())); continue
            name, offset, part, body = answer(data[7:7 + mlen], table)
            s.sendto(struct.pack('<BHHH', 2, txn, mid, len(body)) + body, addr)
            out('ANSWER t=%.3f from=%r name=%s offset=%d domains=%s' % (time.monotonic(), addr, name, offset, json.dumps([p[0] for p in part])))

if __name__ == '__main__':
    try: main()
    except BaseException as exc:
        out('STOP: exception %r' % (exc,))
        while True: time.sleep(1)
