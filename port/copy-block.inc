enum {
    OP_PACKED_COPY_BLOCK = 0x8c,
    COPY_BLOCK_BITS = 12,
    COPY_BLOCK_MASK = (1 << COPY_BLOCK_BITS) - 1,
    COPY_BLOCK_MAX_FIELDS = 24
};

typedef struct {
    int32_t id;
    int16_t builtin;
    bool parent;
} CopyField;

typedef struct {
    CopyField read;
    CopyField write;
} FieldCopy;

typedef struct {
    uint32_t first;
    uint8_t count;
} FieldCopyBlock;

static const VMContext* copyBlockContext;
static FieldCopy* copyFields;
static FieldCopyBlock* copyBlocks;

static void releaseCopyBlocks(void) {
    arrfree(copyFields);
    arrfree(copyBlocks);
    copyBlockContext = nullptr;
}

static bool decodeCopyField(VMContext* ctx, const uint8_t* bytes, bool write, CopyField* field) {
    const OpWord word = {BinaryUtils_readUint32Aligned(bytes)};
    const uint8_t opcode = OpWord_opcode(word);
    if (OpWord_type1(word) != GML_TYPE_VARIABLE) return false;
    if (write && OpWord_type2(word) != GML_TYPE_VARIABLE) return false;
    if (!write && OpWord_type2(word) != 0) return false;

    if (opcode == (write ? OP_PACKED_VAR_WRITE : OP_PACKED_VAR_READ)) {
        const uint16_t operand = (uint16_t)OpWord_extra(word);
        const unsigned scope = operand >> 14;
        if (scope != PACKED_SELF && scope != PACKED_OTHER) return false;
        *field = (CopyField){operand & PACKED_VAR_ID_MASK, -1, scope == PACKED_OTHER};
        return true;
    }
    if (opcode != (write ? OP_POP : OP_PUSH)) return false;
    const int16_t scope = OpWord_extra(word);
    if (scope != INSTANCE_SELF && scope != INSTANCE_OTHER) return false;
    const uint32_t ref = resolveVarOperand(bytes + 4);
    if ((ref >> 24 & 0xf8) != VARTYPE_NORMAL) return false;
    const Variable* var = resolveVarDef(ctx, ref);
    if (var->varID >= 0) {
        *field = (CopyField){var->varID, -1, scope == INSTANCE_OTHER};
        return true;
    }
    if (var->varID != VARIABLE_BUILTIN) return false;
    const int16_t builtin = var->builtinVarId;
    if (builtin != BUILTIN_VAR_X && builtin != BUILTIN_VAR_Y && (write || builtin != BUILTIN_VAR_ID)) return false;
    *field = (CopyField){-1, builtin, scope == INSTANCE_OTHER};
    return true;
}

static void packCopyBlocks(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    releaseCopyBlocks();
    copyBlockContext = ctx;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        const CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t ip = 0; ip + 4 <= entry->length;) {
            const uint32_t at = ip;
            const OpWord word = {BinaryUtils_readUint32Aligned(bytes + at)};
            ip += 4 + OpWord_operandSize(word);
            if (OpWord_opcode(word) != OP_PUSH_ENV || arrlen(copyBlocks) > COPY_BLOCK_MASK) continue;
            const uint32_t distance = word.value & 0x00ffffffu;
            if (distance > COPY_BLOCK_MASK || distance < 9 || (distance - 1) % 4 != 0) continue;
            const uint32_t end = at + distance * sizeof(uint32_t);
            const unsigned count = (distance - 1) / 4;
            if (end + sizeof(uint32_t) > entry->length || count > COPY_BLOCK_MAX_FIELDS) continue;
            const OpWord pop = {BinaryUtils_readUint32Aligned(bytes + end)};
            if (OpWord_opcode(pop) != OP_POP_ENV || OpWord_branchOffset(pop) != 4 - (int32_t)(end - at)) continue;
            FieldCopy fields[COPY_BLOCK_MAX_FIELDS];
            unsigned field = 0;
            for (; field < count; ++field) {
                const uint8_t* pair = bytes + ip + 16 * field;
                if (!decodeCopyField(ctx, pair, false, &fields[field].read) ||
                    !decodeCopyField(ctx, pair + 8, true, &fields[field].write)) break;
            }
            if (field != count) continue;
            const uint32_t block = (uint32_t)arrlen(copyBlocks);
            const uint32_t first = (uint32_t)arrlen(copyFields);
            arrput(copyBlocks, ((FieldCopyBlock){first, count}));
            memcpy(arraddnptr(copyFields, count), fields, count * sizeof(*fields));
            BinaryUtils_writeUint32(bytes + at, ((uint32_t)OP_PACKED_COPY_BLOCK << 24) |
                                   (block << COPY_BLOCK_BITS) | distance);
            ip = end + sizeof(uint32_t);
        }
    }
    logInfo("HLD: bound %u instance-copy blocks with %u field copies at load time\n",
            (unsigned)arrlen(copyBlocks), (unsigned)arrlen(copyFields));
}

#ifdef HLD_COMPARE_COPY_BLOCKS
static unsigned copiedBlocks, copiedFields;

static inline bool useCopyBlocks(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportCopyBlocks(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita instance copy blocks frame=%d enabled=%d blocks=%u fields=%u\n",
            frame, (int)useCopyBlocks(frame - 1), copiedBlocks, copiedFields);
    copiedBlocks = copiedFields = 0;
}
#endif

static void copyInstanceFields(VMContext* ctx, Instance* parent, Instance* child, uint32_t blockIndex) {
    const FieldCopyBlock block = copyBlocks[blockIndex];
    const FieldCopy* fields = copyFields + block.first;
    for (uint32_t i = 0; i < block.count; ++i) {
        const FieldCopy* copy = &fields[i];
        Instance* from = copy->read.parent ? parent : child;
        Instance* to = copy->write.parent ? parent : child;
        RValue value = copy->read.builtin >= 0
            ? VMBuiltins_getVariable(ctx, from, copy->read.builtin, nullptr, -1)
            : Instance_getSelfVar(from, copy->read.id);
        value.gmlStackType = GML_TYPE_VARIABLE;
        if (copy->write.builtin >= 0) {
            VMBuiltins_setVariable(ctx, to, copy->write.builtin, nullptr, value, -1);
        } else {
            Instance_setSelfVar(to, copy->write.id, value);
        }
    }
#ifdef HLD_COMPARE_COPY_BLOCKS
    ++copiedBlocks;
    copiedFields += block.count;
#endif
}

static bool tryCopyBlock(VMContext* ctx, uint32_t blockIndex) {
    if (copyBlockContext != ctx || ctx->runner == nullptr || ctx->currentCallFrame->self == nullptr) return false;
#ifdef HLD_COMPARE_COPY_BLOCKS
    if (!useCopyBlocks(ctx->runner->frameCount)) return false;
#endif
    require(ctx->stack.top > 0);
    const RValue target = ctx->stack.slots[ctx->stack.top - 1];
    if (target.type != RVALUE_INT32 || target.int32 < INSTANCE_ID_BASE) return false;
    Instance* child = hmget(ctx->runner->instancesById, target.int32);
    Instance* parent = ctx->currentCallFrame->self;
    if (parent->objectIndex == STRUCT_OBJECT_INDEX || (child != nullptr && child->objectIndex == STRUCT_OBJECT_INDEX))
        return false;
    --ctx->stack.top;
    if (child != nullptr && child->active) copyInstanceFields(ctx, parent, child, blockIndex);
    return true;
}
