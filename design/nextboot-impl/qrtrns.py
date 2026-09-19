"""Read-only QRTR name-service lookup: list every published (service, version, instance, node, port)."""
import select, socket, struct, time
QRTR_PORT_CTRL, NEW_SERVER, NEW_LOOKUP = 0xfffffffe, 4, 10

def lookup(secs=2.0):
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    try:
        me = _local_node(s)                          # the name service listens on our own node's control port
        s.sendto(struct.pack('<5I', NEW_LOOKUP, 0, 0, 0, 0), (me, QRTR_PORT_CTRL))
        out = []; end = time.monotonic() + secs
        while time.monotonic() < end:
            r, _, _ = select.select([s], [], [], 0.2)
            if not r: continue
            d = s.recv(64)
            cmd, svc, inst, nd, port = struct.unpack_from('<5I', d)
            if cmd != NEW_SERVER: continue
            if svc == 0 and inst == 0 and nd == 0 and port == 0: break
            out.append({'service': svc, 'version': inst & 0xff, 'instance': inst >> 8, 'node': nd, 'port': port})
        return out
    finally: s.close()

def _local_node(s):
    return s.getsockname()[0]

if __name__ == '__main__':
    import json
    for e in lookup(): print(json.dumps(e))
