"""Use compressed, content-checked decoded sprite copies for room reloads."""
from pathlib import Path


def with_sprite_cache(code):
    anchor = "static RValue builtin_sprite_add(VMContext* ctx, RValue* args, int32_t argCount) {"
    assert code.count(anchor) == 1
    lo = code.index("    int w, h;\n", code.index(anchor)) + len("    int w, h;\n")
    hi = code.index("    free(bytes);", lo)
    assert "ImageDecoder_decodeToRgba(bytes," in code[lo:hi]
    code = code[:lo] + (
        "    uint8_t* pixels = size > 0 ? decodeHldSprite(ctx, bytes, (size_t)size, &w, &h) : nullptr;\n"
    ) + code[hi:]
    cache = Path(__file__).with_name("sprite-cache.inc").read_text()
    return code.replace(anchor, cache + "\n" + anchor)
