#!/usr/bin/env python3
"""Screenshot of the running desktop, straight from the compositor (zwlr_screencopy via wl.py/wlcapture.py).

    python3 screenshot.py out.png [--step N] [--crop X,Y,W,H] [--no-rotate]

The panel is used in landscape (output transform 270), so the capture is turned upright by default; --no-rotate keeps
the compositor's own orientation. --step N samples every Nth pixel for a smaller file (1 = full resolution)."""
import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('XDG_RUNTIME_DIR', '/run/y700-desktop')
os.environ.setdefault('WAYLAND_DISPLAY', 'wayland-0')


def png(path, rows):
    h = len(rows); w = len(rows[0]) // 3
    raw = b''.join(b'\0' + r for r in rows)
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n'
                + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
    return w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--step', type=int, default=1)
    ap.add_argument('--crop', help='X,Y,W,H in compositor coordinates')
    ap.add_argument('--no-rotate', action='store_true')
    a = ap.parse_args()

    import wl, wlcapture
    c = wl.Conn(); reg, g = wl.registry(c)
    buf, info = wlcapture.Capturer(c, reg, g).capture(damage=False)
    w, h, st = info['w'], info['h'], info['stride']
    x0, y0, cw, ch = 0, 0, w, h
    if a.crop:
        x0, y0, cw, ch = (int(v) for v in a.crop.split(','))
        cw, ch = min(cw, w - x0), min(ch, h - y0)
    s = max(a.step, 1)
    px = [[bytes((buf[y * st + x * 4 + 2], buf[y * st + x * 4 + 1], buf[y * st + x * 4]))
           for x in range(x0, x0 + cw, s)] for y in range(y0, y0 + ch, s)]
    if not a.no_rotate:                      # output transform 270: turn the capture upright
        px = [[px[y][len(px[0]) - 1 - x] for y in range(len(px))] for x in range(len(px[0]))]
    ow, oh = png(a.out, [b''.join(r) for r in px])
    print('%s %dx%d (captured %dx%d stride %d)' % (a.out, ow, oh, w, h, st))


if __name__ == '__main__':
    main()
