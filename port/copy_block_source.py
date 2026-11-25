"""Predecode straight-line instance field copies inside single-target with blocks."""

from pathlib import Path


def with_copy_blocks(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("copy-block.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_COPY_BLOCKS\n" + helper
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, helper + "\n" + anchor)
    replace("    packHldArguments(ctx);", "    packHldArguments(ctx);\n    packCopyBlocks(ctx);")
    replace("void VM_free(VMContext* ctx) {", "void VM_free(VMContext* ctx) {\n"
            "    if (copyBlockContext == ctx) releaseCopyBlocks();")
    anchor = "            case OP_PUSH_ENV:\n"
    replace(anchor, r'''            case OP_PACKED_COPY_BLOCK: {
                const uint32_t distance = opword.value & COPY_BLOCK_MASK;
                if (fuseInstructions && tryCopyBlock(ctx, (opword.value >> COPY_BLOCK_BITS) & COPY_BLOCK_MASK)) {
                    ip = instrAddr + (distance + 1) * sizeof(uint32_t);
                } else {
                    VM_SYNC_IP();
                    handlePushEnv(ctx, (OpWord){((uint32_t)OP_PUSH_ENV << 24) | distance}, instrAddr);
                    VM_RELOAD_IP();
                }
                break;
            }
''' + anchor)
    return code
