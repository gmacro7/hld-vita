"""Make the Vita button prompt icons (app0:vita-icons/*.png) from your own data.win.

The Vita's shoulder buttons act as the controller's L2/R2 and D-pad left/right as L1/R1, so the engine points those
prompt sprites at these images (engine_speedups_source.py, hldVitaButtonIcons):
  dleft.png / dright.png  the game's PlayStation D-pad prompt, rotated so its highlighted arm points left / right
  L.png / R.png           the game's L1 / R1 prompt badge with only the letter (the trailing "1" removed)

Usage: python3 make_vita_icons.py <data.win> <output folder>
"""
import io
import struct
import sys
from pathlib import Path

from PIL import Image

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


class Wad:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        self.chunks = {}
        at = 8
        while at < len(self.data):
            tag = self.data[at:at + 4].decode()
            size = struct.unpack_from("<I", self.data, at + 4)[0]
            self.chunks[tag] = at + 8
            at += 8 + size
        start = self.chunks["TXTR"]
        count = struct.unpack_from("<I", self.data, start)[0]
        pointers = struct.unpack_from(f"<{count}I", self.data, start + 4)
        self.page_offsets = [struct.unpack_from("<I", self.data, p + 4)[0] for p in pointers]
        self.pages = {}

    def string(self, offset):
        length = struct.unpack_from("<I", self.data, offset - 4)[0]
        return self.data[offset:offset + length].decode("utf-8", "replace")

    def page(self, index):
        if index not in self.pages:
            # A PNG ends with its IEND chunk; Pillow stops reading there, so no length is needed.
            self.pages[index] = Image.open(io.BytesIO(self.data[self.page_offsets[index]:])).convert("RGBA")
        return self.pages[index]

    def sprite_frame(self, name):
        """First frame of a sprite, at the sprite's full size (bytecode 15 / GameMaker 1.4 layout)."""
        start = self.chunks["SPRT"]
        count = struct.unpack_from("<I", self.data, start)[0]
        for i in range(count):
            pointer = struct.unpack_from("<I", self.data, start + 4 + 4 * i)[0]
            if not pointer or self.string(struct.unpack_from("<I", self.data, pointer)[0]) != name:
                continue
            width, height = struct.unpack_from("<ii", self.data, pointer + 4)
            frames = struct.unpack_from("<I", self.data, pointer + 56)[0]
            if frames < 1:
                raise SystemExit(f"{name} has no frames")
            item = struct.unpack_from("<I", self.data, pointer + 60)[0]
            sx, sy, sw, sh, tx, ty, tw, th, bw, bh, page = struct.unpack_from("<11H", self.data, item)
            crop = self.page(page).crop((sx, sy, sx + sw, sy + sh))
            if (sw, sh) != (tw, th):
                crop = crop.resize((tw, th), Image.NEAREST)
            frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            frame.paste(crop, (tx, ty))
            return frame
        raise SystemExit(f"sprite {name} not found")


def letter_only(image, glyph_x0, glyph_x1):
    """Keep only the letter of an "L1"/"R1" badge: clear the dark text, then redraw the letter's columns centred."""
    width, height = image.size
    out = image.copy()
    for y in range(height):
        for x in range(width):
            if out.getpixel((x, y)) == BLACK:
                out.putpixel((x, y), WHITE)
    columns = [x for x in range(width) if image.getpixel((x, 7))[3] > 0]
    centre = (columns[0] + columns[-1]) / 2
    start = round(centre - (glyph_x1 - glyph_x0) / 2)
    for y in range(height):
        for x in range(glyph_x0, glyph_x1 + 1):
            if image.getpixel((x, y)) == BLACK:
                out.putpixel((start + x - glyph_x0, y), BLACK)
    return out


def badge_sized_pad(pad, angle, size=(24, 16)):
    """Rotate the D-pad so its highlighted arm points left/right, then fit it to the L1/R1 badge size (24x16) by
    dropping repeated rows from the two vertical arms, so the menu layout stays as it was."""
    turned = pad.rotate(angle)
    turned = turned.crop(turned.getbbox())
    rows = [turned.crop((0, y, turned.width, y + 1)) for y in range(turned.height)]
    same = lambda y: rows[y].tobytes() == rows[y - 1].tobytes()
    keep = list(range(len(rows)))
    top = [y for y in range(1, len(rows) // 2) if same(y)]
    bottom = [y for y in range(len(rows) // 2, len(rows)) if same(y)]
    while len(keep) > size[1] and (top or bottom):
        for arm in (top, bottom):
            if arm and len(keep) > size[1]:
                keep.remove(arm.pop())
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    y0 = (size[1] - min(len(keep), size[1])) // 2
    for i, y in enumerate(keep[:size[1]]):
        out.paste(rows[y], ((size[0] - turned.width) // 2, y0 + i))
    return out


def make(data_win, output):
    wad, output = Wad(data_win), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    letter_only(wad.sprite_frame("spr_MEN_Bttn_PS_L1"), 8, 10).save(output / "L.png")
    letter_only(wad.sprite_frame("spr_MEN_Bttn_PS_R1"), 7, 9).save(output / "R.png")
    pad = wad.sprite_frame("spr_MEN_Bttn_PS_dpad")
    badge_sized_pad(pad, -90).save(output / "dleft.png")
    badge_sized_pad(pad, 90).save(output / "dright.png")


def main():
    make(sys.argv[1], sys.argv[2])
    print(f"Vita button icons written to {sys.argv[2]}")


if __name__ == "__main__":
    main()
