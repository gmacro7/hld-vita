"""Specialize unchanged results in the fingerprinted case-query script."""

from pathlib import Path


def with_stable_cases(code, compare=False, verify=False, link_checks=False, case_check_launch=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    mode = "#define HLD_COMPARE_STABLE_CASES\n" if compare else ""
    if verify:
        mode += "#define HLD_VERIFY_STABLE_CASES\n"
    if link_checks:
        mode += "#define HLD_LINK_CASES\n"
    if case_check_launch:
        mode += "#define HLD_CASE_CHECK_LAUNCH\n"
    helper = Path(__file__).with_name("stable-case.inc").read_text()
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, mode + helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindStableCases(ctx);\n" + anchor)
    replace("void VM_free(VMContext* ctx) {", "void VM_free(VMContext* ctx) {\n"
            "    if (stableCases.context == ctx) stableCases = (StableCases){0};")
    anchor = "    // Create a CallFrame for this script\n"
    replace(anchor, r'''#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    RValue stableResult;
    const bool stable = code == stableCases.code && ctx == stableCases.context && argCount == 0 &&
        tryStableCase(ctx, selfInstance, &stableResult);
    if (stable) {
        COUNT_STABLE_CASE();
#ifndef HLD_VERIFY_STABLE_CASES
        Instance_setSelfVar(selfInstance, stableCases.usesCase, RValue_makeReal(1.0));
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return stableResult;
#endif
    }
#endif

''' + anchor)
    anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
    replace(anchor, anchor + r'''
#if defined(HLD_VERIFY_STABLE_CASES) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (stable) {
        require(result.type == stableResult.type && result.int32 == stableResult.int32 &&
                result.gmlStackType == stableResult.gmlStackType && result.ownsReference == stableResult.ownsReference);
        const RValue enabled = Instance_getSelfVar(selfInstance, stableCases.usesCase);
        require(enabled.type == RVALUE_REAL && enabled.real == 1.0 && enabled.gmlStackType == GML_TYPE_DOUBLE);
    }
#endif''')
    return code
