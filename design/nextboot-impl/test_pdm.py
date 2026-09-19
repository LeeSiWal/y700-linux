"""Host tests for pdmapper encoding (no device access)."""
import importlib.util, struct, unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('pdm', Path(__file__).with_name('pdmapper.py')); pdm = importlib.util.module_from_spec(spec); spec.loader.exec_module(pdm)
FW = Path('/home/siwal/y700-design/audio-20260919/firmware')
T = pdm.load([FW/'adspr.jsn', FW/'adspua.jsn', FW/'adsps.jsn'])

class P(unittest.TestCase):
    def test_table(self):
        self.assertEqual([d[0] for d in T['tms/servreg']], ['msm/adsp/root_pd', 'msm/adsp/audio_pd', 'msm/adsp/sensor_pd'])
        self.assertEqual([d[0] for d in T['avs/audio']], ['msm/adsp/audio_pd']); self.assertEqual(T['avs/audio'][0][1], 74)
    def test_answer_encoding(self):
        name, off, part, body = pdm.answer(pdm.tlv(0x01, b'avs/audio'), T)
        t = pdm.tlvs(body)
        self.assertEqual(t[0x02], b'\0\0\0\0'); self.assertEqual(struct.unpack('<H', t[0x10])[0], 1)
        lst = t[0x12]; self.assertEqual(lst[0], 1); n = lst[1]; self.assertEqual(lst[2:2 + n], b'msm/adsp/audio_pd')
        inst, valid, data = struct.unpack_from('<IBI', lst, 2 + n); self.assertEqual(inst, 74)
    def test_offset_and_unknown(self):
        _, _, part, body = pdm.answer(pdm.tlv(0x01, b'tms/servreg') + pdm.tlv(0x10, struct.pack('<I', 2)), T)
        self.assertEqual([p[0] for p in part], ['msm/adsp/sensor_pd']); self.assertEqual(struct.unpack('<H', pdm.tlvs(body)[0x10])[0], 3)
        _, _, part, body = pdm.answer(pdm.tlv(0x01, b'nope/none'), T); self.assertEqual(part, []); self.assertEqual(pdm.tlvs(body)[0x12], b'\0')

if __name__ == '__main__': unittest.main()
