#include <arm_neon.h>
#include "gettime.h"

typedef struct {
    int32_t left;
    int32_t top;
    int32_t right;
    int32_t bottom;
} MaskBounds;

static inline void recordMaskSpan(MaskBounds* bounds, int32_t left, int32_t right, int32_t row) {
    if (left < bounds->left) bounds->left = left;
    if (right > bounds->right) bounds->right = right;
    if (row < bounds->top) bounds->top = row;
    bounds->bottom = row;
}

static MaskBounds fillVectorMask(uint8_t* mask, const uint8_t* pixels, int32_t w, int32_t h, size_t stride) {
    MaskBounds bounds = {w, h, -1, -1};
    const uint8x8_t weights = {128, 64, 32, 16, 8, 4, 2, 1};
    const uint8x8_t zero = vdup_n_u8(0);
    for (int32_t row = 0; row < h; ++row) {
        uint8_t* rowMask = mask + (size_t)row * stride;
        const uint8_t* rgba = pixels + (size_t)row * (size_t)w * 4;
        int32_t col = 0;
        for (; col <= w - 8; col += 8) {
            uint8x8_t alpha = vld4_u8(rgba + (size_t)col * 4).val[3];
            uint8x8_t bits = vand_u8(vcgt_u8(alpha, zero), weights);
            uint32x2_t packed = vpaddl_u16(vpaddl_u8(bits));
            unsigned byte = vget_lane_u32(packed, 0) + vget_lane_u32(packed, 1);
            rowMask[col / 8] = (uint8_t)byte;
            if (byte != 0) {
                int32_t left = col + __builtin_clz(byte) - 24;
                int32_t right = col + 7 - __builtin_ctz(byte);
                recordMaskSpan(&bounds, left, right, row);
            }
        }
        for (; col < w; ++col) {
            if (rgba[(size_t)col * 4 + 3] == 0) continue;
            rowMask[col / 8] |= 0x80u >> (col % 8);
            recordMaskSpan(&bounds, col, col, row);
        }
    }
    return bounds;
}
