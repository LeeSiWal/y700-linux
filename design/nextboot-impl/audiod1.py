"""audio-d1: one userspace GPR round trip - APM_CMD_GET_SPF_STATE (same command spf_core sends at probe; read-only)."""
import json, struct
import gprclient as g
APM_CMD_GET_SPF_STATE, APM_CMD_RSP_GET_SPF_STATE = 0x01001021, 0x02001007

def run(report):
    r = report['gpr'] = {'node': g.node()}
    cl = g.Client()
    try:
        tok = 0x5A5A0001
        r['request'] = g.pack(APM_CMD_GET_SPF_STATE, token=tok).hex()
        m, seen = cl.call(APM_CMD_GET_SPF_STATE, token=tok, secs=3.0)
        r['seen'] = [{k: (v.hex() if isinstance(v, bytes) else v) for k, v in x.items()} for x in seen]
        if m:
            r['reply_opcode'] = hex(m['opcode'])
            r['status'] = struct.unpack_from('<I', m['payload'])[0] if len(m['payload']) >= 4 else None
    finally:
        cl.close()
    print('GPR', json.dumps({k: v for k, v in r.items() if k != 'seen'}), flush=True)
    return r
