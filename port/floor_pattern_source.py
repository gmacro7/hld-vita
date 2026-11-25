"""Submit the original floor-pattern fragments without VM loop dispatch."""

from runtime_verification_source import replace_once


FLOOR_PATTERN = r'''
typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t background;
    uint32_t draw;
} FloorPatternBinding;

typedef struct {
    int32_t sprite, spriteWidth, spriteHeight;
    int32_t sourceStartX, sourceX, sourceY;
    int32_t width, widthLeft, heightLeft;
    GMLReal rowStartX, drawX, drawY;
} FloorPattern;

typedef struct {
    int32_t sprite;
    float left, top, width, height, x, y;
} FloorFragment;

static FloorPatternBinding floorPatternBinding;
static unsigned floorPatternCalls;

static void bindFloorPattern(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_DrawFloorPattern", 800, 0xe7f88ee6u);
    if (code == nullptr || code->localsCount != 13) return;
    const uint8_t* program = codeBytes(ctx, code);
    const Variable* background = resolveVarDef(ctx, resolveVarOperand(program + 4));
    if (background->varID < 0 || strcmp(background->name, "floorBakedBackground") != 0) return;
    static const struct { uint16_t offset; const char* builtin; } calls[] = {
        {0x008, "sprite_exists"}, {0x050, "sprite_get_width"}, {0x068, "sprite_get_height"},
        {0x148, "min"}, {0x150, "max"}, {0x184, "min"}, {0x18c, "max"}, {0x1dc, "draw_sprite_part"},
    };
    for (size_t i = 0; i < sizeof(calls) / sizeof(*calls); ++i) {
        const uint32_t index = resolveFuncOperand(program + calls[i].offset + 4);
        const BuiltinFunc builtin = VM_findBuiltin(ctx, calls[i].builtin);
        if (index >= ctx->funcCallCacheCount || builtin == nullptr ||
            ctx->funcCallCache[index].builtin != (void*)builtin ||
            ctx->funcCallCache[index].scriptCodeIndex >= 0) return;
    }
    floorPatternBinding = (FloorPatternBinding){
        ctx, code, background->varID, resolveFuncOperand(program + 0x1e0)
    };
    logInfo("HLD: bound the floor pattern layout\n");
}

static bool floorPatternNumber(RValue value, GMLReal* number) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *number = RValue_toReal(value);
    return isfinite(*number);
}

static bool prepareFloorPattern(VMContext* ctx, const CodeEntry* code,
                               const RValue* args, int32_t argCount, FloorPattern* plan) {
    if (code != floorPatternBinding.code || ctx != floorPatternBinding.context ||
        args == nullptr || argCount < 6 || ctx->runner == nullptr || ctx->runner->renderer == nullptr ||
        ctx->globalScopeInstance == nullptr) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    GMLReal sprite;
    if (!floorPatternNumber(Instance_getSelfVar(ctx->globalScopeInstance, floorPatternBinding.background), &sprite) ||
        !(sprite >= 0 && sprite < ctx->dataWin->sprt.count) || sprite != (int32_t)sprite) return false;
    const Sprite* texture = &ctx->dataWin->sprt.sprites[(int32_t)sprite];
    if (texture->width == 0 || texture->width > 8192 || texture->height == 0 || texture->height > 8192) return false;
    GMLReal values[6];
    for (unsigned i = 0; i < 6; ++i) {
        if (!floorPatternNumber(args[i], &values[i]) || GMLReal_fabs(values[i]) > 131072.0) return false;
        if (i < 4 && (values[i] < 0 || values[i] != (int32_t)values[i])) return false;
    }
    if (values[2] < 1 || values[2] > 4096 || values[3] < 1 || values[3] > 4096) return false;
    const int32_t width = (int32_t)values[2], height = (int32_t)values[3];
    const int32_t spriteWidth = (int32_t)texture->width, spriteHeight = (int32_t)texture->height;
    const int32_t sourceX = (int32_t)values[0] % spriteWidth;
    const int32_t sourceY = (int32_t)values[1] % spriteHeight;
    *plan = (FloorPattern){
        .sprite = (int32_t)sprite, .spriteWidth = spriteWidth, .spriteHeight = spriteHeight,
        .sourceStartX = sourceX, .sourceX = sourceX, .sourceY = sourceY,
        .width = width, .widthLeft = width, .heightLeft = height,
        .rowStartX = values[0], .drawX = values[4], .drawY = values[5],
    };
    return true;
}

static FloorFragment nextFloorFragment(FloorPattern* plan) {
    const int32_t spriteWidthLeft = plan->spriteWidth - plan->sourceX;
    const int32_t spriteHeightLeft = plan->spriteHeight - plan->sourceY;
    const int32_t w = plan->widthLeft < spriteWidthLeft ? plan->widthLeft : spriteWidthLeft;
    const int32_t h = plan->heightLeft < spriteHeightLeft ? plan->heightLeft : spriteHeightLeft;
    const FloorFragment fragment = {plan->sprite, (float)plan->sourceX, (float)plan->sourceY,
                                    (float)w, (float)h, (float)plan->drawX, (float)plan->drawY};
    plan->widthLeft -= w;
    if (plan->widthLeft != 0) {
        plan->drawX += w;
        plan->sourceX = (plan->sourceX + w) % plan->spriteWidth;
    } else {
        plan->drawX = plan->rowStartX;
        plan->sourceX = plan->sourceStartX;
        plan->widthLeft = plan->width;
        plan->drawY += h;
        plan->sourceY = (plan->sourceY + h) % plan->spriteHeight;
        plan->heightLeft -= h;
    }
    return fragment;
}
'''


FLOOR_CHECK = r'''
static FloorPattern floorCheckPlan;
static bool floorChecking;

static void checkFloorFragment(VMContext* ctx, uint32_t function, const RValue* args, int32_t argc) {
    if (!floorChecking || ctx != floorPatternBinding.context || function != floorPatternBinding.draw ||
        &ctx->dataWin->code.entries[ctx->currentCallFrame->codeIndex] != floorPatternBinding.code) return;
    require(floorCheckPlan.heightLeft > 0 && argc == 8);
    const FloorFragment fragment = nextFloorFragment(&floorCheckPlan);
    require(RValue_toInt32(args[0]) == fragment.sprite && RValue_toInt32(args[1]) == 0);
    const float expected[] = {fragment.left, fragment.top, fragment.width, fragment.height, fragment.x, fragment.y};
    for (unsigned i = 0; i < 6; ++i) {
        const float actual = (float)RValue_toReal(args[i + 2]);
        require(memcmp(&actual, &expected[i], sizeof(actual)) == 0);
    }
}
'''


def with_floor_pattern(code, *, verify=False, launch_flag=None):
    support = FLOOR_PATTERN
    if launch_flag:
        anchor = "static unsigned floorPatternCalls;"
        support = replace_once(support, anchor, anchor + f'''
extern bool {launch_flag};

void hldReportFloorPattern(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita floor pattern frame=%d enabled=%d calls=%u\\n", frame, (int){launch_flag}, floorPatternCalls);
    floorPatternCalls = 0;
}}
''')
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    code = replace_once(code, anchor, support + (FLOOR_CHECK if verify else "") + "\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    code = replace_once(code, anchor, anchor + "\n    bindFloorPattern(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    code = replace_once(code, anchor, anchor + r'''
    if (floorPatternBinding.context == ctx) {
        floorPatternBinding = (FloorPatternBinding){0};
        floorPatternCalls = 0;
    }
''')
    anchor = "    // Create a CallFrame for this script\n"
    guard = ("#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && "
             "!defined(ENABLE_VM_OPCODE_PROFILER)\n")
    if verify:
        setup = r'''    const bool checkFloor = prepareFloorPattern(ctx, code, args, argCount, &floorCheckPlan);
    if (checkFloor) {
        require(!floorChecking);
        floorChecking = true;
    }
'''
    else:
        setup = r'''    FloorPattern plan;
    if (prepareFloorPattern(ctx, code, args, argCount, &plan)) {
        Renderer* renderer = ctx->runner->renderer;
        while (plan.heightLeft > 0) {
            const FloorFragment tile = nextFloorFragment(&plan);
            Renderer_drawSpritePart(renderer, tile.sprite, 0, tile.left, tile.top,
                                    tile.width, tile.height, tile.x, tile.y);
        }
        if (++floorPatternCalls == 100) logInfo("HLD: executed 100 direct floor patterns\n");
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
'''
        if launch_flag:
            setup = replace_once(setup, "    if (prepareFloorPattern(",
                                 f"    if ({launch_flag} && prepareFloorPattern(")
            setup = replace_once(setup, '        if (++floorPatternCalls == 100) logInfo("HLD: executed 100 direct floor patterns\\n");',
                                 "        ++floorPatternCalls;")
    code = replace_once(code, anchor, guard + setup + "#endif\n\n" + anchor)
    if verify:
        anchor = "static void handleCall(VMContext* ctx, OpWord opword, const uint8_t* extraData, bool discard) {"
        declaration = "static void checkFloorFragment(VMContext*, uint32_t, const RValue*, int32_t);\n\n"
        code = replace_once(code, anchor, declaration + anchor)
        anchor = "    // Fast path: cached builtin function pointer"
        code = replace_once(code, anchor, "    checkFloorFragment(ctx, funcIndex, args, argCount);\n" + anchor)
        anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
        code = replace_once(code, anchor, anchor + "\n" + guard + r'''
    if (checkFloor) {
        const RValue expected = scriptFallthroughReturnValue(ctx);
        require(floorCheckPlan.heightLeft == 0 && result.type == expected.type &&
                result.gmlStackType == expected.gmlStackType && result.real == expected.real);
        floorChecking = false;
        if (++floorPatternCalls == 100) logInfo("HLD: checked 100 direct floor patterns\n");
    }
#endif''')
    return code
