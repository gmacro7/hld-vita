"""Preserve legacy depth writes while bypassing numeric script calls."""

from pathlib import Path


def with_depth_helpers(code, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("depth-helpers.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_DEPTH_HELPERS\n" + helper
    if verify:
        helper = "#define HLD_VERIFY_DEPTH_HELPERS\n" + helper
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindDepthHelpers(ctx);\n" + anchor)
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    if (depthHelpers.context == ctx) depthHelpers = (DepthHelpers){0};")
    anchor = "    // Create a CallFrame for this script\n"
    replace(anchor, r'''#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    DepthUpdate depthUpdate;
    const bool numericDepth = tryDepthHelper(ctx, code, selfInstance, args, argCount, &depthUpdate);
#ifdef HLD_VERIFY_DEPTH_HELPERS
    GMLReal expectedDepth = 0.0;
    bool expectedSortDirty = false;
    if (numericDepth) {
        const bool changed = depthUpdate.writeDepth && depthUpdate.depth != selfInstance->depth;
        expectedDepth = changed ? depthUpdate.depth : selfInstance->depth;
        expectedSortDirty = ctx->runner->drawableListSortDirty || changed;
    }
#else
    if (numericDepth) {
        if (depthUpdate.writeDepth) {
            VMBuiltins_setVariable(ctx, selfInstance, BUILTIN_VAR_DEPTH, "depth", RValue_makeReal(depthUpdate.depth), -1);
        }
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return depthUpdate.result;
    }
#endif
#endif

''' + anchor)
    anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
    replace(anchor, anchor + r'''
#if defined(HLD_VERIFY_DEPTH_HELPERS) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (numericDepth) {
        require(result.type == depthUpdate.result.type && result.gmlStackType == depthUpdate.result.gmlStackType);
        if (result.type == RVALUE_REAL) require(memcmp(&result.real, &depthUpdate.result.real, sizeof(result.real)) == 0);
        else require(result.type == RVALUE_INT32 && result.int32 == depthUpdate.result.int32);
        require(memcmp(&selfInstance->depth, &expectedDepth, sizeof(expectedDepth)) == 0);
        require(ctx->runner->drawableListSortDirty == expectedSortDirty);
    }
#endif''')
    return code
