"""Download Valve's linuxarm64 Steam client components listed in the manifest (client-update CDN), verifying sha256."""
import hashlib, re, sys, urllib.request
from pathlib import Path
BASE = 'https://client-update.fastly.steamstatic.com/'
man = Path('steam_client_linuxarm64').read_text()
comps = re.findall(r'"([a-z0-9_]+)"\s*\{\s*"file"\s*"([^"]+)"\s*"size"\s*"(\d+)"\s*"sha2"\s*"([0-9a-f]+)"', man)
want = [c for c in comps if len(sys.argv) < 2 or any(a in c[0] for a in sys.argv[1:])]
out = Path('parts'); out.mkdir(exist_ok=True)
for name, f, size, sha in want:
    p = out/f
    if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == sha:
        print('have', name); continue
    urllib.request.urlretrieve(BASE + f, p)
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    print('%-34s %8.1f MB %s' % (name, int(size)/1e6, 'OK' if got == sha else 'SHA MISMATCH'))
