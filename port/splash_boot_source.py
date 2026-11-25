"""Boot loading screen with the game's own key art (ux0:data/hld-vm/splash.png) behind a slim progress bar.

Replaces vita/boot_progress.c with the same interface. The screen is a 960x544 display-only surface; without the
image it falls back to the original dark background. The decoded art is kept until the game takes over the
display, so text and bar updates repaint from it.
"""

BOOT_PROGRESS = r'''#include "boot_progress.h"

#include "default_font/jetbrains_mono_extrabold_font.h"
#include "log.h"
uint64_t nowNanos(void);
static uint64_t hldBootStartNs;
#include "stb_image.h"
#include <psp2/display.h>
#include <stdlib.h>
#include <vitaGL.h>

enum {
    BOOT_WIDTH = 960, BOOT_HEIGHT = 544, BOOT_PITCH = 960, BOOT_ALIGNMENT = 256,
    BAR_LEFT = 300, BAR_WIDTH = 360, BAR_TOP = 520, BAR_HEIGHT = 3,
    LABEL_TOP = 494, LABEL_SAMPLE = 3, FADE_TOP = 430
};

static void* bootAllocation;
static SceDisplayFrameBuf bootFrame;
static bool bootFinished;
static int lastProgress;
static int lastPhase;
static uint32_t* bootArt;  // BOOT_WIDTH x BOOT_HEIGHT, A8B8G8R8, with the bottom fade applied

static inline uint32_t* framePixel(int x, int y) { return (uint32_t*)bootFrame.base + y * bootFrame.pitch + x; }

static uint32_t backgroundAt(int x, int y) {
    return bootArt != NULL ? bootArt[y * BOOT_WIDTH + x] : 0xff1c1611u;
}

static void restoreRect(int x, int y, int width, int height) {
    for (int row = 0; row < height; ++row)
        for (int column = 0; column < width; ++column) *framePixel(x + column, y + row) = backgroundAt(x + column, y + row);
}

static void fillRect(int x, int y, int width, int height, uint32_t color) {
    for (int row = 0; row < height; ++row)
        for (int column = 0; column < width; ++column) *framePixel(x + column, y + row) = color;
}

static int textWidth(const char* text, int sampleStep) {
    int width = 0;
    while (*text) width += JetBrainsMonoExtraBoldGlyphs[*text++ - DEFAULTFONT_FIRST_CP].xadvance / sampleStep;
    return width;
}

// Glyph coverage blended over whatever is already on the screen.
static void drawText(int x, int top, const char* text, int sampleStep, unsigned red, unsigned green, unsigned blue) {
    while (*text) {
        const JetBrainsMonoExtraBoldGlyph* glyph = &JetBrainsMonoExtraBoldGlyphs[*text++ - DEFAULTFONT_FIRST_CP];
        for (int row = 0; row < glyph->h; row += sampleStep) {
            const uint8_t* alpha = JetBrainsMonoExtraBoldPixels + (glyph->y + row) * DEFAULTFONT_ATLAS_W + glyph->x;
            const int y = top + (glyph->yoffset + row) / sampleStep;
            if (y < 0 || y >= BOOT_HEIGHT) continue;
            for (int column = 0; column < glyph->w; column += sampleStep) {
                const unsigned coverage = alpha[column];
                const int px = x + (glyph->xoffset + column) / sampleStep;
                if (!coverage || px < 0 || px >= BOOT_WIDTH) continue;
                uint32_t* out = framePixel(px, y);
                const unsigned br = *out & 0xff, bg = (*out >> 8) & 0xff, bb = (*out >> 16) & 0xff;
                const unsigned r = (red * coverage + br * (255 - coverage)) / 255;
                const unsigned g = (green * coverage + bg * (255 - coverage)) / 255;
                const unsigned b = (blue * coverage + bb * (255 - coverage)) / 255;
                *out = 0xff000000u | (b << 16) | (g << 8) | r;
            }
        }
        x += glyph->xadvance / sampleStep;
    }
}

// Scale the key art to fill the screen (cropping the longer side) and darken the bottom for the bar and label.
static void loadArt(void) {
    int width = 0, height = 0, channels = 0;
    unsigned char* image = stbi_load("ux0:data/hld-vm/splash.png", &width, &height, &channels, 4);
    if (image == NULL || width <= 0 || height <= 0) {
        if (image != NULL) stbi_image_free(image);
        return;
    }
    bootArt = (uint32_t*)malloc((size_t)BOOT_WIDTH * BOOT_HEIGHT * sizeof(uint32_t));
    if (bootArt == NULL) {
        stbi_image_free(image);
        return;
    }
    // Uniform scale that covers the whole screen.
    const double scale = (double)BOOT_WIDTH / width > (double)BOOT_HEIGHT / height
        ? (double)BOOT_WIDTH / width : (double)BOOT_HEIGHT / height;
    const double offsetX = (width * scale - BOOT_WIDTH) / 2, offsetY = (height * scale - BOOT_HEIGHT) / 2;
    for (int y = 0; y < BOOT_HEIGHT; ++y) {
        int sy = (int)((y + offsetY) / scale);
        if (sy >= height) sy = height - 1;
        const unsigned shade = y < FADE_TOP ? 256 : 256 - (unsigned)(170 * (y - FADE_TOP) / (BOOT_HEIGHT - FADE_TOP));
        for (int x = 0; x < BOOT_WIDTH; ++x) {
            int sx = (int)((x + offsetX) / scale);
            if (sx >= width) sx = width - 1;
            const unsigned char* p = image + ((size_t)sy * width + sx) * 4;
            const unsigned r = p[0] * shade >> 8, g = p[1] * shade >> 8, b = p[2] * shade >> 8;
            bootArt[y * BOOT_WIDTH + x] = 0xff000000u | (b << 16) | (g << 8) | r;
        }
    }
    stbi_image_free(image);
    logInfo("Vita loading screen: key art %dx%d\n", width, height);
}

void VitaBootProgress_begin(void) {
    hldBootStartNs = nowNanos();
    if (sceGxmDisplayQueueFinish() < 0) return;
    // Use a display-only surface, so the loading screen cannot overwrite a
    // GPU render target. VitaGL reserves CDRAM before this point and reuses
    // the memory once the game's own display surface takes over.
    bootAllocation = vglAlloc(BOOT_PITCH * BOOT_HEIGHT * sizeof(uint32_t) + BOOT_ALIGNMENT - 1, VGL_MEM_VRAM);
    if (bootAllocation == NULL) {
        logWarn("Vita loading screen: video memory unavailable\n");
        return;
    }
    uintptr_t aligned = ((uintptr_t)bootAllocation + BOOT_ALIGNMENT - 1) & ~(uintptr_t)(BOOT_ALIGNMENT - 1);
    bootFrame = (SceDisplayFrameBuf){
        .size = sizeof(bootFrame),
        .base = (void*)aligned,
        .pitch = BOOT_PITCH,
        .pixelformat = SCE_DISPLAY_PIXELFORMAT_A8B8G8R8,
        .width = BOOT_WIDTH,
        .height = BOOT_HEIGHT,
    };
    lastPhase = -1;
    lastProgress = -1;
    bootFinished = false;
    loadArt();
    restoreRect(0, 0, BOOT_WIDTH, BOOT_HEIGHT);
    VitaBootProgress_set(VITA_BOOT_ARCHIVE, 0, 1);
}

void VitaBootProgress_set(VitaBootPhase phase, unsigned completed, unsigned total) {
    if (bootAllocation == NULL || bootFinished || total == 0) return;
    static const struct { int start, end; const char* label; } stages[] = {
        {4, 54, "Reading game data"},
        {54, 66, "Starting the game engine"},
        {66, 91, "Preparing graphics"},
        {91, 100, "Loading the opening room"},
    };
    if (completed > total) completed = total;
    int progress = stages[phase].start + (int)((uint64_t)completed * (stages[phase].end - stages[phase].start) / total);
    if ((int)phase == lastPhase && progress == lastProgress) return;
    if ((int)phase != lastPhase) {
        restoreRect(0, LABEL_TOP - 4, BOOT_WIDTH, BAR_TOP - LABEL_TOP);
        const char* label = stages[phase].label;
        drawText((BOOT_WIDTH - textWidth(label, LABEL_SAMPLE)) / 2, LABEL_TOP, label, LABEL_SAMPLE, 236, 228, 240);
    }
    fillRect(BAR_LEFT, BAR_TOP, BAR_WIDTH, BAR_HEIGHT, 0xff3a2430u);
    fillRect(BAR_LEFT, BAR_TOP, BAR_WIDTH * progress / 100, BAR_HEIGHT, 0xffc65af0u);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    int result = sceDisplaySetFrameBuf(&bootFrame, SCE_DISPLAY_SETBUF_NEXTFRAME);
    if (result < 0) {
        logWarn("Vita loading screen: display rejected the frame (%#x)\n", (unsigned)result);
        // Keep any previously displayed surface alive until scanout moves on.
        if (lastPhase < 0) {
            vglFree(bootAllocation);
            bootAllocation = NULL;
        }
        bootFinished = true;
        return;
    }
    if ((int)phase != lastPhase) logInfo("Vita loading screen: %s at_ms=%.0f\n", stages[phase].label, (nowNanos() - hldBootStartNs) * 1e-6);
    lastPhase = phase;
    lastProgress = progress;
}

void VitaBootProgress_end(void) {
    VitaBootProgress_set(VITA_BOOT_ROOM, 1, 1);
    bootFinished = true;
}

void VitaBootProgress_gamePresented(void) {
    if (!bootFinished || bootAllocation == NULL || sceGxmDisplayQueueFinish() < 0) return;
    SceDisplayFrameBuf display = {.size = sizeof(display)};
    if (sceDisplayGetFrameBuf(&display, SCE_DISPLAY_SETBUF_IMMEDIATE) < 0 || display.base == bootFrame.base) return;
    vglFree(bootAllocation);
    bootAllocation = NULL;
    free(bootArt);
    bootArt = NULL;
    logInfo("Vita loading screen handed off to the game at_ms=%.0f\n", (nowNanos() - hldBootStartNs) * 1e-6);
}
'''


def with_splash_boot(code):
    assert "void VitaBootProgress_begin(void)" in code
    return BOOT_PROGRESS
