#!/usr/bin/env python3
"""Read-only: which DRM format lists contain duplicates (weston 14 asserts on a duplicate)? For every plane: GETPLANE
format list and the IN_FORMATS blob (formats[] + libdrm iterator order); for every writeback connector: the
WRITEBACK_PIXEL_FORMATS blob. Uses a borrowed duplicate of the owner's DRM file; no commit. Usage: sudo python3 -B format-probe.py"""
import ctypes as C, collections, json, os, struct, sys
from pathlib import Path
IMPL = Path('/home/siwal/y700-design/nextboot-impl'); sys.path.insert(0, str(IMPL))
import importlib.util, registry as rg, borrow_owner as bo, drmkms as k, drmabi
spec = importlib.util.spec_from_file_location('readers', IMPL/'runtime-readers.py'); c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
GETPLANE = struct.Struct('<6IQ')
def fourcc(v): return struct.pack('<I', v).decode('latin1')
def blob(fd, bid):
    _, n, _ = k.ioctl(fd, 'GETPROPBLOB', k.GETBLOB, bid, 0, 0); buf = C.create_string_buffer(n)
    k.ioctl(fd, 'GETPROPBLOB', k.GETBLOB, bid, n, C.addressof(buf)); return buf.raw
def dups(lst): return {fourcc(f): n for f, n in collections.Counter(lst).items() if n > 1}
def main():
    c.need(os.geteuid() == 0, 'sudo'); boot = c.read('/proc/sys/kernel/random/boot_id').strip(); reg, _ = rg.load(c, boot)
    fd = bo.borrow_fd(c, reg); out = {'planes': {}, 'writeback': {}}
    try:
        for pid in k.plane_ids(fd):
            r = k.ioctl(fd, 'GETPLANE', k.GETPLANE, pid, 0, 0, 0, 0, 0, 0); n = r[5]
            arr = (C.c_uint32 * max(n, 1))(); k.ioctl(fd, 'GETPLANE', k.GETPLANE, pid, 0, 0, 0, 0, n, C.addressof(arr))
            props = k.properties(fd, pid, k.OBJECT_PLANE); rec = {'n_formats': n, 'dup_plane_list': dups(arr[:n])}
            if 'IN_FORMATS' in props and props['IN_FORMATS'][1]:
                b = blob(fd, props['IN_FORMATS'][1]); ver, flags, nf, fo, nm, mo = struct.unpack_from('<6I', b)
                fm = list(struct.unpack_from('<%dI' % nf, b, fo)); mods = [struct.unpack_from('<QII Q'.replace(' ', ''), b, mo + 24 * i) for i in range(nm)]
                seq = [fm[fi] for fi in range(nf) for (bits, off, _, m) in mods if off <= fi < off + 64 and bits >> (fi - off) & 1]
                runs = [f for i, f in enumerate(seq) if i == 0 or seq[i - 1] != f]
                rec.update(in_formats={'n_formats': nf, 'n_modifiers': nm, 'dup_formats_array': dups(fm), 'dup_runs_format_major': dups(runs)})
            out['planes'][pid] = rec
        _, _, _, _, nfb, ncrtc, nconn, nenc, *_ = k.ioctl(fd, 'GETRESOURCES', drmabi.CARD_RES, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) if hasattr(k, 'GETRESOURCES') else drmabi.ioctl(fd, 'GETRESOURCES', drmabi.CARD_RES, *([0] * 12))
        ids = (C.c_uint32 * nconn)(); drmabi.ioctl(fd, 'GETRESOURCES', drmabi.CARD_RES, 0, 0, C.addressof(ids), 0, 0, 0, nconn, 0, 0, 0, 0, 0)
        for cid in ids:
            p = k.properties(fd, cid, k.OBJECT_CONNECTOR)
            if 'WRITEBACK_PIXEL_FORMATS' in p and p['WRITEBACK_PIXEL_FORMATS'][1]:
                b = blob(fd, p['WRITEBACK_PIXEL_FORMATS'][1]); fl = list(struct.unpack('<%dI' % (len(b) // 4), b))
                out['writeback'][cid] = {'n': len(fl), 'dups': dups(fl)}
    finally: os.close(fd)
    bad = {p: r for p, r in out['planes'].items() if r['dup_plane_list'] or (r.get('in_formats') or {}).get('dup_formats_array') or (r.get('in_formats') or {}).get('dup_runs_format_major')}
    print('PLANES', len(out['planes']), 'with duplicates:', json.dumps(bad)[:1500])
    print('WRITEBACK', json.dumps(out['writeback']))
    p = Path('/home/siwal/y700-design/desktop-20260919/format-probe-%s.json' % boot[:8]); p.write_text(json.dumps(out, indent=1)); os.chown(p, 1000, 1000)
if __name__ == '__main__':
    try: main()
    except Exception as e: sys.exit('STOP: %r' % e)
