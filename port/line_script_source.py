"""Bypass VM dispatch around the unchanged line-collision builtin."""

from runtime_verification_source import replace_once


def with_line_script(code, *, verify=False, launch_flag=None, extra_args=False, profile=False):
    helper = r'''
typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    BuiltinFunc collision;
    int32_t debugVariable;
} LineScript;

static LineScript lineScript;
static unsigned lineScriptCalls;

static void bindLineScript(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_CollisionLine", 336, 0xae3ffd23u);
    if (code == nullptr) return;
    const uint8_t* program = codeBytes(ctx, code);
    const uint32_t function = resolveFuncOperand(program + 0xb4);
    const BuiltinFunc collision = VM_findBuiltin(ctx, "collision_line");
    if (function >= ctx->funcCallCacheCount || collision == nullptr ||
        ctx->funcCallCache[function].builtin != (void*)collision ||
        ctx->funcCallCache[function].scriptCodeIndex >= 0) return;
    const Variable* debug = resolveVarDef(ctx, resolveVarOperand(program + 0xfc));
    if (debug->varID < 0 || strcmp(debug->name, "showDebugTools") != 0) return;
    lineScript = (LineScript){ctx, code, collision, debug->varID};
    logInfo("HLD: bound direct line script\n");
}

static bool runLineScript(VMContext* ctx, const CodeEntry* code, Instance* self, Instance* other,
                          const RValue* args, int32_t count, RValue* result) {
    if (code != lineScript.code || ctx != lineScript.context || ctx->runner == nullptr ||
        ctx->globalScopeInstance == nullptr || args == nullptr || count != 5) return false;
    const RValue debug = Instance_getSelfVar(ctx->globalScopeInstance, lineScript.debugVariable);
    if ((debug.type != RVALUE_REAL && debug.type != RVALUE_INT32 && debug.type != RVALUE_BOOL) ||
        RValue_toBool(debug)) return false;
    for (int32_t i = 0; i < count; ++i) {
        if (args[i].type != RVALUE_REAL && args[i].type != RVALUE_INT32 && args[i].type != RVALUE_BOOL)
            return false;
    }

    // This builtin resolves self and other and makes no nested script calls.
    CallFrame frame = {.self = self, .other = other};
    CallFrame* saved = ctx->currentCallFrame;
    RValue params[] = {args[0], args[1], args[2], args[3], args[4],
                       RValue_makeInt32(1), RValue_makeInt32(1)};
    ctx->currentCallFrame = &frame;
    const RValue hit = lineScript.collision(ctx, params, 7);
    ctx->currentCallFrame = saved;
    *result = RValue_makeReal(RValue_toBool(hit));
    result->gmlStackType = GML_TYPE_VARIABLE;
    if (++lineScriptCalls == 100) logInfo("HLD: reached 100 direct line script calls\n");
    return true;
}

'''
    if extra_args:
        helper = replace_once(helper, "args == nullptr || count != 5", "args == nullptr || (count != 5 && count != 7)")
        helper = replace_once(helper, "    for (int32_t i = 0; i < count; ++i) {",
                              "    // The bound program reads only arguments 0–4.\n"
                              "    for (int32_t i = 0; i < 5; ++i) {")
        helper = replace_once(helper, "static unsigned lineScriptCalls;",
                              "static unsigned lineScriptCalls, lineScriptSeven;")
        anchor = "    if (++lineScriptCalls == 100)"
        helper = replace_once(helper, anchor, r'''    if (count == 7 && ++lineScriptSeven == 100)
        logInfo("HLD: reached 100 direct line script calls with seven arguments\n");
''' + anchor)
    if launch_flag:
        anchor = "static unsigned lineScriptCalls, lineScriptSeven;" if extra_args else "static unsigned lineScriptCalls;"
        helper = replace_once(helper, anchor, anchor + f'''
extern bool {launch_flag};

void hldReportLineScript(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita line script frame=%d enabled=%d calls=%u\\n", frame, (int){launch_flag}, lineScriptCalls);
    lineScriptCalls = 0;
}}
''')
        anchor = "    if (++lineScriptCalls == 100) logInfo(\"HLD: reached 100 direct line script calls\\n\");"
        helper = replace_once(helper, anchor, "    ++lineScriptCalls;")
        if extra_args:
            anchor = "    lineScriptCalls = 0;"
            helper = replace_once(helper, anchor, r'''    logInfo("Vita line script seven frame=%d calls=%u\n", frame, lineScriptSeven);
    lineScriptSeven = 0;
''' + anchor)
            helper = replace_once(helper, r'''    if (count == 7 && ++lineScriptSeven == 100)
        logInfo("HLD: reached 100 direct line script calls with seven arguments\n");
''', "    lineScriptSeven += count == 7;\n")
    if profile:
        helper = replace_once(helper, "    int32_t debugVariable;", "    int32_t debugVariable;\n    uint32_t function;")
        helper = replace_once(helper, "(LineScript){ctx, code, collision, debug->varID}",
                              "(LineScript){ctx, code, collision, debug->varID, function}")
        helper = replace_once(helper, "    CallFrame frame = {.self = self, .other = other};",
                              "    const int32_t codeIndex = (int32_t)(code - ctx->dataWin->code.entries);\n"
                              "    CallFrame frame = {.codeIndex = codeIndex, .self = self, .other = other};")
        helper = replace_once(helper, "    const RValue hit = lineScript.collision(ctx, params, 7);",
                              "    const RValue hit = profileBuiltinCall(ctx, lineScript.collision, params, 7, lineScript.function);")
    guard = ("!defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && "
             "!defined(ENABLE_VM_OPCODE_PROFILER)")
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    code = replace_once(code, anchor, f"#if {guard}\n" + helper + "#endif\n\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    code = replace_once(code, anchor, anchor + f"\n#if {guard}\n    bindLineScript(ctx);\n#endif")
    anchor = "void VM_free(VMContext* ctx) {"
    reset_count = "        lineScriptCalls = lineScriptSeven = 0;\n" if extra_args else "        lineScriptCalls = 0;\n"
    code = replace_once(code, anchor, anchor + f"\n#if {guard}\n"
                        "    if (lineScript.context == ctx) {\n"
                        "        lineScript = (LineScript){0};\n"
                        + reset_count +
                        "    }\n#endif")
    anchor = "    // Create a CallFrame for this script\n"
    invoke = r'''    RValue lineResult;
    const bool directLine = runLineScript(ctx, code, selfInstance, otherInstance, args, argCount, &lineResult);
'''
    if launch_flag:
        invoke = replace_once(invoke, "const bool directLine = ", f"const bool directLine = {launch_flag} && ")
    if not verify:
        invoke += r'''    if (directLine) {
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return lineResult;
    }
'''
    sample_anchor = "    // Try the bound line query.\n" if profile else ""
    if profile:
        invoke = replace_once(invoke, "        return lineResult;",
                              "        endVMSample(sample, codeIndex, eventType, eventSubtype);\n"
                              "        return lineResult;")
    code = replace_once(code, anchor, sample_anchor + f"#if {guard}\n" + invoke + "#endif\n\n" + anchor)
    if verify:
        anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
        code = replace_once(code, anchor, anchor + f"\n#if {guard}\n" + r'''
    if (directLine) {
        require(result.type == lineResult.type && result.gmlStackType == lineResult.gmlStackType &&
                result.ownsReference == lineResult.ownsReference && result.assetRefType == lineResult.assetRefType);
        require(result.real == lineResult.real);
    }
#endif''')
    return code
