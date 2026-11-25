"""Resolve enemy targeting using the existing live object order and ray query."""

from runtime_verification_source import replace_once


ENEMY_TARGET = r'''
enum {
    ENEMY_TARGET, ENEMY_DECOY, ENEMY_CRYSTAL, ENEMY_TARGET_X, ENEMY_TARGET_Y,
    ENEMY_TARGET_DIR, ENEMY_TARGET_DISTANCE, ENEMY_TARGET_LEAD,
    ENEMY_TARGET_X_SPEED, ENEMY_TARGET_Y_SPEED, ENEMY_TARGET_FIELD_COUNT
};

typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    BuiltinFunc crystalRay;
    int32_t field[ENEMY_TARGET_FIELD_COUNT], owner, direction, xSpeed, ySpeed;
} EnemyTargetScript;

typedef struct {
    RValue field[ENEMY_TARGET_FIELD_COUNT];
    RValue player;
    bool choseDecoy, choseCrystal, targetIsPlayer;
} EnemyTargetPlan;

static EnemyTargetScript enemyTarget;
static unsigned enemyTargetCalls, enemyTargetDecoys, enemyTargetCrystals;

static int32_t enemyTargetVar(VMContext* ctx, const uint8_t* bytes, const char* name, uint8_t kind) {
    const uint32_t operand = resolveVarOperand(bytes);
    const Variable* variable = resolveVarDef(ctx, operand);
    if (((operand >> 24) & 0xf8) != kind || variable->varID < 0 || strcmp(variable->name, name) != 0) return -1;
    return variable->varID;
}

static bool enemyTargetBuiltin(VMContext* ctx, const uint8_t* bytes, const char* name) {
    const uint32_t id = resolveFuncOperand(bytes);
    const BuiltinFunc builtin = VM_findBuiltin(ctx, name);
    return builtin != nullptr && id < ctx->funcCallCacheCount &&
        ctx->funcCallCache[id].builtin == (void*)builtin && ctx->funcCallCache[id].scriptCodeIndex < 0;
}

static void bindEnemyTarget(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15 ||
        ctx->dataWin->objt.count <= 433) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_find_target", 784, 0x733c0baau);
    const CodeEntry* distance = matchHldCode(ctx, "gml_Script_DistanceBetween", 220, 0xe8d5afdcu);
    if (code == nullptr || code->localsCount != 1 || distance == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint32_t call = resolveFuncOperand(bytes + 0x1c8);
    if (call >= ctx->funcCallCacheCount || ctx->funcCallCache[call].builtin != nullptr ||
        ctx->funcCallCache[call].scriptCodeIndex != distance - ctx->dataWin->code.entries) return;
    const uint16_t callOffsets[] = {0x00c, 0x034, 0x05c, 0x074, 0x110, 0x210, 0x228, 0x2bc};
    const char* callNames[] = {"instance_exists", "instance_nearest", "instance_nearest", "instance_exists",
                               "collision_line", "point_distance", "min", "instance_exists"};
    repeat(sizeof(callOffsets) / sizeof(*callOffsets), i)
        if (!enemyTargetBuiltin(ctx, bytes + callOffsets[i], callNames[i])) return;

    EnemyTargetScript bound = {.context = ctx, .code = code, .crystalRay = VM_findBuiltin(ctx, "collision_line")};
    const uint16_t offsets[] = {0x03c, 0x064, 0x118, 0x158, 0x174, 0x1b0, 0x1d0, 0x230, 0x24c, 0x268};
    const char* names[] = {"target", "decoyTarget", "crystalCol", "tx", "ty", "tdir", "tdis", "tadis", "txspd", "tyspd"};
    repeat(ENEMY_TARGET_FIELD_COUNT, i) {
        bound.field[i] = enemyTargetVar(ctx, bytes + offsets[i], names[i], VARTYPE_NORMAL);
        if (bound.field[i] < 0) return;
        repeat(i, j) if (bound.field[i] == bound.field[j]) return;
    }
    bound.owner = enemyTargetVar(ctx, bytes + 0x090, "owner", VARTYPE_STACKTOP);
    bound.direction = enemyTargetVar(ctx, bytes + 0x1a8, "dir", VARTYPE_STACKTOP);
    bound.xSpeed = enemyTargetVar(ctx, bytes + 0x1f8, "xspd", VARTYPE_STACKTOP);
    bound.ySpeed = enemyTargetVar(ctx, bytes + 0x1e4, "yspd", VARTYPE_STACKTOP);
    if (bound.owner < 0 || bound.direction < 0 || bound.xSpeed < 0 || bound.ySpeed < 0 ||
        bound.xSpeed != enemyTargetVar(ctx, bytes + 0x244, "xspd", VARTYPE_STACKTOP) ||
        bound.ySpeed != enemyTargetVar(ctx, bytes + 0x260, "yspd", VARTYPE_STACKTOP)) return;
    enemyTarget = bound;
    logInfo("HLD: bound native enemy target selection\n");
}

static Instance* enemyNearestTarget(Instance* const* list, GMLReal x, GMLReal y) {
    const size_t count = arrlenu(list);
    if (count == 1) return list[0]->active ? list[0] : nullptr;
    Instance* nearest = nullptr;
    GMLReal best = 0;
    for (size_t i = 0; i < count; ++i) {
        Instance* candidate = list[i];
        if (!candidate->active) continue;
        const GMLReal dx = (GMLReal)candidate->x - x, dy = (GMLReal)candidate->y - y;
        const GMLReal distanceSq = dx * dx + dy * dy;
        if (nearest == nullptr || distanceSq < best) {
            nearest = candidate;
            best = distanceSq;
        }
    }
    return nearest;
}

static bool enemyTargetScalar(RValue value) {
    return !value.ownsReference && (value.type == RVALUE_INT32 || value.type == RVALUE_BOOL ||
        (value.type == RVALUE_REAL && isfinite(value.real)));
}

static RValue enemyTargetValue(RValue value) {
    value.gmlStackType = GML_TYPE_VARIABLE;
    return value;
}

static RValue enemyTargetReal(GMLReal value) {
    return enemyTargetValue(RValue_makeReal(value));
}

static bool enemyTargetInstance(const Instance* instance, const Instance* self) {
    return instance != nullptr && instance != self && instance->active &&
        instance->instanceId >= INSTANCE_ID_BASE && instance->instanceId < (1u << 24) &&
        isfinite(instance->x) && isfinite(instance->y);
}

static bool predictEnemyTarget(VMContext* ctx, Instance* self, EnemyTargetPlan* plan) {
    Runner* runner = ctx->runner;
    if (runner == nullptr || ctx->currentCallFrame == nullptr || self == nullptr || !self->active ||
        runner->instancesByObject == nullptr || !(runner->mathEpsilon >= 0.0 && runner->mathEpsilon < 0.5) ||
        self->instanceId < INSTANCE_ID_BASE || self->instanceId >= (1u << 24) ||
        !isfinite(self->x) || !isfinite(self->y)) return false;
    const GMLReal x = self->x, y = self->y;
    Instance* player = enemyNearestTarget(runner->instancesByObject[419], x, y);
    if (!enemyTargetInstance(player, self)) return false;
    Instance* decoy = enemyNearestTarget(runner->instancesByObject[420], x, y);
    Instance* target = player;
    plan->player = enemyTargetReal(player->instanceId);
    plan->choseDecoy = plan->choseCrystal = false;
    plan->field[ENEMY_DECOY] = enemyTargetReal(INSTANCE_NOONE);
    if (decoy != nullptr) {
        if (!enemyTargetInstance(decoy, self)) return false;
        const RValue owner = Instance_getSelfVar(decoy, enemyTarget.owner);
        if (!enemyTargetScalar(owner)) return false;
        plan->field[ENEMY_DECOY] = enemyTargetReal(decoy->instanceId);
        if (GMLReal_fabs(RValue_toReal(owner) - (GMLReal)player->instanceId) <= runner->mathEpsilon) {
            target = decoy;
            plan->choseDecoy = true;
        }
    }

    plan->field[ENEMY_CRYSTAL] = enemyTargetReal(INSTANCE_NOONE);
    if (arrlenu(runner->instancesByObject[433]) != 0) {
        RValue args[] = {RValue_makeReal(x), RValue_makeReal(y), RValue_makeReal(target->x),
                         RValue_makeReal(target->y), RValue_makeInt32(433), RValue_makeInt32(0), RValue_makeInt32(0)};
        RValue result = enemyTarget.crystalRay(ctx, args, 7);
        if (!enemyTargetScalar(result)) {
            RValue_free(&result);
            return false;
        }
        plan->field[ENEMY_CRYSTAL] = enemyTargetValue(result);
        const int32_t id = RValue_toInt32(result);
        if (id != INSTANCE_NOONE) {
            Instance* crystal = VM_findInstanceByTarget(ctx, id);
            if (!enemyTargetInstance(crystal, self)) return false;
            target = crystal;
            plan->choseCrystal = true;
        }
    }
    plan->field[ENEMY_TARGET] = enemyTargetReal(target->instanceId);
    plan->field[ENEMY_TARGET_X] = enemyTargetReal(target->x);
    plan->field[ENEMY_TARGET_Y] = enemyTargetReal(target->y);
    plan->targetIsPlayer = target->objectIndex == 419;
    if (!plan->targetIsPlayer) return true;

    const RValue direction = Instance_getSelfVar(target, enemyTarget.direction);
    const RValue xSpeed = Instance_getSelfVar(target, enemyTarget.xSpeed);
    const RValue ySpeed = Instance_getSelfVar(target, enemyTarget.ySpeed);
    if (!enemyTargetScalar(direction) || !enemyTargetScalar(xSpeed) || !enemyTargetScalar(ySpeed)) return false;
    const GMLReal dx = (GMLReal)target->x - x, dy = (GMLReal)target->y - y;
    const GMLReal vx = RValue_toReal(xSpeed), vy = RValue_toReal(ySpeed);
    const GMLReal speed = GMLReal_sqrt(vx * vx + vy * vy);
    plan->field[ENEMY_TARGET_DIR] = enemyTargetValue(direction);
    plan->field[ENEMY_TARGET_DISTANCE] = enemyTargetReal(GMLReal_sqrt(dx * dx + dy * dy));
    plan->field[ENEMY_TARGET_LEAD] = enemyTargetReal(speed < (GMLReal)3.5 ? speed : (GMLReal)3.5);
    plan->field[ENEMY_TARGET_X_SPEED] = enemyTargetValue(xSpeed);
    plan->field[ENEMY_TARGET_Y_SPEED] = enemyTargetValue(ySpeed);
    return true;
}

static void noteEnemyTarget(const EnemyTargetPlan* plan) {
    ++enemyTargetCalls;
    enemyTargetDecoys += plan->choseDecoy;
    enemyTargetCrystals += plan->choseCrystal;
}
'''

APPLY = r'''
static void applyEnemyTarget(Instance* self, const EnemyTargetPlan* plan) {
    Instance_setSelfVar(self, enemyTarget.field[ENEMY_TARGET], plan->player);
    Instance_setSelfVar(self, enemyTarget.field[ENEMY_DECOY], plan->field[ENEMY_DECOY]);
    if (plan->choseDecoy) Instance_setSelfVar(self, enemyTarget.field[ENEMY_TARGET], plan->field[ENEMY_DECOY]);
    Instance_setSelfVar(self, enemyTarget.field[ENEMY_CRYSTAL], plan->field[ENEMY_CRYSTAL]);
    if (plan->choseCrystal) Instance_setSelfVar(self, enemyTarget.field[ENEMY_TARGET], plan->field[ENEMY_CRYSTAL]);
    for (unsigned i = ENEMY_TARGET_X, end = plan->targetIsPlayer ? ENEMY_TARGET_FIELD_COUNT : ENEMY_TARGET_DIR;
         i < end; ++i) Instance_setSelfVar(self, enemyTarget.field[i], plan->field[i]);
}

static __attribute__((noinline)) bool runEnemyTarget(VMContext* ctx, Instance* self) {
    EnemyTargetPlan plan;
    if (!predictEnemyTarget(ctx, self, &plan)) return false;
    if (hldWatchCollisionMutation) hldCollisionMutated = true;
    applyEnemyTarget(self, &plan);
    noteEnemyTarget(&plan);
    return true;
}
'''

VERIFY = r'''
static void verifyEnemyTarget(Instance* self, const EnemyTargetPlan* plan) {
    const unsigned end = plan->targetIsPlayer ? ENEMY_TARGET_FIELD_COUNT : ENEMY_TARGET_DIR;
    for (unsigned i = 0; i < end; ++i) {
        const RValue actual = Instance_getSelfVar(self, enemyTarget.field[i]), expected = plan->field[i];
        require(actual.type == expected.type && actual.gmlStackType == expected.gmlStackType &&
                actual.assetRefType == expected.assetRefType && actual.ownsReference == expected.ownsReference);
        if (expected.type == RVALUE_REAL) require(memcmp(&actual.real, &expected.real, sizeof(actual.real)) == 0);
        else require(actual.int32 == expected.int32);
    }
}
'''


FIXTURE = r'''
static bool enemyTargetFixtureSpawned;

static void seedEnemyTargetFixture(Runner* runner, const Instance* self, const Instance* player) {
    if (enemyTargetFixtureSpawned || runner->frameCount < 400) return;
    enemyTargetFixtureSpawned = true;
    const char* mode = getenv("HLD_CHECK_TARGET_FIXTURE");
    if (mode == nullptr) return;
    const GMLReal x = ((GMLReal)self->x + player->x) * 0.5;
    const GMLReal y = ((GMLReal)self->y + player->y) * 0.5;
    if (strcmp(mode, "decoy") == 0) {
        Instance* decoy = Runner_createInstance(runner, x, y, 420);
        require(decoy != nullptr);
        Instance_setSelfVar(decoy, enemyTarget.owner, RValue_makeReal(player->instanceId));
    } else if (strcmp(mode, "crystal") == 0) {
        require(Runner_createInstance(runner, x, y, 433) != nullptr);
    }
}
'''


def with_enemy_target(code, *, checked=False, launch_flag=None):
    helper = ENEMY_TARGET + (VERIFY if checked else APPLY)
    if checked:
        anchor = "static bool predictEnemyTarget("
        helper = replace_once(helper, anchor, FIXTURE + "\n" + anchor)
        anchor = "    if (!enemyTargetInstance(player, self)) return false;"
        helper = replace_once(helper, anchor, anchor + "\n    seedEnemyTargetFixture(runner, self, player);")
    gate = f"{launch_flag} && " if launch_flag else ""
    if launch_flag:
        helper = f"extern bool {launch_flag};\n" + helper
        helper += f'''
void hldReportEnemyTarget(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita enemy targets frame=%d enabled=%d calls=%u decoys=%u crystals=%u\\n",
            frame, (int){launch_flag}, enemyTargetCalls, enemyTargetDecoys, enemyTargetCrystals);
    enemyTargetCalls = enemyTargetDecoys = enemyTargetCrystals = 0;
}}
'''
    else:
        helper += r'''
void hldReportEnemyTargetAtExit(void) {
    logInfo("HLD: enemy targets calls=%u decoys=%u crystals=%u\n", enemyTargetCalls, enemyTargetDecoys, enemyTargetCrystals);
}
'''
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    code = replace_once(code, anchor, helper + "\n" + anchor)
    anchor = "    packHldLiteralInstructions(ctx);"
    code = replace_once(code, anchor, "    bindEnemyTarget(ctx);\n" + anchor)
    guard = f"{gate}enemyTarget.code == code && enemyTarget.context == ctx && argCount == 0"
    if checked:
        setup = f'''
    EnemyTargetPlan targetPlan;
    const bool nativeTarget = {guard} && predictEnemyTarget(ctx, selfInstance, &targetPlan);
'''
        anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
        code = replace_once(code, anchor, anchor + r'''
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (nativeTarget) {
        require(ctx->stack.top == (int32_t)stackTopWhenTheCallFrameWasCreated);
        const RValue expected = scriptFallthroughReturnValue(ctx);
        require(result.type == expected.type && result.gmlStackType == expected.gmlStackType &&
                !result.ownsReference && RValue_toReal(result) == RValue_toReal(expected));
        verifyEnemyTarget(selfInstance, &targetPlan);
        noteEnemyTarget(&targetPlan);
    }
#endif
''')
    else:
        setup = f'''
    if ({guard} && runEnemyTarget(ctx, selfInstance)) {{
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }}
'''
    anchor = "    // Create a CallFrame for this script\n"
    code = replace_once(code, anchor, "#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && "
                        "!defined(ENABLE_VM_OPCODE_PROFILER)\n" + setup + "#endif\n\n" + anchor)
    anchor = "void VM_free(VMContext* ctx) {"
    return replace_once(code, anchor, anchor + "\n    if (enemyTarget.context == ctx) enemyTarget = (EnemyTargetScript){0};")


def with_enemy_target_exit(code):
    anchor = "    Runner_free(runner);"
    return replace_once(code, anchor, "    extern void hldReportEnemyTargetAtExit(void);\n"
                        "    hldReportEnemyTargetAtExit();\n" + anchor)
