"""Use the initialized depth attachment on successfully opened offscreen scenes."""

from runtime_verification_source import replace_once


DRIVER_STATE = r'''
extern uint32_t hldOffscreenSceneSerial;
static unsigned initialDepthOnly, initialDepthWithColor;
static GLboolean traceNextDepthClear;
static uint64_t tracedDepthResetMicros, tracedDepthDrawMicros;

void hldVglReadInitialDepthClears(unsigned* depthOnly, unsigned* withColor) {
    *depthOnly = __atomic_exchange_n(&initialDepthOnly, 0, __ATOMIC_RELAXED);
    *withColor = __atomic_exchange_n(&initialDepthWithColor, 0, __ATOMIC_RELAXED);
}

void hldVglTraceNextDepthClear(void) {
    traceNextDepthClear = GL_TRUE;
    tracedDepthResetMicros = tracedDepthDrawMicros = 0;
}

void hldVglReadDepthClearTrace(uint64_t* reset, uint64_t* draw) {
    *reset = tracedDepthResetMicros;
    *draw = tracedDepthDrawMicros;
}

static GLboolean canUseInitialDepth(const framebuffer* write, uint32_t previousScene) {
#ifdef DEPTH_STENCIL_HACK
    const GLdouble requested = 1.0;
    const float initialized = 1.0f;
    if (!write || write != active_write_fb || write != in_use_framebuffer ||
        previousScene == hldOffscreenSceneSerial || !write->depthbuffer_ptr ||
        scissor_test_state || !depth_mask_state || memcmp(&depth_value, &requested, sizeof(requested)) != 0)
        return GL_FALSE;
    const SceGxmDepthStencilSurface* depth = write->depthbuffer_ptr;
    return depth->depthData == NULL && memcmp(&depth->backgroundDepth, &initialized, sizeof(initialized)) == 0 &&
        sceGxmDepthStencilSurfaceGetForceLoadMode(depth) == SCE_GXM_DEPTH_STENCIL_FORCE_LOAD_DISABLED;
#else
    return GL_FALSE;
#endif
}
'''


def with_initial_depth_scene_generation(code):
    anchor = "void scene_reset(void) {"
    code = replace_once(code, anchor, "uint32_t hldOffscreenSceneSerial;\n\n" + anchor)
    anchor = "\t\t\t\t\t&active_write_fb->colorbuffer,\n\t\t\t\t\tactive_write_fb->depthbuffer_ptr);"
    return replace_once(code, anchor, anchor + "\n\t\t\tif (r == 0) ++hldOffscreenSceneSerial;")


def with_initial_depth_clear(code):
    anchor = "void glClear(GLbitfield mask) {"
    code = replace_once(code, anchor, DRIVER_STATE + "\n" + anchor)
    anchor = "\tscene_reset();\n\tdrawClear(mask, &clear_rgba_val);\n}"
    return replace_once(code, anchor, r'''    const framebuffer* write = active_write_fb;
    const uint32_t previousScene = hldOffscreenSceneSerial;
    const GLboolean trace = traceNextDepthClear;
    traceNextDepthClear = GL_FALSE;
    const uint64_t beforeReset = trace ? sceKernelGetProcessTimeWide() : 0;
    scene_reset();
    const uint64_t afterReset = trace ? sceKernelGetProcessTimeWide() : 0;
    const GLboolean safeMask = mask == GL_DEPTH_BUFFER_BIT || mask == (GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    const GLboolean useInitial = safeMask && canUseInitialDepth(write, previousScene);
    if (useInitial) {
        mask &= ~GL_DEPTH_BUFFER_BIT;
        unsigned* skipped = mask == 0 ? &initialDepthOnly : &initialDepthWithColor;
        __atomic_add_fetch(skipped, 1u, __ATOMIC_RELAXED);
    }
    if (!useInitial || mask != 0) drawClear(mask, &clear_rgba_val);
    if (trace) {
        tracedDepthResetMicros = afterReset - beforeReset;
        tracedDepthDrawMicros = sceKernelGetProcessTimeWide() - afterReset;
    }
}''')


def with_initial_depth_profile_hook(code):
    anchor = """    const uint64_t beforeClear = nowNanos();
    glClearDepthBuffer(renderer, 1.0f);"""
    code = replace_once(code, anchor, """    extern void hldVglTraceNextDepthClear(void);
    hldVglTraceNextDepthClear();
""" + anchor)
    anchor = "                (afterRestore - afterClear) * 1e-6);"
    return replace_once(code, anchor, anchor + r'''
        extern void hldVglReadDepthClearTrace(uint64_t* reset, uint64_t* draw);
        uint64_t resetMicros, drawMicros;
        hldVglReadDepthClearTrace(&resetMicros, &drawMicros);
        logInfo("Vita depth clear driver hitch frame=%d reset_ms=%.2f draw_ms=%.2f\n",
                renderer->runner->frameCount, resetMicros * 0.001, drawMicros * 0.001);''')


def with_initial_depth_report(code):
    anchor = "            hldReportAudioUpdate(runner->frameCount);"
    return replace_once(code, anchor, anchor + r'''
            if (runner->frameCount > 0 && runner->frameCount % 90 == 0) {
                extern void hldVglReadInitialDepthClears(unsigned* depthOnly, unsigned* withColor);
                unsigned depthOnly, withColor;
                hldVglReadInitialDepthClears(&depthOnly, &withColor);
                logInfo("Vita initial scene depth frame=%d depth_only=%u with_color=%u\n",
                        runner->frameCount, depthOnly, withColor);
            }''')
