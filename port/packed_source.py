"""Prepare the format-15 literal bytecode experiment."""
from pathlib import Path


def with_packed_literals(code, math=False, variables=False, branches=False, properties=False, arguments=False,
                         argument_marker=True):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    prep = Path(__file__).with_name("packed-literals.inc").read_text()
    anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
    replace(anchor, prep + "\n" + anchor)
    anchor = "        switch (opcode) {\n            // Push instructions\n"
    replace(anchor, """        switch (opcode) {
            case OP_PACKED_LITERAL_VALUE:
                stackPushTyped(ctx, RValue_makeInt32(OpWord_extra(opword)), GML_TYPE_VARIABLE);
                ip += 4;
                break;
            case OP_PACKED_LITERAL_STORE: {
                OpWord pop = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                uint32_t ref = resolveVarOperand(bytecodeBase + ip + 4);
                ip += 8;
                resolveVariableWrite(ctx, OpWord_extra(pop), ref, RValue_makeReal(OpWord_extra(opword)));
                break;
            }
            case OP_PACKED_LITERAL_BRANCH: {
                const RValue* value = &slots[ctx->stack.top - 1];
                if (value->type != RVALUE_INT32 && value->type != RVALUE_REAL) {
                    handlePushImmediate(ctx, opword);
                    break;
                }
                GMLReal left = value->type == RVALUE_REAL ? value->real : (GMLReal)value->int32;
                GMLReal diff = left - OpWord_extra(opword);
                GMLReal epsilon = ctx->runner != nullptr ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
                int cmp = GMLReal_fabs(diff) <= epsilon ? 0 : diff < 0 ? -1 : 1;
                OpWord cmpOp = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                bool condition;
                switch (OpWord_comparisonFunction(cmpOp)) {
                    case CMPOP_LESS_THAN: condition = cmp < 0; break;
                    case CMPOP_LESS_THAN_OR_EQUAL: condition = cmp <= 0; break;
                    case CMPOP_EQUAL: condition = cmp == 0; break;
                    case CMPOP_NOT_EQUAL: condition = cmp != 0; break;
                    case CMPOP_GREATER_THAN_OR_EQUAL: condition = cmp >= 0; break;
                    case CMPOP_GREATER_THAN: condition = cmp > 0; break;
                    default: abort();
                }
                ip += 4;
                consumeConditionalBranch(bytecodeBase, codeLength, &ip, condition);
                --ctx->stack.top;
                break;
            }
            // Push instructions
""")
    anchor = '    logInfo("VM: Initialized with %u functions mapped\\n", (uint32_t) shlen(ctx->codeIndexByName));'
    replace(anchor, """#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    packHldLiteralInstructions(ctx);
#endif
""" + anchor)
    if math:
        prep = Path(__file__).with_name("packed-math.inc").read_text()
        anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
        replace(anchor, prep + "\n" + anchor)
        anchor = "            case OP_PACKED_LITERAL_VALUE:\n"
        replace(anchor, """            case OP_PACKED_CALL_DISCARD:
                VM_SYNC_IP();
                handleCall(ctx, opword, extraData, true);
                ip += 4;
                break;
            case OP_PACKED_MATH:
                if (!executePackedMath(ctx, opword)) {
                    VM_SYNC_IP();
                    handleCall(ctx, opword, extraData, false);
                }
                break;
""" + anchor)
        replace("static void handleCall(VMContext* ctx, OpWord opword, const uint8_t* extraData) {",
                "static void handleCall(VMContext* ctx, OpWord opword, const uint8_t* extraData, bool discard) {")
        replace("            case OP_CALL:\n                VM_SYNC_IP();\n                handleCall(ctx, opword, extraData);",
                "            case OP_CALL:\n                VM_SYNC_IP();\n                handleCall(ctx, opword, extraData, false);")
        lo = code.index("static void handleCall(")
        hi = code.index("static void handleCallV(", lo)
        handler = code[lo:hi]
        rewrite = handler.replace("            stackPushTyped(ctx, RValue_makeUndefined(), GML_TYPE_VARIABLE);",
                                  "            if (!discard) stackPushTyped(ctx, RValue_makeUndefined(), GML_TYPE_VARIABLE);")
        rewrite = rewrite.replace("        stackPushTyped(ctx, result, GML_TYPE_VARIABLE);",
                                  "        if (discard) RValue_free(&result);\n"
                                  "        else stackPushTyped(ctx, result, GML_TYPE_VARIABLE);")
        rewrite = rewrite.replace("    stackPush(ctx, RValue_makeUndefined());",
                                  "    if (!discard) stackPush(ctx, RValue_makeUndefined());")
        assert rewrite.count("if (discard) RValue_free(&result);") == 2 and rewrite.count("if (!discard)") == 2
        code = code[:lo] + rewrite + code[hi:]
        replace("    packHldLiteralInstructions(ctx);", "    packHldLiteralInstructions(ctx);\n    packHldMathInstructions(ctx);")
    if variables:
        prep = Path(__file__).with_name("packed-vars.inc").read_text()
        anchor = "static bool hldSceneryCull(VMContext* ctx, const uint8_t* bytes) {"
        replace(anchor, prep + "\n" + anchor)
        anchor = "            case OP_PACKED_LITERAL_VALUE:\n"
        replace(anchor, """            case OP_PACKED_VAR_READ: {
                const uint16_t operand = (uint16_t)OpWord_extra(opword);
                const unsigned scope = operand >> 14;
                const int32_t id = operand & PACKED_VAR_ID_MASK;
                RValue value;
                if (scope == PACKED_LOCAL) {
                    uint32_t local = resolveLocalSlot(ctx, id);
                    value = ctx->currentCallFrame->localVars[local];
                    value.ownsReference = false;
                } else {
                    Instance* instance = packedScopeInstance(ctx, scope);
                    value = instance != nullptr
                        ? Instance_getSelfVar(instance, id)
                        : resolveVariableRead(ctx, packedScopeTag[scope], resolveVarOperand(extraData));
                }
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                break;
            }
            case OP_PACKED_VAR_WRITE: {
                const uint16_t operand = (uint16_t)OpWord_extra(opword);
                const unsigned scope = operand >> 14;
                const int32_t id = operand & PACKED_VAR_ID_MASK;
                RValue value = coerceIntStoreToReal(stackPop(ctx), OpWord_type2(opword));
                if (scope == PACKED_LOCAL) {
                    uint32_t local = resolveLocalSlot(ctx, id);
                    RValue_writeIntoSlotStealingOwnershipOrCopying(&ctx->currentCallFrame->localVars[local], value);
                } else {
                    Instance* instance = packedScopeInstance(ctx, scope);
                    if (instance != nullptr) {
                        Instance_setSelfVar(instance, id, value);
                        RValue_free(&value);
                    } else {
                        resolveVariableWrite(ctx, packedScopeTag[scope], resolveVarOperand(extraData), value);
                    }
                }
                break;
            }
""" + anchor)
        replace("    packHldLiteralInstructions(ctx);", "    packHldLiteralInstructions(ctx);\n    packHldVariableInstructions(ctx);")
    if branches:
        assert variables
        # The original encodings are still present at every branch target.
        replace("            const uint32_t head =", """            uint32_t types = word.value & 0x00ff0000u;
            if (read && entry->length - addr >= 20 &&
                OpWord_opcode((OpWord){BinaryUtils_readUint32Aligned(bytes + addr + 8)}) ==
                    OP_PACKED_LITERAL_BRANCH) {
                types |= 0x00f00000u;
            }
            const uint32_t head =""")
        replace("head | (word.value & 0x00ff0000u)", "head | types")
        old_branch = code[code.index("                GMLReal left ="):code.index("                --ctx->stack.top;")]
        assert "consumeConditionalBranch" in old_branch
        equivalent = old_branch.replace("bytecodeBase + ip", "bytecodeBase + *ip")
        equivalent = equivalent.replace("                ip += 4;\n"
                                        "                consumeConditionalBranch(bytecodeBase, codeLength, &ip, condition);\n",
                                        "                *ip += 4;\n"
                                        "                return consumeConditionalBranch(bytecodeBase, codeLength, ip, condition);\n")
        branch_helper = """static inline bool branchOnPackedLiteral(VMContext* ctx, const RValue* value,
                                         OpWord opword, const uint8_t* bytecodeBase,
                                         uint32_t codeLength, uint32_t* ip) {
    if (value->type != RVALUE_INT32 && value->type != RVALUE_REAL) return false;
""" + "".join("    " + line.removeprefix(" " * 16) + "\n" for line in equivalent.splitlines()) + "}\n\n"
        replace("static bool hldSceneryCull(", branch_helper + "static bool hldSceneryCull(")
        start = "                if (value->type != RVALUE_INT32 && value->type != RVALUE_REAL) {\n"
        replace(start + """                    handlePushImmediate(ctx, opword);
                    break;
                }
""" + old_branch + "                --ctx->stack.top;",
                """                uint32_t next = ip;
                if (branchOnPackedLiteral(ctx, value, opword, bytecodeBase, codeLength, &next)) {
                    ip = next;
                    --ctx->stack.top;
                } else {
                    handlePushImmediate(ctx, opword);
                }""")
        replace("                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);\n"
                "                break;\n            }\n            case OP_PACKED_VAR_WRITE:",
                """                if (OpWord_type2(opword) == 0xF) {
                    OpWord literal = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                    uint32_t next = ip + 4;
                    if (branchOnPackedLiteral(ctx, &value, literal, bytecodeBase, codeLength, &next)) {
                        RValue_free(&value);
                        ip = next;
                        break;
                    }
                }
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                break;
            }
            case OP_PACKED_VAR_WRITE:""")
        replace("        // extraData pointer (may not be used depending on opcode)\n"
                "        const uint8_t* extraData = bytecodeBase + ip;\n"
                "        ip += OpWord_operandSize(opword);",
                """        const uint8_t* extraData = bytecodeBase + ip;
        // Two bits per first-operand type store its width in 32-bit words.
        enum {
            OPERAND_WIDTH_WORDS =
                (2u << (2 * GML_TYPE_DOUBLE)) | (1u << (2 * GML_TYPE_FLOAT)) |
                (1u << (2 * GML_TYPE_INT32)) | (2u << (2 * GML_TYPE_INT64)) |
                (1u << (2 * GML_TYPE_BOOL)) | (1u << (2 * GML_TYPE_VARIABLE)) |
                (1u << (2 * GML_TYPE_STRING)),
        };
        if (OpWord_hasOperand(opword))
            ip += ((OPERAND_WIDTH_WORDS >> (2 * OpWord_type1(opword))) & 3u) * sizeof(uint32_t);""")
    if properties:
        prep = Path(__file__).with_name("packed-properties.inc").read_text()
        anchor = "static bool hldSceneryCull("
        replace(anchor, prep + "\n" + anchor)
        replace("    packHldVariableInstructions(ctx);",
                "    packHldVariableInstructions(ctx);\n    packHldInstanceProperties(ctx);")
        anchor = "            case OP_PACKED_VAR_READ: {"
        replace(anchor, """            case OP_PACKED_PROPERTY_READ: {
                Instance* instance = (Instance*)ctx->currentCallFrame->self;
                RValue value;
                if (instance == nullptr || instance->objectIndex == STRUCT_OBJECT_INDEX) {
                    value = resolveVariableRead(ctx, INSTANCE_SELF, resolveVarOperand(extraData));
                } else {
                    const uint8_t* field = (const uint8_t*)instance + (uint16_t)OpWord_extra(opword);
                    switch (OpWord_type2(opword)) {
                        case FIELD_FLOAT: value = RValue_makeReal(*(const float*)field); break;
                        case FIELD_REAL: value = RValue_makeReal(*(const GMLReal*)field); break;
                        case FIELD_INT: value = RValue_makeReal((GMLReal)*(const int32_t*)field); break;
                        case FIELD_UINT: value = RValue_makeReal((GMLReal)*(const uint32_t*)field); break;
                        case FIELD_BOOL: value = RValue_makeBool(*(const bool*)field); break;
                        default: abort();
                    }
                }
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                break;
            }
""" + anchor)
    if arguments:
        assert variables
        prep = Path(__file__).with_name("packed-arguments.inc").read_text()
        if not argument_marker:
            start = prep.index("#ifdef PLATFORM_VITA")
            end = prep.index("#endif\n", start) + len("#endif\n")
            assert "prepared-script-args" in prep[start:end]
            prep = prep[:start] + prep[end:]
        anchor = "static bool hldSceneryCull("
        replace(anchor, prep + "\n" + anchor)
        replace("    packHldVariableInstructions(ctx);",
                "    packHldVariableInstructions(ctx);\n    packHldArguments(ctx);")
        anchor = "            case OP_PACKED_VAR_READ: {"
        replace(anchor, """            case OP_PACKED_ARGUMENT:
            case OP_PACKED_ARGUMENT_ARRAY: {
                if (opcode == OP_PACKED_ARGUMENT_ARRAY) ip += 12;
                CallFrame* frame = ctx->currentCallFrame;
                RValue value = packedScriptArgument(frame, (uint16_t)OpWord_extra(opword));
                if (OpWord_type2(opword) == PACKED_ARGUMENT_TO_LOCAL) {
                    OpWord store = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                    uint32_t local = resolveLocalSlot(ctx, (uint16_t)OpWord_extra(store) & PACKED_VAR_ID_MASK);
                    value.gmlStackType = GML_TYPE_VARIABLE;
                    RValue_writeIntoSlotStealingOwnershipOrCopying(&frame->localVars[local], value);
                    ip += 8;
                } else {
                    stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                }
                break;
            }
""" + anchor)
    return code
