"""Skip the Flame Vent probe loop when its transition cannot fire."""

from pathlib import Path


def with_flame_vent_guard(code, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("flame-vent.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_FLAME_VENT\n" + helper
    if verify:
        helper = "#define HLD_VERIFY_FLAME_VENT\n" + helper
    signature = "static const CodeEntry* matchHldCode(VMContext* ctx, const char* name, uint32_t length, uint32_t expected)"
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, signature + ";\n\n" + helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindFlameVentGuard(ctx);\n" + anchor)
    replace("void VM_free(VMContext* ctx) {", "void VM_free(VMContext* ctx) {\n"
            "    if (flameVentGuard.context == ctx) flameVentGuard = (FlameVentGuard){0};")
    if verify:
        anchor = "    // Some opcodes"
        replace(anchor, "    int32_t flameVentStack = -1;\n" + anchor)
        anchor = "        OpWord opword = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};"
        replace(anchor, r'''        if (flameVentStack >= 0) {
            require(instrAddr != 0x02d0);
            if (instrAddr == HLD_FLAME_VENT_LOOP_END) {
                RValue obj = Instance_getSelfVar(ctx->currentCallFrame->self, flameVentGuard.object);
                require(obj.type == RVALUE_REAL && obj.real == 437.0 && ctx->stack.top == flameVentStack);
                if (++flameVentSkips == 1000) logInfo("HLD: checked 1,000 unreachable Flame Vent transitions\n");
                flameVentStack = -1;
            }
        }
''' + anchor)
    anchor = "            case OP_PUSH_IMMEDIATE: {"
    replace(anchor, r'''            case OP_PACKED_FLAME_VENT: {
                if (fuseInstructions && canSkipFlameVentLoop(ctx)) {
#ifdef HLD_VERIFY_FLAME_VENT
                    flameVentStack = ctx->stack.top;
#else
                    Instance_setSelfVar(ctx->currentCallFrame->self, flameVentGuard.object, RValue_makeReal(437.0));
                    ip = HLD_FLAME_VENT_LOOP_END;
#ifdef HLD_COMPARE_FLAME_VENT
                    ++flameVentSkips;
#endif
                    break;
#endif
                }
                handlePushImmediate(ctx, (OpWord){0x840f0000u});
                break;
            }
''' + anchor)
    return code


def with_flame_vent_probes(code):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    anchor = "static Instance** placementCellTargets("
    replace(anchor, r'''// Negative targets are never used as object filters by the placement builtins.
enum { FLAME_VENT_PROBE_TARGETS = INT32_MIN };

static bool flameVentProbeObject(DataWin* data, int32_t object, bool reference) {
    static const int32_t targets[] = {419, 17, 240, 437, 237};
    for (size_t i = 0; i < sizeof(targets) / sizeof(*targets); i++) {
        const bool matches = reference ? VM_isObjectOrDescendant(data, object, targets[i])
                                       : placementObjectMatches(data, object, targets[i]);
        if (matches) return true;
    }
    return false;
}

''' + anchor)
    anchor = "            if (placementObjectMatches(runner->dataWin, inst->objectIndex, target)) arrput(entry->matches, inst);"
    replace(anchor, """            const bool matches = target == FLAME_VENT_PROBE_TARGETS
                ? flameVentProbeObject(runner->dataWin, inst->objectIndex, false)
                : placementObjectMatches(runner->dataWin, inst->objectIndex, target);
            if (matches) arrput(entry->matches, inst);""")
    anchor = "        if (!VM_isObjectOrDescendant(runner->dataWin, inst->objectIndex, target)) continue;"
    start = code.index("#ifdef HLD_VERIFY_PLACEMENT_CELLS")
    end = code.index("    return entry->matches;", start)
    check = code[start:end]
    assert check.count(anchor) == 1
    replace(check, check.replace(anchor, """        const bool matches = target == FLAME_VENT_PROBE_TARGETS
            ? flameVentProbeObject(runner->dataWin, inst->objectIndex, true)
            : VM_isObjectOrDescendant(runner->dataWin, inst->objectIndex, target);
        if (!matches) continue;"""))
    anchor = "static RValue builtin_place_meeting("
    replace(anchor, Path(__file__).with_name("flame-vent-probes.inc").read_text() + "\n" + anchor)
    return code
