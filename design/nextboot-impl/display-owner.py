#!/usr/bin/env python3
"""display-owner: the single long-lived DRM master for a new boot.

Opens /dev/dri/card0 itself (first client), verifies the splash topology against boot b4e16b26, then:
  commit A (ALLOW_MODESET, same form as the reviewed screen-small worker): connector->CRTC, NEW mode blob with the splash
           timing, ACTIVE=1, left plane = centred 952x1520 cached dumb FB, right plane detached.
  commit B (no modeset, final geometry reached in b4e16b26 over several reviewed commits): native 1904x3040 cached dumb FB
           (1920-px stride), left plane SRC(0,0,952,3040)->(0,0), right plane SRC(952,0,952,3040)->(952,0), zpos 1.
Each commit: TEST_ONLY, then a GO gate from the supervisor, then one blocking commit and a full property readback.
Afterwards the process holds the DRM fd, both framebuffers and mappings forever (test workers borrow the fd).
It never closes the fd, never restores and never retries. Any error -> HOLD."""
import ctypes as C, json, os, signal, stat, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drmkms as k

W, H = 1904, 3040
SW, SH, DX, DY = 952, 1520, 476, 760                 # commit A: centred reduced area, no scaling
REVIEWED_TOPOLOGY = {'connector': 69, 'crtc': 205, 'left': 95, 'right': 128, 'planes': 20}
COLORS = (0xffffff, 0xffff00, 0x00ffff, 0x00ff00, 0xff00ff, 0xff0000, 0x0000ff, 0x404040)

def out(*a): print(*a, flush=True)
def hold(reason):
    out('HELD pid=%d reason=%s' % (os.getpid(), reason))
    while True: time.sleep(1)
def gate(tag):
    out(tag)
    if sys.stdin.readline() != 'GO\n': hold('GATE_NOT_RELEASED')
def need(ok, msg):
    if not ok: out('STOP: ' + msg); hold('CHECK_FAILED')

def pattern(w, h, x, y):
    """Same test pattern as the reviewed kms-held workers: 8 colour bars, 96-px checker in the bottom quarter, 12-px border."""
    c = COLORS[(x * 8) // w]
    if y > h * 3 // 4: c = 0xffffff if ((x // 96 + y // 96) & 1) else 0x101010
    if x < 12 or y < 12 or x > w - 13 or y > h - 13: c = 0xffffff
    return c

def fill(buf, w, h):
    row = (C.c_uint32 * (buf['pitch'] // 4))
    for y in range(h):
        r = row.from_address(buf['addr'] + y * buf['pitch'])
        vals = [pattern(w, h, x, y) for x in range(w)]
        r[:w] = vals
        if buf['pitch'] // 4 > w: r[w:] = [0] * (buf['pitch'] // 4 - w)
    bad = 0
    for y in range(0, h, 7):                         # CPU readback (every 7th row, all columns)
        r = row.from_address(buf['addr'] + y * buf['pitch'])
        bad += sum(1 for x in range(w) if r[x] != pattern(w, h, x, y))
    return bad

def discover(fd):
    conn = int(open('/sys/class/drm/card0-DSI-1/connector_id').read())
    cp = k.properties(fd, conn, k.OBJECT_CONNECTOR); crtc = cp['CRTC_ID'][1]
    need(crtc != 0, 'connector not bound to a CRTC (splash missing)')
    rp = k.properties(fd, crtc, k.OBJECT_CRTC)
    planes = k.plane_ids(fd); bound = []
    for pid in planes:
        p = k.get_plane(fd, pid); props = k.properties(fd, pid, k.OBJECT_PLANE)
        need(p['fb_id'] == 0 and props['FB_ID'][1] == 0, 'a plane already has a framebuffer (not the reviewed splash state)')
        if p['crtc_id'] == crtc: bound.append((pid, props['type'][1]))
        else: need(p['crtc_id'] == 0, 'plane %d bound to another CRTC' % pid)
    # b4e16b26: planes 95 and 128 are both type 1 (primary) and both bound to the splash CRTC; the reviewed worker took the
    # first usable primary in plane-resource order as the left plane and the other as the second plane.
    need(len(bound) == 2 and [t for _, t in bound] == [1, 1], 'splash must bind exactly two primary planes: %r' % bound)
    left, right = bound[0][0], bound[1][0]
    topo = {'connector': conn, 'crtc': crtc, 'left': left, 'right': right, 'planes': len(planes)}
    return topo, cp, rp

def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    st = os.stat('/dev/dri/card0')
    need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(226, 0), 'wrong card0 node')
    fd = os.open('/dev/dri/card0', os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    out('FIRST_DRM_OPEN fd=%d (this process keeps it for the whole boot)' % fd)
    need(k.driver_name(fd) == 'msm_drm', 'unexpected driver')
    need(k.get_cap(fd, k.CAP_DUMB_BUFFER) == 1, 'dumb buffers unsupported')
    k.set_client_cap(fd, k.CLIENT_CAP_ATOMIC, 1); k.set_master(fd)
    topo, cp, rp = discover(fd)
    out('TOPOLOGY ' + json.dumps(topo, sort_keys=True))
    need(topo == REVIEWED_TOPOLOGY, 'topology differs from the reviewed boot; review before any commit')
    need(rp['ACTIVE'][1] == 1, 'splash CRTC not active')
    raw = k.read_mode(fd, rp['MODE_ID'][1]); mode = k.mode_fields(raw)
    need(all(mode[f] == v for f, v in k.REVIEWED_TIMING.items()), 'splash timing differs: %r' % mode)
    out('SPLASH_MODE ' + json.dumps({f: mode[f] for f in k.REVIEWED_TIMING}, sort_keys=True))
    lp = k.properties(fd, topo['left'], k.OBJECT_PLANE); rpl = k.properties(fd, topo['right'], k.OBJECT_PLANE)
    need(lp['zpos'][1] == 0 and lp['alpha'][1] == 65535 and lp['rotation'][1] == 0, 'left plane processing state changed')
    small = k.dumb_fb(fd, SW, SH); need((small['pitch'], small['size']) == (3840, 5836800), 'small dumb geometry')
    need(fill(small, SW, SH) == 0, 'small buffer readback mismatch')
    small_fb = k.add_fb(fd, SW, SH, small['handle'], small['pitch'])
    blob = k.create_blob(fd, raw)
    A = {topo['connector']: [(cp['CRTC_ID'][0], topo['crtc'])],
         topo['crtc']: [(rp['MODE_ID'][0], blob), (rp['ACTIVE'][0], 1)],
         topo['left']: [(lp['FB_ID'][0], small_fb), (lp['CRTC_ID'][0], topo['crtc']), (lp['SRC_X'][0], 0), (lp['SRC_Y'][0], 0),
                        (lp['SRC_W'][0], SW << 16), (lp['SRC_H'][0], SH << 16), (lp['CRTC_X'][0], DX), (lp['CRTC_Y'][0], DY),
                        (lp['CRTC_W'][0], SW), (lp['CRTC_H'][0], SH)],
         topo['right']: [(rpl['FB_ID'][0], 0), (rpl['CRTC_ID'][0], 0)]}
    objsA = [topo['connector'], topo['crtc'], topo['left'], topo['right']]
    try: k.atomic(fd, k.ATOMIC_TEST_ONLY | k.ATOMIC_ALLOW_MODESET, objsA, A)
    except OSError as e: need(False, 'commit A TEST_ONLY errno=%d' % e.errno)
    out('COMMIT_A_TEST_ONLY_PASS small_fb=%d mode_blob=%d' % (small_fb, blob))
    gate('READY_FOR_COMMIT_A')
    out('COMMIT_A_ENTER')
    try: k.atomic(fd, k.ATOMIC_ALLOW_MODESET, objsA, A)
    except OSError as e: out('STOP: commit A errno=%d' % e.errno); hold('COMMIT_A_ERROR_STATE_UNCERTAIN')
    rp2 = k.properties(fd, topo['crtc'], k.OBJECT_CRTC); lp2 = k.properties(fd, topo['left'], k.OBJECT_PLANE)
    rpl2 = k.properties(fd, topo['right'], k.OBJECT_PLANE)
    need(rp2['MODE_ID'][1] == blob and rp2['ACTIVE'][1] == 1, 'commit A CRTC readback')
    need([lp2[x][1] for x in ('FB_ID', 'CRTC_ID', 'SRC_W', 'SRC_H', 'CRTC_X', 'CRTC_Y', 'CRTC_W', 'CRTC_H')] ==
         [small_fb, topo['crtc'], SW << 16, SH << 16, DX, DY, SW, SH], 'commit A left plane readback')
    need(rpl2['FB_ID'][1] == 0 and rpl2['CRTC_ID'][1] == 0, 'commit A right plane readback')
    out('COMMIT_A_RETURNED')
    big = k.dumb_fb(fd, W, H); need((big['pitch'], big['size']) == (7680, 23347200), 'native dumb geometry')
    need(fill(big, W, H) == 0, 'native buffer readback mismatch')
    native_fb = k.add_fb(fd, W, H, big['handle'], big['pitch'])
    Bp = {topo['left']: [(lp2['FB_ID'][0], native_fb), (lp2['SRC_X'][0], 0), (lp2['SRC_Y'][0], 0), (lp2['SRC_W'][0], 952 << 16),
                         (lp2['SRC_H'][0], H << 16), (lp2['CRTC_X'][0], 0), (lp2['CRTC_Y'][0], 0), (lp2['CRTC_W'][0], 952), (lp2['CRTC_H'][0], H)],
          topo['right']: [(rpl2['FB_ID'][0], native_fb), (rpl2['CRTC_ID'][0], topo['crtc']), (rpl2['SRC_X'][0], 952 << 16), (rpl2['SRC_Y'][0], 0),
                          (rpl2['SRC_W'][0], 952 << 16), (rpl2['SRC_H'][0], H << 16), (rpl2['CRTC_X'][0], 952), (rpl2['CRTC_Y'][0], 0),
                          (rpl2['CRTC_W'][0], 952), (rpl2['CRTC_H'][0], H), (rpl2['zpos'][0], 1)]}
    objsB = [topo['left'], topo['right']]
    try: k.atomic(fd, k.ATOMIC_TEST_ONLY, objsB, Bp)
    except OSError as e: need(False, 'commit B TEST_ONLY errno=%d' % e.errno)
    out('COMMIT_B_TEST_ONLY_PASS native_fb=%d' % native_fb)
    gate('READY_FOR_COMMIT_B')
    out('COMMIT_B_ENTER')
    try: k.atomic(fd, 0, objsB, Bp)
    except OSError as e: out('STOP: commit B errno=%d' % e.errno); hold('COMMIT_B_ERROR_STATE_UNCERTAIN')
    L, R = k.properties(fd, topo['left'], k.OBJECT_PLANE), k.properties(fd, topo['right'], k.OBJECT_PLANE)
    want = lambda sx, dx: [native_fb, topo['crtc'], sx << 16, 0, 952 << 16, H << 16, dx, 0, 952, H]
    keys = ('FB_ID', 'CRTC_ID', 'SRC_X', 'SRC_Y', 'SRC_W', 'SRC_H', 'CRTC_X', 'CRTC_Y', 'CRTC_W', 'CRTC_H')
    need([L[x][1] for x in keys] == want(0, 0) and [R[x][1] for x in keys] == want(952, 952), 'commit B plane readback')
    need(L['zpos'][1] == 0 and R['zpos'][1] == 1, 'commit B zpos readback')
    crtc = k.properties(fd, topo['crtc'], k.OBJECT_CRTC); need(crtc['MODE_ID'][1] == blob and crtc['ACTIVE'][1] == 1, 'mode changed')
    out('COMMIT_B_RETURNED')
    global _keep; _keep = (small, big)
    out('OWNER_READY ' + json.dumps({'fd': fd, 'topology': topo, 'mode_blob': blob, 'small_fb': small_fb, 'native_fb': native_fb}, sort_keys=True))
    hold('DISPLAY_OWNER_HOLDING')

if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:   # never exit: exit would close the only DRM master fd
        out('STOP: exception %r' % (exc,)); hold('EXCEPTION')
