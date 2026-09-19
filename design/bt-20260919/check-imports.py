#!/usr/bin/env python3
"""Read-only closure check for candidate modules: every undefined symbol must be exported by the running kernel
(/proc/kallsyms name, any case) or by a candidate module; each __versions CRC is compared with the CRC that any other
reviewed .ko on this host records for the same symbol (exporter __crc_ value or importer __versions entry)."""
import glob, struct, sys, collections
from pathlib import Path

def elf(path):
    d = Path(path).read_bytes(); shoff = struct.unpack_from('<Q', d, 40)[0]; shes, shn, shstr = struct.unpack_from('<HHH', d, 58)
    sh = [struct.unpack_from('<IIQQQQIIQQ', d, shoff + i * shes) for i in range(shn)]
    nm = lambda s, o: d[sh[s][4] + o:d.index(b'\0', sh[s][4] + o)].decode()
    secs = {nm(shstr, s[0]): (i, s) for i, s in enumerate(sh)}
    undef, defined, crcs, vers = set(), set(), {}, {}
    for i, s in enumerate(sh):
        if s[1] != 2: continue                                    # SHT_SYMTAB
        for off in range(s[4], s[4] + s[5], 24):
            name_o, info, other, shndx, value, size = struct.unpack_from('<IBBHQQ', d, off)
            n = nm(s[6], name_o)
            if not n: continue
            if shndx == 0 and (info >> 4) in (1, 2): undef.add(n)
            elif shndx != 0 and (info >> 4) in (1, 2):
                defined.add(n)
                if n.startswith('__crc_') and shndx < len(sh):
                    t = sh[shndx]; crcs[n[6:]] = struct.unpack_from('<I', d, t[4] + value - t[3])[0]
    if '__versions' in secs:
        _, s = secs['__versions']
        for off in range(s[4], s[4] + s[5], 64):
            crc = struct.unpack_from('<Q', d, off)[0] & 0xffffffff; n = d[off + 8:off + 64].split(b'\0')[0].decode()
            vers[n] = crc
    return undef, defined, crcs, vers

cands = sys.argv[1:]
kall = set(l.split()[2] for l in open('/proc/kallsyms'))
info = {c: elf(c) for c in cands}
known = collections.defaultdict(set)
refs = set(glob.glob('/home/siwal/y700-design/**/*.ko', recursive=True)) | set(glob.glob('/home/siwal/y700-agent/**/*.ko', recursive=True))
for r in refs:
    try: u, dfn, crcs, vers = elf(r)
    except Exception: continue
    for k, v in list(crcs.items()) + list(vers.items()): known[k].add((v, Path(r).name))
bad = 0
for c, (u, dfn, crcs, vers) in info.items():
    exported = set().union(*(info[o][1] for o in cands if o != c))
    unres = sorted(x for x in u if x not in kall and x not in exported)
    checked = mism = 0
    for k, v in vers.items():
        others = {x for x in known.get(k, ()) if x[1] != Path(c).name}
        if others:
            checked += 1
            if any(o[0] != v for o in others): mism += 1; print('  CRC MISMATCH', c, k, hex(v), sorted(others)[:3])
    print('%-22s undefined=%d unresolved=%d versions=%d crc_checked=%d mismatch=%d %s' % (Path(c).name, len(u), len(unres), len(vers), checked, mism, unres[:8]))
    bad += len(unres) + mism
print('RESULT', 'OK' if not bad else 'PROBLEMS=%d' % bad)
