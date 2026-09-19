"""Borrow the display-owner's DRM file (pidfd_getfd) as registered for this boot. The owner always keeps its own fd."""
import ctypes, os, platform, stat
from pathlib import Path
import registry as rg

def borrow_fd(c, reg):
    c.need(platform.machine() == 'aarch64', 'pidfd_getfd restricted to reviewed aarch64 ABI')
    owner = reg['owner']; rg.process_ok(c, owner)
    c.need(os.readlink('/proc/%d/fd/%d' % (owner['pid'], owner['fd'])) == '/dev/dri/card0', 'owner DRM fd changed')
    pidfd = os.pidfd_open(owner['pid'], 0); fd = -1
    try:
        rg.process_ok(c, owner)
        libc = ctypes.CDLL(None, use_errno=True); libc.syscall.restype = ctypes.c_long
        fd = libc.syscall(ctypes.c_long(438), ctypes.c_int(pidfd), ctypes.c_int(owner['fd']), ctypes.c_uint(0))
        if fd < 0: e = ctypes.get_errno(); raise OSError(e, os.strerror(e), 'pidfd_getfd')
        st = os.fstat(fd); c.need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(226, 0), 'borrowed fd is not card0')
        rg.process_ok(c, owner)
        return fd
    except BaseException:
        if fd >= 0: os.close(fd)     # only our duplicate
        raise
    finally:
        os.close(pidfd)
