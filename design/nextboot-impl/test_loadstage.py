"""Host tests for load-stage preflight on generated dry-run bundles (live modules simulated as not yet loaded). Read-only."""
import importlib.util, json, subprocess, sys, tempfile, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent

def build(stage, tmp):
    out = Path(tmp)/stage
    subprocess.run([sys.executable, '-B', str(HERE/'make-boot-bundle.py'), stage, '--out', str(out)], check=True, capture_output=True)
    return out

def load_mod(out):
    sys.path.insert(0, str(out))
    for k in ('bootmon', 'bootguard'): sys.modules.pop(k, None)
    spec = importlib.util.spec_from_file_location('ls_' + out.name, out/'load-stage.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); sys.path.pop(0)
    return mod

class T(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.mkdtemp()
    def pre(self, stage, hide, tamper=None):
        out = build(stage, self.tmp); ls = load_mod(out)
        m = json.loads((out/'manifest.json').read_text())
        if tamper: tamper(out, m)
        t = json.loads((out/'review-templates.json').read_text())
        real = ls.bm.live_modules
        ls.bm.live_modules = lambda: real() - set(hide)
        mon = ls.bm.BootMonitor(m, t)
        mon.m['module_names'] = sorted(set(m['module_names']) - set(hide))
        return ls, m, mon
    def test_display_preflight_passes(self):
        ls, m, mon = self.pre('display', [f[:-3].replace('-', '_') for f in ['msm_hfi_core.ko', 'nvmem_qfprom.ko', 'qti-fixed-regulator.ko', 'nvt_36xxx.ko', 'ipclite.ko', 'msm_hw_fence.ko', 'synx-driver.ko', 'qcom_va_minidump.ko', 'sync_fence.ko', 'msm_ext_display.ko', 'gh_irq_lend.ko', 'smcinvoke_dlkm.ko', 'hdcp_qseecom_dlkm.ko', 'drm_display_helper.ko', 'msm_drm.ko']])
        for k in list(m['module_notes']):
            if k not in mon.m['module_names']: del mon.m['module_notes'][k]
        ls.preflight(m, mon)
    def test_tampered_module_rejected(self):
        def tamper(out, m): (out/'msm_drm.ko').write_bytes((out/'msm_drm.ko').read_bytes() + b'\0')
        ls, m, mon = self.pre('display', ['msm_drm'], tamper)
        with self.assertRaises(RuntimeError): ls.preflight(m, mon)
    def test_already_loaded_rejected(self):
        import subprocess
        live = open('/proc/modules').read()
        stage = 'gpu' if 'msm_kgsl ' in live else 'display'          # whichever stage is already loaded on this boot
        ls, m, mon = self.pre(stage, [])
        with self.assertRaises(RuntimeError): ls.preflight(m, mon)
    def test_missing_prerequisite_rejected(self):
        ls, m, mon = self.pre('gpu', ['msm_kgsl', 'coresight', 'msm_sysstats', 'msm_performance', 'governor_msm_adreno_tz',
                                      'governor_gpubw_mon', 'governor_msm_adreno_ro', 'msm_drm'])
        with self.assertRaises(RuntimeError) as e: ls.preflight(m, mon)
        self.assertIn('prerequisite', str(e.exception))
    def test_manifest_shape(self):
        out = build('gpu', self.tmp); m = json.loads((out/'manifest.json').read_text())
        self.assertEqual(m['bind_now'], ['hfi', 'fence']); self.assertEqual(len(m['load_order']), 7)
        self.assertEqual(set(m['load_notes']), {f[:-3] for f in m['load_order']})
        for f in m['load_order']: self.assertIn(f, m['files'])

if __name__ == '__main__': unittest.main()
