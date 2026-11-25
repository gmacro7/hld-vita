"""Symbol tables from the 2016 data.win: variables (VARI), assets by kind, and code entry names."""

import struct
from pathlib import Path

GAME = Path(__import__("os").environ.get("HLD_DATA_WIN") or Path(__file__).resolve().parent.parent / "work" / "game" / "data.win")  # your own copy (scripts/prepare_game.py), or $HLD_DATA_WIN


class DataWin:
    def __init__(self, path=GAME):
        self.data = Path(path).read_bytes()
        self.chunks = {}
        position = 8
        while position < len(self.data):
            name = self.data[position:position + 4].decode()
            size = struct.unpack_from("<I", self.data, position + 4)[0]
            self.chunks[name] = (position + 8, size)
            position += 8 + size

    def string(self, offset):
        length = struct.unpack_from("<I", self.data, offset - 4)[0]
        return self.data[offset:offset + length].decode("utf-8", "replace")

    def named_list(self, chunk):
        """Names of a pointer-list chunk whose entries start with a name pointer."""
        start, _ = self.chunks[chunk]
        count = struct.unpack_from("<I", self.data, start)[0]
        names = []
        for i in range(count):
            pointer = struct.unpack_from("<I", self.data, start + 4 + 4 * i)[0]
            names.append(self.string(struct.unpack_from("<I", self.data, pointer)[0]) if pointer else None)
        return names

    def variables(self):
        """(name, instanceType, varID) for every VARI entry (bytecode 15: 12-byte header, 20-byte entries)."""
        start, size = self.chunks["VARI"]
        out = []
        position = start + 12
        while position + 20 <= start + size:
            name, instance_type, var_id, _, _ = struct.unpack_from("<IiiiI", self.data, position)
            out.append((self.string(name), instance_type, var_id))
            position += 20
        return out

    def assets(self):
        kinds = {}
        for chunk, kind in (("OBJT", "object"), ("SPRT", "sprite"), ("SOND", "sound"), ("ROOM", "room"),
                            ("BGND", "background"), ("PATH", "path"), ("SCPT", "script"), ("FONT", "font"),
                            ("TMLN", "timeline"), ("SHDR", "shader")):
            if chunk in self.chunks:
                for index, name in enumerate(self.named_list(chunk)):
                    if name is not None:
                        kinds.setdefault(name, (kind, index))
        return kinds


if __name__ == "__main__":
    win = DataWin()
    variables = win.variables()
    assets = win.assets()
    print(len(variables), "variables;", len(assets), "assets")
    print(variables[:5])
