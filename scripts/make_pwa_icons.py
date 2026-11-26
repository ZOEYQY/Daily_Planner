"""
Draws the phone app icons in pwa/ (no image library needed).

    python scripts/make_pwa_icons.py

Same look as the calendar's browser tab icon: a white calendar on a dark
rounded square. The "maskable" icon has extra margin so Android can crop it to
a circle or squircle without cutting the calendar off.
"""
import os
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pwa")
BG = (21, 24, 33)        # #151821, same as the Daily Planner top bar
FG = (255, 255, 255)
ACCENT = (58, 110, 165)  # #3a6ea5, the bar's highlighted tab
SS = 4                   # supersampling for smooth edges


def rounded_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def shape(u, v, full_bleed, scale):
    """Colour at unit coords (0..1) of the icon, or None for transparent."""
    if full_bleed:
        inside_bg = True
    else:
        inside_bg = rounded_rect(u, v, 0, 0, 1, 1, 0.22)
    if not inside_bg:
        return None
    # Calendar glyph in a 100x100 design box, shrunk by `scale` about the centre.
    x = (u - 0.5) / scale * 100 + 50
    y = (v - 0.5) / scale * 100 + 50
    w = 7.5  # stroke width
    outer = rounded_rect(x, y, 22, 28, 78, 78, 9)
    inner = rounded_rect(x, y, 22 + w, 28 + w, 78 - w, 78 - w, 4)
    header = 22 <= x <= 78 and 28 <= y <= 46 and outer
    rings = any(rounded_rect(x, y, cx - w / 2, 19, cx + w / 2, 36, w / 2) for cx in (37, 63))
    dot = rounded_rect(x, y, 54, 55, 68, 68, 3)
    if rings or (outer and not inner) or header:
        return FG
    if dot:
        return ACCENT
    return BG


def render(size, full_bleed, scale):
    rows = []
    for py in range(size):
        row = bytearray([0])  # filter type 0
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    u = (px + (sx + 0.5) / SS) / size
                    v = (py + (sy + 0.5) / SS) / size
                    c = shape(u, v, full_bleed, scale)
                    if c is not None:
                        acc[0] += c[0]
                        acc[1] += c[1]
                        acc[2] += c[2]
                        acc[3] += 255
            n = SS * SS
            a = acc[3] // n
            if a:
                cov = acc[3] / 255
                row += bytes([round(acc[0] / cov), round(acc[1] / cov), round(acc[2] / cov), a])
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))
    return png(size, size, b"".join(rows))


def png(w, h, raw):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, False, 1.0),
        ("icon-512.png", 512, False, 1.0),
        ("icon-maskable-512.png", 512, True, 0.78),
        # iOS rounds the corners itself and shows transparency as black.
        ("apple-touch-icon.png", 180, True, 0.92),
    ]
    for name, size, full, scale in jobs:
        with open(os.path.join(OUT_DIR, name), "wb") as fh:
            fh.write(render(size, full, scale))
        print("wrote", name)


if __name__ == "__main__":
    main()
