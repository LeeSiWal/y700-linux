"""Digest of the file tree served to the sensors PD (rootmap targets except /sys)."""
import hashlib, json, os
def tree_digest(rootmap_path):
    m = json.load(open(rootmap_path)); h = hashlib.sha256(); n = 0
    for loc in sorted(set(v for v in m.values() if not v.startswith('/sys'))):
        for dp, dns, fns in sorted(os.walk(loc)):
            dns.sort()
            for fn in sorted(fns):
                p = os.path.join(dp, fn); h.update(os.path.relpath(p, loc).encode() + b'\0'); h.update(hashlib.sha256(open(p, 'rb').read()).digest()); n += 1
    return h.hexdigest(), n
