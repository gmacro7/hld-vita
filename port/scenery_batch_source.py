"""Advance through consecutive scenery instances whose updates can be skipped."""

from pathlib import Path


def with_scenery_batches(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("scenery-batch.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_SCENERY_BATCHES\n" + helper
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    replace(anchor, helper + "\n" + anchor)
    before = """                if (instrAddr == HLD_SCENERY_CULL_START && useHldSceneryCull && hldSceneryCull(ctx, bytecodeBase)) {
                    ip = hldSceneryCanSkipUpdate(ctx) ? HLD_SCENERY_NEXT_INSTANCE : HLD_SCENERY_CULL_END;
                    break;
                }"""
    after = """                if (instrAddr == HLD_SCENERY_CULL_START && useHldSceneryCull &&
                    advanceSceneryBatch(ctx, bytecodeBase, &ip)) break;"""
    replace(before, after)
    return code
