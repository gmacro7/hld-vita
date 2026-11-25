"""Run the common ready, untouched mine update directly."""

from runtime_verification_source import replace_once


DORMANT = r'''
enum { OP_PACKED_DORMANT_MINE = 0x91 };
static int32_t phaseMineTimer, phaseMineCooldown, phaseMineState, phaseMineOff;

#ifdef HLD_VERIFY_DORMANT_MINE
static const CallFrame* predictedMineFrame;
static GMLReal predictedMineTimer;
static unsigned checkedDormantMines;
#endif

static void bindDormantMine(VMContext* ctx, uint8_t* bytes) {
    if (matchHldCode(ctx, "gml_Script_approach", 108, 0x06734704u) == nullptr) return;
    const uint16_t offsets[] = {0x18, 0xa0, 0x3c, 0x48};
    const char* names[] = {"t", "cooldown", "state", "STATE_OFF"};
    int32_t ids[4];
    repeat(4, i) {
        const Variable* variable = resolveVarDef(ctx, resolveVarOperand(bytes + offsets[i]));
        if (variable->varID < 0 || strcmp(variable->name, names[i]) != 0) return;
        ids[i] = variable->varID;
        repeat(i, j) if (ids[i] == ids[j]) return;
    }
    phaseMineTimer = ids[0];
    phaseMineCooldown = ids[1];
    phaseMineState = ids[2];
    phaseMineOff = ids[3];
    BinaryUtils_writeUint32(bytes + 0x14, (uint32_t)OP_PACKED_DORMANT_MINE << 24);
    logInfo("HLD: bound the direct dormant mine update\n");
}

static bool tryDormantMine(VMContext* ctx) {
    Instance* self = ctx->currentCallFrame->self;
    if (self == nullptr || ctx->runner == nullptr || self->imageIndex != 0) return false;
    const RValue timer = Instance_getSelfVar(self, phaseMineTimer);
    const RValue cooldown = Instance_getSelfVar(self, phaseMineCooldown);
    if (timer.type != RVALUE_REAL || !(GMLReal_fabs(timer.real) < 16777216) ||
        cooldown.type != RVALUE_REAL || cooldown.real != 0 || signbit(cooldown.real)) return false;
    const RValue state = Instance_getSelfVar(self, phaseMineState);
    const RValue inactive = Instance_getSelfVar(self, phaseMineOff);
    if ((state.type != RVALUE_REAL && state.type != RVALUE_INT32) ||
        (inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32)) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0 && epsilon < 0.5) ||
        !(GMLReal_fabs(RValue_toReal(state) - RValue_toReal(inactive)) <= epsilon)) return false;

    bool triggered;
    if (!hldPhaseMineTrigger(ctx, &triggered) || triggered) return false;
    const GMLReal next = timer.real + 1;
#ifdef HLD_VERIFY_DORMANT_MINE
    require(predictedMineFrame == nullptr);
    predictedMineFrame = ctx->currentCallFrame;
    predictedMineTimer = next;
    return false;
#else
    RValue age = RValue_makeReal(next), ready = RValue_makeReal(0);
    age.gmlStackType = ready.gmlStackType = GML_TYPE_VARIABLE;
    Instance_setSelfVar(self, phaseMineTimer, age);
    Instance_setSelfVar(self, phaseMineCooldown, ready);
    self->imageIndex = 0;
    return true;
#endif
}

#ifdef HLD_VERIFY_DORMANT_MINE
static void checkDormantMine(const CallFrame* frame, RValue result, VMContext* ctx) {
    if (predictedMineFrame != frame) return;
    predictedMineFrame = nullptr;
    const RValue timer = Instance_getSelfVar(frame->self, phaseMineTimer);
    const RValue cooldown = Instance_getSelfVar(frame->self, phaseMineCooldown);
    const RValue expected = scriptFallthroughReturnValue(ctx);
    require(timer.type == RVALUE_REAL && timer.gmlStackType == GML_TYPE_VARIABLE &&
            memcmp(&timer.real, &predictedMineTimer, sizeof(timer.real)) == 0);
    require(cooldown.type == RVALUE_REAL && cooldown.gmlStackType == GML_TYPE_VARIABLE &&
            cooldown.real == 0 && !signbit(cooldown.real));
    require(frame->self->imageIndex == 0 && !signbit(frame->self->imageIndex));
    require(result.type == expected.type && result.gmlStackType == expected.gmlStackType &&
            !result.ownsReference && RValue_toReal(result) == RValue_toReal(expected));
    if (++checkedDormantMines == 1000) logInfo("HLD: checked 1,000 dormant mine updates\n");
}
#endif

'''


def with_dormant_mines(code, *, verify=False, launch_flag=None):
    declaration = "bool hldPhaseMineTrigger(VMContext* ctx, bool* triggered);"
    mode = "#define HLD_VERIFY_DORMANT_MINE\n" if verify else ""
    helper = mode + DORMANT
    if launch_flag:
        helper = f'''extern bool {launch_flag};
static unsigned dormantMineUpdates;

void hldReportDormantMines(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita dormant mines frame=%d enabled=%d updates=%u\\n",
            frame, (int){launch_flag}, dormantMineUpdates);
    dormantMineUpdates = 0;
}}
''' + helper
        anchor = "static void bindDormantMine(VMContext* ctx, uint8_t* bytes) {"
        helper = replace_once(helper, anchor, anchor + f"\n    if (!{launch_flag}) return;")
        anchor = "    self->imageIndex = 0;"
        helper = replace_once(helper, anchor, anchor + "\n    ++dormantMineUpdates;")
    code = replace_once(code, declaration, declaration + "\n\n" + helper)
    anchor = "    BinaryUtils_writeUint32(bytes + 0xd8,"
    code = replace_once(code, anchor, "    bindDormantMine(ctx, bytes);\n" + anchor)
    anchor = "            case OP_PACKED_PHASE_MINE_TRIGGER:"
    code = replace_once(code, anchor, r'''            case OP_PACKED_DORMANT_MINE: {
                VM_SYNC_IP();
                if (tryDormantMine(ctx)) return scriptFallthroughReturnValue(ctx);
                const uint32_t ref = resolveVarOperand(bytecodeBase + ip);
                stackPushTyped(ctx, resolveVariableRead(ctx, INSTANCE_SELF, ref), GML_TYPE_VARIABLE);
                ip += 4;
                break;
            }
''' + anchor)
    anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
    return replace_once(code, anchor, anchor + r'''
#ifdef HLD_VERIFY_DORMANT_MINE
    checkDormantMine(&callFrame, result, ctx);
#endif
''')
