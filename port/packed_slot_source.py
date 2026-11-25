"""Cache exact hash-table slot numbers at packed instance read sites."""

from runtime_verification_source import replace_once


SLOT_OPERAND = r'''
// Store an eleven-bit slot hint alongside the original sixteen-bit
// normal variable reference. Other operand readers see the original ref.
#define HLD_SLOT_HINT_TAG 0xc0000000u
#define HLD_SLOT_HINT_MASK 0x07ffu
'''


SLOT_READS = r'''
enum { OP_PACKED_SLOT_READ = 0xcb, OP_PACKED_SLOT_CONVERTED_READ = 0xcc };

static void packInstanceSlotReads(VMContext* ctx) {
    const DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned sites = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        const CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t pc = 0; pc + 4 <= entry->length;) {
            const uint32_t start = pc;
            const OpWord op = {BinaryUtils_readUint32Aligned(bytes + pc)};
            pc += 4 + OpWord_operandSize(op);
            const uint8_t opcode = OpWord_opcode(op);
            if (opcode != OP_PACKED_VAR_READ && opcode != OP_PACKED_CONVERTED_READ) continue;
            const unsigned scope = (uint16_t)OpWord_extra(op) >> 14;
            // The switch fast path decodes ordinary global reads in place.
            if ((scope != PACKED_SELF && scope != PACKED_OTHER) || entry->length - start < 8) continue;
            const uint32_t reference = resolveVarOperand(bytes + start + 4);
            if ((reference & 0xffff0000u) != ((uint32_t)VARTYPE_NORMAL << 24)) continue;
            const uint32_t replacement = opcode == OP_PACKED_VAR_READ
                ? OP_PACKED_SLOT_READ : OP_PACKED_SLOT_CONVERTED_READ;
            BinaryUtils_writeUint32(bytes + start, replacement << 24 | (op.value & 0x00ffffffu));
            BinaryUtils_writeUint32(bytes + start + 4, HLD_SLOT_HINT_TAG | (reference & 0xffffu));
            ++sites;
        }
    }
    logInfo("HLD: bound %u cached instance read sites\n", sites);
}

static NOINLINE const RValue* refreshInstanceSlot(IntRValueHashMap* map, int32_t id, uint8_t* operand) {
    RValue* original = IntRValueHashMap_findSlot(map, id);
    if (original == nullptr) return nullptr;
    const size_t slot = ((uintptr_t)original - (uintptr_t)map->entries) / sizeof(*map->entries);
    if (slot <= HLD_SLOT_HINT_MASK) {
        const uint32_t reference = BinaryUtils_readUint32Aligned(operand) & 0xffffu;
        BinaryUtils_writeUint32(operand, HLD_SLOT_HINT_TAG | (uint32_t)slot << 16 | reference);
    }
    return original;
}

static inline RValue readInstanceSlot(Instance* instance, int32_t id, const uint8_t* operand) {
    IntRValueHashMap* map = &instance->selfVars;
    const uint32_t slot = BinaryUtils_readUint32Aligned(operand) >> 16 & HLD_SLOT_HINT_MASK;
    const bool hit = slot < map->capacity && map->entries[slot].key == id;
    const RValue* value = hit ? &map->entries[slot].value : refreshInstanceSlot(map, id, (uint8_t*)operand);
    HLD_COMPARE_SLOT
    if (value == nullptr) return RValue_makeUndefined();
    RValue result = *value;
    result.ownsReference = false;
    return result;
}
'''


def with_packed_slot_reads(code, *, checked=False, launch_flag=None):
    resolve = """static uint32_t resolveVarOperand(const uint8_t* extraData) {
    return BinaryUtils_readUint32Aligned(extraData);
}"""
    code = replace_once(code, resolve, SLOT_OPERAND + """
static uint32_t resolveVarOperand(const uint8_t* extraData) {
    const uint32_t word = BinaryUtils_readUint32Aligned(extraData);
    if ((word & 0xf8000000u) == HLD_SLOT_HINT_TAG)
        return ((uint32_t)VARTYPE_NORMAL << 24) | (word & 0xffffu);
    return word;
}""")
    helpers = SLOT_READS
    if checked:
        helpers = "static unsigned instanceSlotHits, instanceSlotMisses;\n" + helpers
        helpers = helpers.replace("    HLD_COMPARE_SLOT", """    require(value == IntRValueHashMap_findSlot(map, id));
    if (hit) ++instanceSlotHits;
    else ++instanceSlotMisses;""")
        helpers += r'''
void hldReportInstanceSlotsAtExit(void) {
    logInfo("HLD: cached instance reads hits=%u misses=%u\n", instanceSlotHits, instanceSlotMisses);
}
'''
    else:
        helpers = helpers.replace("    HLD_COMPARE_SLOT\n", "")
    anchor = "static const int packedScopeTag[]"
    code = replace_once(code, anchor, helpers + "\n" + anchor)
    for original, replacement in (("OP_PACKED_CONVERTED_READ", "OP_PACKED_SLOT_CONVERTED_READ"),
                                  ("OP_PACKED_VAR_READ", "OP_PACKED_SLOT_READ")):
        anchor = f"            case {original}: {{"
        start = code.index(anchor)
        end = code.index("\n            }\n", start) + len("\n            }\n")
        handler = code[start:end]
        value_start = handler.index("                RValue value;")
        value_end = handler.index("\n                }", handler.index("                    Instance* instance", value_start))
        body = handler[:value_start] + """                Instance* instance = packedScopeInstance(ctx, scope);
                RValue value = instance != nullptr
                    ? readInstanceSlot(instance, id, extraData)
                    : resolveVariableRead(ctx, packedScopeTag[scope], resolveVarOperand(extraData));""" + handler[value_end + len("\n                }"):]
        code = replace_once(code, handler, body.replace(original, replacement, 1) + handler)

    boilerplate = '    logInfo("VM: Initialized with %u functions mapped\\n"'
    condition = f"if ({launch_flag}) " if launch_flag else ""
    setup = f"    {condition}packInstanceSlotReads(ctx);"
    if launch_flag:
        setup = f"    extern bool {launch_flag};\n" + setup
    return replace_once(code, boilerplate,
                        "#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && "
                        "!defined(ENABLE_VM_OPCODE_PROFILER)\n" + setup + "\n#endif\n" + boilerplate)


def with_packed_slot_writes(code, *, checked=False):
    """Extend the existing load-time slot pass to scalar field writes."""
    anchor = "enum { OP_PACKED_SLOT_READ = 0xcb, OP_PACKED_SLOT_CONVERTED_READ = 0xcc };"
    code = replace_once(code, anchor, anchor + "\nenum { OP_PACKED_SLOT_WRITE = 0xcd };")
    anchor = "    unsigned sites = 0;\n"
    code = replace_once(code, anchor, "    unsigned sites = 0, writes = 0;\n")
    code = replace_once(code,
        "            if (opcode != OP_PACKED_VAR_READ && opcode != OP_PACKED_CONVERTED_READ) continue;",
        """            const bool write = opcode == OP_PACKED_VAR_WRITE;
            if (!write && opcode != OP_PACKED_VAR_READ && opcode != OP_PACKED_CONVERTED_READ) continue;""")
    code = replace_once(code,
        "            const uint32_t replacement = opcode == OP_PACKED_VAR_READ\n"
        "                ? OP_PACKED_SLOT_READ : OP_PACKED_SLOT_CONVERTED_READ;",
        """            uint32_t replacement = OP_PACKED_SLOT_CONVERTED_READ;
            if (write) replacement = OP_PACKED_SLOT_WRITE;
            else if (opcode == OP_PACKED_VAR_READ) replacement = OP_PACKED_SLOT_READ;""")
    code = replace_once(code, "            ++sites;\n", "            if (write) ++writes;\n            else ++sites;\n")
    anchor = '    logInfo("HLD: bound %u cached instance read sites\\n", sites);'
    code = replace_once(code, anchor, anchor + '\n    logInfo("HLD: bound %u cached instance write sites\\n", writes);')

    # Keep the hash map's eager resize even when the key already exists.
    # Enumeration order depends on the resulting table layout.
    helper = r'''
static NOINLINE RValue* refreshInstanceWriteSlot(IntRValueHashMap* map, int32_t id, uint8_t* operand) {
    RValue* value = IntRValueHashMap_getOrInsertUndefined(map, id);
    const size_t slot = ((uintptr_t)value - (uintptr_t)map->entries) / sizeof(*map->entries);
    if (slot <= HLD_SLOT_HINT_MASK) {
        const uint32_t reference = BinaryUtils_readUint32Aligned(operand) & 0xffffu;
        BinaryUtils_writeUint32(operand, HLD_SLOT_HINT_TAG | (uint32_t)slot << 16 | reference);
    }
    return value;
}

static inline void writeInstanceSlot(Instance* instance, int32_t id, const uint8_t* operand, RValue value) {
    IntRValueHashMap* map = &instance->selfVars;
    const uint32_t slot = BinaryUtils_readUint32Aligned(operand) >> 16 & HLD_SLOT_HINT_MASK;
    const bool hit = slot < map->capacity && map->entries[slot].key == id &&
        (map->count + 1) * 4 <= map->capacity * 3;
    RValue* target = hit ? &map->entries[slot].value : refreshInstanceWriteSlot(map, id, (uint8_t*)operand);
    HLD_COMPARE_WRITE_SLOT
    RValue_copyIntoSlot(target, value);
    if (value.type == RVALUE_INT32 && value.assetRefType == ASSET_TYPE_INSTANCE)
        instance->mayHaveInstanceReferences = true;
    RValue_free(&value);
}
'''
    if checked:
        helper = "static unsigned instanceWriteSlotHits, instanceWriteSlotMisses;\n" + helper
        helper = helper.replace("    HLD_COMPARE_WRITE_SLOT", """    require(target == IntRValueHashMap_findSlot(map, id));
    if (hit) ++instanceWriteSlotHits;
    else ++instanceWriteSlotMisses;""")
        helper += r'''
void hldReportInstanceWriteSlotsAtExit(void) {
    logInfo("HLD: cached instance writes hits=%u misses=%u\n", instanceWriteSlotHits, instanceWriteSlotMisses);
}
'''
    else:
        helper = helper.replace("    HLD_COMPARE_WRITE_SLOT\n", "")
    anchor = "static const int packedScopeTag[]"
    code = replace_once(code, anchor, helper + "\n" + anchor)
    anchor = "            case OP_PACKED_VAR_WRITE: {"
    return replace_once(code, anchor, r'''            case OP_PACKED_SLOT_WRITE: {
                const uint16_t operand = (uint16_t)OpWord_extra(opword);
                const unsigned scope = operand >> 14;
                const int32_t id = operand & PACKED_VAR_ID_MASK;
                RValue value = coerceIntStoreToReal(stackPop(ctx), OpWord_type2(opword));
                Instance* instance = packedScopeInstance(ctx, scope);
                if (instance != nullptr)
                    writeInstanceSlot(instance, id, extraData, value);
                else
                    resolveVariableWrite(ctx, packedScopeTag[scope], resolveVarOperand(extraData), value);
                break;
            }
''' + anchor)


def with_packed_slot_exit(code):
    anchor = "    Runner_free(runner);"
    return replace_once(code, anchor, "    extern void hldReportInstanceSlotsAtExit(void);\n"
                        "    hldReportInstanceSlotsAtExit();\n" + anchor)
