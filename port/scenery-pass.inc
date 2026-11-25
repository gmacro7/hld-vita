typedef struct {
    const VMContext* context;
    int32_t edges[4];
    int32_t hidden[4];
    int32_t canEdit, printing, inactive;
} SceneryPass;

static SceneryPass sceneryPass;

static int32_t sceneryPlainVariable(VMContext* ctx, const uint8_t* operand) {
    const uint32_t reference = resolveVarOperand(operand);
    if (((reference >> 24) & 0xf8) != VARTYPE_NORMAL) return -1;
    return resolveVarDef(ctx, reference)->varID;
}

static void bindSceneryPass(VMContext* ctx, const uint8_t* step, const uint8_t* visibility) {
    SceneryPass bound = {.context = ctx};
    static const uint16_t hiddenOperands[] = {0x30, 0x54, 0x64, 0x74};
    repeat(4, i) {
        bound.edges[i] = sceneryPlainVariable(ctx, step + 0x042c + i * 24);
        bound.hidden[i] = sceneryPlainVariable(ctx, visibility + hiddenOperands[i]);
        if (bound.edges[i] < 0 || bound.hidden[i] < 0) return;
    }
    bound.canEdit = sceneryPlainVariable(ctx, step + 0x03e4);
    bound.printing = sceneryPlainVariable(ctx, visibility + 0x04);
    bound.inactive = sceneryPlainVariable(ctx, step + 0x0424);
    if (bound.canEdit < 0 || bound.printing < 0 || bound.inactive < 0) return;

    const uint32_t visible = resolveVarOperand(step + 0x04bc);
    if (((visible >> 24) & 0xf8) != VARTYPE_NORMAL) return;
    const Variable* builtin = resolveVarDef(ctx, visible);
    if (builtin->varID != VARIABLE_BUILTIN || builtin->builtinVarId != BUILTIN_VAR_VISIBLE) return;
    sceneryPass = bound;
    logInfo("HLD: bound the direct scenery pass\n");
}

enum SceneryDecision { SCENERY_SCRIPT, SCENERY_UNCHANGED, SCENERY_WRITE };

typedef struct {
    enum SceneryDecision decision;
    bool inactive, visible;
} SceneryChanges;

// The same SCREEN pass and update order, with the field operands already
// resolved. Each object reads live state after the preceding callback.
static inline SceneryChanges sceneryChanges(VMContext* ctx, Instance* instance) {
    const Instance* global = ctx->globalScopeInstance;
    const CallFrame* frame = ctx->currentCallFrame;
    const SceneryChanges fallback = {SCENERY_SCRIPT, false, false};
    if (instance->instanceId >= (1u << 24)) return fallback;

    const GMLReal epsilon = ctx->runner->mathEpsilon;
    const RValue group = frame->localVars[4];
    if (group.type != RVALUE_REAL || !(epsilon >= 0.0)) return fallback;
    if (!(GMLReal_fabs((instance->instanceId % 4) - group.real) <= epsilon))
        return (SceneryChanges){SCENERY_UNCHANGED, false, false};

    if (RValue_toBool(IntRValueHashMap_get(&global->selfVars, sceneryPass.canEdit))) return fallback;
    bool intersects = true;
    for (uint32_t i = 0; i < 4 && intersects; ++i) {
        const RValue edge = Instance_getSelfVar(instance, sceneryPass.edges[i]);
        const RValue view = frame->localVars[6 + i];
        if ((edge.type != RVALUE_REAL && edge.type != RVALUE_INT32) ||
            (view.type != RVALUE_REAL && view.type != RVALUE_INT32)) return fallback;
        const GMLReal diff = RValue_toReal(edge) - RValue_toReal(view);
        if (!isfinite(diff)) return fallback;
        intersects = (i & 1) ? -diff > epsilon : diff > epsilon;
    }

    bool visible = RValue_toBool(IntRValueHashMap_get(&global->selfVars, sceneryPass.printing));
    if (!visible && intersects) {
        visible = true;
        for (uint32_t i = 0; i < 4 && visible; ++i)
            visible = !RValue_toBool(Instance_getSelfVar(instance, sceneryPass.hidden[i]));
    }
    return (SceneryChanges){SCENERY_WRITE, !intersects, visible};
}

#ifdef HLD_COMPARE_SCENERY_PASS
static unsigned sceneryPassWrites;

static inline bool useSceneryPass(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportSceneryPass(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery pass frame=%d enabled=%d writes=%u\n",
            frame, (int)useSceneryPass(frame - 1), sceneryPassWrites);
    sceneryPassWrites = 0;
}
#endif

static inline bool hldSceneryCull(VMContext* ctx, const uint8_t* step) {
    Instance* instance = ctx->currentCallFrame->self;
    if (sceneryPass.context != ctx || ctx->globalScopeInstance == nullptr ||
        instance == nullptr || instance->objectIndex == STRUCT_OBJECT_INDEX)
        return hldSceneryCullReference(ctx, step);
#ifdef HLD_COMPARE_SCENERY_PASS
    if (!useSceneryPass(ctx->runner->frameCount)) return hldSceneryCullReference(ctx, step);
#endif
    const SceneryChanges change = sceneryChanges(ctx, instance);
#ifdef HLD_VERIFY_SCENERY_PASS
    const int32_t stackTop = ctx->stack.top;
    const bool reference = hldSceneryCullReference(ctx, step);
    require(reference == (change.decision != SCENERY_SCRIPT));
    require(stackTop == ctx->stack.top);
    if (change.decision == SCENERY_WRITE) {
        const RValue inactive = Instance_getSelfVar(instance, sceneryPass.inactive);
        require(inactive.type == RVALUE_REAL && inactive.real == (GMLReal)change.inactive);
        require(instance->visible == change.visible);
        static unsigned checked;
        if (++checked == 10000) logInfo("HLD: checked 10,000 direct scenery writes\n");
    }
    return reference;
#else
    if (change.decision == SCENERY_SCRIPT) return false;
    if (change.decision == SCENERY_WRITE) {
        Instance_setSelfVar(instance, sceneryPass.inactive, RValue_makeReal(change.inactive));
        VMBuiltins_setVariable(ctx, instance, BUILTIN_VAR_VISIBLE, "visible", RValue_makeReal(change.visible), -1);
#ifdef HLD_COMPARE_SCENERY_PASS
        ++sceneryPassWrites;
#endif
    }
    return true;
#endif
}
