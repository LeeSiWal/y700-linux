"""KGSL UAPI subset (msm_kgsl.h) used read-only by the first-open worker. Pure; unit-testable."""
import struct

KGSL_IOC_TYPE = 0x09
KGSL_PROP_DEVICE_INFO = 0x1
# struct kgsl_devinfo (arm64): u32 device_id, u32 chip_id, u32 mmu_enabled, [pad], u64 gmem_gpubaseaddr,
#                             u32 gpu_id, [pad], u64 gmem_sizebytes  -> 40 bytes
DEVINFO = struct.Struct('<III4xQI4xQ')
# struct kgsl_device_getproperty (arm64): u32 type, [pad], u64 value ptr, u64 sizebytes -> 24 bytes
GETPROP = struct.Struct('<I4xQQ')


def iowr(typ, nr, size):
    return (3 << 30) | (size << 16) | (typ << 8) | nr


IOCTL_KGSL_DEVICE_GETPROPERTY = iowr(KGSL_IOC_TYPE, 0x2, GETPROP.size)


def parse_devinfo(raw):
    if len(raw) != DEVINFO.size:
        raise ValueError('devinfo size')
    dev, chip, mmu, gbase, gpu_id, gsize = DEVINFO.unpack(raw)
    return {'device_id': dev, 'chip_id': chip, 'mmu_enabled': mmu, 'gmem_gpubaseaddr': gbase,
            'gpu_id': gpu_id, 'gmem_sizebytes': gsize}
