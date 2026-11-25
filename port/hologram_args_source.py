"""Set up numeric DrawHologram arguments without replaying the bytecode prologue."""

from runtime_verification_source import replace_once


HOLOGRAM_ARGS = r'''
enum {
    OP_PACKED_HOLOGRAM_ARGS = 0x94,
    HOLOGRAM_EXIT = 0x220,
    HOLOGRAM_BODY = 0x224,
    HOLOGRAM_ARGUMENTS = 12,
};

static const CodeEntry* hologramArgsCode;
static int32_t hologramGuiRatio;
static unsigned hologramArgsCalls, hologramArgsHidden;

static void bindHologramArgs(VMContext* ctx) {
    hologramArgsCode = nullptr;
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_DrawHologram", 2188, 0xe75f7a8cu);
    if (code == nullptr || code->localsCount != 21) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const Variable* guiRatio = resolveVarDef(ctx, resolveVarOperand(bytes + 0x94));
    if (guiRatio->varID < 0 || strcmp(guiRatio->name, "guiRatio") != 0 ||
        guiRatio->varID != resolveVarDef(ctx, resolveVarOperand(bytes + 0xd0))->varID) return;
    static const uint16_t assignments[] = {0x14, 0x2c, 0x44, 0x5c, 0x74, 0x8c,
                                          0x9c, 0xd8, 0x110, 0x16c, 0x1a4, 0x1dc};
    for (unsigned i = 0; i < HOLOGRAM_ARGUMENTS; ++i) {
        if (resolveVarDef(ctx, resolveVarOperand(bytes + assignments[i]))->varID != (int32_t)(i + 1))
            return;
    }
    hologramArgsCode = code;
    hologramGuiRatio = guiRatio->varID;
}

static void installHologramArgs(VMContext* ctx) {
    if (hologramArgsCode == nullptr) return;
    uint8_t* bytes = codeBytes(ctx, hologramArgsCode);
    const OpWord first = {BinaryUtils_readUint32Aligned(bytes)};
    if (OpWord_opcode(first) != OP_PACKED_ARGUMENT_ARRAY || OpWord_extra(first) != 0) return;
    BinaryUtils_writeUint32(bytes, (first.value & 0x00ffffffu) | ((uint32_t)OP_PACKED_HOLOGRAM_ARGS << 24));
    logInfo("HLD: bound native hologram arguments\n");
}

static bool hologramNumber(RValue value) {
    return value.type == RVALUE_INT32 || value.type == RVALUE_BOOL ||
           (value.type == RVALUE_REAL && isfinite(value.real));
}

#ifdef HLD_VERIFY_HOLOGRAM_ARGS
static const CallFrame* predictedHologramFrame;
static RValue predictedHologramArgs[HOLOGRAM_ARGUMENTS];
static uint32_t predictedHologramNext;
static int32_t predictedHologramStack;

static void verifyHologramArgs(VMContext* ctx, uint32_t next) {
    const CallFrame* frame = ctx->currentCallFrame;
    if (predictedHologramFrame != frame) return;
    require(next == predictedHologramNext && ctx->stack.top == predictedHologramStack);
    for (unsigned i = 0; i < HOLOGRAM_ARGUMENTS; ++i) {
        const RValue actual = frame->localVars[i + 1], expected = predictedHologramArgs[i];
        require(actual.type == expected.type && actual.gmlStackType == expected.gmlStackType &&
                actual.assetRefType == expected.assetRefType && actual.ownsReference == expected.ownsReference);
        if (actual.type == RVALUE_REAL)
            require(memcmp(&actual.real, &expected.real, sizeof(GMLReal)) == 0);
        else
            require(actual.int32 == expected.int32);
    }
    predictedHologramFrame = nullptr;
    ++hologramArgsCalls;
    hologramArgsHidden += next == HOLOGRAM_EXIT;
}
#endif

static uint32_t prepareHologramArgs(VMContext* ctx) {
    CallFrame* frame = ctx->currentCallFrame;
    const int32_t count = frame->scriptArgsCount;
    if (count < 6 || count > HOLOGRAM_ARGUMENTS || frame->scriptArgs == nullptr ||
        frame->localVarsCount <= HOLOGRAM_ARGUMENTS || ctx->runner == nullptr ||
        ctx->globalScopeInstance == nullptr) return 0;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 1.0)) return 0;

    RValue values[HOLOGRAM_ARGUMENTS];
    for (int32_t i = 0; i < count; ++i) {
        values[i] = packedScriptArgument(frame, i);
        if (!hologramNumber(values[i])) return 0;
        values[i].gmlStackType = GML_TYPE_VARIABLE;
    }
    if (count < 8) {
        RValue ratio = Instance_getSelfVar(ctx->globalScopeInstance, hologramGuiRatio);
        if (!hologramNumber(ratio)) return 0;
        ratio.gmlStackType = GML_TYPE_VARIABLE;
        if (count == 6) values[6] = ratio;
        values[7] = ratio;
    }
    if (count < 9) values[8] = RValue_makeReal(1);
    if (RValue_toReal(values[5]) < -epsilon) values[8] = RValue_makeReal(0);
    if (count < 10) values[9] = values[7];
    if (count < 11) values[10] = RValue_makeReal(0);
    if (count < 12) values[11] = RValue_makeReal(1);

    // The original exits at zero alpha before any draw, self write or RNG use.
    const uint32_t next = GMLReal_fabs(RValue_toReal(values[8])) <= epsilon ? HOLOGRAM_EXIT : HOLOGRAM_BODY;
#ifdef HLD_VERIFY_HOLOGRAM_ARGS
    require(predictedHologramFrame == nullptr);
    predictedHologramFrame = frame;
    memcpy(predictedHologramArgs, values, sizeof(values));
    predictedHologramNext = next;
    predictedHologramStack = ctx->stack.top;
    return 0;
#else
    for (unsigned i = 0; i < HOLOGRAM_ARGUMENTS; ++i) {
        RValue_writeIntoSlotStealingOwnershipOrCopying(&frame->localVars[i + 1], values[i]);
    }
    ++hologramArgsCalls;
    hologramArgsHidden += next == HOLOGRAM_EXIT;
    return next;
#endif
}
'''


def with_hologram_args(code, *, checked=False, launch_flag=None):
    helper = HOLOGRAM_ARGS
    if checked:
        helper = "#define HLD_VERIFY_HOLOGRAM_ARGS\n" + helper
    if launch_flag:
        anchor = "    hologramArgsCode = nullptr;"
        helper = f"extern bool {launch_flag};\n" + replace_once(
            helper, anchor, anchor + f"\n    if (!{launch_flag}) return;")
        helper += f'''
void hldReportHologramArgs(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita hologram arguments frame=%d enabled=%d calls=%u hidden=%u\\n",
            frame, (int){launch_flag}, hologramArgsCalls, hologramArgsHidden);
    hologramArgsCalls = hologramArgsHidden = 0;
}}
'''
    else:
        helper += r'''
void hldReportHologramArgsAtExit(void) {
    logInfo("HLD: hologram arguments calls=%u hidden=%u\n", hologramArgsCalls, hologramArgsHidden);
}
'''
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    code = replace_once(code, anchor, helper + "\n" + anchor)
    anchor = "    packHldLiteralInstructions(ctx);"
    code = replace_once(code, anchor, "    bindHologramArgs(ctx);\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    code = replace_once(code, anchor, anchor + "\n    installHologramArgs(ctx);")

    anchor = "            case OP_PACKED_ARGUMENT:\n            case OP_PACKED_ARGUMENT_ARRAY: {\n"
    start = code.index(anchor) + len(anchor)
    end = code.index("\n            }\n", start)
    fallback = replace_once(code[start:end], "if (opcode == OP_PACKED_ARGUMENT_ARRAY) ip += 12;", "ip += 12;")
    code = replace_once(code, anchor, r'''            case OP_PACKED_HOLOGRAM_ARGS: {
                const uint32_t next = prepareHologramArgs(ctx);
                if (next != 0) {
                    ip = next;
                    break;
                }
                // Restore the exact packed argument action for unsupported inputs.
''' + fallback + "\n            }\n" + anchor)
    if checked:
        for opcode, position in (("OP_PACKED_LITERAL_VALUE", "HOLOGRAM_BODY"), ("OP_EXIT", "HOLOGRAM_EXIT")):
            anchor = f"            case {opcode}:\n"
            code = replace_once(code, anchor, anchor + f'''                if (code == hologramArgsCode && instrAddr == {position})
                    verifyHologramArgs(ctx, {position});
''')
    return code


def with_hologram_args_exit(code):
    anchor = "    Runner_free(runner);"
    return replace_once(code, anchor, "    extern void hldReportHologramArgsAtExit(void);\n"
                        "    hldReportHologramArgsAtExit();\n" + anchor)
