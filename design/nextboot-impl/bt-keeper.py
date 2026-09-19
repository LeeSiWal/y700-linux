#!/usr/bin/env python3
"""bt-keeper: after GO, power the WCN786x BT core on through btpower, verify the controller version, download the Brahma 2.0
rampatch + NVM over /dev/ttyHS0 at 115200 (Linux btqca TLV protocol), check HCI_Reset / Read_Local_Version / Read_BD_ADDR,
then attach the tty to the kernel hci_uart H4 line discipline so hci0 is registered. The btpower fd and the tty fd are held
for the whole boot (closing the tty detaches hci0). On any failure before the attach: tty closed, POWER OFF, fds held.
Never sends btpower 0xbfc1/0xbfc2 (kernel panic), FMD, registry or grant ioctls. Never retries.
Usage: bt-keeper.py BTPOWER_MAJ:MIN TTY_MAJ:MIN PRODUCT ROM SOC"""
import fcntl, hashlib, json, os, select, signal, stat, struct, sys, termios, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btprobe import BT_CMD_PWR_CTRL, POWER_OFF, POWER_ON, raw_tty

HERE = os.path.dirname(os.path.abspath(__file__))
TIOCSETD, N_HCI = 0x5423, 15
HCIUARTSETPROTO, HCIUARTGETDEVICE, HCI_UART_H4 = 0x400455c8, 0x800455ca, 0
SEG = 243                                                  # btqca MAX_SIZE_PER_TLV_SEGMENT

def out(*a): print(*a, flush=True)
def hold(reason):
    out('HELD pid=%d reason=%s' % (os.getpid(), reason))
    while True: time.sleep(1)

def cmd(ogf_ocf, params=b''):
    return b'\x01' + struct.pack('<HB', ogf_ocf, len(params)) + params

RX = bytearray()                                           # persistent H4 receive buffer: packets are never dropped or merged
LOG = []

QCA_DEBUG_HANDLE = 0x2edc                                  # hci_qca: controller log/diag ACL packets (e7a9: sent right after the NVM)
DEBUG = {'count': 0, 'bytes': 0, 'first': []}

def pop_packet():
    """Next H4 event. Controller debug ACL packets on handle 0x2EDC are consumed and counted; any other ACL or type is an error."""
    while True:
        if len(RX) >= 5 and RX[0] == 2:
            n = struct.unpack_from('<H', RX, 3)[0]
            if len(RX) < 5 + n: return None
            pkt = bytes(RX[:5 + n]); del RX[:5 + n]
            if struct.unpack_from('<H', pkt, 1)[0] != QCA_DEBUG_HANDLE:        # hci_qca compares the whole le16 (flags included)
                raise RuntimeError('unexpected ACL packet: ' + pkt[:32].hex())
            DEBUG['count'] += 1; DEBUG['bytes'] += n
            if len(DEBUG['first']) < 8: DEBUG['first'].append(pkt[:64].hex())
            continue
        if len(RX) >= 3 and RX[0] == 4 and len(RX) >= 3 + RX[2]:
            pkt = bytes(RX[:3 + RX[2]]); del RX[:3 + RX[2]]; return pkt
        if RX and RX[0] not in (2, 4): raise RuntimeError('non-event byte on the wire: ' + RX[:32].hex())
        return None

def read_event(fd, secs):
    """Next complete H4 event (0x04) or b'' on timeout."""
    end = time.monotonic() + secs
    while True:
        pkt = pop_packet()
        if pkt is not None: return pkt
        left = end - time.monotonic()
        if left <= 0: return b''
        r, _, _ = select.select([fd], [], [], min(0.1, left))
        if r: RX.extend(os.read(fd, 4096))

def write_all(fd, data):
    view = memoryview(data)
    while view:
        _, w, _ = select.select([], [fd], [], 5)
        if not w: raise RuntimeError('tty write stalled (CTS?)')
        n = os.write(fd, view); view = view[n:]

def is_cc(ev, opcode):
    return len(ev) >= 7 and ev[:2] == b'\x04\x0e' and struct.unpack_from('<H', ev, 4)[0] == opcode

LAT = []                                                   # (opcode, rtype, ms) per command: replies were seen >2 s late (3d16/a8af)
def cc(fd, opcode, params=b'', secs=6.0, rtype=None):
    """Send one command and wait for ITS Command Complete (for 0xfc00 the EDL request type byte must match too).
    Anything else arriving first is a protocol error (returned, not skipped)."""
    need_empty = pop_packet()
    if need_empty is not None: return need_empty, False
    t0 = time.monotonic(); write_all(fd, cmd(opcode, params)); ev = read_event(fd, secs)
    LAT.append((opcode, params[:1].hex(), round((time.monotonic() - t0) * 1e3, 1) if ev else None))
    ok = is_cc(ev, opcode) and ev[6] == 0 and (rtype is None or (len(ev) > 7 and ev[7] == rtype))
    return ev, ok

def nvm_for_h4(data):
    """btqca NVM tags as Linux would patch them for a host that runs H4 at 115200: tag 17 (HCI transport) data[1] = baud
    code 0 (115200; file has 0x11 = 3.2 Mbps) and data[0] bit7 (in-band sleep) cleared; tag 27 (deep sleep) data[0] bit0
    cleared (H4 cannot handle IBS/sleep bytes). Returns (bytes, changes)."""
    b = bytearray(data); length = int.from_bytes(b[1:4], 'little'); idx = 4; changes = []
    assert b[0] == 2 and length + 4 == len(b)
    while idx + 12 <= 4 + length:
        tag, tl = struct.unpack_from('<HH', b, idx); d = idx + 12
        if tag == 17:
            old = bytes(b[d:d + 2]); b[d] &= 0x7f; b[d + 1] = 0x00; changes.append({'tag': 17, 'old': old.hex(), 'new': bytes(b[d:d + 2]).hex()})
        if tag == 27:
            old = b[d]; b[d] &= 0xfe; changes.append({'tag': 27, 'old': '%02x' % old, 'new': '%02x' % b[d]})
        idx = d + tl
    assert idx == 4 + length
    return bytes(b), changes

def tlv_download(fd, data, name):
    t0 = time.monotonic(); stray = b''
    for off in range(0, len(data), SEG):
        seg = data[off:off + SEG]
        write_all(fd, cmd(0xfc00, bytes([0x1e, len(seg)]) + seg))       # EDL_PATCH_TLV_REQ_CMD; download_mode 3: no per-segment event
        r, _, _ = select.select([fd], [], [], 0)
        if r: RX.extend(os.read(fd, 4096))
        if len(RX) and off + SEG < len(data): stray = bytes(RX)             # an answer before the last segment = protocol error
        if off // SEG % 200 == 0: out('DNLD %s %d/%d' % (name, off, len(data)))
    termios.tcdrain(fd)
    out('DNLD_DONE %s bytes=%d segs=%d s=%.1f stray=%s' % (name, len(data), (len(data) + SEG - 1) // SEG, time.monotonic() - t0, stray.hex()[:200]))
    return stray

def nvm_download(fd, data):
    """NVM is not covered by the rampatch download_mode (btqca resets dnld_type to NONE): one Command Complete per segment,
    status must be 0 before the next segment (d74ae51a streamed it and got status 0x10/0x01 after the first segment)."""
    t0 = time.monotonic(); segs = 0
    for off in range(0, len(data), SEG):
        seg = data[off:off + SEG]
        last = off + SEG >= len(data)
        ev, ok = cc(fd, 0xfc00, bytes([0x1e, len(seg)]) + seg, secs=10.0 if last else 5.0, rtype=0x1e); segs += 1
        if not ok: return False, 'segment %d at %d: rx=%s' % (segs, off, ev.hex()[:120])
    ms = [x[2] for x in LAT[-segs:]]
    out('DNLD_DONE nvm bytes=%d segs=%d s=%.1f (each acked) ack_ms min/max=%s/%s' % (len(data), segs, time.monotonic() - t0, min(ms), max(ms)))
    return True, ''

TIOCMGET, TIOCOUTQ, TIOCINQ = 0x5415, 0x5411, 0x541b
# msm_geni_serial_ioctl (decoded, 3a1d6dac): 0x54ed vote_clock_on, 0x54ee vote_clock_off, 0x54ef active check, 0x54ec TIOCFAULT (never).
# Without a userspace vote the HS UART runtime-suspends when idle and then DROPS RX that does not start with the wakeup byte
# ("dropping Rx data as wakeup byte not found") - the cause of the lost/late replies in 3d16..6afe. Android's BT HAL holds this vote.
GENI_VOTE_ON, GENI_VOTE_OFF = 0x54ed, 0x54ee
def lines(fd):
    """Modem lines (CTS = controller ready to receive) and queued bytes - diagnostics only."""
    m = struct.unpack('i', fcntl.ioctl(fd, TIOCMGET, struct.pack('i', 0)))[0]
    q = struct.unpack('i', fcntl.ioctl(fd, TIOCOUTQ, struct.pack('i', 0)))[0]
    i = struct.unpack('i', fcntl.ioctl(fd, TIOCINQ, struct.pack('i', 0)))[0]
    return {'cts': bool(m & 0x20), 'rts': bool(m & 0x4), 'outq': q, 'inq': i}

def first_version(fd, tries=3):
    """3d166387 got no reply within 2 s of the first request (probe/d74/e66 did). Ask up to 6 times over ~15 s and log the
    modem lines each time; a request that is still queued (CTS low) is not re-sent."""
    for n in range(tries):
        st = lines(fd)
        if st['outq'] == 0: write_all(fd, cmd(0xfc00, b'\x19'))
        ev = read_event(fd, 6.0); out('VERSION_TRY %d lines=%s rx=%s' % (n, json.dumps(st), ev.hex()))
        if ev:
            ok = is_cc(ev, 0xfc00) and ev[6] == 0 and len(ev) >= 21 and ev[7] == 0x19
            return ev, ok
    return b'', False

def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    pdev, tdev, product, rom, soc = sys.argv[1], sys.argv[2], int(sys.argv[3], 0), int(sys.argv[4], 0), int(sys.argv[5], 0)
    for path, dev in (('/dev/btpower', pdev), ('/dev/ttyHS0', tdev)):
        mj, mn = (int(x) for x in dev.split(':')); st = os.stat(path)
        if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn)): out('STOP: node mismatch ' + path); hold('NODE_REJECTED')
    fw = {n: open(os.path.join(HERE, n), 'rb').read() for n in ('brhbtfw20.tlv', 'brhbtnv20.bin')}
    out('FW ' + json.dumps({n: hashlib.sha256(d).hexdigest() for n, d in fw.items()}))
    nvm, changes = nvm_for_h4(fw['brhbtnv20.bin']); out('NVM_PATCH ' + json.dumps(changes))
    out('READY')
    if sys.stdin.readline() != 'GO\n': hold('GATE_NOT_RELEASED')
    pfd = os.open('/dev/btpower', os.O_RDWR | os.O_CLOEXEC); tfd = None
    voted = False
    def fail(reason):
        if tfd is not None:
            if voted:
                try: fcntl.ioctl(tfd, GENI_VOTE_OFF)
                except OSError as e: out('VOTE_OFF_ERROR %s' % e)
            os.close(tfd)
        try: out('POWER_OFF ret=%s' % fcntl.ioctl(pfd, BT_CMD_PWR_CTRL, POWER_OFF))
        except OSError as e: out('POWER_OFF_ERROR %s' % e)
        hold(reason)
    t0 = time.monotonic(); out('POWER_ON ret=%s ms=%.1f' % (fcntl.ioctl(pfd, BT_CMD_PWR_CTRL, POWER_ON), (time.monotonic() - t0) * 1e3))
    try:
        time.sleep(1)
        tfd = os.open('/dev/ttyHS0', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC); raw_tty(tfd)
        out('GENI_VOTE_ON ret=%s' % fcntl.ioctl(tfd, GENI_VOTE_ON)); voted = True
        out('LINES_AFTER_OPEN ' + json.dumps(lines(tfd)))
        ev, ok = first_version(tfd)
        out('VERSION ' + ev.hex())
        if not ok: out('STOP: no EDL version response'); fail('NO_VERSION')
        # 6564ab80: PATCH_CONFIG after a 1 s idle gap was never answered; probe/d74/e66 sent it immediately and got a reply at once
        # (the ROM controller appears to doze when idle). Keep every step back-to-back: no idle gap > 0.2 s before the attach.
        stale = read_event(tfd, 0.05)                                     # a duplicate answer to a repeated request
        if stale: out('VERSION_DUPLICATE ' + stale.hex())
        if stale and not (is_cc(stale, 0xfc00) and stale[7] == 0x19): fail('UNEXPECTED_EVENT')
        pid, patch, romv, socid = struct.unpack_from('<IHHI', ev, 9)
        out('VERSION_PARSED ' + json.dumps({'product_id': pid, 'patch': patch, 'rom_ver': romv, 'soc_id': socid}))
        if (pid, romv, socid) != (product, rom, soc): out('STOP: controller differs from reviewed probe'); fail('VERSION_MISMATCH')
        ev, ok = cc(tfd, 0xfc00, bytes([0x28, 0x01, 0x00, 0x00, 0x00]), rtype=0x28)     # EDL_PATCH_CONFIG_CMD (btqca, WCN6855/7850 path)
        out('PATCH_CONFIG ok=%s rx=%s ms=%s' % (ok, ev.hex(), LAT[-1][2]))
        if not ok: fail('PATCH_CONFIG_FAILED')
        stray = tlv_download(tfd, fw['brhbtfw20.tlv'], 'rampatch')
        if stray: out('STOP: controller answered during the rampatch stream'); fail('RAMPATCH_EVENTS')
        # e66fd45f: after the LAST rampatch segment the controller sends one CC (status 0, rtype 0x1e) - the "patch done" ack
        ev = read_event(tfd, 5.0); ok = is_cc(ev, 0xfc00) and len(ev) > 7 and ev[6] == 0 and ev[7] == 0x1e
        out('RAMPATCH_ACK ok=%s rx=%s' % (ok, ev.hex()))
        if not ok: fail('NO_RAMPATCH_ACK')
        ev, ok = cc(tfd, 0xfc00, b'\x19', rtype=0x19); out('VERSION_AFTER_PATCH ok=%s rx=%s' % (ok, ev.hex()))
        if not ok: fail('NO_VERSION_AFTER_PATCH')
        ok, why = nvm_download(tfd, nvm)
        if not ok: out('STOP: NVM ' + why); fail('NVM_FAILED')
        # btqca (WCN3991+): disable SoC logging after the download so logs do not flow to the host (QCA_DISABLE_LOGGING 0xfc17, sub-op 0x14)
        ev, ok = cc(tfd, 0xfc17, bytes([0x14, 0x00])); out('DISABLE_LOGGING ok=%s rx=%s ms=%s debug_acl=%s' % (ok, ev.hex(), LAT[-1][2], json.dumps(DEBUG)))
        if not ok: fail('DISABLE_LOGGING_FAILED')
        extra = read_event(tfd, 0.2); out('POST_DNLD_RX ' + extra.hex())
        if extra: fail('UNEXPECTED_EVENT_AFTER_NVM')
        ev, ok = cc(tfd, 0x0c03, secs=3.0); out('HCI_RESET ok=%s rx=%s' % (ok, ev.hex()))
        if not ok: fail('RESET_FAILED')
        out('LATENCY_MS ' + json.dumps([x for x in LAT if x[0] != 0xfc00 or x[1] != '1e']))
        ev, ok = cc(tfd, 0x1001); out('LOCAL_VERSION ok=%s rx=%s' % (ok, ev.hex()))
        if not ok: fail('LOCAL_VERSION_FAILED')
        hv, hrev, lmp, manu, lsub = struct.unpack_from('<BHBHH', ev, 7)
        out('LOCAL_VERSION_PARSED ' + json.dumps({'hci_ver': hv, 'hci_rev': hrev, 'lmp_ver': lmp, 'manufacturer': manu, 'lmp_subver': lsub}))
        ev, ok = cc(tfd, 0x1009); out('BD_ADDR ok=%s addr=%s' % (ok, ':'.join('%02x' % x for x in reversed(ev[7:13])) if ok else '-'))
        ev, ok = cc(tfd, 0xfc00, b'\x19', rtype=0x19); out('VERSION_AFTER ok=%s rx=%s' % (ok, ev.hex()))
        out('DEBUG_ACL_TOTAL ' + json.dumps({k: DEBUG[k] for k in ('count', 'bytes')}))
        if RX: fail('UNREAD_BYTES_BEFORE_ATTACH')
        termios.tcflush(tfd, termios.TCIOFLUSH)
        fcntl.ioctl(tfd, TIOCSETD, struct.pack('i', N_HCI))
        fcntl.ioctl(tfd, HCIUARTSETPROTO, HCI_UART_H4)
        idx = fcntl.ioctl(tfd, HCIUARTGETDEVICE, 0)
    except Exception as exc:
        out('STOP: exception %r' % (exc,)); fail('EXCEPTION')
    out('HCI_ATTACHED hci%d' % idx)
    hold('BT_ATTACHED')

if __name__ == '__main__':
    try: main()
    except BaseException as exc: out('STOP: exception %r' % (exc,)); hold('EXCEPTION')
