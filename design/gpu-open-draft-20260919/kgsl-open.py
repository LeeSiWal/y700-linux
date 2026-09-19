#!/usr/bin/env python3
"""First-open worker: after GO, open /dev/kgsl-3d0 once (this boots GMU/SQE/zap), read DEVICE_INFO, then HOLD.
The descriptor is never closed on any outcome; no command submission, no memory allocation ioctls."""
import ctypes, fcntl, json, os, signal, stat, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kgslabi as k

fd = -1
def out(*a): print(*a, flush=True)
def hold(reason):
    out('HELD pid=%d reason=%s' % (os.getpid(), reason))
    while True: time.sleep(1)   # fd (if any) deliberately stays open

def main():
    global fd
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    st = os.stat('/dev/kgsl-3d0')
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(495, 0)): out('STOP: wrong device node'); hold('NODE_REJECTED')
    out('READY_FOR_OPEN')
    if sys.stdin.readline() != 'GO\n': hold('GATE_NOT_RELEASED')
    out('OPEN_ENTER'); t0 = time.monotonic()
    try: fd = os.open('/dev/kgsl-3d0', os.O_RDWR | os.O_CLOEXEC)
    except OSError as e: out('STOP: open failed errno=%d %s ms=%.1f' % (e.errno, e.strerror, (time.monotonic()-t0)*1e3)); hold('OPEN_FAILED')
    out('OPEN_RETURNED fd=%d ms=%.1f' % (fd, (time.monotonic()-t0)*1e3))
    buf = ctypes.create_string_buffer(k.DEVINFO.size)
    req = bytearray(k.GETPROP.pack(k.KGSL_PROP_DEVICE_INFO, ctypes.addressof(buf), k.DEVINFO.size))
    try: fcntl.ioctl(fd, k.IOCTL_KGSL_DEVICE_GETPROPERTY, req, True)
    except OSError as e: out('STOP: DEVICE_INFO errno=%d %s' % (e.errno, e.strerror)); hold('DEVINFO_FAILED')
    out('DEVINFO ' + json.dumps(k.parse_devinfo(buf.raw), sort_keys=True))
    hold('KGSL_FD_RETAINED')

main()
