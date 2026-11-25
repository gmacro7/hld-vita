"""Run the original enemy path visibility checks within one native block."""

from runtime_verification_source import replace_once


HELPER = r'''
enum {
    OP_PACKED_ENEMY_PATH_PROBE = 0x92,
    OP_PACKED_ENEMY_PATH_CHECK = 0x93,
    ENEMY_PATH_CONTINUATION = 0x298,
};
static BuiltinFunc enemyPathCollisionLine;

#ifdef HLD_VERIFY_ENEMY_PATH_PROBE
static const CallFrame* predictedPathFrame;
static RValue predictedPathLocals[7];
static unsigned checkedPathProbes;
#endif

static void bindEnemyPathProbe(VMContext* ctx) {
    const CodeEntry* path = matchHldCode(ctx, "gml_Script_Enemy_PathFind", 1716, 0x830b327fu);
    if (path == nullptr || path->localsCount != 12) return;
    uint8_t* bytes = codeBytes(ctx, path);
    const BuiltinFunc collisionLine = VM_findBuiltin(ctx, "collision_line");
    const uint16_t offsets[] = {0x148, 0x1dc, 0x228, 0x274};
    if (collisionLine == nullptr) return;
    repeat(sizeof(offsets) / sizeof(*offsets), i) {
        const uint32_t function = resolveFuncOperand(bytes + offsets[i]);
        if (function >= ctx->funcCallCacheCount || ctx->funcCallCache[function].builtin != (void*)collisionLine ||
            ctx->funcCallCache[function].scriptCodeIndex >= 0) return;
    }
    enemyPathCollisionLine = collisionLine;
    BinaryUtils_writeUint32(bytes, (uint32_t)OP_PACKED_ENEMY_PATH_PROBE << 24);
#ifdef HLD_VERIFY_ENEMY_PATH_PROBE
    BinaryUtils_writeUint32(bytes + ENEMY_PATH_CONTINUATION, (uint32_t)OP_PACKED_ENEMY_PATH_CHECK << 24);
#endif
    logInfo("HLD: bound the direct enemy path visibility query\n");
}

static bool pathProbeNumber(RValue value) {
    return value.type == RVALUE_INT32 || value.type == RVALUE_BOOL ||
        (value.type == RVALUE_REAL && isfinite(value.real));
}

static bool tryEnemyPathProbe(VMContext* ctx) {
    CallFrame* frame = ctx->currentCallFrame;
    Instance* self = frame->self;
    if (self == nullptr || ctx->runner == nullptr || frame->localVarsCount < 8 ||
        frame->scriptArgsCount < 2 || frame->scriptArgsCount > 6 ||
        !isfinite(self->x) || !isfinite(self->y)) return false;
    for (int32_t i = 0; i < frame->scriptArgsCount; ++i) {
        if (!pathProbeNumber(frame->scriptArgs[i])) return false;
    }
    const RValue zero = RValue_makeReal(0);
    RValue fields[] = {frame->scriptArgs[0], frame->scriptArgs[1], zero, zero,
                       frame->scriptArgsCount > 3 ? frame->scriptArgs[3] : zero,
                       frame->scriptArgsCount > 4 ? frame->scriptArgs[4] : zero,
                       frame->scriptArgsCount > 5 ? frame->scriptArgs[5] : zero};
    fields[0].gmlStackType = fields[1].gmlStackType = GML_TYPE_VARIABLE;
    for (int32_t i = 3; i < frame->scriptArgsCount; ++i) fields[i + 1].gmlStackType = GML_TYPE_VARIABLE;

    const float x = self->x, y = self->y;
    RValue args[] = {RValue_makeReal(x), RValue_makeReal(y), fields[0], fields[1],
                     zero, RValue_makeInt32(1), RValue_makeInt32(0)};
    bool blocked = false;
    if (frame->scriptArgsCount > 2) {
        const GMLReal target = RValue_toReal(frame->scriptArgs[2]);
        // The alternate script branch handles its special visibility value.
        if (target < 0 || target > 16777215 || target != (int32_t)target) return false;
        args[4] = frame->scriptArgs[2];
        blocked = RValue_toBool(enemyPathCollisionLine(ctx, args, 7));
    } else {
        const int32_t targets[] = {401, 29, 15};
        for (unsigned i = 0; i < sizeof(targets) / sizeof(*targets) && !blocked; ++i) {
            args[4] = RValue_makeInt32(targets[i]);
            blocked = RValue_toBool(enemyPathCollisionLine(ctx, args, 7));
        }
    }
    fields[3] = RValue_makeReal(blocked);
#ifdef HLD_VERIFY_ENEMY_PATH_PROBE
    require(predictedPathFrame == nullptr);
    predictedPathFrame = frame;
    memcpy(predictedPathLocals, fields, sizeof(fields));
    return false;
#else
    repeat(sizeof(fields) / sizeof(*fields), i) {
        RValue_writeIntoSlotStealingOwnershipOrCopying(&frame->localVars[i + 1], fields[i]);
    }
    return true;
#endif
}

#ifdef HLD_VERIFY_ENEMY_PATH_PROBE
static void verifyEnemyPathProbe(const CallFrame* frame) {
    if (predictedPathFrame != frame) return;
    predictedPathFrame = nullptr;
    repeat(sizeof(predictedPathLocals) / sizeof(*predictedPathLocals), i) {
        const RValue chosen = predictedPathLocals[i];
        const RValue original = frame->localVars[i + 1];
        require(original.type == chosen.type && original.gmlStackType == chosen.gmlStackType &&
                original.assetRefType == chosen.assetRefType &&
                RValue_toReal(original) == RValue_toReal(chosen));
    }
    if (++checkedPathProbes == 1000) logInfo("HLD: checked 1,000 enemy path probes\n");
}
#endif

'''


def with_enemy_path_probe(code, *, verify=False, launch_flag=None):
    declaration = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    helper = "#define HLD_VERIFY_ENEMY_PATH_PROBE\n" + HELPER if verify else HELPER
    if launch_flag:
        helper = f'''extern bool {launch_flag};
static unsigned enemyPathProbeCalls;

void hldReportEnemyPathProbe(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita enemy path probes frame=%d enabled=%d calls=%u\\n",
            frame, (int){launch_flag}, enemyPathProbeCalls);
    enemyPathProbeCalls = 0;
}}
''' + helper
        anchor = "static void bindEnemyPathProbe(VMContext* ctx) {"
        helper = replace_once(helper, anchor, anchor + f"\n    if (!{launch_flag}) return;")
        anchor = "        RValue_writeIntoSlotStealingOwnershipOrCopying(&frame->localVars[i + 1], fields[i]);\n    }"
        helper = replace_once(helper, anchor, anchor + "\n    ++enemyPathProbeCalls;")
    code = replace_once(code, declaration, helper + declaration)
    anchor = "    packHldMathInstructions(ctx);"
    code = replace_once(code, anchor, anchor + "\n    bindEnemyPathProbe(ctx);")
    anchor = "            case OP_PACKED_ARGUMENT:"
    return replace_once(code, anchor, r'''            case OP_PACKED_ENEMY_PATH_PROBE: {
                VM_SYNC_IP();
                if (tryEnemyPathProbe(ctx)) {
                    ip = ENEMY_PATH_CONTINUATION;
                    break;
                }
                CallFrame* frame = ctx->currentCallFrame;
                RValue value = packedScriptArgument(frame, 0);
                value.gmlStackType = GML_TYPE_VARIABLE;
                RValue_writeIntoSlotStealingOwnershipOrCopying(&frame->localVars[1], value);
                ip += 20;
                break;
            }
            case OP_PACKED_ENEMY_PATH_CHECK: {
                CallFrame* frame = ctx->currentCallFrame;
#ifdef HLD_VERIFY_ENEMY_PATH_PROBE
                verifyEnemyPathProbe(frame);
#endif
                RValue value = frame->localVars[4];
                value.ownsReference = false;
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                ip += 4;
                break;
            }
''' + anchor)
