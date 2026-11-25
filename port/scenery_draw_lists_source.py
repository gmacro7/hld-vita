"""Lower the fingerprinted scenery draw-list append to native VM operations."""

from runtime_verification_source import replace_once


def with_scenery_draw_lists(code, *, launch_flag=None):
    if launch_flag is not None:
        gate = f" && {launch_flag}"
        declarations = f"extern bool {launch_flag};\n"
        report = f"""
void hldReportSceneryDrawLists(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery draw lists frame=%d enabled=%d visits=%u appends=%u\\n",
            frame, (int){launch_flag}, sceneryDrawListVisits, sceneryDrawListAppends);
    sceneryDrawListVisits = sceneryDrawListAppends = 0;
}}
"""
    else:
        gate = declarations = report = ""
    # Apply after the interpreter variants have been emitted. The same guarded
    # instruction boundary is present in both the switch and threaded loops.
    anchor = "                if (instrAddr == HLD_SCENERY_CULL_START"
    assert code.count(anchor) in (1, 2)
    code = code.replace(anchor, f"""                if (instrAddr == 0x0530 && useHldSceneryCull{gate} && appendSceneryDrawList(ctx)) {{
                    ip = HLD_SCENERY_NEXT_INSTANCE;
                    break;
                }}
""" + anchor)
    anchor = "static bool finishRestingScenery(VMContext* ctx, const CodeEntry* code);"
    code = replace_once(code, anchor, declarations + DRAW_LISTS + report + "\n" + anchor)
    anchor = "                bindSceneryPass(ctx, scenery, codeBytes(ctx, ctx->hldSceneryVisibilityCode));"
    code = replace_once(code, anchor, anchor + "\n                bindSceneryDrawLists(ctx, scenery);")
    anchor = "void VM_free(VMContext* ctx) {"
    return replace_once(code, anchor, anchor + """
    if (sceneryDrawLists.context == ctx) {
        logInfo("HLD: scenery draw-list visits=%u appends=%u\\n", sceneryDrawListVisits, sceneryDrawListAppends);
        sceneryDrawLists = (SceneryDrawLists){0};
        sceneryDrawListVisits = sceneryDrawListAppends = 0;
    }""")


DRAW_LISTS = r'''
enum { DRAW_FLOOR, DRAW_BACKGROUND, DRAW_FOREGROUND, SCENERY_LAYER_COUNT };
enum { SCENERY_LIST_COUNT = 7 };

typedef struct {
    const VMContext* context;
    int32_t level, layer[SCENERY_LAYER_COUNT];
    int32_t variable[SCENERY_LIST_COUNT], index[SCENERY_LIST_COUNT];
} SceneryDrawLists;

static SceneryDrawLists sceneryDrawLists;
static unsigned sceneryDrawListVisits, sceneryDrawListAppends;

static void bindSceneryDrawLists(VMContext* ctx, const uint8_t* step) {
    // These offsets are read only after binding the exact original SCREEN
    // End Step. The seven entries follow the branches in the original code.
    static const uint16_t layerOffsets[] = {0x548, 0x650, 0x700};
    static const uint16_t indexOffsets[] = {0x578, 0x5c8, 0x620, 0x680, 0x6d0, 0x730, 0x780};
    static const uint16_t arrayOffsets[] = {0x584, 0x5d4, 0x62c, 0x68c, 0x6dc, 0x73c, 0x78c};
    SceneryDrawLists binding = {.context = ctx, .level = sceneryPlainVariable(ctx, step + 0x558)};
    if (binding.level < 0) return;
    repeat(SCENERY_LAYER_COUNT, i) {
        binding.layer[i] = sceneryPlainVariable(ctx, step + layerOffsets[i]);
        if (binding.layer[i] < 0) return;
    }
    repeat(SCENERY_LIST_COUNT, i) {
        binding.index[i] = sceneryPlainVariable(ctx, step + indexOffsets[i]);
        const uint32_t arrayRef = resolveVarOperand(step + arrayOffsets[i]);
        if (((arrayRef >> 24) & 0xf8) != VARTYPE_ARRAY) return;
        binding.variable[i] = resolveVarDef(ctx, arrayRef)->varID;
        if (binding.index[i] < 0 || binding.index[i] >= 19 || binding.variable[i] < 0) return;
    }
    const uint32_t idRef = resolveVarOperand(step + 0x56c);
    const Variable* id = resolveVarDef(ctx, idRef);
    if (((idRef >> 24) & 0xf8) != VARTYPE_NORMAL || id->varID != VARIABLE_BUILTIN ||
        id->builtinVarId != BUILTIN_VAR_ID) return;
    sceneryDrawLists = binding;
}

static bool appendSceneryDrawList(VMContext* ctx) {
    if (sceneryDrawLists.context != ctx || ctx->globalScopeInstance == nullptr) return false;
    CallFrame* frame = ctx->currentCallFrame;
    Instance* self = frame->self;
    if (self == nullptr) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.25)) return false;

    const RValue inactive = Instance_getSelfVar(self, ctx->hldSceneryInactiveVarId);
    if (inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32) return false;
    if (!(GMLReal_fabs(RValue_toReal(inactive)) <= epsilon)) {
        ++sceneryDrawListVisits;
        return true;
    }

    int layer;
    for (layer = 0; layer < SCENERY_LAYER_COUNT; ++layer) {
        const RValue flag = Instance_getSelfVar(self, sceneryDrawLists.layer[layer]);
        if (flag.type != RVALUE_REAL && flag.type != RVALUE_INT32 &&
            flag.type != RVALUE_BOOL && flag.type != RVALUE_UNDEFINED) return false;
        if (RValue_toBool(flag)) break;
    }
    if (layer == SCENERY_LAYER_COUNT) {
        ++sceneryDrawListVisits;
        return true;
    }
    const RValue level = Instance_getSelfVar(self, sceneryDrawLists.level);
    if (level.type != RVALUE_REAL && level.type != RVALUE_INT32) return false;
    const GMLReal number = RValue_toReal(level);
    static const unsigned offsets[] = {0, 3, 5};
    unsigned channel = offsets[layer];
    if (GMLReal_fabs(number) <= epsilon) {
        // The first slot for each layer is the level-zero slot.
    } else if (GMLReal_fabs(number - (GMLReal) 1.0) <= epsilon) {
        channel += 1;
    } else if (layer == DRAW_FLOOR && GMLReal_fabs(number - (GMLReal) 0.5) <= epsilon) {
        channel += 2;
    } else {
        ++sceneryDrawListVisits;
        return true;
    }

    const uint32_t local = (uint32_t) sceneryDrawLists.index[channel];
    if (frame->localVarsCount <= local) return false;
    RValue* counter = &frame->localVars[local];
    if (counter->type != RVALUE_INT32 && counter->type != RVALUE_REAL) return false;
    const GMLReal value = RValue_toReal(*counter);
    if (!(value >= 0.0 && value < 32000.0)) return false;
    const int32_t index = (int32_t) value;
    RValue* list = IntRValueHashMap_getOrInsertUndefined(&ctx->globalScopeInstance->selfVars,
                                                       sceneryDrawLists.variable[channel]);
    RValue id = RValue_makeReal((GMLReal) self->instanceId);
    id.gmlStackType = GML_TYPE_VARIABLE;
    VM_arraySetWithCoW(ctx, list, index, id);
    if (counter->type == RVALUE_REAL) counter->real = value + (GMLReal) 1.0;
    else counter->int32 = index + 1;
    counter->gmlStackType = GML_TYPE_VARIABLE;
    ++sceneryDrawListVisits;
    ++sceneryDrawListAppends;
    return true;
}
'''
