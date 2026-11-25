"""Fold the application frame's overlapping depth clears into its color clear."""

from runtime_verification_source import replace_once


STATE = r'''
typedef enum {
    FRAME_DEPTH_IDLE,
    FRAME_DEPTH_EXPECT_BEGIN,
    FRAME_DEPTH_EXPECT_COLOR_CLEAR,
    FRAME_DEPTH_READY,
} FrameDepthState;

static const Renderer* frameDepthOwner;
static FrameDepthState frameDepthState;
static unsigned frameDepthSkipped, firstViewDepthSkipped, frameDepthRestored;

void hldPrepareFrameDepthFold(Renderer* renderer) {
    frameDepthOwner = renderer;
    frameDepthState = FRAME_DEPTH_EXPECT_BEGIN;
}

static bool deferApplicationDepthClear(Renderer* renderer, bool scissorEnabled) {
    if (frameDepthOwner != renderer || frameDepthState != FRAME_DEPTH_EXPECT_BEGIN) return false;
    frameDepthState = scissorEnabled ? FRAME_DEPTH_IDLE : FRAME_DEPTH_EXPECT_COLOR_CLEAR;
    return !scissorEnabled;
}

static void restoreDeferredApplicationDepth(Renderer* renderer) {
    if (frameDepthOwner != renderer) return;
    if (frameDepthState == FRAME_DEPTH_EXPECT_COLOR_CLEAR) {
        const GLboolean scissor = glIsEnabled(GL_SCISSOR_TEST);
        if (scissor) glDisable(GL_SCISSOR_TEST);
        glClearDepthBuffer(renderer, 1.0f);
        if (scissor) glEnable(GL_SCISSOR_TEST);
        ++frameDepthRestored;
    }
    frameDepthState = FRAME_DEPTH_IDLE;
}

static bool mergeApplicationDepthIntoColorClear(Renderer* renderer) {
    if (frameDepthOwner != renderer || frameDepthState != FRAME_DEPTH_EXPECT_COLOR_CLEAR) return false;
    if (glIsEnabled(GL_SCISSOR_TEST)) {
        restoreDeferredApplicationDepth(renderer);
        return false;
    }
    frameDepthState = FRAME_DEPTH_READY;
    ++frameDepthSkipped;
    return true;
}

static bool useApplicationDepthForFirstView(Renderer* renderer) {
    if (frameDepthOwner != renderer || frameDepthState != FRAME_DEPTH_READY) return false;
    frameDepthState = FRAME_DEPTH_IDLE;
    ++firstViewDepthSkipped;
    return true;
}

void hldReportFrameDepthFold(int frame) {
    REPORT_GUARD
    logInfo("HLD application depth fold frame=%d frame_skips=%u first_view_skips=%u restored=%u\n",
            frame, frameDepthSkipped, firstViewDepthSkipped, frameDepthRestored);
    frameDepthSkipped = firstViewDepthSkipped = frameDepthRestored = 0;
}
'''


def with_frame_depth_fold_renderer(code, *, windowed_report=False):
    guard = "if (frame <= 0 || frame % 90 != 0) return;" if windowed_report else ""
    anchor = "static void glBeginFrame(Renderer* renderer, int32_t gameW, int32_t gameH, int32_t windowW, int32_t windowH) {"
    code = replace_once(code, anchor, STATE.replace("REPORT_GUARD", guard) + "\n" + anchor)
    anchor = "    GLCommon_beginFrame(gl, gameW, gameH, windowW, windowH);\n    GLboolean scissor = glIsEnabled(GL_SCISSOR_TEST);"
    code = replace_once(code, anchor, anchor + "\n    if (deferApplicationDepthClear(renderer, scissor)) return;")
    anchor = "    VMBuiltins_applyD3DDefaultView(renderer, viewX, viewY, viewW, viewH, viewAngle);\n    glClearDepthBuffer(renderer, 1.0f);"
    code = replace_once(code, anchor,
                        "    VMBuiltins_applyD3DDefaultView(renderer, viewX, viewY, viewW, viewH, viewAngle);\n"
                        "    if (!useApplicationDepthForFirstView(renderer)) glClearDepthBuffer(renderer, 1.0f);")
    anchor = "static void glClearScreen(Renderer* renderer, uint32_t color, float alpha) {"
    code = replace_once(code, anchor, anchor + "\n    const bool foldedDepth = mergeApplicationDepthIntoColorClear(renderer);\n"
                        "    (void)foldedDepth;")
    anchor = "    if (clearsToTransparent && (modernGl->clearedSurfaces & bit)) {"
    code = replace_once(code, anchor, anchor + r'''
        if (foldedDepth) {
            // The color clear is already redundant. Keep the original explicit depth clear.
            glClearDepthBuffer(renderer, 1.0f);
            --frameDepthSkipped;
            ++frameDepthRestored;
        }''')
    for anchor in (
        "static bool glSetRenderTarget(Renderer* renderer, int32_t surfaceId, bool implicitApplicationSurface) {",
        "static void glBeginGUI(Renderer* renderer, int32_t guiW, int32_t guiH, int32_t portX, int32_t portY, int32_t portW, int32_t portH, int32_t targetSurfaceId) {",
    ):
        code = replace_once(code, anchor, anchor + "\n    restoreDeferredApplicationDepth(renderer);")
    return code


def with_frame_depth_fold_loop(code):
    # The app surface is bound in Runner_beginFrame. The only step before
    # the matching initial color/depth clear is the CPU mouse-coordinate update.
    anchor = "                Runner_beginFrame(runner, gameW, gameH, winW, winH, fbWidth, fbHeight);"
    return replace_once(code, anchor,
                        "                extern void hldPrepareFrameDepthFold(Renderer* renderer);\n"
                        "                hldPrepareFrameDepthFold(renderer);\n" + anchor)
