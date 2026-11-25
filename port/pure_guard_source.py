"""Fuse read-only script guards with the branch that consumes their result."""

from pathlib import Path


def with_pure_guards(code, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("pure-guard.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_PURE_GUARDS\n" + helper
    if verify:
        helper = "#define HLD_VERIFY_PURE_GUARDS\n" + helper
    signature = "static const CodeEntry* matchHldCode(VMContext* ctx, const char* name, uint32_t length, uint32_t expected)"
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, signature + ";\n\n" + helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindPureGuards(ctx);\n" + anchor)
    replace("    packHldArguments(ctx);", "    packHldArguments(ctx);\n    packPureGuards(ctx);")
    replace("void VM_free(VMContext* ctx) {", "void VM_free(VMContext* ctx) {\n"
            "    if (pureGuards.context == ctx) pureGuards = (PureGuards){0};")
    anchor = ("            case OP_CALL:\n                VM_SYNC_IP();\n"
              "                handleCall(ctx, opword, extraData, false);")
    handler = r'''            case OP_PACKED_PURE_GUARD: {
                const int32_t argc = OpWord_extra(opword);
                const OpWord originalCall = {UINT32_C(0xd9020000) | (uint16_t)argc};
                bool answer;
                if (fuseInstructions && pureGuardResult(ctx, argc, &answer)) {
#ifdef HLD_VERIFY_PURE_GUARDS
                    VM_SYNC_IP();
                    handleCall(ctx, originalCall, extraData, false);
                    const RValue result = ctx->stack.slots[ctx->stack.top - 1];
                    require(result.type == RVALUE_INT32 && result.int32 == (int)answer);
                    notePureGuard(argc);
                    break;
#else
                    const bool negated = OpWord_type2(opword) != 0;
                    uint32_t branch = ip + (negated ? 8 : 4);
                    if (consumeConditionalBranch(bytecodeBase, codeLength, &branch, answer != negated)) {
                        ctx->stack.top -= argc;
                        ip = branch;
                        notePureGuard(argc);
                        break;
                    }
#endif
                }
                VM_SYNC_IP();
                handleCall(ctx, originalCall, extraData, false);
                break;
            }
'''
    replace(anchor, handler + anchor)
    return code
