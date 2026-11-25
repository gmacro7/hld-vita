typedef struct {
    const VMContext* context;
    const CodeEntry* setDepth;
    const CodeEntry* modDepth;
    int32_t roomHeight;
} DepthHelpers;

typedef struct {
    RValue result;
    GMLReal depth;
    bool writeDepth;
} DepthUpdate;

static DepthHelpers depthHelpers;
static unsigned setDepthCalls, modDepthCalls;

static bool useDepthHelpers(int frame) {
#ifdef HLD_COMPARE_DEPTH_HELPERS
    return frame >= 180 && (frame / 180) % 2 != 0;
#else
    (void)frame;
    return true;
#endif
}

#ifdef HLD_COMPARE_DEPTH_HELPERS
void hldReportDepthHelpers(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita depth helpers frame=%d enabled=%d set=%u mod=%u\n", frame,
            (int)useDepthHelpers(frame - 1), setDepthCalls, modDepthCalls);
    setDepthCalls = modDepthCalls = 0;
}
#endif

static void bindDepthHelpers(VMContext* ctx) {
    if (DataWin_isVersionAtLeast(ctx->dataWin, 2, 0, 0, 0)) return;
    const CodeEntry* setDepth = matchHldCode(ctx, "gml_Script_setdepth", 212, 0x25a833e5u);
    const CodeEntry* modDepth = matchHldCode(ctx, "gml_Script_moddepth", 48, 0xcb3ca6d8u);
    if (setDepth == nullptr || modDepth == nullptr || setDepth->localsCount != 1 || modDepth->localsCount != 1)
        return;
    const Variable* setRoom = resolveVarDef(ctx, resolveVarOperand(codeBytes(ctx, setDepth) + 0x88));
    const Variable* modRoom = resolveVarDef(ctx, resolveVarOperand(codeBytes(ctx, modDepth) + 0x1c));
    if (setRoom != modRoom || setRoom->instanceType != INSTANCE_GLOBAL || setRoom->varID < 0 ||
        strcmp(setRoom->name, "roomHei") != 0) return;
    depthHelpers = (DepthHelpers){.context = ctx, .setDepth = setDepth, .modDepth = modDepth, .roomHeight = setRoom->varID};
    logInfo("HLD: bound setdepth and moddepth numeric helpers\n");
}

static bool tryDepthHelper(VMContext* ctx, const CodeEntry* code, Instance* inst, const RValue* args,
                           int32_t count, DepthUpdate* update) {
    if (code != depthHelpers.setDepth && code != depthHelpers.modDepth) return false;
    if (ctx != depthHelpers.context || ctx->runner == nullptr || inst == nullptr ||
        inst->objectIndex == STRUCT_OBJECT_INDEX || ctx->globalScopeInstance == nullptr ||
        !useDepthHelpers(ctx->runner->frameCount) || !isfinite(inst->depth)) return false;

    const bool set = code == depthHelpers.setDepth;
    if (set ? count != 0 : count != 1 || args == nullptr) return false;
    const RValue room = Instance_getSelfVar(ctx->globalScopeInstance, depthHelpers.roomHeight);
    if (room.type != RVALUE_REAL && room.type != RVALUE_INT32) return false;
    const GMLReal height = RValue_toReal(room);
    if (!(height >= 1.0) || !isfinite(height)) return false;

    if (set) {
        const GMLReal epsilon = ctx->runner->mathEpsilon;
        if (!(epsilon >= 0.0) || !isfinite(epsilon) || !isfinite(inst->y)) return false;
        const GMLReal depth = -inst->y / height;
        const bool write = !(GMLReal_fabs(inst->depth - depth) <= epsilon);
        update->result = RValue_makeInt32((int32_t)write);
        update->result.gmlStackType = GML_TYPE_VARIABLE;
        update->depth = depth;
        update->writeDepth = write;
    } else {
        const RValue adjustment = args[0];
        if (adjustment.type != RVALUE_REAL && adjustment.type != RVALUE_INT32) return false;
        const GMLReal offset = RValue_toReal(adjustment);
        const GMLReal depth = inst->depth - offset / height;
        if (!isfinite(offset) || !isfinite(depth)) return false;
        update->result = scriptFallthroughReturnValue(ctx);
        update->depth = depth;
        update->writeDepth = true;
    }
    unsigned* calls = set ? &setDepthCalls : &modDepthCalls;
    ++*calls;
#ifndef PLATFORM_VITA
    if (*calls == 100) logInfo("HLD: exercised 100 numeric calls to %s\n", code->name);
#endif
    return true;
}
