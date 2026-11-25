#include "boot_progress.h"

#include "default_font/jetbrains_mono_extrabold_font.h"
#include "log.h"
#include <psp2/display.h>
#include <vitaGL.h>

enum {
    BOOT_WIDTH = 480, BOOT_HEIGHT = 272, BOOT_PITCH = 512, BOOT_ALIGNMENT = 256,
    BAR_LEFT = 135, BAR_WIDTH = 210
};

static void* bootAllocation;
static SceDisplayFrameBuf bootFrame;
static bool bootFinished;
static int lastProgress;
static int lastPhase;

static void fillRect(int x, int y, int width, int height, uint32_t color) {
    uint32_t* pixels = bootFrame.base;
    for (int row = 0; row < height; ++row) {
        uint32_t* out = pixels + (y + row) * bootFrame.pitch + x;
        for (int column = 0; column < width; ++column) out[column] = color;
    }
}

static void drawText(int x, int top, const char* text, int sampleStep,
                     unsigned red, unsigned green, unsigned blue) {
    uint32_t* pixels = bootFrame.base;
    while (*text) {
        const JetBrainsMonoExtraBoldGlyph* glyph = &JetBrainsMonoExtraBoldGlyphs[*text++ - DEFAULTFONT_FIRST_CP];
        for (int row = 0; row < glyph->h; row += sampleStep) {
            const uint8_t* alpha = JetBrainsMonoExtraBoldPixels +
                (glyph->y + row) * DEFAULTFONT_ATLAS_W + glyph->x;
            uint32_t* out = pixels + (top + (glyph->yoffset + row) / sampleStep) * bootFrame.pitch +
                            x + glyph->xoffset / sampleStep;
            for (int column = 0; column < glyph->w; column += sampleStep) {
                const unsigned coverage = alpha[column];
                if (!coverage) continue;
                const unsigned r = (red * coverage + 17 * (255 - coverage)) / 255;
                const unsigned g = (green * coverage + 22 * (255 - coverage)) / 255;
                const unsigned b = (blue * coverage + 28 * (255 - coverage)) / 255;
                out[column / sampleStep] = 0xff000000u | (b << 16) | (g << 8) | r;
            }
        }
        x += glyph->xadvance / sampleStep;
    }
}

void VitaBootProgress_begin(void) {
    if (sceGxmDisplayQueueFinish() < 0) return;
    // Use a display-only surface, so the loading screen cannot overwrite a
    // GPU render target. VitaGL reserves CDRAM before this point and reuses
    // the memory once the game's own display surface takes over.
    bootAllocation = vglAlloc(BOOT_PITCH * BOOT_HEIGHT * sizeof(uint32_t) +
                              BOOT_ALIGNMENT - 1, VGL_MEM_VRAM);
    if (bootAllocation == NULL) {
        logWarn("Vita loading screen: video memory unavailable\n");
        return;
    }
    uintptr_t aligned = ((uintptr_t)bootAllocation + BOOT_ALIGNMENT - 1) &
                        ~(uintptr_t)(BOOT_ALIGNMENT - 1);
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
    fillRect(0, 0, BOOT_WIDTH, BOOT_HEIGHT, 0xff1c1611u);
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
    int progress = stages[phase].start + (int)((uint64_t)completed *
                   (stages[phase].end - stages[phase].start) / total);
    if ((int)phase == lastPhase && progress == lastProgress) return;
    if ((int)phase != lastPhase) {
        fillRect(BAR_LEFT - 2, 104, BAR_WIDTH + 4, 57, 0xff1c1611u);
        drawText(BAR_LEFT, 109, "Loading", 4, 233, 245, 247);
        drawText(BAR_LEFT, 141, stages[phase].label, 6, 151, 176, 194);
    }
    fillRect(BAR_LEFT, 133, BAR_WIDTH, 4, 0xff372a22u);
    fillRect(BAR_LEFT, 133, BAR_WIDTH * progress / 100, 4, 0xffdbe67bu);
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
    if ((int)phase != lastPhase) logInfo("Vita loading screen: %s\n", stages[phase].label);
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
    logInfo("Vita loading screen handed off to the game\n");
}
