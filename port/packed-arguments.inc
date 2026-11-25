enum { OP_PACKED_ARGUMENT = 0xc7, OP_PACKED_ARGUMENT_ARRAY = 0x89 };
enum { PACKED_ARGUMENT_TO_LOCAL = 1 };

static int hldBoundArgument(VMContext* ctx, uint32_t ref, bool array) {
    const Variable* variable = resolveVarDef(ctx, ref);
    if (variable->varID != VARIABLE_BUILTIN) return -1;
    const int builtin = variable->builtinVarId;
    if (array) return builtin == BUILTIN_VAR_ARGUMENT ? 0 : -1;
    if (builtin < BUILTIN_VAR_ARGUMENT0 || builtin > BUILTIN_VAR_ARGUMENT15) return -1;
    return builtin - BUILTIN_VAR_ARGUMENT0;
}

static void packHldArguments(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
#ifdef PLATFORM_VITA
    FILE* flag = fopen("ux0:data/hld-vm/prepared-script-args", "r");
    if (flag == nullptr) return;
    fclose(flag);
#endif
    unsigned reads = 0, stores = 0;
    for (uint32_t i = 0; i < data->code.count; ++i) {
        CodeEntry* entry = &data->code.entries[i];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            const uint32_t address = offset;
            const OpWord first = {BinaryUtils_readUint32Aligned(bytes + address)};
            offset += 4 + OpWord_operandSize(first);
            const uint8_t opcode = OpWord_opcode(first);
            unsigned packedOpcode = 0, packedLength = 0;
            int argument = -1;

            if ((opcode == OP_PUSH_BUILTIN || opcode == OP_PUSH) && offset - address == 8 &&
                offset <= entry->length && OpWord_type1(first) == GML_TYPE_VARIABLE &&
                OpWord_extra(first) == INSTANCE_SELF) {
                const uint32_t ref = resolveVarOperand(bytes + address + 4);
                if ((ref >> 24 & 0xf8) == VARTYPE_NORMAL) {
                    argument = hldBoundArgument(ctx, ref, false);
                    packedLength = 8;
                    packedOpcode = OP_PACKED_ARGUMENT;
                }
            } else if (opcode == OP_PUSH_IMMEDIATE && OpWord_extra(first) == INSTANCE_SELF &&
                       entry->length - address >= 16) {
                const OpWord second = {BinaryUtils_readUint32Aligned(bytes + address + 4)};
                const OpWord read = {BinaryUtils_readUint32Aligned(bytes + address + 8)};
                if (OpWord_opcode(second) == OP_PUSH_IMMEDIATE && OpWord_extra(second) >= 0 &&
                    OpWord_opcode(read) == OP_PUSH && OpWord_type1(read) == GML_TYPE_VARIABLE) {
                    const uint32_t ref = resolveVarOperand(bytes + address + 12);
                    if ((ref >> 24 & 0xf8) == VARTYPE_ARRAY && hldBoundArgument(ctx, ref, true) >= 0) {
                        argument = OpWord_extra(second);
                        packedLength = 16;
                        packedOpcode = OP_PACKED_ARGUMENT_ARRAY;
                    }
                }
            }
            if (argument < 0) continue;
            unsigned flags = 0;
            if (entry->length - address >= packedLength + 8) {
                const OpWord store = {BinaryUtils_readUint32Aligned(bytes + address + packedLength)};
                if (OpWord_opcode(store) == OP_PACKED_VAR_WRITE &&
                    OpWord_type2(store) == GML_TYPE_VARIABLE &&
                    (uint16_t)OpWord_extra(store) >> 14 == PACKED_LOCAL) {
                    flags = PACKED_ARGUMENT_TO_LOCAL;
                    ++stores;
                }
            }
            BinaryUtils_writeUint32(bytes + address, (packedOpcode << 24) | (flags << 20) |
                                    (GML_TYPE_VARIABLE << 16) | (uint32_t)argument);
            ++reads;
        }
    }
    logInfo("HLD: bound %u script argument reads, %u followed by a local assignment\n", reads, stores);
}

static inline RValue packedScriptArgument(const CallFrame* frame, unsigned index) {
    if (frame->scriptArgs == nullptr || frame->scriptArgsCount <= (int32_t)index)
        return RValue_makeUndefined();
    RValue result = frame->scriptArgs[index];
    result.ownsReference = false;
    return result;
}
