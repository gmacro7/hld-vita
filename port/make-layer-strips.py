#!/usr/bin/env python3
"""Write <name>.png.hlz (page_strips.h HLZ3 layout) next to every room layer PNG (sprite_add'ed at each room load):
decoded RGBA in row strips, each LZ4-HC, stored filtered (left-pixel delta) when that is >= --min-saving smaller.

Usage: make-layer-strips.py depot_dir out_dir   (out_dir mirrors <Region>/<file>.png.hlz)"""
import argparse, struct, sys
from pathlib import Path
import lz4.block
import numpy as np
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("depot")
parser.add_argument("out")
parser.add_argument("--strips", type=int, default=12)
parser.add_argument("--min-saving", type=int, default=8 << 10)
options = parser.parse_args()
total = count = 0
for png in sorted(Path(options.depot).glob("*/*.png")):
    data = png.read_bytes()
    width, height, depth, colour, _, _, interlace = struct.unpack_from(">IIBBBBB", data, 16)
    if (depth, colour, interlace) != (8, 6, 0):
        print(f"{png.name}: not 8-bit RGBA, skipped", file=sys.stderr)
        continue
    image = Image.open(png)
    assert image.mode == "RGBA" and image.size == (width, height)
    raw = image.tobytes()
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(height, width * 4)
    delta = pixels.copy()
    delta[:, 4:] = pixels[:, 4:] - pixels[:, :-4]
    delta = delta.tobytes()
    strips = min(options.strips, height)
    rows = -(-height // strips)
    header, blocks = [], []
    for first in range(0, height, rows):
        n = min(rows, height - first)
        span = slice(first * width * 4, (first + n) * width * 4)
        block = lz4.block.compress(raw[span], mode="high_compression", compression=12, store_size=False)
        other = lz4.block.compress(delta[span], mode="high_compression", compression=12, store_size=False)
        flag = 0
        if len(block) - len(other) >= options.min_saving:
            block, flag = other, 0x80000000
        header.append(struct.pack("<III", first, n | flag, len(block)))
        blocks.append(block)
    body = b"HLZ3" + struct.pack("<III", width, height, len(blocks)) + b"".join(header) + b"".join(blocks)
    target = Path(options.out) / png.parent.name / (png.name + ".hlz")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    total += len(body); count += 1
print(f"wrote {count} layer strip files, {total / 2**20:.0f} MB")
