
import argparse
import struct
from pathlib import Path

from PIL import Image

MAX_SIZE = 256


def prepare(img, size=None, colors=None):
    img = img.convert("RGBA")
    if size is None and max(img.size) > MAX_SIZE:
        size = MAX_SIZE
    if size is not None:
        img = img.resize((size, size), Image.LANCZOS)
    if colors:
        alpha = img.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
        rgb = img.convert("RGB").quantize(
            colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE,
        ).convert("RGB")
        rgb.putalpha(alpha)
        img = rgb
    return img


def _rects(img):
    w, h = img.size
    px = img.load()
    rects = []
    open_runs = {}
    for y in range(h):
        row_runs = {}
        x = 0
        while x < w:
            c = px[x, y]
            if c[3] == 0:
                x += 1
                continue
            end = x + 1
            while end < w and px[end, y] == c:
                end += 1
            key = (x, end, c)
            rect = open_runs.get(key)
            if rect is not None:
                rect[3] = y + 1
            else:
                rect = [x, end, y, y + 1, c]
                rects.append(rect)
            row_runs[key] = rect
            x = end
        open_runs = row_runs
    return rects


def png_to_vco(src, size=None, colors=None) -> bytes:
    img = prepare(Image.open(src), size, colors)
    w, h = img.size

    def sx(x):
        return round(x * 255 / w)

    def sy(y):
        return round((h - y) * 255 / h)

    coords = bytearray()
    colors_out = bytearray()
    for x_start, x_end, y_top, y_bottom, c in _rects(img):
        x0, x1, y0, y1 = sx(x_start), sx(x_end), sy(y_bottom), sy(y_top)
        if x0 == x1 or y0 == y1:
            continue
        for tri in (((x0, y0), (x1, y0), (x1, y1)), ((x0, y0), (x1, y1), (x0, y1))):
            for vx, vy in tri:
                coords += struct.pack("BB", vx, vy)
                colors_out += bytes(c)
    return b"VCO\0" + bytes((255, 255, 0, 0)) + bytes(coords) + bytes(colors_out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("src", type=Path)
    parser.add_argument("dst", type=Path, nargs="?")
    parser.add_argument("--size", type=int)
    parser.add_argument("--colors", type=int)
    args = parser.parse_args()
    dst = args.dst or args.src.with_suffix(".dat")
    dst.write_bytes(png_to_vco(args.src, args.size, args.colors))
    print(f"{dst} ({dst.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
