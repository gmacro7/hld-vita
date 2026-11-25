"""Bind the shared scenery update's fields before the per-object pass."""

from pathlib import Path


def with_scenery_pass(code, *, verify=False, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("scenery-pass.inc").read_text()
    if verify:
        helper = "#define HLD_VERIFY_SCENERY_PASS\n" + helper
    if compare:
        helper = "#define HLD_COMPARE_SCENERY_PASS\n" + helper
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, anchor.replace("hldSceneryCull(", "hldSceneryCullReference("))
    anchor = "static bool hldSceneryCanSkipUpdate(VMContext* ctx) {"
    replace(anchor, helper + "\n" + anchor)
    anchor = "                ctx->hldRoomTimeVarId = resolveVarDef(ctx, resolveVarOperand(scenery + 0x050c))->varID;"
    replace(anchor, anchor + "\n                bindSceneryPass(ctx, scenery, codeBytes(ctx, ctx->hldSceneryVisibilityCode));")
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    if (sceneryPass.context == ctx) sceneryPass = (SceneryPass){0};")
    return code
