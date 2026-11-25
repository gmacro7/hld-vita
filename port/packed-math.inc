enum { OP_PACKED_MATH = 0xdc, OP_PACKED_CALL_DISCARD = 0xdd };

typedef enum {
    MATH_NONE, MATH_ABS, MATH_SIGN, MATH_FLOOR, MATH_CEIL, MATH_ROUND,
    MATH_MIN, MATH_MAX, MATH_CLAMP,
} PackedMath;

static void packHldMathInstructions(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    static const struct {
        const char* name;
        PackedMath kind;
        int32_t args;
    } math[] = {
        {"abs", MATH_ABS, 1}, {"sign", MATH_SIGN, 1}, {"floor", MATH_FLOOR, 1},
        {"ceil", MATH_CEIL, 1}, {"round", MATH_ROUND, 1}, {"min", MATH_MIN, 2},
        {"max", MATH_MAX, 2}, {"clamp", MATH_CLAMP, 3},
    };
    uint8_t* kinds = safeCalloc(data->func.functionCount, 1);
    uint8_t* arities = safeCalloc(data->func.functionCount, 1);
    for (uint32_t index = 0; index < data->func.functionCount; ++index) {
        FuncCallCache* cache = &ctx->funcCallCache[index];
        if (cache->builtin == nullptr || cache->scriptCodeIndex >= 0) continue;
        const char* name = data->func.functions[index].name;
        for (size_t op = 0; op < sizeof(math) / sizeof(*math); ++op) {
            if (strcmp(name, math[op].name) == 0) {
                kinds[index] = math[op].kind;
                arities[index] = math[op].args;
                break;
            }
        }
    }
    unsigned packed = 0, discarded = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            OpWord word = {BinaryUtils_readUint32Aligned(bytes + offset)};
            uint32_t addr = offset;
            offset += 4 + OpWord_operandSize(word);
            if (OpWord_opcode(word) != OP_CALL || entry->length - addr < 8 || OpWord_type2(word) != 0) continue;
            uint32_t func = resolveFuncOperand(bytes + addr + 4);
            if (func >= data->func.functionCount) continue;
            if (entry->length - addr >= 12 && BinaryUtils_readUint32Aligned(bytes + addr + 8) == 0x9e050000u) {
                BinaryUtils_writeUint32(bytes + addr, ((uint32_t)OP_PACKED_CALL_DISCARD << 24) | (word.value & 0x00ffffffu));
                ++discarded;
                continue;
            }
            if (kinds[func] == MATH_NONE || OpWord_extra(word) != arities[func]) continue;
            const uint32_t opcode = ((uint32_t)OP_PACKED_MATH << 24) | ((uint32_t)kinds[func] << 20);
            BinaryUtils_writeUint32(bytes + addr, opcode | (word.value & 0x000fffffu));
            ++packed;
        }
    }
    free(arities);
    free(kinds);
    logInfo("HLD: packed %u calls to scalar math builtins and %u discarded results\n", packed, discarded);
}

static bool executePackedMath(VMContext* ctx, OpWord word) {
    const int32_t count = OpWord_extra(word);
    if (count < 1 || count > 3 || ctx->stack.top < count) return false;
    GMLReal arguments[3];
    const RValue* top = ctx->stack.slots + ctx->stack.top;
    for (int32_t index = 0; index < count; ++index) {
        const RValue* value = --top;
        if (value->type == RVALUE_REAL) {
            arguments[index] = value->real;
        } else if (value->type == RVALUE_INT32 || value->type == RVALUE_BOOL) {
            arguments[index] = value->int32;
        } else {
            return false;
        }
    }
    GMLReal result = arguments[0];
    switch ((PackedMath)OpWord_type2(word)) {
        case MATH_ABS: result = GMLReal_fabs(result); break;
        case MATH_SIGN: result = result > 0.0 ? 1.0 : result < 0.0 ? -1.0 : 0.0; break;
        case MATH_FLOOR: result = GMLReal_floor(result); break;
        case MATH_CEIL: result = GMLReal_ceil(result); break;
        case MATH_ROUND: result = GMLReal_bankersRound(result); break;
        case MATH_MIN:
            result = INFINITY;
            if (result > arguments[0]) result = arguments[0];
            if (result > arguments[1]) result = arguments[1];
            break;
        case MATH_MAX:
            result = -INFINITY;
            if (arguments[0] > result) result = arguments[0];
            if (arguments[1] > result) result = arguments[1];
            break;
        case MATH_CLAMP:
            if (arguments[1] > result) result = arguments[1];
            if (result > arguments[2]) result = arguments[2];
            break;
        default: return false;
    }
    ctx->stack.top -= count;
    stackPushTyped(ctx, RValue_makeReal(result), GML_TYPE_VARIABLE);
    return true;
}
