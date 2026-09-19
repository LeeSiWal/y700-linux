"""Host tests for wavtool (no device)."""
import tempfile, unittest, wave
from array import array
from pathlib import Path
import wavtool as w

class W(unittest.TestCase):
    def mk(self, ch, sw, rate, n=4800):
        d = Path(tempfile.mkdtemp()); p = d/'t.wav'; f = wave.open(str(p), 'wb'); f.setnchannels(ch); f.setsampwidth(sw); f.setframerate(rate)
        if sw == 2: f.writeframes(array('h', [1000] * (n * ch)).tobytes())
        else: f.writeframes(bytes([200]) * (n * ch))
        f.close(); return p
    def test_mono_16_to_stereo(self):
        fr, info = w.read_wav(self.mk(1, 2, 48000)); self.assertEqual(fr[0], (1000, 1000)); self.assertEqual(len(fr), 4800); self.assertFalse(info['resampled'])
    def test_resample_44k1(self):
        fr, info = w.read_wav(self.mk(2, 2, 44100, 4410)); self.assertTrue(info['resampled']); self.assertEqual(len(fr), 4800)
    def test_8bit(self):
        fr, _ = w.read_wav(self.mk(1, 1, 48000)); self.assertEqual(fr[0], (72 << 8, 72 << 8))
    def test_limit_caps_peak(self):
        fr, lim = w.limit([(32767, -32767)], -12.0); self.assertLessEqual(abs(fr[0][0]), lim['cap']); self.assertLess(lim['gain'], 1)
    def test_channel_pulses(self):
        p = w.pulses(660, 1, 0.1, 0.0, left=False, right=True); self.assertTrue(all(l == 0 for l, r in p)); self.assertTrue(any(r for l, r in p))
    def test_melody_hash_stable(self):
        import hashlib; d = Path(tempfile.mkdtemp()); w.make_melody(d/'m.wav')
        self.assertEqual(hashlib.sha256((d/'m.wav').read_bytes()).hexdigest(), 'fb1979a37ff35a5a139ce90f6a9056810c8413674f7589638295e0fe7c608e4b')

if __name__ == '__main__': unittest.main()
