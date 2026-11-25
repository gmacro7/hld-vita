"""Set string fields (TITLE, STITLE) of a Vita param.sfo in place (each must fit its reserved max_len)."""
import struct


def set_sfo_strings(data: bytes, values: dict) -> bytes:
    data = bytearray(data)
    magic, version, key_table, data_table, count = struct.unpack_from("<4sIIII", data, 0)
    assert magic == b"\x00PSF", "not a param.sfo"
    found = set()
    for i in range(count):
        key_offset, fmt, length, max_length, data_offset = struct.unpack_from("<HHIII", data, 20 + 16 * i)
        end = data.index(b"\x00", key_table + key_offset)
        key = data[key_table + key_offset:end].decode()
        if key not in values:
            continue
        value = values[key].encode() + b"\x00"
        assert fmt == 0x0204 and len(value) <= max_length, f"{key} does not fit ({len(value)} > {max_length})"
        start = data_table + data_offset
        data[start:start + max_length] = value.ljust(max_length, b"\x00")
        struct.pack_into("<I", data, 20 + 16 * i + 4, len(value))
        found.add(key)
    return bytes(data), found


def dump(data: bytes) -> dict:
    magic, version, key_table, data_table, count = struct.unpack_from("<4sIIII", data, 0)
    out = {}
    for i in range(count):
        key_offset, fmt, length, max_length, data_offset = struct.unpack_from("<HHIII", data, 20 + 16 * i)
        end = data.index(b"\x00", key_table + key_offset)
        key = data[key_table + key_offset:end].decode()
        raw = data[data_table + data_offset:data_table + data_offset + length]
        out[key] = (raw.rstrip(b"\x00").decode(errors="replace") if fmt != 0x0404 else struct.unpack("<I", raw)[0], max_length)
    return out
