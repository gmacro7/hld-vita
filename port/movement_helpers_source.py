"""Bind three frequently called movement and object queries to checked C paths."""

from pathlib import Path


def with_movement_helpers(code, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("movement-helpers.inc").read_text()
    mode = "#define HLD_COMPARE_MOVEMENT_HELPERS\n" if compare else ""
    if verify:
        mode += "#define HLD_VERIFY_MOVEMENT_HELPERS\n"
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, mode + helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindMovementHelpers(ctx);\n" + anchor)
    replace("void VM_free(VMContext* ctx) {", "void VM_free(VMContext* ctx) {\n"
            "    if (movementHelpers.context == ctx) movementHelpers = (MovementHelpers){0};")
    anchor = "    // Create a CallFrame for this script\n"
    replace(anchor, r'''#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    RValue movementResult;
    const MovementHelper movementHelper = tryMovementHelper(ctx, code, args, argCount, &movementResult);
#ifndef HLD_VERIFY_MOVEMENT_HELPERS
    if (movementHelper != MOVEMENT_HELPER_NONE) {
#ifdef HLD_COMPARE_MOVEMENT_HELPERS
        ++movementHelperCalls[movementHelper];
#endif
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return movementResult;
    }
#endif
#endif

''' + anchor)
    anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
    replace(anchor, anchor + r'''
#if defined(HLD_VERIFY_MOVEMENT_HELPERS) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (movementHelper != MOVEMENT_HELPER_NONE) {
        require(result.type == movementResult.type && result.gmlStackType == movementResult.gmlStackType &&
                result.ownsReference == movementResult.ownsReference && result.assetRefType == movementResult.assetRefType);
        if (result.type == RVALUE_REAL) require(memcmp(&result.real, &movementResult.real, sizeof(result.real)) == 0);
        else require(result.int32 == movementResult.int32);
        if (++movementHelperCalls[movementHelper] == 100) {
            logInfo("HLD: checked 100 movement helper calls to %s\n", code->name);
        }
    }
#endif''')
    return code
