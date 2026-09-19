#!/usr/bin/env python3
"""Turnip(KGSL) first compute submission, loader-less. Phase A (gate GO): instance -> device -> host-visible storage
buffer (2048 B, sentinel-filled) -> SPIR-V fill shader (d[i]=3i+7, 4x64 invocations) -> one submit -> fence wait (5 s)
-> CPU verify 256 written + 256 untouched words. Phase B (second gate GO): destroy all objects, vkDestroyDevice,
vkDestroyInstance (closes this process's kgsl fd; PID13398 still holds its fd). Any error: HOLD, no retry."""
import ctypes as C, json, os, signal, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vkabi as v

HERE = os.path.dirname(os.path.abspath(__file__))
ICD = '/home/siwal/y700-gpu/turnip-kgsl-26.2.3/lib/libvulkan_freedreno.so'
N, SIZE, SENTINEL = 256, 2048, 0xDEADBEEF
def out(*a): print(*a, flush=True)
def hold(reason):
    out('HELD pid=%d reason=%s' % (os.getpid(), reason))
    while True: time.sleep(1)
def gate(tag):
    out(tag)
    if sys.stdin.readline() != 'GO\n': hold('GATE_NOT_RELEASED')
def ok(r, what):
    if r != v.VK_SUCCESS: out('STOP: %s result=%d' % (what, r)); hold('VK_ERROR')

def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    spv = open(os.path.join(HERE, 'fill.spv'), 'rb').read()
    gate('READY_FOR_COMPUTE')
    lib = C.CDLL(ICD, mode=os.RTLD_NOW | os.RTLD_LOCAL); out('ICD_LOADED')
    ver = C.c_uint32(5); neg = lib.vk_icdNegotiateLoaderICDInterfaceVersion
    neg.restype = C.c_int32; neg.argtypes = [C.POINTER(C.c_uint32)]; ok(neg(C.byref(ver)), 'negotiate')
    gipa = lib.vk_icdGetInstanceProcAddr; gipa.restype = C.c_void_p; gipa.argtypes = [C.c_void_p, C.c_char_p]
    def ifn(inst, name, *sig):
        p = gipa(inst, name)
        if not p: out('STOP: missing %s' % name.decode()); hold('ENTRYPOINT_MISSING')
        return C.CFUNCTYPE(*sig)(p)
    I, P, VP, U32, U64, PH = C.c_int32, C.POINTER, C.c_void_p, C.c_uint32, C.c_uint64, C.POINTER(v.H)
    @v.DebugCallback
    def on_message(sev, types, data, user):
        msg = data.contents.pMessage.decode(errors='replace') if data and data.contents.pMessage else ''
        out('VKMSG sev=0x%x type=0x%x %s' % (sev, types, msg.replace('\n', ' | '))); return 0
    global _keep; _keep = on_message
    dbg = v.VkDebugUtilsMessengerCreateInfoEXT(v.VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT, None, 0, v.SEV_ALL, v.TYPE_ALL, on_message, None)
    app = v.VkApplicationInfo(0, None, b'y700-vkcompute', 1, b'none', 0, v.API_1_3)
    exts = (C.c_char_p * 1)(b'VK_EXT_debug_utils')
    ci = v.VkInstanceCreateInfo(1, C.cast(C.pointer(dbg), VP), 0, C.pointer(app), 0, None, 1, C.cast(exts, VP))
    inst = VP(); ok(ifn(None, b'vkCreateInstance', I, P(v.VkInstanceCreateInfo), VP, P(VP))(C.byref(ci), None, C.byref(inst)), 'vkCreateInstance')
    out('INSTANCE_CREATED')
    messenger = U64(); ok(ifn(inst, b'vkCreateDebugUtilsMessengerEXT', I, VP, P(v.VkDebugUtilsMessengerCreateInfoEXT), VP, P(U64))(inst, C.byref(dbg), None, C.byref(messenger)), 'messenger')
    out('MESSENGER_CREATED')
    enum = ifn(inst, b'vkEnumeratePhysicalDevices', I, VP, P(U32), P(VP))
    n = U32(1); devs = (VP * 1)(); ok(enum(inst, C.byref(n), devs), 'enumerate')
    if n.value != 1: out('STOP: device count %d' % n.value); hold('DEVICE_COUNT')
    pd = devs[0]
    buf = C.create_string_buffer(v.PROPS_BUF); ifn(inst, b'vkGetPhysicalDeviceProperties', None, VP, VP)(pd, buf)
    props = v.parse_props(buf.raw); out('PHYSICAL_DEVICE ' + json.dumps({k: props[k] for k in ('deviceName', 'vendorID', 'deviceID')}, sort_keys=True))
    if props['deviceName'] != 'Adreno (TM) 840': out('STOP: wrong device'); hold('WRONG_DEVICE')
    qn = U32(0); getq = ifn(inst, b'vkGetPhysicalDeviceQueueFamilyProperties', None, VP, P(U32), VP)
    getq(pd, C.byref(qn), None); qf = (v.VkQueueFamilyProperties * qn.value)(); getq(pd, C.byref(qn), qf)
    fam = next((i for i in range(qn.value) if qf[i].queueFlags & v.QUEUE_COMPUTE), None)
    if fam is None: out('STOP: no compute queue'); hold('NO_COMPUTE_QUEUE')
    out('QUEUE_FAMILY index=%d flags=0x%x count=%d' % (fam, qf[fam].queueFlags, qf[fam].queueCount))
    mp = v.VkPhysicalDeviceMemoryProperties(); ifn(inst, b'vkGetPhysicalDeviceMemoryProperties', None, VP, VP)(pd, C.byref(mp))
    prio = C.c_float(1.0)
    qci = v.VkDeviceQueueCreateInfo(v.ST['DEVICE_QUEUE'], None, 0, fam, 1, C.pointer(prio))
    dci = v.VkDeviceCreateInfo(v.ST['DEVICE'], None, 0, 1, C.pointer(qci), 0, None, 0, None, None)
    dev = VP(); ok(ifn(inst, b'vkCreateDevice', I, VP, P(v.VkDeviceCreateInfo), VP, P(VP))(pd, C.byref(dci), None, C.byref(dev)), 'vkCreateDevice')
    out('DEVICE_CREATED')
    gdpa = ifn(inst, b'vkGetDeviceProcAddr', VP, VP, C.c_char_p)
    def dfn(name, *sig):
        p = gdpa(dev, name)
        if not p: out('STOP: missing %s' % name.decode()); hold('ENTRYPOINT_MISSING')
        return C.CFUNCTYPE(*sig)(p)
    queue = VP(); dfn(b'vkGetDeviceQueue', None, VP, U32, U32, P(VP))(dev, fam, 0, C.byref(queue))
    # buffer + host-visible coherent memory
    bci = v.VkBufferCreateInfo(v.ST['BUFFER'], None, 0, SIZE, v.BUFFER_USAGE_STORAGE, 0, 0, None)
    b = v.H(); ok(dfn(b'vkCreateBuffer', I, VP, P(v.VkBufferCreateInfo), VP, PH)(dev, C.byref(bci), None, C.byref(b)), 'vkCreateBuffer')
    req = v.VkMemoryRequirements(); dfn(b'vkGetBufferMemoryRequirements', None, VP, v.H, P(v.VkMemoryRequirements))(dev, b, C.byref(req))
    want = v.MEM_HOST_VISIBLE | v.MEM_HOST_COHERENT
    mt = next((i for i in range(mp.memoryTypeCount) if (req.memoryTypeBits >> i) & 1 and (mp.memoryTypes[i].propertyFlags & want) == want), None)
    if mt is None: out('STOP: no host-visible coherent memory type'); hold('NO_MEMORY_TYPE')
    mai = v.VkMemoryAllocateInfo(v.ST['MEMORY_ALLOCATE'], None, req.size, mt)
    mem = v.H(); ok(dfn(b'vkAllocateMemory', I, VP, P(v.VkMemoryAllocateInfo), VP, PH)(dev, C.byref(mai), None, C.byref(mem)), 'vkAllocateMemory')
    ok(dfn(b'vkBindBufferMemory', I, VP, v.H, v.H, U64)(dev, b, mem, 0), 'vkBindBufferMemory')
    ptr = VP(); ok(dfn(b'vkMapMemory', I, VP, v.H, U64, U64, U32, P(VP))(dev, mem, 0, SIZE, 0, C.byref(ptr)), 'vkMapMemory')
    words = C.cast(ptr, P(U32))
    for i in range(SIZE // 4): words[i] = SENTINEL
    out('BUFFER_READY size=%d alloc=%d memtype=%d flags=0x%x' % (SIZE, req.size, mt, mp.memoryTypes[mt].propertyFlags))
    # pipeline
    code = C.create_string_buffer(spv, len(spv))
    smi = v.VkShaderModuleCreateInfo(v.ST['SHADER_MODULE'], None, 0, len(spv), C.cast(code, VP))
    sm = v.H(); ok(dfn(b'vkCreateShaderModule', I, VP, P(v.VkShaderModuleCreateInfo), VP, PH)(dev, C.byref(smi), None, C.byref(sm)), 'vkCreateShaderModule')
    binding = v.VkDescriptorSetLayoutBinding(0, v.DESCRIPTOR_STORAGE_BUFFER, 1, v.STAGE_COMPUTE_SHADER, None)
    dsli = v.VkDescriptorSetLayoutCreateInfo(v.ST['DESCRIPTOR_SET_LAYOUT'], None, 0, 1, C.pointer(binding))
    dsl = v.H(); ok(dfn(b'vkCreateDescriptorSetLayout', I, VP, P(v.VkDescriptorSetLayoutCreateInfo), VP, PH)(dev, C.byref(dsli), None, C.byref(dsl)), 'vkCreateDescriptorSetLayout')
    pli = v.VkPipelineLayoutCreateInfo(v.ST['PIPELINE_LAYOUT'], None, 0, 1, C.pointer(dsl), 0, None)
    pl = v.H(); ok(dfn(b'vkCreatePipelineLayout', I, VP, P(v.VkPipelineLayoutCreateInfo), VP, PH)(dev, C.byref(pli), None, C.byref(pl)), 'vkCreatePipelineLayout')
    stage = v.VkPipelineShaderStageCreateInfo(v.ST['PIPELINE_SHADER_STAGE'], None, 0, v.STAGE_COMPUTE_SHADER, sm, b'main', None)
    cpi = v.VkComputePipelineCreateInfo(v.ST['COMPUTE_PIPELINE'], None, 0, stage, pl, 0, -1)
    pipe = v.H(); ok(dfn(b'vkCreateComputePipelines', I, VP, v.H, U32, P(v.VkComputePipelineCreateInfo), VP, PH)(dev, 0, 1, C.byref(cpi), None, C.byref(pipe)), 'vkCreateComputePipelines')
    out('PIPELINE_CREATED')
    psz = v.VkDescriptorPoolSize(v.DESCRIPTOR_STORAGE_BUFFER, 1)
    dpi = v.VkDescriptorPoolCreateInfo(v.ST['DESCRIPTOR_POOL'], None, 0, 1, 1, C.pointer(psz))
    pool = v.H(); ok(dfn(b'vkCreateDescriptorPool', I, VP, P(v.VkDescriptorPoolCreateInfo), VP, PH)(dev, C.byref(dpi), None, C.byref(pool)), 'vkCreateDescriptorPool')
    dsai = v.VkDescriptorSetAllocateInfo(v.ST['DESCRIPTOR_SET_ALLOCATE'], None, pool, 1, C.pointer(dsl))
    ds = v.H(); ok(dfn(b'vkAllocateDescriptorSets', I, VP, P(v.VkDescriptorSetAllocateInfo), PH)(dev, C.byref(dsai), C.byref(ds)), 'vkAllocateDescriptorSets')
    dbi = v.VkDescriptorBufferInfo(b, 0, SIZE)
    wds = v.VkWriteDescriptorSet(v.ST['WRITE_DESCRIPTOR_SET'], None, ds, 0, 0, 1, v.DESCRIPTOR_STORAGE_BUFFER, None, C.pointer(dbi), None)
    dfn(b'vkUpdateDescriptorSets', None, VP, U32, P(v.VkWriteDescriptorSet), U32, VP)(dev, 1, C.byref(wds), 0, None)
    # command buffer
    cpci = v.VkCommandPoolCreateInfo(v.ST['COMMAND_POOL'], None, 0, fam)
    cpool = v.H(); ok(dfn(b'vkCreateCommandPool', I, VP, P(v.VkCommandPoolCreateInfo), VP, PH)(dev, C.byref(cpci), None, C.byref(cpool)), 'vkCreateCommandPool')
    cbai = v.VkCommandBufferAllocateInfo(v.ST['COMMAND_BUFFER_ALLOCATE'], None, cpool, 0, 1)
    cb = VP(); ok(dfn(b'vkAllocateCommandBuffers', I, VP, P(v.VkCommandBufferAllocateInfo), P(VP))(dev, C.byref(cbai), C.byref(cb)), 'vkAllocateCommandBuffers')
    bi = v.VkCommandBufferBeginInfo(v.ST['COMMAND_BUFFER_BEGIN'], None, 1, None)
    ok(dfn(b'vkBeginCommandBuffer', I, VP, P(v.VkCommandBufferBeginInfo))(cb, C.byref(bi)), 'vkBeginCommandBuffer')
    dfn(b'vkCmdBindPipeline', None, VP, U32, v.H)(cb, v.BIND_POINT_COMPUTE, pipe)
    dfn(b'vkCmdBindDescriptorSets', None, VP, U32, v.H, U32, U32, PH, U32, VP)(cb, v.BIND_POINT_COMPUTE, pl, 0, 1, C.byref(ds), 0, None)
    dfn(b'vkCmdDispatch', None, VP, U32, U32, U32)(cb, N // 64, 1, 1)
    mb = v.VkMemoryBarrier(v.ST['MEMORY_BARRIER'], None, v.ACCESS_SHADER_WRITE, v.ACCESS_HOST_READ)
    dfn(b'vkCmdPipelineBarrier', None, VP, U32, U32, U32, U32, P(v.VkMemoryBarrier), U32, VP, U32, VP)(cb, v.PIPE_COMPUTE, v.PIPE_HOST, 0, 1, C.byref(mb), 0, None, 0, None)
    ok(dfn(b'vkEndCommandBuffer', I, VP)(cb), 'vkEndCommandBuffer')
    fci = v.VkFenceCreateInfo(v.ST['FENCE'], None, 0)
    fence = v.H(); ok(dfn(b'vkCreateFence', I, VP, P(v.VkFenceCreateInfo), VP, PH)(dev, C.byref(fci), None, C.byref(fence)), 'vkCreateFence')
    cbs = (VP * 1)(cb)
    si = v.VkSubmitInfo(v.ST['SUBMIT'], None, 0, None, None, 1, cbs, 0, None)
    out('COMMANDS_RECORDED dispatch=%dx1x1 local=64' % (N // 64))
    t0 = time.monotonic()
    ok(dfn(b'vkQueueSubmit', I, VP, U32, P(v.VkSubmitInfo), v.H)(queue, 1, C.byref(si), fence), 'vkQueueSubmit')
    out('SUBMITTED')
    r = dfn(b'vkWaitForFences', I, VP, U32, PH, U32, U64)(dev, 1, C.byref(fence), 1, 5_000_000_000)
    ms = (time.monotonic() - t0) * 1e3
    if r != v.VK_SUCCESS: out('STOP: vkWaitForFences result=%d ms=%.1f' % (r, ms)); hold('FENCE_NOT_SIGNALED')
    out('FENCE_SIGNALED ms=%.2f' % ms)
    bad = [i for i in range(N) if words[i] != (3 * i + 7) & 0xffffffff]
    untouched = sum(1 for i in range(N, SIZE // 4) if words[i] == SENTINEL)
    out('VERIFY written_ok=%d/%d untouched=%d/%d first_bad=%s sample=%s' % (N - len(bad), N, untouched, SIZE // 4 - N,
        bad[:1] and hex(words[bad[0]]), [words[i] for i in (0, 1, 2, 255)]))
    if bad or untouched != SIZE // 4 - N: hold('VERIFY_FAILED')
    out('VERIFY_PASS')
    gate('READY_FOR_DESTROY')
    dfn(b'vkDestroyFence', None, VP, v.H, VP)(dev, fence, None)
    dfn(b'vkDestroyCommandPool', None, VP, v.H, VP)(dev, cpool, None)
    dfn(b'vkDestroyDescriptorPool', None, VP, v.H, VP)(dev, pool, None)
    dfn(b'vkDestroyPipeline', None, VP, v.H, VP)(dev, pipe, None)
    dfn(b'vkDestroyPipelineLayout', None, VP, v.H, VP)(dev, pl, None)
    dfn(b'vkDestroyDescriptorSetLayout', None, VP, v.H, VP)(dev, dsl, None)
    dfn(b'vkDestroyShaderModule', None, VP, v.H, VP)(dev, sm, None)
    dfn(b'vkUnmapMemory', None, VP, v.H)(dev, mem)
    dfn(b'vkDestroyBuffer', None, VP, v.H, VP)(dev, b, None)
    dfn(b'vkFreeMemory', None, VP, v.H, VP)(dev, mem, None)
    ok(dfn(b'vkDeviceWaitIdle', I, VP)(dev), 'vkDeviceWaitIdle')
    dfn(b'vkDestroyDevice', None, VP, VP)(dev, None); out('DEVICE_DESTROYED')
    ifn(inst, b'vkDestroyDebugUtilsMessengerEXT', None, VP, U64, VP)(inst, messenger, None)
    ifn(inst, b'vkDestroyInstance', None, VP, VP)(inst, None); out('INSTANCE_DESTROYED')
    hold('COMPUTE_DONE_DESTROYED')

main()
