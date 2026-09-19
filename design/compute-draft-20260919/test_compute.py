"""Host-only checks: Vulkan struct layouts, SPIR-V artifact, compute log parser. No device access."""
import ctypes as C, hashlib, json, unittest
import vkabi as v
from computelog import ComputeLog

SIZES = {'VkQueueFamilyProperties': 24, 'VkPhysicalDeviceMemoryProperties': 520, 'VkDeviceQueueCreateInfo': 40,
         'VkDeviceCreateInfo': 72, 'VkBufferCreateInfo': 56, 'VkMemoryRequirements': 24, 'VkMemoryAllocateInfo': 32,
         'VkShaderModuleCreateInfo': 40, 'VkDescriptorSetLayoutBinding': 24, 'VkDescriptorSetLayoutCreateInfo': 32,
         'VkPipelineLayoutCreateInfo': 48, 'VkPipelineShaderStageCreateInfo': 48, 'VkComputePipelineCreateInfo': 96,
         'VkDescriptorPoolSize': 8, 'VkDescriptorPoolCreateInfo': 40, 'VkDescriptorSetAllocateInfo': 40,
         'VkDescriptorBufferInfo': 24, 'VkWriteDescriptorSet': 64, 'VkCommandPoolCreateInfo': 24,
         'VkCommandBufferAllocateInfo': 32, 'VkCommandBufferBeginInfo': 32, 'VkMemoryBarrier': 24,
         'VkFenceCreateInfo': 24, 'VkSubmitInfo': 72, 'VkApplicationInfo': 48, 'VkInstanceCreateInfo': 64,
         'VkDebugUtilsMessengerCreateInfoEXT': 48}
DEV = json.dumps({'deviceID': 1141180977, 'deviceName': 'Adreno (TM) 840', 'vendorID': 20803})
def good():
    return ['READY_FOR_COMPUTE', 'ICD_LOADED', 'TU: info: Created an instance', 'INSTANCE_CREATED', 'MESSENGER_CREATED',
            'PHYSICAL_DEVICE ' + DEV, 'QUEUE_FAMILY index=0 flags=0x7 count=1', 'DEVICE_CREATED',
            'BUFFER_READY size=2048 alloc=4096 memtype=1 flags=0x7', 'PIPELINE_CREATED', 'COMMANDS_RECORDED dispatch=4x1x1 local=64',
            'SUBMITTED', 'FENCE_SIGNALED ms=1.25', 'VERIFY written_ok=256/256 untouched=256/256 first_bad=[] sample=[7, 10, 13, 772]',
            'VERIFY_PASS', 'READY_FOR_DESTROY', 'DEVICE_DESTROYED', 'INSTANCE_DESTROYED', 'HELD pid=1 reason=COMPUTE_DONE_DESTROYED']
def run(lines):
    log = ComputeLog()
    for l in lines:
        ev = log.feed(l)
        if ev == 'gate1': log.gates = 1
        if ev == 'gate2': log.gates = 2
    return log

class T(unittest.TestCase):
    def test_sizes(self):
        for n, s in SIZES.items(): self.assertEqual(C.sizeof(getattr(v, n)), s, n)
        self.assertEqual(v.VkComputePipelineCreateInfo.layout.offset, 72)
    def test_spirv(self):
        d = open('fill.spv', 'rb').read()
        self.assertEqual(hashlib.sha256(d).hexdigest(), '8f04a4d33831afd915852a5f372539fb39035e19759b186eb3402cdceba6bb7b')
        self.assertEqual(int.from_bytes(d[:4], 'little'), 0x07230203); self.assertEqual(len(d) % 4, 0)
    def test_good(self):
        log = run(good()); self.assertEqual(log.fence_ms, 1.25); self.assertIsNotNone(log.held)
    def bad(self, i, new):
        l = good(); l[i] = new
        with self.assertRaises(RuntimeError): run(l)
    def test_verify_fail(self): self.bad(13, 'VERIFY written_ok=255/256 untouched=256/256 first_bad=0x0 sample=[]')
    def test_fence_timeout(self): self.bad(12, 'STOP: vkWaitForFences result=2 ms=5000.0')
    def test_vk_error(self): self.bad(7, 'VKMSG sev=0x1000 type=0x1 out of device memory')
    def test_early_hold(self): self.bad(11, 'HELD pid=1 reason=VK_ERROR')
    def test_no_gate2(self):
        log = ComputeLog()
        for l in good()[:16]:
            if log.feed(l) == 'gate1': log.gates = 1
        with self.assertRaises(RuntimeError): log.feed('DEVICE_DESTROYED')
    def test_no_gate1(self):
        log = ComputeLog(); log.feed('READY_FOR_COMPUTE')
        with self.assertRaises(RuntimeError): log.feed('ICD_LOADED')
    def test_wrong_device(self): self.bad(5, 'PHYSICAL_DEVICE ' + json.dumps({'deviceName': 'x'}))

if __name__ == '__main__': unittest.main()
