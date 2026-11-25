typedef enum {
    MOVEMENT_HELPER_NONE,
    MOVEMENT_HELPER_APPROACH,
    MOVEMENT_HELPER_DISTANCE,
    MOVEMENT_HELPER_IS_TYPE,
    MOVEMENT_HELPER_COUNT
} MovementHelper;

typedef struct {
    const VMContext* context;
    const CodeEntry* approach;
    const CodeEntry* distance;
    const CodeEntry* isType;
} MovementHelpers;

static MovementHelpers movementHelpers;

#ifdef HLD_COMPARE_MOVEMENT_HELPERS
static bool nativeMovementHelpers;
#endif

#if defined(HLD_COMPARE_MOVEMENT_HELPERS) || defined(HLD_VERIFY_MOVEMENT_HELPERS)
static unsigned movementHelperCalls[MOVEMENT_HELPER_COUNT];
#endif

static void bindMovementHelpers(VMContext* ctx) {
    movementHelpers = (MovementHelpers){
        .context = ctx,
        .approach = matchHldCode(ctx, "gml_Script_approach", 108, 0x70bdaf57u),
        .distance = matchHldCode(ctx, "gml_Script_DistanceBetween", 220, 0xe8d5afdcu),
        .isType = matchHldCode(ctx, "gml_Script_IsType", 416, 0xc748538fu)
    };
#ifdef HLD_COMPARE_MOVEMENT_HELPERS
    FILE* flag = fopen("ux0:data/hld-vm/native-movement-helpers", "r");
    nativeMovementHelpers = flag != nullptr;
    if (flag != nullptr) fclose(flag);
#endif
}

#ifdef HLD_COMPARE_MOVEMENT_HELPERS
void hldReportMovementHelpers(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita movement helpers frame=%d enabled=%d approach=%u distance=%u object_type=%u\n",
            frame, (int)nativeMovementHelpers, movementHelperCalls[MOVEMENT_HELPER_APPROACH],
            movementHelperCalls[MOVEMENT_HELPER_DISTANCE], movementHelperCalls[MOVEMENT_HELPER_IS_TYPE]);
    memset(movementHelperCalls, 0, sizeof(movementHelperCalls));
}
#endif

static bool movementNumber(RValue input) {
    return input.type == RVALUE_INT32 || (input.type == RVALUE_REAL && isfinite(input.real));
}

static bool movementId(RValue input, int32_t* result) {
    if (input.type == RVALUE_INT32) {
        *result = input.int32;
        return true;
    }
    if (input.type != RVALUE_REAL || !(input.real >= -2147483648.0 && input.real < 2147483648.0)) return false;
    *result = (int32_t)input.real;
    return *result == input.real;
}

static bool movementApproach(const RValue* args, GMLReal epsilon, RValue* result) {
    if (!movementNumber(args[0]) || !movementNumber(args[1]) || !movementNumber(args[2])) return false;
    const GMLReal value = RValue_toReal(args[0]);
    const GMLReal goal = RValue_toReal(args[1]);
    const bool rising = value - goal < -epsilon;
    GMLReal next;
    if (args[0].type == RVALUE_INT32 && args[2].type == RVALUE_INT32) {
        const int64_t step = args[2].int32;
        const int64_t integer = args[0].int32 + (rising ? step : -step);
        if (integer < INT32_MIN || integer > INT32_MAX) return false;
        next = (GMLReal)integer;
    } else {
        const GMLReal step = RValue_toReal(args[2]);
        next = rising ? value + step : value - step;
    }
    if (!isfinite(next)) return false;
    // min/max retain the first value in a tie, including signed zero.
    *result = RValue_makeReal((rising ? goal < next : goal > next) ? goal : next);
    result->gmlStackType = GML_TYPE_VARIABLE;
    return true;
}

static bool movementDistance(Runner* runner, int32_t from, int32_t to, RValue* result) {
    if (from < INSTANCE_ID_BASE || to < INSTANCE_ID_BASE) return false;
    Instance* first = hmget(runner->instancesById, from);
    if (first == nullptr || !first->active || first->objectIndex == STRUCT_OBJECT_INDEX) return false;
    Instance* second = hmget(runner->instancesById, to);
    if (second == nullptr || !second->active || second->objectIndex == STRUCT_OBJECT_INDEX) return false;
    const GMLReal dx = (GMLReal)second->x - (GMLReal)first->x;
    const GMLReal dy = (GMLReal)second->y - (GMLReal)first->y;
    *result = RValue_makeReal(GMLReal_sqrt(dx * dx + dy * dy));
    result->gmlStackType = GML_TYPE_VARIABLE;
    return true;
}

static bool movementInstanceType(Runner* runner, int32_t id, int32_t target, RValue* result) {
    const Objt* objects = &runner->dataWin->objt;
    if ((id < INSTANCE_ID_BASE && id != INSTANCE_NOONE) || target < 0 || (uint32_t)target >= objects->count) return false;
    Instance* instance = hmget(runner->instancesById, id);
    bool match = false;
    if (instance != nullptr && instance->active) {
        if (instance->objectIndex == STRUCT_OBJECT_INDEX) return false;
        int32_t object = instance->objectIndex;
        unsigned depth = 0;
        while (object >= 0 && (uint32_t)object < objects->count) {
            if (depth++ == 32) return false;
            if (object == target) { match = true; break; }
            object = objects->objects[object].parentId;
        }
    }
    *result = RValue_makeInt32(match);
    result->gmlStackType = GML_TYPE_VARIABLE;
    return true;
}

static MovementHelper tryMovementHelper(VMContext* ctx, const CodeEntry* code, const RValue* args,
                                       int32_t argCount, RValue* result) {
    if (argCount == 3) {
        if (code != movementHelpers.approach) return MOVEMENT_HELPER_NONE;
    } else if (argCount != 2 || (code != movementHelpers.distance && code != movementHelpers.isType)) {
        return MOVEMENT_HELPER_NONE;
    }
    if (args == nullptr || ctx != movementHelpers.context || ctx->runner == nullptr) return MOVEMENT_HELPER_NONE;
#ifdef HLD_COMPARE_MOVEMENT_HELPERS
    if (!nativeMovementHelpers) return MOVEMENT_HELPER_NONE;
#endif
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return MOVEMENT_HELPER_NONE;
    if (argCount == 3) {
        return movementApproach(args, epsilon, result) ? MOVEMENT_HELPER_APPROACH : MOVEMENT_HELPER_NONE;
    }
    int32_t first, second;
    if (!movementId(args[0], &first) || !movementId(args[1], &second)) return MOVEMENT_HELPER_NONE;
    if (code == movementHelpers.distance && movementDistance(ctx->runner, first, second, result)) {
        return MOVEMENT_HELPER_DISTANCE;
    }
    if (code == movementHelpers.isType && movementInstanceType(ctx->runner, first, second, result)) {
        return MOVEMENT_HELPER_IS_TYPE;
    }
    return MOVEMENT_HELPER_NONE;
}
