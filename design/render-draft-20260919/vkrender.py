#!/usr/bin/env python3
"""GPU-rendered frame to the panel. Gate 1: turnip(KGSL) compute renders 1904x3040 XRGB8888 (render.spv) into a
host-visible buffer; CPU checks ~70k pixels against the formula; the frame is copied into a NEW cached DRM dumb buffer on the
inherited (borrowed) DRM file, byte-compared, Vulkan objects/instance destroyed, ADDFB2 + TEST_ONLY. Gate 2: one blocking
atomic commit switches FB_ID of planes 95+128 (geometry unchanged, 1:1 halves). Everything DRM-side is then held; nothing is
closed, restored or retried."""
import ctypes as C, json, mmap, os, signal, stat, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vkabi as v
import drmabi as d
import pattern

HERE = os.path.dirname(os.path.abspath(__file__))
ICD = '/home/siwal/y700-gpu/turnip-kgsl-26.2.3/lib/libvulkan_freedreno.so'
W, H, PITCH, STRIDE = 1904, 3040, 7680, 1920
SIZE = PITCH * H   # 23347200: dumb/scanout layout, rows padded to 1920 px
CONFIRMED_FB = 335
SENTINEL = 0xDEADBEEF
def out(*a): print(*a, flush=True)
def hold(reason):
    out('HELD pid=%d reason=%s' % (os.getpid(), reason))
    while True: time.sleep(1)
def gate(tag):
    out(tag)
    if sys.stdin.readline() != 'GO\n': hold('GATE_NOT_RELEASED')
def ok(r, what):
    if r != v.VK_SUCCESS: out('STOP: %s result=%d' % (what, r)); hold('VK_ERROR')

def geometry(p, fb, sx, dx):
    want = {'FB_ID': fb, 'CRTC_ID': 205, 'SRC_X': sx << 16, 'SRC_Y': 0, 'SRC_W': 952 << 16, 'SRC_H': H << 16,
            'CRTC_X': dx, 'CRTC_Y': 0, 'CRTC_W': 952, 'CRTC_H': H}
    bad = {k: p[k][1] for k in want if p[k][1] != want[k]}
    if bad: out('STOP: plane geometry differs %s' % bad); hold('GEOMETRY_CHANGED')

def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    if len(sys.argv) != 3 or sys.argv[1] != '--drm-fd': out('STOP: usage'); hold('USAGE')
    fd = int(sys.argv[2]); st = os.fstat(fd)
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(226, 0)): out('STOP: not card0'); hold('WRONG_FD')
    left, right = d.properties(fd, 95, d.OBJECT_PLANE), d.properties(fd, 128, d.OBJECT_PLANE)
    crtc, conn = d.properties(fd, 205, d.OBJECT_CRTC), d.properties(fd, 69, d.OBJECT_CONNECTOR)
    if not (conn['CRTC_ID'][1] == 205 and crtc['ACTIVE'][1] == 1 and crtc['MODE_ID'][1] == 340): out('STOP: display state'); hold('STATE_CHANGED')
    geometry(left, CONFIRMED_FB, 0, 0); geometry(right, CONFIRMED_FB, 952, 952)
    out('INHERITED_DRM_FILE planes 95/128 on FB335, mode 340 active; no new DRM open')
    spv = open(os.path.join(HERE, 'render.spv'), 'rb').read()
    gate('READY_FOR_RENDER')
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
    app = v.VkApplicationInfo(0, None, b'y700-vkrender', 1, b'none', 0, v.API_1_3)
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
    C.memset(ptr, 0xEF, SIZE)   # sentinel bytes; every pixel must be overwritten by the GPU
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
    dfn(b'vkCmdDispatch', None, VP, U32, U32, U32)(cb, STRIDE // 16, H // 16, 1)
    mb = v.VkMemoryBarrier(v.ST['MEMORY_BARRIER'], None, v.ACCESS_SHADER_WRITE, v.ACCESS_HOST_READ)
    dfn(b'vkCmdPipelineBarrier', None, VP, U32, U32, U32, U32, P(v.VkMemoryBarrier), U32, VP, U32, VP)(cb, v.PIPE_COMPUTE, v.PIPE_HOST, 0, 1, C.byref(mb), 0, None, 0, None)
    ok(dfn(b'vkEndCommandBuffer', I, VP)(cb), 'vkEndCommandBuffer')
    fci = v.VkFenceCreateInfo(v.ST['FENCE'], None, 0)
    fence = v.H(); ok(dfn(b'vkCreateFence', I, VP, P(v.VkFenceCreateInfo), VP, PH)(dev, C.byref(fci), None, C.byref(fence)), 'vkCreateFence')
    cbs = (VP * 1)(cb)
    si = v.VkSubmitInfo(v.ST['SUBMIT'], None, 0, None, None, 1, cbs, 0, None)
    out('COMMANDS_RECORDED dispatch=%dx%dx1 local=16x16' % (STRIDE // 16, H // 16))
    t0 = time.monotonic()
    ok(dfn(b'vkQueueSubmit', I, VP, U32, P(v.VkSubmitInfo), v.H)(queue, 1, C.byref(si), fence), 'vkQueueSubmit')
    out('SUBMITTED')
    r = dfn(b'vkWaitForFences', I, VP, U32, PH, U32, U64)(dev, 1, C.byref(fence), 1, 5_000_000_000)
    ms = (time.monotonic() - t0) * 1e3
    if r != v.VK_SUCCESS: out('STOP: vkWaitForFences result=%d ms=%.1f' % (r, ms)); hold('FENCE_NOT_SIGNALED')
    out('FENCE_SIGNALED ms=%.2f' % ms)
    words = C.cast(ptr, P(U32))
    pts = pattern.sample_points()
    bad = [(x, y) for x, y in pts if words[y * STRIDE + x] != pattern.pixel(x, y)]
    bad += [(x, y) for y in range(0, H, 7) for x in range(W, STRIDE) if words[y * STRIDE + x] != 0]
    frame = C.string_at(ptr, SIZE)
    sentinel_left = frame.count(b'\xef\xef\xef\xef')   # upper bound on untouched words (pattern never has 0xef bytes in all 4)
    out('VERIFY sampled=%d mismatches=%d sentinel_words=%d first_bad=%s' % (len(pts), len(bad), sentinel_left, bad[:1]))
    if bad or sentinel_left: hold('VERIFY_FAILED')
    out('VERIFY_PASS')
    # DRM: new cached dumb buffer, CPU copy, byte compare (same path as the confirmed FB335 buffer)
    height, width, bpp, flags, handle, pitch, size = d.ioctl(fd, 'CREATE_DUMB', d.CREATE_DUMB, H, W, 32, 0, 0, 0, 0)
    if (pitch, size) != (PITCH, SIZE): out('STOP: dumb geometry pitch=%d size=%d' % (pitch, size)); hold('DUMB_GEOMETRY')
    _, offset = d.ioctl(fd, 'MAP_DUMB', d.MAP_DUMB, handle, 0)
    m = mmap.mmap(fd, SIZE, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=offset)
    dst = C.addressof(C.c_char.from_buffer(m))
    C.memmove(dst, ptr, SIZE)
    same = C.string_at(dst, SIZE) == frame
    out('DUMB_COPIED handle=%d offset=%d identical=%s' % (handle, offset, same))
    if not same: hold('COPY_MISMATCH')
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
    dfn(b'vkDestroyDevice', None, VP, VP)(dev, None)
    ifn(inst, b'vkDestroyDebugUtilsMessengerEXT', None, VP, U64, VP)(inst, messenger, None)
    ifn(inst, b'vkDestroyInstance', None, VP, VP)(inst, None); out('VK_DESTROYED')
    fb = d.ioctl(fd, 'ADDFB2', d.FB_CMD2, 0, W, H, d.XRGB8888, 0, handle, 0, 0, 0, PITCH, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)[0]
    g = d.ioctl(fd, 'GETFB2', d.FB_CMD2, fb, *([0] * 20))
    if not (g[1:4] == (W, H, d.XRGB8888) and g[9] == PITCH and g[13] == 0 and g[17] == 0 and fb not in (0, 78, 335, 341, 342)):
        out('STOP: framebuffer readback %s' % (g,)); hold('FB_MISMATCH')
    out('FRAMEBUFFER id=%d %dx%d pitch=%d XR24 linear' % (fb, W, H, PITCH))
    props = {95: [(left['FB_ID'][0], fb)], 128: [(right['FB_ID'][0], fb)]}
    try: d.atomic(fd, d.ATOMIC_TEST_ONLY, [95, 128], props)
    except OSError as e: out('STOP: TEST_ONLY errno=%d' % e.errno); hold('TEST_ONLY_FAILED')
    out('TEST_ONLY_PASS')
    gate('READY_FOR_COMMIT')
    out('COMMIT_ENTER')
    try: d.atomic(fd, 0, [95, 128], props)
    except OSError as e: out('STOP: commit errno=%d' % e.errno); hold('COMMIT_ERROR_STATE_UNCERTAIN')
    left, right = d.properties(fd, 95, d.OBJECT_PLANE), d.properties(fd, 128, d.OBJECT_PLANE)
    geometry(left, fb, 0, 0); geometry(right, fb, 952, 952)
    crtc = d.properties(fd, 205, d.OBJECT_CRTC)
    if not (crtc['MODE_ID'][1] == 340 and crtc['ACTIVE'][1] == 1): out('STOP: mode changed'); hold('MODE_CHANGED')
    out('COMMIT_RETURNED fb=%d' % fb)
    global _hold_refs; _hold_refs = (m, fd)
    hold('GPU_FRAME_DISPLAYED_VISUAL_CONFIRMATION_REQUIRED')

try:
    main()
except BaseException as exc:   # never exit: exit would close the borrowed DRM fd and the kgsl fd
    out('STOP: exception %r' % (exc,)); hold('EXCEPTION')
