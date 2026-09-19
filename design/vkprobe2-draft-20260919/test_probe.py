"""Host-only checks for the Vulkan probe parser and ABI sizes. No device access."""
import ctypes as C, json, unittest
import vkabi as v
from probelog import ProbeLog

P = {'apiVersion': '1.4.335', 'driverVersion': 1, 'vendorID': 0x5143, 'deviceID': 0x44050a31, 'deviceType': 1,
     'deviceName': 'Adreno (TM) 840', 'pipelineCacheUUID': '00'*16}
def good():
    return ['READY_FOR_PROBE', 'ICD_LOADED', 'NEGOTIATED interface=5', 'MESA: info: Created an instance', 'INSTANCE_CREATED',
            'MESSENGER_CREATED', 'ENUMERATE_ENTER', 'VKMSG sev=0x10 type=0x1 Found compatible device', 'MESA: warning: Unable to open neither /dev/dma_heap/system nor /dev/ion', 'PHYSICAL_DEVICES count=1',
            'PROPERTIES ' + json.dumps(P), 'HELD pid=1 reason=VK_INSTANCE_RETAINED']
def run(lines):
    log = ProbeLog()
    for l in lines:
        if log.feed(l) == 'ready': log.go_sent = True
    return log

class T(unittest.TestCase):
    def test_abi(self):
        self.assertEqual((C.sizeof(v.VkApplicationInfo), C.sizeof(v.VkInstanceCreateInfo)), (48, 64))
        raw = v.PROPS_HEAD.pack((1 << 22) | (4 << 12) | 335, 7, 0x5143, 0x44050a31, 1, b'Adreno (TM) 840', b'\1'*16) + b'\0'*100
        p = v.parse_props(raw); self.assertEqual((p['apiVersion'], p['deviceName'], p['vendorID']), ('1.4.335', 'Adreno (TM) 840', 0x5143))
    def test_good(self):
        log = run(good()); self.assertEqual(log.props['deviceName'], 'Adreno (TM) 840'); self.assertEqual(len(log.mesa), 3)
    def bad(self, i, new):
        l = good(); l[i] = new
        with self.assertRaises(RuntimeError): run(l)
    def test_mesa_error(self): self.bad(3, 'MESA: error: vkCreateInstance failed')
    def test_vk_error_msg(self):
        l = good(); l.insert(7, 'VKMSG sev=0x1000 type=0x1 unknown UBWC version 0x6')
        with self.assertRaises(RuntimeError): run(l)
    def test_messenger_sizes(self):
        self.assertEqual(C.sizeof(v.VkDebugUtilsMessengerCreateInfoEXT), 48)
        self.assertEqual(v.VkDebugUtilsMessengerCallbackDataEXT.pMessage.offset, 40)
    def test_stop(self): self.bad(4, 'STOP: vkCreateInstance result=-3')
    def test_two_devices(self): self.bad(9, 'PHYSICAL_DEVICES count=2')
    def test_wrong_device(self): self.bad(10, 'PROPERTIES ' + json.dumps(dict(P, deviceName='llvmpipe', vendorID=0x10005)))
    def test_early_hold(self): self.bad(6, 'HELD pid=1 reason=INSTANCE_FAILED')
    def test_out_of_order(self):
        l = good(); l[4], l[6] = l[6], l[4]
        with self.assertRaises(RuntimeError): run(l)
    def test_before_gate(self):
        log = ProbeLog(); log.feed('READY_FOR_PROBE')
        with self.assertRaises(RuntimeError): log.feed('ICD_LOADED')

if __name__ == '__main__': unittest.main()
