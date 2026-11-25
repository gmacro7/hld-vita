"""Specialize the paths on either side of the original active case event."""

from pathlib import Path


def with_active_case(code, *, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    mode = "#define HLD_COMPARE_ACTIVE_CASE\n" if compare else ""
    if verify:
        mode += "#define HLD_VERIFY_ACTIVE_CASE\n"
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    replace(anchor, mode + r'''enum {
    ACTIVE_CASE_EVENT_ARGUMENT = 0x150,
    ACTIVE_CASE_EVENT_CALL = 0x158,
};
static bool enterActiveCase(VMContext* ctx, const CodeEntry* code);
static bool finishActiveCase(VMContext* ctx, const CodeEntry* code, RValue* result);
#ifdef HLD_VERIFY_ACTIVE_CASE
static void verifyActiveCaseEntry(VMContext* ctx);
#endif

''' + anchor)
    anchor = "                resolveVariableWrite(ctx, OpWord_extra(pop), ref, RValue_makeReal(OpWord_extra(opword)));"
    replace(anchor, anchor + r'''
                if (instrAddr == 0 && enterActiveCase(ctx, code)) ip = ACTIVE_CASE_EVENT_ARGUMENT;''')
    anchor = """            case OP_PACKED_CALL_DISCARD:
                VM_SYNC_IP();
                handleCall(ctx, opword, extraData, true);"""
    replace(anchor, r'''            case OP_PACKED_CALL_DISCARD:
#ifdef HLD_VERIFY_ACTIVE_CASE
                if (instrAddr == ACTIVE_CASE_EVENT_CALL) verifyActiveCaseEntry(ctx);
#endif
                VM_SYNC_IP();
                handleCall(ctx, opword, extraData, true);
                if (instrAddr == ACTIVE_CASE_EVENT_CALL) {
                    RValue result;
                    if (finishActiveCase(ctx, code, &result)) return result;
                }''')
    helper = Path(__file__).with_name("active-case.inc").read_text()
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, helper + "\n" + anchor)
    replace("    packHldLiteralInstructions(ctx);",
            "    bindActiveCase(ctx);\n    packHldLiteralInstructions(ctx);")
    replace("    bindRegionCase(ctx);", "    bindRegionCase(ctx);\n    checkActiveCaseOpcodes(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    if (activeCase.context == ctx) activeCase = (ActiveCase){0};")
    anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
    replace(anchor, anchor + r'''
#ifdef HLD_VERIFY_ACTIVE_CASE
    require(activeCaseEntryCheck != &callFrame);
    if (activeCaseReturnCheck == &callFrame) {
        activeCaseReturnCheck = nullptr;
        require(result.type == RVALUE_INT32 && result.int32 == 1 &&
                result.gmlStackType == GML_TYPE_VARIABLE && !result.ownsReference);
        if (++activeCaseReturns == 1000) logInfo("HLD: checked 1,000 active case returns\n");
    }
#endif''')
    return code
