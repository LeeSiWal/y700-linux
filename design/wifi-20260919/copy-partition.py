#!/usr/bin/env python3
"""Read-only copy of one named UFS partition into an image file (default: modem_a). No writes to any block device.
Verifies the sysfs PARTNAME, dev number and size, creates a 0400 root block node under /dev/block if absent (/dev is a
plain tmpfs here), opens it O_RDONLY, streams it to <name>.img (exclusive create) and prints SHA256.
Usage: sudo python3 -B copy-partition.py [modem_a]"""
import hashlib, os, stat, sys
from pathlib import Path

name = sys.argv[1] if len(sys.argv) > 1 else 'modem_a'
if name not in ('modem_a', 'persist', 'bluetooth_a', 'dsp_a'): sys.exit('STOP: partition not in the reviewed read-only list')
if os.geteuid() != 0: sys.exit('STOP: sudo required')
hits = [b for b in Path('/sys/class/block').iterdir() if (b/'partition').exists() and
        any(l == 'PARTNAME=' + name for l in (b/'uevent').read_text().splitlines())]
if len(hits) != 1: sys.exit('STOP: expected exactly one partition named %s, found %d' % (name, len(hits)))
b = hits[0]; mj, mn = (int(x) for x in (b/'dev').read_text().split(':')); size = int((b/'size').read_text()) * 512
node = Path('/dev/block')/b.name
if not node.exists():
    os.mknod(node, stat.S_IFBLK | 0o400, os.makedev(mj, mn)); print('NODE_CREATED', node, '%d:%d' % (mj, mn), flush=True)
st = os.lstat(node)
if not (stat.S_ISBLK(st.st_mode) and st.st_rdev == os.makedev(mj, mn)): sys.exit('STOP: node mismatch')
out = Path(__file__).with_name(name + '.img')
h = hashlib.sha256(); done = 0
src = os.open(node, os.O_RDONLY)
try:
    with open(out, 'xb') as f:
        while done < size:
            chunk = os.read(src, min(4 << 20, size - done))
            if not chunk: sys.exit('STOP: short read at %d' % done)
            f.write(chunk); h.update(chunk); done += len(chunk)
finally:
    os.close(src)
os.chmod(out, 0o644)
print('COPIED %s %s bytes=%d sha256=%s' % (name, b.name, done, h.hexdigest()), flush=True)
