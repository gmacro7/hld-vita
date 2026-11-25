"""Read and decode the original GML bytecode of the 2016 data.win (format 15).

Mirrors Butterscotch's parseCODE and OpWord rules. Operand words are returned as stored in the file; variable
and function references are resolved at runtime, so the translator only relies on opcodes, types, immediates,
branch targets and instruction addresses.
"""

from dataclasses import dataclass
from pathlib import Path
import struct

OPCODES = {
    0xC0: "PUSH", 0xC1: "PUSH_LOCAL", 0xC2: "PUSH_GLOBAL", 0xC3: "PUSH_BUILTIN", 0x84: "PUSH_IMMEDIATE",
    0x45: "POP", 0x86: "DUP", 0x07: "CONV", 0x08: "MUL", 0x09: "DIV", 0x0A: "REM", 0x0B: "MOD", 0x0C: "ADD",
    0x0D: "SUB", 0x0E: "AND", 0x0F: "OR", 0x10: "XOR", 0x11: "NEG", 0x12: "NOT", 0x13: "SHL", 0x14: "SHR",
    0x15: "CMP", 0xB6: "B", 0xB7: "BT", 0xB8: "BF", 0xD9: "CALL", 0x99: "CALLV", 0xBA: "PUSH_ENV",
    0xBB: "POP_ENV", 0x9C: "RET", 0x9D: "EXIT", 0x9E: "POPZ", 0xFF: "BREAK",
}
TYPE_NAMES = {0: "d", 1: "f", 2: "i", 3: "l", 4: "b", 5: "v", 6: "s", 15: "e"}
OPERAND_BYTES = {0: 8, 1: 4, 2: 4, 3: 8, 4: 4, 5: 4, 6: 4, 15: 0}
DEFAULT_DATA = Path(__import__("os").environ.get("HLD_DATA_WIN") or Path(__file__).resolve().parent.parent / "work" / "game" / "data.win")  # your own copy (scripts/prepare_game.py), or $HLD_DATA_WIN


@dataclass(frozen=True)
class Instruction:
    address: int          # byte offset from the start of the code entry
    word: int
    operand: bytes

    @property
    def opcode(self):
        return self.word >> 24

    @property
    def name(self):
        return OPCODES.get(self.opcode, f"0x{self.opcode:02x}")

    @property
    def type1(self):
        return (self.word >> 16) & 0xF

    @property
    def type2(self):
        return (self.word >> 20) & 0xF

    @property
    def extra(self):
        value = self.word & 0xFFFF
        return value - 0x10000 if value & 0x8000 else value

    @property
    def comparison(self):
        return (self.word >> 8) & 0xF

    @property
    def size(self):
        return 4 + len(self.operand)

    @property
    def branch_target(self):
        offset = ((self.word << 9) & 0xFFFFFFFF)
        offset = (offset - (1 << 32) if offset & 0x80000000 else offset) >> 7
        return self.address + offset

    def __str__(self):
        types = TYPE_NAMES.get(self.type1, "?") + ("." + TYPE_NAMES.get(self.type2, "?") if self.opcode < 0x80 else "")
        return f"{self.address:04x} {self.name}.{types} extra={self.extra} operand={self.operand.hex()}"


@dataclass(frozen=True)
class CodeEntry:
    index: int
    name: str
    length: int
    locals_count: int
    arguments_count: int
    bytecode: bytes       # the entry's own instructions (offset .. length)

    def instructions(self):
        address, data = 0, self.bytecode
        while address < len(data):
            word = struct.unpack_from("<I", data, address)[0]
            opcode = word >> 24
            operand_size = OPERAND_BYTES.get((word >> 16) & 0xF, 0) if opcode & 0x40 else 0
            yield Instruction(address, word, data[address + 4:address + 4 + operand_size])
            address += 4 + operand_size


def _chunks(data):
    assert data[:4] == b"FORM"
    position, end = 8, 8 + struct.unpack_from("<I", data, 4)[0]
    while position < end:
        name, length = data[position:position + 4].decode(), struct.unpack_from("<I", data, position + 4)[0]
        yield name, position + 8, length
        position += 8 + length


def _string(data, pointer):
    # String pointers address the characters, preceded by a 32-bit length.
    length = struct.unpack_from("<I", data, pointer - 4)[0]
    return data[pointer:pointer + length].decode("utf-8", "replace")


def read_code_entries(path=DEFAULT_DATA):
    data = Path(path).read_bytes()
    chunks = {name: (start, length) for name, start, length in _chunks(data)}
    start, length = chunks["CODE"]
    count = struct.unpack_from("<I", data, start)[0]
    pointers = struct.unpack_from(f"<{count}I", data, start + 4)
    entries = []
    for index, pointer in enumerate(pointers):
        name = _string(data, struct.unpack_from("<I", data, pointer)[0])
        code_length, locals_count, arguments_count = struct.unpack_from("<IHH", data, pointer + 4)
        relative_field = pointer + 12
        bytecode_start = relative_field + struct.unpack_from("<i", data, relative_field)[0]
        offset = struct.unpack_from("<I", data, pointer + 16)[0]
        # Shared blobs: an entry runs from its offset inside the blob to the entry's length.
        entries.append(CodeEntry(index, name, code_length, locals_count, arguments_count,
                                 data[bytecode_start + offset:bytecode_start + code_length]))
    return entries
