"""Pack your data.win's texture pages into textures.bin for the Vita: BC3 (DXT5) where that stays visually
identical (mean error limits below), the original PNG otherwise.

Usage: build_texture_bundle.py data.win textures.bin"""
import ctypes
import io
import mmap
from pathlib import Path
import struct
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
ENCODER = ROOT / "deps" / ("bc3-encoder.dylib" if sys.platform == "darwin" else
                          "bc3-encoder.dll" if sys.platform == "win32" else "bc3-encoder.so")  # scripts/bootstrap.py


def encode_bc3_pillow(image, size):
    """BC3 (DXT5) blocks from Pillow's DDS writer, for computers without the stb_dxt encoder library."""
    stream = io.BytesIO()
    image.save(stream, "DDS", pixel_format="DXT5")
    data = stream.getvalue()
    header = 4 + 124 + (20 if data[84:88] == b"DX10" else 0)
    blocks = data[header:header + size]
    if len(blocks) != size:
        raise RuntimeError("unexpected DDS output from Pillow")
    return blocks


def decode_bc3(payload, width, height):
    """Decode a BC3 (DXT5) page with Pillow's DDS reader, to measure the encoding error."""
    fields = [124, 0x81007, height, width, len(payload), 0, 0] + [0] * 11
    fields += [32, 4, int.from_bytes(b"DXT5", "little"), 0, 0, 0, 0, 0]
    fields += [0x1000, 0, 0, 0, 0]
    return Image.open(io.BytesIO(b"DDS " + struct.pack("<31I", *fields) + payload)).convert("RGBA")

PVR_V3 = 0x03525650
PVR_HEADER = struct.Struct("<IIQIIIIIIIII")


def extract_atlas(data):
    at = 8
    while at + 8 <= len(data):
        tag, size = struct.unpack_from("<4sI", data, at)
        start = at + 8
        end = start + size
        if end > len(data):
            raise ValueError("Truncated WAD")
        if tag == b"TXTR":
            count, = struct.unpack_from("<I", data, start)
            pointers = struct.unpack_from(f"<{count}I", data, start + 4)
            offsets = [struct.unpack_from("<I", data, p + 4)[0] for p in pointers]
            if count != 150 or offsets != sorted(offsets) or not start < offsets[0] < offsets[-1] < end:
                raise ValueError("Unexpected 2016 atlas layout")
            return zip(offsets, offsets[1:] + [end])
        at = end
    raise ValueError("No TXTR chunk")


def main():
    source, output = map(Path, sys.argv[1:])
    encoder = None
    if ENCODER.is_file():
        encoder = ctypes.CDLL(str(ENCODER))
        encoder.encode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        encoder.encode.restype = None
    staged = output.with_suffix(".part")
    png_pages = 0
    sys.path.insert(0, str(ROOT / "scripts"))
    from progress import Bar
    bar = Bar(150, "texture pages repacked")
    with source.open("rb") as wad, mmap.mmap(wad.fileno(), 0, access=mmap.ACCESS_READ) as data:
        with staged.open("wb") as stream:
            stream.write(struct.pack("<I", 150))
            for page, (start, end) in enumerate(extract_atlas(data)):
                png = data[start:end]
                image = Image.open(io.BytesIO(png)).convert("RGBA")
                width, height = image.size
                size = ((width + 3) // 4) * ((height + 3) // 4) * 16
                if encoder is not None:
                    bc3 = ctypes.create_string_buffer(size)
                    encoder.encode(image.tobytes(), bc3, width, height)
                    bc3_raw = bc3.raw
                else:
                    bc3_raw = encode_bc3_pillow(image, size)
                decoded = np.asarray(decode_bc3(bc3_raw, width, height), dtype=np.int16)
                original = np.asarray(image, dtype=np.int16)
                delta = np.abs(decoded - original)
                alpha = original[:, :, 3]
                weight = max(1, alpha.sum(dtype=np.int64) * 3)
                visible_mae = (delta[:, :, :3] * alpha[:, :, None].astype(np.int32)).sum() / weight
                alpha_mae = delta[:, :, 3].mean()
                if visible_mae > 6 or alpha_mae > 1:
                    png_pages += 1
                    payload = png
                else:
                    pvr = PVR_HEADER.pack(PVR_V3, 0, 11, 0, 0, height, width, 1, 1, 1, 1, 0)
                    payload = pvr + bc3_raw
                stream.write(struct.pack("<I", len(payload)))
                stream.write(payload)
                bar.update(page + 1)
    bar.finish(False)
    staged.replace(output)
    print(f"{output.name} ready: {output.stat().st_size:,} bytes, {150-png_pages} BC3, {png_pages} original PNG", flush=True)


if __name__ == "__main__":
    main()
