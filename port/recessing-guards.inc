enum {
    OP_PACKED_RECESSING_GUARDS = 0x8e,
    RECESSING_GUARDS_START = 0x00a0,
    RECESSING_LOWERED_EXIT = 0x00f8,
    RECESSING_RAISED_EXIT = 0x0144,
    RECESSING_CONTINUE = 0x0148
};

typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    OpWord originalHeightRead;
    int32_t height, maxHeight, caseStore, hideTimer;
} RecessingGuards;

static RecessingGuards recessingGuards;
static unsigned recessingLowered, recessingRaised, recessingContinued;

static bool useRecessingGuards(int frame) {
#ifdef HLD_COMPARE_RECESSING_GUARDS
    return frame >= 180 && (frame / 180) % 2 != 0;
#else
    (void)frame;
    return true;
#endif
}

#ifdef HLD_COMPARE_RECESSING_GUARDS
void hldReportRecessingGuards(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita recessing guards frame=%d enabled=%d lowered=%u raised=%u continued=%u\n", frame,
            (int)useRecessingGuards(frame - 1), recessingLowered, recessingRaised, recessingContinued);
    recessingLowered = recessingRaised = recessingContinued = 0;
}
#endif

static void bindRecessingGuards(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_RecessingScenery_Step", 3068, 0x5fa3a06du);
    if (code == nullptr || code->localsCount != 12 || code->argumentsCount != 0) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint16_t offsets[] = {0x00a4, 0x0108, 0x00b8, 0x00e0};
    const char* names[] = {"height", "maxHeight", "caseCheckStore", "hideT"};
    int32_t ids[sizeof(offsets) / sizeof(*offsets)];
    repeat(sizeof(offsets) / sizeof(*offsets), i) {
        const Variable* variable = resolveVarDef(ctx, resolveVarOperand(bytes + offsets[i]));
        if (variable->varID < 0 || strcmp(variable->name, names[i]) != 0) return;
        ids[i] = variable->varID;
    }
    recessingGuards = (RecessingGuards){.context = ctx, .code = code,
        .height = ids[0], .maxHeight = ids[1], .caseStore = ids[2], .hideTimer = ids[3]};
}

static void packRecessingGuards(VMContext* ctx) {
    if (recessingGuards.context != ctx || recessingGuards.code == nullptr) return;
    uint8_t* bytes = codeBytes(ctx, recessingGuards.code);
    const OpWord original = {BinaryUtils_readUint32Aligned(bytes + RECESSING_GUARDS_START)};
    if (OpWord_opcode(original) != OP_PACKED_VAR_READ) return;
    recessingGuards.originalHeightRead = original;
    BinaryUtils_writeUint32(bytes + RECESSING_GUARDS_START, (uint32_t)OP_PACKED_RECESSING_GUARDS << 24);
    logInfo("HLD: bound the recessing scenery terminal guards\n");
}

static bool recessingNumber(RValue value, GMLReal* out) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32 && value.type != RVALUE_BOOL) return false;
    *out = RValue_toReal(value);
    return isfinite(*out);
}

static uint32_t tryRecessingGuards(VMContext* ctx) {
    Instance* inst = ctx->currentCallFrame->self;
    if (inst == nullptr || inst->objectIndex == STRUCT_OBJECT_INDEX ||
        !useRecessingGuards(ctx->runner->frameCount)) return 0;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return 0;
    GMLReal height, limit;
    if (!recessingNumber(Instance_getSelfVar(inst, recessingGuards.height), &height)) return 0;
    const RValue condition = Instance_getSelfVar(inst, recessingGuards.caseStore);
    uint32_t target = RECESSING_CONTINUE;
    if (GMLReal_fabs(height) <= epsilon) {
        GMLReal caseValue;
        if (!recessingNumber(condition, &caseValue)) return 0;
        if (GMLReal_fabs(caseValue) <= epsilon && inst->maskIndex == 11) {
            GMLReal hiddenTime;
            if (!recessingNumber(Instance_getSelfVar(inst, recessingGuards.hideTimer), &hiddenTime)) return 0;
            if (hiddenTime - 50.0 > epsilon) target = RECESSING_LOWERED_EXIT;
        }
    }
    if (target == RECESSING_CONTINUE) {
        if (!recessingNumber(Instance_getSelfVar(inst, recessingGuards.maxHeight), &limit)) return 0;
        if (GMLReal_fabs(height - limit) <= epsilon && RValue_toBool(condition) &&
            GMLReal_fabs((GMLReal)inst->maskIndex - (GMLReal)inst->spriteIndex) <= epsilon)
            target = RECESSING_RAISED_EXIT;
    }
    if (target == RECESSING_LOWERED_EXIT) ++recessingLowered;
    else if (target == RECESSING_RAISED_EXIT) ++recessingRaised;
    else ++recessingContinued;
#ifndef PLATFORM_VITA
    if (recessingLowered + recessingRaised + recessingContinued == 1000) {
        logInfo("HLD: exercised 1000 recessing guards, lowered=%u raised=%u continued=%u\n",
                recessingLowered, recessingRaised, recessingContinued);
    }
#endif
    return target;
}
