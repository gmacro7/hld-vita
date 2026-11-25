#include "common.h"
#include "gettime.h"
#include "log.h"
#include "platformdefs.h"
#include "boot_progress.h"
#include <psp2/ctrl.h>
#include <psp2/io/fcntl.h>
#include <psp2/kernel/threadmgr.h>
#include <psp2/touch.h>
#include <vitaGL.h>
#include <stdio.h>

#ifdef ENABLE_VITA_ASYNC_PRESENT
#include "present.h"
#endif

static Runner* activeRunner;
static bool rearTouchEnabled;
static bool measuringSwap;
static uint64_t displayQueueNanos;
static uint64_t finalSceneNanos;

static void swapNow(void);
static void reportFinishedSwap(void);

int __real_sceGxmDisplayQueueAddEntry(SceGxmSyncObject*, SceGxmSyncObject*, const void*);
int __real_sceGxmEndScene(SceGxmContext*, const SceGxmNotification*, const SceGxmNotification*);

int __wrap_sceGxmDisplayQueueAddEntry(SceGxmSyncObject* oldBuffer, SceGxmSyncObject* newBuffer, const void* callbackData) {
    uint64_t start = measuringSwap ? nowNanos() : 0;
    int result = __real_sceGxmDisplayQueueAddEntry(oldBuffer, newBuffer, callbackData);
    if (measuringSwap) displayQueueNanos += nowNanos() - start;
    return result;
}

int __wrap_sceGxmEndScene(SceGxmContext* context, const SceGxmNotification* vertex, const SceGxmNotification* fragment) {
    uint64_t start = measuringSwap ? nowNanos() : 0;
    int result = __real_sceGxmEndScene(context, vertex, fragment);
    if (measuringSwap) finalSceneNanos += nowNanos() - start;
    return result;
}

static bool rearHealPressed(void) {
    SceTouchData touch;
    if (!rearTouchEnabled || sceTouchPeek(SCE_TOUCH_PORT_BACK, &touch, 1) <= 0) return false;

    for (unsigned i = 0; i < touch.reportNum && i < SCE_TOUCH_MAX_REPORT; ++i) {
        const SceTouchReport* contact = &touch.report[i];
        // Rear coordinates use the same 1920x1088 grid as the earlier Vita port.
        if (contact->x >= 0 && contact->x < 960 && contact->y >= 0 && contact->y < 544) return true;
    }
    return false;
}

static void pollVitaGamepad(RunnerGamepadState* pads) {
    SceCtrlData pad = {0};
    GamepadSlot* slot = &pads->slots[0];
    bool connected = sceCtrlPeekBufferPositive(0, &pad, 1) > 0;
    if (connected && !slot->connected) {
        strcpy(slot->description, "PS Vita");
        slot->jid = 0;
    }
    slot->connected = connected;
    pads->connectedCount = connected ? 1 : 0;

    static const uint32_t masks[GP_BUTTON_COUNT] = {
        SCE_CTRL_CROSS, SCE_CTRL_CIRCLE, SCE_CTRL_SQUARE, SCE_CTRL_TRIANGLE,
        SCE_CTRL_L1, 0, SCE_CTRL_LTRIGGER, SCE_CTRL_RTRIGGER, SCE_CTRL_SELECT, SCE_CTRL_START,
        0, 0, SCE_CTRL_UP, SCE_CTRL_DOWN, SCE_CTRL_LEFT, SCE_CTRL_RIGHT, 0,
    };
    uint32_t buttons = pad.buttons;
    if (connected && rearHealPressed()) buttons |= SCE_CTRL_L1;
    for (int i = 0; i < GP_BUTTON_COUNT; ++i) {
        bool down = connected && (buttons & masks[i]) != 0;
        slot->buttonDownPrev[i] = slot->buttonDown[i];
        slot->buttonPressed[i] = down && !slot->buttonDown[i];
        slot->buttonReleased[i] = !down && slot->buttonDown[i];
        slot->buttonDown[i] = down;
        slot->buttonValue[i] = down ? 1.0f : 0.0f;
    }

    const unsigned axes[GP_AXIS_COUNT] = {pad.lx, pad.ly, pad.rx, pad.ry};
    for (int i = 0; i < GP_AXIS_COUNT; ++i) {
        int centered = (int)axes[i] - 128;
        slot->axisValue[i] = connected ? centered / (centered < 0 ? 128.0f : 127.0f) : 0.0f;
    }
}

bool platformInit(MAYBE_UNUSED int32_t width, MAYBE_UNUSED int32_t height,
    MAYBE_UNUSED const char* title, MAYBE_UNUSED bool headless, MAYBE_UNUSED bool fullscreen) {
    rearTouchEnabled = sceTouchSetSamplingState(SCE_TOUCH_PORT_BACK, SCE_TOUCH_SAMPLING_STATE_START) >= 0;
    if (!rearTouchEnabled) logWarn("Vita: rear touch sampling unavailable\n");
#ifdef ENABLE_VITA_ASYNC_PRESENT
    VitaPresent_start(swapNow, reportFinishedSwap);
#endif
    logInfo("Native Vita display backend ready\n");
    return true;
}

static bool displayAlwaysAvailable(void) {
    return true;
}

void platformInitFunctions(Runner* runner) {
    activeRunner = runner;
    runner->windowHasFocus = displayAlwaysAvailable;
    runner->getWindowFullscreen = displayAlwaysAvailable;
    runner->currentCursor = GML_CR_NONE;
}

void platformExit(void) {
#ifdef ENABLE_VITA_ASYNC_PRESENT
    VitaPresent_stop();
#endif
    activeRunner = NULL;
}

bool platformGetWindowSize(int32_t* width, int32_t* height) {
    if (!width || !height) return false;
    *width = 960;
    *height = 544;
    return true;
}

bool platformGetScaledWindowSize(int32_t* width, int32_t* height) {
    return platformGetWindowSize(width, height);
}

void platformSetWindowSize(MAYBE_UNUSED int32_t width, MAYBE_UNUSED int32_t height) {
}

void platformSetWindowTitle(MAYBE_UNUSED const char* title) {
}

void platformGetMousePos(double* x, double* y) {
    if (x) *x = -1.0;
    if (y) *y = -1.0;
}

static void captureVitaFrame(void) {
    static unsigned frames;
    if (++frames != 90) return;
    frames = 0;
    const char* request = "ux0:data/hld-vm/capture-request";
    if (sceIoRemove(request) < 0) return;

    extern void* gxm_color_surfaces_addr[];
    extern unsigned int gxm_front_buffer_index;
    extern int DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_STRIDE;
    if (sceGxmDisplayQueueFinish() < 0) return;
    const uint32_t* pixels = gxm_color_surfaces_addr[gxm_front_buffer_index];
    if (pixels == NULL || DISPLAY_WIDTH != 960 || DISPLAY_HEIGHT != 544 || DISPLAY_STRIDE < 960) return;

    const char* pending = "ux0:data/hld-vm/frame.pending";
    const char* ready = "ux0:data/hld-vm/frame.rgba";
    FILE* output = fopen(pending, "wb");
    if (output == NULL) return;
    int y;
    for (y = 0; y < DISPLAY_HEIGHT; ++y) {
        if (fwrite(pixels + y * DISPLAY_STRIDE, sizeof(*pixels), DISPLAY_WIDTH, output) != (size_t)DISPLAY_WIDTH) break;
    }
    if (fclose(output) == 0 && y == DISPLAY_HEIGHT) {
        sceIoRemove(ready);
        sceIoRename(pending, ready);
        logInfo("Vita frame captured\n");
    }
}

static void swapNow(void) {
    measuringSwap = true;
    vglSwapBuffers(GL_FALSE);
    measuringSwap = false;
}

static void reportFinishedSwap(void) {
    VitaBootProgress_gamePresented();
    static unsigned renderedFrames;
    if (++renderedFrames == 90) {
        const double msPerFrame = 1e-6 / renderedFrames;
        logInfo("Vita present queue_ms=%.2f final_scene_ms=%.2f\n",
            displayQueueNanos * msPerFrame, finalSceneNanos * msPerFrame);
        renderedFrames = 0;
        displayQueueNanos = finalSceneNanos = 0;
    }
    captureVitaFrame();
}

void platformSwapBuffers(void) {
#ifdef ENABLE_VITA_ASYNC_PRESENT
    if (VitaPresent_queue(activeRunner != NULL ? activeRunner->frameCount : -1)) return;
#endif
    swapNow();
    reportFinishedSwap();
}

void* platformGetProcAddress(const char* name) {
    return vglGetProcAddress(name);
}

bool platformHandleEvents(void) {
    if (activeRunner != NULL) pollVitaGamepad(activeRunner->gamepads);
    return false;
}

void platformSleepUntil(uint64_t target) {
    uint64_t now = nowNanos();
    if (target > now + 500000) sceKernelDelayThread((target - now - 300000) / 1000);
    while (nowNanos() < target) {}
}
