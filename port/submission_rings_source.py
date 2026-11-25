"""Compare GXM command ring capacity and observe existing fragment completions."""

def replace_once(code, before, after):
    assert code.count(before) == 1, before
    return code.replace(before, after)


def with_submission_rings(code):
    anchor = "    vglSetParamBufferSize(6 * 1024 * 1024);"
    return replace_once(code, anchor, anchor + r'''
    SceUID ringFlag = sceIoOpen("ux0:data/hld-vm/large-gxm-rings", SCE_O_RDONLY, 0);
    bool largerRings = ringFlag >= 0;
    if (largerRings) {
        sceIoClose(ringFlag);
        vglSetVDMBufferSize(2 * SCE_GXM_DEFAULT_VDM_RING_BUFFER_SIZE);
        vglSetVertexBufferSize(2 * SCE_GXM_DEFAULT_VERTEX_RING_BUFFER_SIZE);
        vglSetFragmentBufferSize(2 * SCE_GXM_DEFAULT_FRAGMENT_RING_BUFFER_SIZE);
    }
    logInfo("Vita GXM larger command rings=%d\n", largerRings);''')


def with_submission_trace(code):
    code = replace_once(code, "#include <stdio.h>", "#include <stdio.h>\n#include <string.h>")
    anchor = "static uint64_t finalSceneNanos;"
    code = replace_once(code, anchor, anchor + r'''

static struct {
    unsigned samples;
    unsigned priorPending;
    unsigned priorDrained;
    unsigned finalDone;
    uint64_t pendingNanos;
    uint64_t readyNanos;
} fragmentProgress;''')
    original = '''    uint64_t start = measuringSwap ? nowNanos() : 0;
    int result = __real_sceGxmEndScene(context, vertex, fragment);
    if (measuringSwap) finalSceneNanos += nowNanos() - start;
    return result;'''
    code = replace_once(code, original, '''    if (!measuringSwap) return __real_sceGxmEndScene(context, vertex, fragment);

    // VitaGL increments the sequence once per scene, immediately before this call.
    const volatile uint32_t* completion = fragment == NULL ? NULL : fragment->address;
    const uint32_t requested = completion == NULL ? 0 : fragment->value;
    const uint32_t prior = requested - 1;
    const bool priorPending = completion != NULL && *completion != prior;
    uint64_t start = nowNanos();
    int result = __real_sceGxmEndScene(context, vertex, fragment);
    const uint64_t elapsed = nowNanos() - start;
    finalSceneNanos += elapsed;

    if (completion != NULL && result >= 0) {
        const uint32_t observed = *completion;
        ++fragmentProgress.samples;
        fragmentProgress.finalDone += observed == requested;
        if (priorPending) {
            ++fragmentProgress.priorPending;
            fragmentProgress.priorDrained += observed == prior || observed == requested;
            fragmentProgress.pendingNanos += elapsed;
        } else {
            fragmentProgress.readyNanos += elapsed;
        }
    }
    return result;''')
    anchor = "        renderedFrames = 0;"
    return replace_once(code, anchor, r'''        logInfo("Vita GXM fragments frame=%d samples=%u prior_pending=%u prior_drained=%u "
            "final_done=%u pending_ms=%.3f ready_ms=%.3f\n", swapFrame, fragmentProgress.samples,
            fragmentProgress.priorPending, fragmentProgress.priorDrained, fragmentProgress.finalDone,
            fragmentProgress.pendingNanos * msPerFrame, fragmentProgress.readyNanos * msPerFrame);
        memset(&fragmentProgress, 0, sizeof fragmentProgress);
''' + anchor)
