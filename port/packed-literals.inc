enum {
    OP_PACKED_LITERAL_VALUE = 0x85,
    OP_PACKED_LITERAL_STORE = 0x87,
    OP_PACKED_LITERAL_BRANCH = 0x88
};

// Only the first opcode word changes. Its width and operand stay the same,
// and branches into the following instructions still use their original code.
static void packHldLiteralInstructions(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned packed = 0;
    for (uint32_t index = 0; index < data->code.count; index++) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldSceneryStepCode || entry == ctx->hldDistortionCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            OpWord op = {BinaryUtils_readUint32Aligned(bytes + offset)};
            uint32_t addr = offset;
            offset += 4 + OpWord_operandSize(op);
            if (OpWord_opcode(op) != OP_PUSH_IMMEDIATE || entry->length - addr < 8) continue;
            OpWord next = {BinaryUtils_readUint32Aligned(bytes + addr + 4)};
            uint8_t replacement = 0;
            if (next.value == 0x07520000u) {
                replacement = OP_PACKED_LITERAL_VALUE;
            } else if (entry->length - addr >= 12 && OpWord_opcode(next) == OP_POP &&
                       OpWord_type1(next) == GML_TYPE_VARIABLE && OpWord_type2(next) == GML_TYPE_INT32 &&
                       (resolveVarOperand(bytes + addr + 8) >> 24 & 0xF8) == VARTYPE_NORMAL) {
                replacement = OP_PACKED_LITERAL_STORE;
            } else if (entry->length - addr >= 12 && OpWord_opcode(next) == OP_CMP &&
                       OpWord_type1(next) == GML_TYPE_INT32 && OpWord_type2(next) == GML_TYPE_VARIABLE) {
                OpWord branch = {BinaryUtils_readUint32Aligned(bytes + addr + 8)};
                if (OpWord_opcode(branch) == OP_BT || OpWord_opcode(branch) == OP_BF) {
                    replacement = OP_PACKED_LITERAL_BRANCH;
                }
            }
            if (replacement != 0) {
                BinaryUtils_writeUint32(bytes + addr, (op.value & 0x00ffffffu) | ((uint32_t)replacement << 24));
                ++packed;
            }
        }
    }
    logInfo("HLD: packed %u literal bytecode sequences\n", packed);
}
