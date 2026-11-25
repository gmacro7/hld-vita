"""Fuse common scalar conversions into the existing variable read."""

from runtime_verification_source import replace_once


CONVERSIONS = r'''
enum { OP_PACKED_CONVERTED_READ = 0xca };
static unsigned convertedReadFast, convertedReadFallback;

static void packScalarReadConversions(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned bindings = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t pc = 0; pc + 4 <= entry->length;) {
            const OpWord read = {BinaryUtils_readUint32Aligned(bytes + pc)};
            const uint32_t start = pc;
            pc += 4 + OpWord_operandSize(read);
            if (OpWord_opcode(read) != OP_PACKED_VAR_READ || OpWord_type2(read) != 0 ||
                pc + 4 > entry->length) continue;
            const OpWord convert = {BinaryUtils_readUint32Aligned(bytes + pc)};
            const uint8_t type = OpWord_type2(convert);
            if (OpWord_opcode(convert) != OP_CONV || OpWord_type1(convert) != GML_TYPE_VARIABLE ||
                (type != GML_TYPE_INT32 && type != GML_TYPE_BOOL)) continue;
            // Leave the conversion word in place for other branch targets and runtime fallbacks.
            const uint32_t packed = (uint32_t)OP_PACKED_CONVERTED_READ << 24 |
                (uint32_t)type << 20 | (read.value & 0x000fffffu);
            BinaryUtils_writeUint32(bytes + start, packed);
            ++bindings;
        }
    }
    logInfo("HLD: fused %u variable reads with scalar conversions\n", bindings);
}

static inline bool convertPackedReadValue(RValue* value, uint8_t type) {
    if (value->ownsReference) return false;
    if (type == GML_TYPE_BOOL) {
        if (value->type == RVALUE_REAL) value->int32 = value->real > (GMLReal)0.5;
        else if (value->type == RVALUE_INT32) value->int32 = value->int32 > 0;
        else if (value->type != RVALUE_BOOL) return false;
        value->type = RVALUE_BOOL;
    } else {
        if (value->type == RVALUE_REAL) {
            const GMLReal real = value->real;
            if (!(real >= (GMLReal)INT32_MIN && real < (GMLReal)2147483648.0)) return false;
            value->int32 = (int32_t)real;
        } else if (value->type != RVALUE_INT32 && value->type != RVALUE_BOOL) {
            return false;
        }
        value->type = RVALUE_INT32;
    }
    return true;
}

void hldReportConvertedReadsAtExit(void) {
    logInfo("HLD: converted variable reads fast=%u fallback=%u\n", convertedReadFast, convertedReadFallback);
}
'''

CONVERTED_READ = r'''
                const uint8_t type = OpWord_type2(opword);
                if (!convertPackedReadValue(&value, type)) {
                    ++convertedReadFallback;
                    stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                    break;
                }
                ++convertedReadFast;
                ip += 4;
                if (!(type == GML_TYPE_BOOL && fuseInstructions &&
                      consumeConditionalBranch(bytecodeBase, codeLength, &ip, value.int32 != 0)))
                    stackPushTyped(ctx, value, type);
                break;
            }
'''


def with_packed_read_conversions(code, *, launch_flag=None):
    anchor = "static inline Instance* packedScopeInstance(VMContext* ctx, unsigned scope) {"
    helpers = CONVERSIONS
    if launch_flag:
        helpers += f"\nextern bool {launch_flag};\n" + r'''
void hldReportConvertedReads(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita converted reads frame=%d fast=%u fallback=%u\n", frame, convertedReadFast, convertedReadFallback);
    convertedReadFast = convertedReadFallback = 0;
}
'''
    code = replace_once(code, anchor, helpers + "\n" + anchor)
    anchor = "    bindRegionCase(ctx);"
    enabled = f"if ({launch_flag}) " if launch_flag else ""
    code = replace_once(code, anchor, anchor + f"\n    {enabled}packScalarReadConversions(ctx);")
    anchor = "            case OP_PACKED_VAR_READ: {"
    start = code.index(anchor)
    end = code.index("                if (OpWord_type2(opword) == 0xF) {", start)
    read = code[start:end].replace("OP_PACKED_VAR_READ", "OP_PACKED_CONVERTED_READ", 1)
    return replace_once(code, anchor, read.rstrip() + CONVERTED_READ + anchor)


def with_packed_read_conversions_exit(code):
    anchor = "            if (args.exitAtFrame >= 0 && runner->frameCount >= args.exitAtFrame) {"
    return replace_once(code, anchor, anchor + """
                extern void hldReportConvertedReadsAtExit(void);
                hldReportConvertedReadsAtExit();""")
