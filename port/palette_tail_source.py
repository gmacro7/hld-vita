"""Submit the bound palette upload without interpreting each channel conversion."""

from runtime_verification_source import replace_once


PALETTE = r'''
enum { PALETTE_TAIL = 0x29c, PALETTE_COLORS = 14, PALETTE_WRITES = 1 + 2 * PALETTE_COLORS };

typedef struct {
    const VMContext* context;
    const uint8_t* program;
    uint32_t reset, set, uniform;
    int32_t handles[PALETTE_WRITES];
} PaletteTail;

typedef struct {
    int32_t handle;
    float value[3];
} PaletteWrite;

static PaletteTail paletteTail;
static unsigned paletteTails;

static bool paletteBuiltin(VMContext* ctx, const uint8_t* program, unsigned offset, const char* name, uint32_t* index) {
    *index = resolveFuncOperand(program + offset + 4);
    const BuiltinFunc builtin = VM_findBuiltin(ctx, name);
    return *index < ctx->funcCallCacheCount && builtin != nullptr &&
        ctx->funcCallCache[*index].builtin == (void*)builtin &&
        ctx->funcCallCache[*index].scriptCodeIndex < 0;
}

static int32_t paletteHandleId(VMContext* ctx, const uint8_t* program, unsigned offset, const char* name) {
    const Variable* variable = resolveVarDef(ctx, resolveVarOperand(program + offset + 4));
    return strcmp(name, variable->name) == 0 ? variable->varID : -1;
}

static void bindPaletteTail(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_shader_set_paletteSwap", 5152, 0x78c5bc7eu);
    if (code == nullptr || code->localsCount != 11) return;
    const uint8_t* program = codeBytes(ctx, code);
    PaletteTail tail = {.context = ctx, .program = program};
    if (!paletteBuiltin(ctx, program, PALETTE_TAIL, "shader_reset", &tail.reset) ||
        !paletteBuiltin(ctx, program, 0x2b0, "shader_set", &tail.set) ||
        !paletteBuiltin(ctx, program, 0x2cc, "shader_set_uniform_f", &tail.uniform)) return;
    tail.handles[0] = paletteHandleId(ctx, program, 0x2c4, "paletteShaderLeeway");
    if (tail.handles[0] < 0) return;
    for (unsigned color = 0; color < 2 * PALETTE_COLORS; ++color) {
        const unsigned offset = 0x370 + (color / 2) * 0x13c + (color % 2) * 0x98;
        uint32_t function;
        if (!paletteBuiltin(ctx, program, offset, "shader_set_uniform_f", &function) ||
            function != tail.uniform ||
            !paletteBuiltin(ctx, program, offset - 0x74, "color_get_blue", &function) ||
            !paletteBuiltin(ctx, program, offset - 0x48, "color_get_green", &function) ||
            !paletteBuiltin(ctx, program, offset - 0x1c, "color_get_red", &function)) return;
        char name[32];
        snprintf(name, sizeof(name), "paletteShaderColor%u%c", color / 2 + 1, "ab"[color % 2]);
        tail.handles[color + 1] = paletteHandleId(ctx, program, offset - 8, name);
        if (tail.handles[color + 1] < 0) return;
    }
    paletteTail = tail;
    logInfo("HLD: bound direct palette upload\n");
}

static bool paletteNumber(RValue value, GMLReal* number) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *number = RValue_toReal(value);
    return isfinite(*number);
}

static bool preparePaletteWrites(VMContext* ctx, PaletteWrite* writes) {
    if (ctx->runner == nullptr || ctx->runner->renderer == nullptr || ctx->globalScopeInstance == nullptr) return false;
    const RValue* local = ctx->currentCallFrame->localVars;
    const RValue inputs[] = {local[3], local[4]};
    GMLReal number;
    if (inputs[0].type != RVALUE_ARRAY || inputs[1].type != RVALUE_ARRAY ||
        !paletteNumber(local[1], &number)) return false;
    writes[0].value[0] = (float)number;
    for (unsigned i = 0; i < PALETTE_WRITES; ++i) {
        const RValue handle = Instance_getSelfVar(ctx->globalScopeInstance, paletteTail.handles[i]);
        if (!paletteNumber(handle, &number) || number < INT32_MIN || number > INT32_MAX) return false;
        writes[i].handle = (int32_t)number;
    }
    for (unsigned i = 0; i < 2 * PALETTE_COLORS; ++i) {
        const RValue value = GMLArray_get(inputs[i % 2].array, (int32_t)(i / 2));
        if (!paletteNumber(value, &number) || number < INT32_MIN || number > INT32_MAX) return false;
        const int32_t color = (int32_t)number;
        writes[i + 1].value[0] = (float)((GMLReal)BGR_R(color) / (GMLReal)255);
        writes[i + 1].value[1] = (float)((GMLReal)BGR_G(color) / (GMLReal)255);
        writes[i + 1].value[2] = (float)((GMLReal)BGR_B(color) / (GMLReal)255);
    }
    return true;
}

#ifdef HLD_CHECK_PALETTE_TAIL
static PaletteWrite expectedPalette[PALETTE_WRITES];
static unsigned paletteCall;
static bool checkingPalette;

static void checkPaletteCall(VMContext* ctx, const uint8_t* program, OpWord op, const uint8_t* operand) {
    if (!checkingPalette || ctx != paletteTail.context || program != paletteTail.program) return;
    const uint32_t function = resolveFuncOperand(operand);
    const unsigned count = (unsigned)OpWord_extra(op);
    const RValue* top = ctx->stack.slots + ctx->stack.top;
    if (paletteCall < 2) {
        require(count == paletteCall && function == (paletteCall == 0 ? paletteTail.reset : paletteTail.set));
        if (paletteCall == 1) require(RValue_toReal(top[-1]) == 2.0);
    } else {
        const unsigned write = paletteCall - 2;
        const unsigned values = write == 0 ? 1 : 3;
        require(write < PALETTE_WRITES && function == paletteTail.uniform && count == values + 1);
        require((int32_t)RValue_toReal(top[-1]) == expectedPalette[write].handle);
        for (unsigned i = 0; i < values; ++i) {
            const float original = (float)RValue_toReal(top[-2 - (int)i]);
            require(memcmp(&original, &expectedPalette[write].value[i], sizeof(original)) == 0);
        }
    }
    if (++paletteCall == 2 + PALETTE_WRITES) {
        checkingPalette = false;
        if (++paletteTails == 100) logInfo("HLD: checked 100 direct palette uploads\n");
    }
}
#endif

static bool runPaletteTail(VMContext* ctx, const uint8_t* program) {
    if (ctx != paletteTail.context || program != paletteTail.program) return false;
#ifdef HLD_CHECK_PALETTE_TAIL
    require(!checkingPalette);
    checkingPalette = preparePaletteWrites(ctx, expectedPalette);
    paletteCall = 0;
    return false;
#else
    PaletteWrite writes[PALETTE_WRITES];
    if (!preparePaletteWrites(ctx, writes)) return false;
    Renderer* renderer = ctx->runner->renderer;
    renderer->vtable->gpuResetShader(renderer);
    renderer->vtable->gpuSetShader(renderer, 2);
    renderer->vtable->shaderSetUniformF(renderer, writes[0].handle, 1, writes[0].value[0], 0, 0, 0);
    for (unsigned i = 1; i < PALETTE_WRITES; ++i) {
        const float* value = writes[i].value;
        renderer->vtable->shaderSetUniformF(renderer, writes[i].handle, 3, value[0], value[1], value[2], 0);
    }
    if (++paletteTails == 100) logInfo("HLD: executed 100 direct palette uploads\n");
    return true;
#endif
}
'''


def with_palette_tail(code, *, verify=False, launch_flag=None):
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    prefix = "#define HLD_CHECK_PALETTE_TAIL\n" if verify else ""
    helper = PALETTE
    if launch_flag:
        anchor_count = "static unsigned paletteTails;"
        helper = replace_once(helper, anchor_count, anchor_count + f'''
extern bool {launch_flag};

void hldReportPaletteTail(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita palette upload frame=%d enabled=%d calls=%u\\n", frame, (int){launch_flag}, paletteTails);
    paletteTails = 0;
}}
''')
        helper = replace_once(helper, "    if (ctx != paletteTail.context || program != paletteTail.program) return false;",
                              f"    if (!{launch_flag} || ctx != paletteTail.context || program != paletteTail.program) return false;")
        helper = replace_once(helper, '    if (++paletteTails == 100) logInfo("HLD: executed 100 direct palette uploads\\n");',
                              "    ++paletteTails;")
    code = replace_once(code, anchor, prefix + helper + "\n" + anchor)
    anchor = "            case OP_PACKED_CALL_DISCARD:\n"
    code = replace_once(code, anchor, anchor + r'''
                if (fuseInstructions && instrAddr == PALETTE_TAIL && runPaletteTail(ctx, bytecodeBase)) {
                    ip = codeLength;
                    break;
                }
#ifdef HLD_CHECK_PALETTE_TAIL
                checkPaletteCall(ctx, bytecodeBase, opword, extraData);
#endif
''')
    anchor = "    packHldMathInstructions(ctx);"
    code = replace_once(code, anchor, anchor + "\n    bindPaletteTail(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    return replace_once(code, anchor, anchor + r'''
    if (paletteTail.context == ctx) {
        paletteTail = (PaletteTail){0};
        paletteTails = 0;
    }
''')
