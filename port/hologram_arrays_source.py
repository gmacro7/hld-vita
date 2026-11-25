"""Allocate the two hologram offset arrays only on the opening branch."""

from runtime_verification_source import replace_once


DEFER_ARRAYS = r'''
static void deferHologramArrays(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_DrawHologram", 2188, 0xe75f7a8cu);
    if (code == nullptr || code->localsCount != 21) return;

    enum {
        ARRAY_START = 0x338,
        OPEN_CHECK = 0x3d8,
        OPEN_BRANCH = 0x3e8,
        OPEN_BODY = 0x3ec,
        SETTLED_BODY = 0x5e0,
        CHECK_LENGTH = OPEN_BODY - OPEN_CHECK,
        MOVED_BRANCH = ARRAY_START + OPEN_BRANCH - OPEN_CHECK,
    };
    uint8_t* bytes = codeBytes(ctx, code);
    uint8_t check[CHECK_LENGTH];
    memcpy(check, bytes + OPEN_CHECK, sizeof(check));
    // The two arrays hold only local constants and are read only on the
    // opening path. Move the original comparison before their allocation.
    // The resolved variable operands and both body offsets stay identical.
    memmove(bytes + ARRAY_START + CHECK_LENGTH, bytes + ARRAY_START, OPEN_CHECK - ARRAY_START);
    memcpy(bytes + ARRAY_START, check, sizeof(check));
    BinaryUtils_writeUint32(bytes + MOVED_BRANCH, ((uint32_t)OP_BF << 24) |
                            ((SETTLED_BODY - MOVED_BRANCH) / 4));
    logInfo("HLD: bound lazy hologram offset arrays\n");
}
'''


def with_hologram_arrays(code, *, launch_flag=None):
    declaration = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    helper = DEFER_ARRAYS
    if launch_flag:
        start = "static void deferHologramArrays(VMContext* ctx) {"
        helper = f"extern bool {launch_flag};\n" + replace_once(
            helper, start, start + f"\n    if (!{launch_flag}) return;")
    code = replace_once(code, declaration, helper + "\n" + declaration)
    anchor = "    packHldLiteralInstructions(ctx);"
    return replace_once(code, anchor, "    deferHologramArrays(ctx);\n" + anchor)
