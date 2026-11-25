typedef enum {
    LEAF_NONE,
    LEAF_ENEMY_STEP,
    LEAF_ENEMY_BEGIN,
    LEAF_DECOR_DRAW,
    LEAF_SCENERY_DRAW,
    LEAF_EVENT_COUNT
} LeafEvent;

typedef struct {
    const VMContext* context;
    const CodeEntry *enemyStep, *enemyBegin, *decorDraw, *sceneryDraw;
    int32_t deactivate, hidden, edit, selected, damage, animated, debugTools;
} LeafEvents;

static LeafEvents leafEvents;

#if defined(HLD_COMPARE_LEAF_EVENTS) || defined(HLD_VERIFY_LEAF_EVENTS)
static unsigned leafEventCalls[LEAF_EVENT_COUNT];
#endif

#ifdef HLD_COMPARE_LEAF_EVENTS
static bool useLeafEvents(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportLeafEvents(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita leaf events frame=%d enabled=%d enemy_step=%u enemy_begin=%u decor_draw=%u scenery_draw=%u\n",
            frame, (int)useLeafEvents(frame - 1), leafEventCalls[LEAF_ENEMY_STEP], leafEventCalls[LEAF_ENEMY_BEGIN],
            leafEventCalls[LEAF_DECOR_DRAW], leafEventCalls[LEAF_SCENERY_DRAW]);
    memset(leafEventCalls, 0, sizeof(leafEventCalls));
}
#endif

static int32_t leafField(VMContext* ctx, const CodeEntry* code, uint32_t offset, const char* name) {
    const uint32_t operand = resolveVarOperand(codeBytes(ctx, code) + offset);
    if (((operand >> 24) & 0xf8) != VARTYPE_NORMAL) return -1;
    const Variable* field = resolveVarDef(ctx, operand);
    return field->varID >= 0 && strcmp(field->name, name) == 0 ? field->varID : -1;
}

static bool leafBuiltin(VMContext* ctx, const CodeEntry* code, uint32_t offset, const char* name) {
    const uint32_t call = resolveFuncOperand(codeBytes(ctx, code) + offset);
    return call < ctx->funcCallCacheCount && ctx->funcCallCache[call].scriptCodeIndex < 0 &&
        ctx->funcCallCache[call].builtin != nullptr &&
        ctx->funcCallCache[call].builtin == (void*)VM_findBuiltin(ctx, name);
}

static void bindLeafEvents(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* step = matchHldCode(ctx, "gml_Object_enemy_Step_0", 9764, 0xd21da724u);
    const CodeEntry* begin = matchHldCode(ctx, "gml_Object_enemy_Step_1", 3636, 0xcb135e2bu);
    const CodeEntry* decor = matchHldCode(ctx, "gml_Object_DecorXRayBlock_Draw_0", 72, 0x61de5b2eu);
    const CodeEntry* scenery = matchHldCode(ctx, "gml_Object_Scenery_Draw_0", 512, 0xead1a060u);
    LeafEvents bindings = {.context = ctx};
    if (step != nullptr) {
        const int32_t deactivate = leafField(ctx, step, 0x04, "deactivateInstance");
        const int32_t hidden = leafField(ctx, step, 0x14, "editorHidden");
        if (deactivate >= 0 && hidden >= 0) {
            bindings.enemyStep = step;
            bindings.deactivate = deactivate;
            bindings.hidden = hidden;
        }
    }
    if (bindings.enemyStep != nullptr && begin != nullptr &&
        leafBuiltin(ctx, begin, 0x18, "instance_exists") &&
        leafField(ctx, begin, 0x74, "deactivateInstance") == bindings.deactivate &&
        leafField(ctx, begin, 0x84, "editorHidden") == bindings.hidden) {
        const int32_t edit = leafField(ctx, begin, 0x44, "canEdit");
        const int32_t selected = leafField(ctx, begin, 0x08, "selectedInEditor");
        const int32_t damage = leafField(ctx, begin, 0x6c, "damageThisFrame");
        if (edit >= 0 && selected >= 0 && damage >= 0 &&
            selected != bindings.deactivate && selected != bindings.hidden &&
            damage != bindings.deactivate && damage != bindings.hidden) {
            bindings.enemyBegin = begin;
            bindings.edit = edit;
            bindings.selected = selected;
            bindings.damage = damage;
        }
    }
    if (decor != nullptr && leafBuiltin(ctx, decor, 0x0c, "sprite_exists") &&
        leafBuiltin(ctx, decor, 0x1c, "draw_self")) {
        bindings.decorDraw = decor;
    }
    if (bindings.decorDraw != nullptr && scenery != nullptr &&
        leafBuiltin(ctx, scenery, 0x1f8, "event_inherited")) {
        const int32_t animated = leafField(ctx, scenery, 0x04, "animated");
        const int32_t debugTools = leafField(ctx, scenery, 0x44, "showDebugTools");
        if (animated >= 0 && debugTools >= 0) {
            bindings.sceneryDraw = scenery;
            bindings.animated = animated;
            bindings.debugTools = debugTools;
        }
    }
    leafEvents = bindings;
    logInfo("HLD: bound leaf events enemy_step=%d enemy_begin=%d decor_draw=%d scenery_draw=%d\n",
            bindings.enemyStep != nullptr, bindings.enemyBegin != nullptr,
            bindings.decorDraw != nullptr, bindings.sceneryDraw != nullptr);
}

static inline LeafEvent planLeafEvent(VMContext* ctx, const CodeEntry* code, Instance* inst,
                                      int32_t owner, int32_t type, int32_t subtype, int32_t count) {
    if (code != leafEvents.enemyStep && code != leafEvents.enemyBegin &&
        code != leafEvents.decorDraw && code != leafEvents.sceneryDraw) return LEAF_NONE;
    if (ctx != leafEvents.context || inst == nullptr || inst->objectIndex == STRUCT_OBJECT_INDEX ||
        ctx->runner == nullptr || count != 0) return LEAF_NONE;
#ifdef HLD_COMPARE_LEAF_EVENTS
    if (!useLeafEvents(ctx->runner->frameCount)) return LEAF_NONE;
#endif
    if (type == EVENT_STEP) {
        if (!RValue_toBool(Instance_getSelfVar(inst, leafEvents.deactivate)) &&
            !RValue_toBool(Instance_getSelfVar(inst, leafEvents.hidden))) return LEAF_NONE;
        if (code == leafEvents.enemyStep && subtype == STEP_NORMAL) return LEAF_ENEMY_STEP;
        if (code == leafEvents.enemyBegin && subtype == STEP_BEGIN && ctx->globalScopeInstance != nullptr &&
            !RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, leafEvents.edit))) return LEAF_ENEMY_BEGIN;
        return LEAF_NONE;
    }
    if (type != EVENT_DRAW || subtype != DRAW_NORMAL || inst->spriteIndex < 0 ||
        (uint32_t)inst->spriteIndex >= ctx->dataWin->sprt.count) return LEAF_NONE;
    if (code == leafEvents.decorDraw) return LEAF_DECOR_DRAW;
    if (code != leafEvents.sceneryDraw || ctx->globalScopeInstance == nullptr ||
        ctx->traceEventInherited || owner < 0 || (uint32_t)owner >= ctx->dataWin->objt.count ||
        RValue_toBool(Instance_getSelfVar(inst, leafEvents.animated)) ||
        RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, leafEvents.debugTools))) return LEAF_NONE;

    const int32_t parent = ctx->dataWin->objt.objects[owner].parentId;
    if (parent < 0 || (uint32_t)parent >= ctx->dataWin->objt.count) return LEAF_NONE;
    const int32_t slot = EventSlotMap_lookup(&ctx->runner->eventSlotMap, EVENT_DRAW, DRAW_NORMAL);
    if (slot < 0) return LEAF_NONE;
    const int32_t inherited = ResolvedEventTable_lookup(&ctx->runner->eventTable, parent, slot, nullptr);
    return inherited == leafEvents.decorDraw - ctx->dataWin->code.entries ? LEAF_SCENERY_DRAW : LEAF_NONE;
}

#ifndef HLD_VERIFY_LEAF_EVENTS
static void applyLeafEvent(VMContext* ctx, LeafEvent event, Instance* inst, Instance* other, int32_t owner) {
    switch (event) {
        case LEAF_ENEMY_BEGIN:
            Instance_setSelfVar(inst, leafEvents.selected, RValue_makeReal(0.0));
            Instance_setSelfVar(inst, leafEvents.damage, RValue_makeReal(0.0));
            break;
        case LEAF_DECOR_DRAW:
            if (ctx->runner->renderer != nullptr) Renderer_drawSelf(ctx->runner->renderer, inst);
            break;
        case LEAF_SCENERY_DRAW:
            Runner_executeEventFromObject(ctx->runner, inst, other, ctx->dataWin->objt.objects[owner].parentId,
                                          EVENT_DRAW, DRAW_NORMAL);
            break;
        default:
            break;
    }
}
#endif
