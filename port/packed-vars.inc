enum { OP_PACKED_VAR_READ = 0xc4, OP_PACKED_VAR_WRITE = 0xc5 };
enum { PACKED_SELF, PACKED_LOCAL, PACKED_GLOBAL, PACKED_OTHER, PACKED_VAR_ID_MASK = 0x3fff };

static void packHldVariableInstructions(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned reads = 0, writes = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        uint8_t previousOpcode = 0;
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            OpWord word = {BinaryUtils_readUint32Aligned(bytes + offset)};
            uint32_t addr = offset;
            offset += 4 + OpWord_operandSize(word);
            uint8_t opcode = OpWord_opcode(word);
            bool read = opcode == OP_PUSH || opcode == OP_PUSH_LOCAL || opcode == OP_PUSH_GLOBAL;
            bool write = opcode == OP_POP && previousOpcode != OP_PACKED_LITERAL_STORE;
            previousOpcode = opcode;
            if ((!read && !write) || entry->length - addr < 8 || OpWord_type1(word) != GML_TYPE_VARIABLE) continue;
            uint32_t ref = resolveVarOperand(bytes + addr + 4);
            if ((ref >> 24 & 0xF8) != VARTYPE_NORMAL) continue;
            unsigned scope;
            if (opcode == OP_PUSH_LOCAL) {
                scope = PACKED_LOCAL;
            } else if (opcode == OP_PUSH_GLOBAL) {
                scope = PACKED_GLOBAL;
            } else {
                switch (OpWord_extra(word)) {
                    case INSTANCE_SELF: scope = PACKED_SELF; break;
                    case INSTANCE_LOCAL: scope = PACKED_LOCAL; break;
                    case INSTANCE_GLOBAL: scope = PACKED_GLOBAL; break;
                    case INSTANCE_OTHER: scope = PACKED_OTHER; break;
                    default: continue;
                }
            }
            int32_t id = resolveVarDef(ctx, ref)->varID;
            if (id < 0 || id > PACKED_VAR_ID_MASK) continue;
            const uint32_t head = (uint32_t)(read ? OP_PACKED_VAR_READ : OP_PACKED_VAR_WRITE) << 24;
            BinaryUtils_writeUint32(bytes + addr, head | (word.value & 0x00ff0000u) | (scope << 14) | (uint32_t)id);
            if (read) ++reads;
            else ++writes;
        }
    }
    logInfo("HLD: bound %u scalar variable reads and %u writes at load time\n", reads, writes);
}

static inline Instance* packedScopeInstance(VMContext* ctx, unsigned scope) {
    if (scope == PACKED_SELF) return (Instance*)ctx->currentCallFrame->self;
    if (scope == PACKED_GLOBAL) return ctx->globalScopeInstance;
    return (Instance*)ctx->currentCallFrame->other;
}

static const int packedScopeTag[] = { INSTANCE_SELF, INSTANCE_LOCAL, INSTANCE_GLOBAL, INSTANCE_OTHER };
