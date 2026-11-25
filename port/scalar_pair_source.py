"""Fuse adjacent scalar reads and their numeric consumer without moving targets."""


def with_scalar_pairs(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    start = code.index("                const uint16_t operand = (uint16_t)OpWord_extra(opword);",
                       code.index("            case OP_PACKED_VAR_READ: {"))
    end = code.index("                if (OpWord_type2(opword) == 0xF) {", start)
    read = code[start:end]
    helper = "static inline RValue readPackedScalar(VMContext* ctx, OpWord opword, const uint8_t* extraData) {\n"
    helper += "\n".join(line[12:] for line in read.splitlines()) + "\n    return value;\n}\n"
    replace(read, "                RValue value = readPackedScalar(ctx, opword, extraData);\n")
    helper += r'''
enum { OP_PACKED_SCALAR_PAIR = 0xc8, PAIR_FIRST_ARGUMENT = 1 };

static bool isPairOperand(OpWord word) {
    const uint8_t op = OpWord_opcode(word);
    return (op == OP_PACKED_VAR_READ && OpWord_type2(word) != 0xf) ||
           (op == OP_PACKED_ARGUMENT && OpWord_type2(word) != PACKED_ARGUMENT_TO_LOCAL);
}

static void packScalarPairs(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned pairs = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        const CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t addr = 0; addr + 4 <= entry->length;) {
            const OpWord first = {BinaryUtils_readUint32Aligned(bytes + addr)};
            const uint32_t start = addr;
            addr += 4 + OpWord_operandSize(first);
            if (!isPairOperand(first) || entry->length - start < 20) continue;
            const OpWord second = {BinaryUtils_readUint32Aligned(bytes + start + 8)};
            const OpWord consumer = {BinaryUtils_readUint32Aligned(bytes + start + 16)};
            if (!isPairOperand(second) || OpWord_type1(consumer) != GML_TYPE_VARIABLE ||
                OpWord_type2(consumer) != GML_TYPE_VARIABLE) continue;
            const uint8_t op = OpWord_opcode(consumer);
            if (op == OP_CMP) {
                if (entry->length - start < 24 || OpWord_comparisonFunction(consumer) < CMPOP_LESS_THAN ||
                    OpWord_comparisonFunction(consumer) > CMPOP_GREATER_THAN) continue;
                const OpWord branch = {BinaryUtils_readUint32Aligned(bytes + start + 20)};
                if (OpWord_opcode(branch) != OP_BT && OpWord_opcode(branch) != OP_BF) continue;
            } else if (op != OP_ADD && op != OP_SUB && op != OP_MUL) continue;
            const uint32_t flags = OpWord_opcode(first) == OP_PACKED_ARGUMENT ? PAIR_FIRST_ARGUMENT : 0;
            BinaryUtils_writeUint32(bytes + start, ((uint32_t)OP_PACKED_SCALAR_PAIR << 24) |
                (flags << 20) | (first.value & 0x000fffffu));
            ++pairs;
        }
    }
    logInfo("HLD: fused %u scalar read pairs at load time\n", pairs);
}

static inline bool scalarPairNumeric(RValue value) {
    return !value.ownsReference && (value.type == RVALUE_INT32 || value.type == RVALUE_REAL);
}

static inline bool scalarPairComparison(GMLReal diff, GMLReal epsilon, uint8_t comparison) {
    const int cmp = GMLReal_fabs(diff) <= epsilon ? 0 : diff < 0 ? -1 : 1;
    switch (comparison) {
        case CMPOP_LESS_THAN: return cmp < 0;
        case CMPOP_LESS_THAN_OR_EQUAL: return cmp <= 0;
        case CMPOP_EQUAL: return cmp == 0;
        case CMPOP_NOT_EQUAL: return cmp != 0;
        case CMPOP_GREATER_THAN_OR_EQUAL: return cmp >= 0;
        case CMPOP_GREATER_THAN: return cmp > 0;
        default: abort();
    }
}
'''
    if compare:
        helper += r'''
static unsigned fusedScalarPairs;

static bool useScalarPairs(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportScalarPairs(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scalar pairs frame=%d enabled=%d fused=%u\n",
            frame, (int)useScalarPairs(frame - 1), fusedScalarPairs);
    fusedScalarPairs = 0;
}
'''
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, helper + "\n" + anchor)
    replace("    packHldArguments(ctx);", "    packHldArguments(ctx);\n    packScalarPairs(ctx);")

    handler = r'''            case OP_PACKED_SCALAR_PAIR: {
                RValue a = OpWord_type2(opword) == PAIR_FIRST_ARGUMENT
                    ? packedScriptArgument(ctx->currentCallFrame, (uint16_t)OpWord_extra(opword))
                    : readPackedScalar(ctx, opword, extraData);
'''
    if compare:
        handler += r'''                if (ctx->runner == nullptr || !useScalarPairs(ctx->runner->frameCount)) {
                    stackPushTyped(ctx, a, GML_TYPE_VARIABLE);
                    break;
                }
'''
    handler += r'''                const OpWord second = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                const OpWord consumer = {BinaryUtils_readUint32Aligned(bytecodeBase + ip + 8)};
                RValue b = OpWord_opcode(second) == OP_PACKED_ARGUMENT
                    ? packedScriptArgument(ctx->currentCallFrame, (uint16_t)OpWord_extra(second))
                    : readPackedScalar(ctx, second, bytecodeBase + ip + 4);
                ip += 8;
                if (scalarPairNumeric(a) && scalarPairNumeric(b)) {
                    const uint8_t op = OpWord_opcode(consumer);
                    if (op == OP_CMP) {
                        const GMLReal left = RValue_toReal(a), right = RValue_toReal(b);
                        const GMLReal epsilon = ctx->runner != nullptr ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
                        const bool condition = scalarPairComparison(left - right, epsilon,
                                                                    OpWord_comparisonFunction(consumer));
                        uint32_t next = ip + 4;
                        if (consumeConditionalBranch(bytecodeBase, codeLength, &next, condition)) {
                            ip = next;
                            COUNT_SCALAR_PAIR();
                            break;
                        }
                    } else if (a.type == RVALUE_INT32 && b.type == RVALUE_INT32) {
                        int32_t result;
                        const bool overflow = op == OP_ADD ? __builtin_add_overflow(a.int32, b.int32, &result)
                            : op == OP_SUB ? __builtin_sub_overflow(a.int32, b.int32, &result)
                                           : __builtin_mul_overflow(a.int32, b.int32, &result);
                        if (!overflow) {
                            stackPushTyped(ctx, RValue_makeInt32(result), GML_TYPE_VARIABLE);
                            ip += 4;
                            COUNT_SCALAR_PAIR();
                            break;
                        }
                    } else {
                        const GMLReal left = RValue_toReal(a), right = RValue_toReal(b);
                        const GMLReal result = op == OP_ADD ? left + right : op == OP_SUB ? left - right : left * right;
                        stackPushTyped(ctx, RValue_makeReal(result), GML_TYPE_VARIABLE);
                        ip += 4;
                        COUNT_SCALAR_PAIR();
                        break;
                    }
                }
                stackPushTyped(ctx, a, GML_TYPE_VARIABLE);
                stackPushTyped(ctx, b, GML_TYPE_VARIABLE);
                break;
            }
'''
    handler = (handler.replace("COUNT_SCALAR_PAIR();", "++fusedScalarPairs;") if compare else
               "".join(line for line in handler.splitlines(keepends=True) if "COUNT_SCALAR_PAIR" not in line))
    anchor = "            case OP_PACKED_VAR_READ: {"
    replace(anchor, handler + anchor)
    return code
