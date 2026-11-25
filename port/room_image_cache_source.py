"""Admit the room-sized sprites to the existing exact RGBA decode cache."""

from runtime_verification_source import replace_once


def with_room_image_cache(code, *, launch_flag=None, checked=False):
    code = replace_once(code, '#include "sprite_decode_cache.h"', '#include "image/sprite_decode_cache.h"')
    code = replace_once(code, '#include "image_decoder.h"', '#include "image/image_decoder.h"')
    code = replace_once(code, "#include <string.h>",
                        '#include <string.h>\n\n#include "gettime.h"\n#include "log.h"')
    if launch_flag:
        code = replace_once(code, "enum { SPRITE_CACHE_ENTRIES",
                            f"extern bool {launch_flag};\n\nenum {{ SPRITE_CACHE_ENTRIES")
    lower_limit = "960 * 544"
    if launch_flag:
        lower_limit = f"({launch_flag} ? 960 * 544 : 1024 * 1024)"
    code = replace_once(code, "pixelCount < 1024 * 1024", f"pixelCount < {lower_limit}")
    anchor = "uint8_t* SpriteDecodeCache_decode("
    code = replace_once(code, anchor, r'''static void reportRoomImageDecode(int w, int h, size_t fileBytes,
                                  uint64_t started, bool cached) {
    if (w < 960 || h < 544) return;
    logInfo("HLD room image decode width=%d height=%d source_bytes=%u cached=%d decode_ms=%.3f\n",
            w, h, (unsigned)fileBytes, (int)cached, (nowNanos() - started) * 1e-6);
}

''' + anchor)
    anchor = "    for (unsigned index = 0; *cache != nullptr && index < SPRITE_CACHE_ENTRIES; ++index) {"
    code = replace_once(code, anchor, "    const uint64_t started = nowNanos();\n" + anchor)
    code = replace_once(code, "        return (uint8_t*)rgba;",
                        "        reportRoomImageDecode(*w, *h, fileBytes, started, true);\n        return (uint8_t*)rgba;")
    anchor = "    if (pixels != nullptr) rememberSpritePixels(cache, file, fileBytes, pixels, *w, *h);"
    code = replace_once(code, anchor, '''    if (pixels != nullptr) {
        rememberSpritePixels(cache, file, fileBytes, pixels, *w, *h);
        reportRoomImageDecode(*w, *h, fileBytes, started, false);
    }''')
    return ("#define HLD_VERIFY_SPRITE_CACHE\n" if checked else "") + code
