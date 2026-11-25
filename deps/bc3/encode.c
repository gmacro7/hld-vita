
#include <string.h>
#define STB_DXT_IMPLEMENTATION
#include "stb_dxt.h"
#ifdef _WIN32
__declspec(dllexport)
#endif
void encode(const unsigned char *src, unsigned char *dst, int w, int h) {
    unsigned char block[64];
    for (int y = 0; y < h; y += 4) for (int x = 0; x < w; x += 4) {
        for (int j = 0; j < 4; j++) for (int i = 0; i < 4; i++) {
            int sx = x + i < w ? x + i : w - 1;
            int sy = y + j < h ? y + j : h - 1;
            memcpy(block + (j * 4 + i) * 4, src + (sy * w + sx) * 4, 4);
        }
        stb_compress_dxt_block(dst, block, 1, STB_DXT_HIGHQUAL);
        dst += 16;
    }
}
