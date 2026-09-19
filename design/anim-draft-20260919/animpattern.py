"""CPU reference of anim.spv (verification only; frames are produced by the GPU)."""
W, H, PITCH, STRIDE = 1904, 3040, 7680, 1920

def pixel(x, y, f):
    if x >= W: return 0
    r = (x * 255) // 1903
    g = (((y + f * 8) % 3040) * 255) // 3039
    b = 255 if ((x >> 6) ^ (y >> 6)) & 1 else 64
    cx = 400 + (f * 12) % 1104
    d2 = (x - cx) ** 2 + (y - 1520) ** 2
    return 0xFFFFFF if 90000 <= d2 < 102400 else (r << 16) | (g << 8) | b

def points(f, n=1200):
    """Per-frame deterministic samples: a stride over the frame + the ring's 4 cardinal points + 4 padding pixels."""
    step = (W * H) // n
    pts = [((i * step + f * 131) % (W * H) % W, ((i * step + f * 131) % (W * H)) // W) for i in range(n)]
    cx = 400 + (f * 12) % 1104
    pts += [(cx + 310, 1520), (cx - 310, 1520), (cx, 1520 + 310), (cx, 1520 - 310)]
    pts += [(1904, 0), (1919, 1519), (1910, 3039), (1905, f % H)]
    return pts
