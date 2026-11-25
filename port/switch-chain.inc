enum { OP_PACKED_SWITCH = 0x8b, SWITCH_DUP_WORD = 0x86050000u };

// All branches and operand words remain in place. Only the first DUP of
// each independent chain changes, so other control-flow entries still work.
static uint32_t switchCaseWidth(const uint8_t* bytes, uint32_t available) {
    if (available < 16 || BinaryUtils_readUint32Aligned(bytes) != SWITCH_DUP_WORD) return 0;
    const OpWord operand = {BinaryUtils_readUint32Aligned(bytes + 4)};
    const uint8_t opcode = OpWord_opcode(operand);
    const bool global = opcode == OP_PACKED_VAR_READ &&
        ((uint16_t)OpWord_extra(operand) >> 14) == PACKED_GLOBAL && OpWord_type2(operand) != 0xf;
    // The original int16 push may already have the literal comparison fused.
    const bool literal = (opcode == OP_PUSH_IMMEDIATE || opcode == OP_PACKED_LITERAL_BRANCH) &&
        OpWord_type1(operand) == GML_TYPE_INT16;
    if (!global && !literal) return 0;
    const uint32_t width = global ? 20 : 16;
    if (available < width) return 0;
    const OpWord comparison = {BinaryUtils_readUint32Aligned(bytes + width - 8)};
    const OpWord branch = {BinaryUtils_readUint32Aligned(bytes + width - 4)};
    if (comparison.value != (global ? 0x15550300u : 0x15520300u) || OpWord_opcode(branch) != OP_BT)
        return 0;
    return width;
}

static void packSwitchChains(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned chains = 0, cases = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        const CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t ip = 0; ip + 4 <= entry->length;) {
            const OpWord word = {BinaryUtils_readUint32Aligned(bytes + ip)};
            uint32_t end = ip;
            unsigned count = 0;
            if (word.value == SWITCH_DUP_WORD) {
                while (count < UINT16_MAX) {
                    const uint32_t width = switchCaseWidth(bytes + end, entry->length - end);
                    if (width == 0) break;
                    end += width;
                    ++count;
                }
            }
            if (count >= 2) {
                BinaryUtils_writeUint32(bytes + ip, ((uint32_t)OP_PACKED_SWITCH << 24) | count);
                ++chains;
                cases += count;
                ip = end;
            } else {
                ip += 4 + OpWord_operandSize(word);
            }
        }
    }
    logInfo("HLD: combined %u switch chains with %u comparisons at load time\n", chains, cases);
}

#ifdef HLD_COMPARE_SWITCH_CHAINS
static unsigned fusedSwitchChains;
static unsigned fusedSwitchCases;

static inline bool useSwitchChains(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportSwitchChains(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita switch chains frame=%d enabled=%d chains=%u cases=%u\n",
            frame, (int)useSwitchChains(frame - 1), fusedSwitchChains, fusedSwitchCases);
    fusedSwitchChains = fusedSwitchCases = 0;
}
#endif

static inline bool trySwitchChain(VMContext* ctx, const uint8_t* bytes, unsigned count, uint32_t* ip) {
#ifdef HLD_COMPARE_SWITCH_CHAINS
    if (ctx->runner == nullptr || !useSwitchChains(ctx->runner->frameCount)) return false;
#endif
    require(ctx->stack.top > 0);
    const RValue value = ctx->stack.slots[ctx->stack.top - 1];
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    const GMLReal lhs = RValue_toReal(value);
    const GMLReal epsilon = ctx->runner != nullptr ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
    uint32_t cursor = *ip;
    unsigned compared = 0;
    for (; compared < count;) {
        const OpWord operand = {BinaryUtils_readUint32Aligned(bytes + cursor + 4)};
        GMLReal rhs;
        uint32_t branch = cursor + 12;
        if (OpWord_opcode(operand) == OP_PACKED_VAR_READ) {
            if (ctx->globalScopeInstance == nullptr) return false;
            const RValue global = Instance_getSelfVar(ctx->globalScopeInstance,
                                                      (uint16_t)OpWord_extra(operand) & PACKED_VAR_ID_MASK);
            if (global.type != RVALUE_REAL && global.type != RVALUE_INT32) return false;
            rhs = RValue_toReal(global);
            branch += 4;
        } else {
            rhs = OpWord_extra(operand);
        }
        ++compared;
        if (GMLReal_fabs(lhs - rhs) <= epsilon) {
            cursor = branch + OpWord_branchOffset((OpWord){BinaryUtils_readUint32Aligned(bytes + branch)});
            break;
        }
        cursor = branch + 4;
    }
    *ip = cursor;
#ifdef HLD_COMPARE_SWITCH_CHAINS
    ++fusedSwitchChains;
    fusedSwitchCases += compared;
#endif
    return true;
}
