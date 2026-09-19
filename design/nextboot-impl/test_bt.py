"""Host tests for the BT keeper helpers (no device access)."""
import importlib.util, struct, unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('btk', Path(__file__).with_name('bt-keeper.py')); btk = importlib.util.module_from_spec(spec); spec.loader.exec_module(btk)
FW = Path('/home/siwal/y700-design/bt-20260919/firmware')
PROBE = bytes.fromhex('040e120100fc00190c21000000586d000200022140')     # bt-probe 70037e22 reply

class T(unittest.TestCase):
    def test_nvm_patch_only_two_tags(self):
        src = (FW/'brhbtnv20.bin').read_bytes(); new, ch = btk.nvm_for_h4(src)
        self.assertEqual(ch, [{'tag': 17, 'old': '1511', 'new': '1500'}, {'tag': 27, 'old': '01', 'new': '00'}])
        diff = [i for i in range(len(src)) if src[i] != new[i]]
        self.assertEqual(len(new), len(src)); self.assertEqual(len(diff), 2)
    def test_version_parse(self):
        self.assertEqual(PROBE[7], 0x19)
        self.assertEqual(struct.unpack_from('<IHHI', PROBE, 9), (0x21, 0x6d58, 0x0200, 0x40210200))
    def test_cmd_encoding(self):
        self.assertEqual(btk.cmd(0xfc00, b'\x19'), bytes.fromhex('0100fc0119'))
        self.assertEqual(btk.cmd(0x0c03), bytes.fromhex('01030c00'))
        seg = b'\xaa' * 243; pkt = btk.cmd(0xfc00, bytes([0x1e, len(seg)]) + seg)
        self.assertEqual(pkt[:6], bytes.fromhex('0100fcf51ef3')); self.assertEqual(len(pkt), 4 + 245)
    def test_rampatch_matches_chip(self):
        d = (FW/'brhbtfw20.tlv').read_bytes(); self.assertEqual(d[0], 1)
        self.assertEqual(struct.unpack_from('<HH', d, 16), (0x21, 0x200)); self.assertEqual(d[14], 3)

class Wire(unittest.TestCase):
    """Replay the e66fd45f failure shape on a pty: a late rampatch ack must not shift later replies."""
    def setUp(self):
        import os, pty; self.m, self.s = pty.openpty(); btk.RX.clear()
        import termios; a = termios.tcgetattr(self.s); a[3] = 0; a[0] = 0; a[1] = 0; termios.tcsetattr(self.s, termios.TCSANOW, a)
    def tearDown(self):
        import os; os.close(self.m); os.close(self.s)
    def test_pending_packet_blocks_next_command(self):
        import os
        os.write(self.m, bytes.fromhex('040e050100fc001e'))            # late ack arrives before VERSION is sent
        ev = btk.read_event(self.s, 1.0); self.assertEqual(ev.hex(), '040e050100fc001e')
        os.write(self.m, bytes.fromhex('040e050100fc001e') + PROBE)     # two packets in one read: none may be lost
        self.assertEqual(btk.read_event(self.s, 1.0).hex(), '040e050100fc001e')
        ev, ok = btk.cc(self.s, 0xfc00, b'\x19', rtype=0x19)            # PROBE still buffered -> reported, not skipped
        self.assertFalse(ok); self.assertEqual(ev, PROBE)
    def test_rtype_must_match(self):
        import os
        os.write(self.m, bytes.fromhex('040e050100fc001e'))
        ev, ok = btk.cc(self.s, 0xfc00, b'\x19', secs=1.0, rtype=0x19); self.assertFalse(ok)

class Acl(unittest.TestCase):
    E7 = bytes.fromhex('02dc2e24002000ff0000215906010009000030140000000a7fddce0000000041') + bytes(9)   # e7a9f6fe debug packet (36 B payload)
    def setUp(self): btk.RX.clear(); btk.DEBUG.update(count=0, bytes=0, first=[])
    def test_debug_acl_consumed(self):
        btk.RX.extend(self.E7 + bytes.fromhex('040e050100fc001e'))
        self.assertEqual(btk.pop_packet().hex(), '040e050100fc001e'); self.assertEqual(btk.DEBUG['count'], 1)
    def test_partial_acl_waits(self):
        btk.RX.extend(self.E7[:20]); self.assertIsNone(btk.pop_packet()); self.assertEqual(len(btk.RX), 20)
    def test_other_acl_rejected(self):
        btk.RX.extend(bytes.fromhex('0201200000')); self.assertRaises(RuntimeError, btk.pop_packet)

if __name__ == '__main__': unittest.main()
