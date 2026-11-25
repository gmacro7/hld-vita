enum { OP_PACKED_FLAME_VENT = 0x8d, HLD_FLAME_VENT_LOOP_END = 0x02f8 };

typedef struct {
    const VMContext* context;
    int32_t object, state, timer, effect;
} FlameVentGuard;

static FlameVentGuard flameVentGuard;

#if defined(HLD_COMPARE_FLAME_VENT) || defined(HLD_VERIFY_FLAME_VENT)
static unsigned flameVentSkips;
#endif

#ifdef HLD_COMPARE_FLAME_VENT
static bool useFlameVentGuard(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportFlameVentGuard(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita Flame Vent guard frame=%d enabled=%d skipped=%u\n",
            frame, (int)useFlameVentGuard(frame - 1), flameVentSkips);
    flameVentSkips = 0;
}
#endif

static void bindFlameVentGuard(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Object_FlameVent_Step_0", 2732, 0x4a054d9cu);
    if (code == nullptr || code->localsCount != 6 || code->argumentsCount != 0) return;
    uint8_t* bytes = codeBytes(ctx, code);
    const uint16_t operands[] = {0x0190, 0x0284, 0x0298, 0x02ac};
    const char* names[] = {"obj", "state", "t", "flameFX"};
    int32_t ids[sizeof(operands) / sizeof(*operands)];
    repeat(sizeof(operands) / sizeof(*operands), i) {
        const Variable* var = resolveVarDef(ctx, resolveVarOperand(bytes + operands[i]));
        if (var->varID < 0 || strcmp(var->name, names[i]) != 0) return;
        ids[i] = var->varID;
    }
    flameVentGuard = (FlameVentGuard){.context = ctx, .object = ids[0], .state = ids[1],
                                    .timer = ids[2], .effect = ids[3]};
    // Replace only the initial push of zero. All fallback and branch
    // offsets retain their original lengths.
    BinaryUtils_writeUint32(bytes + 0x011c, (uint32_t)OP_PACKED_FLAME_VENT << 24);
    logInfo("HLD: bound the Flame Vent transition guard\n");
}

static bool readFlameVentNumber(Instance* inst, int32_t id, GMLReal* out) {
    const RValue value = Instance_getSelfVar(inst, id);
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *out = RValue_toReal(value);
    return isfinite(*out);
}

bool hldFlameVentHasNoProbeCandidate(Runner* runner, Instance* caller);

static bool flameVentMayTransition(Instance* inst, GMLReal epsilon) {
    GMLReal value;
    if (!readFlameVentNumber(inst, flameVentGuard.state, &value)) return true;
    if (GMLReal_fabs(value) > epsilon) return false;
    if (!readFlameVentNumber(inst, flameVentGuard.timer, &value)) return true;
    if (value - 1.0 <= epsilon) return false;
    return !readFlameVentNumber(inst, flameVentGuard.effect, &value) ||
           GMLReal_fabs(value + 999999.0) <= epsilon;
}

static bool canSkipFlameVentLoop(VMContext* ctx) {
    Runner* runner = ctx->runner;
    Instance* inst = ctx->currentCallFrame->self;
    if (flameVentGuard.context != ctx || inst == nullptr ||
        inst->objectIndex == STRUCT_OBJECT_INDEX || runner == nullptr || runner->spatialGrid == nullptr) return false;
#ifdef HLD_COMPARE_FLAME_VENT
    if (!useFlameVentGuard(runner->frameCount)) return false;
#endif
    const GMLReal epsilon = runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    // Only ChangeState can make the probes observable. The other writes
    // are to dead locals and the loop's target, which ends at 437.
    SpatialGrid_syncGrid(runner, runner->spatialGrid);
    return !flameVentMayTransition(inst, epsilon) || hldFlameVentHasNoProbeCandidate(runner, inst);
}
