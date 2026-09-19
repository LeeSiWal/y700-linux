"""CPU reference of render.spv's per-pixel formula (for verification only; the image itself is produced by the GPU)."""
W, H, PITCH, STRIDE = 1904, 3040, 7680, 1920   # row stride in pixels = PITCH/4

def pixel(x, y):
    r = (x * 255) // 1903; g = (y * 255) // 3039
    b = 255 if ((x >> 6) ^ (y >> 6)) & 1 else 64
    d2 = (x - 952) ** 2 + (y - 1520) ** 2
    return 0xFFFFFF if 360000 <= d2 < 384400 else (r << 16) | (g << 8) | b

def sample_points(step=97):
    """Deterministic ~60k points plus all four edges and the ring's cardinal points."""
    pts = [(i % W, i // W) for i in range(0, W * H, step)]
    pts += [(x, 0) for x in range(W)] + [(x, H - 1) for x in range(W)] + [(0, y) for y in range(H)] + [(W - 1, y) for y in range(H)]
    pts += [(952 + 610, 1520), (952 - 610, 1520), (952, 1520 + 610), (952, 1520 - 610), (952, 1520)]
    return pts
