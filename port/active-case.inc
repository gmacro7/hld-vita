typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    uint32_t selectorSlot;
} ActiveCase;

static ActiveCase activeCase;

#if defined(HLD_COMPARE_ACTIVE_CASE) || defined(HLD_VERIFY_ACTIVE_CASE)
static unsigned activeCaseEntries, activeCaseReturns;
#endif

#ifdef HLD_VERIFY_ACTIVE_CASE
static const CallFrame* activeCaseEntryCheck;
static RValue activeCaseSelector;
static const CallFrame* activeCaseReturnCheck;
#endif

#ifdef HLD_COMPARE_ACTIVE_CASE
static bool useActiveCase(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportActiveCase(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita active cases frame=%d enabled=%d entries=%u returns=%u\n",
            frame, (int)useActiveCase(frame - 1), activeCaseEntries, activeCaseReturns);
    activeCaseEntries = activeCaseReturns = 0;
}
#endif

static void bindActiveCase(VMContext* ctx) {
    if (stableCases.context != ctx || stableCases.code == nullptr || stableCases.code->localsCount != 5) return;
    const CodeEntry* code = stableCases.code;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint32_t selector = resolveVarOperand(bytes + 0xf8);
    const Variable* variable = resolveVarDef(ctx, selector);
    if (((selector >> 24) & 0xf8) != VARTYPE_NORMAL || variable->varID != 2 ||
        strcmp(variable->name, "_case") != 0) return;

    const uint32_t event = resolveFuncOperand(bytes + ACTIVE_CASE_EVENT_CALL + 4);
    const BuiltinFunc eventUser = VM_findBuiltin(ctx, "event_user");
    if (event >= ctx->funcCallCacheCount || eventUser == nullptr ||
        ctx->funcCallCache[event].builtin != (void*)eventUser ||
        ctx->funcCallCache[event].scriptCodeIndex >= 0) return;
    activeCase = (ActiveCase){ctx, code, (uint32_t)variable->varID};
}

static void checkActiveCaseOpcodes(VMContext* ctx) {
    if (activeCase.context != ctx) return;
    const uint8_t* bytes = codeBytes(ctx, activeCase.code);
    const OpWord entry = {BinaryUtils_readUint32Aligned(bytes)};
    const OpWord event = {BinaryUtils_readUint32Aligned(bytes + ACTIVE_CASE_EVENT_CALL)};
    if (OpWord_opcode(entry) != OP_PACKED_LITERAL_STORE || OpWord_opcode(event) != OP_PACKED_CALL_DISCARD) {
        activeCase = (ActiveCase){0};
        return;
    }
    logInfo("HLD: bound the active linked case path\n");
}

static bool enterActiveCase(VMContext* ctx, const CodeEntry* code) {
    if (code != activeCase.code || ctx != activeCase.context || ctx->runner == nullptr ||
        ctx->globalScopeInstance == nullptr) return false;
#ifdef HLD_COMPARE_ACTIVE_CASE
    if (!useActiveCase(ctx->runner->frameCount)) return false;
#endif
#ifdef HLD_CASE_CHECK_LAUNCH
    if (!hldCaseChecks) return false;
#endif
    CallFrame* frame = ctx->currentCallFrame;
    Instance* inst = frame->self;
    if (inst == nullptr || inst->objectIndex == STRUCT_OBJECT_INDEX ||
        activeCase.selectorSlot >= frame->localVarsCount ||
        RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, stableCases.canEdit))) return false;

    const GMLReal epsilon = ctx->runner->mathEpsilon;
    GMLReal mode, state;
    RValue selector = Instance_getSelfVar(inst, stableCases.selector);
    if (!(epsilon >= 0 && epsilon < 0.5) ||
        !caseNumber(selector, &mode) || GMLReal_fabs(mode) <= epsilon ||
        !caseNumber(Instance_getSelfVar(inst, stableCases.state), &state) ||
        GMLReal_fabs(state - 2.0) > epsilon) return false;
    selector.gmlStackType = GML_TYPE_VARIABLE;
#ifdef HLD_VERIFY_ACTIVE_CASE
    require(activeCaseEntryCheck == nullptr);
    activeCaseEntryCheck = frame;
    activeCaseSelector = selector;
    return false;
#else
    RValue_writeIntoSlotStealingOwnershipOrCopying(&frame->localVars[activeCase.selectorSlot], selector);
#ifdef HLD_COMPARE_ACTIVE_CASE
    ++activeCaseEntries;
#endif
#ifdef HLD_CASE_CHECK_LAUNCH
    ++activeCaseChecks;
#endif
    return true;
#endif
}

#ifdef HLD_VERIFY_ACTIVE_CASE
static void verifyActiveCaseEntry(VMContext* ctx) {
    if (activeCaseEntryCheck != ctx->currentCallFrame) return;
    activeCaseEntryCheck = nullptr;
    const RValue actual = ctx->currentCallFrame->localVars[activeCase.selectorSlot];
    require(actual.type == activeCaseSelector.type && actual.gmlStackType == activeCaseSelector.gmlStackType &&
            actual.assetRefType == activeCaseSelector.assetRefType &&
            RValue_toReal(actual) == RValue_toReal(activeCaseSelector));
    if (++activeCaseEntries == 1000) logInfo("HLD: checked 1,000 active case entries\n");
}
#endif

static inline bool activeCaseNumber(RValue value, GMLReal* number) {
    return caseNumber(value, number) && GMLReal_fabs(*number) < 16777216.0;
}

static bool finishActiveCase(VMContext* ctx, const CodeEntry* code, RValue* result) {
    if (code != activeCase.code || ctx != activeCase.context || ctx->runner == nullptr) return false;
#ifdef HLD_COMPARE_ACTIVE_CASE
    if (!useActiveCase(ctx->runner->frameCount)) return false;
#endif
#ifdef HLD_CASE_CHECK_LAUNCH
    if (!hldCaseChecks) return false;
#endif
    CallFrame* frame = ctx->currentCallFrame;
    Instance* invoker = frame->self;
    Instance* global = ctx->globalScopeInstance;
    if (invoker == nullptr || global == nullptr || invoker->objectIndex == STRUCT_OBJECT_INDEX ||
        activeCase.selectorSlot >= frame->localVarsCount) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    GMLReal state, check, selector, noObjects, touch, linked;
    if (!(epsilon >= 0 && epsilon < 0.5) ||
        !caseNumber(Instance_getSelfVar(invoker, stableCases.state), &state) || GMLReal_fabs(state - 2.0) > epsilon ||
        !caseNumber(Instance_getSelfVar(invoker, stableCases.check), &check) || GMLReal_fabs(check) > epsilon ||
        !activeCaseNumber(frame->localVars[activeCase.selectorSlot], &selector) ||
        !activeCaseNumber(Instance_getSelfVar(global, stableCases.noObjectsCase), &noObjects) ||
        !activeCaseNumber(Instance_getSelfVar(global, stableCases.touchCase), &touch) ||
        !activeCaseNumber(Instance_getSelfVar(global, stableCases.linkedCase), &linked) ||
        GMLReal_fabs(selector - noObjects) <= epsilon || GMLReal_fabs(selector - touch) <= epsilon ||
        GMLReal_fabs(selector - linked) > epsilon ||
        Instance_getSelfVar(invoker, stableCases.links).type == RVALUE_ARRAY) return false;

    // State 2 and an empty scalar link give _ret=0. With caseCheck also
    // zero, the original tail returns 1 without updating the trigger.
#ifdef HLD_VERIFY_ACTIVE_CASE
    require(activeCaseReturnCheck == nullptr);
    activeCaseReturnCheck = frame;
    return false;
#else
    *result = RValue_makeInt32(1);
    result->gmlStackType = GML_TYPE_VARIABLE;
#ifdef HLD_COMPARE_ACTIVE_CASE
    ++activeCaseReturns;
#endif
    return true;
#endif
}
