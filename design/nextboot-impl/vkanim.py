#!/usr/bin/env python3
"""Finite GPU animation on the panel. Gate 1: turnip(KGSL) device + push-constant compute pipeline (anim.spv), one host-visible
render buffer, two NEW cached dumb buffers A/B + FBs on the inherited DRM file, TEST_ONLY for A, B and the owner's native FB. Gate 2: for k=1..N:
GPU renders frame k (fence), CPU checks ~1.2k pixels, copies into the back buffer (never the one on screen), one blocking atomic
FB_ID switch of planes 95+128 with PAGE_FLIP_EVENT (consumed before the next frame). Finally returns to the owner's native FB (registry),
destroys Vulkan (its kgsl fd closes; the gpu-keeper keeps its own) and holds all DRM resources. Any error: HOLD, no retry."""
import ctypes as C, json, mmap, os, signal, stat, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vkabi as v, drmabi as d, animpattern as ap

HERE = os.path.dirname(os.path.abspath(__file__))
ICD = '/home/siwal/y700-gpu/turnip-kgsl-26.2.3/lib/libvulkan_freedreno.so'
W, H, PITCH, STRIDE = 1904, 3040, 7680, 1920
SIZE = PITCH * H
BASE_FB = int(os.environ.get('ANIM_BASE_FB', '0'))       # the display-owner's native FB for this boot (registry)
START_FB = BASE_FB
FRAMES = int(os.environ.get('ANIM_FRAMES', '0'))
def out(*a): print(*a, flush=True)
def hold(reason):
    out('HELD pid=%d reason=%s' % (os.getpid(), reason))
    while True: time.sleep(1)
def gate(tag):
    out(tag)
    if sys.stdin.readline() != 'GO\n': hold('GATE_NOT_RELEASED')
def ok(r, what):
    if r != v.VK_SUCCESS: out('STOP: %s result=%d' % (what, r)); hold('VK_ERROR')
def geometry(p, fb):
    for name, sx, dx in (('left', 0, 0), ('right', 952, 952)):
        q = p[name]
        want = {'FB_ID': fb, 'CRTC_ID': 205, 'SRC_X': sx << 16, 'SRC_Y': 0, 'SRC_W': 952 << 16, 'SRC_H': H << 16,
                'CRTC_X': dx, 'CRTC_Y': 0, 'CRTC_W': 952, 'CRTC_H': H}
        bad = {k: q[k][1] for k in want if q[k][1] != want[k]}
        if bad: out('STOP: %s plane geometry %s' % (name, bad)); hold('GEOMETRY_CHANGED')
def planes(fd): return {'left': d.properties(fd, 95, d.OBJECT_PLANE), 'right': d.properties(fd, 128, d.OBJECT_PLANE)}

def main():
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    if len(sys.argv) != 3 or sys.argv[1] != '--drm-fd' or FRAMES not in (120, 600): out('STOP: usage/frames'); hold('USAGE')
    fd = int(sys.argv[2]); st = os.fstat(fd)
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(226, 0)): out('STOP: not card0'); hold('WRONG_FD')
    p = planes(fd); crtc = d.properties(fd, 205, d.OBJECT_CRTC)
    if not (crtc['ACTIVE'][1] == 1 and crtc['MODE_ID'][1] == int(os.environ.get('ANIM_MODE_BLOB', '0'))): out('STOP: display state'); hold('STATE_CHANGED')
    if BASE_FB <= 0: out('STOP: base fb'); hold('USAGE')
    geometry(p, START_FB)
    existing = d.fb_ids(fd)
    if BASE_FB not in existing: out('STOP: base fb not registered'); hold('BASE_FB_MISSING')
    fbprop = {95: p['left']['FB_ID'][0], 128: p['right']['FB_ID'][0]}
    out('INHERITED_DRM_FILE planes 95/128 on FB%d, mode active; frames=%d' % (START_FB, FRAMES))
    out('EXISTING_FBS ' + json.dumps(existing))
    spv = open(os.path.join(HERE, 'anim.spv'), 'rb').read()
    gate('READY_FOR_SETUP')
    # ---- Vulkan setup (same call sequence as the verified render worker, plus a push-constant range) ----
    lib = C.CDLL(ICD, mode=os.RTLD_NOW | os.RTLD_LOCAL)
    ver = C.c_uint32(5); neg = lib.vk_icdNegotiateLoaderICDInterfaceVersion; neg.restype = C.c_int32
    neg.argtypes = [C.POINTER(C.c_uint32)]; ok(neg(C.byref(ver)), 'negotiate')
    gipa = lib.vk_icdGetInstanceProcAddr; gipa.restype = C.c_void_p; gipa.argtypes = [C.c_void_p, C.c_char_p]
    def ifn(inst, name, *sig):
        q = gipa(inst, name)
        if not q: out('STOP: missing %s' % name.decode()); hold('ENTRYPOINT_MISSING')
        return C.CFUNCTYPE(*sig)(q)
    I, P, VP, U32, U64, PH = C.c_int32, C.POINTER, C.c_void_p, C.c_uint32, C.c_uint64, C.POINTER(v.H)
    @v.DebugCallback
    def on_message(sev, types, data, user):
        msg = data.contents.pMessage.decode(errors='replace') if data and data.contents.pMessage else ''
        out('VKMSG sev=0x%x type=0x%x %s' % (sev, types, msg.replace('\n', ' | '))); return 0
    global _keep; _keep = on_message
    dbg = v.VkDebugUtilsMessengerCreateInfoEXT(v.VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT, None, 0, v.SEV_ALL, v.TYPE_ALL, on_message, None)
    app = v.VkApplicationInfo(0, None, b'y700-vkanim', 1, b'none', 0, v.API_1_3)
    exts = (C.c_char_p * 1)(b'VK_EXT_debug_utils')
    ci = v.VkInstanceCreateInfo(1, C.cast(C.pointer(dbg), VP), 0, C.pointer(app), 0, None, 1, C.cast(exts, VP))
    inst = VP(); ok(ifn(None, b'vkCreateInstance', I, P(v.VkInstanceCreateInfo), VP, P(VP))(C.byref(ci), None, C.byref(inst)), 'vkCreateInstance')
    messenger = U64(); ok(ifn(inst, b'vkCreateDebugUtilsMessengerEXT', I, VP, P(v.VkDebugUtilsMessengerCreateInfoEXT), VP, P(U64))(inst, C.byref(dbg), None, C.byref(messenger)), 'messenger')
    n = U32(1); devs = (VP * 1)(); ok(ifn(inst, b'vkEnumeratePhysicalDevices', I, VP, P(U32), P(VP))(inst, C.byref(n), devs), 'enumerate')
    pd = devs[0]; props = C.create_string_buffer(v.PROPS_BUF); ifn(inst, b'vkGetPhysicalDeviceProperties', None, VP, VP)(pd, props)
    if n.value != 1 or v.parse_props(props.raw)['deviceName'] != 'Adreno (TM) 840': out('STOP: device'); hold('WRONG_DEVICE')
    qn = U32(0); getq = ifn(inst, b'vkGetPhysicalDeviceQueueFamilyProperties', None, VP, P(U32), VP)
    getq(pd, C.byref(qn), None); qf = (v.VkQueueFamilyProperties * qn.value)(); getq(pd, C.byref(qn), qf)
    fam = next((i for i in range(qn.value) if qf[i].queueFlags & v.QUEUE_COMPUTE), None)
    if fam is None: out('STOP: no compute queue'); hold('NO_COMPUTE_QUEUE')
    mp = v.VkPhysicalDeviceMemoryProperties(); ifn(inst, b'vkGetPhysicalDeviceMemoryProperties', None, VP, VP)(pd, C.byref(mp))
    prio = C.c_float(1.0); qci = v.VkDeviceQueueCreateInfo(v.ST['DEVICE_QUEUE'], None, 0, fam, 1, C.pointer(prio))
    dci = v.VkDeviceCreateInfo(v.ST['DEVICE'], None, 0, 1, C.pointer(qci), 0, None, 0, None, None)
    dev = VP(); ok(ifn(inst, b'vkCreateDevice', I, VP, P(v.VkDeviceCreateInfo), VP, P(VP))(pd, C.byref(dci), None, C.byref(dev)), 'vkCreateDevice')
    gdpa = ifn(inst, b'vkGetDeviceProcAddr', VP, VP, C.c_char_p)
    def dfn(name, *sig):
        q = gdpa(dev, name)
        if not q: out('STOP: missing %s' % name.decode()); hold('ENTRYPOINT_MISSING')
        return C.CFUNCTYPE(*sig)(q)
    queue = VP(); dfn(b'vkGetDeviceQueue', None, VP, U32, U32, P(VP))(dev, fam, 0, C.byref(queue))
    bci = v.VkBufferCreateInfo(v.ST['BUFFER'], None, 0, SIZE, v.BUFFER_USAGE_STORAGE, 0, 0, None)
    b = v.H(); ok(dfn(b'vkCreateBuffer', I, VP, P(v.VkBufferCreateInfo), VP, PH)(dev, C.byref(bci), None, C.byref(b)), 'vkCreateBuffer')
    req = v.VkMemoryRequirements(); dfn(b'vkGetBufferMemoryRequirements', None, VP, v.H, P(v.VkMemoryRequirements))(dev, b, C.byref(req))
    want = v.MEM_HOST_VISIBLE | v.MEM_HOST_COHERENT
    mt = next((i for i in range(mp.memoryTypeCount) if (req.memoryTypeBits >> i) & 1 and (mp.memoryTypes[i].propertyFlags & want) == want), None)
    if mt is None: out('STOP: no host-visible coherent memory'); hold('NO_MEMORY_TYPE')
    mem = v.H(); ok(dfn(b'vkAllocateMemory', I, VP, P(v.VkMemoryAllocateInfo), VP, PH)(dev, C.byref(v.VkMemoryAllocateInfo(v.ST['MEMORY_ALLOCATE'], None, req.size, mt)), None, C.byref(mem)), 'vkAllocateMemory')
    ok(dfn(b'vkBindBufferMemory', I, VP, v.H, v.H, U64)(dev, b, mem, 0), 'vkBindBufferMemory')
    ptr = VP(); ok(dfn(b'vkMapMemory', I, VP, v.H, U64, U64, U32, P(VP))(dev, mem, 0, SIZE, 0, C.byref(ptr)), 'vkMapMemory')
    words = C.cast(ptr, P(U32))
    code = C.create_string_buffer(spv, len(spv))
    sm = v.H(); ok(dfn(b'vkCreateShaderModule', I, VP, P(v.VkShaderModuleCreateInfo), VP, PH)(dev, C.byref(v.VkShaderModuleCreateInfo(v.ST['SHADER_MODULE'], None, 0, len(spv), C.cast(code, VP))), None, C.byref(sm)), 'vkCreateShaderModule')
    binding = v.VkDescriptorSetLayoutBinding(0, v.DESCRIPTOR_STORAGE_BUFFER, 1, v.STAGE_COMPUTE_SHADER, None)
    dsl = v.H(); ok(dfn(b'vkCreateDescriptorSetLayout', I, VP, P(v.VkDescriptorSetLayoutCreateInfo), VP, PH)(dev, C.byref(v.VkDescriptorSetLayoutCreateInfo(v.ST['DESCRIPTOR_SET_LAYOUT'], None, 0, 1, C.pointer(binding))), None, C.byref(dsl)), 'vkCreateDescriptorSetLayout')
    pcr = v.VkPushConstantRange(v.STAGE_COMPUTE_SHADER, 0, 4)
    pli = v.VkPipelineLayoutCreateInfo(v.ST['PIPELINE_LAYOUT'], None, 0, 1, C.pointer(dsl), 1, C.cast(C.pointer(pcr), VP))
    pl = v.H(); ok(dfn(b'vkCreatePipelineLayout', I, VP, P(v.VkPipelineLayoutCreateInfo), VP, PH)(dev, C.byref(pli), None, C.byref(pl)), 'vkCreatePipelineLayout')
    stage = v.VkPipelineShaderStageCreateInfo(v.ST['PIPELINE_SHADER_STAGE'], None, 0, v.STAGE_COMPUTE_SHADER, sm, b'main', None)
    pipe = v.H(); ok(dfn(b'vkCreateComputePipelines', I, VP, v.H, U32, P(v.VkComputePipelineCreateInfo), VP, PH)(dev, 0, 1, C.byref(v.VkComputePipelineCreateInfo(v.ST['COMPUTE_PIPELINE'], None, 0, stage, pl, 0, -1)), None, C.byref(pipe)), 'vkCreateComputePipelines')
    psz = v.VkDescriptorPoolSize(v.DESCRIPTOR_STORAGE_BUFFER, 1)
    pool = v.H(); ok(dfn(b'vkCreateDescriptorPool', I, VP, P(v.VkDescriptorPoolCreateInfo), VP, PH)(dev, C.byref(v.VkDescriptorPoolCreateInfo(v.ST['DESCRIPTOR_POOL'], None, 0, 1, 1, C.pointer(psz))), None, C.byref(pool)), 'vkCreateDescriptorPool')
    ds = v.H(); ok(dfn(b'vkAllocateDescriptorSets', I, VP, P(v.VkDescriptorSetAllocateInfo), PH)(dev, C.byref(v.VkDescriptorSetAllocateInfo(v.ST['DESCRIPTOR_SET_ALLOCATE'], None, pool, 1, C.pointer(dsl))), C.byref(ds)), 'vkAllocateDescriptorSets')
    dbi = v.VkDescriptorBufferInfo(b, 0, SIZE)
    dfn(b'vkUpdateDescriptorSets', None, VP, U32, P(v.VkWriteDescriptorSet), U32, VP)(dev, 1, C.byref(v.VkWriteDescriptorSet(v.ST['WRITE_DESCRIPTOR_SET'], None, ds, 0, 0, 1, v.DESCRIPTOR_STORAGE_BUFFER, None, C.pointer(dbi), None)), 0, None)
    cpool = v.H(); ok(dfn(b'vkCreateCommandPool', I, VP, P(v.VkCommandPoolCreateInfo), VP, PH)(dev, C.byref(v.VkCommandPoolCreateInfo(v.ST['COMMAND_POOL'], None, v.COMMAND_POOL_RESET_COMMAND_BUFFER, fam)), None, C.byref(cpool)), 'vkCreateCommandPool')
    cb = VP(); ok(dfn(b'vkAllocateCommandBuffers', I, VP, P(v.VkCommandBufferAllocateInfo), P(VP))(dev, C.byref(v.VkCommandBufferAllocateInfo(v.ST['COMMAND_BUFFER_ALLOCATE'], None, cpool, 0, 1)), C.byref(cb)), 'vkAllocateCommandBuffers')
    fence = v.H(); ok(dfn(b'vkCreateFence', I, VP, P(v.VkFenceCreateInfo), VP, PH)(dev, C.byref(v.VkFenceCreateInfo(v.ST['FENCE'], None, 0)), None, C.byref(fence)), 'vkCreateFence')
    begin = dfn(b'vkBeginCommandBuffer', I, VP, P(v.VkCommandBufferBeginInfo)); end = dfn(b'vkEndCommandBuffer', I, VP)
    bindp = dfn(b'vkCmdBindPipeline', None, VP, U32, v.H); bindd = dfn(b'vkCmdBindDescriptorSets', None, VP, U32, v.H, U32, U32, PH, U32, VP)
    push = dfn(b'vkCmdPushConstants', None, VP, v.H, U32, U32, U32, VP); dispatch = dfn(b'vkCmdDispatch', None, VP, U32, U32, U32)
    barrier = dfn(b'vkCmdPipelineBarrier', None, VP, U32, U32, U32, U32, P(v.VkMemoryBarrier), U32, VP, U32, VP)
    submit = dfn(b'vkQueueSubmit', I, VP, U32, P(v.VkSubmitInfo), v.H); waitf = dfn(b'vkWaitForFences', I, VP, U32, PH, U32, U64)
    resetf = dfn(b'vkResetFences', I, VP, U32, PH)
    mb = v.VkMemoryBarrier(v.ST['MEMORY_BARRIER'], None, v.ACCESS_SHADER_WRITE, v.ACCESS_HOST_READ)
    cbs = (VP * 1)(cb); si = v.VkSubmitInfo(v.ST['SUBMIT'], None, 0, None, None, 1, cbs, 0, None)
    bi = v.VkCommandBufferBeginInfo(v.ST['COMMAND_BUFFER_BEGIN'], None, 1, None)
    out('VULKAN_READY memtype=%d alloc=%d' % (mt, req.size))
    # ---- DRM: two new cached dumb buffers + framebuffers ----
    bufs = []
    for label in ('A', 'B'):
        _, _, _, _, handle, pitch, size = d.ioctl(fd, 'CREATE_DUMB', d.CREATE_DUMB, H, W, 32, 0, 0, 0, 0)
        if (pitch, size) != (PITCH, SIZE): out('STOP: dumb geometry pitch=%d size=%d' % (pitch, size)); hold('DUMB_GEOMETRY')
        _, off = d.ioctl(fd, 'MAP_DUMB', d.MAP_DUMB, handle, 0)
        m = mmap.mmap(fd, SIZE, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=off)
        fb = d.ioctl(fd, 'ADDFB2', d.FB_CMD2, 0, W, H, d.XRGB8888, 0, handle, 0, 0, 0, PITCH, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)[0]
        g = d.ioctl(fd, 'GETFB2', d.FB_CMD2, fb, *([0] * 20))
        if not (g[1:4] == (W, H, d.XRGB8888) and g[9] == PITCH and g[13] == 0 and g[17] == 0 and fb not in existing and fb != 0):
            out('STOP: framebuffer readback %s' % (g,)); hold('FB_MISMATCH')
        bufs.append({'label': label, 'fb': fb, 'map': m, 'addr': C.addressof(C.c_char.from_buffer(m))})
        out('BUFFER %s fb=%d handle=%d' % (label, fb, handle))
    def switch(fb, flags, user=0):
        d.atomic(fd, flags, [95, 128], {95: [(fbprop[95], fb)], 128: [(fbprop[128], fb)]}, user)
        if flags & d.ATOMIC_PAGE_FLIP_EVENT: d.wait_flip(fd, user)
    try:
        for fbid in (bufs[0]['fb'], bufs[1]['fb'], BASE_FB): switch(fbid, d.ATOMIC_TEST_ONLY)
    except OSError as e: out('STOP: TEST_ONLY errno=%d' % e.errno); hold('TEST_ONLY_FAILED')
    out('TEST_ONLY_PASS A B BASE')
    global _hold_refs; _hold_refs = bufs
    gate('READY_FOR_ANIMATION')
    out('ANIMATION_ENTER')
    frame_no = U32(0); t_start = time.monotonic(); stats = []
    for k in range(1, FRAMES + 1):
        t0 = time.monotonic()
        frame_no.value = k
        ok(begin(cb, C.byref(bi)), 'begin'); bindp(cb, v.BIND_POINT_COMPUTE, pipe)
        bindd(cb, v.BIND_POINT_COMPUTE, pl, 0, 1, C.byref(ds), 0, None)
        push(cb, pl, v.STAGE_COMPUTE_SHADER, 0, 4, C.byref(frame_no)); dispatch(cb, STRIDE // 16, H // 16, 1)
        barrier(cb, v.PIPE_COMPUTE, v.PIPE_HOST, 0, 1, C.byref(mb), 0, None, 0, None); ok(end(cb), 'end')
        ok(resetf(dev, 1, C.byref(fence)), 'reset fence'); ok(submit(queue, 1, C.byref(si), fence), 'submit')
        r = waitf(dev, 1, C.byref(fence), 1, 1_000_000_000)
        if r != v.VK_SUCCESS: out('STOP: fence frame=%d result=%d' % (k, r)); hold('FENCE_NOT_SIGNALED')
        t1 = time.monotonic()
        bad = [(x, y) for x, y in ap.points(k) if words[y * STRIDE + x] != ap.pixel(x, y, k)]
        if bad: out('STOP: frame %d pixel mismatch %s' % (k, bad[:2])); hold('VERIFY_FAILED')
        back = bufs[k % 2]            # k odd -> B, k even -> A; the other one (or START_FB at k=1) is on screen
        C.memmove(back['addr'], ptr, SIZE)
        if k in (1, FRAMES) and C.string_at(back['addr'], SIZE) != C.string_at(ptr, SIZE): out('STOP: copy mismatch frame %d' % k); hold('COPY_MISMATCH')
        t2 = time.monotonic()
        try: switch(back['fb'], d.ATOMIC_PAGE_FLIP_EVENT, k)
        except (OSError, TimeoutError, RuntimeError) as e: out('STOP: flip frame=%d %r' % (k, e)); hold('FLIP_ERROR_STATE_UNCERTAIN')
        t3 = time.monotonic()
        stats.append((k, back['fb'], (t1 - t0) * 1e3, (t2 - t1) * 1e3, (t3 - t2) * 1e3, (t3 - t_start) * 1e3))
        out('F %d fb=%d render_ms=%.2f copy_ms=%.2f flip_ms=%.2f t_ms=%.1f' % stats[-1])
    total = (time.monotonic() - t_start)
    out('ANIMATION_SUMMARY frames=%d seconds=%.3f fps=%.2f max_frame_ms=%.2f' % (FRAMES, total, FRAMES / total,
        max(s[2] + s[3] + s[4] for s in stats)))
    try: switch(BASE_FB, d.ATOMIC_PAGE_FLIP_EVENT, FRAMES + 1)
    except (OSError, TimeoutError, RuntimeError) as e: out('STOP: return flip %r' % (e,)); hold('RETURN_ERROR_STATE_UNCERTAIN')
    geometry(planes(fd), BASE_FB)
    out('RETURNED_BASE')
    for name, sig, *args in ((b'vkDestroyFence', fence), (b'vkDestroyCommandPool', cpool), (b'vkDestroyDescriptorPool', pool),
                             (b'vkDestroyPipeline', pipe), (b'vkDestroyPipelineLayout', pl), (b'vkDestroyDescriptorSetLayout', dsl),
                             (b'vkDestroyShaderModule', sm)):
        dfn(name, None, VP, v.H, VP)(dev, sig, None)
    dfn(b'vkUnmapMemory', None, VP, v.H)(dev, mem); dfn(b'vkDestroyBuffer', None, VP, v.H, VP)(dev, b, None)
    dfn(b'vkFreeMemory', None, VP, v.H, VP)(dev, mem, None); ok(dfn(b'vkDeviceWaitIdle', I, VP)(dev), 'idle')
    dfn(b'vkDestroyDevice', None, VP, VP)(dev, None)
    ifn(inst, b'vkDestroyDebugUtilsMessengerEXT', None, VP, U64, VP)(inst, messenger, None)
    ifn(inst, b'vkDestroyInstance', None, VP, VP)(inst, None); out('VK_DESTROYED')
    hold('ANIMATION_DONE_BASE_RETAINED')

try:
    main()
except BaseException as exc:   # never exit: exit would close the borrowed DRM fd and the kgsl fd
    out('STOP: exception %r' % (exc,)); hold('EXCEPTION')
