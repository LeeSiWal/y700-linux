"""Shared fail-closed module and archive validation; stdlib only."""
from pathlib import Path
import hashlib, re, struct
KERNEL='6.12.30-android16-5-g1750f757fabe-ab13938768-4k'
SIZE=8388608
NAME=re.compile(r'^[A-Za-z0-9_-]+\.ko$')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok, message):
    if not ok: raise ValueError(message)
def sections(data):
    need(data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183, 'not little-endian ARM64 ELF')
    off=struct.unpack_from('<Q',data,40)[0]; size,n,si=struct.unpack_from('<HHH',data,58)
    hdr=[struct.unpack_from('<IIQQQQIIQQ',data,off+i*size) for i in range(n)]
    st=hdr[si]; names=data[st[4]:st[4]+st[5]]
    return {names[h[0]:].split(b'\0',1)[0].decode():data[h[4]:h[4]+h[5]] for h in hdr}
def metadata(p):
    sec=sections(Path(p).read_bytes()); out={}
    for s in sec.get('.modinfo',b'').split(b'\0'):
        if b'=' in s:
            k,v=s.decode(errors='replace').split('=',1); out.setdefault(k,[]).append(v)
    out['has_crcs']=bool(sec.get('__versions') or sec.get('__version_ext_crcs'))
    return out

def closure(root, profiles):
    md=root/'modules/all'; deps={}
    for line in (md/'modules.dep').read_text().splitlines():
        if not line.strip(): continue
        need(':' in line,'malformed modules.dep')
        left,right=line.split(':',1); name=Path(left).name
        value=[Path(x).name for x in right.split()]
        need(name not in deps or deps[name]==value,'conflicting dependency entry: '+name)
        deps[name]=value
    names={p.stem.replace('-','_'):p.name for p in md.glob('*.ko')}
    seen=set(); visiting=set(); order=[]; info={}
    def visit(name):
        need(bool(NAME.fullmatch(name)),'unsafe module name')
        if name in seen:return
        need(name not in visiting,'dependency cycle: '+name)
        need(name in deps,'missing dependency metadata: '+name)
        need((md/name).is_file(),'missing module: '+name)
        visiting.add(name)
        m=metadata(md/name); info[name]=m
        declared=m.get('depends',[''])[0].split(',')
        extra=[]
        for d in filter(None,declared):
            key=d.replace('-','_'); need(key in names,'missing ELF dependency: '+d)
            extra.append(names[key])
        for d in dict.fromkeys(deps[name]+extra):visit(d)
        visiting.remove(name);seen.add(name);order.append(name)
    for profile in profiles:
        need(bool(re.fullmatch('[a-z0-9-]+',profile)),'unsafe profile')
        for line in (root/'modules/profiles'/f'{profile}.txt').read_text().splitlines():
            line=line.split('#',1)[0].strip()
            if line:visit(line)
    return order,info

def cpio(data):
    out={}; pos=0
    while pos+110<=len(data):
        need(data[pos:pos+6]==b'070701','bad CPIO magic')
        f=[int(data[pos+6+8*i:pos+14+8*i],16) for i in range(13)]
        start=pos+110; end=start+f[11]
        need(f[11]>0 and end<=len(data) and data[end-1]==0,'bad CPIO name')
        name=data[start:end-1].decode(); doff=(end+3)&~3; dend=doff+f[6]
        need(dend<=len(data),'truncated CPIO')
        if name=='TRAILER!!!':return out
        need(name not in out or ((f[1]&0o170000)==0o040000 and out[name]==(f[1],data[doff:dend])), 'conflicting or non-directory duplicate CPIO entry: '+name)
        need(not name.startswith('/') and '..' not in Path(name).parts,'unsafe CPIO path')
        out[name]=(f[1],data[doff:dend]);pos=(dend+3)&~3
    raise ValueError('missing CPIO trailer')

def new_kernel_faults(before, after):
    """Only new log records; old boot warnings must not be attributed to this step."""
    old=set(before.splitlines())
    return [line for line in after.splitlines() if line not in old and re.search(r'WARNING:|BUG:|Oops:|Kernel panic|Call trace:|Runtime PM usage count underflow|Unbalanced IRQ|INFO: task .* blocked for more than|(?:SMMU|arm-smmu).*?[Ff]ault|synx:\s*warn:.*Subsystem restart|\[drm[^\n]*\*ERROR\*|\[msm-dsi-error\]',line)]


def require_clean_kernel_log(log):
    """Do not attribute old faults to a new trial, but do block further loading."""
    faults=new_kernel_faults('',log)
    need(not faults, 'pre-existing kernel faults require review before loading: '+str(faults[:3]))
