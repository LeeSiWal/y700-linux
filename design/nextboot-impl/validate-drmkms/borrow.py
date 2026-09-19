"""Borrow the same reviewed DRM file; the original owner is always retained."""
from pathlib import Path
import ctypes
import hashlib
import os
import platform
import stat


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def verify_original(m, reader):
    original = m['original_worker']
    p = Path('/proc') / str(original['pid'])
    status = reader.task_stat(reader.read(p / 'stat'))
    need(status['comm'] == 'kms-held' and status['starttime'] == original['starttime'], 'original worker identity changed')
    need((p / 'exe').resolve() == Path(m['original_executable']), 'original executable changed')
    need(hashlib.sha256(reader.read(p / 'exe', True)).hexdigest() == original['binary_sha256'], 'original worker bytes changed')
    need(os.readlink(p / 'fd' / '3') == '/dev/dri/card0', 'original DRM descriptor changed')
    lines = reader.read('/sys/kernel/debug/dri/0/clients').splitlines()
    need(len(lines) == 2 and lines[0].split() == ['command', 'tgid', 'dev', 'master', 'a', 'uid', 'magic']
         and lines[1].split() == ['kms-held', str(original['pid']), '0', 'y', 'y', '0', '0'], 'DRM owner/client changed')


def borrow_fd(m, reader):
    need(platform.machine() == 'aarch64', 'pidfd_getfd restricted to reviewed aarch64 ABI')
    verify_original(m, reader)
    pidfd = os.pidfd_open(m['original_worker']['pid'], 0)
    borrowed = -1
    try:
        verify_original(m, reader)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        borrowed = libc.syscall(ctypes.c_long(438), ctypes.c_int(pidfd), ctypes.c_int(3), ctypes.c_uint(0))
        if borrowed < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), 'pidfd_getfd')
        st = os.fstat(borrowed)
        need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(226, 0), 'borrowed fd is not card0')
        verify_original(m, reader)
        return borrowed
    except BaseException:
        if borrowed >= 0:
            os.close(borrowed)  # Only this duplicate; the original worker still owns the file.
        raise
    finally:
        os.close(pidfd)
