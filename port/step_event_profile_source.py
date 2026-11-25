"""Sample root event costs without modifying the trained interpreter."""

from runtime_verification_source import replace_once


PROFILE = r'''
enum { STEP_EVENT_PHASES = 3, STEP_EVENT_REPORT_LIMIT = 8 };

typedef struct {
    uint64_t nanos;
    unsigned calls;
} StepEventCost;

typedef struct {
    uint64_t nanos, eventNanos;
    unsigned samples, calls;
} StepPhaseCost;

static Runner* stepEventProfileOwner;
static StepEventCost* stepEventCosts;
static StepPhaseCost stepPhaseCosts[STEP_EVENT_PHASES];
static bool stepEventProfileChecked, stepEventProfileEnabled, stepEventTimingRoot;
static StepEventCost* stepEventActiveCosts;
static StepPhaseCost* stepPhaseActiveCost;

static bool shouldProfileStepEvents(int frame) {
    return frame >= 541 && frame <= 720 && frame % 15 == 1;
}

static void profileStepDispatch(Runner* runner, int32_t subtype, unsigned phase) {
    if (!stepEventProfileChecked) {
        FILE* marker = fopen("ux0:data/hld-vm/sample-step-events", "r");
        stepEventProfileEnabled = marker != nullptr;
        stepEventProfileChecked = true;
        if (marker != nullptr) fclose(marker);
    }
    if (!stepEventProfileEnabled || !shouldProfileStepEvents(runner->frameCount + 1)) {
        Runner_executeEventForAll(runner, EVENT_STEP, subtype);
        return;
    }
    const uint32_t count = runner->dataWin->code.count;
    if (stepEventProfileOwner == nullptr) {
        stepEventProfileOwner = runner;
        stepEventCosts = safeCalloc((size_t)count * STEP_EVENT_PHASES, sizeof(*stepEventCosts));
    }
    if (stepEventProfileOwner != runner) {
        Runner_executeEventForAll(runner, EVENT_STEP, subtype);
        return;
    }

    StepPhaseCost* costs = &stepPhaseCosts[phase];
    stepPhaseActiveCost = costs;
    stepEventActiveCosts = stepEventCosts + (size_t)phase * count;
    const uint64_t start = nowNanos();
    Runner_executeEventForAll(runner, EVENT_STEP, subtype);
    costs->nanos += nowNanos() - start;
    ++costs->samples;
    stepEventActiveCosts = nullptr;
    stepPhaseActiveCost = nullptr;
}

void hldReportStepEventCosts(Runner* runner) {
    const int frame = runner->frameCount;
    if (stepEventProfileOwner != runner || (frame != 630 && frame != 720)) return;
    const uint32_t count = runner->dataWin->code.count;
    static const char* const names[STEP_EVENT_PHASES] = {"begin", "normal", "end"};
    for (unsigned phase = 0; phase < STEP_EVENT_PHASES; ++phase) {
        StepPhaseCost* costs = &stepPhaseCosts[phase];
        if (costs->samples == 0) continue;
        const double scale = 1e-6 / costs->samples;
        logInfo("HLD Step cost frame=%d phase=%s samples=%u calls=%u phase_ms=%.3f events_ms=%.3f\n",
                frame, names[phase], costs->samples, costs->calls, costs->nanos * scale, costs->eventNanos * scale);
        StepEventCost* events = stepEventCosts + (size_t)phase * count;
        for (unsigned row = 0; row < STEP_EVENT_REPORT_LIMIT; ++row) {
            uint32_t best = count;
            for (uint32_t code = 0; code < count; ++code) {
                if (events[code].calls != 0 && (best == count || events[code].nanos > events[best].nanos))
                    best = code;
            }
            if (best == count) break;
            logInfo("HLD Step event frame=%d phase=%s calls=%u mean_ms=%.3f code=%s\n",
                    frame, names[phase], events[best].calls, events[best].nanos * scale,
                    runner->dataWin->code.entries[best].name);
            events[best].calls = 0;
        }
        memset(events, 0, (size_t)count * sizeof(*events));
        *costs = (StepPhaseCost){0};
    }
}

static void releaseStepEventProfile(Runner* runner) {
    if (stepEventProfileOwner != runner) return;
    free(stepEventCosts);
    stepEventCosts = nullptr;
    stepEventProfileOwner = nullptr;
    memset(stepPhaseCosts, 0, sizeof(stepPhaseCosts));
}
'''


def with_step_event_profile(code):
    profile = PROFILE
    if __import__("os").environ.get("HLDC_STEP_SAMPLE_ALL") == "1":
        # Profiling builds: sample every third frame for the whole run and report every 90 frames (lined up with the
        # "Vita timing" rows), so short spikes such as boss barrages are caught wherever they happen.
        profile = replace_once(profile, "return frame >= 541 && frame <= 720 && frame % 15 == 1;",
                               "return frame >= 180 && frame % 3 == 1;")
        profile = replace_once(profile, "(frame != 630 && frame != 720)", "(frame < 270 || frame % 90 != 0)")
    start = code.index("static void Runner_executeResolvedEvent(")
    end = code.index("\nvoid Runner_executeEventFromObject(", start)
    event = code[start:end]
    anchor = "    VM_executeCodeIgnoreInvalidCodeIndices("
    event = replace_once(event, anchor, r'''    const bool sampleRoot = runner == stepEventProfileOwner && stepEventActiveCosts != nullptr &&
        !stepEventTimingRoot && (uint32_t)codeId < runner->dataWin->code.count;
    uint64_t eventStart = 0;
    if (sampleRoot) {
        stepEventTimingRoot = true;
        eventStart = nowNanos();
    }

''' + anchor)
    anchor = "    vm->actionRelativeFlag = savedActionRelativeFlag;"
    event = replace_once(event, anchor, r'''    if (sampleRoot) {
        const uint64_t elapsed = nowNanos() - eventStart;
        stepEventTimingRoot = false;
        StepEventCost* cost = &stepEventActiveCosts[codeId];
        ++cost->calls;
        cost->nanos += elapsed;
        ++stepPhaseActiveCost->calls;
        stepPhaseActiveCost->eventNanos += elapsed;
    }

''' + anchor)
    code = code[:start] + profile + "\n" + event + code[end:]
    for phase, subtype in enumerate(("STEP_BEGIN", "STEP_NORMAL", "STEP_END")):
        code = replace_once(code, f"    Runner_executeEventForAll(runner, EVENT_STEP, {subtype});",
                            f"    profileStepDispatch(runner, {subtype}, {phase});")
    anchor = "void Runner_free(Runner* runner) {"
    return replace_once(code, anchor, anchor + "\n    releaseStepEventProfile(runner);")


def with_step_event_report(code):
    anchor = "            hldReportVitaGraphics(runner->frameCount);"
    return replace_once(code, anchor, anchor + """
            extern void hldReportStepEventCosts(Runner*);
            hldReportStepEventCosts(runner);""")
