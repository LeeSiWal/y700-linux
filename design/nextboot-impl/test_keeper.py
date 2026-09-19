"""Host tests for gpu-keeper parsing and registry display checks (fake readers). No device access."""
import json, unittest
from pathlib import Path
import registry as rg
from keeperlog import KeeperLog, CHIP_ID

def good():
    d = {'chip_id': CHIP_ID, 'device_id': 1, 'gmem_gpubaseaddr': 0, 'gmem_sizebytes': 18874368, 'gpu_id': 0, 'mmu_enabled': 1}
    return ['READY_FOR_OPEN', 'OPEN_ENTER', 'OPEN_RETURNED fd=4 ms=77.4', 'DEVINFO ' + json.dumps(d), 'HELD pid=9 reason=KGSL_FD_RETAINED']
def run(lines):
    l = KeeperLog()
    for x in lines:
        if l.feed(x) == 'ready': l.go_sent = True
    return l

class FakeC:
    def __init__(self, files): self.files = files
    def read(self, p, binary=False):
        v = self.files[str(p)]; return v.encode() if binary and isinstance(v, str) else v
    def need(self, ok, msg):
        if not ok: raise RuntimeError(msg)
    def task_stat(self, text): return {'starttime': text.split()[21]}

REG = {'owner': {'pid': 500, 'starttime': '777', 'cmd': ['python3', '-B', '/b/display-owner.py']}, 'encoder_status': '/enc',
       'underrun_base': {'1': 13, '2': 13}}
def files(**over):
    f = {'/proc/500/stat': '500 (python3) S ' + ' '.join(['0'] * 18) + ' 777 0', '/proc/500/cmdline': 'python3\0-B\0/b/display-owner.py\0',
         '/sys/kernel/debug/dri/0/clients': 'command tgid dev master a uid magic\n python3 500 0 y y 0 0\n',
         '/sys/kernel/debug/dri/0/state': 'STATE', '/enc': 'intf:1    vsync: 5 underrun:   13 mode: video\nintf:2 vsync: 0 underrun: 13 mode: video\n'}
    f.update(over); return f

class T(unittest.TestCase):
    def test_good(self): self.assertEqual(run(good()).fd, 4)
    def test_wrong_chip(self):
        l = good(); l[3] = l[3].replace(str(CHIP_ID), str(0x44050a01))
        with self.assertRaises(RuntimeError): run(l)
    def test_open_before_gate(self):
        l = KeeperLog(); l.feed('READY_FOR_OPEN')
        with self.assertRaises(RuntimeError): l.feed('OPEN_ENTER')
    def test_display_ok(self):
        import pathlib
        orig = pathlib.Path.exists; pathlib.Path.exists = lambda self: str(self) == '/proc/500' or orig(self)
        try:
            rg.display_ok(FakeC(files()), REG, 'STATE')
            for bad in (files(**{'/sys/kernel/debug/dri/0/state': 'OTHER'}),
                        files(**{'/sys/kernel/debug/dri/0/clients': 'command tgid dev master a uid magic\n python3 500 0 y y 0 0\n x 9 0 n y 0 0\n'}),
                        files(**{'/enc': 'intf:1 vsync: 5 underrun: 14 mode: video\nintf:2 vsync: 0 underrun: 13 mode: video\n'}),
                        files(**{'/proc/500/cmdline': 'python3\0-B\0/other.py\0'})):
                with self.assertRaises(RuntimeError): rg.display_ok(FakeC(bad), REG, 'STATE')
        finally: pathlib.Path.exists = orig

if __name__ == '__main__': unittest.main()
