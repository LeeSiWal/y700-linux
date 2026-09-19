"""registry.desktop_state_ok on the recorded native state of boot 684daae1 (host test, no device access)."""
import re, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); import registry as rg
NATIVE = Path('/home/siwal/y700-agent/boot-owner-3c1fefea0bc391dc/native-state.txt')

def desktop(native, fb=340, src='634.000000x2026.000000+0.000000+0.000000'):
    """what the presenter makes of the native text: planes 95/128 show fb with another size, src-pos scaled"""
    out, cur = [], None
    for l in native.splitlines():
        if l and not l[0].isspace(): cur = l.split()[0]
        if cur in rg.DESKTOP_PLANES:
            if l.startswith('\tfb='): l = '\tfb=%d' % fb
            elif l.startswith('\t\tsize='): l = '\t\tsize=634x2026'
            elif l.startswith('\tsrc-pos='): l = '\tsrc-pos=' + src
        if l.strip() == 'planes_changed=0': l = l.replace('=0', '=1')
        out.append(l)
    return '\n'.join(out) + '\n'

@unittest.skipUnless(NATIVE.exists(), 'native state of this boot not present')
class DesktopState(unittest.TestCase):
    def setUp(self): self.native = NATIVE.read_text()
    def test_native_ok(self): self.assertTrue(rg.desktop_state_ok(self.native, self.native, {335}))
    def test_presenter_fb_ok(self): self.assertTrue(rg.desktop_state_ok(self.native, desktop(self.native), {335, 340}))
    def test_unknown_fb_refused(self): self.assertFalse(rg.desktop_state_ok(self.native, desktop(self.native, fb=999), {335, 340}))
    def test_crtc_pos_refused(self):
        now = desktop(self.native).replace('crtc-pos=952x3040+0+0', 'crtc-pos=900x3040+0+0', 1)
        self.assertFalse(rg.desktop_state_ok(self.native, now, {335, 340}))
    def test_other_plane_refused(self):
        now = re.sub(r'(plane\[132\]: plane-2\n\tcrtc=)\(null\)\n\tfb=0', r'\1crtc-0\n\tfb=340', desktop(self.native))
        self.assertNotEqual(now, desktop(self.native)); self.assertFalse(rg.desktop_state_ok(self.native, now, {335, 340}))
    def test_crtc_mode_refused(self):
        now = re.sub(r'(crtc\[205\]: crtc-0\n(?:\t.*\n)*?\t)enable=1', r'\1enable=0', desktop(self.native))
        if now == desktop(self.native): self.skipTest('no enable= line in crtc-0 block')
        self.assertFalse(rg.desktop_state_ok(self.native, now, {335, 340}))

if __name__ == '__main__': unittest.main(verbosity=1)
