enum { OP_PACKED_PROPERTY_READ = 0xc6 };
enum { FIELD_UNBOUND, FIELD_FLOAT, FIELD_REAL, FIELD_INT, FIELD_UINT, FIELD_BOOL };

static uint32_t encodeInstanceProperty(int16_t property) {
#define FIELD_TYPE_FIELD_FLOAT float
#define FIELD_TYPE_FIELD_REAL GMLReal
#define FIELD_TYPE_FIELD_INT int32_t
#define FIELD_TYPE_FIELD_UINT uint32_t
#define FIELD_TYPE_FIELD_BOOL bool
#define INSTANCE_PROPERTY_CASE(name, field, kind) \
    case BUILTIN_VAR_##name: { \
        _Static_assert(offsetof(Instance, field) <= UINT16_MAX, "Packed instance offset overflows"); \
        _Static_assert(_Generic(((Instance*)0)->field, FIELD_TYPE_##kind: 1, default: 0), \
                       "Packed instance field type changed"); \
        return ((uint32_t)(kind) << 20) | (uint32_t)offsetof(Instance, field); \
    }
    switch (property) {
        INSTANCE_PROPERTY_CASE(X, x, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(Y, y, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(XPREVIOUS, xprevious, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(YPREVIOUS, yprevious, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(XSTART, xstart, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(YSTART, ystart, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(IMAGE_SPEED, imageSpeed, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(IMAGE_INDEX, imageIndex, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(IMAGE_ALPHA, imageAlpha, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(IMAGE_XSCALE, imageXscale, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(IMAGE_YSCALE, imageYscale, FIELD_FLOAT)
        INSTANCE_PROPERTY_CASE(IMAGE_ANGLE, imageAngle, FIELD_REAL)
        INSTANCE_PROPERTY_CASE(IMAGE_BLEND, imageBlend, FIELD_UINT)
        INSTANCE_PROPERTY_CASE(DEPTH, depth, FIELD_REAL)
        INSTANCE_PROPERTY_CASE(SPRITE_INDEX, spriteIndex, FIELD_INT)
        INSTANCE_PROPERTY_CASE(MASK_INDEX, maskIndex, FIELD_INT)
        INSTANCE_PROPERTY_CASE(OBJECT_INDEX, objectIndex, FIELD_INT)
        INSTANCE_PROPERTY_CASE(ID, instanceId, FIELD_UINT)
        INSTANCE_PROPERTY_CASE(VISIBLE, visible, FIELD_BOOL)
        default: return 0;
    }
#undef INSTANCE_PROPERTY_CASE
#undef FIELD_TYPE_FIELD_FLOAT
#undef FIELD_TYPE_FIELD_REAL
#undef FIELD_TYPE_FIELD_INT
#undef FIELD_TYPE_FIELD_UINT
#undef FIELD_TYPE_FIELD_BOOL
}

static void packHldInstanceProperties(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned reads = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            const uint32_t addr = offset;
            OpWord word = {BinaryUtils_readUint32Aligned(bytes + offset)};
            offset += 4 + OpWord_operandSize(word);
            const uint8_t opcode = OpWord_opcode(word);
            if ((opcode != OP_PUSH && opcode != OP_PUSH_BUILTIN) || entry->length - addr < 8 ||
                OpWord_type1(word) != GML_TYPE_VARIABLE || OpWord_extra(word) != INSTANCE_SELF) continue;
            const uint32_t ref = resolveVarOperand(bytes + addr + 4);
            if ((ref >> 24 & 0xF8) != VARTYPE_NORMAL) continue;
            const Variable* definition = resolveVarDef(ctx, ref);
            if (definition->varID != VARIABLE_BUILTIN) continue;
            const uint32_t field = encodeInstanceProperty(definition->builtinVarId);
            if (field == 0) continue;
            BinaryUtils_writeUint32(bytes + addr, ((uint32_t)OP_PACKED_PROPERTY_READ << 24) |
                                    ((uint32_t)GML_TYPE_VARIABLE << 16) | field);
            ++reads;
        }
    }
    logInfo("HLD: bound %u reads of scalar instance properties at load time\n", reads);
}
