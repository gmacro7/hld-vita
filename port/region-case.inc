typedef struct {
    const VMContext* context;
    int32_t target, width, height;
} RegionCase;

static RegionCase regionCase;

#ifdef HLD_COMPARE_REGION_CASES
static unsigned regionMisses;

static inline bool useRegionCases(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportRegionCases(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita region cases frame=%d enabled=%d misses=%u\n",
            frame, (int)useRegionCases(frame - 1), regionMisses);
    regionMisses = 0;
}
#endif

static void bindRegionCase(VMContext* ctx) {
    if (stableCases.context != ctx) return;
    const CodeEntry* rectangle = matchHldCode(ctx, "gml_Script_PointInRegion", 276, 0x3ad4ebe2u);
    if (rectangle == nullptr || rectangle->localsCount != 7) return;

    const uint8_t* code = codeBytes(ctx, stableCases.code);
    const uint32_t call = resolveFuncOperand(code + 0x03a0);
    if (call >= ctx->funcCallCacheCount || ctx->funcCallCache[call].builtin != nullptr ||
        ctx->funcCallCache[call].scriptCodeIndex != rectangle - ctx->dataWin->code.entries) return;

    const int32_t target = resolveVarDef(ctx, resolveVarOperand(code + 0x0348))->varID;
    const int32_t height = resolveVarDef(ctx, resolveVarOperand(code + 0x0360))->varID;
    const int32_t width = resolveVarDef(ctx, resolveVarOperand(code + 0x0374))->varID;
    if (target < 0 || width < 0 || height < 0) return;
    regionCase = (RegionCase){ctx, target, width, height};
    logInfo("HLD: bound the direct region case checks\n");
}

static inline bool outsideCaseRegion(const Instance* target, GMLReal x, GMLReal y,
                                     GMLReal right, GMLReal bottom, GMLReal epsilon) {
    const GMLReal px = target->x, py = target->y;
    if (!isfinite(px) || !isfinite(py)) return false;
    return !(px - x > epsilon && px - right < -epsilon && py - y > epsilon && py - bottom < -epsilon);
}

static bool caseRegionMiss(VMContext* ctx, Instance* inst, GMLReal epsilon) {
    if (regionCase.context != ctx || inst->objectIndex != 58) return false;
#ifdef HLD_COMPARE_REGION_CASES
    if (!useRegionCases(ctx->runner->frameCount)) return false;
#endif

    const RValue reference = Instance_getSelfVar(inst, regionCase.target);
    int32_t target;
    if (reference.type == RVALUE_INT32) {
        target = reference.int32;
    } else {
        if (reference.type != RVALUE_REAL || !(reference.real >= 0 && (double)reference.real < 2147483648.0))
            return false;
        target = (int32_t)reference.real;
        if ((GMLReal)target != reference.real) return false;
    }
    GMLReal width, height;
    if (target < 0 || !caseNumber(Instance_getSelfVar(inst, regionCase.width), &width) ||
        !caseNumber(Instance_getSelfVar(inst, regionCase.height), &height)) return false;
    const GMLReal x = inst->x, y = inst->y, right = x + width, bottom = y + height;
    if (!isfinite(x) || !isfinite(y) || !isfinite(right) || !isfinite(bottom)) return false;

    Runner* runner = ctx->runner;
    if (target >= INSTANCE_ID_BASE) {
        Instance* actor = hmget(runner->instancesById, target);
        if (actor != nullptr && actor->active && !outsideCaseRegion(actor, x, y, right, bottom, epsilon)) return false;
    } else {
        if ((uint32_t)target >= ctx->dataWin->objt.count || runner->instancesByObject == nullptr) return false;
        Instance** actors = runner->instancesByObject[target];
        repeat(arrlen(actors), i) {
            const Instance* actor = actors[i];
            if (actor->active && !outsideCaseRegion(actor, x, y, right, bottom, epsilon)) return false;
        }
    }

#ifdef HLD_VERIFY_STABLE_CASES
    static unsigned checkedRegionCases;
    if (++checkedRegionCases == 10000) logInfo("HLD: checked 10,000 empty region cases\n");
#endif
#ifdef HLD_COMPARE_REGION_CASES
    ++regionMisses;
#endif
    return true;
}
