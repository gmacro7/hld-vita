"""Keep numeric switch comparisons on one stack value and dispatch once."""

from pathlib import Path


def with_switch_chains(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("switch-chain.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_SWITCH_CHAINS\n" + helper
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, helper + "\n" + anchor)
    replace("    packHldArguments(ctx);", "    packHldArguments(ctx);\n    packSwitchChains(ctx);")

    anchor = "            case OP_DUP:\n"
    replace(anchor, r'''            case OP_PACKED_SWITCH: {
                uint32_t resume = instrAddr;
                if (fuseInstructions && trySwitchChain(ctx, bytecodeBase, (uint16_t)OpWord_extra(opword), &resume)) {
                    ip = resume;
                } else {
                    handleDup(ctx, (OpWord){SWITCH_DUP_WORD});
                }
                break;
            }
''' + anchor)
    return code
