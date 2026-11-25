"""Measure VM entry shortcuts as work of the called script."""

from pathlib import Path

from runtime_verification_source import replace_once


def with_profiled_native_entry(code, *, scope_arguments="ctx"):
    signature = ("RValue VM_executeCode(VMContext* ctx, int32_t codeIndex, Instance* selfInstance, "
                 "Instance* otherInstance, int32_t eventObjectIndex, int32_t eventType, "
                 "int32_t eventSubtype, RValue* args, int32_t argCount)")
    start = code.index(signature + " {")
    end = code.index("\n}\n", start) + 2
    body = code[start:end]
    entry = signature.replace("RValue VM_executeCode", "static RValue executeProfiledCode")
    entry = entry.removesuffix(")") + ", VMSampleScope* sample)"
    body = replace_once(body, signature, entry)
    anchor = "    // Create a CallFrame for this script\n"
    body = replace_once(body, anchor, "    markVMSampleInterpreted(sample);\n\n" + anchor)
    return code[:start] + body + f"""

{signature} {{
    VMSampleScope sample = beginVMSample({scope_arguments});
    RValue result = executeProfiledCode(ctx, codeIndex, selfInstance, otherInstance,
                                        eventObjectIndex, eventType, eventSubtype, args, argCount, &sample);
    endVMSample(sample, codeIndex, eventType, eventSubtype);
    return result;
}}""" + code[end:]


def with_profiled_builtins(code, sample):
    definition = "static void reportVMSample(VMContext* ctx) {"
    extension = Path(__file__).with_name("builtin-profile.inc").read_text()
    sample = replace_once(sample, definition, extension + "\n" + definition)
    sample = replace_once(sample, "    memset(vmSampleTotals, 0, codeCount * sizeof(*vmSampleTotals));",
                          "    reportVMBuiltins(ctx, msPerFrame);\n"
                          "    memset(vmSampleTotals, 0, codeCount * sizeof(*vmSampleTotals));")
    before = "static void handleCall(VMContext* ctx, OpWord opword, const uint8_t* extraData, bool discard) {"
    declaration = ("static RValue profileBuiltinCall(VMContext* ctx, BuiltinFunc builtin, RValue* args,\n"
                   "                                 int32_t argCount, uint32_t funcIndex);\n\n")
    code = replace_once(code, before, declaration + before)
    code = replace_once(code, "        RValue result = builtin(ctx, args, argCount);",
                        "        RValue result = profileBuiltinCall(ctx, builtin, args, argCount, funcIndex);")
    return code, sample


def with_focused_step_samples(code):
    """Separate interpreter, builtin, and native work under one Step event."""
    sample = Path(__file__).with_name("vm-profile.inc").read_text()
    sample = "#define HLD_PROFILE_VM_DISPATCH\n#define HLD_PROFILE_NATIVE_ENTRY\n" + sample
    code, sample = with_profiled_builtins(code, sample)
    sample = replace_once(sample,
        "    if (ctx->runner == nullptr || ctx->runner->frameCount != vmSampleLastFrame || vmSampleFrames == 0)",
        "    if (vmSampleDepth == 0)")
    sample = replace_once(sample, "static int vmSampleWindow = -1;",
                          "static int vmSampleWindow = -1;\nstatic const char* vmFocusedStep;")
    sample = replace_once(sample, "    const double msPerFrame = 1e-6 / vmSampleFrames;",
        r'''    const double msPerFrame = 1e-6 / vmSampleFrames;
    uint64_t root = 0, dispatch = 0, builtins = 0, native = 0;
    for (uint32_t index = 0; index < codeCount; ++index) {
        const VMSampleTotal* total = &vmSampleTotals[index];
        root += total->root;
        dispatch += total->dispatch;
        builtins += total->builtins;
        native += total->nativeEntry;
    }
    logInfo("Vita focused VM window=%d samples=%u root=%s root_ms=%.3f dispatch_ms=%.3f "
            "builtin_ms=%.3f native_ms=%.3f\n", (vmSampleWindow + 1) * 90, vmSampleFrames,
            vmFocusedStep, root * msPerFrame, dispatch * msPerFrame,
            builtins * msPerFrame, native * msPerFrame);
    vmFocusedStep = nullptr;''')
    start = sample.index("static VMSampleScope beginVMSample(")
    end = sample.index("    VMSampleScope scope = {", start)
    sample = sample[:start] + '''static VMSampleScope beginVMSample(VMContext* ctx, int32_t codeIndex,
                                         int32_t eventType, int32_t eventSubtype) {
    if (ctx->runner == nullptr) return (VMSampleScope){0};
    const int frame = ctx->runner->frameCount;
    const int window = frame / 90;
    if (vmSampleDepth == 0) {
        if (vmSampleFrames > 0 && window != vmSampleWindow) reportVMSample(ctx);
        if (frame < 540 || frame >= 720 || frame % 15 != 6 ||
            ctx->currentCallFrame != nullptr || eventType != EVENT_STEP ||
            codeIndex < 0 || (uint32_t)codeIndex >= ctx->dataWin->code.count ||
            ctx->runner->currentRoom == nullptr) return (VMSampleScope){0};
        const char* room = ctx->runner->currentRoom->name;
        const bool vault = strcmp(room, "rm_WL_WestDrifterVault") == 0;
        if (eventSubtype != (vault ? STEP_NORMAL : STEP_END)) return (VMSampleScope){0};
        const char* target = vault ? "gml_Object_enemy_Step_0" : "gml_Object_SCREEN_Step_2";
        if (strcmp(ctx->dataWin->code.entries[codeIndex].name, target) != 0) return (VMSampleScope){0};
        if (vmFocusedStep != nullptr && strcmp(vmFocusedStep, target) != 0) reportVMSample(ctx);
        if (vmSampleTotals == nullptr) {
            vmSampleTotals = safeCalloc(ctx->dataWin->code.count, sizeof(*vmSampleTotals));
        }
        vmFocusedStep = target;
        vmSampleWindow = window;
        if (vmSampleFrames == 0 || frame != vmSampleLastFrame) {
            vmSampleLastFrame = frame;
            ++vmSampleFrames;
        }
    }
''' + sample[end:]
    marker = "RValue VM_executeCode(VMContext* ctx, int32_t codeIndex,"
    code = replace_once(code, marker, sample + "\n" + marker)
    return with_profiled_native_entry(code, scope_arguments="ctx, codeIndex, eventType, eventSubtype")


def with_offscreen_entry_samples(code):
    sample = Path(__file__).with_name("vm-profile.inc").read_text()
    sample = "#define HLD_PROFILE_CURRENT_RUNTIME\n#define HLD_PROFILE_VM_DISPATCH\n#define HLD_PROFILE_NATIVE_ENTRY\n" + sample
    sample = replace_once(sample, "frame < 550 || frame % 33 != 9", "frame < 180 || frame % 17 != 2")
    sample = replace_once(sample, "const int window = (frame - 550) / 220;", "const int window = (frame - 180) / 180;")
    code, sample = with_profiled_builtins(code, sample)
    sample = sample.replace('"Vita ', '"HLD offscreen ')
    function = "RValue VM_executeCode(VMContext* ctx, int32_t codeIndex,"
    code = replace_once(code, function, sample + "\n" + function)
    cleanup = "void VM_free(VMContext* ctx) {"
    code = replace_once(code, cleanup, cleanup + "\n    if (vmSampleFrames > 0) reportVMSample(ctx);\n"
                        "    free(vmBuiltinTotals);\n    vmBuiltinTotals = nullptr;\n"
                        "    free(vmSampleTotals);\n    vmSampleTotals = nullptr;\n"
                        "    vmSampleLastFrame = vmSampleWindow = -1;\n")
    return with_profiled_native_entry(code)
