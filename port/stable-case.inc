typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t usesCase, canEdit, selector, state, check, caseArgument, links, linkCase;
    int32_t noObjectsCase, touchCase, linkedCase;
#ifdef HLD_LINK_CASES
    int32_t brokenLinkCase;
    bool knownLinkTypes;
#endif
} StableCases;

static StableCases stableCases;

#ifdef HLD_CASE_CHECK_LAUNCH
extern bool hldCaseChecks;
static unsigned activeCaseChecks, brokenLinkChecks;

void hldReportCaseChecks(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita case checks frame=%d enabled=%d calls=%u\n",
            frame, (int)hldCaseChecks, activeCaseChecks + brokenLinkChecks);
    logInfo("Vita case check counts frame=%d active=%u linked=%u\n",
            frame, activeCaseChecks, brokenLinkChecks);
    activeCaseChecks = brokenLinkChecks = 0;
}
#endif

static void bindStableCases(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_CaseCheck", 2720, 0x7d91a362u);
    if (code == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint16_t operands[] = {0x0008, 0x0010, 0x00f0, 0x0120, 0x0988, 0x04cc, 0x0460, 0x0594,
                                 0x0274, 0x0288, 0x029c,
#ifdef HLD_LINK_CASES
                                 0x02b0,
#endif
    };
    int32_t varIds[sizeof(operands) / sizeof(*operands)];
    repeat(sizeof(operands) / sizeof(*operands), i) {
        varIds[i] = resolveVarDef(ctx, resolveVarOperand(bytes + operands[i]))->varID;
        if (varIds[i] < 0) return;
    }
    stableCases = (StableCases){
        .context = ctx, .code = code, .usesCase = varIds[0], .canEdit = varIds[1],
        .selector = varIds[2], .state = varIds[3], .check = varIds[4], .caseArgument = varIds[5],
        .links = varIds[6], .linkCase = varIds[7], .noObjectsCase = varIds[8],
        .touchCase = varIds[9], .linkedCase = varIds[10],
    };
#ifdef HLD_LINK_CASES
    stableCases.brokenLinkCase = varIds[11];
    stableCases.knownLinkTypes = matchHldCode(ctx, "gml_Script_IsType", 416, 0xc748538fu) != nullptr;
#endif
    logInfo("HLD: bound unchanged case-query results\n");
}

#ifdef HLD_COMPARE_STABLE_CASES
static unsigned stableCaseReturns;

static inline bool useStableCases(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportStableCases(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita stable cases frame=%d enabled=%d returns=%u\n",
            frame, (int)useStableCases(frame - 1), stableCaseReturns);
    stableCaseReturns = 0;
}
#define COUNT_STABLE_CASE() (++stableCaseReturns)
#elif defined(HLD_VERIFY_STABLE_CASES)
static unsigned stableCaseVerified;
#define COUNT_STABLE_CASE() do { \
    if (++stableCaseVerified == 1000) logInfo("HLD: checked 1,000 unchanged case-query results\n"); \
} while (0)
#else
#define COUNT_STABLE_CASE() ((void)0)
#endif

static inline bool caseNumber(RValue value, GMLReal* result) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *result = RValue_toReal(value);
    return isfinite(*result);
}

// A successful link would assign linkArg and possibly change the owning
// instance's case state. Only handle the read-only no-match path here.
static bool caseLinksUnchanged(VMContext* ctx, Instance* inst, GMLReal epsilon) {
    const RValue links = Instance_getSelfVar(inst, stableCases.links);
    if (links.type != RVALUE_ARRAY) return true;
    if (links.array == nullptr) return false;

    GMLReal index;
    if (!caseNumber(Instance_getSelfVar(inst, stableCases.caseArgument), &index)) return false;
    const bool matchIndex = index >= -epsilon;
    const int32_t length = GMLArray_length1D(links.array);
    for (int32_t i = 0; i < length; ++i) {
        const RValue* link = GMLArray_slot(links.array, i);
        GMLReal id;
        if (link == nullptr || !caseNumber(*link, &id) || !(id >= INSTANCE_ID_BASE && id <= INT32_MAX)) return false;
        const int32_t target = (int32_t)id;
        if ((GMLReal)target != id || (uint32_t)target < ctx->dataWin->objt.count) return false;
        if (matchIndex && GMLReal_fabs(i - index) > epsilon) continue;
        Instance* candidate = hmget(ctx->runner->instancesById, target);
        if (candidate == nullptr || !candidate->active) continue;
        GMLReal state;
        if (!caseNumber(Instance_getSelfVar(candidate, stableCases.linkCase), &state) ||
            GMLReal_fabs(state - 2.0) <= epsilon) return false;
    }
    return true;
}

#ifdef HLD_LINK_CASES
// Script-specific spawners and collectibles have extra liveness checks.
static bool isOrdinaryCaseLink(VMContext* ctx, const Instance* target) {
    enum { CASE_SPAWNER_OBJECT = 46, CASE_COLLECTIBLE_OBJECT = 131 };
    if (target->objectIndex == CASE_SPAWNER_OBJECT) return false;
    const uint32_t count = ctx->dataWin->objt.count;
    int32_t object = target->objectIndex;
    for (uint32_t i = 0; i < count && (uint32_t)object < count; ++i) {
        if (object == CASE_COLLECTIBLE_OBJECT) return false;
        object = ctx->dataWin->objt.objects[object].parentId;
        if (object == -1) return true;
    }
    return false;
}

static bool caseBrokenLinkReturnsZero(VMContext* ctx, Instance* inst, GMLReal epsilon) {
    const RValue links = Instance_getSelfVar(inst, stableCases.links);
    if (links.type != RVALUE_ARRAY) return true;
    if (links.array == nullptr || !stableCases.knownLinkTypes) return false;
    GMLReal arg;
    if (!caseNumber(Instance_getSelfVar(inst, stableCases.caseArgument), &arg)) return false;
    const bool any = GMLReal_fabs(arg + 999999.0) <= epsilon;
    const bool all = GMLReal_fabs(arg + 1.0) <= epsilon;
    const int32_t length = GMLArray_length1D(links.array);
    for (int32_t i = 0; i < length; ++i) {
        const RValue* link = GMLArray_slot(links.array, i);
        GMLReal id;
        if (link == nullptr || !caseNumber(*link, &id) || !(id >= INSTANCE_ID_BASE && id < 16777216) ||
            (uint32_t)id < ctx->dataWin->objt.count || id != (int32_t)id) return false;
        const Instance* target = hldLookupInstanceId(ctx->runner, (int32_t)id);
        if (target == nullptr || !target->active) {
            if (all) return false;
            continue;
        }
        if (!isOrdinaryCaseLink(ctx, target)) return false;
        if (any || (arg >= -epsilon && GMLReal_fabs(arg - i) <= epsilon)) return true;
    }
    return all && length > 0;
}
#endif

static bool caseLinkResultUnchanged(VMContext* ctx, Instance* inst, GMLReal selector,
                                    GMLReal check, GMLReal epsilon) {
    Instance* global = ctx->globalScopeInstance;
    GMLReal linked;
    if (!caseNumber(Instance_getSelfVar(global, stableCases.linkedCase), &linked)) return false;
    if (GMLReal_fabs(selector - linked) <= epsilon) return caseLinksUnchanged(ctx, inst, epsilon);
#ifdef HLD_LINK_CASES
#ifdef HLD_CASE_CHECK_LAUNCH
    if (!hldCaseChecks) return false;
#endif
    GMLReal broken;
    if (GMLReal_fabs(check - 1.0) > epsilon ||
        !caseNumber(Instance_getSelfVar(global, stableCases.brokenLinkCase), &broken) ||
        GMLReal_fabs(selector - broken) > epsilon || !caseBrokenLinkReturnsZero(ctx, inst, epsilon)) return false;
#ifdef HLD_VERIFY_STABLE_CASES
    static unsigned checkedBrokenLinkCases;
    if (++checkedBrokenLinkCases == 100) logInfo("HLD: checked 100 unchanged broken-link cases\n");
#endif
#ifdef HLD_CASE_CHECK_LAUNCH
    ++brokenLinkChecks;
#endif
    return true;
#else
    (void)check;
    return false;
#endif
}

static bool tryStableCase(VMContext* ctx, Instance* inst, RValue* result) {
    if (ctx->runner == nullptr || inst == nullptr || ctx->globalScopeInstance == nullptr) return false;
#ifdef HLD_COMPARE_STABLE_CASES
    if (!useStableCases(ctx->runner->frameCount)) return false;
#endif
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0 && epsilon < 0.5)) return false;
    const RValue editor = Instance_getSelfVar(ctx->globalScopeInstance, stableCases.canEdit);
    if (RValue_toBool(editor)) return false;

    GMLReal selector;
    if (!caseNumber(Instance_getSelfVar(inst, stableCases.selector), &selector)) return false;
    int32_t returned = -1;
    if (GMLReal_fabs(selector) > epsilon) {
        GMLReal state;
        if (!caseNumber(Instance_getSelfVar(inst, stableCases.state), &state)) return false;
        if (GMLReal_fabs(state - 3.0) > epsilon) {
            // State 2 invokes an event. State 0 with an unmet case check
            // returns zero without invoking the event or updating timers.
            if (GMLReal_fabs(state) > epsilon) return false;
            GMLReal check, empty, touch;
            Instance* global = ctx->globalScopeInstance;
            if (!caseNumber(Instance_getSelfVar(inst, stableCases.check), &check) || GMLReal_fabs(check) <= epsilon ||
                !caseNumber(Instance_getSelfVar(global, stableCases.noObjectsCase), &empty) ||
                !caseNumber(Instance_getSelfVar(global, stableCases.touchCase), &touch) ||
                GMLReal_fabs(selector - empty) <= epsilon || GMLReal_fabs(selector - touch) <= epsilon ||
                !caseLinkResultUnchanged(ctx, inst, selector, check, epsilon)) return false;
        }
        returned = 0;
    }
    *result = RValue_makeInt32(returned);
    result->gmlStackType = GML_TYPE_VARIABLE;
    return true;
}
