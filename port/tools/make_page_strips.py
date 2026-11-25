#!/usr/bin/env python3
"""Write pagez/pNNN.hlz for every data.win texture page: the page decoded to RGBA (8-bit, non-interlaced PNGs only)
and split into whole-row strips, each an LZ4 block (page_strips.h has the layout). The Vita decodes the strips on
several cores at once instead of inflating one PNG stream.

Usage: make-page-strips.py data.win out_dir [--strips 12]"""

import argparse
import io
import struct
import sys
from pathlib import Path

import lz4.block
import numpy as np
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("data")
parser.add_argument("out")
parser.add_argument("--strips", type=int, default=12)
parser.add_argument("--plain", action="store_true", help="HLZ1: rows stored as is")
parser.add_argument("--min-saving", type=int, default=40 << 10,
                    help="HLZ3: a strip is stored filtered only when that saves at least this many bytes")
options = parser.parse_args()

data = Path(options.data).read_bytes()
chunks, offset = {}, 8
while offset < len(data):
    name = data[offset:offset + 4].decode()
    size = struct.unpack_from("<I", data, offset + 4)[0]
    chunks[name] = offset + 8
    offset += 8 + size
txtr = chunks["TXTR"]
count = struct.unpack_from("<I", data, txtr)[0]
out = Path(options.out)
out.mkdir(parents=True, exist_ok=True)
total = 0
for page in range(count):
    entry = struct.unpack_from("<I", data, txtr + 4 + 4 * page)[0]
    blob = struct.unpack_from("<I", data, entry + 4)[0]
    end = data.index(b"IEND", blob) + 8
    png = data[blob:end]
    width, height, depth, colour, _, _, interlace = struct.unpack_from(">IIBBBBB", png, 16)
    if (depth, colour, interlace) != (8, 6, 0):
        print(f"page {page}: not 8-bit RGBA, skipped", file=sys.stderr)
        continue
    image = Image.open(io.BytesIO(png))
    assert image.mode == "RGBA" and image.size == (width, height)
    raw = image.tobytes()
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(height, width * 4)
    filtered = pixels.copy()
    filtered[:, 4:] = pixels[:, 4:] - pixels[:, :-4]  # uint8 arithmetic wraps mod 256
    delta = filtered.tobytes()
    strips = min(options.strips, height)
    rows = -(-height // strips)
    header, blocks = [], []
    for first in range(0, height, rows):
        n = min(rows, height - first)
        span = slice(first * width * 4, (first + n) * width * 4)
        block = lz4.block.compress(raw[span], mode="high_compression", compression=12, store_size=False)
        flag = 0
        if not options.plain:
            other = lz4.block.compress(delta[span], mode="high_compression", compression=12, store_size=False)
            if len(block) - len(other) >= options.min_saving:
                block, flag = other, 0x80000000
        header.append(struct.pack("<III", first, n | flag, len(block)))
        blocks.append(block)
    body = (b"HLZ1" if options.plain else b"HLZ3") + struct.pack("<III", width, height, len(blocks)) + b"".join(header) + b"".join(blocks)
    (out / f"p{page:03d}.hlz").write_bytes(body)
    total += len(body)
print(f"wrote {count} pages, {total / 2**20:.0f} MB")
