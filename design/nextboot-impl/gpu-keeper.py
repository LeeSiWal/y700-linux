#!/usr/bin/env python3
"""gpu-keeper: after GO, open /dev/kgsl-3d0 once (first open boots GMU, loads SQE/AQE/GMU fw and zap via TZ), read
KGSL_PROP_DEVICE_INFO, then hold the fd for the whole boot so later Vulkan clients never perform KGSL's last close.
No allocation, context or submission ioctls. Never closes, never retries. Usage: gpu-keeper.py MAJOR:MINOR"""
import ctypes, fcntl, json, os, signal, stat, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kgslabi as k

CHIP_ID = 0x44050a31                      # Adreno 840 (KGSL DEVICE_INFO on b4e16b26); DT qcom,chipid 0x44050a01 is NOT the device value
def out(*a): print(*a, flush=True)
def hold(reason):
    out('HELD pid=%d reason=%s' % (os.getpid(), reason))
    while True: time.sleep(1)

def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    major, minor = (int(x) for x in sys.argv[1].split(':'))
    st = os.stat('/dev/kgsl-3d0')
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(major, minor)): out('STOP: node does not match sysfs dev'); hold('NODE_REJECTED')
    out('READY_FOR_OPEN')
    if sys.stdin.readline() != 'GO\n': hold('GATE_NOT_RELEASED')
    out('OPEN_ENTER'); t0 = time.monotonic()
    try: fd = os.open('/dev/kgsl-3d0', os.O_RDWR | os.O_CLOEXEC)
    except OSError as e: out('STOP: open failed errno=%d %s' % (e.errno, e.strerror)); hold('OPEN_FAILED')
    out('OPEN_RETURNED fd=%d ms=%.1f' % (fd, (time.monotonic() - t0) * 1e3))
    buf = ctypes.create_string_buffer(k.DEVINFO.size)
    req = bytearray(k.GETPROP.pack(k.KGSL_PROP_DEVICE_INFO, ctypes.addressof(buf), k.DEVINFO.size))
    try: fcntl.ioctl(fd, k.IOCTL_KGSL_DEVICE_GETPROPERTY, req, True)
    except OSError as e: out('STOP: DEVICE_INFO errno=%d %s' % (e.errno, e.strerror)); hold('DEVINFO_FAILED')
    info = k.parse_devinfo(buf.raw)
    out('DEVINFO ' + json.dumps(info, sort_keys=True))
    if info['chip_id'] != CHIP_ID: out('STOP: chip_id 0x%x != 0x%x' % (info['chip_id'], CHIP_ID)); hold('CHIP_MISMATCH')
    hold('KGSL_FD_RETAINED')

if __name__ == '__main__':
    try: main()
    except BaseException as exc: out('STOP: exception %r' % (exc,)); hold('EXCEPTION')
