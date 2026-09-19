#!/usr/bin/env python3
"""Loader-less turnip(KGSL) probe: negotiate ICD interface, vkCreateInstance, vkEnumeratePhysicalDevices,
vkGetPhysicalDeviceProperties. No VkDevice, no command submission. The instance is NOT destroyed (the kgsl fd stays
open) and the process holds; the close path is tested separately later."""
import ctypes as C, json, os, signal, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vkabi as v

ICD = '/home/siwal/y700-gpu/turnip-kgsl-26.2.3/lib/libvulkan_freedreno.so'
def out(*a): print(*a, flush=True)
def hold(reason):
    out('HELD pid=%d reason=%s' % (os.getpid(), reason))
    while True: time.sleep(1)

def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    out('READY_FOR_PROBE')
    if sys.stdin.readline() != 'GO\n': hold('GATE_NOT_RELEASED')
    lib = C.CDLL(ICD, mode=os.RTLD_NOW | os.RTLD_LOCAL)
    out('ICD_LOADED')
    ver = C.c_uint32(5)
    neg = lib.vk_icdNegotiateLoaderICDInterfaceVersion; neg.restype = C.c_int32; neg.argtypes = [C.POINTER(C.c_uint32)]
    r = neg(C.byref(ver))
    if r != v.VK_SUCCESS: out('STOP: negotiate result=%d' % r); hold('NEGOTIATE_FAILED')
    out('NEGOTIATED interface=%d' % ver.value)
    gipa = lib.vk_icdGetInstanceProcAddr; gipa.restype = C.c_void_p; gipa.argtypes = [C.c_void_p, C.c_char_p]
    def fn(inst, name, *sig):
        p = gipa(inst, name)
        if not p: out('STOP: missing entry point %s' % name.decode()); hold('ENTRYPOINT_MISSING')
        return C.CFUNCTYPE(*sig)(p)
    create = fn(None, b'vkCreateInstance', C.c_int32, C.POINTER(v.VkInstanceCreateInfo), C.c_void_p, C.POINTER(C.c_void_p))
    app = v.VkApplicationInfo(0, None, b'y700-vkprobe', 1, b'none', 0, v.API_1_3)
    exts = (C.c_char_p * 1)(b'VK_EXT_debug_utils')
    @v.DebugCallback
    def on_message(sev, types, data, user):
        msg = data.contents.pMessage.decode(errors='replace') if data and data.contents.pMessage else ''
        out('VKMSG sev=0x%x type=0x%x %s' % (sev, types, msg.replace('\n', ' | ')))
        return 0
    global _keep; _keep = on_message
    dbg = v.VkDebugUtilsMessengerCreateInfoEXT(v.VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT, None, 0,
                                               v.SEV_ALL, v.TYPE_ALL, on_message, None)
    ci = v.VkInstanceCreateInfo(1, C.cast(C.pointer(dbg), C.c_void_p), 0, C.pointer(app), 0, None, 1, C.cast(exts, C.c_void_p))
    inst = C.c_void_p()
    r = create(C.byref(ci), None, C.byref(inst))
    if r != v.VK_SUCCESS: out('STOP: vkCreateInstance result=%d' % r); hold('INSTANCE_FAILED')
    out('INSTANCE_CREATED')
    mk = fn(inst, b'vkCreateDebugUtilsMessengerEXT', C.c_int32, C.c_void_p, C.POINTER(v.VkDebugUtilsMessengerCreateInfoEXT), C.c_void_p, C.POINTER(C.c_uint64))
    messenger = C.c_uint64()
    r = mk(inst, C.byref(dbg), None, C.byref(messenger))
    if r != v.VK_SUCCESS: out('STOP: vkCreateDebugUtilsMessengerEXT result=%d' % r); hold('MESSENGER_FAILED')
    out('MESSENGER_CREATED')
    enum = fn(inst, b'vkEnumeratePhysicalDevices', C.c_int32, C.c_void_p, C.POINTER(C.c_uint32), C.POINTER(C.c_void_p))
    n = C.c_uint32(0)
    out('ENUMERATE_ENTER')
    r = enum(inst, C.byref(n), None)
    if r != v.VK_SUCCESS: out('STOP: vkEnumeratePhysicalDevices(count) result=%d' % r); hold('ENUMERATE_FAILED')
    out('PHYSICAL_DEVICES count=%d' % n.value)
    if n.value != 1: out('STOP: expected exactly one physical device'); hold('DEVICE_COUNT')
    devs = (C.c_void_p * 1)()
    r = enum(inst, C.byref(n), devs)
    if r not in (v.VK_SUCCESS, v.VK_INCOMPLETE): out('STOP: vkEnumeratePhysicalDevices result=%d' % r); hold('ENUMERATE_FAILED')
    props = fn(inst, b'vkGetPhysicalDeviceProperties', None, C.c_void_p, C.c_void_p)
    buf = C.create_string_buffer(v.PROPS_BUF)
    props(devs[0], buf)
    out('PROPERTIES ' + json.dumps(v.parse_props(buf.raw), sort_keys=True))
    hold('VK_INSTANCE_RETAINED')

main()
