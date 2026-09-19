#!/usr/bin/env python3
"""Read-only ELF analysis: list strings referenced (via relocations) from a data symbol,
e.g. adreno_gpu_core_gen8_2_1 -> firmware names. stdlib only; never loads the module."""
import struct, sys

def main(path, symname):
    d = open(path, 'rb').read()
    assert d[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', d, 18)[0] == 183, 'not ARM64 ELF64 LE'
    shoff = struct.unpack_from('<Q', d, 40)[0]; shes, shn, shstr = struct.unpack_from('<HHH', d, 58)
    sh = [struct.unpack_from('<IIQQQQIIQQ', d, shoff + i * shes) for i in range(shn)]
    names = d[sh[shstr][4]:sh[shstr][4] + sh[shstr][5]]
    nm = lambda o, tab: tab[o:tab.index(b'\0', o)].decode()
    secname = [nm(s[0], names) for s in sh]
    symtab = next(i for i, s in enumerate(sh) if s[1] == 2)
    strtab = d[sh[sh[symtab][6]][4]:sh[sh[symtab][6]][4] + sh[sh[symtab][6]][5]]
    syms = []
    for off in range(sh[symtab][4], sh[symtab][4] + sh[symtab][5], 24):
        st_name, info, other, shndx, value, size = struct.unpack_from('<IBBHQQ', d, off)
        syms.append((nm(st_name, strtab), shndx, value, size))
    target = [s for s in syms if s[0] == symname]
    assert len(target) == 1, 'symbol not unique/found: %d' % len(target)
    _, tsec, tval, tsize = target[0]
    print('%s in %s offset=0x%x size=%d' % (symname, secname[tsec], tval, tsize))
    def cstr(sec, off):
        base = sh[sec][4] + off
        end = d.index(b'\0', base)
        return d[base:end].decode(errors='replace')
    for i, s in enumerate(sh):
        if s[1] != 4 or s[7] != tsec:  # SHT_RELA applying to the symbol's section
            continue
        for off in range(s[4], s[4] + s[5], 24):
            r_off, r_info, addend = struct.unpack_from('<QQq', d, off)
            if not (tval <= r_off < tval + tsize):
                continue
            sym = syms[r_info >> 32]; rtype = r_info & 0xffffffff
            label = sym[0]
            if not label and sym[1] < len(sh) and sh[sym[1]][1] == 1 and 'str' in secname[sym[1]]:
                label = repr(cstr(sym[1], sym[2] + addend))
            elif not label:
                label = '%s+0x%x' % (secname[sym[1]] if sym[1] < len(sh) else sym[1], sym[2] + addend)
            elif addend:
                label += '+0x%x' % addend
            print('  +0x%03x type=%d %s' % (r_off - tval, rtype, label))

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
