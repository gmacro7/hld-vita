#include "gettime.h"

typedef struct {
    uint64_t elapsed;
    uint64_t self;
    uint64_t root;
    uint32_t calls;
#ifdef HLD_PROFILE_VM_DISPATCH
    uint64_t dispatch, builtins;
#endif
#ifdef HLD_PROFILE_NATIVE_ENTRY
    uint64_t nativeEntry;
    uint32_t nativeReturns;
#endif
} VMSampleTotal;

typedef struct {
    uint64_t start;
    uint64_t savedChildTime;
    bool root;
#ifdef HLD_PROFILE_VM_DISPATCH
    uint64_t savedBuiltinTime;
#endif
#ifdef HLD_PROFILE_NATIVE_ENTRY
    uint64_t nativeEntry;
    uint64_t nativeBuiltins;
    bool enteredInterpreter;
#endif
} VMSampleScope;

static VMSampleTotal* vmSampleTotals;
static int vmSampleLastFrame = -1;
static int vmSampleWindow = -1;
static unsigned vmSampleFrames;
static uint64_t vmSampleChildTime;
#ifdef HLD_PROFILE_NATIVE_ENTRY
static unsigned vmSampleDepth;
#endif
#ifdef HLD_PROFILE_VM_DISPATCH
static uint64_t vmSampleBuiltinTime;
#endif
#ifdef HLD_PROFILE_CURRENT_RUNTIME
enum SamplePhase { SAMPLE_STEP, SAMPLE_BEGIN, SAMPLE_END, SAMPLE_COLLISION, SAMPLE_DRAW, SAMPLE_OTHER, SAMPLE_PHASES };
static uint64_t vmSamplePhaseTime[SAMPLE_PHASES];

static enum SamplePhase vmSamplePhase(int32_t eventType, int32_t eventSubtype) {
    if (eventType == EVENT_STEP) {
        if (eventSubtype == STEP_BEGIN) return SAMPLE_BEGIN;
        if (eventSubtype == STEP_END) return SAMPLE_END;
        return SAMPLE_STEP;
    }
    if (eventType == EVENT_COLLISION) return SAMPLE_COLLISION;
    if (eventType == EVENT_DRAW) return SAMPLE_DRAW;
    return SAMPLE_OTHER;
}
#endif

static void reportVMSample(VMContext* ctx) {
    const uint32_t codeCount = ctx->dataWin->code.count;
    const double msPerFrame = 1e-6 / vmSampleFrames;
#ifdef HLD_PROFILE_CURRENT_RUNTIME
    logInfo("Vita VM roots frame=%d frames=%u normal_ms=%.3f begin_ms=%.3f end_ms=%.3f "
            "collision_ms=%.3f draw_ms=%.3f other_ms=%.3f\n", vmSampleLastFrame, vmSampleFrames,
            vmSamplePhaseTime[SAMPLE_STEP] * msPerFrame, vmSamplePhaseTime[SAMPLE_BEGIN] * msPerFrame,
            vmSamplePhaseTime[SAMPLE_END] * msPerFrame, vmSamplePhaseTime[SAMPLE_COLLISION] * msPerFrame,
            vmSamplePhaseTime[SAMPLE_DRAW] * msPerFrame, vmSamplePhaseTime[SAMPLE_OTHER] * msPerFrame);
    memset(vmSamplePhaseTime, 0, sizeof(vmSamplePhaseTime));
#endif
    for (unsigned rank = 0; rank < 24; rank++) {
        uint32_t best = 0;
        for (uint32_t index = 1; index < codeCount; index++) {
            if (vmSampleTotals[index].self > vmSampleTotals[best].self) best = index;
        }
        VMSampleTotal* total = &vmSampleTotals[best];
        if (total->self == 0) break;
        logInfo("Vita VM sample frame=%d frames=%u calls=%u self_ms=%.3f inclusive_ms=%.3f root_ms=%.3f code=%s\n",
                vmSampleLastFrame, vmSampleFrames, total->calls, total->self * msPerFrame,
                total->elapsed * msPerFrame, total->root * msPerFrame, ctx->dataWin->code.entries[best].name);
        total->self = 0;
    }
#ifdef HLD_PROFILE_VM_DISPATCH
    for (unsigned rank = 0; rank < 12; ++rank) {
        uint32_t best = 0;
        for (uint32_t index = 1; index < codeCount; ++index) {
            if (vmSampleTotals[index].dispatch > vmSampleTotals[best].dispatch) best = index;
        }
        VMSampleTotal* total = &vmSampleTotals[best];
        if (total->dispatch == 0) break;
        logInfo("Vita VM dispatch frame=%d frames=%u calls=%u dispatch_ms=%.3f builtin_ms=%.3f code=%s\n",
                vmSampleLastFrame, vmSampleFrames, total->calls, total->dispatch * msPerFrame,
                total->builtins * msPerFrame, ctx->dataWin->code.entries[best].name);
        total->dispatch = 0;
    }
#endif
#ifdef HLD_PROFILE_NATIVE_ENTRY
    for (unsigned rank = 0; rank < 12; ++rank) {
        uint32_t best = 0;
        for (uint32_t index = 1; index < codeCount; ++index) {
            if (vmSampleTotals[index].nativeEntry > vmSampleTotals[best].nativeEntry) best = index;
        }
        VMSampleTotal* total = &vmSampleTotals[best];
        if (total->nativeEntry == 0) break;
        logInfo("Vita VM native entry frame=%d frames=%u calls=%u returns=%u self_ms=%.3f code=%s\n",
                vmSampleLastFrame, vmSampleFrames, total->calls, total->nativeReturns,
                total->nativeEntry * msPerFrame, ctx->dataWin->code.entries[best].name);
        total->nativeEntry = 0;
    }
#endif
    memset(vmSampleTotals, 0, codeCount * sizeof(*vmSampleTotals));
    vmSampleFrames = 0;
}

static VMSampleScope beginVMSample(VMContext* ctx) {
    const int frame = ctx->runner != nullptr ? ctx->runner->frameCount : 0;
    if (frame < 550 || frame % 33 != 9) return (VMSampleScope){0};

    if (frame != vmSampleLastFrame) {
        const int window = (frame - 550) / 220;
        if (vmSampleTotals == nullptr) {
            vmSampleTotals = safeCalloc(ctx->dataWin->code.count, sizeof(*vmSampleTotals));
        } else if (window != vmSampleWindow && vmSampleFrames > 0) {
            reportVMSample(ctx);
        }
        vmSampleLastFrame = frame;
        vmSampleWindow = window;
        ++vmSampleFrames;
    }
    VMSampleScope scope = {
        .start = nowNanos(),
        .savedChildTime = vmSampleChildTime,
#ifdef HLD_PROFILE_NATIVE_ENTRY
        .root = vmSampleDepth++ == 0,
#else
        .root = ctx->currentCallFrame == nullptr,
#endif
    };
#ifdef HLD_PROFILE_VM_DISPATCH
    scope.savedBuiltinTime = vmSampleBuiltinTime;
    vmSampleBuiltinTime = 0;
#endif
    return scope;
}

#ifdef HLD_PROFILE_NATIVE_ENTRY
static void markVMSampleInterpreted(VMSampleScope* scope) {
    if (scope->start == 0) return;
    const uint64_t elapsed = nowNanos() - scope->start;
    const uint64_t children = vmSampleChildTime - scope->savedChildTime;
    scope->enteredInterpreter = true;
    scope->nativeEntry = elapsed > children ? elapsed - children : 0;
    scope->nativeBuiltins = vmSampleBuiltinTime;
}
#endif

static void endVMSample(VMSampleScope scope, int32_t codeIndex, int32_t eventType, int32_t eventSubtype) {
    if (scope.start == 0) return;
#ifdef HLD_PROFILE_NATIVE_ENTRY
    --vmSampleDepth;
#endif
    uint64_t elapsed = nowNanos() - scope.start;
    uint64_t children = vmSampleChildTime - scope.savedChildTime;
    VMSampleTotal* total = &vmSampleTotals[codeIndex];
    total->elapsed += elapsed;
    const uint64_t self = elapsed > children ? elapsed - children : 0;
    total->self += self;
#ifdef HLD_PROFILE_VM_DISPATCH
    uint64_t interpreted = self;
    uint64_t builtinTime = vmSampleBuiltinTime;
#ifdef HLD_PROFILE_NATIVE_ENTRY
    const uint64_t prefix = scope.enteredInterpreter && scope.nativeEntry < self ? scope.nativeEntry : self;
    total->nativeEntry += prefix;
    total->nativeReturns += !scope.enteredInterpreter;
    interpreted -= prefix;
    builtinTime -= scope.nativeBuiltins;
#endif
    const uint64_t builtins = builtinTime < interpreted ? builtinTime : interpreted;
    total->builtins += builtins;
    total->dispatch += interpreted - builtins;
    vmSampleBuiltinTime = scope.savedBuiltinTime;
#endif
    if (scope.root) {
        total->root += elapsed;
#ifdef HLD_PROFILE_CURRENT_RUNTIME
        vmSamplePhaseTime[vmSamplePhase(eventType, eventSubtype)] += elapsed;
#endif
    }
#ifndef HLD_PROFILE_CURRENT_RUNTIME
    (void)eventType;
    (void)eventSubtype;
#endif
    ++total->calls;
    vmSampleChildTime = scope.root ? 0 : scope.savedChildTime + elapsed;
}
