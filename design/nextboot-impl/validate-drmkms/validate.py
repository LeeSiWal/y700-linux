#!/usr/bin/env python3
"""Read-only live validation of drmkms.py read primitives on the CURRENT boot. Borrows the reviewed DRM file from PID2604
(pidfd_getfd, same as all screen bundles). Calls only VERSION, GET_CAP, GETPLANERESOURCES, GETPLANE, OBJ_GETPROPERTIES/
GETPROPERTY and GETPROPBLOB. No SET_MASTER, no client caps, no blob creation, no commit. Writes validate-result.json."""
import hashlib, importlib.util, json, os, sys
from pathlib import Path
B = Path(__file__).resolve().parent; sys.path.insert(0, str(B))
import drmkms as k
from borrow import borrow_fd
spec = importlib.util.spec_from_file_location('r', B/'runtime-readers.py'); c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
RATE = Path('/home/siwal/y700-agent/screen-rate1200-b452a79e83e608a1/manifest.json')
import signal; signal.signal(signal.SIGALRM, c.timeout)   # readers use alarm(5); default SIGALRM would kill
if os.geteuid() != 0: sys.exit('STOP: sudo required')
if (B/'validate-result.json').exists(): sys.exit('STOP: already run')
m = json.loads(RATE.read_text())
fd = borrow_fd(m, c)
try:
    r = {'driver': k.driver_name(fd), 'dumb_cap': k.get_cap(fd, k.CAP_DUMB_BUFFER),
         'connector_sysfs': int(Path('/sys/class/drm/card0-DSI-1/connector_id').read_text())}
    planes = k.plane_ids(fd); r['plane_count'] = len(planes)
    r['planes'] = {pid: dict(k.get_plane(fd, pid), type=k.properties(fd, pid, k.OBJECT_PLANE)['type'][1]) for pid in (95, 128) if pid in planes}
    r['other_planes_bound'] = [pid for pid in planes if pid not in (95, 128) and k.get_plane(fd, pid)['crtc_id'] != 0]
    cp = k.properties(fd, r['connector_sysfs'], k.OBJECT_CONNECTOR); crtc = cp['CRTC_ID'][1]; rp = k.properties(fd, crtc, k.OBJECT_CRTC)
    mode = k.mode_fields(k.read_mode(fd, rp['MODE_ID'][1]))
    r.update(crtc=crtc, active=rp['ACTIVE'][1], mode_blob=rp['MODE_ID'][1], mode=mode,
             timing_matches_reviewed=all(mode[f] == v for f, v in k.REVIEWED_TIMING.items()))
finally:
    os.close(fd)   # duplicate only; PID2604 and all retained workers keep the DRM file
Path(B/'validate-result.json').write_text(json.dumps(r, indent=2, sort_keys=True)); os.chmod(B/'validate-result.json', 0o644)
print(json.dumps(r, sort_keys=True))
