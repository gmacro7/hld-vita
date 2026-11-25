"""Use one spatial query at the original mine trigger boundary."""

from runtime_verification_source import replace_once


MINE_TRIGGER = r'''
enum { OP_PACKED_PHASE_MINE_TRIGGER = 0x8f };

bool hldPhaseMineTrigger(VMContext* ctx, bool* triggered);

static void bindPhaseMineTrigger(VMContext* ctx) {
    const CodeEntry* mine = matchHldCode(ctx, "gml_Object_PhaseMine_Step_0", 1864, 0xff2ea8d4u);
    if (mine == nullptr || ctx->dataWin->objt.count <= 428) return;
    uint8_t* bytes = codeBytes(ctx, mine);
    const uint8_t* operand = bytes + 0xf4;
    const uint32_t function = resolveFuncOperand(operand);
    const BuiltinFunc builtin = VM_findBuiltin(ctx, "place_meeting");
    if (builtin == nullptr || function >= ctx->funcCallCacheCount ||
        ctx->funcCallCache[function].builtin != (void*)builtin ||
        ctx->funcCallCache[function].scriptCodeIndex >= 0) return;
    BinaryUtils_writeUint32(bytes + 0xd8, (uint32_t)OP_PACKED_PHASE_MINE_TRIGGER << 24 | 0x0f01a3u);
    logInfo("HLD: bound the combined phase mine trigger query\n");
}

'''

MINE_COLLISION = r'''
bool hldPhaseMineTrigger(VMContext* ctx, bool* triggered) {
    Runner* runner = ctx->runner;
    Instance* caller = ctx->currentCallFrame->self;
    if (runner == nullptr || runner->spatialGrid == nullptr || caller == nullptr ||
        !isfinite(caller->x) || !isfinite(caller->y)) return false;

    SpatialGrid* grid = runner->spatialGrid;
    SpatialGrid_syncGrid(runner, grid);
    const InstanceBBox bounds = Collision_computeBBox(runner, caller);
    *triggered = false;
    if (!bounds.valid) return true;
    if (!isfinite(bounds.left) || !isfinite(bounds.top) ||
        !isfinite(bounds.right) || !isfinite(bounds.bottom)) return false;

    const SpatialGridQuery query = SpatialGrid_prepareQuery(
        runner, bounds.left, bounds.top, bounds.right, bounds.bottom, INSTANCE_ALL);
    for (int32_t gx = query.range.minGridX; gx <= query.range.maxGridX && !*triggered; ++gx) {
        for (int32_t gy = query.range.minGridY; gy <= query.range.maxGridY && !*triggered; ++gy) {
            const int32_t index = SpatialGrid_cellIndex(grid, gx, gy);
            Instance** cell = grid->grid[index];
            if (arrlen(cell) == 0) continue;
            Instance** candidates = placementCellTargets(runner, cell, index, PHASE_MINE_PROBE_TARGETS);
            repeat(arrlen(candidates), i) {
                Instance* other = candidates[i];
                if (!other->active || other == caller || other->lastCollisionQueryId == query.queryId) continue;
                other->lastCollisionQueryId = query.queryId;
                const InstanceBBox otherBounds = Collision_computeBBox(runner, other);
                if (!otherBounds.valid || bounds.left >= otherBounds.right || otherBounds.left >= bounds.right ||
                    bounds.top >= otherBounds.bottom || otherBounds.top >= bounds.bottom) continue;
                if (Collision_instancesOverlapPrecise(runner, caller, other, bounds, otherBounds)) {
                    *triggered = true;
                    break;
                }
            }
        }
    }

#ifdef HLD_VERIFY_PHASE_MINE
    RValue args[] = {RValue_makeReal(caller->x), RValue_makeReal(caller->y), RValue_makeInt32(0)};
    bool original = false;
    for (size_t i = 0; i < sizeof(phaseMineTargets) / sizeof(*phaseMineTargets) && !original; ++i) {
        args[2] = RValue_makeInt32(phaseMineTargets[i]);
        original = RValue_toBool(builtin_place_meeting(ctx, args, 3));
    }
    require(original == *triggered);
    static unsigned checked, hits;
    if (++checked == 1000) logInfo("HLD: checked 1,000 phase mine trigger queries\n");
    if (*triggered && ++hits == 10) logInfo("HLD: checked 10 positive phase mine triggers\n");
#endif
    return true;
}

'''


def with_phase_mine_trigger(code, *, launch_flag=None):
    declaration = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    helper = MINE_TRIGGER
    if launch_flag:
        helper = f"extern bool {launch_flag};\n" + helper
        anchor = "static void bindPhaseMineTrigger(VMContext* ctx) {"
        helper = replace_once(helper, anchor, anchor + f"\n    if (!{launch_flag}) return;")
    code = replace_once(code, declaration, helper + declaration)
    anchor = "    packHldMathInstructions(ctx);"
    code = replace_once(code, anchor, anchor + "\n    bindPhaseMineTrigger(ctx);")
    anchor = "            case OP_PACKED_LITERAL_VALUE:"
    return replace_once(code, anchor, r'''            case OP_PACKED_PHASE_MINE_TRIGGER: {
                bool triggered;
                VM_SYNC_IP();
                if (hldPhaseMineTrigger(ctx, &triggered)) {
                    // The original ChangeState runs at 0x188 when triggered.
                    // The switch selector is still on the stack at either target.
                    ip = triggered ? 0x188 : 0x744;
                    break;
                }
                stackPushTyped(ctx, RValue_makeInt32(OpWord_extra(opword)), GML_TYPE_VARIABLE);
                ip += 4;
                break;
            }
''' + anchor)


def with_phase_mine_collision(code, *, verify=False, launch_flag=None):
    anchor = "enum { FLAME_VENT_PROBE_TARGETS = INT32_MIN };"
    code = replace_once(code, anchor, anchor + r'''
enum { PHASE_MINE_PROBE_TARGETS = INT32_MIN + 2 };
static const int32_t phaseMineTargets[] = {419, 17, 428};

static bool phaseMineProbeObject(DataWin* data, int32_t object, bool reference) {
    repeat(sizeof(phaseMineTargets) / sizeof(*phaseMineTargets), i) {
        const int32_t target = phaseMineTargets[i];
        if (reference ? VM_isObjectOrDescendant(data, object, target) : placementObjectMatches(data, object, target))
            return true;
    }
    return false;
}
''')
    anchor = "    if (target == FLAME_VENT_PROBE_TARGETS) return flameVentProbeObject(data, object, reference);"
    if anchor in code:
        code = replace_once(code, anchor, anchor + "\n"
                            "    if (target == PHASE_MINE_PROBE_TARGETS) return phaseMineProbeObject(data, object, reference);")
    else:
        for reference in ("false", "true"):
            anchor = f"? flameVentProbeObject(runner->dataWin, inst->objectIndex, {reference})"
            code = replace_once(code, anchor, anchor + f'''
                : target == PHASE_MINE_PROBE_TARGETS ? phaseMineProbeObject(runner->dataWin, inst->objectIndex, {reference})''')
    anchor = "// GameMaker rounds the coordinates when in collision compatibility mode"
    helper = "#define HLD_VERIFY_PHASE_MINE\n" + MINE_COLLISION if verify else MINE_COLLISION
    if launch_flag:
        helper = f'''extern bool {launch_flag};
static unsigned phaseMineCalls, phaseMineHits;

void hldReportPhaseMineTrigger(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita phase mine triggers frame=%d enabled=%d calls=%u hits=%u\\n",
            frame, (int){launch_flag}, phaseMineCalls, phaseMineHits);
    phaseMineCalls = phaseMineHits = 0;
}}
''' + helper
        helper = replace_once(helper, "#ifdef HLD_VERIFY_PHASE_MINE\n    RValue args[]",
                              "    ++phaseMineCalls;\n    phaseMineHits += *triggered;\n\n"
                              "#ifdef HLD_VERIFY_PHASE_MINE\n    RValue args[]")
    return replace_once(code, anchor, helper + anchor)
