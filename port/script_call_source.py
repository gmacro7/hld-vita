"""Reuse a private call buffer for scalar script arguments."""


def with_script_call_buffers(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    signature = ("RValue VM_executeCode(VMContext* ctx, int32_t codeIndex, Instance* selfInstance, "
                 "Instance* otherInstance, int32_t eventObjectIndex, int32_t eventType, "
                 "int32_t eventSubtype, RValue* args, int32_t argCount)")
    internal = "static " + signature.replace("VM_executeCode(", "executeScriptFrame(")[:-1] + ", bool reuseArgs)"
    replace(signature + " {", internal + " {")
    anchor = "// ===[ Disassembler ]==="
    replace(anchor, signature + " {\n"
            "    return executeScriptFrame(ctx, codeIndex, selfInstance, otherInstance, eventObjectIndex,\n"
            "                              eventType, eventSubtype, args, argCount, false);\n}\n\n" + anchor)

    # The public path continues to hold independent references. Only a
    # private local array whose populated entries are scalar can be borrowed.
    anchor = "    if (argCount > 0 && args != nullptr) {\n        callFrame.scriptArgsOnHeap ="
    replace(anchor, "    if (reuseArgs) {\n        scriptArgs = args;\n"
            "    } else if (argCount > 0 && args != nullptr) {\n        callFrame.scriptArgsOnHeap =")

    support = internal + r''';

static bool canReuseScalarArgs(const RValue* args, int32_t count) {
    if (count <= 0 || count > VM_INLINE_FRAME_VALUES || VM_MAX_STACK_ARGS < VM_INLINE_FRAME_VALUES) return false;
    for (int32_t i = 0; i < count; ++i) {
        const uint8_t type = args[i].type;
        if (args[i].ownsReference || (type != RVALUE_UNDEFINED &&
            (type < RVALUE_INT32 || type > RVALUE_REAL))) return false;
    }
    return true;
}
'''
    if compare:
        support += r'''
static unsigned reusedScriptCallBuffers;

static inline bool useScriptCallBuffers(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportScriptCallBuffers(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita script call buffers frame=%d enabled=%d reused=%u\n",
            frame, (int)useScriptCallBuffers(frame - 1), reusedScriptCallBuffers);
    reusedScriptCallBuffers = 0;
}
'''
    start = "static void handleCall(VMContext* ctx, OpWord opword, const uint8_t* extraData, bool discard) {"
    replace(start, support + "\n" + start)
    begin = code.index(start)
    end = code.index("\n}\n", begin) + 2
    handler = code[begin:end]

    def replace_handler(before, after):
        nonlocal handler
        assert handler.count(before) == 1, before
        handler = handler.replace(before, after)

    mode = "ctx->runner != nullptr && useScriptCallBuffers(ctx->runner->frameCount)" if compare else "true"
    anchor = "    // Pop arguments from stack (args pushed right-to-left, so first arg is on top)"
    replace_handler(anchor, "    FuncCallCache* cache = &ctx->funcCallCache[funcIndex];\n"
                    "    const bool scriptBuffer = cache->builtin == nullptr && cache->scriptCodeIndex >= 0 &&\n"
                    f"        {mode};\n" + anchor)
    replace_handler("    // Fast path: cached builtin function pointer\n    FuncCallCache* cache = &ctx->funcCallCache[funcIndex];",
                    "    // Fast path: cached builtin function pointer")
    replace_handler("        if (useStackArgs && argCount < VM_MAX_STACK_ARGS) {",
                    "        if (!scriptBuffer && useStackArgs && argCount < VM_MAX_STACK_ARGS) {")
    anchor = "    if (cache->scriptCodeIndex >= 0) {\n        RValue result = VM_executeCode("
    replace_handler(anchor, "    if (cache->scriptCodeIndex >= 0) {\n"
                    "        const bool reuseArgs = scriptBuffer && useStackArgs && canReuseScalarArgs(args, argCount);\n"
                    + ("        if (reuseArgs) ++reusedScriptCallBuffers;\n" if compare else "") +
                    "        RValue result = executeScriptFrame(")
    replace_handler("            args,\n            argCount\n        );",
                    "            args,\n            argCount,\n            reuseArgs\n        );")
    replace_handler("        // Free arguments (VM_executeCode copies what it needs)\n        if (args != nullptr) {",
                    "        // Borrowed buffers have already been cleaned up by the script frame.\n"
                    "        if (args != nullptr && !reuseArgs) {")
    return code[:begin] + handler + code[end:]
