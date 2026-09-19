"""Host-only checks of stage-2 parsing and the KGSL ABI subset. No device access."""
import json, unittest
import kgslabi as k
from openlog import OpenLog, CHIP_ID

INFO = {'device_id': 1, 'chip_id': CHIP_ID, 'mmu_enabled': 1, 'gmem_gpubaseaddr': 0, 'gpu_id': 840, 'gmem_sizebytes': 1 << 21}
def good():
    return ['READY_FOR_OPEN', 'OPEN_ENTER', 'OPEN_RETURNED fd=3 ms=412.5', 'DEVINFO ' + json.dumps(INFO), 'HELD pid=9 reason=KGSL_FD_RETAINED']
def run(lines):
    log = OpenLog()
    for l in lines:
        if log.feed(l) == 'ready': log.go_sent = True
    return log

class T(unittest.TestCase):
    def test_abi(self):
        self.assertEqual(k.IOCTL_KGSL_DEVICE_GETPROPERTY, 0xC0180902)
        self.assertEqual((k.DEVINFO.size, k.GETPROP.size), (40, 24))
        raw = k.DEVINFO.pack(1, CHIP_ID, 1, 0, 840, 1 << 21)
        self.assertEqual(k.parse_devinfo(raw)['chip_id'], CHIP_ID)
    def test_good(self):
        log = run(good()); self.assertEqual(log.devinfo['chip_id'], CHIP_ID); self.assertEqual(log.open_ms, 412.5)
    def bad(self, i, new):
        l = good(); l[i] = new
        with self.assertRaises(RuntimeError): run(l)
    def test_wrong_chip(self): self.bad(3, 'DEVINFO ' + json.dumps(dict(INFO, chip_id=0x43050a01)))
    def test_open_failed(self): self.bad(2, 'STOP: open failed errno=110 Connection timed out ms=10000.0')
    def test_devinfo_failed(self): self.bad(3, 'STOP: DEVINFO errno=22 Invalid argument')
    def test_early_hold(self): self.bad(3, 'HELD pid=9 reason=OPEN_FAILED')
    def test_unknown_line(self): self.bad(2, 'something else')
    def test_enter_before_gate(self):
        log = OpenLog(); log.feed('READY_FOR_OPEN')
        with self.assertRaises(RuntimeError): log.feed('OPEN_ENTER')

if __name__ == '__main__': unittest.main()
