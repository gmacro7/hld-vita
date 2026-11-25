"""Specialize the sprite bounds and viewport queries of the resolved 2016 VM."""

from runtime_verification_source import replace_once


QUERIES = r'''
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
typedef struct {
    const VMContext* context;
    const CodeEntry* bounds;
    const CodeEntry* viewport;
    int32_t boundsIds[4];
} SceneQueries;

typedef struct {
    RValue result;
    GMLReal bounds[4];
    bool writesBounds;
} SceneQueryResult;

typedef struct {
    uint16_t address;
    const char* name;
} SceneBuiltin;

static SceneQueries sceneQueries;
static unsigned sceneBoundsCalls, sceneViewportCalls;

static bool sceneBuiltinCalls(VMContext* ctx, const CodeEntry* entry, const SceneBuiltin* calls, size_t count) {
    if (entry == nullptr) return false;
    const uint8_t* program = codeBytes(ctx, entry);
    for (size_t i = 0; i < count; ++i) {
        const uint32_t function = resolveFuncOperand(program + calls[i].address + 4);
        const BuiltinFunc expected = VM_findBuiltin(ctx, calls[i].name);
        if (function >= ctx->funcCallCacheCount || expected == nullptr ||
            ctx->funcCallCache[function].builtin != (void*)expected ||
            ctx->funcCallCache[function].scriptCodeIndex >= 0) return false;
    }
    return true;
}

static void bindSceneQueries(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    SceneQueries binding = {.context = ctx};
    const CodeEntry* bounds = matchHldCode(ctx, "gml_Script_GetCollisionBounds", 312, 0x08a0b168u);
    static const SceneBuiltin boundsCalls[] = {
        {0x020, "sprite_get_xoffset"}, {0x040, "sprite_get_bbox_left"},
        {0x070, "sprite_get_yoffset"}, {0x090, "sprite_get_bbox_top"},
        {0x0c0, "sprite_get_bbox_right"}, {0x0d0, "sprite_get_bbox_left"},
        {0x104, "sprite_get_bbox_bottom"}, {0x114, "sprite_get_bbox_top"},
    };
    if (sceneBuiltinCalls(ctx, bounds, boundsCalls, sizeof(boundsCalls) / sizeof(*boundsCalls))) {
        static const uint16_t writes[] = {0x05c, 0x0ac, 0x0f0, 0x134};
        static const char* const names[] = {"x1", "y1", "x2", "y2"};
        const uint8_t* program = codeBytes(ctx, bounds);
        binding.bounds = bounds;
        for (unsigned i = 0; i < 4; ++i) {
            const Variable* variable = resolveVarDef(ctx, resolveVarOperand(program + writes[i]));
            if (variable->varID < 0 || strcmp(variable->name, names[i]) != 0) binding.bounds = nullptr;
            for (unsigned j = 0; j < i; ++j)
                if (binding.boundsIds[j] == variable->varID) binding.bounds = nullptr;
            binding.boundsIds[i] = variable->varID;
        }
    }
    const CodeEntry* viewport = matchHldCode(ctx, "gml_Script_onscreen_sprite", 572, 0x9176a1b5u);
    static const SceneBuiltin viewportCalls[] = {
        {0x058, "sprite_get_xoffset"}, {0x098, "sprite_get_yoffset"},
        {0x0d0, "sprite_get_width"}, {0x108, "sprite_get_height"},
    };
    if (sceneBuiltinCalls(ctx, viewport, viewportCalls, sizeof(viewportCalls) / sizeof(*viewportCalls)))
        binding.viewport = viewport;
    sceneQueries = binding;
    logInfo("HLD: bound scene queries bounds=%d viewport=%d\n",
            binding.bounds != nullptr, binding.viewport != nullptr);
}

static bool sceneQueryNumber(const RValue value, GMLReal* number) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *number = RValue_toReal(value);
    return isfinite(*number);
}

// The interpreter stores the multiply before applying the next arithmetic
// opcode. Preserve this rounding boundary when the host can contract FMA.
static inline GMLReal sceneRoundedProduct(GMLReal a, GMLReal b) {
    volatile GMLReal product = a * b;
    return product;
}

static bool planSceneQuery(VMContext* ctx, const CodeEntry* code, Instance* instance,
                           const RValue* args, int32_t argCount, SceneQueryResult* plan) {
    if (code != sceneQueries.bounds && code != sceneQueries.viewport) return false;
    if (sceneQueries.context != ctx || ctx->runner == nullptr) return false;
    const Sprt* sprites = &ctx->dataWin->sprt;
    if (code == sceneQueries.bounds && argCount == 0 && instance != nullptr &&
        instance->objectIndex != STRUCT_OBJECT_INDEX) {
        const int32_t spriteId = instance->spriteIndex;
        if (spriteId < 0 || (uint32_t)spriteId >= sprites->count) return false;
        const Sprite* sprite = &sprites->sprites[spriteId];
        const GMLReal sx = instance->imageXscale, sy = instance->imageYscale;
        const GMLReal x = instance->x - sceneRoundedProduct(sprite->originX, sx);
        const GMLReal y = instance->y - sceneRoundedProduct(sprite->originY, sy);
        const GMLReal left = x + sceneRoundedProduct(sprite->marginLeft, sx);
        const GMLReal top = y + sceneRoundedProduct(sprite->marginTop, sy);
        const GMLReal right = left + sceneRoundedProduct((GMLReal)sprite->marginRight - sprite->marginLeft, sx);
        const GMLReal bottom = top + sceneRoundedProduct((GMLReal)sprite->marginBottom - sprite->marginTop, sy);
        if (!isfinite(left) || !isfinite(top) || !isfinite(right) || !isfinite(bottom)) return false;
        *plan = (SceneQueryResult){scriptFallthroughReturnValue(ctx), {left, top, right, bottom}, true};
        ++sceneBoundsCalls;
        return true;
    }
    if (code != sceneQueries.viewport || args == nullptr || (argCount != 3 && argCount != 4)) return false;
    GMLReal x, y, spriteIndex, scale = 1;
    if (!sceneQueryNumber(args[0], &x) || !sceneQueryNumber(args[1], &y) ||
        !sceneQueryNumber(args[2], &spriteIndex) || !(spriteIndex >= 0 && spriteIndex < sprites->count) ||
        (argCount == 4 && !sceneQueryNumber(args[3], &scale))) return false;
    const Sprite* sprite = &sprites->sprites[(int32_t)spriteIndex];
    const GMLCamera* camera = Runner_getCameraForView(ctx->runner, 0);
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (camera == nullptr || !(epsilon >= 0 && epsilon < 0.5)) return false;
    const GMLReal left = x - sceneRoundedProduct(sprite->originX, scale);
    const GMLReal top = y - sceneRoundedProduct(sprite->originY, scale);
    const GMLReal right = left + sceneRoundedProduct(sprite->width, scale);
    const GMLReal bottom = top + sceneRoundedProduct(sprite->height, scale);
    if (!isfinite(left) || !isfinite(top) || !isfinite(right) || !isfinite(bottom)) return false;
    const GMLReal viewX = camera->viewX, viewY = camera->viewY;
    const GMLReal viewRight = viewX + camera->viewWidth, viewBottom = viewY + camera->viewHeight;
    const bool visible = viewRight - left > epsilon && right - viewX > epsilon &&
        bottom - viewY > epsilon && viewBottom - top > epsilon;
    plan->result = RValue_makeInt32(visible);
    plan->result.gmlStackType = GML_TYPE_VARIABLE;
    plan->writesBounds = false;
    ++sceneViewportCalls;
    return true;
}

void hldReportSceneQueriesAtExit(void) {
    logInfo("HLD: scene queries bounds=%u viewport=%u\n", sceneBoundsCalls, sceneViewportCalls);
}
#endif

'''


def with_scene_queries(source, *, verify=False, launch_flag=None):
    support = QUERIES
    gate = f"{launch_flag} && " if launch_flag else ""
    if launch_flag:
        support += f'''#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
extern bool {launch_flag};

void hldReportSceneQueries(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scene queries frame=%d enabled=%d bounds=%u viewport=%u\\n",
            frame, (int){launch_flag}, sceneBoundsCalls, sceneViewportCalls);
    sceneBoundsCalls = sceneViewportCalls = 0;
}}
#else
void hldReportSceneQueries(int frame) {{ (void)frame; }}
#endif

'''
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    source = replace_once(source, anchor, support + anchor)
    anchor = "    bindRestingScenery(ctx);"
    source = replace_once(source, anchor, "    bindSceneQueries(ctx);\n" + anchor)
    anchor = "void VM_free(VMContext* ctx) {"
    source = replace_once(source, anchor, anchor + r'''
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (sceneQueries.context == ctx) sceneQueries = (SceneQueries){0};
#endif''')
    # The caller's existing slow path both sets and restores the complete VM frame.
    support = rf'''
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    SceneQueryResult sceneQuery;
    const bool nativeQuery = {gate}planSceneQuery(ctx, code, selfInstance, args, argCount, &sceneQuery);
'''
    if not verify:
        support += r'''
    if (nativeQuery) {
        if (sceneQuery.writesBounds) {
            for (unsigned i = 0; i < 4; ++i) {
                RValue value = RValue_makeReal(sceneQuery.bounds[i]);
                value.gmlStackType = GML_TYPE_VARIABLE;
                Instance_setSelfVar(selfInstance, sceneQueries.boundsIds[i], value);
            }
        }
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return sceneQuery.result;
    }
'''
    anchor = "    // Create a CallFrame for this script\n"
    source = replace_once(source, anchor, support + "#endif\n\n" + anchor)
    if verify:
        anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
        source = replace_once(source, anchor, anchor + r'''
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (nativeQuery) {
        require(result.type == sceneQuery.result.type && result.assetRefType == sceneQuery.result.assetRefType &&
                result.gmlStackType == sceneQuery.result.gmlStackType && result.ownsReference == sceneQuery.result.ownsReference);
        if (sceneQuery.writesBounds) {
            require(result.real == sceneQuery.result.real);
            for (unsigned i = 0; i < 4; ++i) {
                const RValue actual = Instance_getSelfVar(selfInstance, sceneQueries.boundsIds[i]);
                const RValue expected = RValue_makeReal(sceneQuery.bounds[i]);
                require(actual.type == RVALUE_REAL && actual.gmlStackType == GML_TYPE_VARIABLE &&
                        !actual.ownsReference && actual.assetRefType == expected.assetRefType &&
                        memcmp(&actual.real, &expected.real, sizeof(actual.real)) == 0);
            }
        } else {
            require(result.int32 == sceneQuery.result.int32);
        }
    }
#endif''')
    return source


def with_scene_queries_exit(source):
    anchor = "    Runner_free(runner);"
    return replace_once(source, anchor, "    extern void hldReportSceneQueriesAtExit(void);\n"
                        "    hldReportSceneQueriesAtExit();\n" + anchor)
