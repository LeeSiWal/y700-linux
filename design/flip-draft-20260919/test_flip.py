"""Host-only checks of the flip supervisor's parsing. No device files, no DRM."""
import importlib.util, unittest
from pathlib import Path
from fliplog import FlipLog, STEPS

def good():
    lines=['INHERITED_DRM_FILE x','BUFFER A fb=400 handle=4 readback_pixels=5788160','BUFFER B fb=401 handle=5 readback_pixels=5788160',
           'PLAN steps=8','TEST_ONLY_PASS','READY_FOR_FLIPS']
    for k in range(1,STEPS+1):
        l,fb=('A',400) if k%2 else ('B',401)
        lines+=[f'FLIP_ENTER {l} fb={fb} user={k}',f'FLIP_DONE {l} fb={fb} seq={100+k} ts=10.{k:06d} ioctl_to_event_ms=8.30']
    lines+=[f'FLIP_ENTER RETURN_FB335 fb=335 user={STEPS+1}',f'FLIP_DONE RETURN_FB335 fb=335 seq={200} ts=11.000000 ioctl_to_event_ms=8.1',
            'FLIPS_RETURNED','HELD pid=1 reason=FLIP_BUFFERS_RETAINED_VISUAL_CONFIRMATION_REQUIRED']
    return lines

def run(lines):
    log=FlipLog()
    for line in lines:
        if log.feed(line)=='ready':log.go_sent=True
    return log

class T(unittest.TestCase):
    def test_good(self):
        log=run(good());self.assertTrue(log.returned);self.assertEqual(len(log.done),STEPS+1)
    def mutate(self,i,new):
        l=good();l[i]=new
        with self.assertRaises(RuntimeError):run(l)
    def test_wrong_fb(self):self.mutate(6,'FLIP_ENTER A fb=401 user=1')
    def test_wrong_user(self):self.mutate(6,'FLIP_ENTER A fb=400 user=2')
    def test_old_fb(self):self.mutate(1,'BUFFER A fb=335 handle=4 readback_pixels=5788160')
    def test_short_readback(self):self.mutate(1,'BUFFER A fb=400 handle=4 readback_pixels=5788159')
    def test_done_label_mismatch(self):self.mutate(9,'FLIP_DONE A fb=401 seq=102 ts=10.000002 ioctl_to_event_ms=8.30')
    def test_stop_line(self):self.mutate(9,'STOP: flip event timeout; state uncertain')
    def test_early_hold(self):
        l=good()[:8]+['HELD pid=1 reason=FLIP_ERROR_STATE_UNCERTAIN']
        with self.assertRaises(RuntimeError):run(l)
    def test_return_before_all(self):
        l=good();i=l.index('FLIPS_RETURNED');l=l[:8]+[l[i]]
        with self.assertRaises(RuntimeError):run(l)
    def test_flip_before_gate(self):
        log=FlipLog()
        for line in good()[:5]:log.feed(line)
        with self.assertRaises(RuntimeError):log.feed('FLIP_ENTER A fb=400 user=1')
    def test_gate_before_buffers(self):
        with self.assertRaises(RuntimeError):FlipLog().feed('READY_FOR_FLIPS')
    def test_underrun_parse(self):
        spec=importlib.util.spec_from_file_location('rf',Path(__file__).with_name('run-flip.py'))
        rf=importlib.util.module_from_spec(spec);spec.loader.exec_module(rf)
        text='intf:1    vsync:      70     underrun:      13    mode: video\nintf:2    vsync:       0     underrun:      13    mode: video\n'
        self.assertEqual(rf.underruns(text),{1:13,2:13})
        with self.assertRaises(RuntimeError):rf.underruns('intf:1 garbage')

if __name__=='__main__':unittest.main()
