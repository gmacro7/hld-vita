enum { OP_PACKED_PURE_GUARD = 0xc9 };

typedef struct {
    const VMContext* context;
    const CodeEntry* pause;
    const CodeEntry* onscreen;
    int32_t gamePause, allPause, roomSelect;
    uint16_t screenObject;
} PureGuards;

static PureGuards pureGuards;

static void bindPureGuards(VMContext* ctx) {
    pureGuards = (PureGuards){.context = ctx};
    pureGuards.onscreen = matchHldCode(ctx, "gml_Script_onscreen", 316, 0xa0a24addu);
    const CodeEntry* pause = matchHldCode(ctx, "gml_Script_ActionIsPaused", 96, 0x14535ff1u);
    if (pause == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, pause);
    pureGuards.gamePause = resolveVarDef(ctx, resolveVarOperand(bytes + 0x04))->varID;
    pureGuards.allPause = resolveVarDef(ctx, resolveVarOperand(bytes + 0x18))->varID;
    pureGuards.roomSelect = resolveVarDef(ctx, resolveVarOperand(bytes + 0x2c))->varID;
    pureGuards.screenObject = (uint16_t)OpWord_extra((OpWord){BinaryUtils_readUint32Aligned(bytes + 0x28)});
    if (pureGuards.gamePause >= 0 && pureGuards.allPause >= 0 && pureGuards.roomSelect >= 0 &&
        pureGuards.screenObject < ctx->dataWin->objt.count) pureGuards.pause = pause;
}

static void packPureGuards(VMContext* ctx) {
    if (pureGuards.context != ctx || (pureGuards.pause == nullptr && pureGuards.onscreen == nullptr)) return;
    DataWin* data = ctx->dataWin;
    unsigned packed = 0;
    for (uint32_t i = 0; i < data->code.count; ++i) {
        const CodeEntry* entry = &data->code.entries[i];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t ip = 0; ip + 4 <= entry->length;) {
            const uint32_t start = ip;
            const OpWord call = {BinaryUtils_readUint32Aligned(bytes + ip)};
            ip += 4 + OpWord_operandSize(call);
            const bool pause = call.value == UINT32_C(0xd9020000);
            if ((!pause && call.value != UINT32_C(0xd9020003)) || entry->length - start < 16) continue;
            const CodeEntry* wanted = pause ? pureGuards.pause : pureGuards.onscreen;
            if (wanted == nullptr) continue;
            const uint32_t function = resolveFuncOperand(bytes + start + 4);
            if (function >= data->func.functionCount) continue;
            const FuncCallCache* binding = &ctx->funcCallCache[function];
            if (binding->builtin != nullptr || binding->scriptCodeIndex != wanted - data->code.entries) continue;
            if (BinaryUtils_readUint32Aligned(bytes + ip) != UINT32_C(0x07450000)) continue;
            uint32_t branch = ip + 4;
            const bool negated = BinaryUtils_readUint32Aligned(bytes + branch) == UINT32_C(0x12040000);
            if (negated) branch += 4;
            if (branch + 4 > entry->length) continue;
            const uint8_t jump = OpWord_opcode((OpWord){BinaryUtils_readUint32Aligned(bytes + branch)});
            if (jump != OP_BT && jump != OP_BF) continue;
            const uint32_t flags = negated ? UINT32_C(0x00100000) : 0;
            BinaryUtils_writeUint32(bytes + start, ((uint32_t)OP_PACKED_PURE_GUARD << 24) |
                                    flags | (call.value & UINT32_C(0x000fffff)));
            ++packed;
        }
    }
    logInfo("HLD: bound %u pause and viewport branches\n", packed);
}

static inline bool pureGuardNumber(RValue value, GMLReal* number) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *number = RValue_toReal(value);
    return isfinite(*number);
}

static bool pureGuardPause(VMContext* ctx, GMLReal epsilon, bool* result) {
    Instance* global = ctx->globalScopeInstance;
    GMLReal number;
    if (global == nullptr || !pureGuardNumber(Instance_getSelfVar(global, pureGuards.gamePause), &number)) return false;
    if (GMLReal_fabs(number - 1.0) <= epsilon) {
        *result = true;
        return true;
    }
    if (!pureGuardNumber(Instance_getSelfVar(global, pureGuards.allPause), &number)) return false;
    if (number > epsilon) {
        *result = true;
        return true;
    }
    Instance* screen = VM_findInstanceByTarget(ctx, pureGuards.screenObject);
    if (screen == nullptr || !pureGuardNumber(Instance_getSelfVar(screen, pureGuards.roomSelect), &number)) return false;
    *result = GMLReal_fabs(number - 1.0) <= epsilon;
    return true;
}

static bool pureGuardViewport(VMContext* ctx, GMLReal epsilon, bool* result) {
    const RValue* end = ctx->stack.slots + ctx->stack.top;
    GMLReal x, y, margin;
    if (!pureGuardNumber(end[-1], &x) || !pureGuardNumber(end[-2], &y) || !pureGuardNumber(end[-3], &margin)) return false;
    const GMLCamera* camera = Runner_getCameraForView(ctx->runner, 0);
    if (camera == nullptr) return false;
    const GMLReal viewX = camera->viewX, viewY = camera->viewY;
    const GMLReal right = (viewX + (GMLReal)camera->viewWidth) + margin;
    const GMLReal bottom = (viewY + (GMLReal)camera->viewHeight) + margin;
    const GMLReal left = viewX - margin, top = viewY - margin;
    if (!isfinite(right) || !isfinite(bottom) || !isfinite(left) || !isfinite(top)) return false;
    *result = right - x > epsilon && x - left > epsilon && y - top > epsilon && bottom - y > epsilon;
    return true;
}

#if defined(HLD_COMPARE_PURE_GUARDS) || defined(HLD_VERIFY_PURE_GUARDS)
static unsigned pauseGuardHits, viewportGuardHits;
#endif

#ifdef HLD_COMPARE_PURE_GUARDS
static inline bool usePureGuards(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportPureGuards(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita pure guards frame=%d enabled=%d pause=%u viewport=%u\n",
            frame, (int)usePureGuards(frame - 1), pauseGuardHits, viewportGuardHits);
    pauseGuardHits = viewportGuardHits = 0;
}
#endif

static inline void notePureGuard(int32_t argc) {
#if defined(HLD_COMPARE_PURE_GUARDS) || defined(HLD_VERIFY_PURE_GUARDS)
    unsigned* hits = argc == 0 ? &pauseGuardHits : &viewportGuardHits;
    ++*hits;
#ifdef HLD_VERIFY_PURE_GUARDS
    if (*hits == 1000) logInfo("HLD: verified 1,000 %s branch conditions\n", argc == 0 ? "pause" : "viewport");
#endif
#else
    (void)argc;
#endif
}

static bool pureGuardResult(VMContext* ctx, int32_t argc, bool* result) {
    if (pureGuards.context != ctx || ctx->runner == nullptr || ctx->stack.top < argc) return false;
#ifdef HLD_COMPARE_PURE_GUARDS
    if (!usePureGuards(ctx->runner->frameCount)) return false;
#endif
#ifdef ENABLE_VM_TRACING
    if (shlen(ctx->functionCallsToBeTraced) != 0 || shlen(ctx->varReadsToBeTraced) != 0) return false;
#endif
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0)) return false;
    return argc == 0 ? pureGuardPause(ctx, epsilon, result) : pureGuardViewport(ctx, epsilon, result);
}
