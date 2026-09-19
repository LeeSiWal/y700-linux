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
