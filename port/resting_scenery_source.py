"""Finish resting scenery after its original case callback."""

from pathlib import Path


def with_resting_scenery(code, *, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    mode = "#define HLD_COMPARE_RESTING_SCENERY\n" if compare else ""
    if verify:
        mode += "#define HLD_VERIFY_RESTING_SCENERY\n"
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    replace(anchor, "static bool finishRestingScenery(VMContext* ctx, const CodeEntry* code);\n\n" + anchor)
    anchor = ("            case OP_CALL:\n                VM_SYNC_IP();\n"
              "                handleCall(ctx, opword, extraData, false);")
    replace(anchor, anchor + r'''
                if (instrAddr == 0x7c && finishRestingScenery(ctx, code))
                    return scriptFallthroughReturnValue(ctx);''')
    helper = Path(__file__).with_name("resting-scenery.inc").read_text()
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, mode + helper + "\n" + anchor)
    replace("    packHldLiteralInstructions(ctx);",
            "    bindRestingScenery(ctx);\n    packHldLiteralInstructions(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    if (restingScenery.context == ctx) restingScenery = (RestingScenery){0};")
    anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
    replace(anchor, anchor + r'''
#ifdef HLD_VERIFY_RESTING_SCENERY
    if (restingSceneryCheck == &callFrame) {
        restingSceneryCheck = nullptr;
        const RValue expected = scriptFallthroughReturnValue(ctx);
        const RValue stored = Instance_getSelfVar(selfInstance, restingScenery.caseStore);
        const RValue condition = restingSceneryExpected;
        require(result.type == expected.type && result.real == expected.real &&
            result.gmlStackType == expected.gmlStackType && result.ownsReference == expected.ownsReference);
        require(stored.type == condition.type && RValue_toReal(stored) == RValue_toReal(condition) &&
            stored.gmlStackType == condition.gmlStackType && stored.assetRefType == condition.assetRefType);
        if (++restingSceneryCalls == 1000) logInfo("HLD: checked 1,000 resting scenery updates\n");
    }
#endif''')
    return code
