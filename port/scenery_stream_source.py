"""Process settled scenery in the SCREEN snapshot without changing VM context."""

from idle_scenery_source import IDLE_SCENERY
from runtime_verification_source import replace_once


STREAM = r'''
static unsigned sceneryStreamSkips, sceneryStreamCalls;
static const VMContext* sceneryStreamOwner;

static void bindSceneryStream(VMContext* ctx) {
    if (idleScenery.context != ctx || sceneryPass.context != ctx || sceneryDrawLists.context != ctx ||
        ctx->hldSceneryStepCode == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, ctx->hldSceneryStepCode);
    const OpWord call = {BinaryUtils_readUint32Aligned(bytes + SCENERY_NORMAL_CALL)};
    const uint32_t function = resolveFuncOperand(bytes + SCENERY_NORMAL_CALL + 4);
    if (OpWord_opcode(call) != OP_CALL || OpWord_extra(call) != 0 ||
        BinaryUtils_readUint32Aligned(bytes + SCENERY_NORMAL_CALL + 8) != 0x9e050000u ||
        function >= ctx->funcCallCacheCount || ctx->funcCallCache[function].builtin != nullptr ||
        ctx->funcCallCache[function].scriptCodeIndex != idleScenery.code - ctx->dataWin->code.entries) return;
    // A native list append must never invalidate the saved skip group or
    // change the shutdown/editor guards used by the next entry.
    for (unsigned i = 0; i < SCENERY_LIST_COUNT; ++i) {
        if (sceneryDrawLists.index[i] == 4 || sceneryDrawLists.variable[i] == ctx->hldRoomTimeVarId ||
            sceneryDrawLists.variable[i] == idleScenery.canEdit) return;
    }
    sceneryStreamOwner = ctx;
    logInfo("HLD: bound the settled scenery stream\n");
}

static void streamSettledScenery(VMContext* ctx, unsigned* savedGroup, const uint8_t* bytes) {
    if (*savedGroup == SCENERY_SKIP_REFRESH) {
        *savedGroup = scenerySkipGroup(ctx);
        ++scenerySkipGuardReads;
    } else {
        ++scenerySkipGuardReuses;
    }
    // scenerySkipGroup honors the integrated Vita refinement switch;
    // the comparison build can run this path when that switch is on.
    const unsigned selected = *savedGroup;
    if (selected == SCENERY_SKIP_NO_GROUP) return;
    // The original with block owns the snapshot. Keep its order, and
    // stop before an entry that can change the room or dispatch script.
    EnvFrame* env = ctx->envStack;
    while (env->currentIndex + 1 < env->instanceCount) {
        Instance* next = env->instanceList[env->currentIndex + 1];
        if (!next->active) {
            ++env->currentIndex;
            continue;
        }
        if (next->instanceId >= (1u << 24) || next->instanceId % 4 == selected ||
            next->objectIndex == 36 || next->objectIndex == STRUCT_OBJECT_INDEX) return;
        const RValue inactive = Instance_getSelfVar(next, ctx->hldSceneryInactiveVarId);
        if ((inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32) ||
            !isfinite(RValue_toReal(inactive))) return;
        SCENERY_STREAM_CHECK
        if (RValue_toBool(inactive)) {
            // scenerySkipGroup limits epsilon to less than 0.5, so a
            // true numeric inactivity flag also passes the zero test.
            ++sceneryCursorSkipped;
            ++skippedSceneryBeforeSwitch;
            ++sceneryStreamSkips;
        } else {
            if (!sceneryHasNoUpdate(ctx, next)) return;
            // The original path completes the no-op call and appends one
            // element, if eligible. The append has no effect on failure.
            if (!appendSceneryDrawListFor(ctx, next, inactive)) return;
            SCENERY_STREAM_CALL
            ++nativeSceneryCalls;
            ++nativeSceneryLists;
            ++sceneryStreamCalls;
        }
        ++env->currentIndex;
        ++sceneryCursorEntries;
    }
}
'''


CHECKED_GUARD = r'''
        CallFrame* frame = ctx->currentCallFrame;
        Instance* previous = frame->self;
        frame->self = next;
        require(sceneryChanges(ctx, next).decision == SCENERY_UNCHANGED);
        require(hldSceneryCanSkipUpdate(ctx) == RValue_toBool(inactive));
        frame->self = previous;
'''


CHECKED_CALL = r'''
            CallFrame* frame = ctx->currentCallFrame;
            Instance* previous = frame->self;
            const uint32_t continuation = ctx->ip;
            frame->self = next;
            ctx->ip = SCENERY_NORMAL_CALL + 8;
            handleCall(ctx, (OpWord){BinaryUtils_readUint32Aligned(bytes + SCENERY_NORMAL_CALL)},
                       bytes + SCENERY_NORMAL_CALL + 4, true);
            frame->self = previous;
            ctx->ip = continuation;
'''

DIRECT_CALL = r'''
            ctx->savearefBalance = 0;
            ctx->bytecodeBase = codeBytes(ctx, idleScenery.code);
            ++idleSceneryCalls;
'''


def with_scenery_stream(code, *, checked=False, launch_flag=None):
    helper = STREAM.replace("            SCENERY_STREAM_CALL\n", CHECKED_CALL if checked else DIRECT_CALL)
    helper = helper.replace("        SCENERY_STREAM_CHECK\n", CHECKED_GUARD if checked else "")
    if not checked:
        helper = helper.replace(", const uint8_t* bytes) {", ") {")
    guards = "sceneryStreamOwner == ctx"
    if launch_flag:
        helper = "extern bool hldIdleScenery;\n" + helper
        guards += f" && hldIdleScenery && hldScenerySkip && hldNativeSceneryBody && {launch_flag}"
        helper += f'''\nextern bool {launch_flag};
void hldReportSceneryStream(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery stream frame=%d enabled=%d skipped=%u calls=%u\\n",
            frame, (int){launch_flag}, sceneryStreamSkips, sceneryStreamCalls);
    sceneryStreamSkips = sceneryStreamCalls = 0;
}}
'''
    else:
        helper += r'''
void hldReportSceneryStreamAtExit(void) {
    logInfo("HLD: scenery stream skipped=%u calls=%u\n", sceneryStreamSkips, sceneryStreamCalls);
}
'''
    # The native append only needs its current local variables, the target
    # instance, and the already-read inactivity value.
    start = code.index("static bool appendSceneryDrawList(VMContext* ctx) {")
    end = code.index("\n}\n", start) + len("\n}\n")
    original = code[start:end]
    append = original.replace("static bool appendSceneryDrawList(VMContext* ctx) {",
                              "static bool appendSceneryDrawListFor(VMContext* ctx, Instance* self, RValue inactive) {")
    append = replace_once(append, "    Instance* self = frame->self;\n", "")
    append = replace_once(append, "    const RValue inactive = Instance_getSelfVar(self, ctx->hldSceneryInactiveVarId);\n", "")
    append += '''
static bool appendSceneryDrawList(VMContext* ctx) {
    Instance* self = ctx->currentCallFrame->self;
    if (self == nullptr) return false;
    return appendSceneryDrawListFor(ctx, self, Instance_getSelfVar(self, ctx->hldSceneryInactiveVarId));
}
'''
    code = replace_once(code, original, append)
    anchor = "static void popSceneryEnv(VMContext* ctx, OpWord word, uint32_t instrAddr, const uint8_t* bytes) {"
    code = replace_once(code, IDLE_SCENERY, "")
    code = replace_once(code, anchor, IDLE_SCENERY + helper + "\n" + anchor)
    before = "        if (hldScenerySkip && hldSceneryGuard) skipSceneryWithGuard(ctx, &savedGroup);" if launch_flag else (
        "        skipSceneryWithGuard(ctx, &savedGroup);")
    call = "streamSettledScenery(ctx, &savedGroup" + (", bytes" if checked else "") + ");"
    code = replace_once(code, before, f'''#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && \\
    !defined(ENABLE_VM_OPCODE_PROFILER)
        if ({guards}) {call}
        else
#endif
''' + before)
    code = replace_once(code, "    bindIdleScenery(ctx);", "    bindIdleScenery(ctx);\n    bindSceneryStream(ctx);")
    return replace_once(code, "void VM_free(VMContext* ctx) {", "void VM_free(VMContext* ctx) {\n"
                        "    if (sceneryStreamOwner == ctx) sceneryStreamOwner = nullptr;")


def with_scenery_stream_exit(code):
    anchor = "    Runner_free(runner);"
    return replace_once(code, anchor, "    extern void hldReportSceneryStreamAtExit(void);\n"
                        "    hldReportSceneryStreamAtExit();\n" + anchor)
