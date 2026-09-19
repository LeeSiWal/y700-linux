"""dma-buf (dma-heap 'system') + msm_audio_ion IOVA registration + APM shared-memory map/unmap.
msm_audio_ion.c: IOCTL_MAP_PHYS_ADDR = _IOW('a', 97, int) takes the dma-buf fd as the ioctl argument value.
audio-pkt.c: for APM_CMD_SHARED_MEM_MAP_REGIONS with property_flag & 0x1C1 == 0, shm_addr_lsw carries the fd and is replaced
by the registered IOVA. Upstream audioreach uses mem_pool_id SHMEM8_4K (3), size aligned to 4 KiB."""
import fcntl, mmap, os, stat, struct
from pathlib import Path
import gprclient as g

DMA_HEAP_IOCTL_ALLOC = 0xc0184800                 # _IOWR('H', 0, struct dma_heap_allocation_data) (24 bytes)
IOCTL_MAP_PHYS_ADDR, IOCTL_UNMAP_PHYS_ADDR = 0x40046161, 0x40046162
APM_CMD_SHARED_MEM_MAP_REGIONS, APM_CMD_SHARED_MEM_UNMAP_REGIONS = 0x0100100C, 0x0100100D
APM_CMD_RSP_SHARED_MEM_MAP_REGIONS, GPR_IBASIC_RSP_RESULT = 0x02001001, 0x02001005
POOL_SHMEM8_4K = 3

def chrnode(path, sysdev):
    mj, mn = (int(x) for x in Path(sysdev).read_text().split(':')); p = Path(path)
    p.parent.mkdir(exist_ok=True)
    if not p.exists(): os.mknod(p, stat.S_IFCHR | 0o600, os.makedev(mj, mn))
    st = os.lstat(p)
    if not (stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn)): raise RuntimeError('node mismatch ' + path)
    return '%d:%d' % (mj, mn)

def alloc(size):
    hfd = os.open('/dev/dma_heap/system', os.O_RDWR | os.O_CLOEXEC)
    try:
        req = bytearray(struct.pack('<QIIQ', size, 0, os.O_RDWR | os.O_CLOEXEC, 0))
        fcntl.ioctl(hfd, DMA_HEAP_IOCTL_ALLOC, req, True)
        return struct.unpack_from('<QIIQ', req)[1]
    finally: os.close(hfd)

def map_cmd(fd, size):
    return struct.pack('<HHI', POOL_SHMEM8_4K, 1, 0) + struct.pack('<III', fd, 0, size)

def basic_status(m):
    return struct.unpack_from('<II', m['payload']) if m and m['opcode'] == GPR_IBASIC_RSP_RESULT and len(m['payload']) >= 8 else None

DMA_BUF_IOCTL_SYNC = 0x40086200                            # _IOW('b', 0, struct dma_buf_sync {u64 flags})
SYNC_READ, SYNC_WRITE, SYNC_END = 1, 2, 4

def cpu_write(fd, mm, off, data):
    """CPU writes into a (cached) dma-buf that the DSP reads: begin/end cpu access so the kernel cleans the cache."""
    fcntl.ioctl(fd, DMA_BUF_IOCTL_SYNC, struct.pack('<Q', SYNC_WRITE))
    mm[off:off + len(data)] = data
    fcntl.ioctl(fd, DMA_BUF_IOCTL_SYNC, struct.pack('<Q', SYNC_END | SYNC_WRITE))

def cpu_read(fd, mm, off, n):
    fcntl.ioctl(fd, DMA_BUF_IOCTL_SYNC, struct.pack('<Q', SYNC_READ))
    d = bytes(mm[off:off + n])
    fcntl.ioctl(fd, DMA_BUF_IOCTL_SYNC, struct.pack('<Q', SYNC_END | SYNC_READ))
    return d
