"""Reuse the synchronized framebuffer bytes during dynamic sprite creation."""

from runtime_verification_source import replace_once


BORROW_FRAMEBUFFER = r'''
const uint8_t *hldVglBorrowReadRGBA(GLint x, GLint y, GLsizei width, GLsizei height) {
    THREAD_SAFE()
#if defined(HAVE_UNFLIPPED_FBOS) || defined(READBACKS_SPEEDHACK)
    return NULL;
#else
    // This is the contiguous part of glReadPixels' fast RGBA8 path.
    // The caller consumes it synchronously before touching the surface again.
    framebuffer *fb = active_read_fb;
    if (!fb || !fb->data || fb->format != SCE_GXM_TEXTURE_FORMAT_U8U8U8U8_ABGR ||
        x != 0 || y < 0 || width <= 0 || height <= 0 || width > fb->width ||
        height > fb->height || y > fb->height - height || (size_t)width * 4 != (size_t)fb->stride)
        return NULL;
    if (in_use_framebuffer == fb) {
        dirty_framebuffer = GL_TRUE;
        scene_reset();
        sceGxmFinish(gxm_context);
    }
    const size_t top = (size_t)(fb->height - height - y);
    return (const uint8_t *)fb->data + top * (size_t)fb->stride;
#endif
}
'''

MASK_PACK = r'''
typedef struct {
    int32_t left, top, right, bottom;
} SpriteMaskBounds;

static SpriteMaskBounds packSpriteAlphaMask(uint8_t* mask, const uint8_t* pixels, int32_t w, int32_t h) {
    SpriteMaskBounds bounds = {.left = w, .top = h, .right = -1, .bottom = -1};
    const size_t stride = ((size_t)w + 7) / 8;
#if defined(__ARM_NEON)
    static const uint8_t weights[16] = {128, 64, 32, 16, 8, 4, 2, 1, 128, 64, 32, 16, 8, 4, 2, 1};
    const uint8x16_t powers = vld1q_u8(weights);
    const uint8x16_t zero = vdupq_n_u8(0);
#endif
    for (int32_t row = 0; row < h; ++row) {
        uint8_t* rowMask = mask + (size_t)row * stride;
        const uint8_t* rgba = pixels + (size_t)row * (size_t)w * 4;
        int32_t col = 0;
#if defined(__ARM_NEON)
        for (; col <= w - 16; col += 16) {
            const uint8x16_t alpha = vld4q_u8(rgba + (size_t)col * 4).val[3];
            const uint8x16_t set = vandq_u8(vcgtq_u8(alpha, zero), powers);
            uint8x8_t packed = vpadd_u8(vget_low_u8(set), vget_high_u8(set));
            packed = vpadd_u8(packed, packed);
            packed = vpadd_u8(packed, packed);
            rowMask[col / 8] = vget_lane_u8(packed, 0);
            rowMask[col / 8 + 1] = vget_lane_u8(packed, 1);
        }
#endif
        for (; col <= w - 8; col += 8) {
            const uint8_t* p = rgba + (size_t)col * 4 + 3;
            rowMask[col / 8] = (uint8_t)((p[0] != 0) << 7 | (p[4] != 0) << 6 |
                (p[8] != 0) << 5 | (p[12] != 0) << 4 | (p[16] != 0) << 3 |
                (p[20] != 0) << 2 | (p[24] != 0) << 1 | (p[28] != 0));
        }
        if (col < w) {
            uint8_t tail = 0;
            for (int32_t i = col; i < w; ++i) {
                if (rgba[(size_t)i * 4 + 3]) tail |= 0x80u >> (i % 8);
            }
            rowMask[col / 8] = tail;
        }
        size_t first = 0;
        while (first < stride && rowMask[first] == 0) ++first;
        if (first == stride) continue;
        size_t last = stride - 1;
        while (rowMask[last] == 0) --last;
        const int32_t left = (int32_t)(first * 8) + __builtin_clz((uint32_t)rowMask[first]) - 24;
        const int32_t right = (int32_t)(last * 8) + 7 - __builtin_ctz((uint32_t)rowMask[last]);
        if (left < bounds.left) bounds.left = left;
        if (right > bounds.right) bounds.right = right;
        if (row < bounds.top) bounds.top = row;
        bounds.bottom = row;
    }
    return bounds;
}
'''


def with_borrowed_surface_read(code):
    anchor = "void glReadPixels(GLint x, GLint y, GLsizei width, GLsizei height, GLenum format, GLenum type, GLvoid *data) {"
    return replace_once(code, anchor, BORROW_FRAMEBUFFER + "\n" + anchor)


def with_surface_sprite_renderer(code):
    anchor = "static int32_t glCreateSpriteFromPixels("
    helper = r'''
extern bool hldSurfaceSprites;
const uint8_t* hldVglBorrowReadRGBA(GLint x, GLint y, GLsizei width, GLsizei height);

static bool hldReadbackChecked, hldReadbackRejected;

static bool useHldSurfaceSprites(const DataWin* data) {
    return hldSurfaceSprites && data->gen8.gameID == 742741103 && data->gen8.wadVersion == 15;
}
'''
    code = replace_once(code, anchor, helper + MASK_PACK + "\n" + anchor)
    include = '#include "vm_builtins.h"'
    code = replace_once(code, include, include + r'''
#include <psp2/kernel/processmgr.h>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif''')
    start = code.index(anchor)
    end = code.index("\nstatic void glDeleteSprite(", start)
    before, renderer, after = code[:start], code[start:end], code[end:]
    renderer = replace_once(renderer, "    GLuint newTexId;", "    const uint64_t uploadStart = sceKernelGetProcessTimeWide();\n    GLuint newTexId;")
    marker = "    // Find or allocate slots for texture page, TPAG, and sprite"
    renderer = replace_once(renderer, marker, "    const unsigned uploadUs = (unsigned)(sceKernelGetProcessTimeWide() - uploadStart);\n\n" + marker)
    marker = "    int32_t left = w, top = h, right = -1, bottom = -1;"
    start_mask = renderer.index(marker)
    end_mask = renderer.index("    sprite->marginLeft = left;", start_mask)
    original_mask = renderer[start_mask:end_mask].split("\n", 1)[1].rstrip()
    packed = r'''
    const uint64_t maskStart = sceKernelGetProcessTimeWide();
    int32_t left = w, top = h, right = -1, bottom = -1;
    if (useHldSurfaceSprites(dw)) {
        const SpriteMaskBounds bounds = packSpriteAlphaMask(mask, pixels, w, h);
        left = bounds.left;
        top = bounds.top;
        right = bounds.right;
        bottom = bounds.bottom;
    } else {
'''.lstrip("\n") + "\n".join("    " + line for line in original_mask.splitlines()) + r'''
    }
    logInfo("Vita sprite mask width=%d height=%d upload_us=%u mask_us=%u\n",
            w, h, uploadUs, (unsigned)(sceKernelGetProcessTimeWide() - maskStart));
'''
    renderer = renderer[:start_mask] + packed + renderer[end_mask:]
    anchor = '''    uint8_t* pixels = safeMalloc((size_t)w * (size_t)h * 4);
    glReadPixels(x, y, w, h, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    glBindFramebuffer(GL_FRAMEBUFFER, previousFbo);
    int32_t sprite = glCreateSpriteFromPixels(renderer, pixels, w, h, smooth, xorig, yorig);
    free(pixels);'''
    renderer = replace_once(renderer, anchor, r'''    const uint64_t readStart = sceKernelGetProcessTimeWide();
    const bool fast = useHldSurfaceSprites(renderer->dataWin) && !hldReadbackRejected;
    const uint8_t* borrowed = fast ? hldVglBorrowReadRGBA(x, y, w, h) : nullptr;
    uint8_t* owned = nullptr;
    const size_t size = (size_t)w * (size_t)h * 4;
    // The first admitted framebuffer verifies the driver view on-device.
    if (borrowed == nullptr || !hldReadbackChecked) {
        owned = safeMalloc(size);
        glReadPixels(x, y, w, h, GL_RGBA, GL_UNSIGNED_BYTE, owned);
        if (borrowed != nullptr) {
            hldReadbackChecked = true;
            hldReadbackRejected = memcmp(owned, borrowed, size) != 0;
            if (hldReadbackRejected) borrowed = nullptr;
            logInfo("Vita sprite surface readback verified=%d\n", (int)!hldReadbackRejected);
        }
    }
    glBindFramebuffer(GL_FRAMEBUFFER, previousFbo);
    logInfo("Vita sprite surface width=%d height=%d borrowed=%d read_us=%u\n", w, h,
            (int)(borrowed != nullptr), (unsigned)(sceKernelGetProcessTimeWide() - readStart));
    const int32_t sprite = glCreateSpriteFromPixels(renderer, borrowed ? borrowed : owned, w, h, smooth, xorig, yorig);
    free(owned);''')
    return before + renderer + after
