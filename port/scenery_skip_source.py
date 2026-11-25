"""Skip offscreen scenery that neither updates visibility nor runs an event."""

from runtime_verification_source import replace_once


SCENERY_SKIP = r'''
static unsigned skippedSceneryBeforeSwitch;

static void skipInactiveScenery(VMContext* ctx) {
    if (ctx != sceneryPass.context || ctx->globalScopeInstance == nullptr ||
        ctx->hldSceneryInactiveVarId < 0 || ctx->hldRoomTimeVarId < 0) return;
#ifdef HLD_COMPARE_SCENERY_PASS
    if (!useSceneryPass(ctx->runner->frameCount)) return;
#endif
    const CallFrame* call = ctx->currentCallFrame;
    if (call->localVarsCount <= 4) return;
    const RValue group = call->localVars[4];
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (group.type != RVALUE_REAL || !(group.real >= 0.0 && group.real <= 3.0) ||
        group.real != (unsigned)group.real || !(epsilon >= 0.0 && epsilon < 0.5)) return;
    const unsigned selected = (unsigned)group.real;
    const RValue roomTime = Instance_getSelfVar(ctx->globalScopeInstance, ctx->hldRoomTimeVarId);
    if ((roomTime.type != RVALUE_REAL && roomTime.type != RVALUE_INT32) ||
        !(RValue_toReal(roomTime) - (GMLReal)16 > epsilon)) return;

    EnvFrame* env = ctx->envStack;
    while (env->currentIndex + 1 < env->instanceCount) {
        Instance* next = env->instanceList[env->currentIndex + 1];
        if (!next->active) {
            ++env->currentIndex;
            continue;
        }
        // The non-selected groups leave visibility and inactive state as-is.
        // Recessing scenery still runs an update when inactive.
        if (next->instanceId >= (1u << 24) || next->instanceId % 4 == selected ||
            next->objectIndex == 36 || next->objectIndex == STRUCT_OBJECT_INDEX) break;
        const RValue inactive = Instance_getSelfVar(next, ctx->hldSceneryInactiveVarId);
        if ((inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32) ||
            !RValue_toBool(inactive) || !(RValue_toReal(inactive) > epsilon)) break;
        ++env->currentIndex;
        ++sceneryCursorEntries;
        ++sceneryCursorSkipped;
        if (++skippedSceneryBeforeSwitch == 1000) logInfo("HLD: skipped 1,000 scenery entries before switching context\n");
    }
}
'''


GUARD_REPORT = r'''
static unsigned scenerySkipGuardReads, scenerySkipGuardReuses;

void hldReportScenerySkipGuardAtExit(void) {
    logInfo("HLD: scenery guard reads=%u reuses=%u early_skips=%u\n",
            scenerySkipGuardReads, scenerySkipGuardReuses, skippedSceneryBeforeSwitch);
}
'''


def reuse_scenery_skip_guard(code, *, launch_flag=None, guard_flag=None, checked=False):
    start = code.index("static void skipInactiveScenery(VMContext* ctx) {")
    end = code.index("    EnvFrame* env = ctx->envStack;", start)
    loop_end = code.index("\n}\n", end) + 3
    guard = code[start:end].replace("static void skipInactiveScenery(", "static unsigned scenerySkipGroup(")
    guard = guard.replace("return;", "return SCENERY_SKIP_NO_GROUP;")
    guard = replace_once(guard, "    const unsigned selected = (unsigned)group.real;\n", "")
    if launch_flag:
        guard = replace_once(guard, f"    if (!{launch_flag}) return SCENERY_SKIP_NO_GROUP;\n", "")
    helper = r'''
enum { SCENERY_SKIP_NO_GROUP = 4, SCENERY_SKIP_REFRESH = 5 };
''' + GUARD_REPORT + "\n" + guard.rstrip() + r'''
    return (unsigned)group.real;
}

static void skipSceneryWithGuard(VMContext* ctx, unsigned* savedGroup) {
'''
    helper += r'''    if (*savedGroup == SCENERY_SKIP_REFRESH) {
        *savedGroup = scenerySkipGroup(ctx);
        ++scenerySkipGuardReads;
    } else {
        ++scenerySkipGuardReuses;
    }
    const unsigned selected = *savedGroup;
'''
    if checked:
        helper += "    require(selected == scenerySkipGroup(ctx));\n"
    helper += "    if (selected == SCENERY_SKIP_NO_GROUP) return;\n\n"
    # When the guard passes, epsilon is less than 0.5. Boolean true
    # for either supported type already exceeds that threshold.
    original = """        if ((inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32) ||
            !RValue_toBool(inactive) || !(RValue_toReal(inactive) > epsilon)) break;"""
    simplified = """        const bool canSkip = inactive.type == RVALUE_REAL ? inactive.real > (GMLReal)0.5 :
                            inactive.type == RVALUE_INT32 && inactive.int32 > 0;"""
    if checked:
        simplified += """
        require(canSkip == ((inactive.type == RVALUE_REAL || inactive.type == RVALUE_INT32) &&
                RValue_toBool(inactive) && RValue_toReal(inactive) > ctx->runner->mathEpsilon));"""
    simplified += "\n        if (!canSkip) break;"
    helper += replace_once(code[end:loop_end], original, simplified)
    # Keep the original loop intact in the disabled mode for single-binary A/B.
    if guard_flag:
        helper = code[start:loop_end] + helper
    code = code[:start] + helper + code[loop_end:]
    cursor = "    const uint32_t continuation = ctx->ip;\n    while (true) {"
    code = replace_once(code, cursor, "    const uint32_t continuation = ctx->ip;\n"
                        "    unsigned savedGroup = SCENERY_SKIP_REFRESH;\n    while (true) {")
    call = "skipSceneryWithGuard(ctx, &savedGroup);"
    if guard_flag:
        call = f"if ({launch_flag} && {guard_flag}) {call}\n        else skipInactiveScenery(ctx);"
    code = replace_once(code, "skipInactiveScenery(ctx);", call)
    # Only the bound cull runs between consecutive successful skips.
    # Discard the decision before running any arbitrary GML callback.
    anchor = "        if (!hldSceneryCanSkipUpdate(ctx)) {\n"
    code = replace_once(code, anchor, anchor + "            savedGroup = SCENERY_SKIP_REFRESH;\n")
    if guard_flag:
        anchor = "static void popSceneryEnv(VMContext* ctx, OpWord word, uint32_t instrAddr, const uint8_t* bytes) {"
        code = replace_once(code, anchor, f"extern bool {guard_flag};\n" + r'''
void hldReportScenerySkipGuard(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery guard frame=%d reads=%u reuses=%u\n",
            frame, scenerySkipGuardReads, scenerySkipGuardReuses);
    scenerySkipGuardReads = scenerySkipGuardReuses = 0;
}

''' + anchor)
    return code


def with_scenery_skip_guard_exit(code):
    anchor = "    Runner_free(runner);"
    return replace_once(code, anchor, "    extern void hldReportScenerySkipGuardAtExit(void);\n"
                        "    hldReportScenerySkipGuardAtExit();\n" + anchor)


def with_scenery_skip(code, *, launch_flag=None):
    helper = SCENERY_SKIP
    if launch_flag:
        anchor = "static unsigned skippedSceneryBeforeSwitch;"
        helper = replace_once(helper, anchor, anchor + f'''
extern bool {launch_flag};

void hldReportScenerySkip(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery skips frame=%d enabled=%d calls=%u\\n",
            frame, (int){launch_flag}, skippedSceneryBeforeSwitch);
    skippedSceneryBeforeSwitch = 0;
}}
''')
        anchor = "static void skipInactiveScenery(VMContext* ctx) {"
        helper = replace_once(helper, anchor, anchor + f"\n    if (!{launch_flag}) return;")
        anchor = '        if (++skippedSceneryBeforeSwitch == 1000) logInfo("HLD: skipped 1,000 scenery entries before switching context\\n");'
        helper = replace_once(helper, anchor, "        ++skippedSceneryBeforeSwitch;")
    anchor = "static void popSceneryEnv(VMContext* ctx, OpWord word, uint32_t instrAddr, const uint8_t* bytes) {"
    code = replace_once(code, anchor, helper + "\n" + anchor)
    anchor = "        handlePopEnv(ctx, word, instrAddr);\n        if (ctx->ip != body) return;"
    return replace_once(code, anchor, "        skipInactiveScenery(ctx);\n" + anchor)
