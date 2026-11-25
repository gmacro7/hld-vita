"""Decode constant array reads once and shorten the hot viewport reads."""


def with_fixed_array_reads(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    support = r'''
enum { OP_PACKED_FIXED_ARRAY = 0x8a };
enum { FIXED_ARRAY_GENERIC, FIXED_VIEW_X, FIXED_VIEW_Y, FIXED_VIEW_W, FIXED_VIEW_H };

static unsigned fixedArrayKind(VMContext* ctx, int32_t scope, uint32_t operand) {
    if (scope != INSTANCE_SELF) return FIXED_ARRAY_GENERIC;
    const Variable* var = resolveVarDef(ctx, operand);
    if (var->varID != VARIABLE_BUILTIN) return FIXED_ARRAY_GENERIC;
    switch (var->builtinVarId) {
        case BUILTIN_VAR_VIEW_XVIEW: return FIXED_VIEW_X;
        case BUILTIN_VAR_VIEW_YVIEW: return FIXED_VIEW_Y;
        case BUILTIN_VAR_VIEW_WVIEW: return FIXED_VIEW_W;
        case BUILTIN_VAR_VIEW_HVIEW: return FIXED_VIEW_H;
        default: return FIXED_ARRAY_GENERIC;
    }
}

static void packFixedArrayReads(VMContext* ctx) {
    const DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned fixed = 0, views = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        const CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t next = 0; next + 4 <= entry->length;) {
            const uint32_t start = next;
            const OpWord first = {BinaryUtils_readUint32Aligned(bytes + start)};
            next += 4 + OpWord_operandSize(first);
            if (OpWord_opcode(first) != OP_PUSH_IMMEDIATE || entry->length - start < 16) continue;
            const int32_t scope = OpWord_extra(first);
            if (scope != INSTANCE_SELF && scope != INSTANCE_OTHER && scope != INSTANCE_LOCAL && scope != INSTANCE_GLOBAL) continue;
            const OpWord index = {BinaryUtils_readUint32Aligned(bytes + start + 4)};
            if (OpWord_opcode(index) != OP_PUSH_IMMEDIATE) continue;
            const OpWord read = {BinaryUtils_readUint32Aligned(bytes + start + 8)};
            if (OpWord_opcode(read) != OP_PUSH || OpWord_type1(read) != GML_TYPE_VARIABLE) continue;
            const uint32_t operand = resolveVarOperand(bytes + start + 12);
            if (((operand >> 24) & 0xf8) != VARTYPE_ARRAY) continue;
            const unsigned kind = fixedArrayKind(ctx, scope, operand);
            BinaryUtils_writeUint32(bytes + start, ((uint32_t)OP_PACKED_FIXED_ARRAY << 24) |
                (kind << 20) | (first.value & 0x000fffffu));
            ++fixed;
            if (kind != FIXED_ARRAY_GENERIC) ++views;
        }
    }
    logInfo("HLD: bound %u constant array reads, including %u view fields\n", fixed, views);
}

static inline RValue readFixedView(Runner* runner, int32_t index, unsigned kind) {
    GMLCamera* camera = Runner_getCameraForView(runner, index);
    if (camera == nullptr) return RValue_makeReal(0.0);
    switch (kind) {
        case FIXED_VIEW_X: return RValue_makeReal((GMLReal)camera->viewX);
        case FIXED_VIEW_Y: return RValue_makeReal((GMLReal)camera->viewY);
        case FIXED_VIEW_W: return RValue_makeReal((GMLReal)camera->viewWidth);
        case FIXED_VIEW_H: return RValue_makeReal((GMLReal)camera->viewHeight);
        default: abort();
    }
}
'''
    if compare:
        support += r'''
static unsigned fixedViewReads;

static bool useFixedViews(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportFixedViews(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita fixed view reads frame=%d enabled=%d reads=%u\n",
            frame, (int)useFixedViews(frame - 1), fixedViewReads);
    fixedViewReads = 0;
}
'''
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, support + "\n" + anchor)
    replace("    packHldArguments(ctx);", "    packHldArguments(ctx);\n    packFixedArrayReads(ctx);")

    mode = " && useFixedViews(ctx->runner->frameCount)" if compare else ""
    handler = r'''            case OP_PACKED_FIXED_ARRAY: {
                const OpWord index = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                const OpWord read = {BinaryUtils_readUint32Aligned(bytecodeBase + ip + 4)};
                const unsigned kind = OpWord_type2(opword);
                const int32_t arrayIndex = OpWord_extra(index);
                const Instance* self = ctx->currentCallFrame->self;
                const bool direct = kind != FIXED_ARRAY_GENERIC && ctx->runner != nullptr &&
                                    (self == nullptr || self->objectIndex != STRUCT_OBJECT_INDEX)MODE;
                RValue value;
                if (direct) {
                    value = readFixedView(ctx->runner, arrayIndex, kind);
COUNT_READ
                }
#ifndef HLD_VERIFY_FIXED_VIEWS
                else
#endif
                {
                    const uint32_t operand = resolveVarOperand(bytecodeBase + ip + 8);
                    const ArrayAccess access = {.arrayIndex = arrayIndex, .instanceType = OpWord_extra(opword),
                        .isArray = true, .hasInstanceType = true};
                    const RValue original = resolveVariableReadWithAccess(ctx, OpWord_extra(read), operand, access);
#ifdef HLD_VERIFY_FIXED_VIEWS
                    if (direct) {
                        require(original.type == RVALUE_REAL && value.type == RVALUE_REAL);
                        require(original.real == value.real || (isnan(original.real) && isnan(value.real)));
                    } else
#endif
                    value = original;
                }
                ip += 12;
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                break;
            }
'''
    handler = handler.replace("MODE", mode).replace("COUNT_READ\n", "                    ++fixedViewReads;\n" if compare else "")
    anchor = "            case OP_PACKED_VAR_READ: {"
    replace(anchor, handler + anchor)
    return code
