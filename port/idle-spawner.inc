typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t inWall, canEdit, caseLink, canSpawn, spawned, spawnedAmount, spawnAmount, child;
    uint32_t block;
    bool copyBound;
} IdleSpawner;

static IdleSpawner idleSpawner;

static void bindIdleSpawner(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Object_Spawner_Step_0", 2784, 0x5b7fb46eu);
    if (code == nullptr) return;
    // The visibility and pause helpers perform no writes. Only omit their
    // evaluation when the loaded versions match the audited routines.
    if (matchHldCode(ctx, "gml_Script_onscreen", 316, 0xa0a24addu) == nullptr ||
        matchHldCode(ctx, "gml_Script_ActionIsPaused", 96, 0x14535ff1u) == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint16_t operands[] = {0x08, 0x10, 0x188, 0x1bc, 0x318, 0x8c8, 0x8d0, 0x940};
    int32_t ids[sizeof(operands) / sizeof(*operands)];
    repeat(sizeof(operands) / sizeof(*operands), i) {
        ids[i] = resolveVarDef(ctx, resolveVarOperand(bytes + operands[i]))->varID;
        if (ids[i] < 0) return;
    }
    idleSpawner = (IdleSpawner){.context = ctx, .code = code, .inWall = ids[0], .canEdit = ids[1],
        .caseLink = ids[2], .canSpawn = ids[3], .spawned = ids[4], .spawnedAmount = ids[5],
        .spawnAmount = ids[6], .child = ids[7]};
}

static void bindIdleSpawnerCopies(VMContext* ctx) {
    if (idleSpawner.context != ctx) return;
    const OpWord env = {BinaryUtils_readUint32Aligned(codeBytes(ctx, idleSpawner.code) + 0x948)};
    if (OpWord_opcode(env) != OP_PACKED_COPY_BLOCK) return;
    idleSpawner.block = (env.value >> COPY_BLOCK_BITS) & COPY_BLOCK_MASK;
    idleSpawner.copyBound = true;
    logInfo("HLD: bound the finished spawner path and its ordered field copies\n");
}

#ifdef HLD_COMPARE_IDLE_SPAWNERS
static unsigned idleSpawnerSteps;

static inline bool useIdleSpawner(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportIdleSpawners(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita idle spawners frame=%d enabled=%d steps=%u\n",
            frame, (int)useIdleSpawner(frame - 1), idleSpawnerSteps);
    idleSpawnerSteps = 0;
}
#elif defined(HLD_CHECK_IDLE_SPAWNER_REACH)
static unsigned idleSpawnerSteps;
#endif

static inline bool spawnerNumber(Instance* inst, int32_t id, GMLReal* out) {
    const RValue value = Instance_getSelfVar(inst, id);
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *out = RValue_toReal(value);
    return isfinite(*out);
}

static bool tryIdleSpawner(VMContext* ctx, Instance* inst) {
    if (!idleSpawner.copyBound || copyBlockContext != ctx || ctx->runner == nullptr ||
        inst == nullptr || inst->objectIndex == STRUCT_OBJECT_INDEX || ctx->globalScopeInstance == nullptr) return false;
#ifdef HLD_COMPARE_IDLE_SPAWNERS
    if (!useIdleSpawner(ctx->runner->frameCount)) return false;
#endif
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5) ||
        RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, idleSpawner.canEdit))) return false;

    // Keep the movement and spawn paths in the interpreter. Doing this
    // before any write lets changes in the guard take effect on this step.
    if (!(GMLReal_fabs((GMLReal)inst->x - inst->xprevious) <= epsilon &&
          GMLReal_fabs((GMLReal)inst->y - inst->yprevious) <= epsilon)) return false;
    GMLReal link, spawned, already, total, target;
    if (!spawnerNumber(inst, idleSpawner.caseLink, &link) || GMLReal_fabs(link + 999999.0) > epsilon ||
        !spawnerNumber(inst, idleSpawner.spawned, &spawned) || GMLReal_fabs(spawned) <= epsilon ||
        !spawnerNumber(inst, idleSpawner.spawnedAmount, &already) ||
        !spawnerNumber(inst, idleSpawner.spawnAmount, &total) || total - already > epsilon ||
        !spawnerNumber(inst, idleSpawner.child, &target) || target < INSTANCE_ID_BASE || target > INT32_MAX) return false;
    const int32_t id = (int32_t)target;
    if ((GMLReal)id != target) return false;
    Instance* child = hmget(ctx->runner->instancesById, id);
    if (child != nullptr && child->objectIndex == STRUCT_OBJECT_INDEX) return false;

    Instance_setSelfVar(inst, idleSpawner.inWall, RValue_makeReal(0.0));
    VMBuiltins_setVariable(ctx, inst, BUILTIN_VAR_VISIBLE, "visible", RValue_makeBool(false), -1);
    Instance_setSelfVar(inst, idleSpawner.canSpawn, RValue_makeReal(1.0));
    if (child != nullptr && child->active) copyInstanceFields(ctx, inst, child, idleSpawner.block);
#ifdef HLD_COMPARE_IDLE_SPAWNERS
    ++idleSpawnerSteps;
#elif defined(HLD_CHECK_IDLE_SPAWNER_REACH)
    if (++idleSpawnerSteps == 1000) logInfo("HLD: executed 1,000 finished spawner steps directly\n");
#endif
    return true;
}
