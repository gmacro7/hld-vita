"""Use a shared spatial lookup for the verified miss-only shadow path."""

from pathlib import Path


def with_enemy_shadow(code, *, verify=False, compare=False, grouped=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("enemy-shadow.inc").read_text()
    if grouped:
        helper = helper.replace("static const VMContext* enemyShadowContext;",
                                "static const VMContext* enemyShadowContext;\nstatic const CodeEntry* enemyShadowStep;")
        helper = helper.replace("    // The measured runtime installs the pure pause-call opcode before this check.\n", "")
        helper = helper.replace("0x04ef6847u", "0xd21da724u")
        anchor = "    const uint32_t word = BinaryUtils_readUint32Aligned(bytes + HLD_ENEMY_SHADOW_START);"
        assert helper.count(anchor) == 1
        helper = helper.replace(anchor, r'''    enemyShadowStep = code;
}

static void installEnemyShadow(VMContext* ctx) {
    if (ctx != enemyShadowContext) return;
    uint8_t* bytes = codeBytes(ctx, enemyShadowStep);
''' + anchor + r'''
    if ((word >> 24) != OP_PACKED_VAR_READ) {
        enemyShadowContext = nullptr;
#ifdef HLD_COMPARE_ENEMY_SHADOW
        enemyShadowEnabled = false;
#endif
        logWarn("HLD: unexpected packed enemy shadow opcode %02x\n", (unsigned)(word >> 24));
        return;
    }''')
        helper = helper.replace("hldShadowProbesMiss(Runner* runner, Instance* instance, const RValue* platforms)",
                                "hldShadowProbeGroups(Runner* runner, Instance* instance, const RValue* platforms, "
                                "unsigned* groups)")
        helper = helper.replace("canSkipEnemyShadow(VMContext* ctx)", "findEnemyShadowGroups(VMContext* ctx, unsigned* groups)")
        helper = helper.replace("hldShadowProbesMiss(runner, ctx->currentCallFrame->self, &platforms)",
                                "hldShadowProbeGroups(runner, ctx->currentCallFrame->self, &platforms, groups)")
    if verify:
        helper = "#define HLD_VERIFY_ENEMY_SHADOW\n" + helper
    if compare:
        helper = "#define HLD_COMPARE_ENEMY_SHADOW\n" + helper
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, helper + "\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    if grouped:
        replace(anchor, anchor + "\n    installEnemyShadow(ctx);")
        anchor = "    packHldLiteralInstructions(ctx);"
        replace(anchor, "    bindEnemyShadow(ctx);\n" + anchor)
    else:
        replace(anchor, anchor + "\n    bindEnemyShadow(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    if (enemyShadowContext == ctx) enemyShadowContext = nullptr;")
    if verify:
        anchor = "    // Some opcodes"
        replace(anchor, "    bool verifyShadowMiss = false;\n" + anchor)
        anchor = "        OpWord opword = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};"
        replace(anchor, "        if (verifyShadowMiss) require(instrAddr != 0x255c);\n" + anchor)
    if grouped:
        anchor = "    // Some opcodes"
        replace(anchor, "    unsigned enemyShadowGroups = 0;\n    bool enemyShadowGroupsValid = false;\n" + anchor)
    anchor = "            case OP_PACKED_VAR_READ: {"
    handler = r'''            case OP_PACKED_ENEMY_SHADOW: {
                const uint32_t slot = resolveLocalSlot(ctx, (uint16_t)OpWord_extra(opword) & PACKED_VAR_ID_MASK);
                RValue value = ctx->currentCallFrame->localVars[slot];
                if (fuseInstructions && RValue_toReal(value) == 0.0 && canSkipEnemyShadow(ctx)) {
#ifdef HLD_VERIFY_ENEMY_SHADOW
                    verifyShadowMiss = true;
                    if (++enemyShadowSkips == 1000) logInfo("HLD: checked 1,000 empty enemy shadow probe groups\n");
#else
#ifdef HLD_COMPARE_ENEMY_SHADOW
                    ++enemyShadowSkips;
#endif
                    // A miss only writes dead locals, and this is the event's final loop.
                    ip = codeLength;
                    break;
#endif
                }
                value.ownsReference = false;
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                break;
            }
'''
    if grouped:
        handler = r'''            case OP_PACKED_ENEMY_SHADOW: {
                const uint32_t slot = resolveLocalSlot(ctx, (uint16_t)OpWord_extra(opword) & PACKED_VAR_ID_MASK);
                RValue value = ctx->currentCallFrame->localVars[slot];
#ifdef HLD_VERIFY_ENEMY_SHADOW
                verifyShadowMiss = false;
#endif
                if (fuseInstructions) {
                    const GMLReal index = RValue_toReal(value);
                    if (index >= 0 && index < 8 && index == (unsigned)index) {
                        const unsigned group = (unsigned)index;
                        if (!enemyShadowGroupsValid)
                            enemyShadowGroupsValid = findEnemyShadowGroups(ctx, &enemyShadowGroups);
                        const bool miss = enemyShadowGroupsValid && (enemyShadowGroups & (1u << group)) == 0;
#ifdef HLD_VERIFY_ENEMY_SHADOW
                        verifyShadowMiss = miss;
                        if (miss && ++enemyShadowSkips == 1000)
                            logInfo("HLD: checked 1,000 skipped enemy shadow platform groups\n");
                        enemyShadowGroupsValid = false;
#else
                        if (miss) {
#ifdef HLD_COMPARE_ENEMY_SHADOW
                            enemyShadowSkips += enemyShadowGroups == 0 ? 8 - group : 1;
#endif
                            // The original misses only write locals. Continue at the original increment.
                            ip = enemyShadowGroups == 0 ? codeLength : 0x2608;
                            break;
                        }
                        // The original hit path can call game code. Recheck the grid after it.
                        enemyShadowGroupsValid = false;
#endif
                    }
                }
                value.ownsReference = false;
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                break;
            }
'''
    replace(anchor, handler + anchor)
    return code


def with_enemy_shadow_probes(code, *, grouped=False):
    anchor = "static Instance** placementCellTargets("
    assert code.count(anchor) == 1
    helper = Path(__file__).with_name("enemy-shadow-probes.inc").read_text()
    helper += """
static bool placementCellObjectMatches(DataWin* data, int32_t object, int32_t target, bool reference) {
    if (target == FLAME_VENT_PROBE_TARGETS) return flameVentProbeObject(data, object, reference);
    if (target == ENEMY_SHADOW_TARGETS) return shadowProbeObject(data, object, reference);
    return reference ? VM_isObjectOrDescendant(data, object, target) : placementObjectMatches(data, object, target);
}

"""
    code = code.replace(anchor, helper + anchor)
    for mode, predicate, indent in (("false", "placementObjectMatches", "                "),
                                    ("true", "VM_isObjectOrDescendant", "            ")):
        before = ("target == FLAME_VENT_PROBE_TARGETS\n"
                  f"{indent}? flameVentProbeObject(runner->dataWin, inst->objectIndex, {mode})\n"
                  f"{indent}: {predicate}(runner->dataWin, inst->objectIndex, target)")
        assert code.count(before) == 1, before
        code = code.replace(before, f"placementCellObjectMatches(runner->dataWin, inst->objectIndex, target, {mode})")
    anchor = "// Misc stubs\nstatic RValue builtin_get_timer("
    assert code.count(anchor) == 1
    query = Path(__file__).with_name("enemy-shadow-query.inc").read_text()
    if grouped:
        query = query.replace("hldShadowProbesMiss(Runner* runner, Instance* instance, const RValue* platforms)",
                              "hldShadowProbeGroups(Runner* runner, Instance* instance, const RValue* platforms, "
                              "unsigned* groups)")
        query = query.replace("    unsigned visited = 0;", "    unsigned visited = 0;\n    *groups = 0;")
        needle = "                if (Collision_pointInsideInstanceBox(runner, candidate, px[probe], py[probe])) return false;"
        assert query.count(needle) == 1
        query = query.replace(needle, """                if (!Collision_pointInsideInstanceBox(runner, candidate, px[probe], py[probe])) continue;
                repeat(sizeof(enemyShadowTargets) / sizeof(*enemyShadowTargets), group) {
                    if (placementObjectMatches(runner->dataWin, candidate->objectIndex, enemyShadowTargets[group]))
                        *groups |= 1u << group;
                }
                break;""")
    return code.replace(anchor, query + "\n" + anchor)
