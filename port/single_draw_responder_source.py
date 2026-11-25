"""Dispatch legacy draw subtypes directly when they have one live responder."""

from runtime_verification_source import replace_once


DISPATCH = r'''
static unsigned singleDrawChecks, singleDrawCalls, singleDrawSkipped, singleDrawResumes;

#ifdef HLD_CHECK_SINGLE_DRAW_RESPONDER
static void checkSingleDrawResponder(Runner* runner, Drawable* drawables, int32_t count,
                                     int32_t slot, Instance* expected) {
    Instance* found = nullptr;
    for (int32_t i = 0; i < count; ++i) {
        Drawable* drawable = &drawables[i];
        if (drawable->type != DRAWABLE_INSTANCE) continue;
        require(drawable->layerOrder == 0);
        Instance* instance = drawable->instance;
        if (!instance->active || !instance->visible) continue;
        if (ResolvedEventTable_lookup(&runner->eventTable, instance->objectIndex, slot, nullptr) < 0) continue;
        require(found == nullptr);
        found = instance;
    }
    require(found == expected);
}
#endif

static int32_t fireSingleDrawResponder(Runner* runner, Drawable* drawables, int32_t count,
                                       int32_t subtype, int32_t slot) {
    if (runner->drawableListStructureDirty || drawables != runner->cachedDrawables ||
        runner->instancesByExactObject == nullptr ||
        DataWin_isVersionAtLeast(runner->dataWin, 2, 0, 0, 0)) return 0;
    uint32_t responderCount;
    const SlotResponderEntry* responder = ResolvedEventTable_slotEntries(&runner->eventTable, slot, &responderCount);
    if (responderCount != 1) return 0;
    const int32_t object = responder->concreteObjectId;
    if ((uint32_t)object >= runner->dataWin->objt.count || responder->codeId < 0) return 0;
    Instance** bucket = runner->instancesByExactObject[object];
    if (arrlen(bucket) > 1) return 0;
    Instance* instance = arrlen(bucket) == 0 ? nullptr : bucket[0];
    if (instance != nullptr && (!instance->active || !instance->visible)) instance = nullptr;
#ifdef HLD_CHECK_SINGLE_DRAW_RESPONDER
    checkSingleDrawResponder(runner, drawables, count, slot, instance);
#endif
    ++singleDrawChecks;
    if (instance == nullptr) {
        singleDrawSkipped += (unsigned)count;
        return count;
    }

    int32_t previousShader = Runner_pushLayerShader(runner, instance->layer);
    Runner_executeResolvedEvent(runner, instance, nullptr, EVENT_DRAW, subtype, responder->codeId, responder->ownerObjectId);
    Runner_popLayerShader(runner, previousShader);
    ++singleDrawCalls;

    bucket = runner->instancesByExactObject[object];
    if (!runner->drawableListStructureDirty && arrlen(bucket) == 1 && bucket[0] == instance) {
        singleDrawSkipped += (unsigned)count;
        return count;
    }
    // Draw callbacks can mutate the live bucket. The original pass traverses
    // its existing drawable cache, so resume that pass after the dispatched item.
    for (int32_t i = 0; i < count; ++i) {
        if (drawables[i].type == DRAWABLE_INSTANCE && drawables[i].instance == instance) {
            ++singleDrawResumes;
            return i + 1;
        }
    }
    requireMessage(false, "Draw responder was absent from the current drawable cache");
    return count;
}

void hldReportSingleDrawResponder(int frame) {
    REPORT_GUARD
    logInfo("HLD single draw dispatch frame=%d checked=%u calls=%u skipped=%u resumed=%u\n",
            frame, singleDrawChecks, singleDrawCalls, singleDrawSkipped, singleDrawResumes);
    singleDrawChecks = singleDrawCalls = singleDrawSkipped = singleDrawResumes = 0;
}
'''


def with_single_draw_responder(code, *, checked=False, windowed_report=False):
    anchor = "static void fireDrawSubtype(Runner* runner, Drawable* drawables, int32_t drawableCount, int32_t subtype) {"
    define = "#define HLD_CHECK_SINGLE_DRAW_RESPONDER\n" if checked else ""
    guard = "if (frame <= 0 || frame % 90 != 0) return;" if windowed_report else ""
    code = replace_once(code, anchor, define + DISPATCH.replace("REPORT_GUARD", guard) + "\n" + anchor)
    prefix, dispatch = code.split(anchor, 1)
    before = "    repeat(drawableCount, i) {"
    after = ("    int32_t start = fireSingleDrawResponder(runner, drawables, drawableCount, subtype, slot);\n"
             "    for (int32_t i = start; i < drawableCount; ++i) {")
    dispatch_start, tail = dispatch.split("\n}\n", 1)
    dispatch_start = replace_once(dispatch_start, before, after)
    return prefix + anchor + dispatch_start + "\n}\n" + tail
