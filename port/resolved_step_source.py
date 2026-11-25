"""Use the already-resolved Step handler for each frozen object bucket."""

from runtime_verification_source import replace_once


DISPATCH = r'''
enum { STEP_SNAPSHOT_GROUPS = 128 };

typedef struct {
    const SlotResponderEntry* responder;
    int32_t end;
} StepSnapshotGroup;

static unsigned stepSnapshotGroups, directStepEvents, changedStepEvents, stepSnapshotFallbacks;

static bool dispatchResolvedStep(Runner* runner, int32_t eventSubtype, int32_t slot) {
    uint32_t entryCount;
    const SlotResponderEntry* entries = ResolvedEventTable_slotEntries(&runner->eventTable, slot, &entryCount);
    StepSnapshotGroup groups[STEP_SNAPSHOT_GROUPS];
    unsigned groupCount = 0;
    const int32_t base = (int32_t)arrlen(runner->instanceSnapshots);
    int32_t end = base;

    for (uint32_t i = 0; i < entryCount; ++i) {
        const SlotResponderEntry* responder = &entries[i];
        Instance** bucket = runner->instancesByExactObject[responder->concreteObjectId];
        const int32_t count = (int32_t)arrlen(bucket);
        if (count == 0) continue;
        if (groupCount == STEP_SNAPSHOT_GROUPS || count > INT32_MAX - end) {
            Runner_popInstanceSnapshot(runner, base);
            ++stepSnapshotFallbacks;
            return false;
        }
        arrsetlen(runner->instanceSnapshots, end + count);
        memcpy(runner->instanceSnapshots + end, bucket, (size_t)count * sizeof(*bucket));
        end += count;
        groups[groupCount++] = (StepSnapshotGroup){responder, end};
    }

    stepSnapshotGroups += groupCount;
    int32_t cursor = base;
    for (unsigned group = 0; group < groupCount; ++group) {
        const SlotResponderEntry* responder = groups[group].responder;
        for (; cursor < groups[group].end; ++cursor) {
            // A callback may grow the arena. Use its live base for each read.
            Instance* instance = runner->instanceSnapshots[cursor];
            if (!instance->active) continue;
            if (instance->objectIndex != responder->concreteObjectId) {
                ++changedStepEvents;
                Runner_executeEvent(runner, instance, EVENT_STEP, eventSubtype);
                continue;
            }
            HLD_CHECK_RESOLVED_STEP
            ++directStepEvents;
            Runner_executeResolvedEvent(runner, instance, nullptr, EVENT_STEP, eventSubtype,
                                        responder->codeId, responder->ownerObjectId);
        }
    }
    Runner_popInstanceSnapshot(runner, base);
    return true;
}

void hldReportResolvedStep(int frame) {
    HLD_STEP_REPORT_GUARD
    logInfo("HLD resolved Step frame=%d groups=%u direct=%u changed=%u fallbacks=%u\n",
            frame, stepSnapshotGroups, directStepEvents, changedStepEvents, stepSnapshotFallbacks);
    stepSnapshotGroups = directStepEvents = changedStepEvents = stepSnapshotFallbacks = 0;
}
'''

CHECK = r'''
            int32_t owner = -1;
            const int32_t codeId = findEventCodeIdAndOwner(runner, instance->objectIndex,
                                                          EVENT_STEP, eventSubtype, &owner);
            require(codeId == responder->codeId && owner == responder->ownerObjectId);
'''.strip("\n")


def with_resolved_step(code, *, checked=False, windowed_report=False):
    report_guard = "if (frame <= 0 || frame % 90 != 0) return;" if windowed_report else ""
    dispatch = DISPATCH.replace("    HLD_STEP_REPORT_GUARD\n", f"    {report_guard}\n" if report_guard else "")
    dispatch = dispatch.replace("            HLD_CHECK_RESOLVED_STEP\n", CHECK + "\n" if checked else "")
    anchor = "void Runner_executeEventForAll(Runner* runner, int32_t eventType, int32_t eventSubtype) {"
    code = replace_once(code, anchor, dispatch + "\n" + anchor)
    anchor = """    if (slot == -1) return;

    // We always snapshot the iteration list before dispatching"""
    return replace_once(code, anchor, """    if (slot == -1) return;
    if (eventType == EVENT_STEP && dispatchResolvedStep(runner, eventSubtype, slot)) return;

    // We always snapshot the iteration list before dispatching""")
