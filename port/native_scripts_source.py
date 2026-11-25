"""Run small, frequently called HLD scripts as native C.

Each native script reproduces its GML exactly for numeric arguments and returns false (so the original
script runs) for anything it does not handle. The hook sits in VM_executeCode before the call frame is
built, so a native call costs no frame, argument copies or bytecode. The checked variant computes the
native answer, lets the script run, and requires an identical result; only pure scripts are native, so
computing first changes nothing.

Generated code is appended after the original source and the hooks share existing lines, so profile
feedback for the unchanged functions still matches.
"""

from runtime_verification_source import replace_once

SIGNATURE = ("RValue VM_executeCode(VMContext* ctx, int32_t codeIndex, Instance* selfInstance, Instance* otherInstance, "
             "int32_t eventObjectIndex, int32_t eventType, int32_t eventSubtype, RValue* args, int32_t argCount) {")

SCRIPTS = ("approach", "DistanceBetween", "IsAny", "IsType", "onscreenWid", "shader_set_paletteSwap", "Scenery_IsVisible",
           "ActionIsPaused", "onscreen", "CollisionLine", "CaseCheck",
           "OnAnyConsole", "hld_gamepad_button_check", "hld_gamepad_button_check_pressed",
           "hld_gamepad_button_check_released", "hld_gamepad_axis_value",
           "KillWithOwner", "StepInit", "GetSpd", "DirectionBetween", "PointInRegion",
           "HologramModT", "sin01", "OnGround", "dice", "mirand")

NATIVE = r'''
#define HLD_NATIVE_SCRIPTS_HOOKED
enum { HLD_NATIVE_NONE, HLD_NATIVE_ONSCREEN_SPRITE, HLD_NATIVE_APPROACH, HLD_NATIVE_DISTANCE_BETWEEN,
       HLD_NATIVE_IS_ANY, HLD_NATIVE_IS_TYPE, HLD_NATIVE_ONSCREEN_WID,
       HLD_NATIVE_PALETTE_SWAP, HLD_NATIVE_SCENERY_IS_VISIBLE,
       HLD_NATIVE_ACTION_IS_PAUSED, HLD_NATIVE_ONSCREEN, HLD_NATIVE_COLLISION_LINE, HLD_NATIVE_CASE_CHECK,
       HLD_NATIVE_ON_ANY_CONSOLE, HLD_NATIVE_PAD_CHECK, HLD_NATIVE_PAD_PRESSED, HLD_NATIVE_PAD_RELEASED, HLD_NATIVE_PAD_AXIS,
       HLD_NATIVE_KILL_WITH_OWNER, HLD_NATIVE_STEP_INIT, HLD_NATIVE_GET_SPD, HLD_NATIVE_DIRECTION_BETWEEN,
       HLD_NATIVE_POINT_IN_REGION, HLD_NATIVE_HOLOGRAM_MOD_T, HLD_NATIVE_SIN01, HLD_NATIVE_ON_GROUND, HLD_NATIVE_DICE,
       HLD_NATIVE_MIRAND, HLD_NATIVE_COUNT };
static const char* const hldNativeNames[HLD_NATIVE_COUNT] = {
    nullptr, "-unused-onscreen_sprite", "gml_Script_approach", "gml_Script_DistanceBetween", "gml_Script_IsAny",
    "gml_Script_IsType", "gml_Script_onscreenWid", "gml_Script_shader_set_paletteSwap",
    "gml_Script_Scenery_IsVisible", "gml_Script_ActionIsPaused", "gml_Script_onscreen", "gml_Script_CollisionLine",
    "gml_Script_CaseCheck", "gml_Script_OnAnyConsole", "gml_Script_hld_gamepad_button_check",
    "gml_Script_hld_gamepad_button_check_pressed", "gml_Script_hld_gamepad_button_check_released",
    "gml_Script_hld_gamepad_axis_value", "gml_Script_KillWithOwner", "gml_Script_StepInit", "gml_Script_GetSpd",
    "gml_Script_DirectionBetween", "gml_Script_PointInRegion", "gml_Script_HologramModT", "gml_Script_sin01",
    "gml_Script_OnGround", "gml_Script_dice", "gml_Script_mirand"};

typedef struct {
    const VMContext* context;
    uint8_t* kinds;
    uint32_t count;
    unsigned calls[HLD_NATIVE_COUNT], fallbacks[HLD_NATIVE_COUNT], checked;
} HldNativeScripts;
static HldNativeScripts hldNative;

static void hldNativeBind(VMContext* ctx) {
    free(hldNative.kinds);
    hldNative = (HldNativeScripts){.context = ctx};
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    hldNative.count = ctx->dataWin->code.count;
    hldNative.kinds = safeCalloc(hldNative.count, 1);
    unsigned bound = 0;
    repeat(hldNative.count, i) {
        const CodeEntry* entry = &ctx->dataWin->code.entries[i];
        if (!entry->present || entry->name == nullptr) continue;
        for (int kind = 1; kind < HLD_NATIVE_COUNT; ++kind) {
            if (strcmp(entry->name, hldNativeNames[kind]) == 0) {
                hldNative.kinds[i] = (uint8_t) kind;
                ++bound;
            }
        }
    }
    logInfo("HLD: bound %u native scripts\n", bound);
}

static inline int hldNativeKind(VMContext* ctx, int32_t codeIndex) {
    if (hldNative.context != ctx) hldNativeBind(ctx);
    return (uint32_t) codeIndex < hldNative.count ? hldNative.kinds[codeIndex] : HLD_NATIVE_NONE;
}

static inline bool hldNumber(const RValue* value, GMLReal* out) {
    switch (value->type) {
        case RVALUE_REAL: *out = value->real; return true;
        case RVALUE_INT32: case RVALUE_BOOL: *out = (GMLReal) value->int32; return true;
#ifndef NO_RVALUE_INT64
        case RVALUE_INT64: *out = (GMLReal) value->int64; return true;
#endif
        default: return false;
    }
}

// handleCmp's numeric comparison: values within the math epsilon are equal.
static inline int hldCompare(VMContext* ctx, GMLReal a, GMLReal b) {
    const GMLReal diff = a - b;
    const GMLReal epsilon = ctx->runner != nullptr ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
    return GMLReal_fabs(diff) <= epsilon ? 0 : diff < 0 ? -1 : 1;
}

static inline RValue hldScriptResult(RValue value) {
    value.gmlStackType = value.type == RVALUE_STRING ? GML_TYPE_STRING : GML_TYPE_VARIABLE;
    return value;
}

static bool hldViewRect(VMContext* ctx, GMLReal* x, GMLReal* y, GMLReal* w, GMLReal* h) {
    GMLCamera* camera = ctx->runner != nullptr ? Runner_getCameraForView(ctx->runner, 0) : nullptr;
    *x = camera != nullptr ? (GMLReal) camera->viewX : 0.0;
    *y = camera != nullptr ? (GMLReal) camera->viewY : 0.0;
    *w = camera != nullptr ? (GMLReal) camera->viewWidth : 0.0;
    *h = camera != nullptr ? (GMLReal) camera->viewHeight : 0.0;
    return true;
}

static bool hldApproach(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 3 || args == nullptr) return false;
    GMLReal a, b, step;
    for (int i = 0; i < 3; ++i) if (args[i].type != RVALUE_REAL && args[i].type != RVALUE_INT32) return false;
    if (args[0].type == RVALUE_INT32 && args[2].type == RVALUE_INT32) {
        // Integer addition and subtraction in the VM are exact only without int32 overflow.
        const int64_t sum = (int64_t) args[0].int32 + args[2].int32, difference = (int64_t) args[0].int32 - args[2].int32;
        if (sum < INT32_MIN || sum > INT32_MAX || difference < INT32_MIN || difference > INT32_MAX) return false;
    }
    a = RValue_toReal(args[0]);
    b = RValue_toReal(args[1]);
    step = RValue_toReal(args[2]);
    GMLReal value;
    if (hldCompare(ctx, a, b) < 0) {
        const GMLReal sum = a + step;
        value = INFINITY;
        if (value > sum) value = sum;
        if (value > b) value = b;
    } else {
        const GMLReal difference = a - step;
        value = -INFINITY;
        if (difference > value) value = difference;
        if (b > value) value = b;
    }
    *result = hldScriptResult(RValue_makeReal(value));
    return true;
}

static Instance* hldExistingInstance(VMContext* ctx, const RValue* value, bool* handled) {
    GMLReal number;
    if (!hldNumber(value, &number)) {
        *handled = false;
        return nullptr;
    }
    const int32_t id = RValue_toInt32(*value);
    if (id >= 0 && (uint32_t) id < ctx->dataWin->objt.count) {
        *handled = false;
        return nullptr;
    }
    Instance* instance = id >= 0 ? VM_findInstanceByTarget(ctx, id) : nullptr;
    return instance != nullptr && instance->active ? instance : nullptr;
}

static bool hldDistanceBetween(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 2 || args == nullptr) return false;
    bool handled = true;
    Instance* first = hldExistingInstance(ctx, &args[0], &handled);
    if (!handled) return false;
    if (first == nullptr) {
        *result = hldScriptResult(RValue_makeInt64(999999999999999LL));
        return true;
    }
    Instance* second = hldExistingInstance(ctx, &args[1], &handled);
    if (!handled) return false;
    if (second == nullptr) {
        *result = hldScriptResult(RValue_makeInt64(999999999999999LL));
        return true;
    }
    // The builtin itself, so contraction of its arithmetic matches the script exactly.
    static BuiltinFunc pointDistance;
    if (pointDistance == nullptr) pointDistance = VM_findBuiltin(ctx, "point_distance");
    if (pointDistance == nullptr) return false;
    RValue coordinates[4] = {RValue_makeReal((GMLReal) first->x), RValue_makeReal((GMLReal) first->y),
                             RValue_makeReal((GMLReal) second->x), RValue_makeReal((GMLReal) second->y)};
    *result = hldScriptResult(pointDistance(ctx, coordinates, 4));
    return true;
}

static bool hldIsAny(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 1 || args == nullptr) return false;
    GMLReal first;
    if (!hldNumber(&args[0], &first)) return false;
    for (int32_t i = 1; i < argCount; ++i) {
        if (!hldNumber(&args[i], &(GMLReal){0})) return false;
    }
    int32_t found = 0;
    for (int32_t i = 1; i < argCount && !found; ++i) {
        GMLReal other;
        hldNumber(&args[i], &other);
        found = hldCompare(ctx, first, other) == 0;
    }
    *result = hldScriptResult(RValue_makeInt32(found));
    return true;
}

// object_get_parent: out-of-range objects have parent -1.
static inline int32_t hldObjectParent(VMContext* ctx, int32_t object) {
    return object >= 0 && (uint32_t) object < ctx->dataWin->objt.count ? ctx->dataWin->objt.objects[object].parentId : -1;
}

static bool hldIsType(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 2 || args == nullptr) return false;
    GMLReal target, wanted;
    if (!hldNumber(&args[0], &target) || !hldNumber(&args[1], &wanted)) return false;
    const int32_t id = RValue_toInt32(args[0]);
    int32_t found = 0;
    if (id >= 0 && (uint32_t) id < ctx->dataWin->objt.count) {
        // with(object) visits every instance; only the no-instance case is native.
        if (VM_findInstanceByTarget(ctx, id) != nullptr) return false;
        for (int32_t object = id; object != -1 && !found; object = hldObjectParent(ctx, object)) {
            found = hldCompare(ctx, (GMLReal) object, wanted) == 0;
        }
    } else if (id >= 0) {
        Instance* instance = VM_findInstanceByTarget(ctx, id);
        if (instance != nullptr && instance->active) {
            int32_t object = instance->objectIndex;
            found = hldCompare(ctx, (GMLReal) object, wanted) == 0;
            while (object != -1 && !found) {
                object = hldObjectParent(ctx, object);
                found = hldCompare(ctx, (GMLReal) object, wanted) == 0;
            }
        }
    }
    *result = hldScriptResult(RValue_makeInt32(found));
    return true;
}

static bool hldOnscreenWid(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 5 || args == nullptr) return false;
    GMLReal x, y, w, h, extra;
    if (!hldNumber(&args[0], &x) || !hldNumber(&args[1], &y) || !hldNumber(&args[2], &w) ||
        !hldNumber(&args[3], &h) || !hldNumber(&args[4], &extra)) return false;
    GMLReal vx, vy, vw, vh;
    hldViewRect(ctx, &vx, &vy, &vw, &vh);
    int off = 0;
    if (hldCompare(ctx, x, vx + vw + extra) > 0) ++off;
    else if (hldCompare(ctx, x + w + extra, vx) < 0) ++off;
    if (hldCompare(ctx, y, vy + vh + extra) > 0) ++off;
    else if (hldCompare(ctx, y + h + extra, vy) < 0) ++off;
    *result = hldScriptResult(RValue_makeInt32(off >= 2 ? 0 : 1));
    return true;
}

// shader_set_paletteSwap builds 14 input and output colours (white by default) from two arrays or from argument
// pairs, then uploads them with the 02.78 direct palette path. The local arrays are discarded on return.
static bool hldPaletteNumber(const RValue* value, int32_t* color) {
    GMLReal number;
    if (!paletteNumber(*value, &number) || number < INT32_MIN || number > INT32_MAX) return false;
    *color = (int32_t) number;
    return true;
}

static bool hldPaletteSwap(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (!hldPaletteTail || ctx != paletteTail.context || paletteTail.program == nullptr || argCount < 2 ||
        args == nullptr || ctx->runner == nullptr || ctx->runner->renderer == nullptr || ctx->globalScopeInstance == nullptr) {
        return false;
    }
    GMLReal leeway = 0.1, first;
    if (!hldNumber(&args[0], &first)) return false;
    if (hldCompare(ctx, first, 0) >= 0) {
        if (!paletteNumber(args[0], &leeway)) return false;
    }
    int32_t colors[2][PALETTE_COLORS];
    repeat(PALETTE_COLORS, i) colors[0][i] = colors[1][i] = 16777215;
    if (args[1].type == RVALUE_ARRAY) {
        if (argCount < 3 || args[2].type != RVALUE_ARRAY || args[1].array == nullptr || args[2].array == nullptr) return false;
        const int32_t length = GMLArray_length1D(args[1].array);
        if (length > PALETTE_COLORS || GMLArray_length1D(args[2].array) < length) return false;
        for (int32_t i = 0; i < length; ++i) {
            const RValue in = GMLArray_get(args[1].array, i), out = GMLArray_get(args[2].array, i);
            if (!hldPaletteNumber(&in, &colors[0][i]) || !hldPaletteNumber(&out, &colors[1][i])) return false;
        }
    } else {
        const int32_t pairs = argCount / 2;
        if (pairs > PALETTE_COLORS || 2 * pairs >= argCount) return false;
        for (int32_t i = 0; i < pairs; ++i) {
            if (!hldPaletteNumber(&args[1 + 2 * i], &colors[0][i]) || !hldPaletteNumber(&args[2 + 2 * i], &colors[1][i])) return false;
        }
    }
    PaletteWrite writes[PALETTE_WRITES];
    writes[0].value[0] = (float) leeway;
    GMLReal number;
    for (unsigned i = 0; i < PALETTE_WRITES; ++i) {
        const RValue handle = Instance_getSelfVar(ctx->globalScopeInstance, paletteTail.handles[i]);
        if (!paletteNumber(handle, &number) || number < INT32_MIN || number > INT32_MAX) return false;
        writes[i].handle = (int32_t) number;
    }
    for (unsigned i = 0; i < 2 * PALETTE_COLORS; ++i) {
        const int32_t color = colors[i % 2][i / 2];
        writes[i + 1].value[0] = (float)((GMLReal) BGR_R(color) / (GMLReal) 255);
        writes[i + 1].value[1] = (float)((GMLReal) BGR_G(color) / (GMLReal) 255);
        writes[i + 1].value[2] = (float)((GMLReal) BGR_B(color) / (GMLReal) 255);
    }
    Renderer* renderer = ctx->runner->renderer;
    renderer->vtable->gpuResetShader(renderer);
    renderer->vtable->gpuSetShader(renderer, 2);
    renderer->vtable->shaderSetUniformF(renderer, writes[0].handle, 1, writes[0].value[0], 0, 0, 0);
    for (unsigned i = 1; i < PALETTE_WRITES; ++i) {
        const float* value = writes[i].value;
        renderer->vtable->shaderSetUniformF(renderer, writes[i].handle, 3, value[0], value[1], value[2], 0);
    }
    *result = scriptFallthroughReturnValue(ctx);
    return true;
}

// Scenery_IsVisible reads self and global variables; it runs natively only when each one it reaches is a number.
static struct { const VMContext* context; bool ok; int32_t roomPrinting, deactivateInstance, editorHidden, bg, flr, fg; } hldSceneryVars;

static int32_t hldVariableId(VMContext* ctx, const char* name, bool global) {
    // hldcVarRef's choice: the entry of the requested scope, otherwise the first non-local one.
    int32_t fallback = INT32_MIN;
    for (uint32_t i = 0; i < ctx->dataWin->vari.variableCount; ++i) {
        const Variable* variable = &ctx->dataWin->vari.variables[i];
        if (strcmp(variable->name, name) != 0 || variable->instanceType == INSTANCE_LOCAL) continue;
        if ((variable->instanceType == INSTANCE_GLOBAL) == global) return variable->varID;
        if (fallback == INT32_MIN) fallback = variable->varID;
    }
    return fallback;
}

static inline bool hldVariableTruth(Instance* inst, int32_t varID, bool* truth) {
    const RValue value = Instance_getSelfVar(inst, varID);
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32 && value.type != RVALUE_BOOL) return false;
    *truth = RValue_toBool(value);
    return true;
}

static bool hldSceneryIsVisible(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    Instance* self = hldNativeSelf;
    Instance* global = ctx->globalScopeInstance;
    if (self == nullptr || global == nullptr || self->objectIndex == STRUCT_OBJECT_INDEX || self == global) return false;
    if (hldSceneryVars.context != ctx) {
        hldSceneryVars.context = ctx;
        hldSceneryVars.roomPrinting = hldVariableId(ctx, "roomPrinting", true);
        hldSceneryVars.deactivateInstance = hldVariableId(ctx, "deactivateInstance", false);
        hldSceneryVars.editorHidden = hldVariableId(ctx, "editorHidden", false);
        hldSceneryVars.bg = hldVariableId(ctx, "bg", false);
        hldSceneryVars.flr = hldVariableId(ctx, "flr", false);
        hldSceneryVars.fg = hldVariableId(ctx, "fg", false);
        hldSceneryVars.ok = hldSceneryVars.roomPrinting >= 0 && hldSceneryVars.deactivateInstance >= 0 &&
            hldSceneryVars.editorHidden >= 0 && hldSceneryVars.bg >= 0 && hldSceneryVars.flr >= 0 && hldSceneryVars.fg >= 0;
    }
    if (!hldSceneryVars.ok) return false;
    bool truth;
    if (!hldVariableTruth(global, hldSceneryVars.roomPrinting, &truth)) return false;
    if (truth) {
        *result = hldScriptResult(RValue_makeInt32(1));
        return true;
    }
    if (!hldVariableTruth(self, hldSceneryVars.deactivateInstance, &truth)) return false;
    if (!truth && !hldVariableTruth(self, hldSceneryVars.editorHidden, &truth)) return false;
    if (truth) {
        *result = hldScriptResult(RValue_makeInt32(0));
        return true;
    }
    if (!hldVariableTruth(self, hldSceneryVars.bg, &truth)) return false;
    if (!truth && !hldVariableTruth(self, hldSceneryVars.flr, &truth)) return false;
    if (!truth && !hldVariableTruth(self, hldSceneryVars.fg, &truth)) return false;
    if (truth) {
        GMLReal first;
        if (argCount < 1 || args == nullptr || !hldNumber(&args[0], &first)) return false;
        truth = hldCompare(ctx, first, 0) == 0;
    }
    *result = hldScriptResult(RValue_makeInt32(truth ? 0 : 1));
    return true;
}

// ActionIsPaused: global.gamePause == 1 || global.allPause > 0 || SCREEN.roomSelect == 1, read in that order.
static struct { const VMContext* context; bool ok; int32_t gamePause, allPause, roomSelect, screen, showDebugTools; } hldGuardVars;

static bool hldBindGuardVars(VMContext* ctx) {
    if (hldGuardVars.context == ctx) return hldGuardVars.ok;
    hldGuardVars.context = ctx;
    hldGuardVars.gamePause = hldVariableId(ctx, "gamePause", true);
    hldGuardVars.allPause = hldVariableId(ctx, "allPause", true);
    hldGuardVars.roomSelect = hldVariableId(ctx, "roomSelect", false);
    hldGuardVars.showDebugTools = hldVariableId(ctx, "showDebugTools", true);
    hldGuardVars.screen = -1;
    for (uint32_t i = 0; i < ctx->dataWin->objt.count; ++i) {
        if (strcmp(ctx->dataWin->objt.objects[i].name, "SCREEN") == 0) {
            hldGuardVars.screen = (int32_t) i;
            break;
        }
    }
    hldGuardVars.ok = hldGuardVars.gamePause >= 0 && hldGuardVars.allPause >= 0 && hldGuardVars.roomSelect >= 0 &&
        hldGuardVars.showDebugTools >= 0 && hldGuardVars.screen >= 0;
    return hldGuardVars.ok;
}

static inline bool hldVariableNumber(Instance* inst, int32_t varID, GMLReal* out) {
    const RValue value = Instance_getSelfVar(inst, varID);
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *out = RValue_toReal(value);
    return true;
}

static bool hldActionIsPaused(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    (void) args;
    (void) argCount;
    Instance* global = ctx->globalScopeInstance;
    if (global == nullptr || !hldBindGuardVars(ctx)) return false;
    GMLReal number;
    int32_t paused = 1;
    if (!hldVariableNumber(global, hldGuardVars.gamePause, &number)) return false;
    if (hldCompare(ctx, number, 1) != 0) {
        if (!hldVariableNumber(global, hldGuardVars.allPause, &number)) return false;
        if (hldCompare(ctx, number, 0) <= 0) {
            Instance* screen = VM_findInstanceByTarget(ctx, hldGuardVars.screen);
            if (screen == nullptr || !hldVariableNumber(screen, hldGuardVars.roomSelect, &number)) return false;
            paused = hldCompare(ctx, number, 1) == 0;
        }
    }
    *result = hldScriptResult(RValue_makeInt32(paused));
    return true;
}

// onscreen(x, y, margin): the view rectangle grown by the margin, compared in the script's order.
static bool hldOnscreen(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 3 || args == nullptr) return false;
    GMLReal x, y, margin;
    if (!hldNumber(&args[0], &x) || !hldNumber(&args[1], &y) || !hldNumber(&args[2], &margin)) return false;
    GMLReal vx, vy, vw, vh;
    hldViewRect(ctx, &vx, &vy, &vw, &vh);
    const bool inside = hldCompare(ctx, x, vx + vw + margin) < 0 && hldCompare(ctx, x, vx - margin) > 0 &&
        hldCompare(ctx, y, vy - margin) > 0 && hldCompare(ctx, y, vy + vh + margin) < 0;
    *result = hldScriptResult(RValue_makeInt32(inside ? 1 : 0));
    return true;
}

// CollisionLine(x1, y1, x2, y2, obj): collision_line(..., true, true) unless debug drawing is on.
static bool hldCollisionLine(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 5 || args == nullptr || ctx->globalScopeInstance == nullptr || !hldBindGuardVars(ctx)) return false;
    for (int i = 0; i < 5; ++i) if (!hldNumber(&args[i], &(GMLReal){0})) return false;
    bool debug;
    if (!hldVariableTruth(ctx->globalScopeInstance, hldGuardVars.showDebugTools, &debug) || debug) return false;
    static BuiltinFunc collisionLine;
    if (collisionLine == nullptr) collisionLine = VM_findBuiltin(ctx, "collision_line");
    if (collisionLine == nullptr) return false;
    RValue lineArgs[7] = {args[0], args[1], args[2], args[3], args[4], RValue_makeBool(true), RValue_makeBool(true)};
    for (int i = 0; i < 5; ++i) lineArgs[i].ownsReference = false;
    RValue hit = collisionLine(ctx, lineArgs, 7);
    const bool found = RValue_toBool(hit);
    RValue_free(&hit);
    *result = hldScriptResult(RValue_makeInt32(found ? 1 : 0));
    return true;
}

// CaseCheck for linked cases (CASE_LINKCASEREACT with caseArg below -1, CASE_LINKBROKEN with caseArg -999999) in
// states 0 and 2, when the result leaves the state unchanged: the script then only sets usesCaseChecking, may set
// linkArg (from GetLinkArg) and returns 0 (state 0) or 1 (state 2). Everything is computed before any write; a
// state change, the editor, object-index links, a user event 10 handler or unexpected values run the script.
static struct { const VMContext* context; bool ok; int32_t canEdit, selector, state, check, argument, links, usesCase,
                noObjects, touch, linked, broken, linkArg, spawnChild, myArg, spawnedAmount, spawnAmount, collected,
                myBones; } hldCaseVars;

enum { HLD_OBJ_SPAWNER = 46, HLD_OBJ_DRIFTER_BONES = 131, HLD_OBJ_SWITCH_PARENT = 191, HLD_OBJ_FLOOR_BUTTON = 192,
       HLD_OBJ_BUTTON = 135, HLD_OBJ_EMPTY = 66 };

static bool hldObjectIs(VMContext* ctx, int32_t object, int32_t wanted) {
    for (int32_t depth = 0; object != -1 && depth < 64; ++depth) {
        if (object == wanted) return true;
        object = hldObjectParent(ctx, object);
    }
    return false;
}

// instance_exists for a value: an instance id that is active (true), noone or negative (false); an object index
// or a non-number is left to the script (*known = false).
static Instance* hldExistingLink(VMContext* ctx, RValue value, bool* known) {
    *known = true;
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) { *known = false; return nullptr; }
    const GMLReal number = RValue_toReal(value);
    if (number < 0) return nullptr;
    const int32_t id = RValue_toInt32(value);
    if (id < INSTANCE_ID_BASE) { *known = false; return nullptr; }
    Instance* instance = hldLookupInstanceId(ctx->runner, (uint32_t) id);
    return instance != nullptr && instance->active ? instance : nullptr;
}

// GetLinkArg(link) for an existing instance link.
static bool hldLinkArgument(VMContext* ctx, Instance* link, RValue* argument) {
    if (link->objectIndex < 0 || (uint32_t) link->objectIndex >= ctx->dataWin->objt.count) return false;
    *argument = RValue_makeInt32(-999999);
    bool known;
    Instance* trigger = nullptr;
    if (hldObjectIs(ctx, link->objectIndex, HLD_OBJ_SPAWNER)) {
        trigger = hldExistingLink(ctx, Instance_getSelfVar(link, hldCaseVars.spawnChild), &known);
        if (!known) return false;
    }
    RValue source;
    if (trigger != nullptr) {
        if (trigger->objectIndex < 0 || (uint32_t) trigger->objectIndex >= ctx->dataWin->objt.count) return false;
        if (!(trigger->objectIndex == HLD_OBJ_FLOOR_BUTTON || trigger->objectIndex == HLD_OBJ_BUTTON ||
              hldObjectIs(ctx, trigger->objectIndex, HLD_OBJ_SWITCH_PARENT))) return true;
        source = Instance_getSelfVar(trigger, hldCaseVars.myArg);
    } else if (link->objectIndex == HLD_OBJ_EMPTY) {
        source = Instance_getSelfVar(link, hldCaseVars.myArg);
    } else {
        return true;
    }
    if (source.type != RVALUE_REAL && source.type != RVALUE_INT32) return false;
    *argument = source;
    argument->ownsReference = false;
    return true;
}

static bool hldLinkedCaseIdle(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    (void) args;
    Instance* self = hldNativeSelf;
    Instance* global = ctx->globalScopeInstance;
    Runner* runner = ctx->runner;
    if (argCount != 0 || self == nullptr || global == nullptr || runner == nullptr || self == global ||
        self->objectIndex < 0 || (uint32_t) self->objectIndex >= ctx->dataWin->objt.count) return false;
    if (hldCaseVars.context != ctx) {
        hldCaseVars.context = ctx;
        hldCaseVars.canEdit = hldVariableId(ctx, "canEdit", true);
        hldCaseVars.selector = hldVariableId(ctx, "caseScript", false);
        hldCaseVars.state = hldVariableId(ctx, "caseState", false);
        hldCaseVars.check = hldVariableId(ctx, "caseCheck", false);
        hldCaseVars.argument = hldVariableId(ctx, "caseArg", false);
        hldCaseVars.links = hldVariableId(ctx, "linkID", false);
        hldCaseVars.usesCase = hldVariableId(ctx, "usesCaseChecking", false);
        hldCaseVars.noObjects = hldVariableId(ctx, "CASE_NOOBJECTS", true);
        hldCaseVars.touch = hldVariableId(ctx, "CASE_TOUCHOBJECT", true);
        hldCaseVars.linked = hldVariableId(ctx, "CASE_LINKCASEREACT", true);
        hldCaseVars.broken = hldVariableId(ctx, "CASE_LINKBROKEN", true);
        hldCaseVars.linkArg = hldVariableId(ctx, "linkArg", false);
        hldCaseVars.spawnChild = hldVariableId(ctx, "spawnChild", false);
        hldCaseVars.myArg = hldVariableId(ctx, "myArg", false);
        hldCaseVars.spawnedAmount = hldVariableId(ctx, "spawnedAmount", false);
        hldCaseVars.spawnAmount = hldVariableId(ctx, "spawnAmount", false);
        hldCaseVars.collected = hldVariableId(ctx, "collected", false);
        hldCaseVars.myBones = hldVariableId(ctx, "myBones", false);
        const int32_t* ids = &hldCaseVars.canEdit;
        hldCaseVars.ok = true;
        for (int i = 0; i < 18; ++i) hldCaseVars.ok = hldCaseVars.ok && ids[i] >= 0;
    }
    if (!hldCaseVars.ok) return false;
    const GMLReal epsilon = runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    bool editing;
    if (!hldVariableTruth(global, hldCaseVars.canEdit, &editing) || editing) return false;
    GMLReal selector, state, check, argument, empty, touch, linked, broken;
    if (!hldVariableNumber(self, hldCaseVars.selector, &selector) || hldCompare(ctx, selector, 0) == 0) return false;
    if (!hldVariableNumber(self, hldCaseVars.state, &state)) return false;
    const bool on = hldCompare(ctx, state, 2) == 0;
    if (!on && hldCompare(ctx, state, 0) != 0) return false;
    if (on) {
        const int32_t slot = EventSlotMap_lookup(&runner->eventSlotMap, EVENT_OTHER, OTHER_USER0 + 10);
        if (slot >= 0 && ResolvedEventTable_lookup(&runner->eventTable, self->objectIndex, slot, nullptr) >= 0) return false;
    }
    if (!hldVariableNumber(global, hldCaseVars.noObjects, &empty) || !hldVariableNumber(global, hldCaseVars.touch, &touch) ||
        !hldVariableNumber(global, hldCaseVars.linked, &linked) || !hldVariableNumber(global, hldCaseVars.broken, &broken)) return false;
    if (hldCompare(ctx, selector, empty) == 0 || hldCompare(ctx, selector, touch) == 0) return false;
    const bool react = hldCompare(ctx, selector, linked) == 0;
    if (!react && hldCompare(ctx, selector, broken) != 0) return false;
    const RValue links = Instance_getSelfVar(self, hldCaseVars.links);
    if (links.type != RVALUE_ARRAY || links.array == nullptr) return false;
    if (!hldVariableNumber(self, hldCaseVars.argument, &argument) || !hldVariableNumber(self, hldCaseVars.check, &check)) return false;
    const int32_t count = GMLArray_length1D(links.array);
    int32_t returned = 0;
    bool writeArgument = false;
    RValue linkArgument = RValue_makeUndefined();
    bool known;
    if (react) {
        if (hldCompare(ctx, argument, 0) >= 0 || hldCompare(ctx, argument, -1) == 0) return false;
        for (int32_t i = 0; i < count; ++i) {
            Instance* link = hldExistingLink(ctx, GMLArray_get(links.array, i), &known);
            if (!known) return false;
            if (link == nullptr) continue;
            GMLReal linkState;
            if (!hldVariableNumber(link, hldCaseVars.state, &linkState)) return false;
            if (hldCompare(ctx, linkState, 2) != 0) {
                returned = 0;
                break;
            }
            returned = 1;
            if (!hldLinkArgument(ctx, link, &linkArgument)) return false;
            writeArgument = true;
        }
    } else {
        if (hldCompare(ctx, argument, -999999) != 0) return false;
        returned = 1;
        for (int32_t i = 0; i < count; ++i) {
            Instance* link = hldExistingLink(ctx, GMLArray_get(links.array, i), &known);
            if (!known) return false;
            bool exists = false;
            if (link != nullptr) {
                if (link->objectIndex < 0 || (uint32_t) link->objectIndex >= ctx->dataWin->objt.count) return false;
                if (link->objectIndex != HLD_OBJ_SPAWNER) {
                    exists = true;
                } else {
                    GMLReal spawned, amount;
                    if (!hldVariableNumber(link, hldCaseVars.spawnedAmount, &spawned) ||
                        !hldVariableNumber(link, hldCaseVars.spawnAmount, &amount)) return false;
                    exists = hldCompare(ctx, spawned, amount) < 0;
                    if (!exists) {
                        Instance* child = hldExistingLink(ctx, Instance_getSelfVar(link, hldCaseVars.spawnChild), &known);
                        if (!known) return false;
                        exists = child != nullptr;
                    }
                }
                if (hldObjectIs(ctx, link->objectIndex, HLD_OBJ_DRIFTER_BONES)) {
                    bool collected;
                    if (!hldVariableTruth(link, hldCaseVars.collected, &collected)) return false;
                    exists = !collected;
                    if (collected) {
                        Instance* bones = hldExistingLink(ctx, Instance_getSelfVar(link, hldCaseVars.myBones), &known);
                        if (!known) return false;
                        exists = bones != nullptr;
                    }
                }
            }
            if (exists) {
                returned = 0;
                break;
            }
        }
    }
    // State 0 keeps its state when the result differs from caseCheck; state 2 when it matches.
    const bool same = hldCompare(ctx, returned, check) == 0;
    if (on ? !same : same) return false;
    Instance_setSelfVar(self, hldCaseVars.usesCase, RValue_makeReal(1.0));
    if (writeArgument) Instance_setSelfVar(self, hldCaseVars.linkArg, RValue_makeIndependent(linkArgument));
    *result = hldScriptResult(RValue_makeInt32(on ? 1 : 0));
    return true;
}

// OnAnyConsole: os_type is a console, or SCREEN.fakeOS > 0 (read only when it is not).
static int32_t hldFakeOS = INT32_MIN;
static bool hldOnConsole(VMContext* ctx, bool* console) {
    if (ctx->runner == nullptr || !hldBindGuardVars(ctx)) return false;
    const GMLReal os = (GMLReal) ctx->runner->osType;
    static const GMLReal consoles[] = {17, 15, 16, 14, 12};  // os_xbox360, os_xboxone, os_ps3, os_ps4, os_psvita
    for (unsigned i = 0; i < sizeof(consoles) / sizeof(*consoles); ++i) {
        if (hldCompare(ctx, os, consoles[i]) == 0) {
            *console = true;
            return true;
        }
    }
    if (hldFakeOS == INT32_MIN) hldFakeOS = hldVariableId(ctx, "fakeOS", false);
    Instance* screen = VM_findInstanceByTarget(ctx, hldGuardVars.screen);
    GMLReal fake;
    if (hldFakeOS < 0 || screen == nullptr || !hldVariableNumber(screen, hldFakeOS, &fake)) return false;
    *console = hldCompare(ctx, fake, 0) > 0;
    return true;
}

static bool hldOnAnyConsole(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    (void) args;
    (void) argCount;
    bool console;
    if (!hldOnConsole(ctx, &console)) return false;
    *result = hldScriptResult(RValue_makeInt32(console ? 1 : 0));
    return true;
}

// The gamepad wrappers for one device. On a console (os_type or SCREEN.fakeOS) they call the gamepad builtin with
// the GameMaker constant; otherwise they map the constant through the scripts' switch tables to an SDL index.
enum { HLD_PAD_CHECK, HLD_PAD_PRESSED, HLD_PAD_RELEASED, HLD_PAD_AXIS };

static int32_t hldPadIndex(VMContext* ctx, GMLReal control, bool* axis) {
    static const int32_t axes[][2] = {{32775, 4}, {32776, 5}, {32785, 0}, {32786, 1}, {32787, 2}, {32788, 3}};
    static const int32_t buttons[][2] = {{32769, 0}, {32770, 1}, {32771, 2}, {32772, 3}, {32773, 9}, {32774, 10},
                                         {32777, 4}, {32778, 6}, {32779, 7}, {32780, 8}, {32781, 11}, {32782, 12},
                                         {32783, 13}, {32784, 14}};
    for (unsigned i = 0; i < sizeof(axes) / sizeof(*axes); ++i)
        if (hldCompare(ctx, control, axes[i][0]) == 0) { *axis = true; return axes[i][1]; }
    *axis = false;
    for (unsigned i = 0; i < sizeof(buttons) / sizeof(*buttons); ++i)
        if (hldCompare(ctx, control, buttons[i][0]) == 0) return buttons[i][1];
    return -1;
}

static BuiltinFunc hldPadFunction(VMContext* ctx, const char* name) {
    // A small cache by name: the wrappers use nine builtins.
    static struct { const char* name; BuiltinFunc function; } cache[12];
    for (unsigned i = 0; i < sizeof(cache) / sizeof(*cache); ++i) {
        if (cache[i].name == name) return cache[i].function;
        if (cache[i].name == nullptr) {
            cache[i].name = name;
            cache[i].function = VM_findBuiltin(ctx, name);
            return cache[i].function;
        }
    }
    return VM_findBuiltin(ctx, name);
}

static bool hldPadWrapper(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result, int which) {
    if (argCount < 2 || args == nullptr) return false;
    GMLReal device, control;
    if (!hldNumber(&args[0], &device) || !hldNumber(&args[1], &control)) return false;
    bool console;
    if (!hldOnConsole(ctx, &console)) return false;
    RValue callArgs[2] = {args[0], args[1]};
    callArgs[0].ownsReference = callArgs[1].ownsReference = false;
    if (console) {
        if (hldCompare(ctx, device, -1) == 0) return false;
        static const char* const names[] = {"gamepad_button_check", "gamepad_button_check_pressed",
                                            "gamepad_button_check_released", "gamepad_axis_value"};
        const char* name = names[which];
        if (which == HLD_PAD_AXIS && hldCompare(ctx, control, 32775) == 0) name = "gamepad_button_value";
        BuiltinFunc function = hldPadFunction(ctx, name);
        if (function == nullptr) return false;
        *result = hldScriptResult(function(ctx, callArgs, 2));
        return true;
    }
    bool axis;
    const int32_t index = hldPadIndex(ctx, control, &axis);
    if (index < 0) return false;
    callArgs[1] = RValue_makeInt32(index);
    if (which == HLD_PAD_AXIS) {
        if (!axis) return false;
        BuiltinFunc function = hldPadFunction(ctx, "sdl_gamepad_axis_value");
        if (function == nullptr) return false;
        *result = hldScriptResult(function(ctx, callArgs, 2));
        return true;
    }
    if (axis && which == HLD_PAD_CHECK) {
        BuiltinFunc function = hldPadFunction(ctx, "sdl_gamepad_axis_value");
        if (function == nullptr) return false;
        RValue value = function(ctx, callArgs, 2);
        GMLReal number;
        const bool numeric = hldNumber(&value, &number);
        RValue_free(&value);
        if (!numeric) return false;
        *result = hldScriptResult(RValue_makeInt32(hldCompare(ctx, number, 0) != 0 ? 1 : 0));
        return true;
    }
    static const char* const axisNames[] = {nullptr, "sdl_gamepad_axis_check_pressed", "sdl_gamepad_axis_check_released"};
    static const char* const buttonNames[] = {"sdl_gamepad_button_check", "sdl_gamepad_button_check_pressed",
                                              "sdl_gamepad_button_check_released"};
    BuiltinFunc function = hldPadFunction(ctx, axis ? axisNames[which] : buttonNames[which]);
    if (function == nullptr) return false;
    *result = hldScriptResult(function(ctx, callArgs, 2));
    return true;
}

// instance_exists for any number: an active instance id, or an object with an active instance.
static bool hldInstanceExists(VMContext* ctx, RValue value, bool* exists) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    const int32_t id = RValue_toInt32(value);
    *exists = false;
    if (id < 0) return true;
    Runner* runner = ctx->runner;
    if (id >= INSTANCE_ID_BASE) {
        Instance* instance = hldLookupInstanceId(runner, (uint32_t) id);
        *exists = instance != nullptr && instance->active;
        return true;
    }
    if ((uint32_t) id >= ctx->dataWin->objt.count || runner->instancesByObject == nullptr) return false;
    Instance** bucket = runner->instancesByObject[id];
    for (int32_t i = 0; i < (int32_t) arrlen(bucket) && !*exists; ++i) *exists = bucket[i]->active;
    return true;
}

static struct { const VMContext* context; int32_t owner, roomT, xspd, yspd; } hldSmallVars = {.owner = -2};

static void hldBindSmallVars(VMContext* ctx) {
    if (hldSmallVars.context == ctx) return;
    hldSmallVars.context = ctx;
    hldSmallVars.owner = hldVariableId(ctx, "owner", false);
    hldSmallVars.roomT = hldVariableId(ctx, "roomT", true);
    hldSmallVars.xspd = hldVariableId(ctx, "xspd", false);
    hldSmallVars.yspd = hldVariableId(ctx, "yspd", false);
}

// KillWithOwner: with an existing owner it returns 0; otherwise the script destroys the instance.
static bool hldKillWithOwner(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    (void) args;
    (void) argCount;
    Instance* self = hldNativeSelf;
    if (self == nullptr || self->objectIndex == STRUCT_OBJECT_INDEX || ctx->runner == nullptr) return false;
    hldBindSmallVars(ctx);
    if (hldSmallVars.owner < 0) return false;
    bool exists;
    if (!hldInstanceExists(ctx, Instance_getSelfVar(self, hldSmallVars.owner), &exists) || !exists) return false;
    *result = hldScriptResult(RValue_makeInt32(0));
    return true;
}

// StepInit: global.roomT == 5 returns 1; outside the editor anything else returns 0.
static bool hldStepInit(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    (void) args;
    (void) argCount;
    Instance* global = ctx->globalScopeInstance;
    if (global == nullptr || !hldBindGuardVars(ctx)) return false;
    hldBindSmallVars(ctx);
    GMLReal roomT;
    if (hldSmallVars.roomT < 0 || !hldVariableNumber(global, hldSmallVars.roomT, &roomT)) return false;
    if (hldCompare(ctx, roomT, 5) == 0) {
        *result = hldScriptResult(RValue_makeInt32(1));
        return true;
    }
    static int32_t canEdit = INT32_MIN;
    if (canEdit == INT32_MIN) canEdit = hldVariableId(ctx, "canEdit", true);
    bool editing;
    if (canEdit < 0 || !hldVariableTruth(global, canEdit, &editing) || editing) return false;
    *result = hldScriptResult(RValue_makeInt32(0));
    return true;
}

// GetSpd: point_distance(0, 0, xspd, yspd).
static bool hldGetSpd(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    (void) args;
    (void) argCount;
    Instance* self = hldNativeSelf;
    if (self == nullptr || self->objectIndex == STRUCT_OBJECT_INDEX) return false;
    hldBindSmallVars(ctx);
    if (hldSmallVars.xspd < 0 || hldSmallVars.yspd < 0) return false;
    RValue xspd = Instance_getSelfVar(self, hldSmallVars.xspd), yspd = Instance_getSelfVar(self, hldSmallVars.yspd);
    if ((xspd.type != RVALUE_REAL && xspd.type != RVALUE_INT32) || (yspd.type != RVALUE_REAL && yspd.type != RVALUE_INT32)) return false;
    static BuiltinFunc pointDistance;
    if (pointDistance == nullptr) pointDistance = VM_findBuiltin(ctx, "point_distance");
    if (pointDistance == nullptr) return false;
    xspd.ownsReference = yspd.ownsReference = false;
    RValue callArgs[4] = {RValue_makeInt32(0), RValue_makeInt32(0), xspd, yspd};
    *result = hldScriptResult(pointDistance(ctx, callArgs, 4));
    return true;
}

// DirectionBetween(a, b) for two existing instance ids: point_direction between their positions.
static bool hldDirectionBetween(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 2 || args == nullptr || ctx->runner == nullptr) return false;
    Instance* ends[2];
    for (int i = 0; i < 2; ++i) {
        if (args[i].type != RVALUE_REAL && args[i].type != RVALUE_INT32) return false;
        const int32_t id = RValue_toInt32(args[i]);
        if (id < INSTANCE_ID_BASE) return false;
        ends[i] = hldLookupInstanceId(ctx->runner, (uint32_t) id);
        if (ends[i] == nullptr || !ends[i]->active || ends[i]->objectIndex == STRUCT_OBJECT_INDEX) return false;
    }
    static BuiltinFunc pointDirection;
    if (pointDirection == nullptr) pointDirection = VM_findBuiltin(ctx, "point_direction");
    if (pointDirection == nullptr) return false;
    RValue callArgs[4] = {RValue_makeReal((GMLReal) ends[0]->x), RValue_makeReal((GMLReal) ends[0]->y),
                          RValue_makeReal((GMLReal) ends[1]->x), RValue_makeReal((GMLReal) ends[1]->y)};
    *result = hldScriptResult(pointDirection(ctx, callArgs, 4));
    return true;
}

// PointInRegion(x, y, x1, y1, x2, y2): strictly inside the rectangle.
static bool hldPointInRegion(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 6 || args == nullptr) return false;
    GMLReal v[6];
    for (int i = 0; i < 6; ++i) if (!hldNumber(&args[i], &v[i])) return false;
    const bool inside = hldCompare(ctx, v[0], v[2]) > 0 && hldCompare(ctx, v[0], v[4]) < 0 &&
        hldCompare(ctx, v[1], v[3]) > 0 && hldCompare(ctx, v[1], v[5]) < 0;
    *result = hldScriptResult(RValue_makeInt32(inside ? 1 : 0));
    return true;
}

// The VM's numeric + and -: int32 with int32 stays int32 (only without overflow here), otherwise real.
static bool hldArith(const RValue* a, const RValue* b, bool subtract, RValue* out) {
    if ((a->type != RVALUE_REAL && a->type != RVALUE_INT32) || (b->type != RVALUE_REAL && b->type != RVALUE_INT32)) return false;
    if (a->type == RVALUE_INT32 && b->type == RVALUE_INT32) {
        const int64_t value = subtract ? (int64_t) a->int32 - b->int32 : (int64_t) a->int32 + b->int32;
        if (value < INT32_MIN || value > INT32_MAX) return false;
        *out = RValue_makeInt32((int32_t) value);
    } else {
        const GMLReal x = RValue_toReal(*a), y = RValue_toReal(*b);
        *out = RValue_makeReal(subtract ? x - y : x + y);
    }
    out->gmlStackType = GML_TYPE_VARIABLE;
    return true;
}

static BuiltinFunc hldBuiltinNamed(VMContext* ctx, const char* name, BuiltinFunc* cache) {
    if (*cache == nullptr) *cache = VM_findBuiltin(ctx, name);
    return *cache;
}

// HologramModT(t, mod): t + mod.
static bool hldHologramModT(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    (void) ctx;
    if (argCount < 2 || args == nullptr) return false;
    RValue sum;
    if (!hldArith(&args[0], &args[1], false, &sum)) return false;
    *result = hldScriptResult(sum);
    return true;
}

// sin01(a): (sin(a) + 1) / 2.
static bool hldSin01(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 1 || args == nullptr || (args[0].type != RVALUE_REAL && args[0].type != RVALUE_INT32)) return false;
    static BuiltinFunc sine;
    if (hldBuiltinNamed(ctx, "sin", &sine) == nullptr) return false;
    RValue argument = args[0];
    argument.ownsReference = false;
    RValue value = sine(ctx, &argument, 1);
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) { RValue_free(&value); return false; }
    const GMLReal half = (RValue_toReal(value) + 1) / 2;
    RValue_free(&value);
    *result = hldScriptResult(RValue_makeReal(half));
    return true;
}

// dice(n): floor(random(n)), drawing the same random number.
static bool hldDice(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 1 || args == nullptr || (args[0].type != RVALUE_REAL && args[0].type != RVALUE_INT32)) return false;
    static BuiltinFunc randomFunction, floorFunction;
    if (hldBuiltinNamed(ctx, "random", &randomFunction) == nullptr || hldBuiltinNamed(ctx, "floor", &floorFunction) == nullptr) return false;
    RValue argument = args[0];
    argument.ownsReference = false;
    RValue drawn = randomFunction(ctx, &argument, 1);
    RValue floored = floorFunction(ctx, &drawn, 1);
    RValue_free(&drawn);
    *result = hldScriptResult(floored);
    return true;
}

// mirand(n): n - random(n * 2).
static bool hldMirand(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    if (argCount < 1 || args == nullptr || (args[0].type != RVALUE_REAL && args[0].type != RVALUE_INT32)) return false;
    static BuiltinFunc randomFunction;
    if (hldBuiltinNamed(ctx, "random", &randomFunction) == nullptr) return false;
    RValue doubled;
    if (args[0].type == RVALUE_INT32) {
        const int64_t value = (int64_t) args[0].int32 * 2;
        if (value < INT32_MIN || value > INT32_MAX) return false;
        doubled = RValue_makeInt32((int32_t) value);
    } else {
        doubled = RValue_makeReal(args[0].real * 2);
    }
    doubled.gmlStackType = GML_TYPE_VARIABLE;
    RValue drawn = randomFunction(ctx, &doubled, 1);
    RValue difference;
    const bool ok = hldArith(&args[0], &drawn, true, &difference);
    RValue_free(&drawn);
    if (!ok) return false;  // random returned a non-number; unreachable for a numeric argument
    *result = hldScriptResult(difference);
    return true;
}

// OnGround(): z <= floor height (floorZ for the player and direct enemy children, else 0) and on a SafePlatform.
static bool hldOnGround(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result) {
    (void) args;
    Instance* self = hldNativeSelf;
    if (argCount > 2 || self == nullptr || self->objectIndex == STRUCT_OBJECT_INDEX || ctx->runner == nullptr) return false;
    static int32_t zVar = INT32_MIN, floorVar = INT32_MIN;
    if (zVar == INT32_MIN) {
        zVar = hldVariableId(ctx, "z", false);
        floorVar = hldVariableId(ctx, "floorZ", false);
    }
    if (zVar < 0 || floorVar < 0) return false;
    GMLReal floorZ = 0, z;
    if (self->objectIndex == 419 || hldObjectParent(ctx, self->objectIndex) == 17) {
        if (!hldVariableNumber(self, floorVar, &floorZ)) return false;
    }
    if (!hldVariableNumber(self, zVar, &z)) return false;
    int32_t grounded = 0;
    if (hldCompare(ctx, z, floorZ) <= 0) {
        static BuiltinFunc positionMeeting;
        if (hldBuiltinNamed(ctx, "position_meeting", &positionMeeting) == nullptr) return false;
        RValue meetingArgs[3] = {RValue_makeReal((GMLReal) self->x), RValue_makeReal((GMLReal) self->y), RValue_makeInt32(408)};
        RValue hit = positionMeeting(ctx, meetingArgs, 3);
        grounded = RValue_toBool(hit) ? 1 : 0;
        RValue_free(&hit);
    }
    *result = hldScriptResult(RValue_makeInt32(grounded));
    return true;
}

static bool hldNativeCompute(VMContext* ctx, int kind, const RValue* args, int32_t argCount, RValue* result) {
    switch (kind) {
        case HLD_NATIVE_APPROACH: return hldApproach(ctx, args, argCount, result);
        case HLD_NATIVE_DISTANCE_BETWEEN: return hldDistanceBetween(ctx, args, argCount, result);
        case HLD_NATIVE_IS_ANY: return hldIsAny(ctx, args, argCount, result);
        case HLD_NATIVE_IS_TYPE: return hldIsType(ctx, args, argCount, result);
        case HLD_NATIVE_ONSCREEN_WID: return hldOnscreenWid(ctx, args, argCount, result);
        case HLD_NATIVE_SCENERY_IS_VISIBLE: return hldSceneryIsVisible(ctx, args, argCount, result);
        case HLD_NATIVE_ACTION_IS_PAUSED: return hldActionIsPaused(ctx, args, argCount, result);
        case HLD_NATIVE_ONSCREEN: return hldOnscreen(ctx, args, argCount, result);
        case HLD_NATIVE_COLLISION_LINE: return hldCollisionLine(ctx, args, argCount, result);
        case HLD_NATIVE_CASE_CHECK: return hldLinkedCaseIdle(ctx, args, argCount, result);
        case HLD_NATIVE_ON_ANY_CONSOLE: return hldOnAnyConsole(ctx, args, argCount, result);
        case HLD_NATIVE_PAD_CHECK: return hldPadWrapper(ctx, args, argCount, result, HLD_PAD_CHECK);
        case HLD_NATIVE_PAD_PRESSED: return hldPadWrapper(ctx, args, argCount, result, HLD_PAD_PRESSED);
        case HLD_NATIVE_PAD_RELEASED: return hldPadWrapper(ctx, args, argCount, result, HLD_PAD_RELEASED);
        case HLD_NATIVE_PAD_AXIS: return hldPadWrapper(ctx, args, argCount, result, HLD_PAD_AXIS);
        case HLD_NATIVE_KILL_WITH_OWNER: return hldKillWithOwner(ctx, args, argCount, result);
        case HLD_NATIVE_STEP_INIT: return hldStepInit(ctx, args, argCount, result);
        case HLD_NATIVE_GET_SPD: return hldGetSpd(ctx, args, argCount, result);
        case HLD_NATIVE_DIRECTION_BETWEEN: return hldDirectionBetween(ctx, args, argCount, result);
        case HLD_NATIVE_POINT_IN_REGION: return hldPointInRegion(ctx, args, argCount, result);
        case HLD_NATIVE_HOLOGRAM_MOD_T: return hldHologramModT(ctx, args, argCount, result);
        case HLD_NATIVE_SIN01: return hldSin01(ctx, args, argCount, result);
        case HLD_NATIVE_ON_GROUND: return hldOnGround(ctx, args, argCount, result);
#ifndef HLD_CHECK_NATIVE_SCRIPTS
        // These draw random numbers, so a checked rerun would draw twice; the direct build's state verifies them.
        case HLD_NATIVE_DICE: return hldDice(ctx, args, argCount, result);
        case HLD_NATIVE_MIRAND: return hldMirand(ctx, args, argCount, result);
#endif
#ifndef HLD_CHECK_NATIVE_SCRIPTS
        // Uploads uniforms, so it is verified by the direct build's pixels instead of a checked rerun.
        case HLD_NATIVE_PALETTE_SWAP: return hldPaletteSwap(ctx, args, argCount, result);
#endif
        default: return false;
    }
}

#ifdef HLD_CHECK_NATIVE_SCRIPTS
enum { HLD_NATIVE_DEPTH = 64 };
static struct { int32_t codeIndex; int kind; RValue expected; } hldNativePending[HLD_NATIVE_DEPTH];
static int hldNativePendingCount;

static void hldNativeExpect(VMContext* ctx, int32_t codeIndex, const RValue* args, int32_t argCount) {
    const int kind = hldNativeKind(ctx, codeIndex);
    if (kind == HLD_NATIVE_NONE || hldNativePendingCount >= HLD_NATIVE_DEPTH) return;
    RValue expected;
    if (!hldNativeCompute(ctx, kind, args, argCount, &expected)) {
        if (++hldNative.fallbacks[kind] <= 3) {
            logInfo("HLD: native %s fallback argc=%d types %d %d %d %d\n", hldNativeNames[kind], argCount,
                    argCount > 0 ? args[0].type : -1, argCount > 1 ? args[1].type : -1,
                    argCount > 2 ? args[2].type : -1, argCount > 3 ? args[3].type : -1);
        }
        return;
    }
    hldNativePending[hldNativePendingCount++] = (typeof(hldNativePending[0])){codeIndex, kind, expected};
}

static void hldNativeVerify(VMContext* ctx, int32_t codeIndex, RValue result) {
    if (hldNativePendingCount == 0 || hldNativePending[hldNativePendingCount - 1].codeIndex != codeIndex) return;
    const int kind = hldNativePending[hldNativePendingCount - 1].kind;
    const RValue expected = hldNativePending[--hldNativePendingCount].expected;
    bool same = result.type == expected.type && result.gmlStackType == expected.gmlStackType &&
        result.ownsReference == expected.ownsReference && result.assetRefType == expected.assetRefType;
    if (same && result.type == RVALUE_REAL) same = memcmp(&result.real, &expected.real, sizeof(GMLReal)) == 0;
#ifndef NO_RVALUE_INT64
    else if (same && result.type == RVALUE_INT64) same = result.int64 == expected.int64;
#endif
    else if (same) same = result.int32 == expected.int32;
    if (!same) {
        logError("Native %s differs: type %d/%d stack %d/%d owns %d/%d asset %d/%d value %.17g/%.17g\n",
                 hldNativeNames[kind], result.type, expected.type, result.gmlStackType, expected.gmlStackType,
                 result.ownsReference, expected.ownsReference, result.assetRefType, expected.assetRefType,
                 (double) RValue_toReal(result), (double) RValue_toReal(expected));
        abort();
    }
    ++hldNative.calls[kind];
    if (++hldNative.checked % 20000 == 0) {
        logInfo("HLD: checked %u native script calls:", hldNative.checked);
        for (int i = 1; i < HLD_NATIVE_COUNT; ++i) logInfo(" %s %u/%u", hldNativeNames[i] + 11, hldNative.calls[i], hldNative.fallbacks[i]);
        logInfo("\n");
    }
}
#else
static bool hldNativeScript(VMContext* ctx, int32_t codeIndex, const RValue* args, int32_t argCount, RValue* result) {
    const int kind = hldNativeKind(ctx, codeIndex);
    if (kind == HLD_NATIVE_NONE) return false;
    if (!hldNativeCompute(ctx, kind, args, argCount, result)) {
        ++hldNative.fallbacks[kind];
        return false;
    }
    if (++hldNative.calls[kind] == 1000) logInfo("HLD: ran 1000 native %s calls\n", hldNativeNames[kind]);
    return true;
}
#endif
'''


DIRECT_REGISTRATION = r'''
static bool hldNativeDirect(VMContext* ctx, int32_t codeIndex, Instance* self, const RValue* args, int32_t argc, RValue* result) {
    hldNativeSelf = self;
    return hldNativeScript(ctx, codeIndex, args, argc, result);
}
__attribute__((constructor)) static void hldNativeRegister(void) { hldcNativeCall = hldNativeDirect; }
'''


def with_native_scripts(code, checked=False):
    if checked:
        declarations = ("static Instance* hldNativeSelf; static void hldNativeExpect(VMContext*, int32_t, const RValue*, int32_t); "
                        "static void hldNativeVerify(VMContext*, int32_t, RValue); ")
        hook = " hldNativeSelf = selfInstance; hldNativeExpect(ctx, codeIndex, args, argCount);"
    else:
        declarations = "static Instance* hldNativeSelf; static bool hldNativeScript(VMContext*, int32_t, const RValue*, int32_t, RValue*); "
        hook = (" { RValue hldNativeResult; hldNativeSelf = selfInstance; if (hldNativeScript(ctx, codeIndex, args, argCount, &hldNativeResult)) {"
                " ctx->savearefBalance = 0; ctx->bytecodeBase = codeBytes(ctx, code); return hldNativeResult; } }")
    code = replace_once(code, SIGNATURE, declarations + SIGNATURE)
    start = code.index(SIGNATURE)
    anchor = "    CallFrame callFrame = {0};"
    position = code.index(anchor, start)
    # Directly after the anchor, so a native script also wins over a translated entry hooked on this line.
    end = position + len(anchor)
    code = code[:end] + hook + code[end:]
    if checked:
        # A translated entry returns from the hook on this line; verify its result too.
        translated = ("return hldcFn(ctx, selfInstance, otherInstance, args, argCount, eventObjectIndex, eventType, "
                      "eventSubtype); }")
        if translated in code:
            code = replace_once(code, translated, "{ RValue hldNativeR = hldcFn(ctx, selfInstance, otherInstance, args, "
                                "argCount, eventObjectIndex, eventType, eventSubtype); hldNativeVerify(ctx, codeIndex, "
                                "hldNativeR); return hldNativeR; } }")
        anchor = "    result = RValue_stealOwnershipOrCopy(result);"
        position = code.index(anchor, start)
        end = code.index("\n", position)
        code = code[:end] + " hldNativeVerify(ctx, codeIndex, result);" + code[end:]
    # Translated code (appended earlier) asks hldcHasShortcut whether to call a script directly; a native
    # script must be reached through VM_executeCode, so the check cannot wait for the define below.
    guarded = "#ifdef HLD_NATIVE_SCRIPTS_HOOKED\n    if (hldNativeKind(ctx, codeIndex) != 0) return true;\n#endif\n"
    if guarded in code:
        code = replace_once(code, guarded, "    if (hldNativeKind(ctx, codeIndex) != 0) return true;\n")
        code = replace_once(code, "bool hldcHasShortcut(VMContext* ctx, int32_t codeIndex) {",
                            "static inline int hldNativeKind(VMContext* ctx, int32_t codeIndex);\n"
                            "bool hldcHasShortcut(VMContext* ctx, int32_t codeIndex) {")
    prefix = "#define HLD_CHECK_NATIVE_SCRIPTS\n" if checked else ""
    if not checked and "bool (*hldcNativeCall)(" in code:
        # Translated callers reach native scripts without entering VM_executeCode.
        return code + "\n" + NATIVE + DIRECT_REGISTRATION
    return code + "\n" + prefix + NATIVE
