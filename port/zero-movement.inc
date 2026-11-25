typedef enum {
    ZERO_REMAINDER_X, ZERO_REMAINDER_Y, ZERO_IS_CHAR, ZERO_IS_ENEMY,
    ZERO_SLOPES, ZERO_NO_CLIP, ZERO_CLIFF, ZERO_OVER, ZERO_UNDER, ZERO_REPEATS,
    ZERO_MOVEMENT_FIELD_COUNT
} ZeroMovementField;

typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t fields[ZERO_MOVEMENT_FIELD_COUNT];
    int32_t flying, cliffBlock, overCrissCross, editorNoClip;
#ifdef HLD_MANTLE_SWEEP
    int32_t z;
#endif
} ZeroMovement;

typedef struct {
    RValue fields[ZERO_MOVEMENT_FIELD_COUNT];
    float x, y;
#ifdef HLD_MANTLE_SWEEP
    float penultimateX, penultimateY;
    GMLReal horizontalSteps, verticalSteps;
#endif
} ZeroMovementResult;

static ZeroMovement zeroMovement;

#if defined(HLD_MANTLE_SWEEP) && defined(HLD_VERIFY_ZERO_MOVEMENT)
enum { SWEEP_CALL, SWEEP_MOVING, SWEEP_MOVER, SWEEP_CLIFF, SWEEP_COORDS, SWEEP_CONTACT, SWEEP_CLEAR };
static unsigned mantleSweepChecks[SWEEP_CLEAR + 1];
#define COUNT_SWEEP(kind) (++mantleSweepChecks[kind])

static void reportMantleSweepChecks(void) {
    logInfo("HLD: movement sweep calls=%u moving=%u mover=%u cliff=%u coordinates=%u contact=%u clear=%u\n",
            mantleSweepChecks[SWEEP_CALL], mantleSweepChecks[SWEEP_MOVING], mantleSweepChecks[SWEEP_MOVER],
            mantleSweepChecks[SWEEP_CLIFF], mantleSweepChecks[SWEEP_COORDS],
            mantleSweepChecks[SWEEP_CONTACT], mantleSweepChecks[SWEEP_CLEAR]);
}
#else
#define COUNT_SWEEP(kind) ((void)0)
#endif

#if defined(HLD_COMPARE_ZERO_MOVEMENT) || defined(HLD_VERIFY_ZERO_MOVEMENT)
static unsigned zeroMovementCalls;
#endif

#ifdef HLD_COMPARE_ZERO_MOVEMENT
static bool zeroMovementEnabled;

void hldReportZeroMovement(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
#ifdef HLD_MANTLE_SWEEP
    logInfo("Vita movement sweep frame=%d enabled=%d calls=%u\n",
            frame, (int)zeroMovementEnabled, zeroMovementCalls);
#else
    logInfo("Vita zero movement frame=%d enabled=%d calls=%u\n",
            frame, (int)zeroMovementEnabled, zeroMovementCalls);
#endif
    zeroMovementCalls = 0;
}
#endif

static int32_t zeroMovementVariable(VMContext* ctx, const uint8_t* bytes, uint16_t offset, const char* name) {
    const Variable* variable = resolveVarDef(ctx, resolveVarOperand(bytes + offset));
    return strcmp(variable->name, name) == 0 ? variable->varID : -1;
}

static void bindZeroMovement(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_MantleMovementReturn", 4076, 0x67e0dc68u);
    if (code == nullptr || code->localsCount != 10) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint16_t offsets[] = {0x04, 0x20, 0xc4, 0xe4, 0xf0, 0x134, 0x174, 0x1b4, 0x1c0, 0x228};
    const char* names[] = {"xc", "yc", "_isChar", "_isEnemy", "_canDoSlopes", "_noClip",
                           "_cliffBlock", "_overBlock", "_underBlock", "_repeats"};
    ZeroMovement bound = {.context = ctx, .code = code};
    repeat(ZERO_MOVEMENT_FIELD_COUNT, i) {
        const int32_t id = zeroMovementVariable(ctx, bytes, offsets[i], names[i]);
        if (id < 0) return;
        repeat(i, earlier) if (bound.fields[earlier] == id) return;
        bound.fields[i] = id;
    }
    bound.flying = zeroMovementVariable(ctx, bytes, 0x18c, "flying");
    bound.cliffBlock = zeroMovementVariable(ctx, bytes, 0x1a0, "cliffBlock");
    bound.overCrissCross = zeroMovementVariable(ctx, bytes, 0x1f0, "overCrissCross");
    bound.editorNoClip = zeroMovementVariable(ctx, bytes, 0x14c, "editorNoClip");
    if (bound.flying < 0 || bound.cliffBlock < 0 || bound.overCrissCross < 0 || bound.editorNoClip < 0) return;
#ifdef HLD_MANTLE_SWEEP
    bound.z = zeroMovementVariable(ctx, bytes, 0x268, "z");
    if (bound.z < 0) return;
#endif
    repeat(ZERO_MOVEMENT_FIELD_COUNT, i) {
        if (bound.fields[i] == bound.flying || bound.fields[i] == bound.cliffBlock ||
            bound.fields[i] == bound.overCrissCross) return;
    }
    zeroMovement = bound;
#ifdef HLD_COMPARE_ZERO_MOVEMENT
#ifdef HLD_MANTLE_SWEEP
    FILE* flag = fopen("ux0:data/hld-vm/mantle-sweep", "r");
#else
    FILE* flag = fopen("ux0:data/hld-vm/native-zero-movement", "r");
#endif
    zeroMovementEnabled = flag != nullptr;
    if (flag != nullptr) fclose(flag);
#ifdef HLD_MANTLE_SWEEP
    logInfo("Vita movement sweep enabled=%d\n", (int)zeroMovementEnabled);
#endif
#endif
}

static bool zeroMovementNumber(RValue value) {
    return value.type == RVALUE_INT32 || (value.type == RVALUE_REAL && isfinite(value.real));
}

static inline RValue zeroMovementVariant(RValue value) {
    value.gmlStackType = GML_TYPE_VARIABLE;
    return value;
}

#ifdef HLD_MANTLE_SWEEP
bool hldMantleSweepClear(Runner* runner, Instance* inst, float endX, float endY, bool needsSupport);

static bool predictMantleSweep(VMContext* ctx, Instance* inst, const RValue* args,
                               GMLReal h, GMLReal v, ZeroMovementResult* result) {
    // The normal non-player axis loops are pure between terrain misses.
    // Keep the player and the base enemy's locked platform rules on
    // their original paths.
    if (inst->objectIndex == 419 || inst->objectIndex == 17 ||
        !inst->active || inst->destroyed || GMLReal_fabs(h) > 32 || GMLReal_fabs(v) > 32) {
        COUNT_SWEEP(SWEEP_MOVER);
        return false;
    }
    bool needsSupport = false;
    if (RValue_toBool(result->fields[ZERO_CLIFF])) {
        const RValue z = Instance_getSelfVar(inst, zeroMovement.z);
        if (!zeroMovementNumber(z) && z.type != RVALUE_BOOL) return false;
        needsSupport = GMLReal_fabs(RValue_toReal(z)) <= ctx->runner->mathEpsilon;
        if (needsSupport) COUNT_SWEEP(SWEEP_CLIFF);
    }
    const float x = inst->x, y = inst->y;
    if (!(fabsf(x) < 65536 && fabsf(y) < 65536) ||
        x != (int32_t)x || y != (int32_t)y) {
        COUNT_SWEEP(SWEEP_COORDS);
        return false;
    }
    const GMLReal dx = RValue_toReal(args[0]), dy = RValue_toReal(args[1]);
    const int sx = (dx > 0) - (dx < 0), sy = (dy > 0) - (dy < 0);
    if ((h != 0 && sx == 0) || (v != 0 && sy == 0)) return false;
    // The archived script uses the sign of the argument, even when an
    // accumulated remainder rounds with the opposite sign.
    const float endX = h == 0 ? x : x + sx * GMLReal_fabs(h);
    const float endY = v == 0 ? y : y + sy * GMLReal_fabs(v);
    if (!hldMantleSweepClear(ctx->runner, inst, endX, endY, needsSupport)) {
        COUNT_SWEEP(SWEEP_CONTACT);
        return false;
    }
    COUNT_SWEEP(SWEEP_CLEAR);

    result->x = endX;
    result->y = endY;
    result->penultimateX = v == 0 ? endX - sx : endX;
    result->penultimateY = v == 0 ? endY : endY - sy;
    result->horizontalSteps = h;
    result->verticalSteps = v;
    return true;
}
#endif

static bool predictZeroMovement(VMContext* ctx, Instance* inst, const RValue* args, int32_t count,
                                ZeroMovementResult* result) {
    COUNT_SWEEP(SWEEP_CALL);
#ifdef HLD_COMPARE_ZERO_MOVEMENT
    if (!zeroMovementEnabled) return false;
#endif
    if (count != 2 || args == nullptr || inst == nullptr || ctx->runner == nullptr ||
        inst->objectIndex < 0 || (uint32_t)inst->objectIndex >= ctx->dataWin->objt.count) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0 && epsilon < 0.5) || !zeroMovementNumber(args[0]) || !zeroMovementNumber(args[1])) return false;
    const RValue xc = Instance_getSelfVar(inst, zeroMovement.fields[ZERO_REMAINDER_X]);
    const RValue yc = Instance_getSelfVar(inst, zeroMovement.fields[ZERO_REMAINDER_Y]);
    if (xc.type != RVALUE_REAL || yc.type != RVALUE_REAL) return false;
    const GMLReal xTotal = xc.real + RValue_toReal(args[0]);
    const GMLReal yTotal = yc.real + RValue_toReal(args[1]);
    if (!isfinite(xTotal) || !isfinite(yTotal) || !isfinite(inst->x) || !isfinite(inst->y)) return false;
    const GMLReal h = GMLReal_bankersRound(xTotal);
    const GMLReal v = GMLReal_bankersRound(yTotal);
#ifdef HLD_MANTLE_SWEEP
    if (h == 0.0 && v == 0.0) return false;
    COUNT_SWEEP(SWEEP_MOVING);
#else
    if (h != 0.0 || v != 0.0) return false;
#endif

    const bool isChar = inst->objectIndex == 419;
    const bool isEnemy = ctx->dataWin->objt.objects[inst->objectIndex].parentId == 17;
    bool noClip = false;
    if (isChar) {
        if (ctx->globalScopeInstance == nullptr) return false;
        noClip = RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, zeroMovement.editorNoClip));
    }
    const RValue zero = RValue_makeReal(0.0);
    RValue cliff = zero, over = zero, under = zero;
    if (isEnemy) {
        const RValue flying = Instance_getSelfVar(inst, zeroMovement.flying);
        if (!zeroMovementNumber(flying) && flying.type != RVALUE_BOOL) return false;
        if (GMLReal_fabs(RValue_toReal(flying)) <= epsilon) {
            cliff = Instance_getSelfVar(inst, zeroMovement.cliffBlock);
            if (!zeroMovementNumber(cliff) && cliff.type != RVALUE_BOOL) return false;
            cliff = zeroMovementVariant(cliff);
        }
    }
    if (isChar || isEnemy) {
        over = Instance_getSelfVar(inst, zeroMovement.overCrissCross);
        if (!zeroMovementNumber(over) && over.type != RVALUE_BOOL) return false;
        over = zeroMovementVariant(over);
        under = RValue_makeBool(!RValue_toBool(over));
    }
    result->fields[ZERO_REMAINDER_X] = zeroMovementVariant(RValue_makeReal(xTotal - h));
    result->fields[ZERO_REMAINDER_Y] = zeroMovementVariant(RValue_makeReal(yTotal - v));
    result->fields[ZERO_IS_CHAR] = RValue_makeBool(isChar);
    result->fields[ZERO_IS_ENEMY] = RValue_makeBool(isEnemy);
    result->fields[ZERO_SLOPES] = RValue_makeReal(isChar || inst->objectIndex == 342);
    result->fields[ZERO_NO_CLIP] = RValue_makeReal(noClip);
    result->fields[ZERO_CLIFF] = cliff;
    result->fields[ZERO_OVER] = over;
    result->fields[ZERO_UNDER] = under;
    result->fields[ZERO_REPEATS] = zeroMovementVariant(RValue_makeReal(GMLReal_fabs(v)));
#ifdef HLD_MANTLE_SWEEP
    return predictMantleSweep(ctx, inst, args, h, v, result);
#else
    const float x = inst->x, y = inst->y;
    const float roundedX = GMLReal_bankersRound(x), roundedY = GMLReal_bankersRound(y);
    result->x = roundedX == x ? x : roundedX;
    result->y = roundedY == y ? y : roundedY;
    return true;
#endif
}

#ifndef HLD_VERIFY_ZERO_MOVEMENT
static void applyZeroMovement(VMContext* ctx, Instance* inst, const ZeroMovementResult* result) {
    repeat(ZERO_MOVEMENT_FIELD_COUNT, i) Instance_setSelfVar(inst, zeroMovement.fields[i], result->fields[i]);
#ifdef HLD_MANTLE_SWEEP
    // Every original probe flushes the grid before advancing. With no
    // callbacks, only the mover changes bucket order between probes. Leave
    // the same penultimate membership and final pending position.
    if (inst->x != result->penultimateX || inst->y != result->penultimateY) {
        VMBuiltins_setVariable(ctx, inst, BUILTIN_VAR_X, "x", RValue_makeReal(result->penultimateX), -1);
        VMBuiltins_setVariable(ctx, inst, BUILTIN_VAR_Y, "y", RValue_makeReal(result->penultimateY), -1);
        SpatialGrid_syncGrid(ctx->runner, ctx->runner->spatialGrid);
    }
#endif
    VMBuiltins_setVariable(ctx, inst, BUILTIN_VAR_X, "x", RValue_makeReal(result->x), -1);
    VMBuiltins_setVariable(ctx, inst, BUILTIN_VAR_Y, "y", RValue_makeReal(result->y), -1);
}
#else
static void verifyZeroMovement(Instance* inst, const ZeroMovementResult* expected, RValue result) {
    require(result.type == RVALUE_STRING && result.ownsReference && result.gmlStackType == GML_TYPE_STRING);
    require(strcmp(result.string, "none") == 0);
    require(memcmp(&inst->x, &expected->x, sizeof(inst->x)) == 0);
    require(memcmp(&inst->y, &expected->y, sizeof(inst->y)) == 0);
    repeat(ZERO_MOVEMENT_FIELD_COUNT, i) {
        const RValue state = Instance_getSelfVar(inst, zeroMovement.fields[i]);
        const RValue field = expected->fields[i];
        require(state.type == field.type && state.gmlStackType == field.gmlStackType &&
                state.assetRefType == field.assetRefType && state.ownsReference == field.ownsReference);
        if (state.type == RVALUE_REAL) require(memcmp(&state.real, &field.real, sizeof(state.real)) == 0);
        else require(state.int32 == field.int32);
    }
    if (++zeroMovementCalls == 100) {
#ifdef HLD_MANTLE_SWEEP
        logInfo("HLD: checked 100 clear movement sweeps\n");
#else
        logInfo("HLD: checked 100 zero-step movement calls\n");
#endif
    }
}
#endif
