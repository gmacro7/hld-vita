"""Return a bound scalar linked case directly when it has no event handler."""

from runtime_verification_source import replace_once


HELPER = r'''
static unsigned eventlessCaseReturns;
static const VMContext* eventlessCaseContext;

void hldReportEventlessCaseAtExit(void) {
    logInfo("HLD: eventless linked case returns=%u\n", eventlessCaseReturns);
}

static void bindEventlessCase(VMContext* ctx) {
    if (stableCases.context != ctx || stableCases.code == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, stableCases.code);
    const uint32_t event = resolveFuncOperand(bytes + 0x15c);
    const BuiltinFunc builtin = VM_findBuiltin(ctx, "event_user");
    if (event < ctx->funcCallCacheCount && builtin != nullptr &&
        ctx->funcCallCache[event].builtin == (void*)builtin && ctx->funcCallCache[event].scriptCodeIndex < 0)
        eventlessCaseContext = ctx;
}

static bool hldEventlessLinkedCase(VMContext* ctx, Instance* inst, GMLReal selector, GMLReal epsilon) {
    // The first write is performed by the existing stable-case caller.
    // With no callback and a scalar link, the linked test yields zero.
    Runner* runner = ctx->runner;
    if (eventlessCaseContext != ctx || inst->objectIndex < 0 ||
        (uint32_t)inst->objectIndex >= ctx->dataWin->objt.count ||
        !(GMLReal_fabs(selector) < 16777216.0)) return false;
    const int32_t slot = EventSlotMap_lookup(&runner->eventSlotMap, EVENT_OTHER, OTHER_USER0 + 10);
    if (slot >= 0 && ResolvedEventTable_lookup(&runner->eventTable, inst->objectIndex, slot, nullptr) >= 0)
        return false;
    GMLReal check, empty, touch, linked;
    Instance* global = ctx->globalScopeInstance;
    return caseNumber(Instance_getSelfVar(inst, stableCases.check), &check) && GMLReal_fabs(check) <= epsilon &&
        caseNumber(Instance_getSelfVar(global, stableCases.noObjectsCase), &empty) && GMLReal_fabs(empty) < 16777216.0 &&
        caseNumber(Instance_getSelfVar(global, stableCases.touchCase), &touch) && GMLReal_fabs(touch) < 16777216.0 &&
        caseNumber(Instance_getSelfVar(global, stableCases.linkedCase), &linked) && GMLReal_fabs(linked) < 16777216.0 &&
        GMLReal_fabs(selector - empty) > epsilon && GMLReal_fabs(selector - touch) > epsilon &&
        GMLReal_fabs(selector - linked) <= epsilon &&
        Instance_getSelfVar(inst, stableCases.links).type != RVALUE_ARRAY;
}

'''


def with_eventless_case(source, *, verify=False, launch_flag=None):
    anchor = "static bool tryStableCase(VMContext* ctx, Instance* inst, RValue* result) {"
    helper = HELPER
    gate = ""
    if launch_flag:
        gate = f"{launch_flag} && "
        helper += f'''extern bool {launch_flag};

void hldReportEventlessCase(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita eventless linked cases frame=%d enabled=%d returns=%u\\n",
            frame, (int){launch_flag}, eventlessCaseReturns);
    eventlessCaseReturns = 0;
}}

'''
    source = replace_once(source, anchor, helper + anchor)
    anchor = "        if (GMLReal_fabs(state - 3.0) > epsilon) {"
    source = replace_once(source, anchor, f'''
        if ({gate}GMLReal_fabs(state - 2.0) <= epsilon && hldEventlessLinkedCase(ctx, inst, selector, epsilon)) {{
            *result = RValue_makeInt32(1);
            result->gmlStackType = GML_TYPE_VARIABLE;
            ++eventlessCaseReturns;
            return true;
        }}
''' + anchor)
    anchor = "    bindRestingScenery(ctx);"
    source = replace_once(source, anchor, anchor + "\n    bindEventlessCase(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    source = replace_once(source, anchor, anchor + "\n    if (eventlessCaseContext == ctx) {\n"
                          "        eventlessCaseContext = nullptr;\n        eventlessCaseReturns = 0;\n    }")
    if verify:
        # Reuse the existing original-interpreter comparison for all stable returns.
        source = "#define HLD_VERIFY_STABLE_CASES\n" + source
    return source


def with_eventless_case_exit(source):
    anchor = "    Runner_free(runner);"
    return replace_once(source, anchor, "    extern void hldReportEventlessCaseAtExit(void);\n"
                        "    hldReportEventlessCaseAtExit();\n" + anchor)
