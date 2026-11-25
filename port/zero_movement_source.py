"""Run the original zero-step movement path without interpreting its setup."""

from pathlib import Path


def with_zero_movement(code, compare=False, verify=False, sweep=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("zero-movement.inc").read_text()
    mode = "#define HLD_COMPARE_ZERO_MOVEMENT\n" if compare else ""
    if verify:
        mode += "#define HLD_VERIFY_ZERO_MOVEMENT\n"
    if sweep:
        mode += "#define HLD_MANTLE_SWEEP\n"
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, mode + helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindZeroMovement(ctx);\n" + anchor)
    replace("void VM_free(VMContext* ctx) {", r'''void VM_free(VMContext* ctx) {
#if defined(HLD_MANTLE_SWEEP) && defined(HLD_VERIFY_ZERO_MOVEMENT)
    reportMantleSweepChecks();
#endif
    if (zeroMovement.context == ctx) zeroMovement = (ZeroMovement){0};''')
    anchor = "    // Create a CallFrame for this script\n"
    replace(anchor, r'''#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    ZeroMovementResult zeroMoveResult;
    const bool zeroMove = code == zeroMovement.code && ctx == zeroMovement.context &&
        predictZeroMovement(ctx, selfInstance, args, argCount, &zeroMoveResult);
    if (zeroMove) {
#ifndef HLD_VERIFY_ZERO_MOVEMENT
        applyZeroMovement(ctx, selfInstance, &zeroMoveResult);
#ifdef HLD_COMPARE_ZERO_MOVEMENT
        ++zeroMovementCalls;
#endif
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return RValue_makeOwnedString(safeStrdup("none"));
#endif
    }
#endif

''' + anchor)
    anchor = "    result = RValue_stealOwnershipOrCopy(result);"
    replace(anchor, anchor + r'''
#if defined(HLD_VERIFY_ZERO_MOVEMENT) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (zeroMove) {
#ifdef HLD_MANTLE_SWEEP
        const GMLReal expectedH = zeroMoveResult.horizontalSteps, expectedV = zeroMoveResult.verticalSteps;
#else
        const GMLReal expectedH = 0.0, expectedV = 0.0;
#endif
        require(callFrame.localVars[1].type == RVALUE_REAL && callFrame.localVars[1].real == expectedH);
        require(callFrame.localVars[2].type == RVALUE_REAL && callFrame.localVars[2].real == expectedV);
        verifyZeroMovement(selfInstance, &zeroMoveResult, result);
    }
#endif''')
    return code
