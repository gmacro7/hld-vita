"""Profiling builds only (HLDC_CHURN_PROFILE=1): time instance churn and the draw-list rebuild.

Every 90 frames the log gets one line with per-frame averages:
  creates, alloc_us (createAndInitInstance), layer_us (depth layer move), events_us (pre-create + create events),
  destroys / destroy_us (Runner_destroyInstance, destroy events included), cleanup_us
  (Runner_cleanupDestroyedInstances), drawlist_us (rebuildDrawableCacheIfDirty).
Normal builds are unchanged.
"""

from runtime_verification_source import replace_once

COUNTERS = r'''
uint64_t nowNanos(void);
static struct {
    uint64_t alloc, layer, events, destroy, cleanup, drawlist;
    unsigned creates, destroys, rebuilds;
} hldChurn;

void hldReportChurn(int frame) {
    if (frame < 90 || frame % 90 != 0) return;
    const double us = 1e-3 / 90.0;
    logInfo("HLD churn frame=%d creates=%.1f alloc_us=%.0f layer_us=%.0f events_us=%.0f destroys=%.1f destroy_us=%.0f "
            "cleanup_us=%.0f drawlist_us=%.0f rebuilds=%u\n",
            frame, hldChurn.creates / 90.0, hldChurn.alloc * us, hldChurn.layer * us, hldChurn.events * us,
            hldChurn.destroys / 90.0, hldChurn.destroy * us, hldChurn.cleanup * us, hldChurn.drawlist * us,
            hldChurn.rebuilds);
    memset(&hldChurn, 0, sizeof(hldChurn));
}
'''

CREATE_OLD = '''    Instance* inst = createAndInitInstance(runner, runner->nextInstanceId++, objectIndex, x, y);
    Runner_moveInstanceToDepthLayer(runner, inst, inst->depth);
    dispatchInstanceCreationEvents(runner, inst);
    return inst;
}'''

CREATE_NEW = '''    const uint64_t hldT0 = nowNanos();
    Instance* inst = createAndInitInstance(runner, runner->nextInstanceId++, objectIndex, x, y);
    const uint64_t hldT1 = nowNanos();
    Runner_moveInstanceToDepthLayer(runner, inst, inst->depth);
    const uint64_t hldT2 = nowNanos();
    dispatchInstanceCreationEvents(runner, inst);
    const uint64_t hldT3 = nowNanos();
    hldChurn.alloc += hldT1 - hldT0;
    hldChurn.layer += hldT2 - hldT1;
    hldChurn.events += hldT3 - hldT2;
    ++hldChurn.creates;
    return inst;
}'''


def _wrap(code, signature, inner, call, counter, count=None):
    """Rename a function and add a same-signature wrapper right after it that times the original."""
    start = code.index(signature)
    end = code.index("\n}\n", start) + 3
    renamed = code[start:end].replace(signature, signature.replace(signature.split("(")[0].split()[-1], inner), 1)
    returns = signature.split()[0] if not signature.startswith("static") else signature.split()[1]
    wrapper = (signature + "\n    const uint64_t hldT0 = nowNanos();\n    " + call + "\n"
               f"    hldChurn.{counter} += nowNanos() - hldT0;\n" + (f"    ++hldChurn.{count};\n" if count else "") + "}\n")
    if signature.startswith("static"):
        renamed = renamed  # stays static
    else:
        renamed = "static " + renamed if not renamed.startswith("static") else renamed
    assert returns == "void"
    return code[:start] + renamed + wrapper + code[end:]


def with_churn_profile(code):
    first = code.index("static void rebuildDrawableCacheIfDirty(Runner* runner) {")
    code = code[:first] + COUNTERS + "\n" + code[first:]
    code = replace_once(code, CREATE_OLD, CREATE_NEW)
    code = _wrap(code, "static void rebuildDrawableCacheIfDirty(Runner* runner) {", "hldRebuildDrawableCacheInner",
                 "hldRebuildDrawableCacheInner(runner);", "drawlist", "rebuilds")
    code = _wrap(code, "void Runner_destroyInstance(MAYBE_UNUSED Runner* runner, Instance* inst, bool runDestroyEvent) {",
                 "hldDestroyInstanceInner", "hldDestroyInstanceInner(runner, inst, runDestroyEvent);", "destroy",
                 "destroys")
    code = _wrap(code, "void Runner_cleanupDestroyedInstances(Runner* runner) {", "hldCleanupDestroyedInner",
                 "hldCleanupDestroyedInner(runner);", "cleanup")
    return code


def with_churn_report(code):
    anchor = "            hldReportVitaGraphics(runner->frameCount);"
    return replace_once(code, anchor, anchor + """
            extern void hldReportChurn(int frame);
            hldReportChurn(runner->frameCount);""")
