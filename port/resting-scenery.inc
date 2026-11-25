typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t caseStore, height, maxHeight, hideTime;
} RestingScenery;

static RestingScenery restingScenery;

#if defined(HLD_COMPARE_RESTING_SCENERY) || defined(HLD_VERIFY_RESTING_SCENERY)
static unsigned restingSceneryCalls;
#endif

#ifdef HLD_VERIFY_RESTING_SCENERY
static const CallFrame* restingSceneryCheck;
static RValue restingSceneryExpected;
#endif

#ifdef HLD_COMPARE_RESTING_SCENERY
static bool useRestingScenery(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportRestingScenery(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita resting scenery frame=%d enabled=%d calls=%u\n",
            frame, (int)useRestingScenery(frame - 1), restingSceneryCalls);
    restingSceneryCalls = 0;
}
#endif

static void bindRestingScenery(VMContext* ctx) {
    if (stableCases.context != ctx || pureGuards.context != ctx || pureGuards.pause == nullptr) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_RecessingScenery_Step", 3068, 0x5fa3a06du);
    if (code == nullptr || code->localsCount != 12 || code->argumentsCount != 0) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint32_t caseCall = resolveFuncOperand(bytes + 0x80);
    const uint32_t pauseCall = resolveFuncOperand(bytes + 0x90);
    if (caseCall >= ctx->funcCallCacheCount || pauseCall >= ctx->funcCallCacheCount ||
        ctx->funcCallCache[caseCall].builtin != nullptr || ctx->funcCallCache[pauseCall].builtin != nullptr ||
        ctx->funcCallCache[caseCall].scriptCodeIndex != stableCases.code - ctx->dataWin->code.entries ||
        ctx->funcCallCache[pauseCall].scriptCodeIndex != pureGuards.pause - ctx->dataWin->code.entries) return;
    static const uint16_t offsets[] = {0x88, 0xa4, 0x108, 0xe0};
    static const char* const names[] = {"caseCheckStore", "height", "maxHeight", "hideT"};
    int32_t ids[4];
    repeat(4, i) {
        const uint32_t operand = resolveVarOperand(bytes + offsets[i]);
        const Variable* variable = resolveVarDef(ctx, operand);
        if (((operand >> 24) & 0xf8) != VARTYPE_NORMAL ||
            variable->varID < 0 || strcmp(variable->name, names[i]) != 0) return;
        ids[i] = variable->varID;
    }
    if (pureGuards.roomSelect == ids[0]) return;
    restingScenery = (RestingScenery){ctx, code, ids[0], ids[1], ids[2], ids[3]};
    logInfo("HLD: bound the resting scenery update\n");
}

static bool sceneryAtRest(VMContext* ctx, Instance* inst, RValue condition, GMLReal number, GMLReal epsilon) {
    bool paused;
    if (!pureGuardPause(ctx, epsilon, &paused)) return false;
    if (paused) return true;

    GMLReal height;
    if (!caseNumber(Instance_getSelfVar(inst, restingScenery.height), &height)) return false;
    if (GMLReal_fabs(height) <= epsilon && GMLReal_fabs(number) <= epsilon && inst->maskIndex == 11) {
        GMLReal hiddenTime;
        if (!caseNumber(Instance_getSelfVar(inst, restingScenery.hideTime), &hiddenTime)) return false;
        if (hiddenTime - 50.0 > epsilon) return true;
    }
    if (RValue_toBool(condition)) {
        GMLReal limit;
        return caseNumber(Instance_getSelfVar(inst, restingScenery.maxHeight), &limit) &&
            GMLReal_fabs(height) <= 16777216.0 && GMLReal_fabs(limit) <= 16777216.0 &&
            GMLReal_fabs(height - limit) <= epsilon &&
            GMLReal_fabs((GMLReal)inst->maskIndex - (GMLReal)inst->spriteIndex) <= epsilon;
    }
    return false;
}

static bool finishRestingScenery(VMContext* ctx, const CodeEntry* code) {
    if (code != restingScenery.code || ctx != restingScenery.context ||
        ctx->runner == nullptr || ctx->stack.top <= 0) return false;
#ifdef HLD_COMPARE_RESTING_SCENERY
    if (!useRestingScenery(ctx->runner->frameCount)) return false;
#endif
    Instance* inst = ctx->currentCallFrame->self;
    if (inst == nullptr || inst == ctx->globalScopeInstance || inst->objectIndex == STRUCT_OBJECT_INDEX) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    const RValue condition = ctx->stack.slots[ctx->stack.top - 1];
    GMLReal number;
    if (!(epsilon >= 0.0 && epsilon < 0.5) || !caseNumber(condition, &number) ||
        !sceneryAtRest(ctx, inst, condition, number, epsilon)) return false;

#ifdef HLD_VERIFY_RESTING_SCENERY
    require(restingSceneryCheck == nullptr);
    restingSceneryCheck = ctx->currentCallFrame;
    restingSceneryExpected = condition;
    return false;
#else
    Instance_setSelfVar(inst, restingScenery.caseStore, condition);
    --ctx->stack.top;
#ifdef HLD_COMPARE_RESTING_SCENERY
    ++restingSceneryCalls;
#endif
    return true;
#endif
}
