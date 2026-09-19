"""Host-only checks of the rate supervisor's parsing. No device files, no DRM."""
import importlib.util, unittest
from pathlib import Path
from ratelog import RateLog, fb_at

def good(steps=120,dt=8333,first=8200):
    times=[first+dt*i for i in range(steps+1)]
    span=times[steps-1]-times[0]
    lines=['INHERITED_DRM_FILE x','RETAINED_FB id=341 1904x3040 pitch=7680','RETAINED_FB id=342 1904x3040 pitch=7680',
           'RETAINED_FB id=335 1904x3040 pitch=7680',f'PLAN steps={steps} back-to-back','TEST_ONLY_PASS','READY_FOR_RATE','RATE_ENTER',
           f'RATE_SUMMARY steps={steps} intervals={steps-1} span_us={span} mean_us={dt} min_us={dt} max_us={dt} slow=0 fps_x100={(steps-1)*100000000//span} first_us={first}']
    lines+=[f'T {k} {fb_at(k,steps)} {times[k-1]}' for k in range(1,steps+2)]
    return lines+['RATE_RETURNED','HELD pid=1 reason=RATE_DONE_FB335_RETAINED']

def run(lines,steps=120):
    log=RateLog(steps)
    for line in lines:
        if log.feed(line)=='ready':log.go_sent=True
    return log

class T(unittest.TestCase):
    def test_good(self):
        log=run(good());self.assertTrue(log.returned);self.assertEqual(log.summary['fps_x100'],12000)
    def test_good_1200(self):self.assertTrue(run(good(1200),1200).returned)
    def bad(self,i,new,steps=120):
        l=good(steps);l[i]=new
        with self.assertRaises(RuntimeError):run(l,steps)
    def test_wrong_retained(self):self.bad(1,'RETAINED_FB id=343 1904x3040 pitch=7680')
    def test_plan_steps(self):self.bad(4,'PLAN steps=1200 back-to-back')
    def test_wrong_fb_in_times(self):self.bad(10,'T 2 341 16533')
    def test_final_not_335(self):
        l=good();i=l.index('RATE_RETURNED')-1;l[i]=l[i].replace(' 335 ',' 342 ')
        with self.assertRaises(RuntimeError):run(l)
    def test_times_not_increasing(self):self.bad(11,'T 3 341 100')
    def test_span_mismatch(self):
        l=good();l[8]=l[8].replace('span_us=','span_us=1')
        with self.assertRaises(RuntimeError):run(l)
    def test_partial(self):self.bad(9,'RATE_PARTIAL completed=5')
    def test_failed_at(self):self.bad(9,'RATE_FAILED_AT 6 fb=342')
    def test_stop(self):self.bad(9,'STOP: flip event timeout; state uncertain')
    def test_early_hold(self):
        l=good()[:9]+['HELD pid=1 reason=RATE_ERROR_STATE_UNCERTAIN']
        with self.assertRaises(RuntimeError):run(l)
    def test_enter_before_gate(self):
        log=RateLog(120)
        for line in good()[:6]:log.feed(line)
        with self.assertRaises(RuntimeError):log.feed('RATE_ENTER')
    def test_supervisor_imports(self):
        spec=importlib.util.spec_from_file_location('rr',Path(__file__).with_name('run-rate.py'))
        rr=importlib.util.module_from_spec(spec);spec.loader.exec_module(rr)
        self.assertEqual(rr.underruns('intf:1    vsync:      70     underrun:      13    mode: video\nintf:2    vsync:       0     underrun:      13    mode: video\n'),{1:13,2:13})

if __name__=='__main__':unittest.main()
