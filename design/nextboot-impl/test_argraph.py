"""Host tests for the AudioReach packet builder: layouts cross-checked against upstream struct sizes."""
import struct, unittest
import argraph as g

def walk(payload):
    out, i = [], 0
    while i < len(payload):
        iid, pid, size, err = struct.unpack_from('<IIII', payload, i); out.append((iid, pid, size, payload[i + 16:i + 16 + size])); i += 16 + size
    return out

class A(unittest.TestCase):
    def test_graph_open_blocks(self):
        blocks = walk(g.graph_open_payload())
        self.assertEqual([b[1] for b in blocks], [0x08001001, 0x08001000, 0x08001002, 0x08001003, 0x08001004])
        for iid, pid, size, data in blocks: self.assertEqual(iid, 1); self.assertEqual((16 + size) % 8, 0)
        sg = blocks[0][3]; self.assertEqual(struct.unpack_from('<I', sg)[0], 2)
        self.assertEqual(len(sg), 96)                                      # 4 + 2 x sizeof(apm_sub_graph_data)=44, padded to 8
        ct = blocks[1][3]; self.assertEqual(struct.unpack_from('<I', ct)[0], 2); self.assertEqual(len(ct), 4 + 2 * 96 + 4)   # 7 props: 8 + 16 + 6 x 12 = 96
        mp = blocks[3][3]; self.assertEqual(struct.unpack_from('<I', mp)[0], 3); self.assertEqual(len(mp), 4 + 3 * 24 + 4)   # apm_mod_prop_obj = 24
        mc = blocks[4][3]; self.assertEqual(struct.unpack('<9I', mc[:36])[0], 2)
        self.assertEqual(struct.unpack_from('<4I', mc, 4), (g.IID_SHM, 1, g.IID_CNV, 2))
        self.assertEqual(struct.unpack_from('<IIIIII', ct, 4)[:6], (g.CONT_STREAM, 7, 0x08001011, 8, 1, g.TYPE_GC))
        ml = blocks[2][3]; n, sg1, c1, nm = struct.unpack_from('<IIII', ml); self.assertEqual((n, sg1, c1, nm), (2, g.SG_STREAM, g.CONT_STREAM, 2))
    def test_set_cfg(self):
        blocks = walk(g.set_cfg_payload())
        self.assertEqual([(b[0], b[1]) for b in blocks], [(g.IID_SHM, 0x0800100C), (g.IID_CNV, 0x08001008), (g.IID_I2S, 0x08001019), (g.IID_I2S, 0x08001017), (g.IID_I2S, 0x08001018)])
        self.assertEqual(blocks[2][2], 16)                                          # APM_I2S_INTF_CFG_PSIZE 32 - 16
        self.assertEqual(struct.unpack_from('<IIHH', blocks[2][3]), (0, 0, 2, 1))           # speaker data line SD1
        self.assertEqual(struct.unpack_from('<IHHI', blocks[3][3]), (48000, 16, 2, 1))
        mf = blocks[0][3]; self.assertEqual(struct.unpack_from('<III', mf), (1, 0x09001000, 20)); self.assertEqual(struct.unpack_from('<IHHHHHH', mf, 12), (48000, 16, 1, 16, 15, 1, 2))
    def test_mgmt_and_buffers(self):
        b = walk(g.mgmt_payload([g.SG_STREAM, g.SG_DEVICE])); self.assertEqual(b[0][1], 0x08001005); self.assertEqual(struct.unpack_from('<III', b[0][3]), (2, g.SG_STREAM, g.SG_DEVICE))
        self.assertEqual(len(g.wr_buffer(0, 5, 8192)), 44); self.assertEqual(g.mmap_payload(9, 1000)[:8], struct.pack('<HHI', 3, 1, 4))
        pcm, amp = g.sine_s16_stereo(440, 0.01); self.assertEqual(len(pcm), 480 * 4); self.assertLess(max(abs(x) for x in struct.unpack('<%dh' % (len(pcm) // 2), pcm)), 1100)

if __name__ == '__main__': unittest.main()
