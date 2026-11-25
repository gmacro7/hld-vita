"""Translate decompiled GML (gml_parse) into C that runs on the Butterscotch VM's own semantics.

Each code entry becomes `RValue hldc_<n>(ctx, self, other, args, argc, eventObject, eventType, eventSubtype)`. It
pushes a real CallFrame (self, other, event fields, independent copies of the arguments) so builtins and the VM's
variable paths see what they would under the interpreter. Variables go through the VM's resolvers
(resolveVariableReadWithAccess and an access-taking copy of resolveVariableWrite); locals are C variables;
operators reproduce the VM's arithmetic (add/sub/mul inline, the rest through the VM's own handlers); `with`
drives the VM's environment stack; scripts are called through VM_executeCode, which dispatches to translated code.

The generated source is appended to vm.c (`with_translated_gml`), so the VM's static helpers are in scope.
"""

import json
import os
import re
from pathlib import Path

from gml_parse import parse

CONSTANTS = {
    "true": 1, "false": 0, "pi": "pi", "all": -3, "noone": -4,
    "c_white": 16777215, "c_black": 0, "c_red": 255, "c_fuchsia": 16711935, "c_gray": 8421504, "c_lime": 65280,
    "c_aqua": 16776960, "c_dkgray": 4210752, "c_ltgray": 12632256, "c_orange": 4235519, "c_blue": 16711680,
    "c_yellow": 65535, "c_purple": 8388736, "c_maroon": 128, "c_olive": 32896, "c_green": 32768, "c_navy": 8388608,
    "c_teal": 8421376, "c_silver": 12632256,
    "ev_create": 0, "ev_destroy": 1, "ev_alarm": 2, "ev_step": 3, "ev_collision": 4, "ev_keyboard": 5,
    "ev_mouse": 6, "ev_other": 7, "ev_draw": 8, "ev_keypress": 9, "ev_keyrelease": 10,
    "ev_draw_normal": 0, "ev_step_normal": 0, "ev_step_begin": 1, "ev_step_end": 2,
    "mb_left": 1, "mb_right": 2, "mb_middle": 3, "mb_none": 0, "mb_any": -1,
    "fa_left": 0, "fa_center": 1, "fa_right": 2, "fa_top": 0, "fa_middle": 1, "fa_bottom": 2,
    "vk_nokey": 0, "vk_anykey": 1, "vk_backspace": 8, "vk_tab": 9, "vk_enter": 13, "vk_shift": 16,
    "vk_control": 17, "vk_alt": 18, "vk_escape": 27, "vk_space": 32, "vk_left": 37, "vk_up": 38,
    "vk_right": 39, "vk_down": 40, "vk_delete": 46,
    **{f"vk_f{i}": 111 + i for i in range(1, 13)},
    "bm_normal": 0, "bm_add": 1, "bm_max": 2, "bm_subtract": 3, "bm_zero": 1, "bm_one": 2, "bm_src_color": 3,
    "bm_inv_src_color": 4, "bm_src_alpha": 5, "bm_inv_src_alpha": 6,
    "ds_type_map": 1, "ds_type_list": 2, "ds_type_stack": 3, "ds_type_queue": 4,
    "os_windows": 0, "os_macosx": 1, "os_ios": 3, "os_android": 4, "os_linux": 6, "os_psvita": 12, "os_ps4": 14,
    "os_xboxone": 15, "os_ps3": 16, "os_unknown": -1, "os_symbian": 5, "os_winphone": 7, "os_tizen": 8,
    "os_win8native": 9, "os_wiiu": 10, "os_3ds": 11, "os_bb10": 13, "os_xbox360": 17, "os_uwp": 18,
    "buffer_fixed": 0, "buffer_grow": 1, "buffer_wrap": 2, "buffer_fast": 3, "buffer_seek_start": 0,
    "cr_none": -1, "cr_default": 0, "audio_falloff_linear_distance": 3, "gp_start": 32778,
    "pr_pointlist": 1, "pr_linelist": 2, "pr_linestrip": 3, "pr_trianglelist": 4, "pr_trianglestrip": 5,
}

# Opcodes and comparison functions (butterscotch op.h / cmpop.h).
OPCODES = {"/": "OP_DIV", "%": "OP_MOD", "div": "OP_REM", "&": "OP_AND", "|": "OP_OR", "^": "OP_XOR",
           "<<": "OP_SHL", ">>": "OP_SHR"}
COMPARISONS = {"<": "CMPOP_LESS_THAN", "<=": "CMPOP_LESS_THAN_OR_EQUAL", "==": "CMPOP_EQUAL",
               "!=": "CMPOP_NOT_EQUAL", ">=": "CMPOP_GREATER_THAN_OR_EQUAL", ">": "CMPOP_GREATER_THAN"}
ARGUMENT = re.compile(r"argument(\d+)$")
# GameMaker 1.x built-in arrays: a bare name compiles to element 0, which the decompiler prints without "[0]".
BUILTIN_ARRAYS = {"background_index", "background_visible", "view_hport", "view_hview", "view_surface_id",
                  "view_visible", "view_wport", "view_wview", "view_xview", "view_yview", "view_xport", "view_yport",
                  "view_angle", "view_object", "view_hborder", "view_vborder", "view_hspeed", "view_vspeed", "alarm"}

SHARED = r'''
#include "vm.h"
#include "vm_builtins.h"
#include "instance.h"
#include "runner.h"
#include "opword.h"
#include "op.h"
#include "cmpop.h"
#include "math_compat.h"
#include "stb_ds.h"
#include <stdlib.h>
#include <string.h>

#ifdef HLDC_STATS
enum { HLDC_STAT_READ_SLOW, HLDC_STAT_WRITE_SLOW, HLDC_STAT_COMPARE_SLOW, HLDC_STAT_SCRIPT_VM, HLDC_STAT_SCRIPT_DIRECT,
       HLDC_STAT_BUILTIN, HLDC_STAT_VM_READ, HLDC_STAT_VM_WRITE, HLDC_STAT_WITH, HLDC_STAT_ARITH_SLOW, HLDC_STAT_SHAPE_MISS, HLDC_STAT_FAST, HLDC_STAT_SHADOW_CALL, HLDC_STAT_SHADOW_SKIP, HLDC_STAT_SHADOW_INVALID, HLDC_STAT_COUNT };
extern unsigned long long hldcStat[HLDC_STAT_COUNT];
#define HLDC_STAT(i) (++hldcStat[i])
void hldcStatFunction(VMContext* ctx, uint32_t codeIndex);
#define HLDC_STAT_FUNCTION(ctx, code) hldcStatFunction(ctx, code)
uint64_t nowNanos(void);
extern uint64_t* hldcTime;
void hldcStatBuiltin(void* fn, const char* name, uint64_t nanos);
extern uint64_t* hldcSelf; extern uint64_t hldcChildNanos;
#define HLDC_STAT_START() const uint64_t hldcStart = nowNanos(); const uint64_t hldcOuterChild = hldcChildNanos; hldcChildNanos = 0
#define HLDC_STAT_STOP(code) do { const uint64_t hldcSpent = nowNanos() - hldcStart; if (hldcTime != NULL) { hldcTime[code] += hldcSpent; hldcSelf[code] += hldcSpent - hldcChildNanos; } hldcChildNanos = hldcOuterChild + hldcSpent; } while (0)
void hldcStatName(VMContext* ctx, uint32_t ref, int kind);
#define HLDC_STAT_NAME(ctx, ref, kind) hldcStatName(ctx, ref, kind)
#else
#define HLDC_STAT(i) ((void) 0)
#define HLDC_STAT_FUNCTION(ctx, code) ((void) 0)
#define HLDC_STAT_START() ((void) 0)
#define HLDC_STAT_STOP(code) ((void) 0)
#define HLDC_STAT_NAME(ctx, ref, kind) ((void) 0)
#endif

// Matches vm.c's VM_INLINE_FRAME_VALUES (checked there): frames with more arguments keep them on the heap.
enum { HLDC_INLINE_ARGS = 8 };

// Per-site cache for a self variable: the shape seen last and the variable's slot in it (-1: absent).
typedef struct { uint32_t ref; int32_t varID; int16_t builtin; const IntRValueShape* shape; int32_t slot; const IntRValueShape* shape2; int32_t slot2; } HldcSite;
// A site that alternates between two layouts (an event shared by several object types) keeps the other one too.
static inline void hldcSiteShape(HldcSite* site, const IntRValueShape* shape) {
    if (shape == site->shape2) { site->shape2 = site->shape; const int32_t slot = site->slot2; site->slot2 = site->slot; site->shape = shape; site->slot = slot; return; }
    site->shape2 = site->shape; site->slot2 = site->slot;
    site->shape = shape; site->slot = IntRValueShape_slotOf(shape, site->varID);
}
void hldcSiteResolve(VMContext* ctx, HldcSite* site, const char* name, bool global);
void hldcSetBuiltin(VMContext* ctx, Instance* inst, HldcSite* site, RValue val);
RValue hldcGetBuiltin(VMContext* ctx, Instance* inst, HldcSite* site, int32_t index);
RValue hldcReadSlow(VMContext* ctx, Instance* inst, HldcSite* site);
void hldcWriteSlow(VMContext* ctx, HldcSite* site, RValue val);
RValue hldcAddSlow(VMContext* ctx, RValue a, RValue b);
RValue hldcSubSlow(VMContext* ctx, RValue a, RValue b);
RValue hldcMulSlow(VMContext* ctx, RValue a, RValue b);
bool hldcCompareSlow(VMContext* ctx, uint8_t comparison, RValue a, RValue b);

static inline bool hldcNumeric(RValue v) { return v.type == RVALUE_INT32 || v.type == RVALUE_REAL; }
static inline GMLReal hldcNumber(RValue v) { return v.type == RVALUE_INT32 ? (GMLReal) v.int32 : v.real; }

RValue hldcRead(VMContext* ctx, int32_t scope, uint32_t ref, int32_t index, bool computed);
void hldcWrite(VMContext* ctx, int32_t scope, uint32_t ref, int32_t index, bool computed, RValue val);

// Instances whose variables live in selfVars: objects, and the global scope (which is marked as a struct).
static inline bool hldcPlainInstance(VMContext* ctx, Instance* inst) {
    return inst != NULL && (inst->objectIndex != STRUCT_OBJECT_INDEX || inst == ctx->globalScopeInstance);
}

static __attribute__((noinline)) RValue hldcReadOnFull(VMContext* ctx, Instance* inst, HldcSite* site, const char* name, bool global) {
    if (site->ref == UINT32_MAX) hldcSiteResolve(ctx, site, name, global);
    if (site->varID == VARIABLE_BUILTIN && inst != NULL && inst->objectIndex != STRUCT_OBJECT_INDEX) {
        // VMBuiltins_getVariable's answers for the hottest instance built-ins.
        switch (site->builtin) {
            case BUILTIN_VAR_X: return RValue_makeReal(inst->x);
            case BUILTIN_VAR_Y: return RValue_makeReal(inst->y);
            case BUILTIN_VAR_OBJECT_INDEX: return RValue_makeReal((GMLReal) inst->objectIndex);
            case BUILTIN_VAR_ID: return RValue_makeReal((GMLReal) inst->instanceId);
            case BUILTIN_VAR_SPRITE_INDEX: return RValue_makeReal((GMLReal) inst->spriteIndex);
            case BUILTIN_VAR_IMAGE_INDEX: return RValue_makeReal(inst->imageIndex);
            default: break;
        }
    }
    if (site->varID >= 0 && hldcPlainInstance(ctx, inst)) {
        HLDC_STAT(HLDC_STAT_FAST);
        if (inst->selfVars.shape != site->shape) {
            HLDC_STAT(HLDC_STAT_SHAPE_MISS);
            hldcSiteShape(site, inst->selfVars.shape);
        }
        if (site->slot < 0) return RValue_makeUndefined();
        RValue value = inst->selfVars.entries[site->slot].value;
        value.ownsReference = false;
        return value;
    }
    return hldcReadSlow(ctx, inst, site);
}

// Instance_setSelfVar when the variable already exists in the cached shape; consumes val.
static __attribute__((noinline)) bool hldcWriteOnFull(VMContext* ctx, Instance* inst, HldcSite* site, const char* name, bool global, RValue* val) {
    if (site->ref == UINT32_MAX) hldcSiteResolve(ctx, site, name, global);
    if (site->varID == VARIABLE_BUILTIN && inst != NULL && inst->objectIndex != STRUCT_OBJECT_INDEX) {
        // writeSingleInstanceVariable's built-in path: the engine setter, then the caller's copy is freed.
        hldcSetBuiltin(ctx, inst, site, *val);
        RValue_free(val);
        return true;
    }
    if (site->varID >= 0 && hldcPlainInstance(ctx, inst)) {
        HLDC_STAT(HLDC_STAT_FAST);
        if (inst->selfVars.shape != site->shape) {
            HLDC_STAT(HLDC_STAT_SHAPE_MISS);
            hldcSiteShape(site, inst->selfVars.shape);
        }
        if (site->slot >= 0) {
            RValue* slot = &inst->selfVars.entries[site->slot].value;
            RValue copy = RValue_makeIndependent(*val);
            RValue_free(slot);
            *slot = copy;
            if (val->type == RVALUE_INT32 && val->assetRefType == ASSET_TYPE_INSTANCE) inst->mayHaveInstanceReferences = true;
            RValue_free(val);
            return true;
        }
    }
    return false;
}

// Inline front ends: a site that already resolved a plain variable on an instance with this exact variable layout
// (shape) reads/writes that slot directly, exactly as the full functions' slot path does; everything else goes to
// the full (out-of-line) versions. HLD has no structs other than the global scope, which the full path accepts too.
static inline __attribute__((always_inline)) RValue hldcReadOn(VMContext* ctx, Instance* inst, HldcSite* site, const char* name, bool global) {
    if (__builtin_expect(inst != NULL && site->shape != NULL && inst->selfVars.shape == site->shape && site->slot >= 0, 1)) {
        HLDC_STAT(HLDC_STAT_FAST);
        RValue value = inst->selfVars.entries[site->slot].value;
        value.ownsReference = false;
        return value;
    }
    return hldcReadOnFull(ctx, inst, site, name, global);
}
static inline __attribute__((always_inline)) bool hldcWriteOn(VMContext* ctx, Instance* inst, HldcSite* site, const char* name, bool global, RValue* val) {
    if (__builtin_expect(inst != NULL && site->shape != NULL && inst->selfVars.shape == site->shape && site->slot >= 0, 1)) {
        HLDC_STAT(HLDC_STAT_FAST);
        RValue* slot = &inst->selfVars.entries[site->slot].value;
        RValue copy = RValue_makeIndependent(*val);
        RValue_free(slot);
        *slot = copy;
        if (val->type == RVALUE_INT32 && val->assetRefType == ASSET_TYPE_INSTANCE) inst->mayHaveInstanceReferences = true;
        RValue_free(val);
        return true;
    }
    return hldcWriteOnFull(ctx, inst, site, name, global, val);
}

void hldcArraySet(VMContext* ctx, RValue* slot, int32_t index, RValue val);
// name[index] on self or global: built-in arrays through the engine getter, variables through the cached slot.
static inline RValue hldcReadIndexOn(VMContext* ctx, Instance* inst, int32_t scope, HldcSite* site, const char* name, int32_t index) {
    if (site->ref == UINT32_MAX) hldcSiteResolve(ctx, site, name, scope == INSTANCE_GLOBAL);
    if (site->varID == VARIABLE_BUILTIN && inst != NULL && inst->objectIndex != STRUCT_OBJECT_INDEX)
        return hldcGetBuiltin(ctx, inst, site, index);
    if (site->varID >= 0 && hldcPlainInstance(ctx, inst)) {
        HLDC_STAT(HLDC_STAT_FAST);
        if (inst->selfVars.shape != site->shape) {
            HLDC_STAT(HLDC_STAT_SHAPE_MISS);
            hldcSiteShape(site, inst->selfVars.shape);
        }
        if (site->slot < 0) return RValue_makeUndefined();
        return GMLArray_getOnArrayRef(&inst->selfVars.entries[site->slot].value, index);
    }
    return hldcRead(ctx, scope, site->ref, index, false);
}

static inline void hldcWriteIndexOn(VMContext* ctx, Instance* inst, int32_t scope, HldcSite* site, const char* name, int32_t index, RValue val) {
    if (site->ref == UINT32_MAX) hldcSiteResolve(ctx, site, name, scope == INSTANCE_GLOBAL);
    if (site->varID >= 0 && hldcPlainInstance(ctx, inst) && index >= 0) {
        HLDC_STAT(HLDC_STAT_FAST);
        if (inst->selfVars.shape != site->shape) {
            HLDC_STAT(HLDC_STAT_SHAPE_MISS);
            hldcSiteShape(site, inst->selfVars.shape);
        }
        if (site->slot >= 0) {
            hldcArraySet(ctx, &inst->selfVars.entries[site->slot].value, index, val);
            return;
        }
    }
    hldcWrite(ctx, scope, site->ref, index, false, val);
}

static inline RValue hldcReadSelf(VMContext* ctx, HldcSite* site, const char* name) {
    return hldcReadOn(ctx, (Instance*) ctx->currentCallFrame->self, site, name, false);
}

static inline void hldcWriteSelf(VMContext* ctx, HldcSite* site, const char* name, RValue val) {
    if (!hldcWriteOn(ctx, (Instance*) ctx->currentCallFrame->self, site, name, false, &val))
        hldcWrite(ctx, INSTANCE_SELF, site->ref, INT32_MIN, false, val);
}

// global.name and other.name: the VM's targets for INSTANCE_GLOBAL and INSTANCE_OTHER (other falls back to self).
static inline Instance* hldcScopeInstance(VMContext* ctx, int32_t scope) {
    if (scope == INSTANCE_GLOBAL) return ctx->globalScopeInstance;
    if (scope == INSTANCE_OTHER && ctx->currentCallFrame->other != NULL) return (Instance*) ctx->currentCallFrame->other;
    return (Instance*) ctx->currentCallFrame->self;
}

static inline RValue hldcReadScope(VMContext* ctx, int32_t scope, HldcSite* site, const char* name) {
    return hldcReadOn(ctx, hldcScopeInstance(ctx, scope), site, name, scope == INSTANCE_GLOBAL);
}

static inline void hldcWriteScope(VMContext* ctx, int32_t scope, HldcSite* site, const char* name, RValue val) {
    if (!hldcWriteOn(ctx, hldcScopeInstance(ctx, scope), site, name, scope == INSTANCE_GLOBAL, &val))
        hldcWrite(ctx, scope, site->ref, INT32_MIN, false, val);
}

// target.name for a computed target: an instance id takes the cached path, anything else the VM's resolver.
Instance* hldcTargetInstance(VMContext* ctx, int32_t target);
static inline RValue hldcReadTarget(VMContext* ctx, int32_t target, HldcSite* site, const char* name) {
    Instance* inst = hldcTargetInstance(ctx, target);
    if (inst != NULL) return hldcReadOn(ctx, inst, site, name, false);
    if (site->ref == UINT32_MAX) hldcSiteResolve(ctx, site, name, false);
    return hldcRead(ctx, target, site->ref, INT32_MIN, true);
}

static inline void hldcWriteTarget(VMContext* ctx, int32_t target, HldcSite* site, const char* name, RValue val) {
    Instance* inst = target >= INSTANCE_ID_BASE ? hldcTargetInstance(ctx, target) : NULL;
    if (inst != NULL && hldcWriteOn(ctx, inst, site, name, false, &val)) return;
    if (site->ref == UINT32_MAX) hldcSiteResolve(ctx, site, name, false);
    hldcWrite(ctx, target, site->ref, INT32_MIN, true, val);
}

typedef RValue (*HldcFunction)(VMContext*, Instance*, Instance*, RValue*, int32_t, int32_t, int32_t, int32_t);
typedef struct { BuiltinFunc builtin; int32_t script; bool resolved, checkedDirect; HldcFunction direct; } HldcCallee;
bool hldcHasShortcut(VMContext* ctx, int32_t codeIndex);
HldcFunction hldcTranslated(VMContext* ctx, int32_t codeIndex);
void hldcSetLocalIndex(VMContext* ctx, RValue* local, int32_t index, RValue value);
// VM_arraySetWithCoW on a variable's slot; consumes val.
void hldcArraySet(VMContext* ctx, RValue* slot, int32_t index, RValue val);
RValue hldcFallthrough(VMContext* ctx);
uint32_t hldcVarRef(VMContext* ctx, const char* name, bool global);
RValue hldcRead(VMContext* ctx, int32_t scope, uint32_t ref, int32_t index, bool computed);
void hldcWrite(VMContext* ctx, int32_t scope, uint32_t ref, int32_t index, bool computed, RValue val);
int32_t hldcCodeIndex(VMContext* ctx, const char* name);
static inline RValue hldcAdd(VMContext* ctx, RValue a, RValue b) {
    if (hldcNumeric(a) && hldcNumeric(b)) {
        RValue result = a.type == RVALUE_INT32 && b.type == RVALUE_INT32 ? RValue_makeInt32(a.int32 + b.int32) :
            RValue_makeReal(hldcNumber(a) + hldcNumber(b));
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
    }
    return hldcAddSlow(ctx, a, b);
}
static inline RValue hldcSub(VMContext* ctx, RValue a, RValue b) {
    if (hldcNumeric(a) && hldcNumeric(b)) {
        RValue result = a.type == RVALUE_INT32 && b.type == RVALUE_INT32 ? RValue_makeInt32(a.int32 - b.int32) :
            RValue_makeReal(hldcNumber(a) - hldcNumber(b));
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
    }
    return hldcSubSlow(ctx, a, b);
}
static inline RValue hldcMul(VMContext* ctx, RValue a, RValue b) {
    if (hldcNumeric(a) && hldcNumeric(b)) {
        RValue result = a.type == RVALUE_INT32 && b.type == RVALUE_INT32 ? RValue_makeInt32(a.int32 * b.int32) :
            RValue_makeReal(hldcNumber(a) * hldcNumber(b));
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
    }
    return hldcMulSlow(ctx, a, b);
}
RValue hldcBinaryVM(VMContext* ctx, uint8_t opcode, RValue a, RValue b);
bool hldcEnemyShadowMisses(VMContext* ctx);
bool hldcFlameVentSkip(VMContext* ctx);
// handleDiv and handleMod for numbers (the translated opword carries no integer types, so / never traps).
// The quotient is computed out of line: under -ffast-math a constant divisor would become a reciprocal multiply.
GMLReal hldcQuotient(GMLReal a, GMLReal b);
static inline RValue hldcDiv(VMContext* ctx, RValue a, RValue b) {
    if (hldcNumeric(a) && hldcNumeric(b)) {
        RValue result = RValue_makeReal(hldcQuotient(hldcNumber(a), hldcNumber(b)));
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
    }
    return hldcBinaryVM(ctx, OP_DIV, a, b);
}
static inline RValue hldcMod(VMContext* ctx, RValue a, RValue b) {
    if (hldcNumeric(a) && hldcNumeric(b) && hldcNumber(b) != 0.0) {
        RValue result = RValue_makeReal(GMLReal_fmod(hldcNumber(a), hldcNumber(b)));
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
    }
    return hldcBinaryVM(ctx, OP_MOD, a, b);
}
static inline bool hldcCompare(VMContext* ctx, uint8_t comparison, RValue a, RValue b) {
    if (hldcNumeric(a) && hldcNumeric(b)) {
        const GMLReal diff = hldcNumber(a) - hldcNumber(b);
        const GMLReal epsilon = ctx->runner != NULL ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
        const int cmp = GMLReal_fabs(diff) <= epsilon ? 0 : (diff < 0 ? -1 : 1);
        switch (comparison) {
            case CMPOP_LESS_THAN: return cmp < 0;
            case CMPOP_LESS_THAN_OR_EQUAL: return cmp <= 0;
            case CMPOP_EQUAL: return cmp == 0;
            case CMPOP_NOT_EQUAL: return cmp != 0;
            case CMPOP_GREATER_THAN_OR_EQUAL: return cmp >= 0;
            case CMPOP_GREATER_THAN: return cmp > 0;
        }
    }
    return hldcCompareSlow(ctx, comparison, a, b);
}
RValue hldcNeg(VMContext* ctx, RValue a);
RValue hldcBitNot(VMContext* ctx, RValue a);
void hldcResolve(VMContext* ctx, HldcCallee* callee, const char* name);
RValue hldcCall(VMContext* ctx, HldcCallee* callee, const char* name, RValue* args, int32_t argc);
bool hldcWithBegin(VMContext* ctx, RValue targetValue);
void hldcWithEnd(VMContext* ctx);
bool hldcWithNext(VMContext* ctx);
int32_t hldcAssetIndex(VMContext* ctx, const char* name);

// ===[ Translated GML runtime ]===

typedef RValue (*HldcFunction)(VMContext*, Instance*, Instance*, RValue*, int32_t, int32_t, int32_t, int32_t);

#define HLDC_REF(slot, name, global) (slot != UINT32_MAX ? slot : (slot = hldcVarRef(ctx, name, global)))

static inline int32_t hldcIndex(RValue value) {
    const int32_t index = RValue_toInt32(value);
    RValue_free(&value);
    return index;
}

static inline int32_t hldcIndex2(RValue first, RValue second) {
    return hldcIndex(first) * 32000 + hldcIndex(second);
}

static inline RValue hldcReal(GMLReal value) { return RValue_makeReal(value); }

static inline RValue hldcInt(int32_t value) { return RValue_makeInt32(value); }

static inline RValue hldcString(const char* text) { return RValue_makeString(text); }

static inline bool hldcTruthy(RValue value) {
    const bool truth = RValue_toBool(value);
    RValue_free(&value);
    return truth;
}

static inline void hldcSetLocal(RValue* local, RValue value) {
    RValue copy = RValue_makeIndependent(value);
    RValue_free(&value);
    RValue_free(local);
    *local = copy;
}

static inline RValue hldcWeak(RValue value) {
    value.ownsReference = false;
    return value;
}

static inline int32_t hldcIndexOf(RValue value, int dimensions) {
    (void) dimensions;
    return hldcIndex(value);
}

#define HLDC_CODE(name) ({ static int32_t hldcCodeSlot = INT32_MIN; \
    if (hldcCodeSlot == INT32_MIN) hldcCodeSlot = hldcCodeIndex(ctx, name); hldcCodeSlot; })

static inline RValue hldcLocalIndex(RValue* local, int32_t index) {
    return GMLArray_getOnArrayRef(local, index);
}



static inline RValue hldcArgument(VMContext* ctx, int32_t index) {
    const CallFrame* frame = ctx->currentCallFrame;
    if (frame->scriptArgs == nullptr || index < 0 || index >= frame->scriptArgsCount) return RValue_makeUndefined();
    RValue value = frame->scriptArgs[index];
    value.ownsReference = false;
    return value;
}

static inline RValue hldcArgumentIndex(VMContext* ctx, int32_t argument, int32_t index) {
    RValue value = hldcArgument(ctx, argument);
    if (value.type != RVALUE_ARRAY || value.array == nullptr) return RValue_makeUndefined();
    return GMLArray_getOnArrayRef(&value, index);
}

static inline void hldcSetArgumentIndex(VMContext* ctx, int32_t argument, int32_t index, RValue value) {
    VM_writeToScriptArgsArrayElement(ctx, argument, index, value);
    RValue_free(&value);
}

static inline void hldcSetArgument(VMContext* ctx, int32_t index, RValue value) {
    VM_writeToScriptArgs(ctx, index, value);
    RValue_free(&value);
}

#define HLDC_OPWORD(opcode, extra) ((OpWord){(uint32_t)(opcode) << 24 | (uint32_t) GML_TYPE_VARIABLE << 20 | \
                                             (uint32_t) GML_TYPE_VARIABLE << 16 | (uint32_t)(extra)})

static inline RValue hldcBool(bool value) {
    RValue result = RValue_makeBool(value);
    return result;
}



#define HLDC_ENTER(codeIndexValue) \
    CallFrame hldcFrame = {0}; \
    hldcFrame.parent = ctx->currentCallFrame; \
    hldcFrame.codeIndex = (uint32_t)(codeIndexValue); \
    hldcFrame.initialSelf = hldcFrame.self = self; \
    hldcFrame.initialOther = hldcFrame.other = other; \
    hldcFrame.eventObjectIndex = eventObject; \
    hldcFrame.eventType = eventType; \
    hldcFrame.eventSubtype = eventSubtype; \
    RValue hldcArgs[HLDC_INLINE_ARGS]; \
    if (argc > 0 && args != nullptr) { \
        hldcFrame.scriptArgsOnHeap = argc > HLDC_INLINE_ARGS; \
        hldcFrame.scriptArgs = hldcFrame.scriptArgsOnHeap ? (RValue*) safeMalloc((size_t) argc * sizeof(RValue)) : hldcArgs; \
        for (int32_t hldcI = 0; hldcI < argc; ++hldcI) hldcFrame.scriptArgs[hldcI] = RValue_makeIndependent(args[hldcI]); \
        hldcFrame.scriptArgsCount = argc; \
    } \
    ctx->currentCallFrame = &hldcFrame; \
    HLDC_STAT_FUNCTION(ctx, hldcFrame.codeIndex); \
    HLDC_STAT_START(); \
    const int32_t hldcEnvDepth = ctx->envDepth; \
    RValue hldcResult = hldcFallthrough(ctx);

#define HLDC_LEAVE() \
    while (ctx->envDepth > hldcEnvDepth) hldcWithEnd(ctx); \
    for (int32_t hldcI = 0; hldcI < hldcFrame.scriptArgsCount; ++hldcI) RValue_free(&hldcFrame.scriptArgs[hldcI]); \
    if (hldcFrame.scriptArgsOnHeap) free(hldcFrame.scriptArgs); \
    ctx->currentCallFrame = hldcFrame.parent; \
    HLDC_STAT_STOP(hldcFrame.codeIndex); \
    ctx->savearefBalance = 0;

static inline RValue hldcReturnValue(RValue value) {
    RValue result = RValue_makeIndependent(value);
    RValue_free(&value);
    result.gmlStackType = result.type == RVALUE_STRING ? GML_TYPE_STRING : GML_TYPE_VARIABLE;
    return result;
}

typedef struct { int32_t kind, index; const char* name; } HldcAsset;

#define HLDC_ASSET(slot, name) hldcInt(slot != INT32_MIN ? slot : (slot = hldcAssetIndex(ctx, name)))
'''

RUNTIME = r'''
#ifdef HLDC_STATS
unsigned long long hldcStat[HLDC_STAT_COUNT];
static uint32_t* hldcCalls;
uint64_t* hldcTime;
uint64_t* hldcSelf;
uint64_t hldcChildNanos;
static uint32_t* hldcNames[5];
// Builtin calls from translated code by function: count and time.
enum { HLDC_BUILTIN_SLOTS = 1024 };
static struct { void* fn; const char* name; uint32_t calls; uint64_t nanos, selfNanos; } hldcBuiltins[HLDC_BUILTIN_SLOTS];
static uint64_t hldcBuiltinChild;
void hldcStatBuiltin(void* fn, const char* name, uint64_t nanos) {
    uint32_t i = (uint32_t)(((uintptr_t) fn >> 4) * 2654435761u) & (HLDC_BUILTIN_SLOTS - 1);
    while (hldcBuiltins[i].fn != NULL && hldcBuiltins[i].fn != fn) i = (i + 1) & (HLDC_BUILTIN_SLOTS - 1);
    hldcBuiltins[i].fn = fn;
    hldcBuiltins[i].name = name;
    ++hldcBuiltins[i].calls;
    hldcBuiltins[i].nanos += nanos;
    hldcBuiltins[i].selfNanos += nanos - hldcBuiltinChild;
}

// Slow variable accesses by variable: 0 slow self read, 1 VM read, 2 VM array read, 3 VM write, 4 VM array write.
void hldcStatName(VMContext* ctx, uint32_t ref, int kind) {
    const uint32_t count = ctx->dataWin->vari.variableCount;
    if (hldcNames[kind] == nullptr) hldcNames[kind] = safeCalloc(count, sizeof(uint32_t));
    const uint32_t index = ref & 0x07FFFFFF;
    if (index < count) ++hldcNames[kind][index];
}
static int hldcStatFrame = -1;

// Per 300 frames: slow-path counters and the most called translated entries, as per-frame averages.
uint32_t* hldcVmCalls;
void hldcStatFunction(VMContext* ctx, uint32_t codeIndex) {
    if (hldcCalls == nullptr) hldcCalls = safeCalloc(ctx->dataWin->code.count, sizeof(*hldcCalls));
    if (hldcTime == nullptr) { hldcTime = safeCalloc(ctx->dataWin->code.count, sizeof(*hldcTime)); hldcSelf = safeCalloc(ctx->dataWin->code.count, sizeof(*hldcSelf)); }
    if (codeIndex < ctx->dataWin->code.count) ++hldcCalls[codeIndex];
    {
        // Spike report: when a frame (first translated call to the next frame's) takes over 60 ms, log its
        // costliest translated functions (inclusive time within that frame).
        static int spikeFrame = -1;
        static uint64_t spikeStart;
        static uint64_t* spikeSnapshot;
        const int nowFrame = ctx->runner != nullptr ? ctx->runner->frameCount : 0;
        if (nowFrame != spikeFrame) {
            const uint64_t now = nowNanos();
            if (spikeSnapshot == nullptr) spikeSnapshot = safeCalloc(ctx->dataWin->code.count, sizeof(*spikeSnapshot));
            else if (spikeFrame >= 0 && now - spikeStart > 60000000ull) {
                logInfo("HLDCSPIKE frame=%d ms=%.1f", spikeFrame, (now - spikeStart) * 1e-6);
                for (int rank = 0; rank < 10; ++rank) {
                    uint32_t best = UINT32_MAX; uint64_t bestDelta = 0;
                    for (uint32_t i = 0; i < ctx->dataWin->code.count; ++i) {
                        const uint64_t delta = hldcTime[i] - spikeSnapshot[i];
                        if (delta > bestDelta) { bestDelta = delta; best = i; }
                    }
                    if (best == UINT32_MAX || bestDelta < 1000000ull) break;
                    logInfo(" %s=%.1f", ctx->dataWin->code.entries[best].name, bestDelta * 1e-6);
                    spikeSnapshot[best] = hldcTime[best];
                }
                logInfo("\n");
            }
            memcpy(spikeSnapshot, hldcTime, ctx->dataWin->code.count * sizeof(*spikeSnapshot));
            spikeFrame = nowFrame;
            spikeStart = nowNanos();  // after any report above, so logging is not charged to the next frame
        }
    }
    {
        // HLDC_TRACE=ENTRY:var1,var2,... logs the caller's object and those self variables on frames 600-601.
        static int traceIndex = -2;
        static int32_t traceVars[8];
        static int traceVarCount;
        if (traceIndex == -2) {
            traceIndex = -1;
            const char* spec = getenv("HLDC_TRACE");
            if (spec != NULL) {
                char buffer[512];
                snprintf(buffer, sizeof(buffer), "%s", spec);
                char* vars = strchr(buffer, ':');
                if (vars != NULL) *vars++ = '\0';
                traceIndex = hldcCodeIndex(ctx, buffer);
                for (char* name = vars ? strtok(vars, ",") : NULL; name != NULL && traceVarCount < 8; name = strtok(NULL, ","))
                    traceVars[traceVarCount++] = ctx->dataWin->vari.variables[hldcVarRef(ctx, name, false) & 0x07FFFFFF].varID;
            }
        }
        const int traceFrame = ctx->runner != nullptr ? ctx->runner->frameCount : 0;
        if ((int) codeIndex == traceIndex && traceFrame >= 600 && traceFrame <= 601) {
            Instance* self = (Instance*) ctx->currentCallFrame->self;
            logInfo("HLDCTRACE frame=%d obj=%s", traceFrame, self != NULL && self->objectIndex >= 0 &&
                    (uint32_t) self->objectIndex < ctx->dataWin->objt.count ? ctx->dataWin->objt.objects[self->objectIndex].name : "?");
            for (int i = 0; i < traceVarCount && self != NULL; ++i) {
                const RValue value = Instance_getSelfVar(self, traceVars[i]);
                logInfo(" %g", (double) (value.type == RVALUE_REAL || value.type == RVALUE_INT32 ? RValue_toReal(value) : -12345));
            }
            logInfo("\n");
        }
    }
    const int frame = ctx->runner != nullptr ? ctx->runner->frameCount : 0;
    if (hldcStatFrame < 0) hldcStatFrame = frame;
    if (frame - hldcStatFrame < 300) return;
    {
        // HLDC_HOT_DUMP=path appends every entry called in this window with its calls per frame.
        const char* dump = getenv("HLDC_HOT_DUMP");
        FILE* out = dump != NULL ? fopen(dump, "a") : NULL;
        if (out != NULL) {
            for (uint32_t i = 0; i < ctx->dataWin->code.count; ++i)
                if (hldcCalls[i] != 0) fprintf(out, "%s %s %.3f\n", ctx->runner->currentRoom->name,
                                               ctx->dataWin->code.entries[i].name, hldcCalls[i] / (double) (frame - hldcStatFrame));
            fclose(out);
        }
    }
    static const char* const names[] = {"read_slow", "write_slow", "compare_slow", "script_vm", "script_direct",
                                        "builtin", "vm_read", "vm_write", "with", "arith_slow", "shape_miss", "fast",
                                        "shadow_call", "shadow_skip", "shadow_invalid"};
    const double frames = frame - hldcStatFrame;
    logInfo("HLDCSTAT frame=%d room=%s", frame, ctx->runner->currentRoom->name);
    for (int i = 0; i < HLDC_STAT_COUNT; ++i) logInfo(" %s=%.0f", names[i], hldcStat[i] / frames);
    logInfo("\\n");
    for (int rank = 0; rank < 30; ++rank) {
        uint32_t best = 0, count = 0;
        for (uint32_t i = 0; i < ctx->dataWin->code.count; ++i) if (hldcCalls[i] > count) { count = hldcCalls[i]; best = i; }
        if (count == 0) break;
        logInfo("HLDCSTAT calls=%.1f %s\\n", count / frames, ctx->dataWin->code.entries[best].name);
        hldcCalls[best] = 0;
    }
    {
        extern uint32_t* hldcVmCalls;
        for (int rank = 0; hldcVmCalls != nullptr && rank < 15; ++rank) {
            uint32_t best = 0, count = 0;
            for (uint32_t i = 0; i < ctx->dataWin->code.count; ++i) if (hldcVmCalls[i] > count) { count = hldcVmCalls[i]; best = i; }
            if (count == 0) break;
            logInfo("HLDCVMCALL calls=%.1f shortcut=%d %s\n", count / frames, (int) hldcHasShortcut(ctx, (int32_t) best),
                    ctx->dataWin->code.entries[best].name);
            hldcVmCalls[best] = 0;
        }
    }
    static const char* const kinds[] = {"read_slow", "vm_read", "vm_array_read", "vm_write", "vm_array_write"};
    for (int kind = 0; kind < 5; ++kind) {
        if (hldcNames[kind] == nullptr) continue;
        for (int rank = 0; rank < 12; ++rank) {
            uint32_t best = 0, count = 0;
            for (uint32_t i = 0; i < ctx->dataWin->vari.variableCount; ++i) if (hldcNames[kind][i] > count) { count = hldcNames[kind][i]; best = i; }
            if (count == 0) break;
            const Variable* variable = &ctx->dataWin->vari.variables[best];
            logInfo("HLDCVAR %s %.1f %s type=%d id=%d\n", kinds[kind], count / frames, variable->name, variable->instanceType, variable->varID);
            hldcNames[kind][best] = 0;
        }
        memset(hldcNames[kind], 0, ctx->dataWin->vari.variableCount * sizeof(uint32_t));
    }
    memset(hldcStat, 0, sizeof(hldcStat));
    for (int rank = 0; rank < 120; ++rank) {
        uint32_t best = 0;
        uint64_t most = 0;
        for (uint32_t i = 0; i < ctx->dataWin->code.count; ++i) if (hldcTime[i] > most) { most = hldcTime[i]; best = i; }
        if (most == 0) break;
        logInfo("HLDCTIME ms=%.4f %s\n", most * 1e-6 / frames, ctx->dataWin->code.entries[best].name);
        hldcTime[best] = 0;
    }
    memset(hldcTime, 0, ctx->dataWin->code.count * sizeof(*hldcTime));
    for (int rank = 0; rank < 60; ++rank) {
        uint32_t best = 0;
        uint64_t most = 0;
        for (uint32_t i = 0; i < ctx->dataWin->code.count; ++i) if (hldcSelf[i] > most) { most = hldcSelf[i]; best = i; }
        if (most == 0) break;
        logInfo("HLDCSELF ms=%.4f %s\n", most * 1e-6 / frames, ctx->dataWin->code.entries[best].name);
        hldcSelf[best] = 0;
    }
    memset(hldcSelf, 0, ctx->dataWin->code.count * sizeof(*hldcSelf));
    { int order[HLDC_BUILTIN_SLOTS]; int n = 0; for (int i = 0; i < HLDC_BUILTIN_SLOTS; ++i) if (hldcBuiltins[i].selfNanos) order[n++] = i;
      for (int rank = 0; rank < 25 && rank < n; ++rank) { int best = rank; for (int j = rank + 1; j < n; ++j) if (hldcBuiltins[order[j]].selfNanos > hldcBuiltins[order[best]].selfNanos) best = j;
        int t = order[rank]; order[rank] = order[best]; order[best] = t;
        logInfo("HLDCBSELF ms=%.4f %s\n", hldcBuiltins[order[rank]].selfNanos * 1e-6 / frames, hldcBuiltins[order[rank]].name); }
      for (int i = 0; i < HLDC_BUILTIN_SLOTS; ++i) hldcBuiltins[i].selfNanos = 0; }
    for (int rank = 0; rank < 30; ++rank) {
        int best = -1;
        for (int i = 0; i < HLDC_BUILTIN_SLOTS; ++i)
            if (hldcBuiltins[i].nanos != 0 && (best < 0 || hldcBuiltins[i].nanos > hldcBuiltins[best].nanos)) best = i;
        if (best < 0) break;
        logInfo("HLDCBUILTIN ms=%.4f calls=%.1f %s\n", hldcBuiltins[best].nanos * 1e-6 / frames,
                hldcBuiltins[best].calls / frames, hldcBuiltins[best].name);
        hldcBuiltins[best].nanos = 0;
    }
    for (int i = 0; i < HLDC_BUILTIN_SLOTS; ++i) hldcBuiltins[i].calls = 0, hldcBuiltins[i].nanos = 0;
    memset(hldcCalls, 0, ctx->dataWin->code.count * sizeof(*hldcCalls));
    hldcStatFrame = frame;
}
#endif
void hldcSiteResolve(VMContext* ctx, HldcSite* site, const char* name, bool global) {
    site->ref = hldcVarRef(ctx, name, global);
    const Variable* variable = resolveVarDef(ctx, site->ref);
    site->varID = variable->varID;
    site->builtin = variable->builtinVarId;
    site->shape = NULL;
    site->slot = -1;
}

// Instance for a computed target, as the VM's read path resolves it: an instance id or an object's first
// active instance. Others (self/other/global numbers, noone) go through the VM.
Instance* hldcTargetInstance(VMContext* ctx, int32_t target) {
    if (target < 0) return nullptr;
    return VM_findInstanceByTarget(ctx, target);
}

void hldcSetBuiltin(VMContext* ctx, Instance* inst, HldcSite* site, RValue val) {
    VMBuiltins_setVariable(ctx, inst, site->builtin, resolveVarDef(ctx, site->ref)->name, val, -1);
}

RValue hldcGetBuiltin(VMContext* ctx, Instance* inst, HldcSite* site, int32_t index) {
    return VMBuiltins_getVariable(ctx, inst, site->builtin, resolveVarDef(ctx, site->ref)->name, index);
}

RValue hldcReadSlow(VMContext* ctx, Instance* self, HldcSite* site) {
    HLDC_STAT(HLDC_STAT_READ_SLOW);
    HLDC_STAT_NAME(ctx, site->ref, 0);
    if (site->varID == VARIABLE_BUILTIN && self != nullptr && self->objectIndex != STRUCT_OBJECT_INDEX) {
        switch (site->builtin) {
            case BUILTIN_VAR_X: return RValue_makeReal(self->x);
            case BUILTIN_VAR_Y: return RValue_makeReal(self->y);
            case BUILTIN_VAR_IMAGE_INDEX: return RValue_makeReal(self->imageIndex);
            case BUILTIN_VAR_IMAGE_XSCALE: return RValue_makeReal(self->imageXscale);
            case BUILTIN_VAR_IMAGE_YSCALE: return RValue_makeReal(self->imageYscale);
            case BUILTIN_VAR_IMAGE_ANGLE: return RValue_makeReal(self->imageAngle);
            case BUILTIN_VAR_IMAGE_ALPHA: return RValue_makeReal(self->imageAlpha);
            case BUILTIN_VAR_SPRITE_INDEX: return RValue_makeReal((GMLReal) self->spriteIndex);
            case BUILTIN_VAR_VISIBLE: return RValue_makeBool(self->visible);
            case BUILTIN_VAR_DEPTH: return RValue_makeReal((GMLReal) self->depth);
            case BUILTIN_VAR_ID: return RValue_makeReal((GMLReal) self->instanceId);
            case BUILTIN_VAR_OBJECT_INDEX: return RValue_makeReal((GMLReal) self->objectIndex);
            // The same values VMBuiltins_getVariable returns for these instance built-ins.
            case BUILTIN_VAR_IMAGE_BLEND: return RValue_makeReal((GMLReal) self->imageBlend);
            case BUILTIN_VAR_MASK_INDEX: return RValue_makeReal((GMLReal) self->maskIndex);
            case BUILTIN_VAR_IMAGE_SINGLE:
                return RValue_makeReal(self->imageSpeed == 0.0 ? (GMLReal) self->imageIndex : -1.0);
            case BUILTIN_VAR_IMAGE_NUMBER:
                return RValue_makeReal(self->spriteIndex >= 0
                    ? (GMLReal) ctx->runner->dataWin->sprt.sprites[self->spriteIndex].textureCount : 0.0);
            case BUILTIN_VAR_ROOM: return RValue_makeReal((GMLReal) ctx->runner->currentRoomIndex);
            default: break;
        }
    }
    if (self == (Instance*) ctx->currentCallFrame->self) return hldcRead(ctx, INSTANCE_SELF, site->ref, INT32_MIN, false);
    if (self == ctx->globalScopeInstance) return hldcRead(ctx, INSTANCE_GLOBAL, site->ref, INT32_MIN, false);
    if (self != nullptr) return VMBuiltins_getVariable(ctx, self, site->builtin, resolveVarDef(ctx, site->ref)->name, -1);
    return RValue_makeReal(0.0);
}

void hldcWriteSlow(VMContext* ctx, HldcSite* site, RValue val) {
    hldcWrite(ctx, INSTANCE_SELF, site->ref, INT32_MIN, false, val);
}
// A script without a return statement returns what the interpreter returns at the end of the bytecode.
void hldcArraySet(VMContext* ctx, RValue* slot, int32_t index, RValue val) {
    VM_arraySetWithCoW(ctx, slot, index, val);
    RValue_free(&val);
}

RValue hldcFallthrough(VMContext* ctx) {
    return scriptFallthroughReturnValue(ctx);
}
_Static_assert(HLDC_INLINE_ARGS == VM_INLINE_FRAME_VALUES, "translated frames must match the VM's inline arguments");
void hldcSetLocalIndex(VMContext* ctx, RValue* local, int32_t index, RValue value) {
    VM_arraySetWithCoW(ctx, local, index, value);
    RValue_free(&value);
}
// The VARI entry for a name (global scope prefers instanceType -5), as a VARTYPE_NORMAL reference.

uint32_t hldcVarRef(VMContext* ctx, const char* name, bool global) {
    int32_t fallback = -1;
    for (uint32_t i = 0; i < ctx->dataWin->vari.variableCount; ++i) {
        const Variable* variable = &ctx->dataWin->vari.variables[i];
        if (strcmp(variable->name, name) != 0 || variable->instanceType == INSTANCE_LOCAL) continue;
        if ((variable->instanceType == INSTANCE_GLOBAL) == global) return i | (uint32_t) VARTYPE_NORMAL << 24;
        if (fallback < 0) fallback = (int32_t) i;
    }
    if (fallback < 0) {
        logError("HLDC: no variable %s\n", name);
        abort();
    }
    return (uint32_t) fallback | (uint32_t) VARTYPE_NORMAL << 24;
}

RValue hldcRead(VMContext* ctx, int32_t scope, uint32_t ref, int32_t index, bool computed) {
    HLDC_STAT(HLDC_STAT_VM_READ);
    HLDC_STAT_NAME(ctx, ref, index != INT32_MIN ? 2 : 1);
    ArrayAccess access = {.arrayIndex = index, .instanceType = scope, .isArray = index != INT32_MIN,
                          .hasInstanceType = computed || index != INT32_MIN};
    if (!access.isArray) access.arrayIndex = -1;
    return resolveVariableReadWithAccess(ctx, scope, ref, access);
}

// Writes consume val.

void hldcWrite(VMContext* ctx, int32_t scope, uint32_t ref, int32_t index, bool computed, RValue val) {
    HLDC_STAT(HLDC_STAT_VM_WRITE);
    HLDC_STAT_NAME(ctx, ref, index != INT32_MIN ? 4 : 3);
    ArrayAccess access = {.arrayIndex = index, .instanceType = scope, .isArray = index != INT32_MIN,
                          .hasInstanceType = computed || index != INT32_MIN};
    if (!access.isArray) access.arrayIndex = -1;
    resolveVariableWriteAccess(ctx, scope, ref, val, access);
}

// Local assignment: the local owns an independent copy; consumes value.

int32_t hldcCodeIndex(VMContext* ctx, const char* name) {
    const ptrdiff_t slot = shgeti(ctx->codeIndexByName, (char*) name);
    return slot < 0 ? -1 : ctx->codeIndexByName[slot].value;
}

// The VM's numeric fast paths, then its slow paths, for + - *.

RValue hldcAddSlow(VMContext* ctx, RValue a, RValue b) {
    HLDC_STAT(HLDC_STAT_ARITH_SLOW);
    if ((a.type == RVALUE_INT32 || a.type == RVALUE_REAL) && (b.type == RVALUE_INT32 || b.type == RVALUE_REAL)) {
        RValue result = a.type == RVALUE_INT32 && b.type == RVALUE_INT32 ? RValue_makeInt32(a.int32 + b.int32) :
            RValue_makeReal((a.type == RVALUE_INT32 ? (GMLReal) a.int32 : a.real) + (b.type == RVALUE_INT32 ? (GMLReal) b.int32 : b.real));
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
    }
    if (a.type == RVALUE_STRING || b.type == RVALUE_STRING) {
        handleAddString(ctx, a, b, GML_TYPE_VARIABLE);
        return stackPop(ctx);
    }
#ifndef NO_RVALUE_INT64
    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) return RValue_makeInt64(a.int64 + b.int64);
#endif
    const GMLReal result = RValue_toReal(a) + RValue_toReal(b);
    RValue_free(&a);
    RValue_free(&b);
    return RValue_makeReal(result);
}

RValue hldcSubSlow(VMContext* ctx, RValue a, RValue b) {
    HLDC_STAT(HLDC_STAT_ARITH_SLOW);
    (void) ctx;
    if ((a.type == RVALUE_INT32 || a.type == RVALUE_REAL) && (b.type == RVALUE_INT32 || b.type == RVALUE_REAL)) {
        RValue result = a.type == RVALUE_INT32 && b.type == RVALUE_INT32 ? RValue_makeInt32(a.int32 - b.int32) :
            RValue_makeReal((a.type == RVALUE_INT32 ? (GMLReal) a.int32 : a.real) - (b.type == RVALUE_INT32 ? (GMLReal) b.int32 : b.real));
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
    }
#ifndef NO_RVALUE_INT64
    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) return RValue_makeInt64(a.int64 - b.int64);
#endif
    const GMLReal result = RValue_toReal(a) - RValue_toReal(b);
    RValue_free(&a);
    RValue_free(&b);
    return RValue_makeReal(result);
}

RValue hldcMulSlow(VMContext* ctx, RValue a, RValue b) {
    HLDC_STAT(HLDC_STAT_ARITH_SLOW);
    if ((a.type == RVALUE_INT32 || a.type == RVALUE_REAL) && (b.type == RVALUE_INT32 || b.type == RVALUE_REAL)) {
        RValue result = a.type == RVALUE_INT32 && b.type == RVALUE_INT32 ? RValue_makeInt32(a.int32 * b.int32) :
            RValue_makeReal((a.type == RVALUE_INT32 ? (GMLReal) a.int32 : a.real) * (b.type == RVALUE_INT32 ? (GMLReal) b.int32 : b.real));
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
    }
    if (a.type == RVALUE_STRING || b.type == RVALUE_STRING) {
        handleMulString(ctx, a, b, GML_TYPE_VARIABLE);
        return stackPop(ctx);
    }
#ifndef NO_RVALUE_INT64
    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) return RValue_makeInt64(a.int64 * b.int64);
#endif
    const GMLReal result = RValue_toReal(a) * RValue_toReal(b);
    RValue_free(&a);
    RValue_free(&b);
    return RValue_makeReal(result);
}

__attribute__((noinline)) GMLReal hldcQuotient(GMLReal a, GMLReal b) { return a / b; }

// Codex's Flame Vent guard: when it holds, the probe loop is skipped and obj takes its final value, as there.
bool hldcFlameVentSkip(VMContext* ctx) {
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (!canSkipFlameVentLoop(ctx)) return false;
    Instance_setSelfVar(ctx->currentCallFrame->self, flameVentGuard.object, RValue_makeReal(437.0));
    return true;
#else
    return false;
#endif
}

// For the platform groups whose boxes hold a probe point, run the loop's own position_meeting probes (no side
// effects). If none hits, the GML loop would only count zeros, so it can be skipped; any hit runs the loop (Log).
static bool hldcShadowGroupsMiss(VMContext* ctx, unsigned groups) {
    if (groups == 0) return true;
    if (getenv("HLD_NO_SHADOW_PROBES") != NULL) return false;
    static BuiltinFunc meet, ldx, ldy;
    if (meet == NULL) { meet = VM_findBuiltin(ctx, "position_meeting"); ldx = VM_findBuiltin(ctx, "lengthdir_x"); ldy = VM_findBuiltin(ctx, "lengthdir_y"); }
    Instance* self = ctx->currentCallFrame->self;
    if (meet == NULL || ldx == NULL || ldy == NULL || self == NULL || ctx->globalScopeInstance == NULL) return false;
    const RValue platforms = Instance_getSelfVar(ctx->globalScopeInstance, enemyShadowPlatforms);
    if (platforms.type != RVALUE_ARRAY || platforms.array == NULL) return false;
    for (unsigned g = 0; g < 32 && (groups >> g) != 0; ++g) {
        if ((groups & (1u << g)) == 0) continue;
        if ((int32_t) g >= GMLArray_length1D(platforms.array)) return false;
        const RValue* item = GMLArray_slot(platforms.array, (int32_t) g);
        if (item == NULL || (item->type != RVALUE_INT32 && item->type != RVALUE_REAL)) return false;
        for (int dir = 0; dir <= 270; dir += 90) {
            RValue a[2] = {RValue_makeReal(8.0), RValue_makeInt32(dir)}, b[2] = {RValue_makeReal(4.0), RValue_makeInt32(dir)};
            const RValue dx = ldx(ctx, a, 2), dy = ldy(ctx, b, 2);
            RValue m[3] = {RValue_makeReal((GMLReal) self->x + RValue_toReal(dx)), RValue_makeReal((GMLReal) self->y + RValue_toReal(dy)), *item};
            if (RValue_toBool(meet(ctx, m, 3))) return false;
        }
    }
    return true;
}

// Codex's grouped enemy shadow probe: true when no probe of any non-floor platform would hit.
bool hldcEnemyShadowMisses(VMContext* ctx) {
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    unsigned groups = 0;
#ifdef HLDC_STATS
    extern unsigned long long hldcStat[];
    ++hldcStat[HLDC_STAT_SHADOW_CALL];
    const bool hldcShadowValid = findEnemyShadowGroups(ctx, &groups);
    const bool hldcShadowSkip = hldcShadowValid && hldcShadowGroupsMiss(ctx, groups);
    if (hldcShadowSkip) ++hldcStat[HLDC_STAT_SHADOW_SKIP];
    if (!hldcShadowValid) {
        ++hldcStat[HLDC_STAT_SHADOW_SKIP + 1];
        static unsigned hldcShadowWhy;
        if (hldcShadowWhy++ < 5) {
            const RValue hldcPlatforms = ctx->globalScopeInstance != NULL ? Instance_getSelfVar(ctx->globalScopeInstance, enemyShadowPlatforms) : RValue_makeUndefined();
            logInfo("HLDCSHADOWWHY ctxok=%d grid=%d self=%d eps=%g ptype=%d plen=%d\n", ctx == enemyShadowContext,
                    ctx->runner->spatialGrid != NULL, ctx->currentCallFrame->self != NULL, (double) ctx->runner->mathEpsilon,
                    (int) hldcPlatforms.type, hldcPlatforms.type == RVALUE_ARRAY && hldcPlatforms.array != NULL ? (int) GMLArray_length1D(hldcPlatforms.array) : -1);
            if (hldcPlatforms.type == RVALUE_ARRAY && hldcPlatforms.array != NULL)
                for (int32_t k = 0; k < GMLArray_length1D(hldcPlatforms.array); ++k) {
                    const RValue* item = GMLArray_slot(hldcPlatforms.array, k);
                    logInfo("HLDCSHADOWWHY item%d type=%d value=%g\n", k, item ? (int) item->type : -1, item ? (double) RValue_toReal(*item) : -1.0);
                }
        }
    }
    else if (groups != 0) { static unsigned hldcShadowGroupLog; if (hldcShadowGroupLog++ < 20) logInfo("HLDCSHADOW groups=%#x obj=%d\n", groups, ctx->currentCallFrame->self->objectIndex); }
    return hldcShadowSkip;
#endif
    return findEnemyShadowGroups(ctx, &groups) && hldcShadowGroupsMiss(ctx, groups);
#else
    return false;
#endif
}

RValue hldcBinaryVM(VMContext* ctx, uint8_t opcode, RValue a, RValue b) {
    stackPushTyped(ctx, a, GML_TYPE_VARIABLE);
    stackPushTyped(ctx, b, GML_TYPE_VARIABLE);
    const OpWord op = HLDC_OPWORD(opcode, 0);
    switch (opcode) {
        case OP_DIV: handleDiv(ctx, op); break;
        case OP_REM: handleRem(ctx, op); break;
        case OP_MOD: handleMod(ctx, op); break;
        case OP_AND: handleAnd(ctx, op); break;
        case OP_OR: handleOr(ctx, op); break;
        case OP_XOR: handleXor(ctx, op); break;
        case OP_SHL: handleShl(ctx, op); break;
        case OP_SHR: handleShr(ctx, op); break;
        default: abort();
    }
    return stackPop(ctx);
}

bool hldcCompareSlow(VMContext* ctx, uint8_t comparison, RValue a, RValue b) {
    HLDC_STAT(HLDC_STAT_COMPARE_SLOW);
    stackPushTyped(ctx, a, GML_TYPE_VARIABLE);
    stackPushTyped(ctx, b, GML_TYPE_VARIABLE);
    handleCmp(ctx, HLDC_OPWORD(OP_CMP, (uint32_t) comparison << 8));
    return hldcTruthy(stackPop(ctx));
}

RValue hldcNeg(VMContext* ctx, RValue a) {
    stackPushTyped(ctx, a, GML_TYPE_VARIABLE);
    handleNeg(ctx, HLDC_OPWORD(OP_NEG, 0));
    return stackPop(ctx);
}

// Bitwise not of a value (GML ~); logical not is !hldcTruthy.

RValue hldcBitNot(VMContext* ctx, RValue a) {
    stackPushTyped(ctx, a, GML_TYPE_VARIABLE);
    handleNot(ctx, (OpWord){(uint32_t) OP_NOT << 24 | (uint32_t) GML_TYPE_INT32 << 16});
    return stackPop(ctx);
}

void hldcResolve(VMContext* ctx, HldcCallee* callee, const char* name) {
    // As the VM's call cache: GameMaker 1 scripts (bytecode 15 and below) win over builtins of the same name.
    callee->resolved = true;
    callee->builtin = VM_findBuiltin(ctx, name);
    callee->script = -1;
    const ptrdiff_t slot = shgeti(ctx->codeIndexByName, (char*) name);
    if (slot >= 0 && ctx->dataWin->gen8.wadVersion <= 15) callee->builtin = nullptr;
    if (callee->builtin != nullptr) return;
    if (slot >= 0) {
        callee->script = ctx->codeIndexByName[slot].value;
        return;
    }
    char codeName[160];
    snprintf(codeName, sizeof(codeName), "gml_Script_%s", name);
    const ptrdiff_t scriptSlot = shgeti(ctx->codeIndexByName, codeName);
    if (scriptSlot >= 0) callee->script = ctx->codeIndexByName[scriptSlot].value;
    else logWarn("HLDC: unknown function %s\n", name);
}

// Native scripts (native_scripts_source) register here; they answer a call directly, as VM_executeCode would.
bool (*hldcNativeCall)(VMContext* ctx, int32_t codeIndex, Instance* self, const RValue* args, int32_t argc, RValue* result);

// Calls consume their arguments.

RValue hldcCall(VMContext* ctx, HldcCallee* callee, const char* name, RValue* args, int32_t argc) {
    if (!callee->resolved) hldcResolve(ctx, callee, name);
    RValue result;
    if (callee->builtin != nullptr) {
        HLDC_STAT(HLDC_STAT_BUILTIN);
#ifdef HLDC_STATS
        const uint64_t hldcBuiltinStart = nowNanos(); const uint64_t hldcOuter = hldcChildNanos; hldcChildNanos = 0;
        result = callee->builtin(ctx, args, argc);
        { const uint64_t spent = nowNanos() - hldcBuiltinStart; hldcBuiltinChild = hldcChildNanos; hldcStatBuiltin((void*) callee->builtin, name, spent); hldcChildNanos = hldcOuter + spent; }
        if (getenv("HLDC_GPU_TRACE") != NULL && ctx->runner != NULL && ctx->runner->frameCount == 600 &&
            (strncmp(name, "surface_", 8) == 0 || strncmp(name, "draw_clear", 10) == 0 || strncmp(name, "shader_", 7) == 0 ||
             strncmp(name, "draw_set_colour_write", 21) == 0 || strncmp(name, "draw_surface", 12) == 0 ||
             strncmp(name, "draw_sprite", 11) == 0 || strncmp(name, "draw_rectangle", 14) == 0 ||
             strncmp(name, "draw_set_blend", 14) == 0 || strncmp(name, "gpu_", 4) == 0 || strncmp(name, "draw_text", 9) == 0)) {
            logInfo("HLDCGPU %s %s", ctx->dataWin->code.entries[ctx->currentCallFrame->codeIndex].name, name);
            for (int32_t i = 0; i < argc && i < 3; ++i) logInfo(" %g", (double) (args[i].type == RVALUE_REAL || args[i].type == RVALUE_INT32 ? RValue_toReal(args[i]) : -1));
            logInfo("\n");
        }
#else
        result = callee->builtin(ctx, args, argc);
#endif
    } else if (callee->script >= 0) {
        CallFrame* frame = ctx->currentCallFrame;
        if (callee->direct == nullptr && !callee->checkedDirect) {
            // Translated scripts call each other directly unless VM_executeCode has a native shortcut for them.
            callee->checkedDirect = true;
            if (!hldcHasShortcut(ctx, callee->script)) callee->direct = hldcTranslated(ctx, callee->script);
        }
        if (callee->direct != nullptr) {
            HLDC_STAT(HLDC_STAT_SCRIPT_DIRECT);
            result = callee->direct(ctx, frame->self, frame->other, args, argc, frame->eventObjectIndex,
                                    frame->eventType, frame->eventSubtype);
            ctx->savearefBalance = 0;
        } else if (hldcNativeCall != nullptr && hldcNativeCall(ctx, callee->script, frame->self, args, argc, &result)) {
            ctx->savearefBalance = 0;
        } else {
            HLDC_STAT(HLDC_STAT_SCRIPT_VM);
#ifdef HLDC_STATS
            { extern uint32_t* hldcVmCalls; if (hldcVmCalls == nullptr) hldcVmCalls = safeCalloc(ctx->dataWin->code.count, sizeof(uint32_t));
              if ((uint32_t) callee->script < ctx->dataWin->code.count) ++hldcVmCalls[callee->script]; }
#endif
            result = VM_executeCode(ctx, callee->script, frame->self, frame->other, frame->eventObjectIndex,
                                    frame->eventType, frame->eventSubtype, args, argc);
        }
    } else {
        result = RValue_makeUndefined();
    }
    for (int32_t i = 0; i < argc; ++i) RValue_free(&args[i]);
    return result;
}

// `with`: the VM's environment stack. hldcWithBegin returns whether the body runs for a first instance.

bool hldcWithBegin(VMContext* ctx, RValue targetValue) {
    HLDC_STAT(HLDC_STAT_WITH);
    int32_t target = RValue_toInt32(targetValue);
    if (targetValue.type == RVALUE_UNDEFINED) target = INSTANCE_NOONE;
    RValue_free(&targetValue);
    EnvFrame* frame = ctx->envDepth < VM_INLINE_ENV_FRAMES ? &ctx->inlineEnvFrames[ctx->envDepth]
                                                           : (EnvFrame*) safeMalloc(sizeof(EnvFrame));
    ctx->envDepth++;
    frame->savedInstance = (Instance*) ctx->currentCallFrame->self;
    frame->savedOtherInstance = (Instance*) ctx->currentCallFrame->other;
    frame->instanceList = nullptr;
    frame->instanceCount = 0;
    frame->currentIndex = 0;
    frame->parent = ctx->envStack;
    ctx->envStack = frame;
    ctx->currentCallFrame->other = (Instance*) ctx->currentCallFrame->self;
    Runner* runner = (Runner*) ctx->runner;
    if (target == INSTANCE_SELF) return true;
    if (target == INSTANCE_OTHER) {
        if (frame->parent != nullptr) switchToInstance(ctx, frame->parent->savedInstance);
        else if (frame->savedOtherInstance != nullptr) switchToInstance(ctx, frame->savedOtherInstance);
        return true;
    }
    if (target == INSTANCE_ALL || (target >= 0 && (uint32_t) target < INSTANCE_ID_BASE)) {
        if (target == INSTANCE_ALL) snapshotEnvInstances(ctx, frame, runner->instances, false);
        else if ((uint32_t) target < ctx->dataWin->objt.count) snapshotEnvInstances(ctx, frame, runner->instancesByObject[target], true);
        if (frame->instanceCount == 0) return false;
        switchToInstance(ctx, frame->instanceList[0]);
        return true;
    }
    if (target >= INSTANCE_ID_BASE) {
        Instance* inst = hldLookupInstanceId(runner, target);
        if (inst != nullptr && inst->active) {
            switchToInstance(ctx, inst);
            return true;
        }
    }
    return false;
}

void hldcWithEnd(VMContext* ctx) {
    EnvFrame* frame = ctx->envStack;
    restoreEnvContext(ctx, frame);
    ctx->envStack = frame->parent;
    if (frame->instanceList != nullptr && frame->instanceList != frame->inlineInstances) {
        if (hldReducedVMWork) releaseEnvSnapshot(ctx, frame->instanceList);
        else free(frame->instanceList);
    }
    if (--ctx->envDepth >= VM_INLINE_ENV_FRAMES) free(frame);
}

// Advances to the next active instance; ends the with and returns false when done.

bool hldcWithNext(VMContext* ctx) {
    EnvFrame* frame = ctx->envStack;
    while (frame->currentIndex + 1 < frame->instanceCount) {
        frame->currentIndex++;
        Instance* next = frame->instanceList[frame->currentIndex];
        if (next->active) {
            switchToInstance(ctx, next);
            return true;
        }
    }
    hldcWithEnd(ctx);
    return false;
}

// Function prologue/epilogue: a CallFrame like VM_executeCode builds, with independent argument copies.

int32_t hldcAssetIndex(VMContext* ctx, const char* name) {
    RValue arg = RValue_makeString(name);
    static BuiltinFunc lookup;
    if (lookup == nullptr) lookup = VM_findBuiltin(ctx, "asset_get_index");
    RValue result = lookup(ctx, &arg, 1);
    const int32_t index = RValue_toInt32(result);
    RValue_free(&result);
    return index;
}
'''


class Emitter:
    def __init__(self, code_name, code_index_expr, assets, variables):
        self.code_name = code_name
        self.code_index_expr = code_index_expr
        self.assets = assets
        self.variables = variables
        self.locals = []
        self.local_set = set()
        self.statics = []          # static declarations (var refs, callees, asset slots)
        self.static_names = {}
        self.lines = []
        self.label = 0
        self.loop_stack = []       # (break_label, continue_label, is_with)
        self.lp = code_name in os.environ.get("HLDC_LINEPROF", "").split(",")  # statement timing (diagnostic), comma list
        self.lp_suffix = ""
        self.lp_texts = []
        self.temp = 0

    # ---- helpers ----
    def new_label(self, prefix):
        self.label += 1
        return f"hldc_{prefix}{self.label}"

    def static(self, key, declaration):
        if key not in self.static_names:
            name = f"hldcS{len(self.statics)}"
            self.static_names[key] = name
            self.statics.append(declaration.format(name=name))
        return self.static_names[key]

    def var_ref(self, name, global_scope=False):
        slot = self.static(("var", name, global_scope), "static uint32_t {name} = UINT32_MAX;")
        return f"HLDC_REF({slot}, {c_string(name)}, {'true' if global_scope else 'false'})"

    def seq(self, parts, combine):
        """Evaluate parts in order into temps, then combine(temps): C argument order is unspecified."""
        if len(parts) <= 1 or all(simple(p) for p in parts[1:]):
            return combine(parts)
        names = []
        out = "({ "
        for part in parts:
            self.temp += 1
            name = f"hldcT{self.temp}"
            out += f"RValue {name} = {part}; "
            names.append(name)
        return out + combine(names) + "; })"

    def site(self, name):
        return self.static(("site", name, False), "static HldcSite {name} = {{.ref = UINT32_MAX}};")

    def emit(self, line, depth):
        self.lines.append("    " * depth + line)

    # ---- expressions (each returns a C expression of type RValue, owned or weak) ----
    def expr(self, node):
        kind = node[0]
        if kind == "num":
            value = node[1]
            if value == int(value) and -2**31 <= value < 2**31:
                return f"hldcInt({int(value)})"
            return f"hldcReal({value!r})"
        if kind == "str":
            return f"hldcString({c_string(node[1])})"
        if kind == "name":
            return self.read_name(node[1])
        if kind == "field":
            return self.read_field(node[1], node[2], None)
        if kind == "index":
            return self.read_index(node)
        if kind == "call":
            return self.call(node[1], node[2])
        if kind == "unary":
            op, operand = node[1], node[2]
            if op == "!":
                return f"hldcBool(!hldcTruthy({self.expr(operand)}))"
            if op == "-":
                return f"hldcNeg(ctx, {self.expr(operand)})"
            if op == "~":
                return f"hldcBitNot(ctx, {self.expr(operand)})"
        if kind == "binary":
            op, a, b = node[1], node[2], node[3]
            if op == "&&":
                return f"hldcBool(hldcTruthy({self.expr(a)}) && hldcTruthy({self.expr(b)}))"
            if op == "||":
                return f"hldcBool(hldcTruthy({self.expr(a)}) || hldcTruthy({self.expr(b)}))"
            if op == "^^":
                return f"hldcBool(hldcTruthy({self.expr(a)}) != hldcTruthy({self.expr(b)}))"
            pair = [self.expr(a), self.expr(b)]
            if op in COMPARISONS:
                return self.seq(pair, lambda t: f"hldcBool(hldcCompare(ctx, {COMPARISONS[op]}, {t[0]}, {t[1]}))")
            if op == "+":
                return self.seq(pair, lambda t: f"hldcAdd(ctx, {t[0]}, {t[1]})")
            if op == "-":
                return self.seq(pair, lambda t: f"hldcSub(ctx, {t[0]}, {t[1]})")
            if op == "*":
                return self.seq(pair, lambda t: f"hldcMul(ctx, {t[0]}, {t[1]})")
            if op == "/":
                return self.seq(pair, lambda t: f"hldcDiv(ctx, {t[0]}, {t[1]})")
            if op == "%":
                return self.seq(pair, lambda t: f"hldcMod(ctx, {t[0]}, {t[1]})")
            if op in OPCODES:
                return self.seq(pair, lambda t: f"hldcBinaryVM(ctx, {OPCODES[op]}, {t[0]}, {t[1]})")
        if kind == "ternary":
            return f"(hldcTruthy({self.expr(node[1])}) ? {self.expr(node[2])} : {self.expr(node[3])})"
        if kind in ("preinc", "predec", "postinc", "postdec"):
            return self.increment(node)
        raise ValueError(f"{self.code_name}: unsupported expression {node}")

    def read_name(self, name):
        if name in self.local_set:
            return f"hldcWeak(L_{name})"
        match = ARGUMENT.fullmatch(name)
        if match:
            return f"hldcArgument(ctx, {match[1]})"
        if name == "argument_count":
            return "hldcInt(ctx->currentCallFrame->scriptArgsCount)"
        if name in ("self", "other", "global"):
            return f"hldcInt({ {'self': -1, 'other': -2, 'global': -5}[name] })"
        if name in BUILTIN_ARRAYS:
            return f"hldcReadIndexOn(ctx, (Instance*) ctx->currentCallFrame->self, INSTANCE_SELF, &{self.site(name)}, {c_string(name)}, 0)"
        if name in self.variables:
            return f"hldcReadSelf(ctx, &{self.site(name)}, {c_string(name)})"
        if name in self.assets:
            # Asset names are compile-time indices in the bytecode.
            return f"hldcInt({self.assets[name][1]})"
        if name in CONSTANTS:
            value = CONSTANTS[name]
            return "hldcReal(M_PI)" if value == "pi" else f"hldcInt({value})"
        raise ValueError(f"{self.code_name}: unknown name {name}")

    def scope(self, base):
        """(scope C expression, computed?) for the instance part of base.name."""
        if base[0] == "name" and base[1] in ("self", "other", "global"):
            return {"self": "INSTANCE_SELF", "other": "INSTANCE_OTHER", "global": "INSTANCE_GLOBAL"}[base[1]], False
        return f"hldcIndex({self.expr(base)})", True

    def read_field(self, base, name, index):
        """index is a list of index expressions or None."""
        is_global = base[0] == "name" and base[1] == "global"
        if index is not None and base[0] == "name" and base[1] in ("self", "other", "global"):
            site = self.static(("site", name, is_global), "static HldcSite {name} = {{.ref = UINT32_MAX}};")
            scope = {"self": "INSTANCE_SELF", "other": "INSTANCE_OTHER", "global": "INSTANCE_GLOBAL"}[base[1]]
            return (f"hldcReadIndexOn(ctx, hldcScopeInstance(ctx, {scope}), {scope}, &{site}, {c_string(name)}, "
                    f"hldcIndex({self.index_value(index)}))")
        if index is None and name not in BUILTIN_ARRAYS:
            site = self.static(("site", name, is_global), "static HldcSite {name} = {{.ref = UINT32_MAX}};")
            if base[0] == "name" and base[1] in ("self", "other", "global"):
                scope = {"self": "INSTANCE_SELF", "other": "INSTANCE_OTHER", "global": "INSTANCE_GLOBAL"}[base[1]]
                return f"hldcReadScope(ctx, {scope}, &{site}, {c_string(name)})"
            return f"hldcReadTarget(ctx, hldcIndex({self.expr(base)}), &{site}, {c_string(name)})"
        ref = self.var_ref(name, is_global)
        fixed = base[0] == "name" and base[1] in ("self", "other", "global")
        parts = ([] if fixed else [self.expr(base)]) + ([self.index_value(index)] if index else [])
        def combine(t):
            scope = {"self": "INSTANCE_SELF", "other": "INSTANCE_OTHER", "global": "INSTANCE_GLOBAL"}[base[1]] if fixed \
                else f"hldcIndex({t[0]})"
            idx = f"hldcIndexOf({t[-1]}, {len(index)})" if index else "INT32_MIN"
            return f"hldcRead(ctx, {scope}, {ref}, {idx}, {'false' if fixed else 'true'})"
        if not parts:
            return combine([])
        return self.seq(parts, combine) if len(parts) > 1 else combine(parts)

    def index_value(self, indices):
        """One RValue expression for the (possibly 2D) index: 2D indices fold to i * 32000 + j."""
        if len(indices) == 1:
            return self.expr(indices[0])
        if len(indices) == 2:
            return self.seq([self.expr(indices[0]), self.expr(indices[1])],
                            lambda t: f"hldcInt(hldcIndex2({t[0]}, {t[1]}))")
        raise ValueError(f"{self.code_name}: {len(indices)}-dimensional index")

    def index_expr(self, indices):
        if len(indices) == 1:
            return f"hldcIndex({self.expr(indices[0])})"
        if len(indices) == 2:
            return f"hldcIndex2({self.expr(indices[0])}, {self.expr(indices[1])})"
        raise ValueError(f"{self.code_name}: {len(indices)}-dimensional index")

    def read_index(self, node):
        base, indices = node[1], node[2]
        if base[0] == "field":
            return self.read_field(base[1], base[2], indices)
        index = self.index_expr(indices)
        if base[0] == "name":
            name = base[1]
            if name in self.local_set:
                return f"hldcLocalIndex(&L_{name}, {index})"
            if name == "argument":
                return f"hldcArgument(ctx, {index})"
            if ARGUMENT.fullmatch(name):
                return f"hldcArgumentIndex(ctx, {ARGUMENT.fullmatch(name)[1]}, {index})"
            return (f"hldcReadIndexOn(ctx, (Instance*) ctx->currentCallFrame->self, INSTANCE_SELF, &{self.site(name)}, "
                    f"{c_string(name)}, {index})")
        raise ValueError(f"{self.code_name}: unsupported index base {base}")

    def call(self, name, args):
        slot = self.static(("call", name), "static HldcCallee {name};")
        if not args:
            return f"hldcCall(ctx, &{slot}, {c_string(name)}, nullptr, 0)"
        # GML pushes arguments last first, so they are evaluated right to left.
        values = [self.expr(arg) for arg in args]
        if len(values) == 1 or all(simple(v) for v in values):
            return f"hldcCall(ctx, &{slot}, {c_string(name)}, (RValue[]){{{', '.join(values)}}}, {len(args)})"
        out = "({ "
        names = [None] * len(values)
        for position in range(len(values) - 1, -1, -1):
            self.temp += 1
            names[position] = f"hldcT{self.temp}"
            out += f"RValue {names[position]} = {values[position]}; "
        return out + f"hldcCall(ctx, &{slot}, {c_string(name)}, (RValue[]){{{', '.join(names)}}}, {len(args)}); }})"

    def increment(self, node):
        kind, target = node
        delta = "hldcInt(1)"
        op = "hldcAdd" if kind in ("preinc", "postinc") else "hldcSub"
        # Read, compute, write; the value of the expression is the new (pre) or old (post) value.
        self.temp += 1
        temp = f"hldcT{self.temp}"
        read = self.read_target(target)
        if kind.startswith("pre"):
            return f"({{ RValue {temp} = {op}(ctx, {read}, {delta}); {self.write_target(target, f'RValue_makeIndependent({temp})')}; {temp}; }})"
        return (f"({{ RValue {temp} = RValue_makeIndependent({read}); "
                f"{self.write_target(target, f'{op}(ctx, RValue_makeIndependent({temp}), {delta})')}; {temp}; }})")

    def write_field(self, base, name, indices, value):
        is_global = base[0] == "name" and base[1] == "global"
        if indices is not None and base[0] == "name" and base[1] in ("self", "other", "global"):
            site = self.static(("site", name, is_global), "static HldcSite {name} = {{.ref = UINT32_MAX}};")
            scope = {"self": "INSTANCE_SELF", "other": "INSTANCE_OTHER", "global": "INSTANCE_GLOBAL"}[base[1]]
            # value first, then the index, as the bytecode does.
            return self.seq([value, self.index_value(indices)],
                            lambda t: f"hldcWriteIndexOn(ctx, hldcScopeInstance(ctx, {scope}), {scope}, &{site}, "
                                      f"{c_string(name)}, hldcIndex({t[1]}), {t[0]})")
        if indices is None and name not in BUILTIN_ARRAYS:
            site = self.static(("site", name, is_global), "static HldcSite {name} = {{.ref = UINT32_MAX}};")
            if base[0] == "name" and base[1] in ("self", "other", "global"):
                scope = {"self": "INSTANCE_SELF", "other": "INSTANCE_OTHER", "global": "INSTANCE_GLOBAL"}[base[1]]
                return f"hldcWriteScope(ctx, {scope}, &{site}, {c_string(name)}, {value})"
            # value first, then the target, as the bytecode does.
            return self.seq([value, self.expr(base)],
                            lambda t: f"hldcWriteTarget(ctx, hldcIndex({t[1]}), &{site}, {c_string(name)}, {t[0]})")
        ref = self.var_ref(name, is_global)
        fixed = base[0] == "name" and base[1] in ("self", "other", "global")
        parts = [value] + ([] if fixed else [self.expr(base)]) + ([self.index_value(indices)] if indices else [])
        def combine(t):
            scope = {"self": "INSTANCE_SELF", "other": "INSTANCE_OTHER", "global": "INSTANCE_GLOBAL"}[base[1]] if fixed \
                else f"hldcIndex({t[1]})"
            idx = f"hldcIndex({t[-1]})" if indices else "INT32_MIN"
            return f"hldcWrite(ctx, {scope}, {ref}, {idx}, {'false' if fixed else 'true'}, {t[0]})"
        return self.seq(parts, combine) if len(parts) > 1 else combine(parts)

    def read_target(self, target):
        return self.expr(target)

    # ---- assignment targets ----
    def write_target(self, target, value):
        kind = target[0]
        if kind == "name":
            name = target[1]
            if name in self.local_set:
                return f"hldcSetLocal(&L_{name}, {value})"
            match = ARGUMENT.fullmatch(name)
            if match:
                return f"hldcSetArgument(ctx, {match[1]}, {value})"
            if name in BUILTIN_ARRAYS:
                return f"hldcWriteIndexOn(ctx, (Instance*) ctx->currentCallFrame->self, INSTANCE_SELF, &{self.site(name)}, {c_string(name)}, 0, {value})"
            return f"hldcWriteSelf(ctx, &{self.site(name)}, {c_string(name)}, {value})"
        if kind == "field":
            return self.write_field(target[1], target[2], None, value)
        if kind == "index":
            base, indices = target[1], target[2]
            if base[0] == "field":
                return self.write_field(base[1], base[2], indices, value)
            if base[0] == "name":
                name = base[1]
                def combine(t):
                    index = f"hldcIndex({t[1]})"
                    if name in self.local_set:
                        return f"hldcSetLocalIndex(ctx, &L_{name}, {index}, {t[0]})"
                    if ARGUMENT.fullmatch(name):
                        return f"hldcSetArgumentIndex(ctx, {ARGUMENT.fullmatch(name)[1]}, {index}, {t[0]})"
                    return (f"hldcWriteIndexOn(ctx, (Instance*) ctx->currentCallFrame->self, INSTANCE_SELF, "
                            f"&{self.site(name)}, {c_string(name)}, {index}, {t[0]})")
                return self.seq([value, self.index_value(indices)], combine)
        raise ValueError(f"{self.code_name}: unsupported assignment target {target}")

    # ---- statements ----
    def statement(self, node, depth):
        if self.lp and node[0] != "block":
            ident = len(self.lp_texts)
            self.lp_texts.append("")
            self.emit(f"hldcLPMark{self.lp_suffix}({ident});", depth)
            mark = len(self.lines)
            self._statement(node, depth)
            text = self.lines[mark].strip() if mark < len(self.lines) else ""
            self.lp_texts[ident] = f"d{depth} " + text[:150]
            return
        self._statement(node, depth)

    def _statement(self, node, depth):
        kind = node[0]
        if kind == "block":
            for child in node[1]:
                self.statement(child, depth)
        elif kind in ("var", "globalvar"):
            for name, init in node[1]:
                if init is not None:
                    if kind == "globalvar":
                        self.emit(f"hldcWrite(ctx, INSTANCE_GLOBAL, {self.var_ref(name, True)}, INT32_MIN, false, {self.expr(init)});", depth)
                    else:
                        self.emit(f"hldcSetLocal(&L_{name}, {self.expr(init)});", depth)
        elif kind == "assign":
            op, target, value = node[1], node[2], node[3]
            if op == "=":
                self.emit(self.write_target(target, self.expr(value)) + ";", depth)
            else:
                binary = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%", "|=": "|", "&=": "&", "^=": "^"}[op]
                combined = ("binary", binary, target, value)
                self.emit(self.write_target(target, self.expr(combined)) + ";", depth)
        elif kind == "expr":
            self.emit(f"{{ RValue hldcDiscard = {self.expr(node[1])}; RValue_free(&hldcDiscard); }}", depth)
        elif kind == "if":
            self.emit(f"if (hldcTruthy({self.expr(node[1])})) {{", depth)
            self.statement(node[2], depth + 1)
            if node[3] is not None:
                self.emit("} else {", depth)
                self.statement(node[3], depth + 1)
            self.emit("}", depth)
        elif kind == "while":
            end, cont = self.new_label("end"), self.new_label("next")
            self.emit("while (1) {", depth)
            self.emit(f"if (!hldcTruthy({self.expr(node[1])})) break;", depth + 1)
            self.loop(node[2], depth + 1, end, cont)
            self.emit(f"{cont}:;", depth + 1)
            self.emit("}", depth)
            self.emit(f"{end}:;", depth)
        elif kind == "dountil":
            end, cont = self.new_label("end"), self.new_label("next")
            self.emit("while (1) {", depth)
            self.loop(node[1], depth + 1, end, cont)
            self.emit(f"{cont}:;", depth + 1)
            self.emit(f"if (hldcTruthy({self.expr(node[2])})) break;", depth + 1)
            self.emit("}", depth)
            self.emit(f"{end}:;", depth)
        elif kind == "for":
            init, condition, step, body = node[1], node[2], node[3], node[4]
            end, cont = self.new_label("end"), self.new_label("next")
            # enemy Step's last statement probes four shadow points per non-floor platform. When none hits
            # (Codex's grouped probe), the loop only writes locals that are never read again, so it is skipped.
            shadow = self.code_name == "gml_Object_enemy_Step_0" and "nonFloorPlatforms" in repr(condition)
            # FlameVent Step probes four object types for a trigger. Codex's guard proves when no probe can change
            # the state; the loop then only leaves obj at its last target and writes dead locals.
            vent = self.code_name == "gml_Object_FlameVent_Step_0" and "enemyWeaponCol" in repr(body)
            if shadow:
                self.emit("if (!hldcEnemyShadowMisses(ctx)) {", depth)
                depth += 1
            elif vent:
                self.emit("if (!hldcFlameVentSkip(ctx)) {", depth)
                depth += 1
            if init is not None:
                self.statement(init, depth)
            self.emit("while (1) {", depth)
            if condition is not None:
                self.emit(f"if (!hldcTruthy({self.expr(condition)})) break;", depth + 1)
            self.loop(body, depth + 1, end, cont)
            self.emit(f"{cont}:;", depth + 1)
            if step is not None:
                self.statement(step, depth + 1)
            self.emit("}", depth)
            self.emit(f"{end}:;", depth)
            if shadow or vent:
                self.emit("}", depth - 1)
        elif kind == "repeat":
            end, cont = self.new_label("end"), self.new_label("next")
            self.temp += 1
            counter = f"hldcRepeat{self.temp}"
            self.emit(f"for (int32_t {counter} = hldcIndex({self.expr(node[1])}); {counter} > 0; --{counter}) {{", depth)
            self.loop(node[2], depth + 1, end, cont)
            self.emit(f"{cont}:;", depth + 1)
            self.emit("}", depth)
            self.emit(f"{end}:;", depth)
        elif kind == "with":
            end, cont = self.new_label("wend"), self.new_label("wnext")
            self.emit(f"if (hldcWithBegin(ctx, {self.expr(node[1])})) {{", depth)
            self.emit("do {", depth + 1)
            self.loop_stack.append((end, cont, True))
            self.statement(node[2], depth + 2)
            self.loop_stack.pop()
            self.emit(f"{cont}:;", depth + 2)
            self.emit("} while (hldcWithNext(ctx));", depth + 1)
            self.emit(f"goto {end}_done;", depth + 1)
            self.emit(f"{end}: hldcWithEnd(ctx);", depth + 1)
            self.emit(f"{end}_done:;", depth + 1)
            self.emit("} else {", depth)
            self.emit("hldcWithEnd(ctx);", depth + 1)
            self.emit("}", depth)
        elif kind == "switch":
            self.switch(node, depth)
        elif kind == "break":
            target = self.loop_stack[-1]
            self.emit(f"goto {target[0]};", depth)
        elif kind == "continue":
            target = next(t for t in reversed(self.loop_stack) if t[1] is not None)
            # continue inside a switch continues the enclosing loop; inside with it ends this instance.
            self.emit(f"goto {target[1]};", depth)
        elif kind == "exit":
            self.emit("goto hldc_done;", depth)
        elif kind == "return":
            if node[1] is not None:
                self.emit(f"RValue_free(&hldcResult); hldcResult = hldcReturnValue({self.expr(node[1])});", depth)
            self.emit("goto hldc_done;", depth)
        else:
            raise ValueError(f"{self.code_name}: unsupported statement {kind}")

    def loop(self, body, depth, end, cont):
        self.loop_stack.append((end, cont, False))
        self.statement(body, depth)
        self.loop_stack.pop()

    def switch(self, node, depth):
        subject, cases = node[1], node[2]
        end = self.new_label("swend")
        self.temp += 1
        value = f"hldcSwitch{self.temp}"
        self.emit("{", depth)
        self.emit(f"RValue {value} = RValue_makeIndependent({self.expr(subject)});", depth + 1)
        labels = [self.new_label("case") for _ in cases]
        default = None
        for label, (case, _) in zip(labels, cases):
            if case is None:
                default = label
                continue
            # Each comparison consumes its operands, so it gets a view of the switch value.
            self.emit(f"if (hldcCompare(ctx, CMPOP_EQUAL, hldcWeak({value}), {self.expr(case)})) goto {label};", depth + 1)
        self.emit(f"goto {default or end};", depth + 1)
        # A switch is a break target but not a continue target.
        self.loop_stack.append((end, None, False))
        for label, (_, body) in zip(labels, cases):
            self.emit(f"{label}:;", depth + 1)
            for statement in body:
                self.statement(statement, depth + 2)
        self.loop_stack.pop()
        self.emit(f"{end}:;", depth + 1)
        self.emit(f"RValue_free(&{value});", depth + 1)
        self.emit("}", depth)

    def function(self, tree, symbol):
        self.lp_suffix = "_" + symbol
        collect_locals(tree, self.locals, self.local_set)
        self.statement(tree, 1)
        body = self.lines
        out = [f"// {self.code_name}",
               f"RValue {symbol}(VMContext* ctx, Instance* self, Instance* other, RValue* args, int32_t argc, "
               "int32_t eventObject, int32_t eventType, int32_t eventSubtype) {",
               *("    " + s for s in self.statics),
               f"    HLDC_ENTER({self.code_index_expr})"]
        if self.lp:
            n = max(1, len(self.lp_texts))
            texts = ", ".join(c_string(t) for t in self.lp_texts) or '""'
            x = self.lp_suffix
            name = self.code_name
            runtime = [
                "uint64_t nowNanos(void);",
                f"static const char* const hldcLPText{x}[{n}] = {{{texts}}};",
                f"static uint64_t hldcLPAcc{x}[{n}]; static uint32_t hldcLPCnt{x}[{n}]; static int hldcLPCur{x} = -1; static uint64_t hldcLPT{x};",
                f"static inline void hldcLPMark{x}(int id) {{ const uint64_t t = nowNanos(); if (hldcLPCur{x} >= 0) hldcLPAcc{x}[hldcLPCur{x}] += t - hldcLPT{x}; hldcLPCur{x} = id; hldcLPT{x} = t; ++hldcLPCnt{x}[id]; }}",
                f"struct HldcLPGuard{x} {{ VMContext* ctx; }};",
                f"static void hldcLPExit{x}(struct HldcLPGuard{x}* g) {{",
                f"    const uint64_t t = nowNanos(); if (hldcLPCur{x} >= 0) hldcLPAcc{x}[hldcLPCur{x}] += t - hldcLPT{x}; hldcLPCur{x} = -1;",
                "    static int last = -1; const int f = g->ctx->runner != NULL ? g->ctx->runner->frameCount : 0; if (last < 0) last = f;",
                "    if (f - last < 300) return;",
                "    for (int rank = 0; rank < 40; ++rank) { int best = -1; uint64_t most = 0;",
                f"        for (int i = 0; i < {n}; ++i) if (hldcLPAcc{x}[i] > most) {{ most = hldcLPAcc{x}[i]; best = i; }}",
                "        if (best < 0) break;",
                f"        logInfo(\"HLDCLP {name} frame=%d ms=%.4f n=%.1f id=%d %s\\n\", f, most * 1e-6 / (f - last), hldcLPCnt{x}[best] / (double) (f - last), best, hldcLPText{x}[best]); hldcLPAcc{x}[best] = 0; }}",
                f"    memset(hldcLPAcc{x}, 0, sizeof hldcLPAcc{x}); memset(hldcLPCnt{x}, 0, sizeof hldcLPCnt{x}); last = f;",
                "}",
            ]
            out = runtime + out
            out += [f"    struct HldcLPGuard{self.lp_suffix} hldcLPGuard __attribute__((cleanup(hldcLPExit{self.lp_suffix}))) = {{ctx}};"]
        out += [f"    RValue L_{name} = RValue_makeUndefined();" for name in self.locals]
        out += body
        out += ["hldc_done:;"]
        out += [f"    RValue_free(&L_{name});" for name in self.locals]
        out += ["    HLDC_LEAVE()", "    return hldcResult;", "}", ""]
        return out


def simple(code):
    """C expressions whose evaluation order cannot matter (literals and plain local views)."""
    return re.fullmatch(r"hldc(Int|Real|String)\([^()]*\)|hldcWeak\(L_\w+\)|HLDC_ASSET\(\w+, \"[^\"]*\"\)", code) is not None


def collect_locals(node, order, seen):
    if isinstance(node, tuple):
        if node and node[0] == "var":
            for name, init in node[1]:
                if name not in seen:
                    seen.add(name)
                    order.append(name)
                collect_locals(init, order, seen)
            return
        for child in node[1:]:
            collect_locals(child, order, seen)
    elif isinstance(node, list):
        for child in node:
            collect_locals(child, order, seen)


def c_string(text):
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


def translate(entries, directory, assets, variables, per_part=150, stats=False):
    """(vm.c addition, [part sources]): the runtime and name table for vm.c, and the translated functions split
    into separate translation units so they compile in parallel."""
    shared = ("#define HLDC_STATS\n" if stats else "") + SHARED
    parts, current, table = [], [], []
    for number, name in enumerate(entries):
        tree = parse((Path(directory) / f"{name}.gml").read_text())
        symbol = f"hldc_{number}"
        emitter = Emitter(name, f"HLDC_CODE({c_string(name)})", assets, variables)
        current += emitter.function(tree, symbol)
        table.append((name, symbol))
        if len(table) % per_part == 0:
            parts.append(shared + "\n" + "\n".join(current))
            current = []
    if current:
        parts.append(shared + "\n" + "\n".join(current))
    vm = [shared, RUNTIME]
    vm += [f"RValue {symbol}(VMContext*, Instance*, Instance*, RValue*, int32_t, int32_t, int32_t, int32_t);"
           for _, symbol in table]
    vm.append("static const struct { const char* name; HldcFunction function; } hldcTable[] = {")
    vm += [f"    {{{c_string(name)}, {symbol}}}," for name, symbol in table]
    vm += ["    {nullptr, nullptr},", "};"]
    return "\n".join(vm), parts
