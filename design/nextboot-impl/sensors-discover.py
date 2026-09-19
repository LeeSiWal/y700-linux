#!/usr/bin/env python3
"""Read-only SSC discovery: SUID lookup per data type, then GET_ATTRIBUTES for each SUID. Nothing is enabled."""
import json, sys, time
import pb, sscclient as s

TYPES = ['accel', 'gyro', 'mag', 'ambient_light', 'proximity', 'pressure', 'hall', 'sar', 'gravity', 'rotv', 'game_rv',
         'sig_motion', 'step_detect', 'device_orient', 'amd', 'sensor_temperature', 'registry', 'ambient_temperature', 'humidity']

def main():
    cl = s.Client(); out = {'ssc_addr': cl.addr, 'types': {}}
    try:
        for dt in TYPES:
            r = cl.control(s.request(s.SUID_SENSOR, s.MSG_SUID_REQ, pb.f_bytes(1, dt.encode()) + pb.f_varint(2, 0)))
            uids = []
            for uid, evs in cl.events(1.0):
                for mid, ts, msg in evs:
                    if mid == s.MSG_SUID_RESP:
                        d = pb.decode(msg); t = [v for f, _, v in d if f == 1]
                        if t and t[0].decode() == dt:
                            for ub in [v for f, _, v in d if f == 2]:
                                u = pb.decode(ub); uids.append((u[0][2], u[1][2]))
            out['types'][dt] = {'control': r, 'suids': ['%016x:%016x' % (h, l) for l, h in uids], 'attrs': []}
            for uid in uids[:4]:
                cl.control(s.request(uid, s.MSG_ATTR_REQ, b''))
                for u2, evs in cl.events(1.0):
                    for mid, ts, msg in evs:
                        if u2 == uid and mid == s.MSG_ATTR_RESP: out['types'][dt]['attrs'].append(s.parse_attrs(msg))
            found = out['types'][dt]
            print('%-20s suids=%d %s' % (dt, len(found['suids']), json.dumps([{k: a.get(k) for k in ('name', 'vendor', 'type', 'available', 'rates', 'stream_type', 'physical')} for a in found['attrs']])[:300]), flush=True)
    finally: cl.close()
    json.dump(out, open(sys.argv[1] if len(sys.argv) > 1 else '/dev/null', 'w'), indent=1, default=str)

if __name__ == '__main__': main()
