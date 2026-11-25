"""Skip the VitaGL mask reset draw when the next mask covers the whole target."""

from runtime_verification_source import replace_once


FULL_MASK_TRACKING = r'''
static unsigned fullViewportMasks, scissorMaskUpdates;

void hldVglReadScissorMasks(unsigned* full, unsigned* total) {
    *full = __atomic_exchange_n(&fullViewportMasks, 0, __ATOMIC_RELAXED);
    *total = __atomic_exchange_n(&scissorMaskUpdates, 0, __ATOMIC_RELAXED);
}

static inline GLboolean hldExactlyFullScissor(int x, int y, int width, int height,
                                             uint32_t targetWidth, uint32_t targetHeight) {
    return x == 0 && y == 0 && width > 0 && height > 0 &&
           (uint32_t)width == targetWidth && (uint32_t)height == targetHeight;
}
'''


def with_full_viewport_scissor(code):
    anchor = "void update_scissor_test() {"
    code = replace_once(code, anchor, FULL_MASK_TRACKING + "\n" + anchor)
    anchor = "\t// Converting openGL scissor test region to sceGxm one"
    code = replace_once(code, anchor, r'''    const GLboolean full = scissor_test_state &&
        hldExactlyFullScissor(region.gl_x, region.gl_y, region.gl_w, region.gl_h, active_w, active_h);
    __atomic_add_fetch(&scissorMaskUpdates, 1u, __ATOMIC_RELAXED);
    if (full) __atomic_add_fetch(&fullViewportMasks, 1u, __ATOMIC_RELAXED);

''' + anchor)
    before = "\t\tvoid *vertex_buffer;\n\t\tsceGxmReserveVertexDefaultUniformBuffer(gxm_context, &vertex_buffer);"
    start = code.index(before, code.index("void update_scissor_test() {"))
    end = code.index("\n\t}\n\n\t// Setting stencil surface mask update bit", start)
    # Keep the original rectangle vertices and mask-set draw. An exact full
    # rectangle sets every bit the first mask draw would have cleared.
    block = "\n".join("\t" + line if line else line for line in code[start:end].split("\n"))
    return code[:start] + "\t\tif (!full) {\n" + block + "\n\t\t}" + code[end:]


def with_scissor_mask_report(code):
    anchor = "            hldReportAudioUpdate(runner->frameCount);"
    return replace_once(code, anchor, anchor + r'''
            if (runner->frameCount > 0 && runner->frameCount % 90 == 0) {
                extern void hldVglReadScissorMasks(unsigned* full, unsigned* total);
                unsigned full, total;
                hldVglReadScissorMasks(&full, &total);
                logInfo("Vita scissor masks frame=%d full_saved=%u updates=%u\n",
                        runner->frameCount, full, total);
            }''')


def with_application_setup_profile(code):
    anchor = """    runner->applicationSurfaceId = renderer->vtable->ensureApplicationSurface(renderer, gameW, gameH);
    renderer->vtable->beginFrame(renderer, gameW, gameH, framebufferW, framebufferH);"""
    return replace_once(code, anchor, r'''    const uint64_t beforeEnsure = nowNanos();
    runner->applicationSurfaceId = renderer->vtable->ensureApplicationSurface(renderer, gameW, gameH);
    const uint64_t afterEnsure = nowNanos();
    renderer->vtable->beginFrame(renderer, gameW, gameH, framebufferW, framebufferH);
    const uint64_t afterBegin = nowNanos();
    if (afterBegin - beforeEnsure >= UINT64_C(80000000)) {
        logInfo("Vita app setup hitch frame=%d ensure_ms=%.2f begin_ms=%.2f\n", runner->frameCount,
                (afterEnsure - beforeEnsure) * 1e-6, (afterBegin - afterEnsure) * 1e-6);
    }''')


def with_application_clear_profile(code):
    anchor = '#include "vm_builtins.h"'
    if '#include "gettime.h"' not in code:
        code = replace_once(code, anchor, anchor + '\n#include "gettime.h"')
    anchor = """    GLCommon_beginFrame(gl, gameW, gameH, windowW, windowH);
    GLboolean scissor = glIsEnabled(GL_SCISSOR_TEST);
    glDisable(GL_SCISSOR_TEST);
    glClearDepthBuffer(renderer, 1.0f);
    if (scissor) glEnable(GL_SCISSOR_TEST);"""
    return replace_once(code, anchor, r'''    const uint64_t beforeBind = nowNanos();
    GLCommon_beginFrame(gl, gameW, gameH, windowW, windowH);
    const uint64_t afterBind = nowNanos();
    GLboolean scissor = glIsEnabled(GL_SCISSOR_TEST);
    glDisable(GL_SCISSOR_TEST);
    const uint64_t beforeClear = nowNanos();
    glClearDepthBuffer(renderer, 1.0f);
    const uint64_t afterClear = nowNanos();
    if (scissor) glEnable(GL_SCISSOR_TEST);
    const uint64_t afterRestore = nowNanos();
    if (afterRestore - beforeBind >= UINT64_C(80000000)) {
        logInfo("Vita app depth hitch frame=%d bind_ms=%.2f state_ms=%.2f clear_ms=%.2f restore_ms=%.2f\n",
                renderer->runner->frameCount, (afterBind - beforeBind) * 1e-6,
                (beforeClear - afterBind) * 1e-6, (afterClear - beforeClear) * 1e-6,
                (afterRestore - afterClear) * 1e-6);
    }''')
