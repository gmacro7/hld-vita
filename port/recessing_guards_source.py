"""Fuse the authored terminal-state branches in the scenery update."""

from pathlib import Path


def with_recessing_guards(code, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("recessing-guards.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_RECESSING_GUARDS\n" + helper
    if verify:
        helper = "#define HLD_VERIFY_RECESSING_GUARDS\n" + helper
    signature = "static const CodeEntry* matchHldCode(VMContext* ctx, const char* name, uint32_t length, uint32_t expected)"
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, signature + ";\n\n" + helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindRecessingGuards(ctx);\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    replace(anchor, anchor + "\n    packRecessingGuards(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    if (recessingGuards.context == ctx) recessingGuards = (RecessingGuards){0};")
    if verify:
        anchor = "    // Some opcodes"
        replace(anchor, "    uint32_t expectedRecessingTarget = 0;\n    int32_t recessingStackTop = 0;\n" + anchor)
        anchor = "        OpWord opword = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};"
        replace(anchor, r'''        if (expectedRecessingTarget != 0 && (instrAddr == RECESSING_LOWERED_EXIT ||
            instrAddr == RECESSING_RAISED_EXIT || instrAddr == RECESSING_CONTINUE)) {
            require(instrAddr == expectedRecessingTarget && ctx->stack.top == recessingStackTop);
            expectedRecessingTarget = 0;
        }
''' + anchor)
    anchor = "            case OP_PACKED_VAR_READ: {"
    replace(anchor, anchor + "\n            hldRecessingHeightRead: ;")
    anchor = "            case OP_PUSH_IMMEDIATE: {"
    replace(anchor, r'''            case OP_PACKED_RECESSING_GUARDS: {
                if (fuseInstructions && ctx->runner != nullptr) {
                    const uint32_t next = tryRecessingGuards(ctx);
                    if (next != 0) {
#ifdef HLD_VERIFY_RECESSING_GUARDS
                        expectedRecessingTarget = next;
                        recessingStackTop = ctx->stack.top;
#else
                        ip = next;
                        break;
#endif
                    }
                }
                // Keep the existing packed read and literal branch in control windows.
                opword = recessingGuards.originalHeightRead;
                ip += sizeof(uint32_t);
                goto hldRecessingHeightRead;
            }
''' + anchor)
    return code
