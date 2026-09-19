#!/usr/bin/env python3
"""Read-only touch event capture for the NVT input device (no EVIOCGRAB, no writes). Needs root to open /dev/input/eventN.
Prints the ABS ranges, then records every contact (start/end position, slot) for N seconds and writes touch-events.json.
Suggested gesture sequence (in the orientation you hold the tablet): 1 top-left, 2 top-right, 3 bottom-right, 4 bottom-left,
5 centre, 6 two fingers together. Usage: sudo python3 -B touch-events.py [seconds]"""
import fcntl, json, os, re, select, struct, sys, time
from pathlib import Path

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_MT_SLOT, ABS_MT_TRACKING_ID, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_PRESSURE = 0x2f, 0x39, 0x35, 0x36, 0x3a
EVENT = struct.Struct('llHHi')           # struct input_event on arm64: timeval (2 x long), type, code, value
ABSINFO = struct.Struct('6i')            # value minimum maximum fuzz flat resolution
def eviocgabs(code): return (2 << 30) | (ABSINFO.size << 16) | (ord('E') << 8) | (0x40 + code)

def find_node():
    blocks = Path('/proc/bus/input/devices').read_text().strip().split('\n\n')
    hits = [b for b in blocks if 'N: Name="NVTCapacitiveTouchScreen"' in b]      # the pen is a separate device (NVTCapacitivePen)
    if len(hits) != 1: sys.exit('STOP: expected exactly one NVTCapacitiveTouchScreen, found %d' % len(hits))
    ev = re.findall(r'\bevent(\d+)\b', hits[0])
    if len(ev) != 1: sys.exit('STOP: no unique event handler')
    return hits[0], '/dev/input/event' + ev[0]

def main():
    if os.geteuid() != 0: sys.exit('STOP: sudo required')
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 40
    out = Path(__file__).with_name('touch-events.json')
    if out.exists(): sys.exit('STOP: touch-events.json exists; keep evidence, use a copy of this script')
    block, node = find_node()
    # /dev is a plain tmpfs here (no devtmpfs): udev makes by-path links but not the nodes. Create the event node from sysfs.
    import stat
    dev = Path('/sys/class/input')/Path(node).name/'dev'
    mj, mn = (int(x) for x in dev.read_text().split(':'))
    if not os.path.exists(node):
        os.mknod(node, stat.S_IFCHR | 0o600, os.makedev(mj, mn)); print('NODE_CREATED', node, '%d:%d' % (mj, mn), flush=True)
    st = os.lstat(node)
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn)): sys.exit('STOP: %s does not match sysfs %d:%d' % (node, mj, mn))
    fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
    ranges = {}
    for name, code in (('slot', ABS_MT_SLOT), ('x', ABS_MT_POSITION_X), ('y', ABS_MT_POSITION_Y), ('pressure', ABS_MT_PRESSURE)):
        buf = bytearray(ABSINFO.size)
        try: fcntl.ioctl(fd, eviocgabs(code), buf, True); ranges[name] = dict(zip(('value', 'min', 'max', 'fuzz', 'flat', 'res'), ABSINFO.unpack(buf)))
        except OSError as e: ranges[name] = {'error': e.errno}
    print('DEVICE', node, json.dumps(ranges), flush=True)
    print('Touch now: 1 top-left, 2 top-right, 3 bottom-right, 4 bottom-left, 5 centre, 6 two fingers (%.0f s)' % secs, flush=True)
    slot = 0; live = {}; contacts = []; maxc = 0; t_end = time.monotonic() + secs; nev = 0
    while time.monotonic() < t_end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r: continue
        data = os.read(fd, EVENT.size * 64)
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, typ, code, val = EVENT.unpack_from(data, i); nev += 1
            if typ != EV_ABS: continue
            if code == ABS_MT_SLOT: slot = val
            elif code == ABS_MT_TRACKING_ID:
                if val >= 0: live[slot] = {'slot': slot, 'id': val, 'start': [None, None], 'last': [None, None], 't': time.monotonic()}
                elif slot in live:
                    c = live.pop(slot); c['ms'] = round((time.monotonic() - c.pop('t')) * 1e3); contacts.append(c)
                    print('CONTACT', json.dumps(c), flush=True)
                maxc = max(maxc, len(live))
            elif code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y) and slot in live:
                k = 0 if code == ABS_MT_POSITION_X else 1
                if live[slot]['start'][k] is None: live[slot]['start'][k] = val
                live[slot]['last'][k] = val
    report = {'node': node, 'device_block': block, 'ranges': ranges, 'events': nev, 'contacts': contacts, 'max_simultaneous': maxc}
    out.write_text(json.dumps(report, indent=2)); os.chmod(out, 0o644)
    print('SUMMARY events=%d contacts=%d max_simultaneous=%d saved %s' % (nev, len(contacts), maxc, out), flush=True)

main()
