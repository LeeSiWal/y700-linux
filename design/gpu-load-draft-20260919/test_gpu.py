"""Host-only checks of the KGSL trial's pure guards. No device access."""
import unittest
import gpuguard as g

class T(unittest.TestCase):
    base = '[  1.0] [    T1] boot\n[  2.0] [    T2] kgsl: old failure line\n'
    def test_new_only(self):
        self.assertEqual(g.gpu_faults(self.base, self.base), [])
    def test_kgsl_error(self):
        after = self.base+'[ 9.0] [  T900] kgsl kgsl-3d0: Unable to map GMU kernel block\n'
        self.assertEqual(len(g.gpu_faults(self.base, after)), 1)
    def test_gmu_fault(self):
        after = self.base+'[ 9.1] [  T900] adreno-gen8-gmu 3d37000.qcom,gmu: GMU watchdog expired interrupt received fault\n'
        self.assertEqual(len(g.gpu_faults(self.base, after)), 1)
    def test_zap_denied(self):
        after = self.base+'[ 9.2] [  T900] kgsl kgsl-3d0: SCM zap resume call failed: -13\n'
        self.assertEqual(len(g.gpu_faults(self.base, after)), 1)
    def test_benign(self):
        after = self.base+'[ 9.3] [  T900] kgsl kgsl-3d0: Initialized kgsl version 3\n'
        self.assertEqual(g.gpu_faults(self.base, after), [])
    def test_fw_mentions(self):
        after = self.base+'[ 9.4] [ T9] firmware_class: loading gen80200_gmu.bin\n'
        self.assertEqual(len(g.firmware_mentions(self.base, after)), 1)
    def test_underruns(self):
        self.assertEqual(g.underruns('intf:1    vsync:  70  underrun:  13    mode: video\nintf:2    vsync:   0  underrun:  13    mode: video\n'), {1: 13, 2: 13})
        with self.assertRaises(RuntimeError): g.underruns('intf:1 x')

if __name__ == '__main__': unittest.main()
