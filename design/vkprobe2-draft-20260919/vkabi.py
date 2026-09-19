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
