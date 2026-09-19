"""audio-d2: one 64 KiB dma-buf -> mmap pattern check -> msm_audio_ion MAP -> APM shared-mem MAP (handle) -> UNMAP -> ion UNMAP -> close.
No graph is opened; nothing reaches a speaker."""
import json, mmap, os, struct
import gprclient as g, audioshm as s

SIZE = 64 * 1024
def run(report):
    r = report['shm'] = {'nodes': {'pkt': g.node(), 'ion': s.chrnode('/dev/msm_audio_ion', '/sys/class/msm_audio_ion/msm_audio_ion/dev'),
                                   'heap': s.chrnode('/dev/dma_heap/system', '/sys/class/dma_heap/system/dev')}, 'steps': []}
    step = lambda **k: (r['steps'].append(k), print('STEP', json.dumps(k), flush=True))
    buf = ion = cl = None; ion_mapped = handle = None
    try:
        buf = s.alloc(SIZE); step(alloc_fd=buf, size=SIZE)
        mm = mmap.mmap(buf, SIZE); mm[:8] = b'Y700SHM1'; mm[SIZE - 8:] = b'Y700SHM2'; ok = mm[:8] == b'Y700SHM1' and mm[SIZE - 8:] == b'Y700SHM2'; mm.close()
        step(mmap_pattern=ok)
        ion = os.open('/dev/msm_audio_ion', os.O_RDWR | os.O_CLOEXEC)
        step(ion_map_ret=__import__('fcntl').ioctl(ion, s.IOCTL_MAP_PHYS_ADDR, buf)); ion_mapped = True
        cl = g.Client()
        m, seen = cl.call(s.APM_CMD_SHARED_MEM_MAP_REGIONS, s.map_cmd(buf, SIZE), token=0x5A5A0002, secs=3.0)
        if m and m['opcode'] == s.APM_CMD_RSP_SHARED_MEM_MAP_REGIONS: handle = struct.unpack_from('<I', m['payload'])[0]
        step(map_reply=hex(m['opcode']) if m else None, handle=handle, basic=s.basic_status(m), seen=len(seen))
        if handle is not None:
            m, seen = cl.call(s.APM_CMD_SHARED_MEM_UNMAP_REGIONS, struct.pack('<I', handle), token=0x5A5A0003, secs=3.0)
            st = s.basic_status(m); step(unmap_reply=hex(m['opcode']) if m else None, basic=st)
            r['unmap_ok'] = bool(st and st[0] == s.APM_CMD_SHARED_MEM_UNMAP_REGIONS and st[1] == 0)
        r['handle'] = handle
    finally:
        if cl: cl.close()
        if ion_mapped:
            try: step(ion_unmap_ret=__import__('fcntl').ioctl(ion, s.IOCTL_UNMAP_PHYS_ADDR, buf))
            except OSError as e: step(ion_unmap_error=str(e))
        if ion is not None: os.close(ion)
        if buf is not None: os.close(buf)
    return r
