"""Enter the existing dormant mine update before allocating a VM frame."""

from runtime_verification_source import replace_once


def with_mine_entry_grid(code):
    anchor = ('    requireMessageFormatted(__FILE__, __LINE__, target >= 0 || target == INSTANCE_ALL, '
              '"SpatialGrid: [%s] Query target cannot be instance type %d!", '
              'VM_getCurrentCallFrameCodeName(runner->vmContext), target);')
    return replace_once(code, anchor, """\
    if (target < 0 && target != INSTANCE_ALL) {
        VMContext* vm = runner->vmContext;
        const char* caller = vm->currentCallFrame ? VM_getCurrentCallFrameCodeName(vm) : "native collision query";
        requireMessageFormatted(__FILE__, __LINE__, false,
                                "SpatialGrid: [%s] Query target cannot be instance type %d!", caller, target);
    }""")


def with_mine_entry_builtins(code):
    signature = "bool hldPhaseMineTrigger(VMContext* ctx, bool* triggered) {"
    start = code.index(signature)
    end = code.index("\n}\n", start) + 2
    query = code[start:end]
    query = replace_once(query, signature,
                         "bool hldPhaseMineTriggerFor(VMContext* ctx, Instance* caller, bool* triggered) {")
    query = replace_once(query, "    Instance* caller = ctx->currentCallFrame->self;\n", "")
    return code[:start] + query + "\n\n" + signature + """
    return hldPhaseMineTriggerFor(ctx, ctx->currentCallFrame->self, triggered);
}""" + code[end:]


def with_mine_entry(code, *, checked, launch_flag=None):
    anchor = "static void bindDormantMine(VMContext* ctx, uint8_t* bytes) {"
    code = replace_once(code, anchor, """bool hldPhaseMineTriggerFor(VMContext* ctx, Instance* caller, bool* triggered);
static const CodeEntry* dormantMineEntry;

static void bindDormantMine(VMContext* ctx, const CodeEntry* event, uint8_t* bytes) {""")
    anchor = '    logInfo("HLD: bound the direct dormant mine update\\n");'
    code = replace_once(code, anchor, """    const OpWord pause = {BinaryUtils_readUint32Aligned(bytes)};
    if (pureGuards.context == ctx && pureGuards.pause != nullptr && OpWord_opcode(pause) == OP_PACKED_PURE_GUARD)
        dormantMineEntry = event;
""" + anchor)
    code = replace_once(code, "    bindDormantMine(ctx, bytes);", "    bindDormantMine(ctx, mine, bytes);")

    signature = "static bool tryDormantMine(VMContext* ctx) {"
    start = code.index(signature)
    stop = code.index("\n}\n", start) + 2
    body = code[start:stop]
    prefix, remainder = body.split("    const GMLReal next = timer.real + 1;\n", 1)
    prediction = replace_once(prefix, signature + "\n    Instance* self = ctx->currentCallFrame->self;",
                              "static bool planDormantMine(VMContext* ctx, Instance* self, GMLReal* next) {")
    prediction = replace_once(prediction, "hldPhaseMineTrigger(ctx, &triggered)",
                              "hldPhaseMineTriggerFor(ctx, self, &triggered)")
    prediction += "    *next = timer.real + 1;\n    return true;\n}\n\n"
    update, _ = remainder.split("#else\n", 1)[1].split("    return true;\n", 1)
    apply = "static void applyDormantMine(Instance* self, GMLReal next) {\n" + update + "}\n\n"
    body = signature + """
    Instance* self = ctx->currentCallFrame->self;
    GMLReal next;
    if (!planDormantMine(ctx, self, &next)) return false;
""" + replace_once(remainder, update, "    applyDormantMine(self, next);\n")
    code = code[:start] + prediction + apply + body + code[stop:]

    anchor = "    // Create a CallFrame for this script\n"
    candidate = """\
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    GMLReal mineEntryTimer = 0;
    bool mineEntryPaused = true;
    const bool useMineEntry = code == dormantMineEntry && pureGuards.context == ctx &&
        ctx->runner != nullptr && eventType == EVENT_STEP && eventSubtype == STEP_NORMAL && argCount == 0 &&
        ctx->runner->mathEpsilon >= 0 && ctx->runner->mathEpsilon < 0.5 &&
        pureGuardPause(ctx, ctx->runner->mathEpsilon, &mineEntryPaused) &&
        !mineEntryPaused && planDormantMine(ctx, selfInstance, &mineEntryTimer);
"""
    if not checked:
        note = "    ++mineEntryReturns;" if launch_flag else (
            '    if (++mineEntryReturns == 1000) logInfo("HLD: entered 1,000 dormant mine updates directly\\n");'
        )
        candidate += """\
    if (useMineEntry) {
        applyDormantMine(selfInstance, mineEntryTimer);
    NOTE_MINE_ENTRY
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
"""
        candidate = replace_once(candidate, "    NOTE_MINE_ENTRY", "    " + note)
    if launch_flag:
        candidate = replace_once(candidate, "useMineEntry = code == dormantMineEntry",
                                 f"useMineEntry = {launch_flag} && code == dormantMineEntry")
    code = replace_once(code, anchor, candidate + "#endif\n\n" + anchor)
    counters = "static unsigned mineEntryReturns;\n"
    if launch_flag:
        counters += f"""\
extern bool {launch_flag};

void hldReportMineEntry(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita dormant mine entries frame=%d enabled=%d calls=%u\\n",
            frame, (int){launch_flag}, mineEntryReturns);
    mineEntryReturns = 0;
}}
"""
    code = replace_once(code, "static const CodeEntry* dormantMineEntry;",
                        "static const CodeEntry* dormantMineEntry;\n" + counters)
    if checked:
        anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
        code = replace_once(code, anchor, anchor + """
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (useMineEntry) {
        const RValue timer = Instance_getSelfVar(selfInstance, phaseMineTimer);
        const RValue cooldown = Instance_getSelfVar(selfInstance, phaseMineCooldown);
        const RValue expected = scriptFallthroughReturnValue(ctx);
        require(timer.type == RVALUE_REAL && timer.gmlStackType == GML_TYPE_VARIABLE &&
                memcmp(&timer.real, &mineEntryTimer, sizeof(mineEntryTimer)) == 0);
        require(cooldown.type == RVALUE_REAL && cooldown.gmlStackType == GML_TYPE_VARIABLE &&
                cooldown.real == 0 && !signbit(cooldown.real));
        require(selfInstance->imageIndex == 0 && !signbit(selfInstance->imageIndex));
        require(result.type == expected.type && result.gmlStackType == expected.gmlStackType &&
                !result.ownsReference && RValue_toReal(result) == RValue_toReal(expected));
        if (++mineEntryReturns == 1000) logInfo("HLD: checked 1,000 dormant mine entries\\n");
    }
#endif
""")
    anchor = "void VM_free(VMContext* ctx) {"
    return replace_once(code, anchor, anchor + """
    if (pureGuards.context == ctx) {
        dormantMineEntry = nullptr;
        mineEntryReturns = 0;
    }
""")
