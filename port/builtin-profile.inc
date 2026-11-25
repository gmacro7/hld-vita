typedef struct {
    uint64_t elapsed;
    uint64_t flameVentElapsed;
    unsigned calls;
    unsigned flameVentCalls;
} VMBuiltinSample;

static VMBuiltinSample* vmBuiltinTotals;
static int32_t vmFlameVentCode = -1;

static RValue profileBuiltinCall(VMContext* ctx, BuiltinFunc builtin, RValue* args,
                                 int32_t argCount, uint32_t funcIndex) {
    if (ctx->runner == nullptr || ctx->runner->frameCount != vmSampleLastFrame || vmSampleFrames == 0)
        return builtin(ctx, args, argCount);
    if (vmBuiltinTotals == nullptr) {
        vmBuiltinTotals = safeCalloc(ctx->dataWin->func.functionCount, sizeof(*vmBuiltinTotals));
        ptrdiff_t slot = shgeti(ctx->codeIndexByName, "gml_Object_FlameVent_Step_0");
        if (slot >= 0) vmFlameVentCode = ctx->codeIndexByName[slot].value;
    }
    const bool flameVent = ctx->currentCallFrame != nullptr && ctx->currentCallFrame->codeIndex == vmFlameVentCode;
    const uint64_t childTime = vmSampleChildTime;
    const uint64_t start = nowNanos();
    RValue result = builtin(ctx, args, argCount);
    const uint64_t elapsed = nowNanos() - start;
    const uint64_t nested = vmSampleChildTime - childTime;
    const uint64_t self = elapsed > nested ? elapsed - nested : 0;
#ifdef HLD_PROFILE_VM_DISPATCH
    vmSampleBuiltinTime += self;
#endif
    VMBuiltinSample* total = &vmBuiltinTotals[funcIndex];
    total->elapsed += self;
    ++total->calls;
    if (flameVent) {
        total->flameVentElapsed += self;
        ++total->flameVentCalls;
    }
    return result;
}

static void reportVMBuiltins(VMContext* ctx, double msPerFrame) {
    if (vmBuiltinTotals == nullptr) return;
    const uint32_t count = ctx->dataWin->func.functionCount;
    for (unsigned rank = 0; rank < 12; ++rank) {
        uint32_t best = 0;
        for (uint32_t index = 1; index < count; ++index) {
            if (vmBuiltinTotals[index].elapsed > vmBuiltinTotals[best].elapsed) best = index;
        }
        VMBuiltinSample* total = &vmBuiltinTotals[best];
        if (total->elapsed == 0) break;
        logInfo("Vita builtin sample frame=%d frames=%u calls=%u self_ms=%.3f name=%s\n",
                vmSampleLastFrame, vmSampleFrames, total->calls,
                total->elapsed * msPerFrame, ctx->dataWin->func.functions[best].name);
        total->elapsed = 0;
    }
    for (uint32_t index = 0; index < count; ++index) {
        const VMBuiltinSample* total = &vmBuiltinTotals[index];
        if (total->flameVentCalls != 0) {
            logInfo("Vita FlameVent builtin frame=%d frames=%u calls=%u self_ms=%.3f name=%s\n",
                    vmSampleLastFrame, vmSampleFrames, total->flameVentCalls,
                    total->flameVentElapsed * msPerFrame, ctx->dataWin->func.functions[index].name);
        }
    }
    memset(vmBuiltinTotals, 0, count * sizeof(*vmBuiltinTotals));
}
