"""Bypass finished spawner steps while preserving each state write."""

from pathlib import Path


def with_idle_spawners(code, compare=False, check_reach=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("idle-spawner.inc").read_text()
    mode = "#define HLD_COMPARE_IDLE_SPAWNERS\n" if compare else ""
    if check_reach:
        mode += "#define HLD_CHECK_IDLE_SPAWNER_REACH\n"
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, mode + helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindIdleSpawner(ctx);\n" + anchor)
    replace("    packCopyBlocks(ctx);", "    packCopyBlocks(ctx);\n    bindIdleSpawnerCopies(ctx);")
    replace("void VM_free(VMContext* ctx) {", "void VM_free(VMContext* ctx) {\n"
            "    if (idleSpawner.context == ctx) idleSpawner = (IdleSpawner){0};")
    anchor = "    // Create a CallFrame for this script\n"
    replace(anchor, r'''#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (code == idleSpawner.code && ctx == idleSpawner.context && argCount == 0 &&
        tryIdleSpawner(ctx, selfInstance)) {
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
#endif

''' + anchor)
    return code
