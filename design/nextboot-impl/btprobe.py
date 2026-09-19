"""BT chip probe (bt-probe stage): btpower POWER ON -> read the controller version over ttyHS0 -> POWER OFF.
ioctl numbers decoded from btpower.ko bt_ioctl (3a1d6dac): jump table base 0xbfac; 0xbfad = BT_CMD_PWR_CTRL, arg 0/1/2 =
"Power OFF"/"Power ON"/"Power Retention" (.rodata name table), anything > 2 rejected. Never sent: 0xbfc1/0xbfc2 (kernel panic),
0xbfb2 (FMD), 0xbfe2 (registry: would make us a signal target), 0xbfe4/5 (grant). No firmware is downloaded."""
import os, select, stat, termios, time, fcntl
from pathlib import Path

BT_CMD_PWR_CTRL = 0xbfad
POWER_OFF, POWER_ON = 0, 1
EDL_VERSION = bytes.fromhex('0100fc0119')      # H4 cmd, opcode 0xFC00 (EDL patch), len 1, EDL_PATCH_VER_REQ_CMD 0x19
HCI_RESET = bytes.fromhex('01030c00')

def node(path, sysdev):
    mj, mn = (int(x) for x in Path(sysdev).read_text().split(':'))
    p = Path(path)
    if not p.exists(): os.mknod(p, stat.S_IFCHR | 0o600, os.makedev(mj, mn))
    st = os.lstat(p)
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn)): raise RuntimeError('node mismatch ' + path)
    return '%d:%d' % (mj, mn)

def raw_tty(fd):
    a = termios.tcgetattr(fd)
    a[0] = 0; a[1] = 0; a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.CRTSCTS
    a[4] = a[5] = termios.B115200; a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a); termios.tcflush(fd, termios.TCIOFLUSH)

def xfer(fd, pkt, mon, secs=3.0):
    os.write(fd, pkt); buf = b''; end = time.monotonic() + secs
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            chunk = os.read(fd, 4096)
            buf += chunk
            if len(buf) >= 3 and buf[0] == 4 and len(buf) >= 3 + buf[2]: break
        mon.tick()
    return buf

def run(c, need, mon, report):
    r = report['bt_probe'] = {'steps': []}
    r['btpower_node'] = node('/dev/btpower', '/sys/class/bt-dev/btpower/dev')
    r['tty_node'] = node('/dev/ttyHS0', '/sys/class/tty/ttyHS0/dev')
    base = set(c.dmesg().splitlines())
    pfd = os.open('/dev/btpower', os.O_RDWR); powered = False; tfd = None
    try:
        t0 = time.monotonic(); ret = fcntl.ioctl(pfd, BT_CMD_PWR_CTRL, POWER_ON); powered = True
        r['steps'].append({'power_on_ret': ret, 'ms': round((time.monotonic() - t0) * 1000, 1)}); print('POWER_ON ret=%s' % ret, flush=True)
        mon.observe(1, quiet=True)
        tfd = os.open('/dev/ttyHS0', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK); raw_tty(tfd)
        resp = xfer(tfd, EDL_VERSION, mon); r['steps'].append({'edl_version_tx': EDL_VERSION.hex(), 'rx': resp.hex()})
        print('EDL_VERSION rx=%s' % (resp.hex() or '(none)'), flush=True)
        if not resp:
            resp = xfer(tfd, HCI_RESET, mon); r['steps'].append({'hci_reset_tx': HCI_RESET.hex(), 'rx': resp.hex()})
            print('HCI_RESET rx=%s' % (resp.hex() or '(none)'), flush=True)
    finally:
        if tfd is not None: os.close(tfd)
        if powered:
            try: r['steps'].append({'power_off_ret': fcntl.ioctl(pfd, BT_CMD_PWR_CTRL, POWER_OFF)}); print('POWER_OFF', flush=True)
            except OSError as e: r['steps'].append({'power_off_error': str(e)})
        os.close(pfd)
        r['log'] = [l for l in c.dmesg().splitlines() if l not in base][-200:]
    mon.observe(5, quiet=True)
    return r
