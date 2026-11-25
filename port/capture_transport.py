"""Transport exact Vita framebuffer bytes using either raw RGBA or zlib."""

from pathlib import Path
import zlib


MAGIC = b"HLDRGBAZ"
FRAME_BYTES = 960 * 544 * 4


def decode_capture(payload):
    if payload is None:
        return None
    if len(payload) == FRAME_BYTES:
        return payload
    if not payload.startswith(MAGIC):
        return None
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(payload[len(MAGIC):], FRAME_BYTES + 1)
    except zlib.error:
        return None
    if len(pixels) != FRAME_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        return None
    return pixels


def with_compressed_capture(code):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    anchor = '#include <stdio.h>'
    replace(anchor, anchor + '\n#define MINIZ_NO_ZLIB_COMPATIBLE_NAMES\n#include <miniz.h>')
    anchor = "static void captureVitaFrame(void) {"
    replace(anchor, Path(__file__).with_name("capture-transport.inc").read_text() + "\n" + anchor)
    before = """    int y;
    for (y = 0; y < DISPLAY_HEIGHT; ++y) {
        if (fwrite(pixels + y * DISPLAY_STRIDE, sizeof(*pixels), DISPLAY_WIDTH, output) != (size_t)DISPLAY_WIDTH) break;
    }
    if (fclose(output) == 0 && y == DISPLAY_HEIGHT) {
        sceIoRemove(ready);
        sceIoRename(pending, ready);
        logInfo("Vita frame captured on runner frame %d\\n", swapFrame);
    }"""
    after = """    FILE* marker = exact ? fopen("ux0:data/hld-vm/compressed-capture", "r") : NULL;
    bool success = false;
    if (marker != NULL) {
        fclose(marker);
        success = writeCompressedCapture(output, pixels, DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_STRIDE);
        if (!success) {
            fclose(output);
            output = fopen(pending, "wb");
            if (output == NULL) {
                sceIoRemove(pending);
                return;
            }
        }
    }
    if (!success) {
        int y;
        for (y = 0; y < DISPLAY_HEIGHT; ++y) {
            if (fwrite(pixels + y * DISPLAY_STRIDE, sizeof(*pixels), DISPLAY_WIDTH, output) != (size_t)DISPLAY_WIDTH) break;
        }
        success = y == DISPLAY_HEIGHT;
    }
    if (fclose(output) == 0 && success) {
        sceIoRemove(ready);
        if (sceIoRename(pending, ready) >= 0) {
            logInfo("Vita frame captured on runner frame %d\\n", swapFrame);
            return;
        }
    }
    sceIoRemove(pending);"""
    replace(before, after)
    return code
