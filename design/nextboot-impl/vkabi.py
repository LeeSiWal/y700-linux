"""Minimal Vulkan structures for a loader-less ICD probe (64-bit). Pure ctypes; unit-testable."""
import ctypes as C
import struct

VK_SUCCESS = 0
VK_INCOMPLETE = 5
API_1_3 = (1 << 22) | (3 << 12)

class VkApplicationInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('pApplicationName', C.c_char_p),
                ('applicationVersion', C.c_uint32), ('pEngineName', C.c_char_p), ('engineVersion', C.c_uint32),
                ('apiVersion', C.c_uint32)]

class VkInstanceCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32),
                ('pApplicationInfo', C.POINTER(VkApplicationInfo)), ('enabledLayerCount', C.c_uint32),
                ('ppEnabledLayerNames', C.c_void_p), ('enabledExtensionCount', C.c_uint32),
                ('ppEnabledExtensionNames', C.c_void_p)]

PROPS_HEAD = struct.Struct('<IIIII256s16s')   # apiVersion, driverVersion, vendorID, deviceID, deviceType, deviceName, UUID
PROPS_BUF = 4096                               # VkPhysicalDeviceProperties is 824 bytes; oversize buffer

def version(v):
    return '%d.%d.%d' % ((v >> 22) & 0x7f, (v >> 12) & 0x3ff, v & 0xfff)

def parse_props(raw):
    api, drv, vendor, dev, typ, name, uuid = PROPS_HEAD.unpack_from(raw, 0)
    return {'apiVersion': version(api), 'driverVersion': drv, 'vendorID': vendor, 'deviceID': dev,
            'deviceType': typ, 'deviceName': name.split(b'\0', 1)[0].decode(), 'pipelineCacheUUID': uuid.hex()}

# VK_EXT_debug_utils (so release-build vk_errorf messages reach the probe log)
VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT = 1000128004
SEV_ALL = 0x1 | 0x10 | 0x100 | 0x1000        # verbose|info|warning|error
TYPE_ALL = 0x1 | 0x2 | 0x4                    # general|validation|performance

class VkDebugUtilsMessengerCallbackDataEXT(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('pMessageIdName', C.c_char_p),
                ('messageIdNumber', C.c_int32), ('pMessage', C.c_char_p)]   # trailing members unused

DebugCallback = C.CFUNCTYPE(C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(VkDebugUtilsMessengerCallbackDataEXT), C.c_void_p)

class VkDebugUtilsMessengerCreateInfoEXT(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('messageSeverity', C.c_uint32),
                ('messageType', C.c_uint32), ('pfnUserCallback', DebugCallback), ('pUserData', C.c_void_p)]

# ---- compute test structures (64-bit layouts; sizes checked in test_compute.py) ----
H = C.c_uint64   # non-dispatchable handle

class VkQueueFamilyProperties(C.Structure):
    _fields_ = [('queueFlags', C.c_uint32), ('queueCount', C.c_uint32), ('timestampValidBits', C.c_uint32),
                ('granularity', C.c_uint32 * 3)]

class VkMemoryType(C.Structure):
    _fields_ = [('propertyFlags', C.c_uint32), ('heapIndex', C.c_uint32)]
class VkMemoryHeap(C.Structure):
    _fields_ = [('size', C.c_uint64), ('flags', C.c_uint32)]
class VkPhysicalDeviceMemoryProperties(C.Structure):
    _fields_ = [('memoryTypeCount', C.c_uint32), ('memoryTypes', VkMemoryType * 32),
                ('memoryHeapCount', C.c_uint32), ('memoryHeaps', VkMemoryHeap * 16)]

class VkDeviceQueueCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('queueFamilyIndex', C.c_uint32),
                ('queueCount', C.c_uint32), ('pQueuePriorities', C.POINTER(C.c_float))]
class VkDeviceCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('queueCreateInfoCount', C.c_uint32),
                ('pQueueCreateInfos', C.POINTER(VkDeviceQueueCreateInfo)), ('enabledLayerCount', C.c_uint32),
                ('ppEnabledLayerNames', C.c_void_p), ('enabledExtensionCount', C.c_uint32),
                ('ppEnabledExtensionNames', C.c_void_p), ('pEnabledFeatures', C.c_void_p)]

class VkBufferCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('size', C.c_uint64),
                ('usage', C.c_uint32), ('sharingMode', C.c_uint32), ('queueFamilyIndexCount', C.c_uint32),
                ('pQueueFamilyIndices', C.c_void_p)]
class VkMemoryRequirements(C.Structure):
    _fields_ = [('size', C.c_uint64), ('alignment', C.c_uint64), ('memoryTypeBits', C.c_uint32)]
class VkMemoryAllocateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('allocationSize', C.c_uint64), ('memoryTypeIndex', C.c_uint32)]

class VkShaderModuleCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('codeSize', C.c_size_t),
                ('pCode', C.c_void_p)]
class VkDescriptorSetLayoutBinding(C.Structure):
    _fields_ = [('binding', C.c_uint32), ('descriptorType', C.c_uint32), ('descriptorCount', C.c_uint32),
                ('stageFlags', C.c_uint32), ('pImmutableSamplers', C.c_void_p)]
class VkDescriptorSetLayoutCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('bindingCount', C.c_uint32),
                ('pBindings', C.POINTER(VkDescriptorSetLayoutBinding))]
class VkPipelineLayoutCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('setLayoutCount', C.c_uint32),
                ('pSetLayouts', C.POINTER(H)), ('pushConstantRangeCount', C.c_uint32), ('pPushConstantRanges', C.c_void_p)]
class VkPipelineShaderStageCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('stage', C.c_uint32),
                ('module', H), ('pName', C.c_char_p), ('pSpecializationInfo', C.c_void_p)]
class VkComputePipelineCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32),
                ('stage', VkPipelineShaderStageCreateInfo), ('layout', H), ('basePipelineHandle', H),
                ('basePipelineIndex', C.c_int32)]
class VkDescriptorPoolSize(C.Structure):
    _fields_ = [('type', C.c_uint32), ('descriptorCount', C.c_uint32)]
class VkDescriptorPoolCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('maxSets', C.c_uint32),
                ('poolSizeCount', C.c_uint32), ('pPoolSizes', C.POINTER(VkDescriptorPoolSize))]
class VkDescriptorSetAllocateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('descriptorPool', H), ('descriptorSetCount', C.c_uint32),
                ('pSetLayouts', C.POINTER(H))]
class VkDescriptorBufferInfo(C.Structure):
    _fields_ = [('buffer', H), ('offset', C.c_uint64), ('range', C.c_uint64)]
class VkWriteDescriptorSet(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('dstSet', H), ('dstBinding', C.c_uint32),
                ('dstArrayElement', C.c_uint32), ('descriptorCount', C.c_uint32), ('descriptorType', C.c_uint32),
                ('pImageInfo', C.c_void_p), ('pBufferInfo', C.POINTER(VkDescriptorBufferInfo)), ('pTexelBufferView', C.c_void_p)]
class VkCommandPoolCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('queueFamilyIndex', C.c_uint32)]
class VkCommandBufferAllocateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('commandPool', H), ('level', C.c_uint32),
                ('commandBufferCount', C.c_uint32)]
class VkCommandBufferBeginInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32), ('pInheritanceInfo', C.c_void_p)]
class VkMemoryBarrier(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('srcAccessMask', C.c_uint32), ('dstAccessMask', C.c_uint32)]
class VkFenceCreateInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('flags', C.c_uint32)]
class VkSubmitInfo(C.Structure):
    _fields_ = [('sType', C.c_uint32), ('pNext', C.c_void_p), ('waitSemaphoreCount', C.c_uint32),
                ('pWaitSemaphores', C.c_void_p), ('pWaitDstStageMask', C.c_void_p), ('commandBufferCount', C.c_uint32),
                ('pCommandBuffers', C.POINTER(C.c_void_p)), ('signalSemaphoreCount', C.c_uint32), ('pSignalSemaphores', C.c_void_p)]

# sTypes / enums used
ST = dict(DEVICE_QUEUE=2, DEVICE=3, SUBMIT=4, MEMORY_ALLOCATE=5, FENCE=8, BUFFER=12, SHADER_MODULE=16, PIPELINE_SHADER_STAGE=18,
          COMPUTE_PIPELINE=29, PIPELINE_LAYOUT=30, DESCRIPTOR_SET_LAYOUT=32, DESCRIPTOR_POOL=33, DESCRIPTOR_SET_ALLOCATE=34,
          WRITE_DESCRIPTOR_SET=35, COMMAND_POOL=39, COMMAND_BUFFER_ALLOCATE=40, COMMAND_BUFFER_BEGIN=42, MEMORY_BARRIER=46)
QUEUE_COMPUTE = 0x2
MEM_HOST_VISIBLE, MEM_HOST_COHERENT = 0x2, 0x4
BUFFER_USAGE_STORAGE = 0x20
DESCRIPTOR_STORAGE_BUFFER = 7
STAGE_COMPUTE_SHADER = 0x20
BIND_POINT_COMPUTE = 1
ACCESS_SHADER_WRITE, ACCESS_HOST_READ = 0x40, 0x2000
PIPE_COMPUTE, PIPE_HOST = 0x800, 0x4000

class VkPushConstantRange(C.Structure):
    _fields_ = [('stageFlags', C.c_uint32), ('offset', C.c_uint32), ('size', C.c_uint32)]
COMMAND_POOL_RESET_COMMAND_BUFFER = 0x2
