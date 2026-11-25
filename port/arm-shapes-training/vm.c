#include "vm.h"
#include "vm_builtins.h"
#ifdef ENABLE_SPRITE_DECODE_CACHE
#include "image/sprite_decode_cache.h"
#endif
#include "instance.h"
#include "runner.h"
#include "binary_utils.h"
#include "utils.h"
#include "wad_versions.h"
#include "profiler.h"
#include "string_builder.h"
#include "opword.h"
#include "op.h"
#include "cmpop.h"

#include "stdio_compat.h"
#include <stdlib.h>
#include "string_compat.h"
#include "math_compat.h"

#include "stb_ds.h"


enum { HLD_DIRECT_ID_COUNT = 16384 };
extern const Runner* hldDirectIdOwner;
extern Instance* hldDirectIds[HLD_DIRECT_ID_COUNT];
extern unsigned hldDirectIdHits, hldDirectIdFallbacks;

static inline Instance* hldLookupInstanceId(Runner* runner, uint32_t id) {
    const uint32_t index = id - INSTANCE_ID_BASE;
    if (runner == hldDirectIdOwner && index < HLD_DIRECT_ID_COUNT) {
        Instance* result = hldDirectIds[index];
#ifdef HLD_VERIFY_DIRECT_IDS
        require(result == hmget(runner->instancesById, id));
#endif
        ++hldDirectIdHits;
        return result;
    }
    ++hldDirectIdFallbacks;
    return hmget(runner->instancesById, id);
}



#include <assert.h>

// ===[ Stack Operations ]===

#ifdef ENABLE_VM_TRACING
static bool shouldTraceStack(VMContext* ctx) {
    if (shlen(ctx->stackToBeTraced) == 0) return false;
    if (ctx->traceBytecodeAfterFrame > ctx->runner->frameCount) return false;
    return shgeti(ctx->stackToBeTraced, "*") != -1 || shgeti(ctx->stackToBeTraced, VM_getCurrentCallFrameCodeName(ctx)) != -1;
}

// Returns a heap-allocated "[elem0, elem1, ..., elemN]" string for the current stack contents (bottom -> top). Caller frees.
static char* formatStackContents(VMContext* ctx) {
    StringBuilder sb = StringBuilder_create(256);
    StringBuilder_appendChar(&sb, '[');
    repeat(ctx->stack.top, si) {
        char* typed = RValue_toStringTyped(ctx->stack.slots[si]);
        if (si > 0) StringBuilder_append(&sb, ", ");
        StringBuilder_append(&sb, typed);
        free(typed);
    }
    StringBuilder_appendChar(&sb, ']');
    char* result = StringBuilder_toString(&sb);
    StringBuilder_free(&sb);
    return result;
}
#endif

// Returns the native byte size of a GML data type on the runner's stack.
// The Dup instruction (and several BC17+ BREAK sub-opcodes) encode byte counts, not slot counts.
static int gmlTypeNativeSize(uint8_t gmlType) {
    switch (gmlType) {
        case GML_TYPE_DOUBLE:   return 8;
        case GML_TYPE_FLOAT:    return 4;
        case GML_TYPE_INT32:    return 4;
        case GML_TYPE_INT64:    return 8;
        case GML_TYPE_BOOL:     return 4;
        case GML_TYPE_VARIABLE: return 16;
        case GML_TYPE_STRING:   return 4;
        case GML_TYPE_INT16:    return 4;
        default:                return 16;
    }
}

static void stackPush(VMContext* ctx, RValue val) {
    assert(VM_STACK_SIZE > ctx->stack.top);
#ifdef ENABLE_VM_TRACING
    if (shouldTraceStack(ctx)) {
        char* valStr = RValue_toStringTyped(val);
        ctx->stack.slots[ctx->stack.top++] = val;
        char* stackBuf = formatStackContents(ctx);
        logInfo("VM: [%s] PUSH %s [stack=%d -> %d] %s\n", VM_getCurrentCallFrameCodeName(ctx), valStr, ctx->stack.top - 1, ctx->stack.top, stackBuf);
        free(stackBuf);
        free(valStr);
        return;
    }
#endif
    ctx->stack.slots[ctx->stack.top++] = val;
}

static void stackPushTyped(VMContext* ctx, RValue val, uint8_t gmlStackType) {
    val.gmlStackType = gmlStackType;
    stackPush(ctx, val);
}

#ifdef ENABLE_VM_TRACING
static void logStackPopTrace(VMContext* ctx, RValue val) {
    if (shouldTraceStack(ctx)) {
        char* valStr = RValue_toStringTyped(val);
        char* stackBuf = formatStackContents(ctx);
        logInfo("VM: [%s] POP  %s [stack=%d -> %d] %s\n", VM_getCurrentCallFrameCodeName(ctx), valStr, ctx->stack.top + 1, ctx->stack.top, stackBuf);
        free(stackBuf);
        free(valStr);
    }
}
#endif

static inline RValue stackPop(VMContext* ctx) {
    assert(ctx->stack.top > 0);
#ifdef ENABLE_VM_TRACING
    RValue val = ctx->stack.slots[--ctx->stack.top];
    logStackPopTrace(ctx, val);
    return val;
#else
    return ctx->stack.slots[--ctx->stack.top];
#endif
}

// Helper function that pops from the stack and casts the result to an int32_t
static inline int32_t stackPopInt32(VMContext* ctx) {
    return RValue_toInt32(stackPop(ctx));
}

#if IS_WAD17_OR_HIGHER_ENABLED

static RValue* stackPeek(VMContext* ctx) {
    assert(ctx->stack.top > 0);
    return &ctx->stack.slots[ctx->stack.top - 1];
}

#endif

// ===[ Reference Chain Resolution ]===

// Walks reference chains from the bytecode buffer and builds hash maps
// mapping absolute file offsets to resolved operand values.
// The bytecode buffer stays completely read-only.
// Patches bytecode operands in-place so that variable/function reference chain deltas
// are replaced with resolved indices. This avoids needing hash map lookups at runtime.
static void patchReferenceOperands(VMContext* ctx) {
    DataWin* dataWin = ctx->dataWin;
    uint8_t* buf = dataWin->bytecodeBuffer;
    size_t base = dataWin->bytecodeBufferBase;

    // Patch variable operands: replace delta with varIdx (preserving upper 5 bits)
    repeat(dataWin->vari.variableCount, varIdx) {
        Variable* v = &dataWin->vari.variables[varIdx];
        if (v->occurrences == 0) continue;

        uint32_t addr = v->firstAddress;
        repeat(v->occurrences, occ) {
            uint32_t operandAddr = addr + 4;
            uint32_t operand = BinaryUtils_readUint32(&buf[operandAddr - base]);
            uint32_t delta = operand & 0x07FFFFFF;
            uint32_t upperBits = operand & 0xF8000000;

            // Patch in-place: upper bits preserved, lower 27 = varIdx
            BinaryUtils_writeUint32(&buf[operandAddr - base], upperBits | (varIdx & 0x07FFFFFF));

            if (v->occurrences > occ + 1) {
                addr += delta;
            }
        }
    }

    // Patch function operands: replace delta with funcIdx
    repeat(dataWin->func.functionCount, funcIdx) {
        Function* f = &dataWin->func.functions[funcIdx];
        if (f->occurrences == 0) continue;

        uint32_t addr = f->firstAddress;
        repeat(f->occurrences, occ) {
            uint32_t operandAddr = addr + 4;
            uint32_t operand = BinaryUtils_readUint32(&buf[operandAddr - base]);

            uint32_t instrWord = BinaryUtils_readUint32(&buf[addr - base]);
            OpWord opword = {instrWord};
            bool isPushRef = OpWord_opcode(opword) == OP_BREAK && OpWord_extra(opword) == BREAK_PUSHREF;

            uint32_t delta;
            if (isPushRef) {
                delta = operand & 0x00FFFFFF;
                BinaryUtils_writeUint32(&buf[operandAddr - base], ((uint32_t) ASSET_TYPE_SCRIPT << 24) | (funcIdx & 0x00FFFFFF));
            } else {
                delta = operand & 0x07FFFFFF;
                BinaryUtils_writeUint32(&buf[operandAddr - base], funcIdx);
            }

            if (f->occurrences > occ + 1) {
                addr += delta;
            }
        }
    }
}

// Resolve a variable operand: returns upper bits | varIndex (read directly from patched bytecode)

// Store an eleven-bit slot hint alongside the original sixteen-bit
// normal variable reference. Other operand readers see the original ref.
#define HLD_SLOT_HINT_TAG 0xc0000000u
#define HLD_SLOT_HINT_MASK 0x07ffu

static uint32_t resolveVarOperand(const uint8_t* extraData) {
    const uint32_t word = BinaryUtils_readUint32Aligned(extraData);
    if ((word & 0xf8000000u) == HLD_SLOT_HINT_TAG)
        return ((uint32_t)VARTYPE_NORMAL << 24) | (word & 0xffffu);
    return word;
}

// Resolve a function operand: returns funcIndex (read directly from patched bytecode)
static uint32_t resolveFuncOperand(const uint8_t* extraData) {
    return BinaryUtils_readUint32Aligned(extraData);
}

// ===[ Array Operations ]===
//
// All arrays live as RVALUE_ARRAY (GMLArray*) inside a scalar variable slot (self vars, global vars, or local vars).
// Variable reads return the RValue (which may be an array pointer) and variable writes update the slot directly.
//
// Reads return a weak view of the slot value - callers must incRef + set ownsReference if they want to retain it.
//
// Writes (VARTYPE_ARRAY Pop, BREAK_POPAF, BREAK_PUSHAC materialisation) go through VM_arraySetWithCoW,
// which handles:
//   * slot-not-yet-an-array -> allocate a fresh GMLArray
//   * CoW fork when another scope/slot owns the array (BC16 predicate uses the slot address; BC17+ predicate compares against ctx->currentArrayOwner set by BREAK_SETOWNER)
//   * grow-on-write past the current length
//   * transfer ownership of "val" into arr->data[index], freeing whatever was there before.
//

// CoW fork for array writes (BC17+): when the slot's array is owned by a different scope, replace it with a uniquely-owned clone stamped with the current owner,
// so the upcoming store (Pop for one dimensional arrays, or the chain's eventual BREAK_POPAF for multi-dimensional arrays) can write in place.
// Essentially "If we don't own this array, copy it and decrease the reference count of the original array".
// (See GameMaker-HTML5's "__yy_gml_array_check" / "__yy_gml_array_check_index_chain").
static void cowForkSlotForModificationIfNeeded(VMContext* ctx, RValue* slot) {
    GMLArray* arr = slot->array;
    if (arr->owner == ctx->currentArrayOwner) return;
    GMLArray* clone = GMLArray_clone(arr, ctx->currentArrayOwner);
    GMLArray_decRef(arr);
    slot->array = clone;
    slot->ownsReference = true;
}

// Write array[index] = val with CoW semantics. Always makes an independent copy of val, caller retains ownership and must RValue_free(&val) when done.
// `slot` is the RValue* holding the array (e.g. &globalVars[id], &inst->selfVars[..].value, &localVars[slot]).
// Returns the (possibly newly-forked) GMLArray* now in *slot.
static GMLArray* VM_arraySetWithCoW(VMContext* ctx, RValue* slot, int32_t index, RValue val) {
    require(slot != nullptr);
    requireMessageFormatted(__FILE__, __LINE__, index >= 0, "Trying to write to an array using a negative index! Index: %d", index);

    RValue writeVal = RValue_makeIndependent(val);

    void* intendedOwner;
#if IS_WAD17_OR_HIGHER_ENABLED
    intendedOwner = IS_WAD17_OR_HIGHER(ctx) ? ctx->currentArrayOwner : (void*) slot;
#else
    (void)ctx;
    intendedOwner = (void*) slot;
#endif

    // Case 1: slot doesn't hold an array yet, replace whatever's there with a fresh one.
    if (slot->type != RVALUE_ARRAY || slot->array == nullptr) {
        RValue_free(slot);
        GMLArray* fresh = GMLArray_create(ctx->dataWin, 0);
        fresh->owner = intendedOwner;
        *slot = RValue_makeArray(fresh);
        GMLArray_growTo(fresh, index + 1);
        RValue_writeIntoSlotStealingOwnershipOrCopying(GMLArray_slot(fresh, index), writeVal);
        return fresh;
    }

    GMLArray* arr = slot->array;

    // Case 2: CoW fork check.
    if (IS_WAD17_OR_HIGHER(ctx)) {
        cowForkSlotForModificationIfNeeded(ctx, slot);
        arr = slot->array;
    } else if (arr->refCount > 1 && arr->owner != (void*) slot) {
        GMLArray* clone = GMLArray_clone(arr, intendedOwner);
        GMLArray_decRef(arr);
        slot->array = clone;
        slot->ownsReference = true;
        arr = clone;
    } else if (arr->owner == nullptr) {
        // Claim ownership on first write to an unowned array (e.g. freshly allocated by a builtin).
        arr->owner = intendedOwner;
    }

    // Case 3: Write!
    GMLArray_growTo(arr, index + 1);
    RValue_writeIntoSlotStealingOwnershipOrCopying(GMLArray_slot(arr, index), writeVal);
    return arr;
}

// Creates a copy of "name"
int32_t VM_getOrAllocateVarID(VMContext* ctx, const char* name) {
    ptrdiff_t slot = shgeti(ctx->varNameMap, name);
    if (slot >= 0) return ctx->varNameMap[slot].value;
    int32_t id = ctx->nextDynamicVarID++;
    shput(ctx->varNameMap, safeStrdup(name), id);
    return id;
}

char* VM_getVariableNameByVarId(VMContext* ctx, int32_t varId) {
    char* name = nullptr;
    repeat(shlen(ctx->varNameMap), j) {
        if (ctx->varNameMap[j].value == varId) {
            name = ctx->varNameMap[j].key;
        }
    }
    if (name == nullptr) {
        forEach(Variable, variable, ctx->dataWin->vari.variables, ctx->dataWin->vari.variableCount) {
            if (variable->varID == varId && variable->instanceType != INSTANCE_LOCAL) {
                return (char*) variable->name;
            }
        }
    }
    return name;
}

RValue VM_structGetVariableByVarId(Instance* structInst, int32_t slotId, int32_t arrayIndex) {
    requireMessageFormatted(__FILE__, __LINE__, structInst->objectIndex == STRUCT_OBJECT_INDEX, "Trying to use VM_structGetByVarId on a instance that isn't a struct! objectIndex=%d", structInst->objectIndex);
    RValue* slot = IntRValueHashMap_findSlot(&structInst->selfVars, slotId);
    if (slot != nullptr) {
        if (arrayIndex >= 0) return GMLArray_getOnArrayRef(slot, arrayIndex);
        RValue result = *slot;
        result.ownsReference = false;
        return result;
    }
    return RValue_makeUndefined();
}

// Plain member read on a struct.
// Returns a weak view (or undefined when the member/element doesn't exist).
RValue VM_structGetVariableByVarName(VMContext* ctx, Instance* structInst, const char* name, int32_t arrayIndex) {
    requireMessageFormatted(__FILE__, __LINE__, structInst->objectIndex == STRUCT_OBJECT_INDEX, "Trying to use VM_structGet on a instance that isn't a struct! objectIndex=%d", structInst->objectIndex);
    ptrdiff_t nameSlot = shgeti(ctx->varNameMap, (char*) name);
    if (nameSlot >= 0) {
        return VM_structGetVariableByVarId(structInst, ctx->varNameMap[nameSlot].value, arrayIndex);
    }
    return RValue_makeUndefined();
}

// Creates a copy of "name"
// This should ONLY be used for structs!
void VM_structSet(VMContext* ctx, Instance* structInst, const char* name, RValue val, int32_t arrayIndex) {
    requireMessageFormatted(__FILE__, __LINE__, structInst->objectIndex == STRUCT_OBJECT_INDEX, "Trying to use VM_structSet on a instance that isn't a struct! objectIndex=%d", structInst->objectIndex);
    int32_t varID = VM_getOrAllocateVarID(ctx, name);
    if (arrayIndex >= 0) {
        RValue* slot = IntRValueHashMap_getOrInsertUndefined(&structInst->selfVars, varID);
        VM_arraySetWithCoW(ctx, slot, arrayIndex, val);
    } else {
        Instance_setSelfVar(structInst, varID, val);
    }
}

// Creates a copy of "name"
// This should ONLY be used for structs!
void VM_structSetAndFreeVal(VMContext* ctx, Instance* structInst, const char* name, RValue val, int32_t arrayIndex) {
    VM_structSet(ctx, structInst, name, val, arrayIndex);
    RValue_free(&val);
}

// ===[ Array Access Helpers ]===

typedef struct {
    int32_t arrayIndex; // -1 when not an array access
    int32_t instanceType; // Instance type from stack (for VARTYPE_ARRAY / VARTYPE_STACKTOP)
    bool isArray;
    bool hasInstanceType; // true when instanceType was popped from stack
} ArrayAccess;

static int32_t resolveInstanceStackTop(VMContext* ctx) {
    return stackPopInt32(ctx);
}

static const char* varTypeToString(uint8_t varType) {
    switch (varType) {
        case VARTYPE_ARRAY:    return "ARRAY";
        case VARTYPE_STACKTOP: return "STACKTOP";
        case VARTYPE_NORMAL:   return "NORMAL";
        case VARTYPE_INSTANCE: return "INSTANCE";
        default:               return "UNKNOWN";
    }
}

// Pops array index (and optional stacktop value) from the stack if the varRef
// indicates an array or stacktop access. Returns { .arrayIndex = -1, .isArray = false }
// for plain variable access.
static ArrayAccess popArrayAccess(VMContext* ctx, uint32_t varRef) {
    uint8_t varType = (varRef >> 24) & 0xF8;
    ArrayAccess ret = {0};
    if (varType == VARTYPE_ARRAY) {
        // For array reads, GMS pushes: instanceType then arrayIndex (arrayIndex on top)
        int32_t arrayIndex = stackPopInt32(ctx);
        int32_t instanceType = stackPopInt32(ctx);

        // BC17: if instanceType is -9 (INSTANCE_STACKTOP), the actual instance is the next stack item.
        // This is used for chained access like `command_actor[i].specialsprite[arg]` where the array variable's owning instance is resolved from a computed value on the stack.
        if (IS_WAD17_OR_HIGHER(ctx) && instanceType == INSTANCE_STACKTOP) {
            instanceType = resolveInstanceStackTop(ctx);
        }

        ret.arrayIndex = arrayIndex;
        ret.instanceType = instanceType;
        ret.isArray = true;
        ret.hasInstanceType = true;
        return ret;
    }
    if (varType == VARTYPE_STACKTOP) {
        int32_t instanceType = stackPopInt32(ctx);

        // BC17: PushI.e -9 (INSTANCE_STACKTOP) is pushed before the Pop instruction.
        // When we pop -9, it means "the real instance type is the next item on the stack".
        if (IS_WAD17_OR_HIGHER(ctx) && instanceType == INSTANCE_STACKTOP) {
            instanceType = resolveInstanceStackTop(ctx);
        }
        ret.arrayIndex = -1;
        ret.isArray = false;
        ret.hasInstanceType = true;
        ret.instanceType = instanceType;
        return ret;
    }
    ret.arrayIndex = -1;
    ret.isArray = false;
    ret.hasInstanceType = false;
    return ret;
}

// ===[ Variable Resolution ]===

// Returns the object name for an instance, or "<global_scope>" for the global scope dummy instance
#ifdef ENABLE_VM_TRACING

static const char* instanceObjectName(VMContext* ctx, Instance* inst) {
    if (inst->objectIndex == STRUCT_OBJECT_INDEX) return "<global_scope>";
    return ctx->dataWin->objt.objects[inst->objectIndex].name;
}

#endif

static Variable* resolveVarDef(VMContext* ctx, uint32_t varRef) {
    uint32_t varIndex = varRef & 0x07FFFFFF;
    require(ctx->dataWin->vari.variableCount > varIndex);
    Variable* varDef = &ctx->dataWin->vari.variables[varIndex];
    return varDef;
}

// Maps a GML local's varID to its slot position in the current code's localVars[] array.
//
// BC15/16: varIDs for locals are already sequential slot indices (0, 1, 2, ...), so we return the varID unchanged.
//
// BC13/BC14, BC17+: a single GML local can surface as several VARI chunk entries that share a varID (BC17+) or has no pre-assigned slot at all (BC13/BC14).
// We key by varID/varIdx via the per-code currentCodeLocalsSlotMap so reads/writes via any reference agree on the same localVars slot.
enum { VM_INLINE_FRAME_VALUES = 8, VM_INLINE_LOCAL_VALUES = 24 };

static uint32_t resolveLocalSlot(VMContext* ctx, int32_t varID) {
    uint32_t slot;
    if (IS_WAD15_OR_HIGHER(ctx) && IS_WAD16_OR_BELOW(ctx)) {
        slot = (uint32_t) varID;
    } else {
        // BC13/BC14 and BC17+: allocate the slot dynamically because the data.win CANNOT be trusted to know how many localVars the script has
        slot = IntIntHashMap_getOrInsertSequential(ctx->currentCallFrame->currentCodeLocalsSlotMap, varID);
    }

    // Grow this frame's localVars window to cover `slot` whether the entry is pre-existing or freshly allocated.
    // Pre-existing entries can still be past ctx->currentCallFrame->localVarsCount if a nested call to the same code extended the slot map while the outer frame was suspended (the outer frame's localVarCount is captured at call entry and doesn't follow later growth).
    CallFrame* frame = ctx->currentCallFrame;
    if (slot >= frame->localVarsCount) {
        uint32_t count = slot + 1;
        if (!frame->localVarsOnHeap && count <= VM_INLINE_LOCAL_VALUES) {
            memset(frame->localVars + frame->localVarsCount, 0, (count - frame->localVarsCount) * sizeof(RValue));
        } else {
            RValue* locals = (RValue*)safeCalloc(count, sizeof(RValue));
            memcpy(locals, frame->localVars, frame->localVarsCount * sizeof(RValue));
            if (frame->localVarsOnHeap) free(frame->localVars);
            frame->localVars = locals;
            frame->localVarsOnHeap = true;
        }
        frame->localVarsCount = count;
    }
    return slot;
}

// Finds an instance by target value.
// target >= INSTANCE_ID_BASE: instance ID (find specific instance, including recently-destroyed-but-not-cleaned-up-yet ones so GML code can read properties of an instance just after instance_destroy within the same step).
// target >= 0 && target < INSTANCE_ID_BASE: object index (find first ACTIVE instance of that object, checking parent chains)
Instance* VM_findInstanceByTarget(VMContext* ctx, int32_t target) {
    Runner* runner = (Runner*) ctx->runner;

    if (target >= INSTANCE_ID_BASE) {
        // Instance ID - find specific instance
        return hldLookupInstanceId(runner, target);
    }

    // Object index - find first active matching instance via the descendant-inclusive bucket. Pure read, no user code, so we walk the bucket directly without an arena snapshot.
    if (target >= 0 && runner->dataWin->objt.count > (uint32_t) target) {
        Instance** bucket = runner->instancesByObject[target];
        int32_t bucketCount = (int32_t) arrlen(bucket);
        for (int32_t i = 0; bucketCount > i; i++) {
            if (bucket[i]->active) return bucket[i];
        }
    }
    return nullptr;
}

// Inline read of a non-array, non-builtin variable from a simple scope.
// Returns false when the instanceType isn't covered or the scope's instance pointer is unavailable, so the caller can fall through to the full resolveVariableRead.
// Used by the OP_PUSH/PUSHLOC/PUSHGLB fast paths in executeBytecodeInstructions to skip the entire resolveVariableRead dispatch overhead.
static inline bool tryFastVarRead(VMContext* ctx, int32_t instanceType, Variable* varDef, RValue* out) {
    switch (instanceType) {
        case INSTANCE_SELF: {
            Instance* inst = (Instance*) ctx->currentCallFrame->self;
            if (inst == nullptr) return false;
#if IS_WAD17_OR_HIGHER_ENABLED
            if (IS_WAD17_OR_HIGHER(ctx)) {
                RValue* slot = IntRValueHashMap_findSlot(&inst->selfVars, varDef->varID);
                if (slot == nullptr) return false; // Static struct fallback.
                *out = *slot;
                out->ownsReference = false;
                return true;
            }
#endif
            *out = IntRValueHashMap_get(&inst->selfVars, varDef->varID);
            return true;
        }
        case INSTANCE_LOCAL: {
            uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
            require(ctx->currentCallFrame->localVarsCount > localSlot);
            *out = ctx->currentCallFrame->localVars[localSlot];
            out->ownsReference = false;
            return true;
        }
        case INSTANCE_GLOBAL: {
            Instance* inst = (Instance*) ctx->globalScopeInstance;
#if IS_WAD17_OR_HIGHER_ENABLED
            if (IS_WAD17_OR_HIGHER(ctx)) {
                RValue* slot = IntRValueHashMap_findSlot(&inst->selfVars, varDef->varID);
                if (slot == nullptr) return false; // Static struct fallback.
                *out = *slot;
                out->ownsReference = false;
                return true;
            }
#endif
            *out = IntRValueHashMap_get(&inst->selfVars, varDef->varID);
            return true;
        }
        case INSTANCE_OTHER: {
            Instance* inst = (Instance*) ctx->currentCallFrame->other;
            if (inst == nullptr) return false;
#if IS_WAD17_OR_HIGHER_ENABLED
            if (IS_WAD17_OR_HIGHER(ctx)) {
                RValue* slot = IntRValueHashMap_findSlot(&inst->selfVars, varDef->varID);
                if (slot == nullptr) return false; // Static struct fallback.
                *out = *slot;
                out->ownsReference = false;
                return true;
            }
#endif
            *out = IntRValueHashMap_get(&inst->selfVars, varDef->varID);
            return true;
        }
    }
    return false;
}

#if IS_WAD17_OR_HIGHER_ENABLED
// Static variables: Each code index has its own "struct" for static variables.
// Lazily create a struct for each codeIndex that needs a static variable.
Instance* VM_getOrCreateStaticStruct(VMContext* ctx, int32_t codeIndex) {
    if (ctx->staticStructs == nullptr || 0 > codeIndex || (uint32_t) codeIndex >= ctx->dataWin->code.count) return nullptr;
    Instance* staticStruct = ctx->staticStructs[codeIndex];
    if (staticStruct == nullptr) {
        staticStruct = Runner_createStruct((Runner*) ctx->runner);
        staticStruct->pinned = true;
        ctx->staticStructs[codeIndex] = staticStruct;
    }
    return staticStruct;
}

// Used when the static variable could not be read from the original struct, walks through the chain to find who is the owner of the static variable.
// Returns true and fills *out when found.
static bool tryReadStaticFallback(VMContext* ctx, Instance* inst, int32_t varID, ArrayAccess* access, RValue* out) {
    if (ctx->staticStructs == nullptr || inst == nullptr || inst->objectIndex != STRUCT_OBJECT_INDEX) return false;
    if (0 > inst->constructorCodeIndex || (uint32_t) inst->constructorCodeIndex >= ctx->dataWin->code.count) return false;
    Instance* staticStruct = ctx->staticStructs[inst->constructorCodeIndex];
    int32_t depth = 0;
    // Walk instance's static struct -> parent static -> ...
    while (staticStruct != nullptr) {
        requireMessage(64 > depth, "Try read static fallback chain is too deep! Bug?");
        RValue* sslot = IntRValueHashMap_findSlot(&staticStruct->selfVars, varID);
        if (sslot != nullptr) {
            if (access->isArray) {
                *out = GMLArray_getOnArrayRef(sslot, access->arrayIndex);
            } else {
                *out = *sslot;
                out->ownsReference = false;
            }
            return true;
        }
        staticStruct = staticStruct->staticParent;

        depth++;
    }
    return false;
}

// Links the current constructor's static struct to a parent constructor's static struct for static inheritance.
void VM_copyStatic(VMContext* ctx, RValue* parentRef) {
    if (ctx->staticStructs == nullptr) return;
    // Resolve the parent constructor's code index (FUNC index -> name -> codeIndex), mirroring @@NewGMLObject@@.
    int32_t parentCodeIndex = -1;
    if (parentRef->type == RVALUE_METHOD && parentRef->method != nullptr) {
        parentCodeIndex = parentRef->method->codeIndex;
    } else {
        int32_t rawArg = RValue_toInt32(*parentRef);
        if (rawArg >= 0 && ctx->dataWin->func.functionCount > (uint32_t) rawArg) {
            const char* funcName = ctx->dataWin->func.functions[rawArg].name;
            if (funcName != nullptr) {
                ptrdiff_t idx = shgeti(ctx->codeIndexByName, (char*) funcName);
                if (idx >= 0) parentCodeIndex = ctx->codeIndexByName[idx].value;
            }
        }
    }
    if (0 > parentCodeIndex) return;
    Instance* childStatic = VM_getOrCreateStaticStruct(ctx, ctx->currentCallFrame->codeIndex);
    Instance* parentStatic = VM_getOrCreateStaticStruct(ctx, parentCodeIndex);
    if (childStatic != nullptr && parentStatic != nullptr && childStatic != parentStatic) {
        childStatic->staticParent = parentStatic;
    }
}
#endif

// Tries reading a instance variable (from the selfVars map) and, if it doesn't exist, tries reading from the shared static struct.
static bool tryReadInstanceVarOrStatic(VMContext* ctx, Instance* instance, int32_t varID, ArrayAccess* access, RValue* out) {
    RValue* slot = IntRValueHashMap_findSlot(&instance->selfVars, varID);
    if (slot != nullptr) {
        if (access->isArray) {
            *out = GMLArray_getOnArrayRef(slot, access->arrayIndex);
            return true;
        }
        RValue val = *slot;
        val.ownsReference = false;
        *out = val;
        return true;
    }

#if IS_WAD17_OR_HIGHER_ENABLED
    // Static variables: a struct field declared "static" lives on the constructor's shared static struct, not the instance.
    RValue staticVal;
    if (tryReadStaticFallback(ctx, instance, varID, access, &staticVal)) {
        *out = staticVal;
        return true;
    }
#else
    (void)ctx;
#endif
    return false;
}

static inline bool VM_ensureScriptArg(VMContext* ctx, int32_t writeIndex) {
    CallFrame* frame = ctx->currentCallFrame;
    if (writeIndex < 0) return false;
    if (writeIndex < frame->scriptArgsCount) return true;

    int32_t count = writeIndex + 1;
    if (!frame->scriptArgsOnHeap && frame->scriptArgs != nullptr && count <= VM_INLINE_FRAME_VALUES) {
        memset(frame->scriptArgs + frame->scriptArgsCount, 0, (count - frame->scriptArgsCount) * sizeof(RValue));
    } else {
        RValue* args = (RValue*)safeCalloc(count, sizeof(RValue));
        if (frame->scriptArgsCount > 0) {
            memcpy(args, frame->scriptArgs, frame->scriptArgsCount * sizeof(RValue));
            if (frame->scriptArgsOnHeap) free(frame->scriptArgs);
        }
        frame->scriptArgs = args;
        frame->scriptArgsOnHeap = true;
    }
    frame->scriptArgsCount = count;
    return true;
}

void VM_writeToScriptArgs(VMContext* ctx, int32_t writeIndex, RValue val) {
    if (!VM_ensureScriptArg(ctx, writeIndex)) return;

    RValue independent = RValue_makeIndependent(val);
    RValue_free(&ctx->currentCallFrame->scriptArgs[writeIndex]);
    ctx->currentCallFrame->scriptArgs[writeIndex] = independent;
}

void VM_writeToScriptArgsArrayElement(VMContext* ctx, int32_t writeIndex, int32_t arrayIndex, RValue val) {
    if (!VM_ensureScriptArg(ctx, writeIndex)) return;
    VM_arraySetWithCoW(ctx, &ctx->currentCallFrame->scriptArgs[writeIndex], arrayIndex, val);
}

static RValue resolveVariableReadWithAccess(VMContext* ctx, int32_t instanceType, uint32_t varRef, ArrayAccess access) {
    Variable* varDef = resolveVarDef(ctx, varRef);

    // Use instance type from stack when available (VARTYPE_ARRAY / VARTYPE_STACKTOP)
    int32_t originalInstanceType = instanceType;
    if (access.hasInstanceType) {
        instanceType = access.instanceType;
    }

    // BC17+: Push.v/Pop.v with instrInstanceType == -9 (STACKTOP) and VARTYPE_NORMAL means
    // "the instance is on the stack" (e.g. `struct.field` after @@NewGMLObject@@). Pop it here.
#if IS_WAD17_OR_HIGHER_ENABLED
    if (IS_WAD17_OR_HIGHER(ctx) && !access.hasInstanceType && instanceType == INSTANCE_STACKTOP) {
        instanceType = resolveInstanceStackTop(ctx);
    }
#endif

    // Resolve target instance for object/instance references (instanceType >= 0)
    Instance* targetInstance = (Instance*) ctx->currentCallFrame->self;
    if (instanceType >= 0) {
        targetInstance = VM_findInstanceByTarget(ctx, instanceType);
        if (targetInstance == nullptr) {
            const char* varTypeName = varTypeToString((varRef >> 24) & 0xF8);
            if (instanceType < INSTANCE_ID_BASE && (uint32_t) instanceType < ctx->dataWin->objt.count) {
                GameObject* gameObject = &ctx->dataWin->objt.objects[instanceType];
                logWarn("VM: [%s] READ var '%s' on object index %d (%s) but no instance found (varType=%s, isArray=%s, originalInstanceType=%d, hasInstanceType=%s, varID=%d)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, instanceType, gameObject->name, varTypeName, access.isArray ? "true" : "false", originalInstanceType, access.hasInstanceType ? "true" : "false", varDef->varID);
            } else {
                logWarn("VM: [%s] READ var '%s' on instance %d but no instance found (varType=%s, isArray=%s, originalInstanceType=%d, hasInstanceType=%s, varID=%d)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, instanceType, varTypeName, access.isArray ? "true" : "false", originalInstanceType, access.hasInstanceType ? "true" : "false", varDef->varID);
            }
            return RValue_makeReal(0.0);
        }
    } else if (instanceType == INSTANCE_GLOBAL) {
        targetInstance = ctx->globalScopeInstance;
    } else if (instanceType == INSTANCE_OTHER) {
        if (ctx->currentCallFrame->other != nullptr) {
            targetInstance = (Instance*) ctx->currentCallFrame->other;
        }
#if IS_WAD17_OR_HIGHER_ENABLED
    } else if (instanceType == INSTANCE_STATIC) {
        // "static" scope: read from the current constructor's shared static struct via the normal slot path below.
        targetInstance = VM_getOrCreateStaticStruct(ctx, ctx->currentCallFrame->codeIndex);
#endif
    } else if (IS_WAD17_OR_HIGHER(ctx) && instanceType == INSTANCE_ARG) {
        // BC17: argument0..argument15 via INSTANCE_ARG instance type (builtinVarId pre-resolved at parse time)
        int16_t builtinVarId = varDef->builtinVarId;
        RValue result;
        if (builtinVarId == BUILTIN_VAR_ARGUMENT_COUNT) {
            result = RValue_makeReal((GMLReal) ctx->currentCallFrame->scriptArgsCount);
        } else if (builtinVarId == BUILTIN_VAR_ARGUMENT) {
            // argument[N] array-style access
            int32_t idx = access.arrayIndex;
            if (ctx->currentCallFrame->scriptArgs != nullptr && ctx->currentCallFrame->scriptArgsCount > idx && idx >= 0) {
                result = ctx->currentCallFrame->scriptArgs[idx];
                result.ownsReference = false;
            } else {
                result = RValue_makeUndefined();
            }
        } else if (builtinVarId >= BUILTIN_VAR_ARGUMENT0 && BUILTIN_VAR_ARGUMENT15 >= builtinVarId) {
            int32_t argIndex = builtinVarId - BUILTIN_VAR_ARGUMENT0;
            if (ctx->currentCallFrame->scriptArgs != nullptr && ctx->currentCallFrame->scriptArgsCount > argIndex) {
                result = ctx->currentCallFrame->scriptArgs[argIndex];
                result.ownsReference = false;
                // If we are trying to access the argument via an array (example: argName[i]), we NEED to read INSIDE the array
                // Example:
                // function init(arg2) {
                //     var test = arg2[0]; // We NEED to read the [0] from the array
                // }
                // Without this, the caller gets the whole array back
                if (access.isArray && result.type == RVALUE_ARRAY && result.array != nullptr) {
                    result = GMLArray_getOnArrayRef(&result, access.arrayIndex);
                }
            } else {
                result = RValue_makeUndefined();
            }
        } else {
            logWarn("VM: [%s] INSTANCE_ARG read on unknown variable '%s' (builtinVarId=%d)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, builtinVarId);
            result = RValue_makeUndefined();
        }
        return result;
    }

    // Check for built-in variable (varID == -6 sentinel)
    if (varDef->varID == VARIABLE_BUILTIN) {
        // Struct fields are independent of instance built-ins with the same names.
        if (targetInstance != nullptr && targetInstance->objectIndex == STRUCT_OBJECT_INDEX && VMBuiltins_isInstanceScopedBuiltinVar(varDef->builtinVarId)) {
            ptrdiff_t nameSlot = shgeti(ctx->varNameMap, (char*) varDef->name);
            if (nameSlot >= 0) {
                int32_t structVarID = ctx->varNameMap[nameSlot].value;
                RValue value;
                if (tryReadInstanceVarOrStatic(ctx, targetInstance, structVarID, &access, &value))
                    return value;
            }
            return RValue_makeUndefined();
        }

        RValue result = VMBuiltins_getVariable(ctx, targetInstance, varDef->builtinVarId, varDef->name, access.arrayIndex);

#ifdef ENABLE_VM_TRACING
        if (instanceType == INSTANCE_GLOBAL) {
            VM_checkIfVariableShouldBeTracedAndLog(ctx, "global", nullptr, varDef->name, result, false, access.arrayIndex, -1, " (builtin)");
        } else if (targetInstance != nullptr && targetInstance->objectIndex >= 0) {
            VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, targetInstance), "self", varDef->name, result, false, access.arrayIndex, targetInstance->instanceId, " (builtin)");
        }
#endif

        return result;
    }

    // Resolve the variable's scalar slot pointer for the target scope. Array-valued vars live inline as RVALUE_ARRAY in the same slot.
    // GMLArray_getOnArrayRef handles the array indirection when access.isArray, VM_arraySetWithCoW handles CoW forking when writing.
    RValue* slot = nullptr;
    switch (instanceType) {
        case INSTANCE_LOCAL: {
            uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
            require(ctx->currentCallFrame->localVarsCount > localSlot);
            slot = &ctx->currentCallFrame->localVars[localSlot];
            break;
        }
        case INSTANCE_SELF:
        case INSTANCE_GLOBAL:
        default: {
            Instance* inst = targetInstance;
            if (inst == nullptr) {
                const char* varTypeName = varTypeToString((varRef >> 24) & 0xF8);
                logWarn("VM: [%s] Read on self var '%s' but no current instance (instanceType=%d, varType=%s, isArray=%s, originalInstanceType=%d, hasInstanceType=%s, varID=%d)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, instanceType, varTypeName, access.isArray ? "true" : "false", originalInstanceType, access.hasInstanceType ? "true" : "false", varDef->varID);
                return RValue_makeReal(0.0);
            }
            slot = IntRValueHashMap_findSlot(&inst->selfVars, varDef->varID);
            // sparse storage: nonexistent entry -> treat as undefined scalar (array reads fall through to GMLArray_getOnArrayRef returning undefined)
            if (slot == nullptr) {
#if IS_WAD17_OR_HIGHER_ENABLED
                // Static variables: a struct field declared "static" lives on the constructor's shared static struct, not the instance.
                RValue staticVal;
                if (tryReadStaticFallback(ctx, inst, varDef->varID, &access, &staticVal)) {
                    return staticVal;
                }
#endif
                return RValue_makeUndefined();
            }
            break;
        }
    }

    // Array access: read array[index] from the slot.
    if (access.isArray) {
        RValue result = GMLArray_getOnArrayRef(slot, access.arrayIndex);
#ifdef ENABLE_VM_TRACING
        const char* scopeName =
            instanceType == INSTANCE_LOCAL ? "local" :
            instanceType == INSTANCE_GLOBAL ? "global" :
            (targetInstance != nullptr ? instanceObjectName(ctx, targetInstance) : "self");
        const char* altName = (instanceType == INSTANCE_SELF || instanceType >= 0 || instanceType == INSTANCE_OTHER) ? "self" : nullptr;
        VM_checkIfVariableShouldBeTracedAndLog(ctx, scopeName, altName, varDef->name, result, false, access.arrayIndex, -1, "");
#endif
        return result;
    }

    // Scalar access: return the slot's current value as a weak view (slot retains ownership).
    RValue result = *slot;
    result.ownsReference = false;

#ifdef ENABLE_VM_TRACING
    if (instanceType == INSTANCE_GLOBAL) {
        VM_checkIfVariableShouldBeTracedAndLog(ctx, "global", nullptr, varDef->name, result, false, -1, -1, "");
    } else if (instanceType == INSTANCE_SELF || instanceType >= 0) {
        Instance* inst = targetInstance;
        if (inst != nullptr) {
            VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, result, false, -1, inst->instanceId, "");
        }
    }
#endif

    return result;
}

static RValue resolveVariableRead(VMContext* ctx, int32_t instanceType, uint32_t varRef) {
    return resolveVariableReadWithAccess(ctx, instanceType, varRef, popArrayAccess(ctx, varRef));
}

// Helper: write a variable value to a single specific instance (always copies, never moves the original val)
static void writeSingleInstanceVariable(VMContext* ctx, Instance* inst, Variable* varDef, ArrayAccess* access, RValue val) {
    // Built-in variable (varID == -6 sentinel)
    if (varDef->varID == VARIABLE_BUILTIN) {
        if (inst != nullptr && inst->objectIndex == STRUCT_OBJECT_INDEX)
            VM_structSet(ctx, inst, varDef->name, val, access->arrayIndex);
        else
            VMBuiltins_setVariable(ctx, inst, varDef->builtinVarId, varDef->name, val, access->arrayIndex);
        return;
    }

    // Array write - materialise-on-write via VM_arraySetWithCoW. getOrInsertUndefined returns the existing slot or inserts an UNDEFINED entry and returns it.
    if (access->isArray) {
        RValue* slot = IntRValueHashMap_getOrInsertUndefined(&inst->selfVars, varDef->varID);
        VM_arraySetWithCoW((VMContext*) ctx, slot, access->arrayIndex, val);
        return;
    }

    // Scalar write (Instance_setSelfVar always takes an independent ref; caller still owns "val").
    Instance_setSelfVar(inst, varDef->varID, val);
}

// Force out-of-line so the OP_POP fast path in executeBytecodeInstructions doesn't inline this, because we already have an "optimized" version for common writes
NOINLINE
static void resolveVariableWrite(VMContext* ctx, int32_t instanceType, uint32_t varRef, RValue val) {
    Variable* varDef = resolveVarDef(ctx, varRef);

    // Fast path: When the varType==VARTYPE_NORMAL...
    // * We can skip the popArrayAccess
    // * We can skip the BC17 STACKTOP and INSTANCE_ARG branches
    // * We can skip the array-write block itself
    // * We can skip BOTH instanceType switches
    if (varDef->varID >= 0) {
        switch (instanceType) {
            case INSTANCE_LOCAL: {
                uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
                require(ctx->currentCallFrame->localVarsCount > localSlot);
                RValue_writeIntoSlotStealingOwnershipOrCopying(&ctx->currentCallFrame->localVars[localSlot], val);
#ifdef ENABLE_VM_TRACING
                VM_checkIfVariableShouldBeTracedAndLog(ctx, "local", nullptr, varDef->name, ctx->currentCallFrame->localVars[localSlot], true, -1, -1, "");
#endif
                return;
            }
            case INSTANCE_SELF:
            case INSTANCE_GLOBAL: {
                Instance* inst = (Instance*) ctx->currentCallFrame->self;
                if (instanceType == INSTANCE_GLOBAL)
                    inst = ctx->globalScopeInstance;

                if (inst != nullptr) {
                    Instance_setSelfVar(inst, varDef->varID, val);
#ifdef ENABLE_VM_TRACING
                    {
                        RValue written = Instance_getSelfVar(inst, varDef->varID);
                        VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, written, true, -1, inst->instanceId, "");
                    }
#endif
                    RValue_free(&val);
                    return;
                }
                break; // fall through to slow path so the existing nullptr-instance error gets logged
            }
            case INSTANCE_OTHER: {
                Instance* inst = (Instance*) ctx->currentCallFrame->other;
                if (inst != nullptr) {
                    Instance_setSelfVar(inst, varDef->varID, val);
#ifdef ENABLE_VM_TRACING
                    {
                        RValue written = Instance_getSelfVar(inst, varDef->varID);
                        VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, written, true, -1, inst->instanceId, "");
                    }
#endif
                    RValue_free(&val);
                    return;
                }
                break; // fall through (otherInstance was nullptr, slow path will use currentInstance)
            }
        }
    }

    // The slow path is used for builtin vars, object/instance references (instanceType >= 0), INSTANCE_ARG/STACKTOP, and other miscellaneous things like if we get a nullptr above
    ArrayAccess access = popArrayAccess(ctx, varRef);

    // Use instance type from stack when available (VARTYPE_ARRAY / VARTYPE_STACKTOP)
    int32_t originalInstanceType = instanceType;
    if (access.hasInstanceType) {
        instanceType = access.instanceType;
    }

    // BC17+: Pop.v with instrInstanceType == -9 (STACKTOP) and VARTYPE_NORMAL means
    // "the instance is on the stack" (e.g. `struct.field =` after @@NewGMLObject@@). Pop it here.
#if IS_WAD17_OR_HIGHER_ENABLED
    if (IS_WAD17_OR_HIGHER(ctx) && !access.hasInstanceType && instanceType == INSTANCE_STACKTOP) {
        instanceType = resolveInstanceStackTop(ctx);
    }

    // "static" scope: write to the current constructor's shared static struct (runs once, guarded by isstaticok/setstatic).
    if (instanceType == INSTANCE_STATIC) {
        Instance* staticStruct = VM_getOrCreateStaticStruct(ctx, ctx->currentCallFrame->codeIndex);
        if (staticStruct != nullptr) {
            writeSingleInstanceVariable(ctx, staticStruct, varDef, &access, val);
        }
        RValue_free(&val);
        return;
    }
#endif

    // GML: writing through an object reference (obj_foo.var = val) sets the variable on ALL instances of that object. The setter (writeSingleInstanceVariable) can run user code, so iterate a snapshot of the bucket.
    if (instanceType >= 0 && INSTANCE_ID_BASE > instanceType) {
        Runner* runner = (Runner*) ctx->runner;
        bool found = false;
        int32_t snapBase = Runner_pushInstancesOfObject(runner, instanceType);
        int32_t snapEnd  = (int32_t) arrlen(runner->instanceSnapshots);
        for (int32_t i = snapBase; snapEnd > i; i++) {
            Instance* inst = runner->instanceSnapshots[i];
            if (!inst->active) continue;
            found = true;
            writeSingleInstanceVariable(ctx, inst, varDef, &access, val);
#ifdef ENABLE_VM_TRACING
            VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, val, true, -1, inst->instanceId, " (all-instances object write)");
#endif
        }
        Runner_popInstanceSnapshot(runner, snapBase);
        if (!found) {
            if (ctx->dataWin->objt.count > (uint32_t) instanceType) {
                GameObject* gameObject = &ctx->dataWin->objt.objects[instanceType];
                char* valAsString = RValue_toString(val, ctx->runner->dataWin);
                logWarn("VM: [%s] WRITE var '%s' on object %d (%s) but no instances found (value=%s)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, instanceType, gameObject->name, valAsString);
                free(valAsString);
            }
        }
        RValue_free(&val);
        return;
    }

    // Resolve target instance for instance ID references (instanceType >= INSTANCE_ID_BASE) or special types
    Instance* targetInstance = (Instance*) ctx->currentCallFrame->self;
    if (instanceType >= 0) {
        targetInstance = VM_findInstanceByTarget(ctx, instanceType);
        if (targetInstance == nullptr) {
            const char* varTypeName = varTypeToString((varRef >> 24) & 0xF8);
            char* valAsString = RValue_toString(val, ctx->runner->dataWin);
            logWarn("VM: [%s] WRITE var '%s' on instance %d but no instance found (varType=%s, isArray=%s, originalInstanceType=%d, hasInstanceType=%s, varID=%d, value=%s)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, instanceType, varTypeName, access.isArray ? "true" : "false", originalInstanceType, access.hasInstanceType ? "true" : "false", varDef->varID, valAsString);
            free(valAsString);
            return;
        }
    } else if (instanceType == INSTANCE_OTHER) {
        if (ctx->currentCallFrame->other != nullptr) {
            targetInstance = (Instance*) ctx->currentCallFrame->other;
        }
    } else if (IS_WAD17_OR_HIGHER(ctx) && instanceType == INSTANCE_ARG) {
        // BC17: write to argument0..argument15 via INSTANCE_ARG instance type (builtinVarId pre-resolved at parse time)
        int16_t bid = varDef->builtinVarId;
        int32_t writeIndex = -1;
        if (bid >= BUILTIN_VAR_ARGUMENT0 && BUILTIN_VAR_ARGUMENT15 >= bid) {
            writeIndex = bid - BUILTIN_VAR_ARGUMENT0;
        } else if (bid == BUILTIN_VAR_ARGUMENT) {
            writeIndex = access.arrayIndex;
        } else {
            logWarn("VM: [%s] INSTANCE_ARG write on unknown variable '%s' (builtinVarId=%d)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, bid);
        }
        if (writeIndex >= 0) {
            if (access.isArray && bid != BUILTIN_VAR_ARGUMENT) {
                VM_writeToScriptArgsArrayElement(ctx, writeIndex, access.arrayIndex, val);
            } else {
                VM_writeToScriptArgs(ctx, writeIndex, val);
            }
        }
        RValue_free(&val);
        return;
    }

    // Check for built-in variable (varID == -6 sentinel)
    if (varDef->varID == VARIABLE_BUILTIN) {
        if (targetInstance != nullptr && targetInstance->objectIndex == STRUCT_OBJECT_INDEX && VMBuiltins_isInstanceScopedBuiltinVar(varDef->builtinVarId))
            VM_structSet(ctx, targetInstance, varDef->name, val, access.arrayIndex);
        else
            VMBuiltins_setVariable(ctx, targetInstance, varDef->builtinVarId, varDef->name, val, access.arrayIndex);

#ifdef ENABLE_VM_TRACING
        if (instanceType == INSTANCE_GLOBAL) {
            VM_checkIfVariableShouldBeTracedAndLog(ctx, "global", nullptr, varDef->name, val, true, access.arrayIndex, -1, " (builtin)");
        } else if (targetInstance != nullptr && targetInstance->objectIndex >= 0) {
            VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, targetInstance), "self", varDef->name, val, true, access.arrayIndex, targetInstance->instanceId, " (builtin)");
        }
#endif

        // VMBuiltins_setVariable reads values (toReal, toInt32, etc.) but does not take ownership
        RValue_free(&val);
        return;
    }

    // Resolve the slot pointer for this scope. For INSTANCE_SELF we materialise a sparse selfVars entry if it doesn't exist so VM_arraySetWithCoW has a stable slot to own.
    RValue* slot = nullptr;
    switch (instanceType) {
        case INSTANCE_LOCAL: {
            uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
            require(ctx->currentCallFrame->localVarsCount > localSlot);
            slot = &ctx->currentCallFrame->localVars[localSlot];
            break;
        }
        case INSTANCE_SELF:
        case INSTANCE_GLOBAL:
        default: {
            Instance* inst = targetInstance;
            if (instanceType == INSTANCE_GLOBAL)
                inst = ctx->globalScopeInstance;

            if (inst == nullptr) {
                const char* varTypeName = varTypeToString((varRef >> 24) & 0xF8);
                char* valAsString = RValue_toString(val, ctx->runner->dataWin);
                logWarn("VM: [%s] Write on self var '%s' but no current instance (instanceType=%d, varType=%s, isArray=%s, originalInstanceType=%d, hasInstanceType=%s, varID=%d, value=%s)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, instanceType, varTypeName, access.isArray ? "true" : "false", originalInstanceType, access.hasInstanceType ? "true" : "false", varDef->varID, valAsString);
                free(valAsString);
                RValue_free(&val);
                return;
            }
            slot = IntRValueHashMap_getOrInsertUndefined(&inst->selfVars, varDef->varID);
            break;
        }
    }

    // Array write via VM_arraySetWithCoW.
    if (access.isArray) {
        VM_arraySetWithCoW(ctx, slot, access.arrayIndex, val);
#ifdef ENABLE_VM_TRACING
        const char* scopeName =
            instanceType == INSTANCE_LOCAL ? "local" :
            instanceType == INSTANCE_GLOBAL ? "global" :
            (targetInstance != nullptr ? instanceObjectName(ctx, targetInstance) : "self");
        const char* altName = (instanceType == INSTANCE_SELF || instanceType >= 0 || instanceType == INSTANCE_OTHER) ? "self" : nullptr;
        VM_checkIfVariableShouldBeTracedAndLog(ctx, scopeName, altName, varDef->name, val, true, access.arrayIndex, -1, "");
#endif
        RValue_free(&val);
        return;
    }

    switch (instanceType) {
        case INSTANCE_LOCAL: {
            uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
            require(ctx->currentCallFrame->localVarsCount > localSlot);
            RValue_writeIntoSlotStealingOwnershipOrCopying(&ctx->currentCallFrame->localVars[localSlot], val);
            return;
        }
        case INSTANCE_SELF:
        case INSTANCE_GLOBAL:
        default: {
            // Self or object/instance reference - use sparse hashmap
            Instance* inst = targetInstance;
            if (instanceType == INSTANCE_GLOBAL)
                inst = ctx->globalScopeInstance;
            Instance_setSelfVar(inst, varDef->varID, val);
#ifdef ENABLE_VM_TRACING
            {
                RValue written = Instance_getSelfVar(inst, varDef->varID);
                VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, written, true, -1, inst->instanceId, "");
            }
#endif
            // Instance_setSelfVar always copies strings, so free the original
            RValue_free(&val);
            return;
        }
    }
}

// ===[ Type Conversion ]===

static RValue convertValue(RValue val, uint8_t targetType, DataWin* dataWin) {
    switch (targetType) {
        case GML_TYPE_DOUBLE:
            return RValue_makeReal(RValue_toReal(val));
        case GML_TYPE_FLOAT:
            return RValue_makeReal((GMLReal) (float) RValue_toReal(val));
        case GML_TYPE_INT32:
            return RValue_makeInt32(RValue_toInt32(val));
        case GML_TYPE_INT64:
            return RValue_makeInt64(RValue_toInt64(val));
        case GML_TYPE_BOOL:
            return RValue_makeBool(RValue_toBool(val));
        case GML_TYPE_STRING: {
            char* str = RValue_toString(val, dataWin);
            return RValue_makeOwnedString(str);
        }
        case GML_TYPE_VARIABLE:
            // Variable type on stack is just an RValue passthrough
            return val;
        default:
            logWarn("VM: Unknown target type 0x%X for conversion\n", targetType);
            return val;
    }
}

// ===[ Opcode Handlers ]===

static void handlePush(VMContext* ctx, OpWord opword, const uint8_t* extraData, uint8_t type1) {
    switch (type1) {
        case GML_TYPE_DOUBLE:
            stackPushTyped(ctx, RValue_makeReal(BinaryUtils_readFloat64Aligned(extraData)), GML_TYPE_DOUBLE);
            break;
        case GML_TYPE_FLOAT:
            // Native push.f reads a 4-byte float; the bytecode-declared footprint is FLOAT (4 bytes), not DOUBLE (8).
            stackPushTyped(ctx, RValue_makeReal((GMLReal) BinaryUtils_readFloat32Aligned(extraData)), GML_TYPE_FLOAT);
            break;
        case GML_TYPE_INT32:
            stackPushTyped(ctx, RValue_makeInt32(BinaryUtils_readInt32Aligned(extraData)), GML_TYPE_INT32);
            break;
        case GML_TYPE_INT64:
            stackPushTyped(ctx, RValue_makeInt64(BinaryUtils_readInt64Aligned(extraData)), GML_TYPE_INT64);
            break;
        case GML_TYPE_BOOL:
            stackPushTyped(ctx, RValue_makeBool(BinaryUtils_readInt32Aligned(extraData) != 0), GML_TYPE_BOOL);
            break;
        case GML_TYPE_VARIABLE: {
            int32_t instanceType = (int32_t) OpWord_extra(opword);
            uint32_t varRef = resolveVarOperand(extraData);
            uint8_t varType = (varRef >> 24) & 0xF8;
            // BC17: VARTYPE_INSTANCE encodes (instanceId - INSTANCE_ID_BASE) in the instruction's lower 16 bits.
            // Add INSTANCE_ID_BASE back so findInstanceByTarget sees the real runtime instance ID.
            if (varType == VARTYPE_INSTANCE) instanceType += INSTANCE_ID_BASE;
#if IS_WAD17_OR_HIGHER_ENABLED
            if (varType == VARTYPE_ARRAYPUSHAF || varType == VARTYPE_ARRAYPOPAF) {
                // V17: multi-dim first-step. Stack has [scope, firstIndex] (with an optional real-instance slot underneath when scope == -9 INSTANCE_STACKTOP).
                // We resolve the variable's top-level array slot, materialise it if needed, then drill into arr->data[firstIndex] (materialising a sub-array there too).
                // The sub-array is pushed as a weak ref; subsequent BREAK_PUSHAC/PUSHAF/POPAF consume it.
                Variable* varDef = resolveVarDef(ctx, varRef);
                int32_t firstIndex = stackPopInt32(ctx);
                int32_t scope = stackPopInt32(ctx);
                if (IS_WAD17_OR_HIGHER(ctx) && scope == INSTANCE_STACKTOP) {
                    scope = resolveInstanceStackTop(ctx);
                }

                // Built-in variables do NOT live in selfVars: argument[] is the call frame, not a
                // member. Falling through would call getOrInsertUndefined, find nothing, materialise
                // a FRESH EMPTY array in that slot and drill into it -- so argument[0][i] came back
                // undefined while argument[0] (which goes through resolveVariableRead) was correct.
                // Read the real value and hand out a weak ref to it instead.
                if (varDef->builtinVarId >= 0) {
                    Instance* builtinInst = (scope == INSTANCE_OTHER && ctx->currentCallFrame->other != nullptr)
                        ? (Instance*) ctx->currentCallFrame->other
                        : (scope == INSTANCE_GLOBAL) ? ctx->globalScopeInstance
                        : (scope >= 0) ? VM_findInstanceByTarget(ctx, scope)
                        : (Instance*) ctx->currentCallFrame->self;
                    RValue builtinVal = VMBuiltins_getVariable(ctx, builtinInst, varDef->builtinVarId, varDef->name, firstIndex);
                    // Only a borrowed array can become a weak ref: freeing an owned one here would
                    // leave the stack pointing at released memory. Anything else keeps the old path,
                    // so this cannot turn a working case into an abort.
                    if (builtinVal.type == RVALUE_ARRAY && builtinVal.array != nullptr && !builtinVal.ownsReference) {
                        stackPush(ctx, RValue_makeArrayWeak(builtinVal.array));
                        break;
                    }
                    RValue_free(&builtinVal);
                }

                // Resolve the slot for this scope.
                RValue* slot = nullptr;
                switch (scope) {
                    case INSTANCE_LOCAL: {
                        uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
                        require(ctx->currentCallFrame->localVarsCount > localSlot);
                        slot = &ctx->currentCallFrame->localVars[localSlot];
                        break;
                    }
                    case INSTANCE_SELF:
                    case INSTANCE_OTHER:
                    case INSTANCE_GLOBAL: {
                        Instance* inst = scope == INSTANCE_GLOBAL ? ctx->globalScopeInstance
                            : (scope == INSTANCE_OTHER && ctx->currentCallFrame->other != nullptr)
                            ? (Instance*) ctx->currentCallFrame->other
                            : (Instance*) ctx->currentCallFrame->self;
                        require(inst != nullptr);
                        slot = IntRValueHashMap_getOrInsertUndefined(&inst->selfVars, varDef->varID);
                        break;
                    }
                    default: {
                        // Negative pseudo-scopes not handled above resolve to the current instance, mirroring handlePushBltn.
                        // Positive scopes are real instance IDs resolved by lookup.
                        Instance* inst = (0 > scope) ? (Instance*) ctx->currentCallFrame->self : VM_findInstanceByTarget(ctx, scope);
                        if (inst == nullptr) {
                            logError("VM: ARRAYPUSHAF: no instance for scope %d varID=%d\n", scope, varDef->varID);
                            abort();
                        }
                        slot = IntRValueHashMap_getOrInsertUndefined(&inst->selfVars, varDef->varID);
                        break;
                    }
                }

                bool forWrite = (varType == VARTYPE_ARRAYPOPAF);
                // Materialise the top-level array in the slot if needed.
                if (slot->type != RVALUE_ARRAY || slot->array == nullptr) {
                    RValue_free(slot);
                    GMLArray* fresh = GMLArray_create(ctx->dataWin, 0);
                    fresh->owner = IS_WAD17_OR_HIGHER(ctx) ? ctx->currentArrayOwner : (void*) slot;
                    *slot = RValue_makeArray(fresh);
                } else if (forWrite) {
                    cowForkSlotForModificationIfNeeded(ctx, slot);
                }
                GMLArray* top = slot->array;
                GMLArray_growTo(top, firstIndex + 1);
                RValue* topSlot = GMLArray_slot(top, firstIndex);
                // Materialise the sub-array at [firstIndex] if it's not already an array.
                if (topSlot->type != RVALUE_ARRAY || topSlot->array == nullptr) {
                    RValue_free(topSlot);
                    GMLArray* sub = GMLArray_create(ctx->dataWin, 0);
                    sub->owner = top->owner;
                    *topSlot = RValue_makeArray(sub);
                } else if (forWrite) {
                    cowForkSlotForModificationIfNeeded(ctx, topSlot);
                }
                // Push a weak ref to the sub-array — short-lived, consumed by the next BREAK op.
                stackPush(ctx, RValue_makeArrayWeak(topSlot->array));
            } else
#endif
            {
                RValue val = resolveVariableRead(ctx, instanceType, varRef);
                // Mark as variable-width (16 bytes on native stack) regardless of the RValue's actual type
                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
            }
            break;
        }
        case GML_TYPE_STRING: {
            int32_t stringIndex = BinaryUtils_readInt32Aligned(extraData);
            require(stringIndex >= 0 && ctx->dataWin->strg.count > (uint32_t) stringIndex);
            stackPush(ctx, RValue_makeString(ctx->dataWin->strg.strings[stringIndex]));
            break;
        }
        case GML_TYPE_INT16: {
            int16_t value = OpWord_extra(opword);
            RValue val = RValue_makeInt32((int32_t) value);
            stackPushTyped(ctx, val, GML_TYPE_INT16);
            break;
        }
        default:
            logError("VM: Push with unknown type 0x%X\n", type1);
            abort();
    }
}

#if IS_WAD17_OR_HIGHER_ENABLED
// For V17+ VARTYPE_ARRAYPUSHAF/POPAF on a top-level variable: return the slot's GMLArray*,
// materialising a fresh empty one in the slot if it isn't an array yet. Used by PushLoc/Glb/Bltn.
// Pushes a weak ref onto the stack — short-lived, consumed by the next BREAK_PUSHAC/PUSHAF/POPAF.
// forWrite (VARTYPE_ARRAYPOPAF) chains end in a BREAK_POPAF store, so the slot must be CoW-forked up front.
static void pushTopLevelArrayRef(VMContext* ctx, RValue* slot, bool forWrite) {
    if (slot->type != RVALUE_ARRAY || slot->array == nullptr) {
        RValue_free(slot);
        GMLArray* fresh = GMLArray_create(ctx->dataWin, 0);
        fresh->owner = IS_WAD17_OR_HIGHER(ctx) ? ctx->currentArrayOwner : (void*) slot;
        *slot = RValue_makeArray(fresh);
    } else if (forWrite) {
        cowForkSlotForModificationIfNeeded(ctx, slot);
    }
    stackPush(ctx, RValue_makeArrayWeak(slot->array));
}
#endif

static void handlePushBuiltin(VMContext* ctx, OpWord opword, const uint8_t* extraData) {
    uint32_t varRef = resolveVarOperand(extraData);
#if IS_WAD17_OR_HIGHER_ENABLED
    uint8_t varType = (varRef >> 24) & 0xF8;
    if (varType == VARTYPE_ARRAYPUSHAF || varType == VARTYPE_ARRAYPOPAF) {
        Variable* varDef = resolveVarDef(ctx, varRef);
        int32_t scope = OpWord_extra(opword);
        Instance* inst = nullptr;
        if (scope == INSTANCE_SELF || scope == -1) {
            inst = (Instance*) ctx->currentCallFrame->self;
        } else if (scope == INSTANCE_OTHER && ctx->currentCallFrame->other != nullptr) {
            inst = (Instance*) ctx->currentCallFrame->other;
        } else if (scope >= 0) {
            inst = VM_findInstanceByTarget(ctx, scope);
        } else {
            inst = (Instance*) ctx->currentCallFrame->self;
        }
        if (inst == nullptr) {
            logError("VM: PushBltn ARRAYPUSHAF: no instance for scope %d varID=%d\n", scope, varDef->varID);
            abort();
        }
        // Same trap as in handlePush: a built-in has no selfVars slot, and materialising one there
        // would replace the real value with an empty array. PushBltn only ever names built-ins, but
        // the index of the first step is NOT on the stack here -- this opcode form carries no array
        // index, so read the variable whole (arrayIndex -1) and hand out a weak ref to it.
        if (varDef->builtinVarId >= 0) {
            RValue builtinVal = VMBuiltins_getVariable(ctx, inst, varDef->builtinVarId, varDef->name, -1);
            if (builtinVal.type == RVALUE_ARRAY && builtinVal.array != nullptr && !builtinVal.ownsReference) {
                stackPush(ctx, RValue_makeArrayWeak(builtinVal.array));
                return;
            }
            RValue_free(&builtinVal);
        }
        RValue* slot = IntRValueHashMap_getOrInsertUndefined(&inst->selfVars, varDef->varID);
        pushTopLevelArrayRef(ctx, slot, varType == VARTYPE_ARRAYPOPAF);
        return;
    }
#endif
    RValue val = resolveVariableRead(ctx, OpWord_extra(opword), varRef);
    stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
}

static void handlePushImmediate(VMContext* ctx, OpWord opword) {
    int16_t value =  OpWord_extra(opword);
    RValue val = RValue_makeInt32((int32_t) value);
    stackPushTyped(ctx, val, GML_TYPE_INT16);
}

// When storing into a variant variable from an int32/int64 stack source, coerce to real.
// GMS variables normalize integer literals to doubles so subsequent arithmetic routes through the real fast path instead of int32 x int32 wrapping.
static inline RValue coerceIntStoreToReal(RValue val, uint8_t type2) {
    if (type2 == GML_TYPE_INT32 || type2 == GML_TYPE_INT64 || type2 == GML_TYPE_INT16) {
        if (val.type == RVALUE_INT32) {
            return RValue_makeReal((GMLReal) val.int32);
        }
#ifndef NO_RVALUE_INT64
        if (val.type == RVALUE_INT64) {
            return RValue_makeReal((GMLReal) val.int64);
        }
#endif
    }
    return val;
}

static void handlePop(VMContext* ctx, uint8_t type1, uint8_t type2, uint32_t varRef, uint8_t varType, int32_t instanceType) {
    RValue val;
    int32_t arrayIndex = -1;

    int32_t originalInstanceType = instanceType;
    if (varType == VARTYPE_ARRAY) {
        if (type1 == GML_TYPE_VARIABLE) {
            // Simple assignment (Pop.v.v): stack bottom-to-top = [value, (realInstance,) instanceType, arrayIndex]
            arrayIndex = stackPopInt32(ctx);
            instanceType = stackPopInt32(ctx);

            // BC17: -9 (INSTANCE_STACKTOP) means "pop again for the real instance ID/object index" (e.g. `su_actor.specialsprite[0] = ...`)
            if (IS_WAD17_OR_HIGHER(ctx) && instanceType == INSTANCE_STACKTOP) {
                instanceType = resolveInstanceStackTop(ctx);
            }

            val = stackPop(ctx);
        } else {
            // Compound assignment (Pop.i.v, etc.): stack bottom-to-top = [(realInstance,) instanceType, arrayIndex, value]
            val = stackPop(ctx);

            arrayIndex = stackPopInt32(ctx);
            instanceType = stackPopInt32(ctx);

            // BC17: -9 (INSTANCE_STACKTOP) means "pop again for the real instance ID/object index"
            if (IS_WAD17_OR_HIGHER(ctx) && instanceType == INSTANCE_STACKTOP) {
                instanceType = resolveInstanceStackTop(ctx);
            }
        }
    } else if (varType == VARTYPE_STACKTOP && type1 == GML_TYPE_VARIABLE) {
        // Simple assignment (Pop.v.v) with STACKTOP: stack bottom-to-top = [value, instanceType]
        // Pop instanceType first (top), then value (bottom)
        instanceType = stackPopInt32(ctx);

        // BC17: -9 (INSTANCE_STACKTOP) means "pop again for the real instance type"
        if (IS_WAD17_OR_HIGHER(ctx) && instanceType == INSTANCE_STACKTOP) {
            instanceType = resolveInstanceStackTop(ctx);
        }

        val = stackPop(ctx);

        // Clear STACKTOP type bits so resolveVariableWrite's popArrayAccess won't double-pop
        varRef = (varRef & 0x07FFFFFF) | ((uint32_t) VARTYPE_NORMAL << 24);
    } else {
        val = stackPop(ctx);
    }

    // Convert if source type differs from destination type.
    // For VARTYPE_ARRAY compound assignments (type1 != GML_TYPE_VARIABLE), the type1 field
    // indicates the stack layout (compound vs simple), NOT a type conversion target.
    // Skip conversion in that case to preserve string values through += operations.
    // For compound assignments (type1 != GML_TYPE_VARIABLE) with VARTYPE_ARRAY or VARTYPE_STACKTOP,
    // the type1 field indicates the stack layout (compound vs simple), NOT a type conversion target.
    // Skip conversion to preserve the actual computed value (e.g. g.image_angle -= 4.5 must not truncate to int).
    bool isCompoundAssignment = ((varType == VARTYPE_ARRAY || varType == VARTYPE_STACKTOP) && type1 != GML_TYPE_VARIABLE);
    if (type2 != type1 && type1 != GML_TYPE_VARIABLE && !isCompoundAssignment) {
        RValue converted = convertValue(val, type1, ctx->runner->dataWin);
        RValue_free(&val);
        val = converted;
    }

    if (type1 == GML_TYPE_VARIABLE && !isCompoundAssignment) {
        val = coerceIntStoreToReal(val, type2);
    }

    if (varType == VARTYPE_ARRAY) {
        Variable* varDef = resolveVarDef(ctx, varRef);
        if (varDef->varID == VARIABLE_BUILTIN) {
            // Resolve target instance for built-in array variable writes
            if (instanceType >= 0) {
                if (INSTANCE_ID_BASE > instanceType) {
                    // Object reference: write to ALL instances of that object. The setter can run user code, so iterate a snapshot of the bucket.
                    Runner* runner = (Runner*) ctx->runner;
                    int32_t snapBase = Runner_pushInstancesOfObject(runner, instanceType);
                    int32_t snapEnd  = (int32_t) arrlen(runner->instanceSnapshots);
                    for (int32_t i = snapBase; snapEnd > i; i++) {
                        Instance* inst = runner->instanceSnapshots[i];
                        if (!inst->active)
                            continue;

                        VMBuiltins_setVariable(ctx, inst, varDef->builtinVarId, varDef->name, val, arrayIndex);
#ifdef ENABLE_VM_TRACING
                        VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, val, true, arrayIndex, inst->instanceId, " (builtin, all-instances object write)");
#endif
                    }
                    Runner_popInstanceSnapshot(runner, snapBase);
                } else {
                    // Instance ID reference
                    Instance* target = VM_findInstanceByTarget(ctx, instanceType);
                    if (target != nullptr) {
                        VMBuiltins_setVariable(ctx, target, varDef->builtinVarId, varDef->name, val, arrayIndex);
#ifdef ENABLE_VM_TRACING
                        VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, target), "self", varDef->name, val, true, arrayIndex, target->instanceId, " (builtin)");
#endif
                    }
                }
            } else {
                Instance* targetInstance = instanceType == INSTANCE_OTHER && ctx->currentCallFrame->other != nullptr ? ctx->currentCallFrame->other : ctx->currentCallFrame->self;
                VMBuiltins_setVariable(ctx, targetInstance, varDef->builtinVarId, varDef->name, val, arrayIndex);
#ifdef ENABLE_VM_TRACING
                VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, targetInstance), "self", varDef->name, val, true, arrayIndex, targetInstance->instanceId, " (builtin)");
#endif
            }
        } else {
            // Resolve slot for this scope: VM_arraySetWithCoW handles CoW + materialisation + grow.
            RValue* slot = nullptr;
            switch (instanceType) {
                case INSTANCE_LOCAL: {
                    uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
                    require(ctx->currentCallFrame->localVarsCount > localSlot);
                    slot = &ctx->currentCallFrame->localVars[localSlot];
                    break;
                }
                case INSTANCE_SELF:
                case INSTANCE_GLOBAL:
                default: {
                    struct Instance* inst = (struct Instance*) ctx->currentCallFrame->self;
                    if (instanceType >= 0) {
                        inst = VM_findInstanceByTarget(ctx, instanceType);
                        if (inst == nullptr) {
                            const char* varTypeName = varTypeToString(varType);
                            char* valAsString = RValue_toString(val, ctx->runner->dataWin);
                            if (instanceType < INSTANCE_ID_BASE && (uint32_t) instanceType < ctx->dataWin->objt.count) {
                                logWarn("VM: [%s] WRITE array var '%s[%d]' on object index %d (%s) but no instance found (varType=%s, originalInstanceType=%d, varID=%d, value=%s)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, arrayIndex, instanceType, ctx->dataWin->objt.objects[instanceType].name, varTypeName, originalInstanceType, varDef->varID, valAsString);
                            } else {
                                logWarn("VM: [%s] WRITE array var '%s[%d]' on instance %d but no instance found (varType=%s, originalInstanceType=%d, varID=%d, value=%s)\n", VM_getCurrentCallFrameCodeName(ctx), varDef->name, arrayIndex, instanceType, varTypeName, originalInstanceType, varDef->varID, valAsString);
                            }
                            free(valAsString);
                            RValue_free(&val);
                            return;
                        }
                    } else if (instanceType == INSTANCE_OTHER && ctx->currentCallFrame->other != nullptr) {
                        inst = (Instance*) ctx->currentCallFrame->other;
                    } else if (instanceType == INSTANCE_GLOBAL) {
                        inst = ctx->globalScopeInstance;
                    }
                    if (inst == nullptr) {
                        RValue_free(&val);
                        return;
                    }
                    slot = IntRValueHashMap_getOrInsertUndefined(&inst->selfVars, varDef->varID);
                    break;
                }
            }
            if (slot != nullptr) {
                VM_arraySetWithCoW(ctx, slot, arrayIndex, val);
#ifdef ENABLE_VM_TRACING
                bool isSelfScope = (instanceType != INSTANCE_LOCAL && instanceType != INSTANCE_GLOBAL);
                const char* scopeName = instanceType == INSTANCE_LOCAL ? "local" : instanceType == INSTANCE_GLOBAL ? "global" : "self";
                VM_checkIfVariableShouldBeTracedAndLog(ctx, scopeName, isSelfScope ? nullptr : "self", varDef->name, val, true, arrayIndex, -1, "");
#endif
            }
            RValue_free(&val);
        }
    } else {
        resolveVariableWrite(ctx, instanceType, varRef, val);
    }
}

static void handlePopz(VMContext* ctx) {
    RValue val = stackPop(ctx);
    RValue_free(&val);
}

NOINLINE
static void handleAddString(VMContext* ctx, RValue a, RValue b, uint8_t resultType) {
    if (a.type == RVALUE_STRING && b.type == RVALUE_STRING) {
        // String concatenation
        const char* sa = a.string != nullptr ? a.string : "";
        const char* sb = b.string != nullptr ? b.string : "";
        size_t lenA = strlen(sa);
        size_t lenB = strlen(sb);
        char* result = (char *)safeMalloc(lenA + lenB + 1);
        memcpy(result, sa, lenA);
        memcpy(result + lenA, sb, lenB + 1);
        RValue_free(&a);
        RValue_free(&b);
        stackPushTyped(ctx, RValue_makeOwnedString(result), resultType);
    } else {
        // For anything else, we'll convert to numbers and then sum
#ifndef NO_RVALUE_INT64
        if (a.type == RVALUE_INT64 || b.type == RVALUE_INT64) {
            int64_t result = (a.type == RVALUE_STRING) ? (int64_t) GMLReal_strtod(a.string, nullptr) : a.int64;
            result += (b.type == RVALUE_STRING) ? (int64_t) GMLReal_strtod(b.string, nullptr) : b.int64;
            RValue_free(&a);
            RValue_free(&b);
            stackPushTyped(ctx, RValue_makeInt64(result), resultType);
            return;
        }
#endif
        if (a.type == RVALUE_INT32 || b.type == RVALUE_INT32) {
            int32_t result = (a.type == RVALUE_STRING) ? (int32_t) GMLReal_strtod(a.string, nullptr) : a.int32;
            result += (b.type == RVALUE_STRING) ? (int32_t) GMLReal_strtod(b.string, nullptr) : b.int32;
            RValue_free(&a);
            RValue_free(&b);
            stackPushTyped(ctx, RValue_makeInt32(result), resultType);
            return;
        }
        GMLReal result = RValue_toReal(a) + RValue_toReal(b);
        RValue_free(&a);
        RValue_free(&b);
        stackPushTyped(ctx, RValue_makeReal(result), resultType);
    }
}

NOINLINE
static void handleMulString(VMContext* ctx, RValue a, RValue b, uint8_t resultType) {
    // a.type == RVALUE_STRING; b is the repetition count.
    int count = RValue_toInt32(b);
    const char* str = a.string != nullptr ? a.string : "";
    size_t len = strlen(str);
    if (0 >= count || len == 0) {
        RValue_free(&a);
        RValue_free(&b);
        stackPushTyped(ctx, RValue_makeOwnedString(safeStrdup("")), resultType);
    } else {
        char* result = (char *)safeMalloc(len * count + 1);
        repeat(count, i) {
            memcpy(result + i * len, str, len);
        }
        result[len * count] = '\0';
        RValue_free(&a);
        RValue_free(&b);
        stackPushTyped(ctx, RValue_makeOwnedString(result), resultType);
    }
}

static void handleDiv(VMContext* ctx, OpWord opword) {
    RValue b = stackPop(ctx);
    RValue a = stackPop(ctx);
    uint8_t type1 = OpWord_type1(opword);
    uint8_t type2 = OpWord_type2(opword);
    GMLReal divisor = RValue_toReal(b);
    // In GameMaker's native runner, ONLY integer/integer division throws a hard error on zero, float/variable types rely on IEEE 754 (produces NaN)
    if ((type1 == GML_TYPE_INT32 || type1 == GML_TYPE_INT64) && (type2 == GML_TYPE_INT32 || type2 == GML_TYPE_INT64)) {
        requireMessageFormatted(__FILE__, __LINE__, divisor != 0.0, "VM: [%s] DoDiv :: Divide by zero", VM_getCurrentCallFrameCodeName(ctx));
    }
    GMLReal result = RValue_toReal(a) / divisor;
    RValue_free(&a);
    RValue_free(&b);
    stackPushTyped(ctx, RValue_makeReal(result), type2);
}

static void handleRem(VMContext* ctx, OpWord opword) {
    RValue b = stackPop(ctx);
    RValue a = stackPop(ctx);
    int64_t divisor = RValue_toInt64(b);
    requireMessageFormatted(__FILE__, __LINE__, divisor != 0, "VM: [%s] DoRem :: Divide by zero", VM_getCurrentCallFrameCodeName(ctx));
    int64_t result = RValue_toInt64(a) / divisor;
    RValue_free(&a);
    RValue_free(&b);
    stackPushTyped(ctx, RValue_makeInt64(result), OpWord_type2(opword));
}

static void handleMod(VMContext* ctx, OpWord opword) {
    RValue b = stackPop(ctx);
    RValue a = stackPop(ctx);
    GMLReal divisor = RValue_toReal(b);
    requireMessageFormatted(__FILE__, __LINE__, divisor != 0.0, "VM: [%s] DoMod :: Divide by zero", VM_getCurrentCallFrameCodeName(ctx));
    GMLReal result = GMLReal_fmod(RValue_toReal(a), divisor);
    RValue_free(&a);
    RValue_free(&b);
    stackPushTyped(ctx, RValue_makeReal(result), OpWord_type2(opword));
}

#define SIMPLE_BYTECODE_BITWISE_OPERATION(op) \
    int32_t b = stackPopInt32(ctx); \
    int32_t a = stackPopInt32(ctx); \
    int32_t result = a op b; \
    stackPushTyped(ctx, RValue_makeInt32(result), OpWord_type2(opword))

static void handleAnd(VMContext* ctx, OpWord opword) {
    SIMPLE_BYTECODE_BITWISE_OPERATION(&);
}

static void handleOr(VMContext* ctx, OpWord opword) {
    SIMPLE_BYTECODE_BITWISE_OPERATION(|);
}

static void handleXor(VMContext* ctx, OpWord opword) {
    SIMPLE_BYTECODE_BITWISE_OPERATION(^);
}

static void handleNeg(VMContext* ctx, OpWord opword) {
    RValue a = stackPop(ctx);
    GMLReal result = -RValue_toReal(a);
    RValue_free(&a);
    stackPushTyped(ctx, RValue_makeReal(result), OpWord_type1(opword));
}

static void handleNot(VMContext* ctx, OpWord opword) {
    uint8_t resultType = OpWord_type1(opword);
    int32_t a = stackPopInt32(ctx);
    if (GML_TYPE_BOOL == resultType) {
        // Logical NOT: compiler emits this for the ! operator on boolean expressions
        int32_t result = (a == 0) ? 1 : 0;
        stackPushTyped(ctx, RValue_makeBool(result != 0), resultType);
    } else {
        // Bitwise NOT: used for ~ operator on integer types
        int32_t result = ~a;
        stackPushTyped(ctx, RValue_makeInt32(result), resultType);
    }
}

static void handleShl(VMContext* ctx, OpWord opword) {
    SIMPLE_BYTECODE_BITWISE_OPERATION(<<);
}

static void handleShr(VMContext* ctx, OpWord opword) {
    SIMPLE_BYTECODE_BITWISE_OPERATION(>>);
}

static void handleConv(VMContext* ctx, uint8_t srcType, uint8_t dstType, uint8_t convKey) {
    RValue val = stackPop(ctx);

    RValue result;

    switch (convKey) {
        // Identity conversions (no-op)
        case 0x00: case 0x22: case 0x33: case 0x44: case 0x66:
            result = val;
            break;

        // Double (0) -> other
        case 0x20: result = RValue_makeInt32((int32_t) val.real); break;
        case 0x30: result = RValue_makeInt64((int64_t) val.real); break;
        case 0x40: result = RValue_makeBool(val.real > 0.5); break;
        case 0x50: result = val; break; // Double -> Variable (passthrough)
        case 0x60: { char* s = RValue_toString(val, ctx->runner->dataWin); result = RValue_makeOwnedString(s); break; }
        case 0xF0: result = RValue_makeInt32((int32_t) val.real); break;

        // Float (1) -> other (float stored as double in our RValue)
        case 0x01: result = RValue_makeReal(val.real); break;
        case 0x21: result = RValue_makeInt32((int32_t) val.real); break;
        case 0x31: result = RValue_makeInt64((int64_t) val.real); break;
        case 0x41: result = RValue_makeBool(val.real > 0.5); break;
        case 0x51: result = val; break; // Float -> Variable (passthrough)

        // Int32 (2) -> other
        case 0x02: result = RValue_makeReal((GMLReal) val.int32); break;
        case 0x12: result = RValue_makeReal((GMLReal) val.int32); break;
        case 0x32: result = RValue_makeInt64((int64_t) val.int32); break;
        case 0x42: result = RValue_makeBool(val.int32 > 0); break;
        case 0x52: result = val; break; // Int32 -> Variable (passthrough)
        case 0x62: { char* s = RValue_toString(val, ctx->runner->dataWin); result = RValue_makeOwnedString(s); break; }
        case 0xF2: result = val; break;

#ifndef NO_RVALUE_INT64
        // Int64 (3) -> other
        case 0x03: result = RValue_makeReal((GMLReal) val.int64); break;
        case 0x23: result = RValue_makeInt32((int32_t) val.int64); break;
        case 0x43: result = RValue_makeBool(val.int64 > 0); break;
        case 0x53: result = val; break; // Int64 -> Variable (passthrough)
#elif IS_WAD17_OR_HIGHER_ENABLED
        // Int64 (3) -> other (Int64 stored as Int32 when NO_RVALUE_INT64).
        // Only emitted on BC17+ builds: BC16 games (Undertale, SURVEY_PROGRAM) never emit Int64 Conv opcodes.
        case 0x03: result = RValue_makeReal((GMLReal) val.int32); break;
        case 0x23: result = val; break; // Already Int32
        case 0x43: result = RValue_makeBool(val.int32 > 0); break;
        case 0x53: result = val; break; // Int64 -> Variable (passthrough)
#endif

        // Bool (4) -> other
        case 0x04: result = RValue_makeReal((GMLReal) val.int32); break;
        case 0x24: result = RValue_makeInt32(val.int32); break;
        case 0x34: result = RValue_makeInt64((int64_t) val.int32); break;
        case 0x54: result = val; break; // Bool -> Variable (passthrough)
        case 0x64: { char* s = RValue_toString(val, ctx->runner->dataWin); result = RValue_makeOwnedString(s); break; }

        // Variable (5) -> other
        case 0x05: result = RValue_makeReal(RValue_toReal(val)); break;
        case 0x15: result = RValue_makeReal(RValue_toReal(val)); break;
        case 0x25: result = RValue_makeInt32(RValue_toInt32(val)); break;
        case 0x35: result = RValue_makeInt64(RValue_toInt64(val)); break;
        case 0x45: result = RValue_makeBool(RValue_toBool(val)); break;
        case 0x55: result = val; break; // Variable -> Variable (identity)
        case 0x65: { char* s = RValue_toString(val, ctx->runner->dataWin); result = RValue_makeOwnedString(s); break; }
        case 0xF5: result = RValue_makeInt32(RValue_toInt32(val)); break;

        // String (6) -> other
        case 0x06: result = RValue_makeReal(GMLReal_strtod(val.string, nullptr)); break;
        case 0x26: result = RValue_makeInt32((int32_t) GMLReal_strtod(val.string, nullptr)); break;
        case 0x36: result = RValue_makeInt64((int64_t) GMLReal_strtod(val.string, nullptr)); break;
        case 0x46: result = RValue_makeBool(val.string != nullptr && val.string[0] != '\0'); break;
        case 0x56: {
            // String -> Variable: keep as-is since our RValue handles strings natively
            result = val;
            break;
        }

        // Int16 (F) -> other
        case 0x0F: result = RValue_makeReal((GMLReal) val.int32); break;
        case 0x2F: result = val; break;
        case 0x5F: result = val; break;

        default:
            logWarn("VM: [%s] Conv unhandled conversion 0x%02X (src=0x%X dst=0x%X)\n", VM_getCurrentCallFrameCodeName(ctx), convKey, srcType, dstType);
            result = val;
            break;
    }

    // Don't free the old value if we're returning the same value (identity conversion or passthrough)
    if (result.string != val.string || result.type != val.type) {
        RValue_free(&val);
    }

    // Set gmlStackType to the bytecode-declared destination type so Dup and other byte-level walks see the correct native footprint.
    result.gmlStackType = dstType;
    stackPush(ctx, result);
}

// Tries to parse a string as a real number, mirroring HTML5 yyCompareVal's behavior:
// trim leading whitespace, then accept a numeric prefix (sign, digits, decimal, exponent).
// Returns true on success, with the parsed value written to *out.
static bool tryParseRealFromString(const char* str, GMLReal* out) {
    if (str == nullptr) return false;
    while (*str == ' ' || *str == '\t' || *str == '\n' || *str == '\r') str++;
    if (*str == '\0') return false;
    char* endPtr = nullptr;
    GMLReal value = GMLReal_strtod(str, &endPtr);
    if (endPtr == str) return false;
    *out = value;
    return true;
}

static void handleCmp(VMContext* ctx, OpWord opword) {
    uint8_t cmpKind = OpWord_comparisonFunction(opword);
    RValue b = stackPop(ctx);
    RValue a = stackPop(ctx);

    bool result;
    if (a.type == RVALUE_UNDEFINED || b.type == RVALUE_UNDEFINED) {
        // Undefined is only == to undefined
        switch (cmpKind) {
            case CMPOP_EQUAL:  result = a.type == b.type;  break;
            case CMPOP_NOT_EQUAL: result = a.type != b.type; break;
            case CMPOP_GREATER_THAN: result = false; break;
            case CMPOP_LESS_THAN: result = false; break;
            case CMPOP_GREATER_THAN_OR_EQUAL: result = a.type == b.type; break;
            case CMPOP_LESS_THAN_OR_EQUAL: result = a.type == b.type; break;
            default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
        }
    } else if (a.type == RVALUE_ARRAY || b.type == RVALUE_ARRAY) {
        // Array is only == to the same array
        bool eq = (a.type == RVALUE_ARRAY && b.type == RVALUE_ARRAY) && (a.array == b.array);
        switch (cmpKind) {
            case CMPOP_EQUAL:  result = eq;  break;
            case CMPOP_NOT_EQUAL: result = !eq; break;
            default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
        }
#if IS_WAD17_OR_HIGHER_ENABLED
    } else if (a.type == RVALUE_METHOD || b.type == RVALUE_METHOD) {
        // Method is only == to the same method
        bool eq = (a.type == RVALUE_METHOD && b.type == RVALUE_METHOD) && (a.method == b.method);
        switch (cmpKind) {
            case CMPOP_EQUAL:  result = eq;  break;
            case CMPOP_NOT_EQUAL: result = !eq; break;
            default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
        }
#endif
    } else if (a.type == RVALUE_STRUCT || b.type == RVALUE_STRUCT) {
        // Struct is only == to the same struct (identity comparison)
        bool eq = (a.type == RVALUE_STRUCT && b.type == RVALUE_STRUCT) && (a.structInst == b.structInst);
        switch (cmpKind) {
            case CMPOP_EQUAL:  result = eq;  break;
            case CMPOP_NOT_EQUAL: result = !eq; break;
            default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
        }
    } else if (a.type == RVALUE_STRING && b.type == RVALUE_STRING) {
        int cmp = strcmp(a.string != nullptr ? a.string : "", b.string != nullptr ? b.string : "");
        switch (cmpKind) {
            case CMPOP_LESS_THAN:  result = 0 > cmp; break;
            case CMPOP_LESS_THAN_OR_EQUAL: result = 0 >= cmp; break;
            case CMPOP_EQUAL:  result = cmp == 0; break;
            case CMPOP_NOT_EQUAL: result = cmp != 0; break;
            case CMPOP_GREATER_THAN_OR_EQUAL: result = cmp >= 0; break;
            case CMPOP_GREATER_THAN:  result = cmp > 0; break;
            default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
        }
    } else {
        // Mixed string/number: coerce strings to reals (matching GameMaker-HTML5 yyCompareVal).
        // Don't be fooled, this behavior is not a GameMaker-HTML5 (JavaScript) quirk! Some GameMaker games do use this,
        // such as gml_Object_obj_ch2_scene6_Step_0 in DELTARUNE: Chapter 2, where the c_wait uses a string instead of a number
        //
        // If a string side fails to parse as a number, the values are considered incomparable: false for all comparisons except NEQ.
        bool incomparable = false;
        GMLReal da = 0.0;
        GMLReal db = 0.0;
        if (a.type == RVALUE_STRING) {
            if (!tryParseRealFromString(a.string, &da)) incomparable = true;
        } else {
            da = RValue_toReal(a);
        }
        if (!incomparable) {
            if (b.type == RVALUE_STRING) {
                if (!tryParseRealFromString(b.string, &db)) incomparable = true;
            } else {
                db = RValue_toReal(b);
            }
        }

        if (incomparable) {
            switch (cmpKind) {
                case CMPOP_EQUAL:  result = false; break;
                case CMPOP_NOT_EQUAL: result = true;  break;
                default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
            }
        } else {
            GMLReal diff = da - db;
            // GML uses epsilon-based comparison for all numeric CMP operations.
            // math_set_epsilon() updates the runner's epsilon at runtime.
            GMLReal epsilon = (ctx->runner != NULL) ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
            int cmp = GMLReal_fabs(diff) <= epsilon ? 0 : (diff < 0 ? -1 : 1);
            switch (cmpKind) {
                case CMPOP_LESS_THAN:  result = cmp < 0; break;
                case CMPOP_LESS_THAN_OR_EQUAL: result = cmp <= 0; break;
                case CMPOP_EQUAL:  result = cmp == 0; break;
                case CMPOP_NOT_EQUAL: result = cmp != 0; break;
                case CMPOP_GREATER_THAN_OR_EQUAL: result = cmp >= 0; break;
                case CMPOP_GREATER_THAN:  result = cmp > 0; break;
                default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
            }
        }
    }

    RValue_free(&a);
    RValue_free(&b);
    stackPush(ctx,RValue_makeBool(result));
}

// Converts a native byte count to RValue slot count by walking the stack backwards from a given position.
// Reads each slot's gmlStackType (set at push time) to compute its native footprint.
static int32_t bytesToSlotCount(VMContext* ctx, int32_t nativeBytes, int32_t stackPos) {
    int32_t slots = 0;
    int32_t remaining = nativeBytes;
    while (remaining > 0) {
        slots++;
        require(stackPos >= slots);
        uint8_t slotGmlType = ctx->stack.slots[stackPos - slots].gmlStackType;
        int32_t slotSize = gmlTypeNativeSize(slotGmlType);
        if (slotGmlType == GML_TYPE_VARIABLE && remaining > 0 && remaining < slotSize)
            remaining = 0;
        else
            remaining -= slotSize;
    }
    require(remaining == 0); // Byte count must align exactly to slot boundaries
    return slots;
}

static void handleDup(VMContext* ctx, OpWord opword) {
    uint16_t operand = OpWord_extra(opword);
    uint8_t type1 = OpWord_type1(opword);
    int32_t typeSize = gmlTypeNativeSize(type1);

#if IS_WAD17_OR_HIGHER_ENABLED
    // Swap mode (WAD17+): bit 15 of operand is set.
    // The Dup instruction doubles as a stack rotation when bit 15 is set.
    // It takes the top N items and moves them below the next M items.
    // Bits 0-10: top group size (in native type units)
    // Bits 11-14: bottom group size (in native type units)
    if (IS_WAD17_OR_HIGHER(ctx) && (operand & 0x8000) != 0) {
        int32_t topNativeCount = operand & 0x7FF;
        int32_t bottomNativeCount = (operand >> 11) & 0xF;

        int32_t topSlots = topNativeCount;
        int32_t bottomSlots = bottomNativeCount;

        if (type1 != GML_TYPE_VARIABLE) {
            int32_t topBytes = topNativeCount * typeSize;
            int32_t bottomBytes = bottomNativeCount * typeSize;
            topSlots = bytesToSlotCount(ctx, topBytes, ctx->stack.top);
            bottomSlots = bytesToSlotCount(ctx, bottomBytes, ctx->stack.top - topSlots);
        }

        int32_t totalSlots = topSlots + bottomSlots;
        int32_t baseIdx = ctx->stack.top - totalSlots;

        // Save top group to temp
        RValue temp[32];
        for (int32_t i = 0; topSlots > i; i++) {
            temp[i] = ctx->stack.slots[ctx->stack.top - topSlots + i];
        }

        // Shift bottom group up to where top group was
        {
            for (int32_t i = bottomSlots - 1; i >= 0; i--) {
                ctx->stack.slots[baseIdx + topSlots + i] = ctx->stack.slots[baseIdx + i];
            }
        }

        // Place top group at the bottom
        {
            for (int32_t i = 0; topSlots > i; i++) {
                ctx->stack.slots[baseIdx + i] = temp[i];
            }
        }
        return;
    }
#endif

    // Normal dup mode: total bytes to duplicate = (operand + 1) * sizeof(type1)
    // WAD17+ uses the low 15 bits of operand.
    // WAD16 and below uses the low 15 bits of operand.
    // Bit 15 is the swap-mode flag, handled above.
    int32_t operandCount;
#if IS_WAD17_OR_HIGHER_ENABLED
    operandCount = IS_WAD17_OR_HIGHER(ctx) ? (int32_t)(operand & 0x7FFF) : (int32_t)(operand & 0xFF);
#else
    operandCount = (int32_t)(operand & 0xFF);
#endif
    int32_t totalBytes = (operandCount + 1) * typeSize;
    int32_t count = bytesToSlotCount(ctx, totalBytes, ctx->stack.top);

    // Copy 'count' items from the top of the stack (preserving order)
    int32_t startIdx = ctx->stack.top - count;
    for (int32_t i = 0; count > i; i++) {
        stackPush(ctx, RValue_makeIndependent(ctx->stack.slots[startIdx + i]));
    }
}

// ===[ Function Call Handler ]===

extern bool hldWatchCollisionMutation;
extern bool hldCollisionMutated;

static void handleCall(VMContext* ctx, OpWord opword, const uint8_t* extraData, bool discard) {
    int32_t argCount = OpWord_extra(opword);
    uint32_t funcIndex = resolveFuncOperand(extraData);
    require(ctx->dataWin->func.functionCount > funcIndex);

#ifdef PLATFORM_VITA
    // HLD's noise surfaces are used only by the distortion shader. When that
    // program is unavailable, keep the default shader the original call
    // would have selected and skip generating textures it cannot consume.
    if (funcIndex + 1 == ctx->hldDistortionCallPlusOne && argCount == 0) {
        Renderer* renderer = ctx->runner->renderer;
        if (renderer != nullptr && !renderer->vtable->shaderIsCompiled(renderer, 7)) {
            if (renderer->currentShader != -1) renderer->vtable->gpuResetShader(renderer);
            if (!ctx->loggedHldDistortionFallback) {
                logInfo("Vita: skipping noise surfaces for the unavailable screen distortion shader\n");
                ctx->loggedHldDistortionFallback = true;
            }
            if (!discard) stackPushTyped(ctx, RValue_makeUndefined(), GML_TYPE_VARIABLE);
            return;
        }
    }
#endif

    // Pop arguments from stack (args pushed right-to-left, so first arg is on top)
    RValue* args = nullptr;
    RValue stackArgs[VM_MAX_STACK_ARGS];
    bool useStackArgs = argCount <= VM_MAX_STACK_ARGS;
    if (argCount > 0) {
        args = useStackArgs ? stackArgs : (RValue*)safeMalloc((size_t)argCount * sizeof(RValue));
        if (useStackArgs && argCount < VM_MAX_STACK_ARGS) {
            memset(stackArgs + argCount, 0, (VM_MAX_STACK_ARGS - (size_t)argCount) * sizeof(RValue));
        }
#ifdef ENABLE_VM_TRACING
        repeat(argCount, i) {
            args[i] = stackPop(ctx);
        }
#else
        assert(ctx->stack.top >= argCount);
        const RValue* top = ctx->stack.slots + ctx->stack.top;
        repeat(argCount, i) {
            args[i] = *--top;
        }
        ctx->stack.top -= argCount;
#endif
    }

#ifdef ENABLE_VM_TRACING
    const char* funcName = ctx->dataWin->func.functions[funcIndex].name;
    bool functionIsBeingTraced = shgeti(ctx->functionCallsToBeTraced, "*") != -1 || shgeti(ctx->functionCallsToBeTraced, funcName) != -1 || shgeti(ctx->functionCallsToBeTraced, VM_getCurrentCallFrameCodeName(ctx)) != -1;
    char* functionArgumentList = nullptr;
    if (functionIsBeingTraced) {
        functionArgumentList = safeStrdup("");
        for (int32_t i = 0; i < argCount; i++) {
            char* display = RValue_toStringFancy(args[i], ctx->runner->dataWin);

            if (i > 0) {
                size_t bufsz = strlen(functionArgumentList) + 2 + strlen(display) + 1;
                char* tmp = (char *)safeMalloc(bufsz);
                snprintf(tmp, bufsz, "%s, %s", functionArgumentList, display);
                free(functionArgumentList);
                functionArgumentList = tmp;
            } else {
                free(functionArgumentList);
                functionArgumentList = safeStrdup(display);
            }
            free(display);
        }

        logInfo("VM: [%s] Calling function \"%s(%s)\"\n", VM_getCurrentCallFrameCodeName(ctx), funcName, functionArgumentList);
    }
#endif

    // Fast path: cached builtin function pointer
    FuncCallCache* cache = &ctx->funcCallCache[funcIndex];
    if (cache->builtin != nullptr) {
        if (hldWatchCollisionMutation) hldCollisionMutated = true;
        BuiltinFunc builtin = (BuiltinFunc) cache->builtin;
        RValue result = builtin(ctx, args, argCount);
        // Free arguments
        if (args != nullptr) {
            repeat(argCount, i) {
                RValue_free(&args[i]);
            }
            if (!useStackArgs) free(args);
        }

#ifdef ENABLE_VM_TRACING
        if (functionIsBeingTraced) {
            char* returnValueAsString = RValue_toStringFancy(result, ctx->runner->dataWin);
            logInfo("VM: [%s] Built-in function \"%s(%s)\" returned %s\n", VM_getCurrentCallFrameCodeName(ctx), funcName, functionArgumentList, returnValueAsString);
            free(returnValueAsString);
            free(functionArgumentList);
        }
#endif

        if (discard) RValue_free(&result);
        else stackPushTyped(ctx, result, GML_TYPE_VARIABLE);
        return;
    }

    // Fast path: cached script code index
    if (cache->scriptCodeIndex >= 0) {
        RValue result = VM_executeCode(
            ctx,
            cache->scriptCodeIndex,
            ctx->currentCallFrame->self,
            ctx->currentCallFrame->other,
            ctx->currentCallFrame->eventObjectIndex,
            ctx->currentCallFrame->eventType,
            ctx->currentCallFrame->eventSubtype,
            args,
            argCount
        );

#ifdef ENABLE_VM_TRACING
        if (functionIsBeingTraced) {
            char* returnValueAsString = RValue_toStringFancy(result, ctx->runner->dataWin);
            logInfo("VM: [%s] Script function \"%s(%s)\" returned %s\n", VM_getCurrentCallFrameCodeName(ctx), funcName, functionArgumentList, returnValueAsString);
            free(returnValueAsString);
            free(functionArgumentList);
        }
#endif

        // Free arguments (VM_executeCode copies what it needs)
        if (args != nullptr) {
            repeat(argCount, i) {
                RValue_free(&args[i]);
            }
            if (!useStackArgs) free(args);
        }

        if (discard) RValue_free(&result);
        else stackPushTyped(ctx, result, GML_TYPE_VARIABLE);
        return;
    }

    // Slow path: unknown function (not cached as builtin or script)
#ifdef ENABLE_VM_STUB_LOGS
    const char* unknownFuncName = ctx->dataWin->func.functions[funcIndex].name;

    // Log once per (callingCode, funcName) pair
    const char* callerName = VM_getCurrentCallFrameCodeName(ctx);
    char* dedupKey = VM_createDedupKey(callerName, unknownFuncName);

    if (ctx->alwaysLogUnknownFunctions || 0 > shgeti(ctx->loggedUnknownFuncs, dedupKey)) {
        shput(ctx->loggedUnknownFuncs, dedupKey, true);
        logWarn("VM: [%s] Unknown function \"%s\"!\n", callerName, unknownFuncName);
    } else {
        free(dedupKey);
    }
#endif

    // Free arguments and push undefined
    if (args != nullptr) {
        repeat(argCount, i) {
            RValue_free(&args[i]);
        }
        if (!useStackArgs) free(args);
    }

#ifdef ENABLE_VM_TRACING
    if (functionIsBeingTraced) {
        free(functionArgumentList);
    }
#endif

    if (!discard) stackPush(ctx, RValue_makeUndefined());
}

#if IS_WAD17_OR_HIGHER_ENABLED
// BC17+ CALLV: dynamic call through a variable (method/script reference).
// Stack layout (top -> bottom): function, instance, arg[N-1], ..., arg[0]
// argCount is in the low 16 bits of the instruction.
static void handleCallV(VMContext* ctx, OpWord opword) {
    int32_t argCount = OpWord_extra(opword);

    RValue function = stackPop(ctx);
    RValue instance = stackPop(ctx);

    RValue* args = nullptr;
    RValue stackArgs[VM_MAX_STACK_ARGS];
    bool useStackArgs = argCount <= VM_MAX_STACK_ARGS;
    if (argCount > 0) {
        args = useStackArgs ? stackArgs : (RValue*)safeMalloc((size_t)argCount * sizeof(RValue));
        if (useStackArgs && argCount < VM_MAX_STACK_ARGS) {
            memset(stackArgs + argCount, 0, (VM_MAX_STACK_ARGS - (size_t)argCount) * sizeof(RValue));
        }
#ifdef ENABLE_VM_TRACING
        repeat(argCount, i) {
            args[i] = stackPop(ctx);
        }
#else
        assert(ctx->stack.top >= argCount);
        const RValue* top = ctx->stack.slots + ctx->stack.top;
        repeat(argCount, i) {
            args[i] = *--top;
        }
        ctx->stack.top -= argCount;
#endif
    }

    int32_t codeIndex = -1;
    int32_t boundInstance = -1;
    BuiltinFunc builtin = nullptr;
    const char* unresolvedName = nullptr;
    if (function.type == RVALUE_METHOD && function.method != nullptr) {
        codeIndex = function.method->codeIndex;
        boundInstance = function.method->boundInstanceId;
        builtin = (BuiltinFunc) function.method->builtin;
        unresolvedName = function.method->unresolvedName;
    } else if (DataWin_isVersionAtLeast(ctx->dataWin, 2, 3, 0, 0) && (function.type == RVALUE_INT32 || function.type == RVALUE_INT64 || function.type == RVALUE_REAL || function.type == RVALUE_BOOL)) {
        // In GMS 2.3.0+: CALLV coerces any numeric operand to a script index and wraps it with method() before calling.
        // Example: A button callback field assigned "field = some_script" without method().
        int32_t rawArg = RValue_toInt32(function);
        if (rawArg >= 0 && ctx->dataWin->func.functionCount > (uint32_t) rawArg) {
            const char* funcName = ctx->dataWin->func.functions[rawArg].name;
            if (funcName != nullptr) {
                ptrdiff_t idx = shgeti(ctx->codeIndexByName, (char*) funcName);
                if (idx >= 0) {
                    codeIndex = ctx->codeIndexByName[idx].value;
                } else {
                    ptrdiff_t bidx = shgeti(ctx->builtinMap, (char*) funcName);
                    if (bidx >= 0) builtin = ctx->builtinMap[bidx].value;
                }
            }
        }
    }

    // Decide target self: prefer method's bound instance, else the stack-provided instance.
    int32_t targetInstance = (boundInstance > 0) ? boundInstance : RValue_toInt32(instance);
    Instance* savedSelf = ctx->currentCallFrame->self;
    if (targetInstance != INSTANCE_SELF && targetInstance != 0) {
        Instance* target = VM_findInstanceByTarget(ctx, targetInstance);
        if (target != nullptr) ctx->currentCallFrame->self = target;
    }

    RValue result;
    if (codeIndex >= 0 && ctx->dataWin->code.count > (uint32_t) codeIndex) {
        result = VM_executeCode(
            ctx,
            codeIndex,
            ctx->currentCallFrame->self,
            ctx->currentCallFrame->other,
            ctx->currentCallFrame->eventObjectIndex,
            ctx->currentCallFrame->eventType,
            ctx->currentCallFrame->eventSubtype,
            args,
            argCount
        );
    } else if (builtin != nullptr) {
        result = builtin(ctx, args, argCount);
    } else if (unresolvedName != nullptr) {
#ifdef ENABLE_VM_STUB_LOGS
        const char* callerName = VM_getCurrentCallFrameCodeName(ctx);
        char* dedupKey = VM_createDedupKey(callerName, unresolvedName);
        if (ctx->alwaysLogUnknownFunctions || 0 > shgeti(ctx->loggedUnknownFuncs, dedupKey)) {
            shput(ctx->loggedUnknownFuncs, dedupKey, true);
            logWarn("VM: [%s] Unknown function \"%s\"! (via CallV)\n", callerName, unresolvedName);
        } else {
            free(dedupKey);
        }
#endif
        result = RValue_makeUndefined();
    } else {
        logWarn("VM: [%s] CALLV with unresolvable function reference (type=%d, codeIndex=%d)\n", VM_getCurrentCallFrameCodeName(ctx), function.type, codeIndex);
#ifdef ENABLE_WAD17
        VMException* exception = (VMException *)safeCalloc(1, sizeof(VMException));
        exception->message = safeStrdup("CALLV with unresolvable function reference");
        ctx->exception = exception;
        result = RValue_makeUndefined();
#endif
    }

    ctx->currentCallFrame->self = savedSelf;

    RValue_free(&function);
    RValue_free(&instance);
    if (args != nullptr) {
        repeat(argCount, i) {
            RValue_free(&args[i]);
        }
        if (!useStackArgs) free(args);
    }

    stackPushTyped(ctx, result, GML_TYPE_VARIABLE);
}
#endif

// ===[ With-Statement Helpers (PushEnv/PopEnv) ]===

// Resolves a collision/instance "target" argument by mapping the special INSTANCE_SELF and INSTANCE_OTHER to the concrete instance ID they refer to.
int32_t VM_resolveInstanceTarget(VMContext* ctx, int32_t target) {
    if (target == INSTANCE_SELF) {
        Instance* self = (Instance*) ctx->currentCallFrame->self;
        return self != nullptr ? (int32_t) self->instanceId : INSTANCE_NOONE;
    }
    if (target == INSTANCE_OTHER) {
        Instance* other = (Instance*) ctx->currentCallFrame->other;
        return other != nullptr ? (int32_t) other->instanceId : INSTANCE_NOONE;
    }
    return target;
}

// Checks if objectIndex is or inherits from targetObjectIndex by walking the parent chain.
bool VM_isObjectOrDescendant(DataWin* dataWin, int32_t objectIndex, int32_t targetObjectIndex) {
    int32_t currentObj = objectIndex;
    int depth = 0;
    while (currentObj >= 0 && (uint32_t) currentObj < dataWin->objt.count && 32 > depth) {
        if (currentObj == targetObjectIndex) return true;
        currentObj = dataWin->objt.objects[currentObj].parentId;
        depth++;
    }
    return false;
}


// Sets the VM instance context from an Instance.
static void switchToInstance(VMContext* ctx, Instance* inst) {
    ctx->currentCallFrame->self = inst;
}

// Restores VM context from an EnvFrame's saved fields.
static void restoreEnvContext(VMContext* ctx, EnvFrame* frame) {
    ctx->currentCallFrame->self = frame->savedInstance;
    ctx->currentCallFrame->other = frame->savedOtherInstance;
}


typedef struct {
    size_t capacity;
    Instance* items[];
} EnvSnapshotBuffer;

enum { ENV_SNAPSHOT_POOL_SIZE = 4, ENV_SNAPSHOT_MAX_RETAINED = 2048 };
static VMContext* envSnapshotOwner;
static EnvSnapshotBuffer* envSnapshotPool[ENV_SNAPSHOT_POOL_SIZE];
static unsigned envSnapshotReused, envSnapshotAllocated, envSnapshotItems;

static Instance** takeEnvSnapshot(VMContext* ctx, size_t count) {
    if (envSnapshotOwner == nullptr) envSnapshotOwner = ctx;
    if (envSnapshotOwner == ctx && count <= ENV_SNAPSHOT_MAX_RETAINED) {
        int best = -1;
        for (int i = 0; i < ENV_SNAPSHOT_POOL_SIZE; ++i) {
            EnvSnapshotBuffer* buffer = envSnapshotPool[i];
            if (buffer != nullptr && buffer->capacity >= count &&
                (best < 0 || buffer->capacity < envSnapshotPool[best]->capacity)) best = i;
        }
        if (best >= 0) {
            EnvSnapshotBuffer* buffer = envSnapshotPool[best];
            envSnapshotPool[best] = nullptr;
            ++envSnapshotReused;
            return buffer->items;
        }
    }

    EnvSnapshotBuffer* buffer = safeMalloc(sizeof(*buffer) + count * sizeof(*buffer->items));
    buffer->capacity = count;
    ++envSnapshotAllocated;
    return buffer->items;
}

static void releaseEnvSnapshot(VMContext* ctx, Instance** items) {
    EnvSnapshotBuffer* buffer = (EnvSnapshotBuffer*)((char*)items - offsetof(EnvSnapshotBuffer, items));
    if (envSnapshotOwner == ctx && buffer->capacity <= ENV_SNAPSHOT_MAX_RETAINED) {
        int smallest = 0;
        for (int i = 0; i < ENV_SNAPSHOT_POOL_SIZE; ++i) {
            if (envSnapshotPool[i] == nullptr) {
                envSnapshotPool[i] = buffer;
                return;
            }
            if (envSnapshotPool[i]->capacity < envSnapshotPool[smallest]->capacity) smallest = i;
        }
        if (buffer->capacity > envSnapshotPool[smallest]->capacity) {
            free(envSnapshotPool[smallest]);
            envSnapshotPool[smallest] = buffer;
            return;
        }
    }
    free(buffer);
}

static void clearEnvSnapshotPool(VMContext* ctx) {
    if (envSnapshotOwner != ctx) return;
    for (int i = 0; i < ENV_SNAPSHOT_POOL_SIZE; ++i) {
        free(envSnapshotPool[i]);
        envSnapshotPool[i] = nullptr;
    }
    envSnapshotOwner = nullptr;
}

void hldReportEnvSnapshotPoolAtExit(void) {
    logInfo("HLD: with snapshots reused=%u allocated=%u copied_instances=%u\n",
            envSnapshotReused, envSnapshotAllocated, envSnapshotItems);
}

extern bool hldReducedVMWork;
static void legacySnapshotEnvInstances(EnvFrame* frame, Instance** source, bool reverseLargeLists) {
    int32_t sourceCount = (int32_t) arrlen(source);
    Instance** snapshot = sourceCount <= VM_INLINE_ENV_INSTANCES
        ? frame->inlineInstances
        : (Instance**) safeMalloc((size_t) sourceCount * sizeof(Instance*));
    int32_t count = 0;
    for (int32_t i = 0; i < sourceCount; i++) {
        if (source[i]->active) snapshot[count++] = source[i];
    }
    // GameMaker visits object matches in reverse order when more than two are active.
    if (reverseLargeLists && count > 2) {
        for (int32_t i = 0; i < count / 2; i++) {
            Instance* first = snapshot[i];
            snapshot[i] = snapshot[count - 1 - i];
            snapshot[count - 1 - i] = first;
        }
    }
    frame->instanceList = snapshot;
    frame->instanceCount = count;
}

void hldReportEnvSnapshotPool(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita with snapshots frame=%d reused=%u allocated=%u copied_instances=%u\n",
            frame, envSnapshotReused, envSnapshotAllocated, envSnapshotItems);
    envSnapshotReused = envSnapshotAllocated = envSnapshotItems = 0;
}

static void snapshotEnvInstances(VMContext* ctx, EnvFrame* frame, Instance** source, bool reverseLargeLists) {
    if (!hldReducedVMWork) {
        legacySnapshotEnvInstances(frame, source, reverseLargeLists);
        return;
    }
    int32_t sourceCount = (int32_t) arrlen(source);
    Instance** snapshot = sourceCount <= VM_INLINE_ENV_INSTANCES
        ? frame->inlineInstances
        : takeEnvSnapshot(ctx, (size_t)sourceCount);

    int32_t count = 0;
    if (reverseLargeLists) {
        for (int32_t i = sourceCount - 1; i >= 0; --i) {
            if (source[i]->active) snapshot[count++] = source[i];
        }
        // The legacy order is forward when exactly two instances qualify.
        if (count == 2) {
            Instance* first = snapshot[0];
            snapshot[0] = snapshot[1];
            snapshot[1] = first;
        }
    } else {
        for (int32_t i = 0; i < sourceCount; ++i)
            if (source[i]->active) snapshot[count++] = source[i];
    }
    if (sourceCount > VM_INLINE_ENV_INSTANCES) envSnapshotItems += (unsigned)count;
    frame->instanceList = snapshot;
    frame->instanceCount = count;
}

static void handlePushEnv(VMContext* ctx, OpWord opword, uint32_t instrAddr) {
    int32_t jumpOffset = OpWord_branchOffset(opword);

    // Pop target from stack
    RValue targetValue = stackPop(ctx);
    int32_t target = RValue_toInt32(targetValue);
    // BC17: -9 (INSTANCE_STACKTOP) means "pop again for the real target"
    if (IS_WAD17_OR_HIGHER(ctx) && target == INSTANCE_STACKTOP) {
        target = stackPopInt32(ctx);
    }
    if (targetValue.type == RVALUE_UNDEFINED) target = INSTANCE_NOONE;

    // Create env frame, save current context
    EnvFrame* frame = ctx->envDepth < VM_INLINE_ENV_FRAMES
        ? &ctx->inlineEnvFrames[ctx->envDepth]
        : (EnvFrame*) safeMalloc(sizeof(EnvFrame));
    ctx->envDepth++;
    frame->savedInstance = (Instance*) ctx->currentCallFrame->self;
    frame->savedOtherInstance = (Instance*) ctx->currentCallFrame->other;
    frame->instanceList = nullptr;
    frame->instanceCount = 0;
    frame->currentIndex = 0;
    frame->parent = ctx->envStack;
    ctx->envStack = frame;

    // Inside a with-block, "other" refers to the instance that executed the with-statement
    ctx->currentCallFrame->other = (Instance*) ctx->currentCallFrame->self;

    Runner* runner = (Runner*) ctx->runner;

    if (target == INSTANCE_SELF) {
        // with(self) - no-op, keep current instance
        return;
    }

    if (target == INSTANCE_OTHER) {
        // with(other) - switch to the instance that was "self" before the nearest enclosing with-block
        // For nested with-blocks, other refers to the saved instance from the parent env frame
        if (frame->parent != nullptr) {
            switchToInstance(ctx, frame->parent->savedInstance);
        } else if (frame->savedOtherInstance != nullptr) {
            // No parent env frame, but we have an otherInstance (example: from collision events), so let's use it!
            switchToInstance(ctx, frame->savedOtherInstance);
        }
        // If no parent frame and no otherInstance, keep the saved instance (no-op)
        return;
    }

    if (target == INSTANCE_NOONE) {
        // with(noone) - skip the block entirely
        ctx->ip = instrAddr + jumpOffset;
        return;
    }

    if (target == INSTANCE_ALL || (uint32_t) target < INSTANCE_ID_BASE) {
        if (target == INSTANCE_ALL) {
            snapshotEnvInstances(ctx, frame, runner->instances, false);
        } else if ((uint32_t) target < ctx->dataWin->objt.count) {
            // Snapshot the descendant-inclusive list before scripts can modify it.
            snapshotEnvInstances(ctx, frame, runner->instancesByObject[target], true);
        }

        if (frame->instanceCount == 0) {
            ctx->ip = instrAddr + jumpOffset;
        } else {
            switchToInstance(ctx, frame->instanceList[0]);
        }
        return;
    }

    if (target >= INSTANCE_ID_BASE) {
        // Instance ID - find specific instance
        Instance* inst = hldLookupInstanceId(runner, target);
        if (inst != nullptr && inst->active) {
            switchToInstance(ctx, inst);
            return;
        }

        // Instance not found, skip the block
        ctx->ip = instrAddr + jumpOffset;
        return;
    }

    if (target == -999999 && ctx->dataWin->gen8.gameID == 742741103 && ctx->dataWin->gen8.wadVersion == 15) {
        ctx->ip = instrAddr + jumpOffset;
        return;
    }

    if (0 > target) {
        // This is triggered in DELTARUNE Chapter 5 in room_dw_garden_meetflowery. This is not a bug, as
        // gml_Object_obj_bush_leaf_Step_0 calls scr_depth(-20). scr_depth then uses with(arg0), causing this log.
        logWarn("VM: [%s] PushEnv with negative target %d, this could be a Int64 number that is getting truncated to Int32!\n", VM_getCurrentCallFrameCodeName(ctx), target);
    } else {
        logWarn("VM: [%s] PushEnv with unhandled target %d\n", VM_getCurrentCallFrameCodeName(ctx), target);
    }
    ctx->ip = instrAddr + jumpOffset;
}

static void handlePopEnv(VMContext* ctx, OpWord opword, uint32_t instrAddr) {
    EnvFrame* frame = ctx->envStack;
    require(frame != nullptr);

    // An exit inside a with block uses 0xBBF00000 to pop without visiting further instances.
    while (opword.value != 0xBBF00000 && frame->currentIndex + 1 < frame->instanceCount) {
        frame->currentIndex++;
        Instance* nextInst = frame->instanceList[frame->currentIndex];
        if (nextInst->active) {
            switchToInstance(ctx, nextInst);
            // Jump back to the start of the with-block body
            int32_t jumpOffset = OpWord_branchOffset(opword);
            ctx->ip = instrAddr + jumpOffset;
            return;
        }
    }

    // Done iterating - restore context and pop frame
    restoreEnvContext(ctx, frame);
    ctx->envStack = frame->parent;
    if (frame->instanceList != nullptr && frame->instanceList != frame->inlineInstances) {
        if (hldReducedVMWork) releaseEnvSnapshot(ctx, frame->instanceList);
        else free(frame->instanceList);
    }
    if (--ctx->envDepth >= VM_INLINE_ENV_FRAMES) free(frame);
}

// ===[ Execution Loop ]===

#ifdef ENABLE_VM_OPCODE_PROFILER
static char gmlTypeChar(uint8_t type);

static const char* rvalueTypeName(uint8_t type) {
    switch (type) {
        case RVALUE_REAL:      return "REAL";
        case RVALUE_STRING:    return "STRING";
        case RVALUE_INT32:     return "INT32";
        case RVALUE_INT64:     return "INT64";
        case RVALUE_BOOL:      return "BOOL";
        case RVALUE_UNDEFINED: return "UNDEF";
        case RVALUE_ARRAY:     return "ARRAY";
        case RVALUE_METHOD:    return "METHOD";
        case RVALUE_STRUCT:    return "STRUCT";
        case RVALUE_ASSETREF:  return "ASSETREF";
        case 0xF:              return "-";
        default:               return "???";
    }
}

static const char* breakSubOpName(int16_t breakType) {
    switch (breakType) {
        case BREAK_CHKINDEX:    return "chkindex";
        case BREAK_PUSHAF:      return "pushaf";
        case BREAK_POPAF:       return "popaf";
        case BREAK_PUSHAC:      return "pushac";
        case BREAK_SETOWNER:    return "setowner";
        case BREAK_ISSTATICOK:  return "isstaticok";
        case BREAK_SETSTATIC:   return "setstatic";
        case BREAK_SAVEAREF:    return "savearef";
        case BREAK_RESTOREAREF: return "restorearef";
        case BREAK_ISNULLISH:   return "isnullish";
        case BREAK_PUSHREF:     return "pushref";
        default:                return "???";
    }
}

void VM_printOpcodeProfilerReport(const VMContext* ctx) {
    if (!ctx->opcodeProfilerEnabled) return;

    typedef struct { uint16_t key; uint64_t count; } CountEntry;
    CountEntry entries[256];
    int entryCount = 0;
    uint64_t total = 0;
    for (int i = 0; 256 > i; i++) {
        if (ctx->opcodeCounts[i] > 0) {
            entries[entryCount].key = (uint16_t) i;
            entries[entryCount].count = ctx->opcodeCounts[i];
            entryCount++;
            total += ctx->opcodeCounts[i];
        }
    }

    // Simple insertion sort (max 256 entries, runs once at shutdown)
    {
    for (int i = 1; entryCount > i; i++) {
        CountEntry tmp = entries[i];
        int j = i;
        while (j > 0 && entries[j - 1].count < tmp.count) {
            entries[j] = entries[j - 1];
            j--;
        }
        entries[j] = tmp;
    }
    }

    logInfo("=== Opcode Profiler Report ===\n");
    logInfo("Total instructions executed: %llu\n", (unsigned longlong) total);
    logInfo("%-12s %-6s %16s %8s\n", "Opcode", "Hex", "Count", "Pct");
    {
    forEachIndexed(CountEntry, entry, i, entries, entryCount) {
        (void) i;
        double pct = total > 0 ? (100.0 * (double)(int64_t)entry->count / (double)(int64_t)total) : 0.0;
        logInfo("%-12s 0x%02X   %16llu %7.2f%%\n", Op_getOpcodeName((Op) entry->key), (uint8_t) entry->key, (unsigned longlong) entry->count, pct);
    }
    }

    // Per-opcode breakdown by type variant. Sorted within each opcode by count desc.
    logInfo("\n--- Type variant breakdown (per opcode) ---\n");
    forEachIndexed(CountEntry, entry, idx, entries, entryCount) {
        (void) idx;
        uint8_t opcode = (uint8_t) entry->key;
        const uint64_t* variants = &ctx->opcodeVariantCounts[opcode * 256];

        CountEntry variantEntries[256];
        int variantCount = 0;
        for (int t = 0; 256 > t; t++) {
            if (variants[t] > 0) {
                variantEntries[variantCount].key = (uint16_t) t;
                variantEntries[variantCount].count = variants[t];
                variantCount++;
            }
        }
        for (int i = 1; variantCount > i; i++) {
            CountEntry tmp = variantEntries[i];
            int j = i;
            while (j > 0 && variantEntries[j - 1].count < tmp.count) {
                variantEntries[j] = variantEntries[j - 1];
                j--;
            }
            variantEntries[j] = tmp;
        }

        logInfo("%s (0x%02X): %llu total\n", Op_getOpcodeName((Op) opcode), opcode, (unsigned longlong) entry->count);
        forEachIndexed(CountEntry, ve, vi, variantEntries, variantCount) {
            (void) vi;
            uint8_t type1 = (uint8_t) ((ve->key >> 4) & 0xF);
            uint8_t type2 = (uint8_t) (ve->key & 0xF);
            double vpct = entry->count > 0 ? (100.0 * (double)(int64_t)ve->count / (double)(int64_t)entry->count) : 0.0;
            logInfo("    .%c.%c  %16llu %7.2f%%\n", gmlTypeChar(type1), gmlTypeChar(type2), (unsigned longlong) ve->count, vpct);
        }

        // Runtime RValue type breakdown (a, b types observed at execution time)
        {
            const uint64_t* rvCounts = &ctx->opcodeRValueTypeCounts[opcode * 256];
            CountEntry rvEntries[256];
            int rvCount = 0;
            uint64_t rvTotal = 0;
            for (int t = 0; 256 > t; t++) {
                if (rvCounts[t] > 0) {
                    rvEntries[rvCount].key = (uint16_t) t;
                    rvEntries[rvCount].count = rvCounts[t];
                    rvCount++;
                    rvTotal += rvCounts[t];
                }
            }
            if (rvCount > 0) {
                for (int i = 1; rvCount > i; i++) {
                    CountEntry tmp = rvEntries[i];
                    int j = i;
                    while (j > 0 && rvEntries[j - 1].count < tmp.count) {
                        rvEntries[j] = rvEntries[j - 1];
                        j--;
                    }
                    rvEntries[j] = tmp;
                }
                logInfo("    -- runtime types (a, b):\n");
                forEachIndexed(CountEntry, re, ri, rvEntries, rvCount) {
                    (void) ri;
                    uint8_t typeA = (uint8_t) ((re->key >> 4) & 0xF);
                    uint8_t typeB = (uint8_t) (re->key & 0xF);
                    double rpct = rvTotal > 0 ? (100.0 * (double)(int64_t)re->count / (double)(int64_t)rvTotal) : 0.0;
                    logInfo("    (%-6s, %-6s) %16llu %7.2f%%\n", rvalueTypeName(typeA), rvalueTypeName(typeB), (unsigned longlong) re->count, rpct);
                }
            }
        }

        // Extended BREAK (0xFF) sub-opcode breakdown
        if (opcode == OP_BREAK) {
            CountEntry breakEntries[64];
            int breakCount = 0;
            for (int i = 0; 64 > i; i++) {
                if (ctx->breakSubOpCounts[i] > 0) {
                    breakEntries[breakCount].key = (uint16_t) i;
                    breakEntries[breakCount].count = ctx->breakSubOpCounts[i];
                    breakCount++;
                }
            }
            {
            for (int i = 1; breakCount > i; i++) {
                CountEntry tmp = breakEntries[i];
                int j = i;
                while (j > 0 && breakEntries[j - 1].count < tmp.count) {
                    breakEntries[j] = breakEntries[j - 1];
                    j--;
                }
                breakEntries[j] = tmp;
            }
            }
            logInfo("    -- sub-opcodes:\n");
            forEachIndexed(CountEntry, be, bi, breakEntries, breakCount) {
                (void) bi;
                int16_t breakType = (int16_t) -((int) be->key);
                double bpct = entry->count > 0 ? (100.0 * (double)(int64_t)be->count / (double)(int64_t)entry->count) : 0.0;
                logInfo("    %-12s (%4d) %16llu %7.2f%%\n", breakSubOpName(breakType), (int) breakType, (unsigned longlong) be->count, bpct);
            }
        }
    }
    logInfo("==============================\n");
}
#endif // ENABLE_VM_OPCODE_PROFILER

// Forward declaration for formatInstruction (defined in disassembler section, used by trace-opcodes)
static void formatInstruction(VMContext* ctx, const uint8_t* bytecodeBase, uint32_t instrAddr, OpWord opword, const uint8_t* extraData, char* opcodeStr, size_t opcodeSize, char* operandStr, size_t operandSize, char* commentStr, size_t commentSize);

#if IS_WAD17_OR_HIGHER_ENABLED
// ===[ BREAK sub-opcode handlers (BC17+) ]===

static void handleBreakChkIndex(VMContext* ctx, uint32_t instrAddr) {
    // Validate top-of-stack array index is in [0, 32000)
    RValue* top = stackPeek(ctx);
    int32_t idx = RValue_toInt32(*top);
    if (0 > idx || 32000 <= idx) {
        logError("VM: chkindex out of bounds: %d at offset %u in %s\n", idx, instrAddr, VM_getCurrentCallFrameCodeName(ctx));
        abort();
    }
}

static void handleBreakPushAF(VMContext* ctx) {
    // Pop index + array ref, push array[index]. Array ref is a weak RVALUE_ARRAY pointer.
    int32_t idx = stackPopInt32(ctx);
    RValue arrayRef = stackPop(ctx);
    RValue result;
    RValue* cell = arrayRef.type == RVALUE_ARRAY ? GMLArray_slot(arrayRef.array, idx) : nullptr;
    if (cell != nullptr) {
        result = *cell;
        result.ownsReference = false; // weak view
    } else {
        result = RValue_makeUndefined();
    }
    stackPushTyped(ctx, result, GML_TYPE_VARIABLE);
    RValue_free(&arrayRef);
}

static void handleBreakPopAF(VMContext* ctx) {
    // Pop index + array ref + value, store value at array[index].
    // CoW via VM_arraySetWithCoW requires a slot pointer, since the stack-held arrayRef is a weak view, the real slot is whatever variable holds this array.
    // We can't easily recover the slot here, so we write directly into the array (no CoW fork at this level, fork already happened when the top-level variable was first written, or on a PUSHAC materialisation).
    // Assert the array is uniquely-owned or matches the current scope owner. A mismatch here means a shared/aliased array is about to be mutated in place, which silently breaks CoW semantics. BC17+ default mode (pass by reference) is expected to satisfy this since fork already happened at the top-level write. If this fires, a CoW path upstream failed to fork.
    int32_t idx = stackPopInt32(ctx);
    RValue arrayRef = stackPop(ctx);
    RValue value = stackPop(ctx);
    if (arrayRef.type == RVALUE_ARRAY && arrayRef.array != nullptr && idx >= 0) {
        GMLArray* arr = arrayRef.array;
        requireMessageFormatted(__FILE__, __LINE__, arr->refCount == 1 || arr->owner == ctx->currentArrayOwner, "BREAK_POPAF: Writing through shared/aliased array without prior CoW fork in %s: arr=%p refCount=%d owner=%p currentArrayOwner=%p idx=%d", VM_getCurrentCallFrameCodeName(ctx), (void*) arr, arr->refCount, arr->owner, ctx->currentArrayOwner, idx);
        GMLArray_set(arr, idx, value);
    }
    RValue_free(&arrayRef);
    RValue_free(&value);
}

static void handleBreakPushAC(VMContext* ctx, uint32_t instrAddr) {
    // Pop index + parent array ref, push sub-array at parent[index]. Materialise a fresh sub-array if the slot isn't already an RVALUE_ARRAY (multi-dim auto-init).
    int32_t idx = stackPopInt32(ctx);
    RValue arrayRef = stackPop(ctx);
    if (arrayRef.type != RVALUE_ARRAY || arrayRef.array == nullptr) {
        logError("VM: pushac on non-array (type=%d) at offset %u in %s\n", arrayRef.type, instrAddr, VM_getCurrentCallFrameCodeName(ctx));
        abort();
    }
    GMLArray* parent = arrayRef.array;
    GMLArray_growTo(parent, idx + 1);
    RValue* parentSlot = GMLArray_slot(parent, idx);
    if (parentSlot->type != RVALUE_ARRAY || parentSlot->array == nullptr) {
        RValue_free(parentSlot);
        GMLArray* sub = GMLArray_create(ctx->dataWin, 0);
        sub->owner = ctx->currentArrayOwner;
        *parentSlot = RValue_makeArray(sub);
    } else {
        cowForkSlotForModificationIfNeeded(ctx, parentSlot);
    }
    stackPush(ctx, RValue_makeArrayWeak(parentSlot->array));
    RValue_free(&arrayRef);
}

static void handleBreakSetOwner(VMContext* ctx) {
    // CoW scope owner for BC17+.
    // The bytecode emits this at the top of each script or event, passing a token (usually self-instance ID cast to int) that uniquely identifies the current scope.
    // Arrays whose .owner doesn't match fork on write.
    RValue value = stackPop(ctx);
    int64_t token = RValue_toInt64(value);
    ctx->currentArrayOwner = (void*) (intptr_t) token;
    RValue_free(&value);
}

static void handleBreakIsStaticOk(VMContext* ctx) {
    // Push bool: has this function's static block already run?
    bool initialized = ctx->staticInitialized[ctx->currentCallFrame->codeIndex];
    stackPush(ctx, RValue_makeBool(initialized));
}

static void handleBreakSetStatic(VMContext* ctx) {
    // Mark current function's static as initialized
    ctx->staticInitialized[ctx->currentCallFrame->codeIndex] = true;
}

static void handleBreakSaveARef(VMContext* ctx) {
    // Native 2.3: SAVEAREF does `g_pSavedArraySetContainer = g_pArraySetContainer`, doesn't touch the stack.
    // `g_pArraySetContainer` is a runner-global set by PUSHAC when traversing multi-dim parents, used by SET_RValue_Array as the container to write into.
    // Since our PUSHAC pushes the sub-array directly onto the VM stack instead of stashing it in a container, this is a no-op.
    //
    // To track if we are doing everything correct, we'll track the savearefBalance to figure out when a game does something wrong.
    ctx->savearefBalance++;
}

static void handleBreakRestoreARef(VMContext* ctx) {
    // Native 2.3: restores `g_pArraySetContainer` from the saved slot. No-op here (see BREAK_SAVEAREF).
    // A negative balance means RESTOREAREF was emitted without a matching SAVEAREF, which means that we are doing things wrong or it is a bytecode pattern that we don't understand.
    requireMessage(ctx->savearefBalance > 0, "BREAK_RESTOREAREF without matching SAVEAREF");
    ctx->savearefBalance--;
}

static void handleBreakIsNullish(VMContext* ctx) {
    // Peek the top of the stack and push a bool above it: true if the value is "nullish"
    RValue* value = stackPeek(ctx);
    // TODO: We need to support a RValue pointer_null later, because that's also considered as "nullish" here!
    bool nullish = value->type == RVALUE_UNDEFINED;
    stackPush(ctx, RValue_makeBool(nullish));
}

static void handleBreakPushRef(VMContext* ctx, const uint8_t* extraData) {
    // Push an asset reference encoded in the 32-bit operand: high byte = asset type, low 24 bits = index.
    // If it is a script reference, the low 24 bits is a funcIdx which we resolve to a callable method; everything else is a plain asset.
    uint32_t operand = BinaryUtils_readUint32Aligned(extraData);
    uint8_t assetType = (uint8_t) ((operand >> 24) & 0xFF);
    int32_t index = (int32_t) (operand & 0x00FFFFFF);

    if (assetType == ASSET_TYPE_SCRIPT) {
        // Resolve to a callable method
        if (ctx->dataWin->func.functionCount > (uint32_t) index) {
            FuncCallCache* cache = &ctx->funcCallCache[index];
            if (cache->scriptCodeIndex >= 0) {
                stackPushTyped(ctx, RValue_makeMethodFromCodeIndexAndInstanceId(cache->scriptCodeIndex, -1), GML_TYPE_VARIABLE);
                return;
            }
            GMLMethod* method;
            if (cache->builtin != nullptr) {
                method = GMLMethod_createBuiltin((BuiltinFunc) cache->builtin, -1);
            } else {
                method = GMLMethod_createUnresolved(ctx->dataWin->func.functions[index].name, -1);
            }

            stackPushTyped(ctx, RValue_makeMethod(method), GML_TYPE_VARIABLE);
        } else {
            stackPushTyped(ctx, RValue_makeUndefined(), GML_TYPE_VARIABLE);
        }
        return;
    }

    stackPushTyped(ctx, RValue_makeAssetRef(index, assetType), GML_TYPE_VARIABLE);
}

static void handleBreak(VMContext* ctx, OpWord opword, uint32_t instrAddr, const uint8_t* extraData) {
    if (IS_WAD16_OR_BELOW(ctx)) return;
    int16_t breakType = OpWord_extra(opword);
    switch (breakType) {
        case BREAK_CHKINDEX:    handleBreakChkIndex(ctx, instrAddr); break;
        case BREAK_PUSHAF:      handleBreakPushAF(ctx); break;
        case BREAK_POPAF:       handleBreakPopAF(ctx); break;
        case BREAK_PUSHAC:      handleBreakPushAC(ctx, instrAddr); break;
        case BREAK_SETOWNER:    handleBreakSetOwner(ctx); break;
        case BREAK_ISSTATICOK:  handleBreakIsStaticOk(ctx); break;
        case BREAK_SETSTATIC:   handleBreakSetStatic(ctx); break;
        case BREAK_SAVEAREF:    handleBreakSaveARef(ctx); break;
        case BREAK_RESTOREAREF: handleBreakRestoreARef(ctx); break;
        case BREAK_ISNULLISH:   handleBreakIsNullish(ctx); break;
        case BREAK_PUSHREF:     handleBreakPushRef(ctx, extraData); break;
        default:
            logError("VM: Unknown BREAK sub-opcode %d at offset %u in %s\n", breakType, instrAddr, VM_getCurrentCallFrameCodeName(ctx));
            abort();
    }
}
#endif

#define VM_SYNC_IP()    do { ctx->ip = ip; } while (0)
#define VM_RELOAD_IP()  do { ip = ctx->ip; } while (0)

// Gets the value a code block returns when it ends without an explicit "return <value>"
static inline RValue scriptFallthroughReturnValue(VMContext* ctx) {
    if (DataWin_isVersionAtLeast(ctx->dataWin, 2, 3, 1, 0)) {
        return RValue_makeUndefined();
    }
    return RValue_makeReal(0.0);
}

// Unwinds and frees the VMStack down to the newStackTop
static void unwindVMStack(VMContext* ctx, int32_t newStackTop) {
    repeat(ctx->stack.top - newStackTop, i) {
        RValue_free(&ctx->stack.slots[i + newStackTop]);
    }
    ctx->stack.top = newStackTop;
}

enum {
    HLD_DISTORTION_LOOP_START = 0x354,
    HLD_DISTORTION_LOOP_END = 0x638,
};

static GMLReal hldDistortionRandom(Random* random, GMLReal range) {
    return ((GMLReal) Random_nextUInt32(random) / (GMLReal) BS_RAND_MAX) * range;
}

static void hldDrawDistortionPoint(Renderer* renderer, int x, int y, int sourceX, uint32_t color) {
    renderer->vtable->drawRectangle(renderer, (float) x, (float) y, (float) x + 1.0f, (float) y + 1.0f,
        (uint32_t) sourceX | color, renderer->drawAlpha, false);
}

static bool drawHldDistortionLoop(VMContext* ctx) {
    Runner* runner = ctx->runner;
    Renderer* renderer = runner->renderer;
    Instance* self = ctx->currentCallFrame->self;
    if (renderer == nullptr || renderer->vtable->drawRectangle == nullptr || self == nullptr) return false;

    // Local slots refer to the fingerprinted bytecode below. Only its
    // checkerboard loop is specialized. The same surfaces and shader remain.
    RValue* locals = ctx->currentCallFrame->localVars;
    GMLReal widthValue = RValue_toReal(locals[1]);
    GMLReal sectionHeight = RValue_toReal(locals[9]);
    GMLReal bleedMax = RValue_toReal(locals[10]);
    GMLReal colorIntensity = RValue_toReal(locals[12]);
    GMLReal epsilon = runner->mathEpsilon;
    if (!(widthValue >= 1.0 && widthValue <= 4096.0 && sectionHeight >= 1.0 && sectionHeight <= 4096.0
        && bleedMax >= 0.0 && bleedMax <= 4096.0 && colorIntensity >= 0.0 && colorIntensity <= 1.0
        && epsilon >= 0.0 && epsilon < 1.0)) return false;
    int width = (int) widthValue;
    if ((GMLReal) width != widthValue) return false;

    uint32_t colorA = ((uint32_t) (127.0 * colorIntensity)) << 16;
    uint32_t colorB = ((uint32_t) (128.0 + 127.0 * colorIntensity)) << 16;
    // The GML initializes the left sweep at -1 and the right sweep at
    // the surface width. Starting both at zero changes the RNG stream.
    GMLReal endA = RValue_toReal(locals[14]);
    GMLReal endB = RValue_toReal(locals[16]);
    int sourceA = RValue_toInt32(locals[13]);
    int sourceB = RValue_toInt32(locals[15]);
    int offset = runner->applyOffsetForPrimitives ? 1 : 0;
    Random* random = &runner->random;
    int y = 0;
    for (; sectionHeight - (GMLReal) y > epsilon; ++y) {
        int parity = y & 1;
        int row = y + offset;
        for (int x = parity; width - x > epsilon; x += 2) {
            if ((GMLReal) x - endA > epsilon) {
                if (1.0 - hldDistortionRandom(random, 32.0) > epsilon) {
                    sourceA = x;
                    endA = x + hldDistortionRandom(random, bleedMax);
                }
            } else {
                hldDrawDistortionPoint(renderer, x + offset, row, sourceA, colorA);
            }
        }
        endA -= width;
        for (int x = width - 1 - parity; x >= 0; x -= 2) {
            if (endB - (GMLReal) x > epsilon) {
                if (1.0 - hldDistortionRandom(random, 32.0) > epsilon) {
                    sourceB = x;
                    endB = x - hldDistortionRandom(random, bleedMax);
                }
            } else {
                hldDrawDistortionPoint(renderer, x + offset, row, sourceB, colorB);
            }
        }
        endB += width;
    }
    if (y > 0) Instance_setSelfVar(self, 1014, RValue_makeReal((y - 1) & 1)); // SCREEN.screenOffset
    return true;
}

static inline bool consumeConditionalBranch(const uint8_t* bytecode, uint32_t length, uint32_t* ip, bool condition) {
    if (*ip >= length) return false;
    OpWord next = {BinaryUtils_readUint32Aligned(bytecode + *ip)};
    switch (OpWord_opcode(next)) {
        case OP_BT:
            *ip += condition ? OpWord_branchOffset(next) : 4;
            return true;
        case OP_BF:
            *ip += condition ? 4 : OpWord_branchOffset(next);
            return true;
        default:
            return false;
    }
}

enum {
    HLD_SCENERY_CULL_START = 0x03c0,
    HLD_SCENERY_CULL_END = 0x04d0,
    HLD_SCENERY_NEXT_INSTANCE = 0x07a8
};

static uint8_t* codeBytes(const VMContext* ctx, const CodeEntry* code) {
    return ctx->dataWin->bytecodeBuffer + (code->bytecodeAbsoluteOffset - ctx->dataWin->bytecodeBufferBase);
}

enum {
    OP_PACKED_LITERAL_VALUE = 0x85,
    OP_PACKED_LITERAL_STORE = 0x87,
    OP_PACKED_LITERAL_BRANCH = 0x88
};

// Only the first opcode word changes. Its width and operand stay the same,
// and branches into the following instructions still use their original code.
static void packHldLiteralInstructions(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned packed = 0;
    for (uint32_t index = 0; index < data->code.count; index++) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldSceneryStepCode || entry == ctx->hldDistortionCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            OpWord op = {BinaryUtils_readUint32Aligned(bytes + offset)};
            uint32_t addr = offset;
            offset += 4 + OpWord_operandSize(op);
            if (OpWord_opcode(op) != OP_PUSH_IMMEDIATE || entry->length - addr < 8) continue;
            OpWord next = {BinaryUtils_readUint32Aligned(bytes + addr + 4)};
            uint8_t replacement = 0;
            if (next.value == 0x07520000u) {
                replacement = OP_PACKED_LITERAL_VALUE;
            } else if (entry->length - addr >= 12 && OpWord_opcode(next) == OP_POP &&
                       OpWord_type1(next) == GML_TYPE_VARIABLE && OpWord_type2(next) == GML_TYPE_INT32 &&
                       (resolveVarOperand(bytes + addr + 8) >> 24 & 0xF8) == VARTYPE_NORMAL) {
                replacement = OP_PACKED_LITERAL_STORE;
            } else if (entry->length - addr >= 12 && OpWord_opcode(next) == OP_CMP &&
                       OpWord_type1(next) == GML_TYPE_INT32 && OpWord_type2(next) == GML_TYPE_VARIABLE) {
                OpWord branch = {BinaryUtils_readUint32Aligned(bytes + addr + 8)};
                if (OpWord_opcode(branch) == OP_BT || OpWord_opcode(branch) == OP_BF) {
                    replacement = OP_PACKED_LITERAL_BRANCH;
                }
            }
            if (replacement != 0) {
                BinaryUtils_writeUint32(bytes + addr, (op.value & 0x00ffffffu) | ((uint32_t)replacement << 24));
                ++packed;
            }
        }
    }
    logInfo("HLD: packed %u literal bytecode sequences\n", packed);
}

enum { OP_PACKED_MATH = 0xdc, OP_PACKED_CALL_DISCARD = 0xdd };

typedef enum {
    MATH_NONE, MATH_ABS, MATH_SIGN, MATH_FLOOR, MATH_CEIL, MATH_ROUND,
    MATH_MIN, MATH_MAX, MATH_CLAMP,
} PackedMath;

static void packHldMathInstructions(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    static const struct {
        const char* name;
        PackedMath kind;
        int32_t args;
    } math[] = {
        {"abs", MATH_ABS, 1}, {"sign", MATH_SIGN, 1}, {"floor", MATH_FLOOR, 1},
        {"ceil", MATH_CEIL, 1}, {"round", MATH_ROUND, 1}, {"min", MATH_MIN, 2},
        {"max", MATH_MAX, 2}, {"clamp", MATH_CLAMP, 3},
    };
    uint8_t* kinds = safeCalloc(data->func.functionCount, 1);
    uint8_t* arities = safeCalloc(data->func.functionCount, 1);
    for (uint32_t index = 0; index < data->func.functionCount; ++index) {
        FuncCallCache* cache = &ctx->funcCallCache[index];
        if (cache->builtin == nullptr || cache->scriptCodeIndex >= 0) continue;
        const char* name = data->func.functions[index].name;
        for (size_t op = 0; op < sizeof(math) / sizeof(*math); ++op) {
            if (strcmp(name, math[op].name) == 0) {
                kinds[index] = math[op].kind;
                arities[index] = math[op].args;
                break;
            }
        }
    }
    unsigned packed = 0, discarded = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            OpWord word = {BinaryUtils_readUint32Aligned(bytes + offset)};
            uint32_t addr = offset;
            offset += 4 + OpWord_operandSize(word);
            if (OpWord_opcode(word) != OP_CALL || entry->length - addr < 8 || OpWord_type2(word) != 0) continue;
            uint32_t func = resolveFuncOperand(bytes + addr + 4);
            if (func >= data->func.functionCount) continue;
            if (entry->length - addr >= 12 && BinaryUtils_readUint32Aligned(bytes + addr + 8) == 0x9e050000u) {
                BinaryUtils_writeUint32(bytes + addr, ((uint32_t)OP_PACKED_CALL_DISCARD << 24) | (word.value & 0x00ffffffu));
                ++discarded;
                continue;
            }
            if (kinds[func] == MATH_NONE || OpWord_extra(word) != arities[func]) continue;
            const uint32_t opcode = ((uint32_t)OP_PACKED_MATH << 24) | ((uint32_t)kinds[func] << 20);
            BinaryUtils_writeUint32(bytes + addr, opcode | (word.value & 0x000fffffu));
            ++packed;
        }
    }
    free(arities);
    free(kinds);
    logInfo("HLD: packed %u calls to scalar math builtins and %u discarded results\n", packed, discarded);
}

static bool executePackedMath(VMContext* ctx, OpWord word) {
    const int32_t count = OpWord_extra(word);
    if (count < 1 || count > 3 || ctx->stack.top < count) return false;
    GMLReal arguments[3];
    const RValue* top = ctx->stack.slots + ctx->stack.top;
    for (int32_t index = 0; index < count; ++index) {
        const RValue* value = --top;
        if (value->type == RVALUE_REAL) {
            arguments[index] = value->real;
        } else if (value->type == RVALUE_INT32 || value->type == RVALUE_BOOL) {
            arguments[index] = value->int32;
        } else {
            return false;
        }
    }
    GMLReal result = arguments[0];
    switch ((PackedMath)OpWord_type2(word)) {
        case MATH_ABS: result = GMLReal_fabs(result); break;
        case MATH_SIGN: result = result > 0.0 ? 1.0 : result < 0.0 ? -1.0 : 0.0; break;
        case MATH_FLOOR: result = GMLReal_floor(result); break;
        case MATH_CEIL: result = GMLReal_ceil(result); break;
        case MATH_ROUND: result = GMLReal_bankersRound(result); break;
        case MATH_MIN:
            result = INFINITY;
            if (result > arguments[0]) result = arguments[0];
            if (result > arguments[1]) result = arguments[1];
            break;
        case MATH_MAX:
            result = -INFINITY;
            if (arguments[0] > result) result = arguments[0];
            if (arguments[1] > result) result = arguments[1];
            break;
        case MATH_CLAMP:
            if (arguments[1] > result) result = arguments[1];
            if (result > arguments[2]) result = arguments[2];
            break;
        default: return false;
    }
    ctx->stack.top -= count;
    stackPushTyped(ctx, RValue_makeReal(result), GML_TYPE_VARIABLE);
    return true;
}

enum { OP_PACKED_VAR_READ = 0xc4, OP_PACKED_VAR_WRITE = 0xc5 };
enum { PACKED_SELF, PACKED_LOCAL, PACKED_GLOBAL, PACKED_OTHER, PACKED_VAR_ID_MASK = 0x3fff };

static void packHldVariableInstructions(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned reads = 0, writes = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        uint8_t previousOpcode = 0;
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            OpWord word = {BinaryUtils_readUint32Aligned(bytes + offset)};
            uint32_t addr = offset;
            offset += 4 + OpWord_operandSize(word);
            uint8_t opcode = OpWord_opcode(word);
            bool read = opcode == OP_PUSH || opcode == OP_PUSH_LOCAL || opcode == OP_PUSH_GLOBAL;
            bool write = opcode == OP_POP && previousOpcode != OP_PACKED_LITERAL_STORE;
            previousOpcode = opcode;
            if ((!read && !write) || entry->length - addr < 8 || OpWord_type1(word) != GML_TYPE_VARIABLE) continue;
            uint32_t ref = resolveVarOperand(bytes + addr + 4);
            if ((ref >> 24 & 0xF8) != VARTYPE_NORMAL) continue;
            unsigned scope;
            if (opcode == OP_PUSH_LOCAL) {
                scope = PACKED_LOCAL;
            } else if (opcode == OP_PUSH_GLOBAL) {
                scope = PACKED_GLOBAL;
            } else {
                switch (OpWord_extra(word)) {
                    case INSTANCE_SELF: scope = PACKED_SELF; break;
                    case INSTANCE_LOCAL: scope = PACKED_LOCAL; break;
                    case INSTANCE_GLOBAL: scope = PACKED_GLOBAL; break;
                    case INSTANCE_OTHER: scope = PACKED_OTHER; break;
                    default: continue;
                }
            }
            int32_t id = resolveVarDef(ctx, ref)->varID;
            if (id < 0 || id > PACKED_VAR_ID_MASK) continue;
            uint32_t types = word.value & 0x00ff0000u;
            if (read && entry->length - addr >= 20 &&
                OpWord_opcode((OpWord){BinaryUtils_readUint32Aligned(bytes + addr + 8)}) ==
                    OP_PACKED_LITERAL_BRANCH) {
                types |= 0x00f00000u;
            }
            const uint32_t head = (uint32_t)(read ? OP_PACKED_VAR_READ : OP_PACKED_VAR_WRITE) << 24;
            BinaryUtils_writeUint32(bytes + addr, head | types | (scope << 14) | (uint32_t)id);
            if (read) ++reads;
            else ++writes;
        }
    }
    logInfo("HLD: bound %u scalar variable reads and %u writes at load time\n", reads, writes);
}


enum { OP_PACKED_CONVERTED_READ = 0xca };
static unsigned convertedReadFast, convertedReadFallback;

static void packScalarReadConversions(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned bindings = 0;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t pc = 0; pc + 4 <= entry->length;) {
            const OpWord read = {BinaryUtils_readUint32Aligned(bytes + pc)};
            const uint32_t start = pc;
            pc += 4 + OpWord_operandSize(read);
            if (OpWord_opcode(read) != OP_PACKED_VAR_READ || OpWord_type2(read) != 0 ||
                pc + 4 > entry->length) continue;
            const OpWord convert = {BinaryUtils_readUint32Aligned(bytes + pc)};
            const uint8_t type = OpWord_type2(convert);
            if (OpWord_opcode(convert) != OP_CONV || OpWord_type1(convert) != GML_TYPE_VARIABLE ||
                (type != GML_TYPE_INT32 && type != GML_TYPE_BOOL)) continue;
            // Leave the conversion word in place for other branch targets and runtime fallbacks.
            const uint32_t packed = (uint32_t)OP_PACKED_CONVERTED_READ << 24 |
                (uint32_t)type << 20 | (read.value & 0x000fffffu);
            BinaryUtils_writeUint32(bytes + start, packed);
            ++bindings;
        }
    }
    logInfo("HLD: fused %u variable reads with scalar conversions\n", bindings);
}

static inline bool convertPackedReadValue(RValue* value, uint8_t type) {
    if (value->ownsReference) return false;
    if (type == GML_TYPE_BOOL) {
        if (value->type == RVALUE_REAL) value->int32 = value->real > (GMLReal)0.5;
        else if (value->type == RVALUE_INT32) value->int32 = value->int32 > 0;
        else if (value->type != RVALUE_BOOL) return false;
        value->type = RVALUE_BOOL;
    } else {
        if (value->type == RVALUE_REAL) {
            const GMLReal real = value->real;
            if (!(real >= (GMLReal)INT32_MIN && real < (GMLReal)2147483648.0)) return false;
            value->int32 = (int32_t)real;
        } else if (value->type != RVALUE_INT32 && value->type != RVALUE_BOOL) {
            return false;
        }
        value->type = RVALUE_INT32;
    }
    return true;
}

void hldReportConvertedReadsAtExit(void) {
    logInfo("HLD: converted variable reads fast=%u fallback=%u\n", convertedReadFast, convertedReadFallback);
}

extern bool hldReducedVMWork;

void hldReportConvertedReads(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita converted reads frame=%d fast=%u fallback=%u\n", frame, convertedReadFast, convertedReadFallback);
    convertedReadFast = convertedReadFallback = 0;
}

static inline Instance* packedScopeInstance(VMContext* ctx, unsigned scope) {
    if (scope == PACKED_SELF) return (Instance*)ctx->currentCallFrame->self;
    if (scope == PACKED_GLOBAL) return ctx->globalScopeInstance;
    return (Instance*)ctx->currentCallFrame->other;
}


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
    if (value == nullptr) return RValue_makeUndefined();
    RValue result = *value;
    result.ownsReference = false;
    return result;
}

static const int packedScopeTag[] = { INSTANCE_SELF, INSTANCE_LOCAL, INSTANCE_GLOBAL, INSTANCE_OTHER };

static inline bool branchOnPackedLiteral(VMContext* ctx, const RValue* value,
                                         OpWord opword, const uint8_t* bytecodeBase,
                                         uint32_t codeLength, uint32_t* ip) {
    if (value->type != RVALUE_INT32 && value->type != RVALUE_REAL) return false;
    GMLReal left = value->type == RVALUE_REAL ? value->real : (GMLReal)value->int32;
    GMLReal diff = left - OpWord_extra(opword);
    GMLReal epsilon = ctx->runner != nullptr ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
    int cmp = GMLReal_fabs(diff) <= epsilon ? 0 : diff < 0 ? -1 : 1;
    OpWord cmpOp = {BinaryUtils_readUint32Aligned(bytecodeBase + *ip)};
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
    *ip += 4;
    return consumeConditionalBranch(bytecodeBase, codeLength, ip, condition);
}

enum { OP_PACKED_ARGUMENT = 0xc7, OP_PACKED_ARGUMENT_ARRAY = 0x89 };
enum { PACKED_ARGUMENT_TO_LOCAL = 1 };

static int hldBoundArgument(VMContext* ctx, uint32_t ref, bool array) {
    const Variable* variable = resolveVarDef(ctx, ref);
    if (variable->varID != VARIABLE_BUILTIN) return -1;
    const int builtin = variable->builtinVarId;
    if (array) return builtin == BUILTIN_VAR_ARGUMENT ? 0 : -1;
    if (builtin < BUILTIN_VAR_ARGUMENT0 || builtin > BUILTIN_VAR_ARGUMENT15) return -1;
    return builtin - BUILTIN_VAR_ARGUMENT0;
}

static void packHldArguments(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    unsigned reads = 0, stores = 0;
    for (uint32_t i = 0; i < data->code.count; ++i) {
        CodeEntry* entry = &data->code.entries[i];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t offset = 0; offset + 4 <= entry->length;) {
            const uint32_t address = offset;
            const OpWord first = {BinaryUtils_readUint32Aligned(bytes + address)};
            offset += 4 + OpWord_operandSize(first);
            const uint8_t opcode = OpWord_opcode(first);
            unsigned packedOpcode = 0, packedLength = 0;
            int argument = -1;

            if ((opcode == OP_PUSH_BUILTIN || opcode == OP_PUSH) && offset - address == 8 &&
                offset <= entry->length && OpWord_type1(first) == GML_TYPE_VARIABLE &&
                OpWord_extra(first) == INSTANCE_SELF) {
                const uint32_t ref = resolveVarOperand(bytes + address + 4);
                if ((ref >> 24 & 0xf8) == VARTYPE_NORMAL) {
                    argument = hldBoundArgument(ctx, ref, false);
                    packedLength = 8;
                    packedOpcode = OP_PACKED_ARGUMENT;
                }
            } else if (opcode == OP_PUSH_IMMEDIATE && OpWord_extra(first) == INSTANCE_SELF &&
                       entry->length - address >= 16) {
                const OpWord second = {BinaryUtils_readUint32Aligned(bytes + address + 4)};
                const OpWord read = {BinaryUtils_readUint32Aligned(bytes + address + 8)};
                if (OpWord_opcode(second) == OP_PUSH_IMMEDIATE && OpWord_extra(second) >= 0 &&
                    OpWord_opcode(read) == OP_PUSH && OpWord_type1(read) == GML_TYPE_VARIABLE) {
                    const uint32_t ref = resolveVarOperand(bytes + address + 12);
                    if ((ref >> 24 & 0xf8) == VARTYPE_ARRAY && hldBoundArgument(ctx, ref, true) >= 0) {
                        argument = OpWord_extra(second);
                        packedLength = 16;
                        packedOpcode = OP_PACKED_ARGUMENT_ARRAY;
                    }
                }
            }
            if (argument < 0) continue;
            unsigned flags = 0;
            if (entry->length - address >= packedLength + 8) {
                const OpWord store = {BinaryUtils_readUint32Aligned(bytes + address + packedLength)};
                if (OpWord_opcode(store) == OP_PACKED_VAR_WRITE &&
                    OpWord_type2(store) == GML_TYPE_VARIABLE &&
                    (uint16_t)OpWord_extra(store) >> 14 == PACKED_LOCAL) {
                    flags = PACKED_ARGUMENT_TO_LOCAL;
                    ++stores;
                }
            }
            BinaryUtils_writeUint32(bytes + address, (packedOpcode << 24) | (flags << 20) |
                                    (GML_TYPE_VARIABLE << 16) | (uint32_t)argument);
            ++reads;
        }
    }
    logInfo("HLD: bound %u script argument reads, %u followed by a local assignment\n", reads, stores);
}

static inline RValue packedScriptArgument(const CallFrame* frame, unsigned index) {
    if (frame->scriptArgs == nullptr || frame->scriptArgsCount <= (int32_t)index)
        return RValue_makeUndefined();
    RValue result = frame->scriptArgs[index];
    result.ownsReference = false;
    return result;
}

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

enum {
    OP_PACKED_COPY_BLOCK = 0x8c,
    COPY_BLOCK_BITS = 12,
    COPY_BLOCK_MASK = (1 << COPY_BLOCK_BITS) - 1,
    COPY_BLOCK_MAX_FIELDS = 24
};

typedef struct {
    int32_t id;
    int16_t builtin;
    bool parent;
} CopyField;

typedef struct {
    CopyField read;
    CopyField write;
} FieldCopy;

typedef struct {
    uint32_t first;
    uint8_t count;
} FieldCopyBlock;

static const VMContext* copyBlockContext;
static FieldCopy* copyFields;
static FieldCopyBlock* copyBlocks;

static void releaseCopyBlocks(void) {
    arrfree(copyFields);
    arrfree(copyBlocks);
    copyBlockContext = nullptr;
}

static bool decodeCopyField(VMContext* ctx, const uint8_t* bytes, bool write, CopyField* field) {
    const OpWord word = {BinaryUtils_readUint32Aligned(bytes)};
    const uint8_t opcode = OpWord_opcode(word);
    if (OpWord_type1(word) != GML_TYPE_VARIABLE) return false;
    if (write && OpWord_type2(word) != GML_TYPE_VARIABLE) return false;
    if (!write && OpWord_type2(word) != 0) return false;

    if (opcode == (write ? OP_PACKED_VAR_WRITE : OP_PACKED_VAR_READ)) {
        const uint16_t operand = (uint16_t)OpWord_extra(word);
        const unsigned scope = operand >> 14;
        if (scope != PACKED_SELF && scope != PACKED_OTHER) return false;
        *field = (CopyField){operand & PACKED_VAR_ID_MASK, -1, scope == PACKED_OTHER};
        return true;
    }
    if (opcode != (write ? OP_POP : OP_PUSH)) return false;
    const int16_t scope = OpWord_extra(word);
    if (scope != INSTANCE_SELF && scope != INSTANCE_OTHER) return false;
    const uint32_t ref = resolveVarOperand(bytes + 4);
    if ((ref >> 24 & 0xf8) != VARTYPE_NORMAL) return false;
    const Variable* var = resolveVarDef(ctx, ref);
    if (var->varID >= 0) {
        *field = (CopyField){var->varID, -1, scope == INSTANCE_OTHER};
        return true;
    }
    if (var->varID != VARIABLE_BUILTIN) return false;
    const int16_t builtin = var->builtinVarId;
    if (builtin != BUILTIN_VAR_X && builtin != BUILTIN_VAR_Y && (write || builtin != BUILTIN_VAR_ID)) return false;
    *field = (CopyField){-1, builtin, scope == INSTANCE_OTHER};
    return true;
}

static void packCopyBlocks(VMContext* ctx) {
    DataWin* data = ctx->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15) return;
    releaseCopyBlocks();
    copyBlockContext = ctx;
    for (uint32_t index = 0; index < data->code.count; ++index) {
        const CodeEntry* entry = &data->code.entries[index];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t ip = 0; ip + 4 <= entry->length;) {
            const uint32_t at = ip;
            const OpWord word = {BinaryUtils_readUint32Aligned(bytes + at)};
            ip += 4 + OpWord_operandSize(word);
            if (OpWord_opcode(word) != OP_PUSH_ENV || arrlen(copyBlocks) > COPY_BLOCK_MASK) continue;
            const uint32_t distance = word.value & 0x00ffffffu;
            if (distance > COPY_BLOCK_MASK || distance < 9 || (distance - 1) % 4 != 0) continue;
            const uint32_t end = at + distance * sizeof(uint32_t);
            const unsigned count = (distance - 1) / 4;
            if (end + sizeof(uint32_t) > entry->length || count > COPY_BLOCK_MAX_FIELDS) continue;
            const OpWord pop = {BinaryUtils_readUint32Aligned(bytes + end)};
            if (OpWord_opcode(pop) != OP_POP_ENV || OpWord_branchOffset(pop) != 4 - (int32_t)(end - at)) continue;
            FieldCopy fields[COPY_BLOCK_MAX_FIELDS];
            unsigned field = 0;
            for (; field < count; ++field) {
                const uint8_t* pair = bytes + ip + 16 * field;
                if (!decodeCopyField(ctx, pair, false, &fields[field].read) ||
                    !decodeCopyField(ctx, pair + 8, true, &fields[field].write)) break;
            }
            if (field != count) continue;
            const uint32_t block = (uint32_t)arrlen(copyBlocks);
            const uint32_t first = (uint32_t)arrlen(copyFields);
            arrput(copyBlocks, ((FieldCopyBlock){first, count}));
            memcpy(arraddnptr(copyFields, count), fields, count * sizeof(*fields));
            BinaryUtils_writeUint32(bytes + at, ((uint32_t)OP_PACKED_COPY_BLOCK << 24) |
                                   (block << COPY_BLOCK_BITS) | distance);
            ip = end + sizeof(uint32_t);
        }
    }
    logInfo("HLD: bound %u instance-copy blocks with %u field copies at load time\n",
            (unsigned)arrlen(copyBlocks), (unsigned)arrlen(copyFields));
}

#ifdef HLD_COMPARE_COPY_BLOCKS
static unsigned copiedBlocks, copiedFields;

static inline bool useCopyBlocks(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportCopyBlocks(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita instance copy blocks frame=%d enabled=%d blocks=%u fields=%u\n",
            frame, (int)useCopyBlocks(frame - 1), copiedBlocks, copiedFields);
    copiedBlocks = copiedFields = 0;
}
#endif

static void copyInstanceFields(VMContext* ctx, Instance* parent, Instance* child, uint32_t blockIndex) {
    const FieldCopyBlock block = copyBlocks[blockIndex];
    const FieldCopy* fields = copyFields + block.first;
    for (uint32_t i = 0; i < block.count; ++i) {
        const FieldCopy* copy = &fields[i];
        Instance* from = copy->read.parent ? parent : child;
        Instance* to = copy->write.parent ? parent : child;
        RValue value = copy->read.builtin >= 0
            ? VMBuiltins_getVariable(ctx, from, copy->read.builtin, nullptr, -1)
            : Instance_getSelfVar(from, copy->read.id);
        value.gmlStackType = GML_TYPE_VARIABLE;
        if (copy->write.builtin >= 0) {
            VMBuiltins_setVariable(ctx, to, copy->write.builtin, nullptr, value, -1);
        } else {
            Instance_setSelfVar(to, copy->write.id, value);
        }
    }
#ifdef HLD_COMPARE_COPY_BLOCKS
    ++copiedBlocks;
    copiedFields += block.count;
#endif
}

static bool tryCopyBlock(VMContext* ctx, uint32_t blockIndex) {
    if (copyBlockContext != ctx || ctx->runner == nullptr || ctx->currentCallFrame->self == nullptr) return false;
#ifdef HLD_COMPARE_COPY_BLOCKS
    if (!useCopyBlocks(ctx->runner->frameCount)) return false;
#endif
    require(ctx->stack.top > 0);
    const RValue target = ctx->stack.slots[ctx->stack.top - 1];
    if (target.type != RVALUE_INT32 || target.int32 < INSTANCE_ID_BASE) return false;
    Instance* child = hldLookupInstanceId(ctx->runner, target.int32);
    Instance* parent = ctx->currentCallFrame->self;
    if (parent->objectIndex == STRUCT_OBJECT_INDEX || (child != nullptr && child->objectIndex == STRUCT_OBJECT_INDEX))
        return false;
    --ctx->stack.top;
    if (child != nullptr && child->active) copyInstanceFields(ctx, parent, child, blockIndex);
    return true;
}

static const CodeEntry* matchHldCode(VMContext* ctx, const char* name, uint32_t length, uint32_t expected);

enum { OP_PACKED_PURE_GUARD = 0xc9 };

typedef struct {
    const VMContext* context;
    const CodeEntry* pause;
    const CodeEntry* onscreen;
    int32_t gamePause, allPause, roomSelect;
    uint16_t screenObject;
} PureGuards;

static PureGuards pureGuards;

static void bindPureGuards(VMContext* ctx) {
    pureGuards = (PureGuards){.context = ctx};
    pureGuards.onscreen = matchHldCode(ctx, "gml_Script_onscreen", 316, 0xa0a24addu);
    const CodeEntry* pause = matchHldCode(ctx, "gml_Script_ActionIsPaused", 96, 0x14535ff1u);
    if (pause == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, pause);
    pureGuards.gamePause = resolveVarDef(ctx, resolveVarOperand(bytes + 0x04))->varID;
    pureGuards.allPause = resolveVarDef(ctx, resolveVarOperand(bytes + 0x18))->varID;
    pureGuards.roomSelect = resolveVarDef(ctx, resolveVarOperand(bytes + 0x2c))->varID;
    pureGuards.screenObject = (uint16_t)OpWord_extra((OpWord){BinaryUtils_readUint32Aligned(bytes + 0x28)});
    if (pureGuards.gamePause >= 0 && pureGuards.allPause >= 0 && pureGuards.roomSelect >= 0 &&
        pureGuards.screenObject < ctx->dataWin->objt.count) pureGuards.pause = pause;
}

static void packPureGuards(VMContext* ctx) {
    if (pureGuards.context != ctx || (pureGuards.pause == nullptr && pureGuards.onscreen == nullptr)) return;
    DataWin* data = ctx->dataWin;
    unsigned packed = 0;
    for (uint32_t i = 0; i < data->code.count; ++i) {
        const CodeEntry* entry = &data->code.entries[i];
        if (entry->offset != 0 || entry == ctx->hldDistortionCode || entry == ctx->hldSceneryStepCode) continue;
        uint8_t* bytes = codeBytes(ctx, entry);
        for (uint32_t ip = 0; ip + 4 <= entry->length;) {
            const uint32_t start = ip;
            const OpWord call = {BinaryUtils_readUint32Aligned(bytes + ip)};
            ip += 4 + OpWord_operandSize(call);
            const bool pause = call.value == UINT32_C(0xd9020000);
            if ((!pause && call.value != UINT32_C(0xd9020003)) || entry->length - start < 16) continue;
            const CodeEntry* wanted = pause ? pureGuards.pause : pureGuards.onscreen;
            if (wanted == nullptr) continue;
            const uint32_t function = resolveFuncOperand(bytes + start + 4);
            if (function >= data->func.functionCount) continue;
            const FuncCallCache* binding = &ctx->funcCallCache[function];
            if (binding->builtin != nullptr || binding->scriptCodeIndex != wanted - data->code.entries) continue;
            if (BinaryUtils_readUint32Aligned(bytes + ip) != UINT32_C(0x07450000)) continue;
            uint32_t branch = ip + 4;
            const bool negated = BinaryUtils_readUint32Aligned(bytes + branch) == UINT32_C(0x12040000);
            if (negated) branch += 4;
            if (branch + 4 > entry->length) continue;
            const uint8_t jump = OpWord_opcode((OpWord){BinaryUtils_readUint32Aligned(bytes + branch)});
            if (jump != OP_BT && jump != OP_BF) continue;
            const uint32_t flags = negated ? UINT32_C(0x00100000) : 0;
            BinaryUtils_writeUint32(bytes + start, ((uint32_t)OP_PACKED_PURE_GUARD << 24) |
                                    flags | (call.value & UINT32_C(0x000fffff)));
            ++packed;
        }
    }
    logInfo("HLD: bound %u pause and viewport branches\n", packed);
}

static inline bool pureGuardNumber(RValue value, GMLReal* number) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *number = RValue_toReal(value);
    return isfinite(*number);
}

static bool pureGuardPause(VMContext* ctx, GMLReal epsilon, bool* result) {
    Instance* global = ctx->globalScopeInstance;
    GMLReal number;
    if (global == nullptr || !pureGuardNumber(Instance_getSelfVar(global, pureGuards.gamePause), &number)) return false;
    if (GMLReal_fabs(number - 1.0) <= epsilon) {
        *result = true;
        return true;
    }
    if (!pureGuardNumber(Instance_getSelfVar(global, pureGuards.allPause), &number)) return false;
    if (number > epsilon) {
        *result = true;
        return true;
    }
    Instance* screen = VM_findInstanceByTarget(ctx, pureGuards.screenObject);
    if (screen == nullptr || !pureGuardNumber(Instance_getSelfVar(screen, pureGuards.roomSelect), &number)) return false;
    *result = GMLReal_fabs(number - 1.0) <= epsilon;
    return true;
}

static bool pureGuardViewport(VMContext* ctx, GMLReal epsilon, bool* result) {
    const RValue* end = ctx->stack.slots + ctx->stack.top;
    GMLReal x, y, margin;
    if (!pureGuardNumber(end[-1], &x) || !pureGuardNumber(end[-2], &y) || !pureGuardNumber(end[-3], &margin)) return false;
    const GMLCamera* camera = Runner_getCameraForView(ctx->runner, 0);
    if (camera == nullptr) return false;
    const GMLReal viewX = camera->viewX, viewY = camera->viewY;
    const GMLReal right = (viewX + (GMLReal)camera->viewWidth) + margin;
    const GMLReal bottom = (viewY + (GMLReal)camera->viewHeight) + margin;
    const GMLReal left = viewX - margin, top = viewY - margin;
    if (!isfinite(right) || !isfinite(bottom) || !isfinite(left) || !isfinite(top)) return false;
    *result = right - x > epsilon && x - left > epsilon && y - top > epsilon && bottom - y > epsilon;
    return true;
}

#if defined(HLD_COMPARE_PURE_GUARDS) || defined(HLD_VERIFY_PURE_GUARDS)
static unsigned pauseGuardHits, viewportGuardHits;
#endif

#ifdef HLD_COMPARE_PURE_GUARDS
static inline bool usePureGuards(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportPureGuards(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita pure guards frame=%d enabled=%d pause=%u viewport=%u\n",
            frame, (int)usePureGuards(frame - 1), pauseGuardHits, viewportGuardHits);
    pauseGuardHits = viewportGuardHits = 0;
}
#endif

static inline void notePureGuard(int32_t argc) {
#if defined(HLD_COMPARE_PURE_GUARDS) || defined(HLD_VERIFY_PURE_GUARDS)
    unsigned* hits = argc == 0 ? &pauseGuardHits : &viewportGuardHits;
    ++*hits;
#ifdef HLD_VERIFY_PURE_GUARDS
    if (*hits == 1000) logInfo("HLD: verified 1,000 %s branch conditions\n", argc == 0 ? "pause" : "viewport");
#endif
#else
    (void)argc;
#endif
}

static bool pureGuardResult(VMContext* ctx, int32_t argc, bool* result) {
    if (pureGuards.context != ctx || ctx->runner == nullptr || ctx->stack.top < argc) return false;
#ifdef HLD_COMPARE_PURE_GUARDS
    if (!usePureGuards(ctx->runner->frameCount)) return false;
#endif
#ifdef ENABLE_VM_TRACING
    if (shlen(ctx->functionCallsToBeTraced) != 0 || shlen(ctx->varReadsToBeTraced) != 0) return false;
#endif
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0)) return false;
    return argc == 0 ? pureGuardPause(ctx, epsilon, result) : pureGuardViewport(ctx, epsilon, result);
}

static const CodeEntry* matchHldCode(VMContext* ctx, const char* name, uint32_t length, uint32_t expected);

enum { OP_PACKED_FLAME_VENT = 0x8d, HLD_FLAME_VENT_LOOP_END = 0x02f8 };

typedef struct {
    const VMContext* context;
    int32_t object, state, timer, effect;
} FlameVentGuard;

static FlameVentGuard flameVentGuard;

#if defined(HLD_COMPARE_FLAME_VENT) || defined(HLD_VERIFY_FLAME_VENT)
static unsigned flameVentSkips;
#endif

#ifdef HLD_COMPARE_FLAME_VENT
static bool useFlameVentGuard(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportFlameVentGuard(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita Flame Vent guard frame=%d enabled=%d skipped=%u\n",
            frame, (int)useFlameVentGuard(frame - 1), flameVentSkips);
    flameVentSkips = 0;
}
#endif

static void bindFlameVentGuard(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Object_FlameVent_Step_0", 2732, 0x4a054d9cu);
    if (code == nullptr || code->localsCount != 6 || code->argumentsCount != 0) return;
    uint8_t* bytes = codeBytes(ctx, code);
    const uint16_t operands[] = {0x0190, 0x0284, 0x0298, 0x02ac};
    const char* names[] = {"obj", "state", "t", "flameFX"};
    int32_t ids[sizeof(operands) / sizeof(*operands)];
    repeat(sizeof(operands) / sizeof(*operands), i) {
        const Variable* var = resolveVarDef(ctx, resolveVarOperand(bytes + operands[i]));
        if (var->varID < 0 || strcmp(var->name, names[i]) != 0) return;
        ids[i] = var->varID;
    }
    flameVentGuard = (FlameVentGuard){.context = ctx, .object = ids[0], .state = ids[1],
                                    .timer = ids[2], .effect = ids[3]};
    // Replace only the initial push of zero. All fallback and branch
    // offsets retain their original lengths.
    BinaryUtils_writeUint32(bytes + 0x011c, (uint32_t)OP_PACKED_FLAME_VENT << 24);
    logInfo("HLD: bound the Flame Vent transition guard\n");
}

static bool readFlameVentNumber(Instance* inst, int32_t id, GMLReal* out) {
    const RValue value = Instance_getSelfVar(inst, id);
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *out = RValue_toReal(value);
    return isfinite(*out);
}

bool hldFlameVentHasNoProbeCandidate(Runner* runner, Instance* caller);

static bool flameVentMayTransition(Instance* inst, GMLReal epsilon) {
    GMLReal value;
    if (!readFlameVentNumber(inst, flameVentGuard.state, &value)) return true;
    if (GMLReal_fabs(value) > epsilon) return false;
    if (!readFlameVentNumber(inst, flameVentGuard.timer, &value)) return true;
    if (value - 1.0 <= epsilon) return false;
    return !readFlameVentNumber(inst, flameVentGuard.effect, &value) ||
           GMLReal_fabs(value + 999999.0) <= epsilon;
}

static bool canSkipFlameVentLoop(VMContext* ctx) {
    Runner* runner = ctx->runner;
    Instance* inst = ctx->currentCallFrame->self;
    if (flameVentGuard.context != ctx || inst == nullptr ||
        inst->objectIndex == STRUCT_OBJECT_INDEX || runner == nullptr || runner->spatialGrid == nullptr) return false;
#ifdef HLD_COMPARE_FLAME_VENT
    if (!useFlameVentGuard(runner->frameCount)) return false;
#endif
    const GMLReal epsilon = runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    // Only ChangeState can make the probes observable. The other writes
    // are to dead locals and the loop's target, which ends at 437.
    SpatialGrid_syncGrid(runner, runner->spatialGrid);
    return !flameVentMayTransition(inst, epsilon) || hldFlameVentHasNoProbeCandidate(runner, inst);
}

#define HLD_COMPARE_ENEMY_SHADOW
enum { OP_PACKED_ENEMY_SHADOW = 0xce, HLD_ENEMY_SHADOW_START = 0x24a0 };

static const VMContext* enemyShadowContext;
static const CodeEntry* enemyShadowStep;
static int32_t enemyShadowPlatforms;

#if defined(HLD_COMPARE_ENEMY_SHADOW) || defined(HLD_VERIFY_ENEMY_SHADOW)
static unsigned enemyShadowSkips;
#endif

#ifdef HLD_COMPARE_ENEMY_SHADOW
static bool enemyShadowEnabled;

void hldReportEnemyShadow(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita enemy shadow frame=%d enabled=%d skipped=%u\n",
            frame, (int)enemyShadowEnabled, enemyShadowSkips);
    enemyShadowSkips = 0;
}
#endif

bool hldBindShadowProbes(VMContext* ctx, uint32_t x, uint32_t y, uint32_t meeting);
bool hldShadowProbeGroups(Runner* runner, Instance* instance, const RValue* platforms, unsigned* groups);

static void bindEnemyShadow(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Object_enemy_Step_0", 9764, 0xd21da724u);
    if (code == nullptr || code->localsCount != 12 || code->argumentsCount != 0) return;
    uint8_t* bytes = codeBytes(ctx, code);
    const Variable* platforms = resolveVarDef(ctx, resolveVarOperand(bytes + 0x2500));
    if (platforms->varID < 0 || strcmp(platforms->name, "nonFloorPlatforms") != 0) return;
    if (!hldBindShadowProbes(ctx, resolveFuncOperand(bytes + 0x2544), resolveFuncOperand(bytes + 0x2520),
                             resolveFuncOperand(bytes + 0x2550))) return;

    enemyShadowContext = ctx;
    enemyShadowPlatforms = platforms->varID;
#ifdef HLD_COMPARE_ENEMY_SHADOW
    FILE* marker = fopen("ux0:data/hld-vm/native-enemy-shadow", "r");
    enemyShadowEnabled = marker != nullptr;
    if (marker != nullptr) fclose(marker);
#endif
    enemyShadowStep = code;
}

static void installEnemyShadow(VMContext* ctx) {
    if (ctx != enemyShadowContext) return;
    uint8_t* bytes = codeBytes(ctx, enemyShadowStep);
    const uint32_t word = BinaryUtils_readUint32Aligned(bytes + HLD_ENEMY_SHADOW_START);
    if ((word >> 24) != OP_PACKED_VAR_READ) {
        enemyShadowContext = nullptr;
#ifdef HLD_COMPARE_ENEMY_SHADOW
        enemyShadowEnabled = false;
#endif
        logWarn("HLD: unexpected packed enemy shadow opcode %02x\n", (unsigned)(word >> 24));
        return;
    }
    BinaryUtils_writeUint32(bytes + HLD_ENEMY_SHADOW_START,
                           (word & 0x00ffffffu) | ((uint32_t)OP_PACKED_ENEMY_SHADOW << 24));
    logInfo("HLD: bound the enemy shadow probe guard\n");
}

static bool findEnemyShadowGroups(VMContext* ctx, unsigned* groups) {
    Runner* runner = ctx->runner;
    if (ctx != enemyShadowContext || runner == nullptr || runner->spatialGrid == nullptr ||
        ctx->globalScopeInstance == nullptr || ctx->currentCallFrame->self == nullptr) return false;
#ifdef HLD_COMPARE_ENEMY_SHADOW
    if (!enemyShadowEnabled) return false;
#endif
    const GMLReal epsilon = runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    const RValue platforms = Instance_getSelfVar(ctx->globalScopeInstance, enemyShadowPlatforms);
    return hldShadowProbeGroups(runner, ctx->currentCallFrame->self, &platforms, groups);
}

static bool hldSceneryCullReference(VMContext* ctx, const uint8_t* bytes) {
    CallFrame* frame = ctx->currentCallFrame;
    Instance* inst = frame->self;
    if (inst == nullptr || inst->instanceId >= (1u << 24)) return false;

    // SCREEN's End Step updates one of four scenery groups each frame.
    GMLReal epsilon = ctx->runner->mathEpsilon;
    RValue group = frame->localVars[4];
    if (group.type != RVALUE_REAL || !(epsilon >= 0.0)) return false;
    if (!(GMLReal_fabs((inst->instanceId % 4) - group.real) <= epsilon)) return true;
    if (RValue_toBool(resolveVariableRead(ctx, INSTANCE_GLOBAL, resolveVarOperand(bytes + 0x03e4)))) return false;

    // Compare the original bounds in the same order. Unusual values use the VM.
    bool intersects = true;
    for (uint32_t axis = 0; axis < 4 && intersects; axis++) {
        RValue edge = resolveVariableRead(ctx, INSTANCE_SELF, resolveVarOperand(bytes + 0x042c + axis * 24));
        RValue view = frame->localVars[6 + axis];
        if ((edge.type != RVALUE_REAL && edge.type != RVALUE_INT32) ||
            (view.type != RVALUE_REAL && view.type != RVALUE_INT32)) return false;
        GMLReal diff = RValue_toReal(edge) - RValue_toReal(view);
        if (!isfinite(diff)) return false;
        intersects = (axis & 1) ? -diff > epsilon : diff > epsilon;
    }

    const uint8_t* visibility = codeBytes(ctx, ctx->hldSceneryVisibilityCode);
    bool printing = RValue_toBool(resolveVariableRead(ctx, INSTANCE_GLOBAL, resolveVarOperand(visibility + 0x04)));
    bool visible = printing || (intersects &&
        !RValue_toBool(resolveVariableRead(ctx, INSTANCE_SELF, resolveVarOperand(visibility + 0x30))) &&
        !RValue_toBool(resolveVariableRead(ctx, INSTANCE_SELF, resolveVarOperand(visibility + 0x54))) &&
        !RValue_toBool(resolveVariableRead(ctx, INSTANCE_SELF, resolveVarOperand(visibility + 0x64))) &&
        !RValue_toBool(resolveVariableRead(ctx, INSTANCE_SELF, resolveVarOperand(visibility + 0x74))));
    resolveVariableWrite(ctx, INSTANCE_SELF, resolveVarOperand(bytes + 0x0424), RValue_makeReal(!intersects));
    resolveVariableWrite(ctx, INSTANCE_SELF, resolveVarOperand(bytes + 0x04bc), RValue_makeReal(visible));
    return true;
}

#define HLD_COMPARE_SCENERY_PASS
typedef struct {
    const VMContext* context;
    int32_t edges[4];
    int32_t hidden[4];
    int32_t canEdit, printing, inactive;
} SceneryPass;

static SceneryPass sceneryPass;

static int32_t sceneryPlainVariable(VMContext* ctx, const uint8_t* operand) {
    const uint32_t reference = resolveVarOperand(operand);
    if (((reference >> 24) & 0xf8) != VARTYPE_NORMAL) return -1;
    return resolveVarDef(ctx, reference)->varID;
}

static void bindSceneryPass(VMContext* ctx, const uint8_t* step, const uint8_t* visibility) {
    SceneryPass bound = {.context = ctx};
    static const uint16_t hiddenOperands[] = {0x30, 0x54, 0x64, 0x74};
    repeat(4, i) {
        bound.edges[i] = sceneryPlainVariable(ctx, step + 0x042c + i * 24);
        bound.hidden[i] = sceneryPlainVariable(ctx, visibility + hiddenOperands[i]);
        if (bound.edges[i] < 0 || bound.hidden[i] < 0) return;
    }
    bound.canEdit = sceneryPlainVariable(ctx, step + 0x03e4);
    bound.printing = sceneryPlainVariable(ctx, visibility + 0x04);
    bound.inactive = sceneryPlainVariable(ctx, step + 0x0424);
    if (bound.canEdit < 0 || bound.printing < 0 || bound.inactive < 0) return;

    const uint32_t visible = resolveVarOperand(step + 0x04bc);
    if (((visible >> 24) & 0xf8) != VARTYPE_NORMAL) return;
    const Variable* builtin = resolveVarDef(ctx, visible);
    if (builtin->varID != VARIABLE_BUILTIN || builtin->builtinVarId != BUILTIN_VAR_VISIBLE) return;
    sceneryPass = bound;
    logInfo("HLD: bound the direct scenery pass\n");
}

enum SceneryDecision { SCENERY_SCRIPT, SCENERY_UNCHANGED, SCENERY_WRITE };

typedef struct {
    enum SceneryDecision decision;
    bool inactive, visible;
} SceneryChanges;

// The same SCREEN pass and update order, with the field operands already
// resolved. Each object reads live state after the preceding callback.
static inline SceneryChanges sceneryChanges(VMContext* ctx, Instance* instance) {
    const Instance* global = ctx->globalScopeInstance;
    const CallFrame* frame = ctx->currentCallFrame;
    const SceneryChanges fallback = {SCENERY_SCRIPT, false, false};
    if (instance->instanceId >= (1u << 24)) return fallback;

    const GMLReal epsilon = ctx->runner->mathEpsilon;
    const RValue group = frame->localVars[4];
    if (group.type != RVALUE_REAL || !(epsilon >= 0.0)) return fallback;
    if (!(GMLReal_fabs((instance->instanceId % 4) - group.real) <= epsilon))
        return (SceneryChanges){SCENERY_UNCHANGED, false, false};

    if (RValue_toBool(IntRValueHashMap_get(&global->selfVars, sceneryPass.canEdit))) return fallback;
    bool intersects = true;
    for (uint32_t i = 0; i < 4 && intersects; ++i) {
        const RValue edge = Instance_getSelfVar(instance, sceneryPass.edges[i]);
        const RValue view = frame->localVars[6 + i];
        if ((edge.type != RVALUE_REAL && edge.type != RVALUE_INT32) ||
            (view.type != RVALUE_REAL && view.type != RVALUE_INT32)) return fallback;
        const GMLReal diff = RValue_toReal(edge) - RValue_toReal(view);
        if (!isfinite(diff)) return fallback;
        intersects = (i & 1) ? -diff > epsilon : diff > epsilon;
    }

    bool visible = RValue_toBool(IntRValueHashMap_get(&global->selfVars, sceneryPass.printing));
    if (!visible && intersects) {
        visible = true;
        for (uint32_t i = 0; i < 4 && visible; ++i)
            visible = !RValue_toBool(Instance_getSelfVar(instance, sceneryPass.hidden[i]));
    }
    return (SceneryChanges){SCENERY_WRITE, !intersects, visible};
}

#ifdef HLD_COMPARE_SCENERY_PASS
static unsigned sceneryPassWrites;

static inline bool useSceneryPass(int frame) {
    extern bool hldRefinedRuntime;
    (void)frame;
    return hldRefinedRuntime;
}

void hldReportSceneryPass(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery pass frame=%d enabled=%d writes=%u\n",
            frame, (int)useSceneryPass(frame - 1), sceneryPassWrites);
    sceneryPassWrites = 0;
}
#endif


static unsigned sceneryScalarWrites, sceneryUnchangedWrites, scenerySetterFallbacks;

static inline bool putSceneryInactive(Instance* instance, int32_t key, bool inactive) {
    IntRValueHashMap* map = &instance->selfVars;
    // The ordinary setter grows a sparse table at this boundary even for an existing key.
    bool keepsBucketOrder = (uint64_t)(map->count + 1) * 4 <= (uint64_t)map->capacity * 3;
#ifdef SHARED_FIELD_MASK
    keepsBucketOrder |= hasSharedFields(map);
#endif
    if (!keepsBucketOrder) return false;
    RValue* slot = IntRValueHashMap_findSlot(map, key);
    if (slot == nullptr || slot->ownsReference) return false;

    const RValue canonical = RValue_makeReal(inactive);
    if (slot->type == canonical.type && slot->gmlStackType == canonical.gmlStackType &&
        slot->assetRefType == canonical.assetRefType && memcmp(&slot->real, &canonical.real, sizeof(GMLReal)) == 0) {
        ++sceneryUnchangedWrites;
    } else {
        *slot = canonical;
        ++sceneryScalarWrites;
    }
    return true;
}

void hldReportSceneryScalarWritesAtExit(void) {
    logInfo("HLD: scenery scalar writes changed=%u unchanged=%u fallback=%u\n",
            sceneryScalarWrites, sceneryUnchangedWrites, scenerySetterFallbacks);
}

extern bool hldReducedVMWork;

void hldReportSceneryScalarWrites(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery scalar writes frame=%d changed=%u unchanged=%u fallback=%u\n",
            frame, sceneryScalarWrites, sceneryUnchangedWrites, scenerySetterFallbacks);
    sceneryScalarWrites = sceneryUnchangedWrites = scenerySetterFallbacks = 0;
}

static inline bool hldSceneryCull(VMContext* ctx, const uint8_t* step) {
    Instance* instance = ctx->currentCallFrame->self;
    if (sceneryPass.context != ctx || ctx->globalScopeInstance == nullptr ||
        instance == nullptr || instance->objectIndex == STRUCT_OBJECT_INDEX)
        return hldSceneryCullReference(ctx, step);
#ifdef HLD_COMPARE_SCENERY_PASS
    if (!useSceneryPass(ctx->runner->frameCount)) return hldSceneryCullReference(ctx, step);
#endif
    const SceneryChanges change = sceneryChanges(ctx, instance);
#ifdef HLD_VERIFY_SCENERY_PASS
    const int32_t stackTop = ctx->stack.top;
    const bool reference = hldSceneryCullReference(ctx, step);
    require(reference == (change.decision != SCENERY_SCRIPT));
    require(stackTop == ctx->stack.top);
    if (change.decision == SCENERY_WRITE) {
        const RValue inactive = Instance_getSelfVar(instance, sceneryPass.inactive);
        require(inactive.type == RVALUE_REAL && inactive.real == (GMLReal)change.inactive);
        require(instance->visible == change.visible);
        static unsigned checked;
        if (++checked == 10000) logInfo("HLD: checked 10,000 direct scenery writes\n");
    }
    return reference;
#else
    if (change.decision == SCENERY_SCRIPT) return false;
    if (change.decision == SCENERY_WRITE) {
        if (hldReducedVMWork) {
            const bool inPlace = putSceneryInactive(instance, sceneryPass.inactive, change.inactive);
            if (!inPlace) {
                ++scenerySetterFallbacks;
                Instance_setSelfVar(instance, sceneryPass.inactive, RValue_makeReal(change.inactive));
            }
            instance->visible = change.visible;
        } else {
            Instance_setSelfVar(instance, sceneryPass.inactive, RValue_makeReal(change.inactive));
            VMBuiltins_setVariable(ctx, instance, BUILTIN_VAR_VISIBLE, "visible", RValue_makeReal(change.visible), -1);
        }
#ifdef HLD_COMPARE_SCENERY_PASS
        ++sceneryPassWrites;
#endif
    }
    return true;
#endif
}

static bool hldSceneryCanSkipUpdate(VMContext* ctx) {
    Instance* scenery = ctx->currentCallFrame->self;
    // RecessingScenery has its own update even when offscreen.
    if (scenery->objectIndex == 36 || ctx->globalScopeInstance == nullptr ||
        ctx->hldSceneryInactiveVarId < 0 || ctx->hldRoomTimeVarId < 0) return false;

    // The ordinary scenery branch tests both boolean inactivity and numeric
    // inequality with zero. Keep the comparisons and room warm-up intact.
    RValue inactive = Instance_getSelfVar(scenery, ctx->hldSceneryInactiveVarId);
    if ((inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32) || !RValue_toBool(inactive)) return false;
    GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 1.0 && RValue_toReal(inactive) > epsilon)) return false;

    RValue roomTime = Instance_getSelfVar(ctx->globalScopeInstance, ctx->hldRoomTimeVarId);
    return (roomTime.type == RVALUE_REAL || roomTime.type == RVALUE_INT32) &&
        RValue_toReal(roomTime) - (GMLReal) 16 > epsilon;
}

extern bool hldSceneryDrawLists;

enum { DRAW_FLOOR, DRAW_BACKGROUND, DRAW_FOREGROUND, SCENERY_LAYER_COUNT };
enum { SCENERY_LIST_COUNT = 7 };

typedef struct {
    const VMContext* context;
    int32_t level, layer[SCENERY_LAYER_COUNT];
    int32_t variable[SCENERY_LIST_COUNT], index[SCENERY_LIST_COUNT];
} SceneryDrawLists;

static SceneryDrawLists sceneryDrawLists;
static unsigned sceneryDrawListVisits, sceneryDrawListAppends;

static void bindSceneryDrawLists(VMContext* ctx, const uint8_t* step) {
    // These offsets are read only after binding the exact original SCREEN
    // End Step. The seven entries follow the branches in the original code.
    static const uint16_t layerOffsets[] = {0x548, 0x650, 0x700};
    static const uint16_t indexOffsets[] = {0x578, 0x5c8, 0x620, 0x680, 0x6d0, 0x730, 0x780};
    static const uint16_t arrayOffsets[] = {0x584, 0x5d4, 0x62c, 0x68c, 0x6dc, 0x73c, 0x78c};
    SceneryDrawLists binding = {.context = ctx, .level = sceneryPlainVariable(ctx, step + 0x558)};
    if (binding.level < 0) return;
    repeat(SCENERY_LAYER_COUNT, i) {
        binding.layer[i] = sceneryPlainVariable(ctx, step + layerOffsets[i]);
        if (binding.layer[i] < 0) return;
    }
    repeat(SCENERY_LIST_COUNT, i) {
        binding.index[i] = sceneryPlainVariable(ctx, step + indexOffsets[i]);
        const uint32_t arrayRef = resolveVarOperand(step + arrayOffsets[i]);
        if (((arrayRef >> 24) & 0xf8) != VARTYPE_ARRAY) return;
        binding.variable[i] = resolveVarDef(ctx, arrayRef)->varID;
        if (binding.index[i] < 0 || binding.index[i] >= 19 || binding.variable[i] < 0) return;
    }
    const uint32_t idRef = resolveVarOperand(step + 0x56c);
    const Variable* id = resolveVarDef(ctx, idRef);
    if (((idRef >> 24) & 0xf8) != VARTYPE_NORMAL || id->varID != VARIABLE_BUILTIN ||
        id->builtinVarId != BUILTIN_VAR_ID) return;
    sceneryDrawLists = binding;
}

static bool appendSceneryDrawList(VMContext* ctx) {
    if (sceneryDrawLists.context != ctx || ctx->globalScopeInstance == nullptr) return false;
    CallFrame* frame = ctx->currentCallFrame;
    Instance* self = frame->self;
    if (self == nullptr) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.25)) return false;

    const RValue inactive = Instance_getSelfVar(self, ctx->hldSceneryInactiveVarId);
    if (inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32) return false;
    if (!(GMLReal_fabs(RValue_toReal(inactive)) <= epsilon)) {
        ++sceneryDrawListVisits;
        return true;
    }

    int layer;
    for (layer = 0; layer < SCENERY_LAYER_COUNT; ++layer) {
        const RValue flag = Instance_getSelfVar(self, sceneryDrawLists.layer[layer]);
        if (flag.type != RVALUE_REAL && flag.type != RVALUE_INT32 &&
            flag.type != RVALUE_BOOL && flag.type != RVALUE_UNDEFINED) return false;
        if (RValue_toBool(flag)) break;
    }
    if (layer == SCENERY_LAYER_COUNT) {
        ++sceneryDrawListVisits;
        return true;
    }
    const RValue level = Instance_getSelfVar(self, sceneryDrawLists.level);
    if (level.type != RVALUE_REAL && level.type != RVALUE_INT32) return false;
    const GMLReal number = RValue_toReal(level);
    static const unsigned offsets[] = {0, 3, 5};
    unsigned channel = offsets[layer];
    if (GMLReal_fabs(number) <= epsilon) {
        // The first slot for each layer is the level-zero slot.
    } else if (GMLReal_fabs(number - (GMLReal) 1.0) <= epsilon) {
        channel += 1;
    } else if (layer == DRAW_FLOOR && GMLReal_fabs(number - (GMLReal) 0.5) <= epsilon) {
        channel += 2;
    } else {
        ++sceneryDrawListVisits;
        return true;
    }

    const uint32_t local = (uint32_t) sceneryDrawLists.index[channel];
    if (frame->localVarsCount <= local) return false;
    RValue* counter = &frame->localVars[local];
    if (counter->type != RVALUE_INT32 && counter->type != RVALUE_REAL) return false;
    const GMLReal value = RValue_toReal(*counter);
    if (!(value >= 0.0 && value < 32000.0)) return false;
    const int32_t index = (int32_t) value;
    RValue* list = IntRValueHashMap_getOrInsertUndefined(&ctx->globalScopeInstance->selfVars,
                                                       sceneryDrawLists.variable[channel]);
    RValue id = RValue_makeReal((GMLReal) self->instanceId);
    id.gmlStackType = GML_TYPE_VARIABLE;
    VM_arraySetWithCoW(ctx, list, index, id);
    if (counter->type == RVALUE_REAL) counter->real = value + (GMLReal) 1.0;
    else counter->int32 = index + 1;
    counter->gmlStackType = GML_TYPE_VARIABLE;
    ++sceneryDrawListVisits;
    ++sceneryDrawListAppends;
    return true;
}

void hldReportSceneryDrawLists(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery draw lists frame=%d enabled=%d visits=%u appends=%u\n",
            frame, (int)hldSceneryDrawLists, sceneryDrawListVisits, sceneryDrawListAppends);
    sceneryDrawListVisits = sceneryDrawListAppends = 0;
}



enum {
    SCENERY_RECESS_CALL = 0x04e4,
    SCENERY_NORMAL_CALL = 0x0524,
    SCENERY_LIST_START = 0x0530,
};

static unsigned nativeSceneryCalls, nativeSceneryLists;

void hldReportNativeSceneryBodyAtExit(void) {
    logInfo("HLD: native scenery body calls=%u lists=%u\n", nativeSceneryCalls, nativeSceneryLists);
}

static uint32_t finishSceneryBody(VMContext* ctx, const uint8_t* bytes) {
    Instance* self = ctx->currentCallFrame->self;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (self == nullptr || ctx->globalScopeInstance == nullptr || !(epsilon >= 0.0 && epsilon < 1.0))
        return HLD_SCENERY_CULL_END;
    uint32_t call = SCENERY_RECESS_CALL;
    if (self->objectIndex != 36) {
        if (ctx->hldSceneryInactiveVarId < 0 || ctx->hldRoomTimeVarId < 0)
            return HLD_SCENERY_CULL_END;
        const RValue inactive = Instance_getSelfVar(self, ctx->hldSceneryInactiveVarId);
        const RValue roomTime = Instance_getSelfVar(ctx->globalScopeInstance, ctx->hldRoomTimeVarId);
        if ((inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32) ||
            (roomTime.type != RVALUE_REAL && roomTime.type != RVALUE_INT32) ||
            !isfinite(RValue_toReal(inactive)) || !isfinite(RValue_toReal(roomTime)))
            return HLD_SCENERY_CULL_END;

        // A warm, inactive instance reaches the list test without a script.
        // The existing cull guard may already prove that it skips the list.
        call = !RValue_toBool(inactive) || RValue_toReal(roomTime) - 16.0 <= epsilon
            ? SCENERY_NORMAL_CALL : 0;
    }

    if (call != 0) {
        const OpWord op = {BinaryUtils_readUint32Aligned(bytes + call)};
        const bool consumesReturn = OpWord_opcode(op) == OP_PACKED_CALL_DISCARD ||
            (OpWord_opcode(op) == OP_CALL && BinaryUtils_readUint32Aligned(bytes + call + 8) == 0x9e050000u);
        if (!consumesReturn || OpWord_extra(op) != 0)
            return HLD_SCENERY_CULL_END;
        // Match the interpreter's CALL boundary and use the same cached
        // target, frame metadata, ownership and return-discard semantics.
        ctx->ip = call + 8;
        handleCall(ctx, op, bytes + call + 4, true);
        ++nativeSceneryCalls;
    }

    if (!appendSceneryDrawList(ctx)) return SCENERY_LIST_START;
    ++nativeSceneryLists;
    return HLD_SCENERY_NEXT_INSTANCE;
}

extern bool hldNativeSceneryBody;

void hldReportNativeSceneryBody(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita native scenery body frame=%d enabled=%d calls=%u lists=%u\n",
            frame, (int)hldNativeSceneryBody, nativeSceneryCalls, nativeSceneryLists);
    nativeSceneryCalls = nativeSceneryLists = 0;
}

static unsigned sceneryCursorEntries, sceneryCursorSkipped;

void hldReportSceneryCursorAtExit(void) {
    logInfo("HLD: scenery cursor entries=%u skipped=%u\n", sceneryCursorEntries, sceneryCursorSkipped);
}

#ifdef PLATFORM_VITA
extern bool hldSceneryCursor;

void hldReportSceneryCursor(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery cursor frame=%d enabled=%d entries=%u skipped=%u\n",
            frame, (int)hldSceneryCursor, sceneryCursorEntries, sceneryCursorSkipped);
    sceneryCursorEntries = sceneryCursorSkipped = 0;
}
#else
static const bool hldSceneryCursor = true;
#endif


static unsigned skippedSceneryBeforeSwitch;
extern bool hldScenerySkip;

void hldReportScenerySkip(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery skips frame=%d enabled=%d calls=%u\n",
            frame, (int)hldScenerySkip, skippedSceneryBeforeSwitch);
    skippedSceneryBeforeSwitch = 0;
}


static void skipInactiveScenery(VMContext* ctx) {
    if (!hldScenerySkip) return;
    if (ctx != sceneryPass.context || ctx->globalScopeInstance == nullptr ||
        ctx->hldSceneryInactiveVarId < 0 || ctx->hldRoomTimeVarId < 0) return;
#ifdef HLD_COMPARE_SCENERY_PASS
    if (!useSceneryPass(ctx->runner->frameCount)) return;
#endif
    const CallFrame* call = ctx->currentCallFrame;
    if (call->localVarsCount <= 4) return;
    const RValue group = call->localVars[4];
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (group.type != RVALUE_REAL || !(group.real >= 0.0 && group.real <= 3.0) ||
        group.real != (unsigned)group.real || !(epsilon >= 0.0 && epsilon < 0.5)) return;
    const unsigned selected = (unsigned)group.real;
    const RValue roomTime = Instance_getSelfVar(ctx->globalScopeInstance, ctx->hldRoomTimeVarId);
    if ((roomTime.type != RVALUE_REAL && roomTime.type != RVALUE_INT32) ||
        !(RValue_toReal(roomTime) - (GMLReal)16 > epsilon)) return;

    EnvFrame* env = ctx->envStack;
    while (env->currentIndex + 1 < env->instanceCount) {
        Instance* next = env->instanceList[env->currentIndex + 1];
        if (!next->active) {
            ++env->currentIndex;
            continue;
        }
        // The non-selected groups leave visibility and inactive state as-is.
        // Recessing scenery still runs an update when inactive.
        if (next->instanceId >= (1u << 24) || next->instanceId % 4 == selected ||
            next->objectIndex == 36 || next->objectIndex == STRUCT_OBJECT_INDEX) break;
        const RValue inactive = Instance_getSelfVar(next, ctx->hldSceneryInactiveVarId);
        if ((inactive.type != RVALUE_REAL && inactive.type != RVALUE_INT32) ||
            !RValue_toBool(inactive) || !(RValue_toReal(inactive) > epsilon)) break;
        ++env->currentIndex;
        ++sceneryCursorEntries;
        ++sceneryCursorSkipped;
        ++skippedSceneryBeforeSwitch;
    }
}

enum { SCENERY_SKIP_NO_GROUP = 4, SCENERY_SKIP_REFRESH = 5 };

static unsigned scenerySkipGuardReads, scenerySkipGuardReuses;

void hldReportScenerySkipGuardAtExit(void) {
    logInfo("HLD: scenery guard reads=%u reuses=%u early_skips=%u\n",
            scenerySkipGuardReads, scenerySkipGuardReuses, skippedSceneryBeforeSwitch);
}

static unsigned scenerySkipGroup(VMContext* ctx) {
    if (ctx != sceneryPass.context || ctx->globalScopeInstance == nullptr ||
        ctx->hldSceneryInactiveVarId < 0 || ctx->hldRoomTimeVarId < 0) return SCENERY_SKIP_NO_GROUP;
#ifdef HLD_COMPARE_SCENERY_PASS
    if (!useSceneryPass(ctx->runner->frameCount)) return SCENERY_SKIP_NO_GROUP;
#endif
    const CallFrame* call = ctx->currentCallFrame;
    if (call->localVarsCount <= 4) return SCENERY_SKIP_NO_GROUP;
    const RValue group = call->localVars[4];
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (group.type != RVALUE_REAL || !(group.real >= 0.0 && group.real <= 3.0) ||
        group.real != (unsigned)group.real || !(epsilon >= 0.0 && epsilon < 0.5)) return SCENERY_SKIP_NO_GROUP;
    const RValue roomTime = Instance_getSelfVar(ctx->globalScopeInstance, ctx->hldRoomTimeVarId);
    if ((roomTime.type != RVALUE_REAL && roomTime.type != RVALUE_INT32) ||
        !(RValue_toReal(roomTime) - (GMLReal)16 > epsilon)) return SCENERY_SKIP_NO_GROUP;
    return (unsigned)group.real;
}

static void skipSceneryWithGuard(VMContext* ctx, unsigned* savedGroup) {
    if (*savedGroup == SCENERY_SKIP_REFRESH) {
        *savedGroup = scenerySkipGroup(ctx);
        ++scenerySkipGuardReads;
    } else {
        ++scenerySkipGuardReuses;
    }
    const unsigned selected = *savedGroup;
    if (selected == SCENERY_SKIP_NO_GROUP) return;

    EnvFrame* env = ctx->envStack;
    while (env->currentIndex + 1 < env->instanceCount) {
        Instance* next = env->instanceList[env->currentIndex + 1];
        if (!next->active) {
            ++env->currentIndex;
            continue;
        }
        // The non-selected groups leave visibility and inactive state as-is.
        // Recessing scenery still runs an update when inactive.
        if (next->instanceId >= (1u << 24) || next->instanceId % 4 == selected ||
            next->objectIndex == 36 || next->objectIndex == STRUCT_OBJECT_INDEX) break;
        const RValue inactive = Instance_getSelfVar(next, ctx->hldSceneryInactiveVarId);
        const bool canSkip = inactive.type == RVALUE_REAL ? inactive.real > (GMLReal)0.5 :
                            inactive.type == RVALUE_INT32 && inactive.int32 > 0;
        if (!canSkip) break;
        ++env->currentIndex;
        ++sceneryCursorEntries;
        ++sceneryCursorSkipped;
        ++skippedSceneryBeforeSwitch;
    }
}

extern bool hldSceneryGuard;

void hldReportScenerySkipGuard(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery guard frame=%d reads=%u reuses=%u\n",
            frame, scenerySkipGuardReads, scenerySkipGuardReuses);
    scenerySkipGuardReads = scenerySkipGuardReuses = 0;
}

static void popSceneryEnv(VMContext* ctx, OpWord word, uint32_t instrAddr, const uint8_t* bytes) {
    const uint32_t body = HLD_SCENERY_CULL_START;
    // The caller has already matched the exact SCREEN function and loop.
    // Keep one with-block snapshot, its iteration order and its normal
    // active filtering. Re-evaluate live fields after each scripted callback.
    const uint32_t continuation = ctx->ip;
    unsigned savedGroup = SCENERY_SKIP_REFRESH;
    while (true) {
        if (hldScenerySkip && hldSceneryGuard) skipSceneryWithGuard(ctx, &savedGroup);
        else skipInactiveScenery(ctx);
        handlePopEnv(ctx, word, instrAddr);
        if (ctx->ip != body) return;
        ++sceneryCursorEntries;
        if (!hldSceneryCull(ctx, bytes)) return;
        if (!hldSceneryCanSkipUpdate(ctx)) {
            savedGroup = SCENERY_SKIP_REFRESH;
            ctx->ip = hldNativeSceneryBody ? finishSceneryBody(ctx, bytes) : HLD_SCENERY_CULL_END;
            if (ctx->ip != HLD_SCENERY_NEXT_INSTANCE) return;
        } else {
            ++sceneryCursorSkipped;
        }
        ctx->ip = continuation;
    }
}

static bool finishRestingScenery(VMContext* ctx, const CodeEntry* code);


enum { PALETTE_TAIL = 0x29c, PALETTE_COLORS = 14, PALETTE_WRITES = 1 + 2 * PALETTE_COLORS };

typedef struct {
    const VMContext* context;
    const uint8_t* program;
    uint32_t reset, set, uniform;
    int32_t handles[PALETTE_WRITES];
} PaletteTail;

typedef struct {
    int32_t handle;
    float value[3];
} PaletteWrite;

static PaletteTail paletteTail;
static unsigned paletteTails;
extern bool hldPaletteTail;

void hldReportPaletteTail(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita palette upload frame=%d enabled=%d calls=%u\n", frame, (int)hldPaletteTail, paletteTails);
    paletteTails = 0;
}


static bool paletteBuiltin(VMContext* ctx, const uint8_t* program, unsigned offset, const char* name, uint32_t* index) {
    *index = resolveFuncOperand(program + offset + 4);
    const BuiltinFunc builtin = VM_findBuiltin(ctx, name);
    return *index < ctx->funcCallCacheCount && builtin != nullptr &&
        ctx->funcCallCache[*index].builtin == (void*)builtin &&
        ctx->funcCallCache[*index].scriptCodeIndex < 0;
}

static int32_t paletteHandleId(VMContext* ctx, const uint8_t* program, unsigned offset, const char* name) {
    const Variable* variable = resolveVarDef(ctx, resolveVarOperand(program + offset + 4));
    return strcmp(name, variable->name) == 0 ? variable->varID : -1;
}

static void bindPaletteTail(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_shader_set_paletteSwap", 5152, 0x78c5bc7eu);
    if (code == nullptr || code->localsCount != 11) return;
    const uint8_t* program = codeBytes(ctx, code);
    PaletteTail tail = {.context = ctx, .program = program};
    if (!paletteBuiltin(ctx, program, PALETTE_TAIL, "shader_reset", &tail.reset) ||
        !paletteBuiltin(ctx, program, 0x2b0, "shader_set", &tail.set) ||
        !paletteBuiltin(ctx, program, 0x2cc, "shader_set_uniform_f", &tail.uniform)) return;
    tail.handles[0] = paletteHandleId(ctx, program, 0x2c4, "paletteShaderLeeway");
    if (tail.handles[0] < 0) return;
    for (unsigned color = 0; color < 2 * PALETTE_COLORS; ++color) {
        const unsigned offset = 0x370 + (color / 2) * 0x13c + (color % 2) * 0x98;
        uint32_t function;
        if (!paletteBuiltin(ctx, program, offset, "shader_set_uniform_f", &function) ||
            function != tail.uniform ||
            !paletteBuiltin(ctx, program, offset - 0x74, "color_get_blue", &function) ||
            !paletteBuiltin(ctx, program, offset - 0x48, "color_get_green", &function) ||
            !paletteBuiltin(ctx, program, offset - 0x1c, "color_get_red", &function)) return;
        char name[32];
        snprintf(name, sizeof(name), "paletteShaderColor%u%c", color / 2 + 1, "ab"[color % 2]);
        tail.handles[color + 1] = paletteHandleId(ctx, program, offset - 8, name);
        if (tail.handles[color + 1] < 0) return;
    }
    paletteTail = tail;
    logInfo("HLD: bound direct palette upload\n");
}

static bool paletteNumber(RValue value, GMLReal* number) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *number = RValue_toReal(value);
    return isfinite(*number);
}

static bool preparePaletteWrites(VMContext* ctx, PaletteWrite* writes) {
    if (ctx->runner == nullptr || ctx->runner->renderer == nullptr || ctx->globalScopeInstance == nullptr) return false;
    const RValue* local = ctx->currentCallFrame->localVars;
    const RValue inputs[] = {local[3], local[4]};
    GMLReal number;
    if (inputs[0].type != RVALUE_ARRAY || inputs[1].type != RVALUE_ARRAY ||
        !paletteNumber(local[1], &number)) return false;
    writes[0].value[0] = (float)number;
    for (unsigned i = 0; i < PALETTE_WRITES; ++i) {
        const RValue handle = Instance_getSelfVar(ctx->globalScopeInstance, paletteTail.handles[i]);
        if (!paletteNumber(handle, &number) || number < INT32_MIN || number > INT32_MAX) return false;
        writes[i].handle = (int32_t)number;
    }
    for (unsigned i = 0; i < 2 * PALETTE_COLORS; ++i) {
        const RValue value = GMLArray_get(inputs[i % 2].array, (int32_t)(i / 2));
        if (!paletteNumber(value, &number) || number < INT32_MIN || number > INT32_MAX) return false;
        const int32_t color = (int32_t)number;
        writes[i + 1].value[0] = (float)((GMLReal)BGR_R(color) / (GMLReal)255);
        writes[i + 1].value[1] = (float)((GMLReal)BGR_G(color) / (GMLReal)255);
        writes[i + 1].value[2] = (float)((GMLReal)BGR_B(color) / (GMLReal)255);
    }
    return true;
}

#ifdef HLD_CHECK_PALETTE_TAIL
static PaletteWrite expectedPalette[PALETTE_WRITES];
static unsigned paletteCall;
static bool checkingPalette;

static void checkPaletteCall(VMContext* ctx, const uint8_t* program, OpWord op, const uint8_t* operand) {
    if (!checkingPalette || ctx != paletteTail.context || program != paletteTail.program) return;
    const uint32_t function = resolveFuncOperand(operand);
    const unsigned count = (unsigned)OpWord_extra(op);
    const RValue* top = ctx->stack.slots + ctx->stack.top;
    if (paletteCall < 2) {
        require(count == paletteCall && function == (paletteCall == 0 ? paletteTail.reset : paletteTail.set));
        if (paletteCall == 1) require(RValue_toReal(top[-1]) == 2.0);
    } else {
        const unsigned write = paletteCall - 2;
        const unsigned values = write == 0 ? 1 : 3;
        require(write < PALETTE_WRITES && function == paletteTail.uniform && count == values + 1);
        require((int32_t)RValue_toReal(top[-1]) == expectedPalette[write].handle);
        for (unsigned i = 0; i < values; ++i) {
            const float original = (float)RValue_toReal(top[-2 - (int)i]);
            require(memcmp(&original, &expectedPalette[write].value[i], sizeof(original)) == 0);
        }
    }
    if (++paletteCall == 2 + PALETTE_WRITES) {
        checkingPalette = false;
        if (++paletteTails == 100) logInfo("HLD: checked 100 direct palette uploads\n");
    }
}
#endif

static bool runPaletteTail(VMContext* ctx, const uint8_t* program) {
    if (!hldPaletteTail || ctx != paletteTail.context || program != paletteTail.program) return false;
#ifdef HLD_CHECK_PALETTE_TAIL
    require(!checkingPalette);
    checkingPalette = preparePaletteWrites(ctx, expectedPalette);
    paletteCall = 0;
    return false;
#else
    PaletteWrite writes[PALETTE_WRITES];
    if (!preparePaletteWrites(ctx, writes)) return false;
    Renderer* renderer = ctx->runner->renderer;
    renderer->vtable->gpuResetShader(renderer);
    renderer->vtable->gpuSetShader(renderer, 2);
    renderer->vtable->shaderSetUniformF(renderer, writes[0].handle, 1, writes[0].value[0], 0, 0, 0);
    for (unsigned i = 1; i < PALETTE_WRITES; ++i) {
        const float* value = writes[i].value;
        renderer->vtable->shaderSetUniformF(renderer, writes[i].handle, 3, value[0], value[1], value[2], 0);
    }
    ++paletteTails;
    return true;
#endif
}

extern bool hldPhaseMineQuery;

enum { OP_PACKED_PHASE_MINE_TRIGGER = 0x8f };

bool hldPhaseMineTrigger(VMContext* ctx, bool* triggered);

extern bool hldDormantMines;
static unsigned dormantMineUpdates;

void hldReportDormantMines(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita dormant mines frame=%d enabled=%d updates=%u\n",
            frame, (int)hldDormantMines, dormantMineUpdates);
    dormantMineUpdates = 0;
}

enum { OP_PACKED_DORMANT_MINE = 0x91 };
static int32_t phaseMineTimer, phaseMineCooldown, phaseMineState, phaseMineOff;

#ifdef HLD_VERIFY_DORMANT_MINE
static const CallFrame* predictedMineFrame;
static GMLReal predictedMineTimer;
static unsigned checkedDormantMines;
#endif

bool hldPhaseMineTriggerFor(VMContext* ctx, Instance* caller, bool* triggered);
static const CodeEntry* dormantMineEntry;
static unsigned mineEntryReturns;
extern bool hldMineEntry;

void hldReportMineEntry(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita dormant mine entries frame=%d enabled=%d calls=%u\n",
            frame, (int)hldMineEntry, mineEntryReturns);
    mineEntryReturns = 0;
}


static void bindDormantMine(VMContext* ctx, const CodeEntry* event, uint8_t* bytes) {
    if (!hldDormantMines) return;
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
    const OpWord pause = {BinaryUtils_readUint32Aligned(bytes)};
    if (pureGuards.context == ctx && pureGuards.pause != nullptr && OpWord_opcode(pause) == OP_PACKED_PURE_GUARD)
        dormantMineEntry = event;
    logInfo("HLD: bound the direct dormant mine update\n");
}

static bool planDormantMine(VMContext* ctx, Instance* self, GMLReal* next) {
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
    if (!hldPhaseMineTriggerFor(ctx, self, &triggered) || triggered) return false;
    *next = timer.real + 1;
    return true;
}

static void applyDormantMine(Instance* self, GMLReal next) {
    RValue age = RValue_makeReal(next), ready = RValue_makeReal(0);
    age.gmlStackType = ready.gmlStackType = GML_TYPE_VARIABLE;
    Instance_setSelfVar(self, phaseMineTimer, age);
    Instance_setSelfVar(self, phaseMineCooldown, ready);
    self->imageIndex = 0;
    ++dormantMineUpdates;
}

static bool tryDormantMine(VMContext* ctx) {
    Instance* self = ctx->currentCallFrame->self;
    GMLReal next;
    if (!planDormantMine(ctx, self, &next)) return false;
#ifdef HLD_VERIFY_DORMANT_MINE
    require(predictedMineFrame == nullptr);
    predictedMineFrame = ctx->currentCallFrame;
    predictedMineTimer = next;
    return false;
#else
    applyDormantMine(self, next);
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



static void bindPhaseMineTrigger(VMContext* ctx) {
    if (!hldPhaseMineQuery) return;
    const CodeEntry* mine = matchHldCode(ctx, "gml_Object_PhaseMine_Step_0", 1864, 0xff2ea8d4u);
    if (mine == nullptr || ctx->dataWin->objt.count <= 428) return;
    uint8_t* bytes = codeBytes(ctx, mine);
    const uint8_t* operand = bytes + 0xf4;
    const uint32_t function = resolveFuncOperand(operand);
    const BuiltinFunc builtin = VM_findBuiltin(ctx, "place_meeting");
    if (builtin == nullptr || function >= ctx->funcCallCacheCount ||
        ctx->funcCallCache[function].builtin != (void*)builtin ||
        ctx->funcCallCache[function].scriptCodeIndex >= 0) return;
    bindDormantMine(ctx, mine, bytes);
    BinaryUtils_writeUint32(bytes + 0xd8, (uint32_t)OP_PACKED_PHASE_MINE_TRIGGER << 24 | 0x0f01a3u);
    logInfo("HLD: bound the combined phase mine trigger query\n");
}

static bool useOriginalDistortion;

extern bool hldIdleScenery;

typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t canEdit, sticker;
} IdleScenery;

static IdleScenery idleScenery;
static unsigned idleSceneryCalls;

static int32_t idleSceneryVar(VMContext* ctx, const uint8_t* bytes, const char* name) {
    const uint32_t operand = resolveVarOperand(bytes);
    if (((operand >> 24) & 0xf8) != VARTYPE_NORMAL) return -1;
    const Variable* variable = resolveVarDef(ctx, operand);
    return strcmp(variable->name, name) == 0 ? variable->varID : -1;
}

static void bindIdleScenery(VMContext* ctx) {
    idleScenery = (IdleScenery){0};
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_Scenery_Step", 1440, 0x82213ed2u);
    const CodeEntry* moved = matchHldCode(ctx, "gml_Script_MovedInEditor", 136, 0x0f222736u);
    if (code == nullptr || code->localsCount != 2 || code->argumentsCount != 0 ||
        moved == nullptr || moved->localsCount != 1 || moved->argumentsCount != 0) return;

    const uint8_t* bytes = codeBytes(ctx, code);
    const uint32_t call = resolveFuncOperand(bytes + 0x18);
    if (call >= ctx->funcCallCacheCount || ctx->funcCallCache[call].builtin != nullptr ||
        ctx->funcCallCache[call].scriptCodeIndex != moved - ctx->dataWin->code.entries) return;
    const int32_t editor = idleSceneryVar(ctx, bytes + 0x80, "canEdit");
    const int32_t sticker = idleSceneryVar(ctx, bytes + 0x438, "sticker");
    if (editor < 0 || sticker < 0 || editor != idleSceneryVar(ctx, bytes + 0xdc, "canEdit") ||
        editor != idleSceneryVar(ctx, codeBytes(ctx, moved) + 4, "canEdit") ||
        sticker != idleSceneryVar(ctx, bytes + 0xcc, "sticker")) return;
    idleScenery = (IdleScenery){ctx, code, editor, sticker};
    logInfo("HLD: bound the idle scenery script return\n");
}

static inline bool sceneryHasNoUpdate(VMContext* ctx, Instance* self) {
    if (ctx->globalScopeInstance == nullptr || self == nullptr || self == ctx->globalScopeInstance ||
        self->objectIndex < 0 || self->objectIndex == STRUCT_OBJECT_INDEX) return false;
    // MovedInEditor takes its constant-false path whenever the editor
    // is inactive, so safeChild cannot cause a position or visibility write.
    // The remaining gameplay branch can run only for a sticker.
    return !RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, idleScenery.canEdit)) &&
           !RValue_toBool(Instance_getSelfVar(self, idleScenery.sticker));
}

void hldReportIdleScenery(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita idle scenery frame=%d enabled=%d calls=%u\n", frame, (int)hldIdleScenery, idleSceneryCalls);
    idleSceneryCalls = 0;
}

#if !IS_WAD17_OR_HIGHER_ENABLED && !defined(ENABLE_VM_TRACING) &&     !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)

#if defined(__GNUC__) && !defined(__clang__)
__attribute__((noinline, noclone, optimize("no-crossjumping")))
#endif
static RValue executeThreadedInstructions(VMContext* ctx, CodeEntry* code) {
    // codeLength and bytecodeBase are invariant for the lifetime of this executeBytecodeInstructions call, so let's hoist them to avoid the compiler emitting code to
    // reload the values at the end of every iteration.
    const uint32_t codeLength = code->length;
    ctx->bytecodeBase = codeBytes(ctx, code);
    const uint8_t* const bytecodeBase = ctx->bytecodeBase;
    // If you just joined the stream: ip is short for instruction pointer chat
    // The ip is mutable, so we need to use VM_SYNC_IP and VM_RELOAD_IP every time an opcode handler may access it or write to it
    uint32_t ip = code->offset;
    bool useHldDistortionLoop = code == ctx->hldDistortionCode && !useOriginalDistortion;
    bool fuseInstructions = true;
#ifdef ENABLE_VM_TRACING
    fuseInstructions = shlen(ctx->opcodesToBeTraced) == 0 && shlen(ctx->stackToBeTraced) == 0;
#endif
#ifdef ENABLE_VM_GML_PROFILER
    fuseInstructions = fuseInstructions && ctx->profiler == nullptr;
#endif
#ifdef ENABLE_VM_OPCODE_PROFILER
    fuseInstructions = fuseInstructions && !ctx->opcodeProfilerEnabled;
#endif
    bool useHldSceneryCull = fuseInstructions && code == ctx->hldSceneryStepCode;
#ifdef ENABLE_VM_TRACING
    useHldSceneryCull = useHldSceneryCull && shlen(ctx->functionCallsToBeTraced) == 0 &&
        shlen(ctx->varReadsToBeTraced) == 0 && shlen(ctx->varWritesToBeTraced) == 0;
#endif

    RValue* restrict slots = ctx->stack.slots;

    unsigned enemyShadowGroups = 0;
    bool enemyShadowGroupsValid = false;

    uint32_t instrAddr;
    OpWord opword;
    const uint8_t* extraData;
    uint8_t opcode;
    static void* const jump[256] = {
        [OP_PACKED_CALL_DISCARD] = &&thread_OP_PACKED_CALL_DISCARD,
        [OP_PACKED_MATH] = &&thread_OP_PACKED_MATH,
        [OP_PACKED_ARGUMENT] = &&thread_OP_PACKED_ARGUMENT,
        [OP_PACKED_ARGUMENT_ARRAY] = &&thread_OP_PACKED_ARGUMENT_ARRAY,
        [OP_PACKED_ENEMY_SHADOW] = &&thread_OP_PACKED_ENEMY_SHADOW,
        [OP_PACKED_SLOT_CONVERTED_READ] = &&thread_OP_PACKED_SLOT_CONVERTED_READ,
        [OP_PACKED_CONVERTED_READ] = &&thread_OP_PACKED_CONVERTED_READ,
        [OP_PACKED_SLOT_READ] = &&thread_OP_PACKED_SLOT_READ,
        [OP_PACKED_VAR_READ] = &&thread_OP_PACKED_VAR_READ,
        [OP_PACKED_VAR_WRITE] = &&thread_OP_PACKED_VAR_WRITE,
        [OP_PACKED_DORMANT_MINE] = &&thread_OP_PACKED_DORMANT_MINE,
        [OP_PACKED_PHASE_MINE_TRIGGER] = &&thread_OP_PACKED_PHASE_MINE_TRIGGER,
        [OP_PACKED_LITERAL_VALUE] = &&thread_OP_PACKED_LITERAL_VALUE,
        [OP_PACKED_LITERAL_STORE] = &&thread_OP_PACKED_LITERAL_STORE,
        [OP_PACKED_LITERAL_BRANCH] = &&thread_OP_PACKED_LITERAL_BRANCH,
        [OP_PUSH] = &&thread_OP_PUSH,
        [OP_PUSH_LOCAL] = &&thread_OP_PUSH_LOCAL,
        [OP_PUSH_GLOBAL] = &&thread_OP_PUSH_GLOBAL,
        [OP_PUSH_BUILTIN] = &&thread_OP_PUSH_BUILTIN,
        [OP_PACKED_FLAME_VENT] = &&thread_OP_PACKED_FLAME_VENT,
        [OP_PUSH_IMMEDIATE] = &&thread_OP_PUSH_IMMEDIATE,
        [OP_POP] = &&thread_OP_POP,
        [OP_POPZ] = &&thread_OP_POPZ,
        [OP_ADD] = &&thread_OP_ADD,
        [OP_SUB] = &&thread_OP_SUB,
        [OP_MUL] = &&thread_OP_MUL,
        [OP_DIV] = &&thread_OP_DIV,
        [OP_REM] = &&thread_OP_REM,
        [OP_MOD] = &&thread_OP_MOD,
        [OP_AND] = &&thread_OP_AND,
        [OP_OR] = &&thread_OP_OR,
        [OP_XOR] = &&thread_OP_XOR,
        [OP_SHL] = &&thread_OP_SHL,
        [OP_SHR] = &&thread_OP_SHR,
        [OP_NEG] = &&thread_OP_NEG,
        [OP_NOT] = &&thread_OP_NOT,
        [OP_CONV] = &&thread_OP_CONV,
        [OP_CMP] = &&thread_OP_CMP,
        [OP_PACKED_SWITCH] = &&thread_OP_PACKED_SWITCH,
        [OP_DUP] = &&thread_OP_DUP,
        [OP_B] = &&thread_OP_B,
        [OP_BT] = &&thread_OP_BT,
        [OP_BF] = &&thread_OP_BF,
        [OP_PACKED_PURE_GUARD] = &&thread_OP_PACKED_PURE_GUARD,
        [OP_CALL] = &&thread_OP_CALL,
        [OP_RET] = &&thread_OP_RET,
        [OP_EXIT] = &&thread_OP_EXIT,
        [OP_PACKED_COPY_BLOCK] = &&thread_OP_PACKED_COPY_BLOCK,
        [OP_PUSH_ENV] = &&thread_OP_PUSH_ENV,
        [OP_POP_ENV] = &&thread_OP_POP_ENV,
        [OP_BREAK] = &&thread_OP_BREAK,
    };
#define VM_NEXT() do { \
    if (ip >= codeLength) return scriptFallthroughReturnValue(ctx); \
        instrAddr = ip; \
        opword.value = BinaryUtils_readUint32Aligned(bytecodeBase + ip); \
        ip += 4; \
 \
        extraData = bytecodeBase + ip; \
        enum { \
            OPERAND_WIDTH_WORDS = \
                (2u << (2 * GML_TYPE_DOUBLE)) | (1u << (2 * GML_TYPE_FLOAT)) | \
                (1u << (2 * GML_TYPE_INT32)) | (2u << (2 * GML_TYPE_INT64)) | \
                (1u << (2 * GML_TYPE_BOOL)) | (1u << (2 * GML_TYPE_VARIABLE)) | \
                (1u << (2 * GML_TYPE_STRING)), \
        }; \
        if (OpWord_hasOperand(opword)) \
            ip += ((OPERAND_WIDTH_WORDS >> (2 * OpWord_type1(opword))) & 3u) * sizeof(uint32_t); \
 \
        opcode = OpWord_opcode(opword); \
    void* target = jump[opcode]; \
    goto *(target != nullptr ? target : &&thread_unknown); \
} while (0)
    VM_NEXT();

thread_OP_PACKED_CALL_DISCARD:

    do {
        if (fuseInstructions && instrAddr == PALETTE_TAIL && runPaletteTail(ctx, bytecodeBase)) {
                    ip = codeLength;
                    break;
                }
#ifdef HLD_CHECK_PALETTE_TAIL
                checkPaletteCall(ctx, bytecodeBase, opword, extraData);
#endif
                VM_SYNC_IP();
                handleCall(ctx, opword, extraData, true);
                ip += 4;
                break;
    } while (0);

    VM_NEXT();

thread_OP_PACKED_MATH:

    do {
        if (!executePackedMath(ctx, opword)) {
                    VM_SYNC_IP();
                    handleCall(ctx, opword, extraData, false);
                }
                break;
    } while (0);

    VM_NEXT();

thread_OP_PACKED_ARGUMENT:
thread_OP_PACKED_ARGUMENT_ARRAY:

    do {
        {
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
    } while (0);

    VM_NEXT();

thread_OP_PACKED_ENEMY_SHADOW:

    do {
        {
                const uint32_t slot = resolveLocalSlot(ctx, (uint16_t)OpWord_extra(opword) & PACKED_VAR_ID_MASK);
                RValue value = ctx->currentCallFrame->localVars[slot];
#ifdef HLD_VERIFY_ENEMY_SHADOW
                verifyShadowMiss = false;
#endif
                if (fuseInstructions) {
                    const GMLReal index = RValue_toReal(value);
                    if (index >= 0 && index < 8 && index == (unsigned)index) {
                        const unsigned group = (unsigned)index;
                        if (!enemyShadowGroupsValid)
                            enemyShadowGroupsValid = findEnemyShadowGroups(ctx, &enemyShadowGroups);
                        const bool miss = enemyShadowGroupsValid && (enemyShadowGroups & (1u << group)) == 0;
#ifdef HLD_VERIFY_ENEMY_SHADOW
                        verifyShadowMiss = miss;
                        if (miss && ++enemyShadowSkips == 1000)
                            logInfo("HLD: checked 1,000 skipped enemy shadow platform groups\n");
                        enemyShadowGroupsValid = false;
#else
                        if (miss) {
#ifdef HLD_COMPARE_ENEMY_SHADOW
                            enemyShadowSkips += enemyShadowGroups == 0 ? 8 - group : 1;
#endif
                            // The original misses only write locals. Continue at the original increment.
                            ip = enemyShadowGroups == 0 ? codeLength : 0x2608;
                            break;
                        }
                        // The original hit path can call game code. Recheck the grid after it.
                        enemyShadowGroupsValid = false;
#endif
                    }
                }
                value.ownsReference = false;
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PACKED_SLOT_CONVERTED_READ:

    do {
        {
                const uint16_t operand = (uint16_t)OpWord_extra(opword);
                const unsigned scope = operand >> 14;
                const int32_t id = operand & PACKED_VAR_ID_MASK;
                Instance* instance = packedScopeInstance(ctx, scope);
                RValue value = instance != nullptr
                    ? readInstanceSlot(instance, id, extraData)
                    : resolveVariableRead(ctx, packedScopeTag[scope], resolveVarOperand(extraData));
                const uint8_t type = OpWord_type2(opword);
                if (!convertPackedReadValue(&value, type)) {
                    ++convertedReadFallback;
                    stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                    break;
                }
                ++convertedReadFast;
                ip += 4;
                if (!(type == GML_TYPE_BOOL && fuseInstructions &&
                      consumeConditionalBranch(bytecodeBase, codeLength, &ip, value.int32 != 0)))
                    stackPushTyped(ctx, value, type);
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PACKED_CONVERTED_READ:

    do {
        {
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
                const uint8_t type = OpWord_type2(opword);
                if (!convertPackedReadValue(&value, type)) {
                    ++convertedReadFallback;
                    stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                    break;
                }
                ++convertedReadFast;
                ip += 4;
                if (!(type == GML_TYPE_BOOL && fuseInstructions &&
                      consumeConditionalBranch(bytecodeBase, codeLength, &ip, value.int32 != 0)))
                    stackPushTyped(ctx, value, type);
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PACKED_SLOT_READ:

    do {
        {
                const uint16_t operand = (uint16_t)OpWord_extra(opword);
                const unsigned scope = operand >> 14;
                const int32_t id = operand & PACKED_VAR_ID_MASK;
                Instance* instance = packedScopeInstance(ctx, scope);
                RValue value = instance != nullptr
                    ? readInstanceSlot(instance, id, extraData)
                    : resolveVariableRead(ctx, packedScopeTag[scope], resolveVarOperand(extraData));
                if (OpWord_type2(opword) == 0xF) {
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
    } while (0);

    VM_NEXT();

thread_OP_PACKED_VAR_READ:

    do {
        {
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
                if (OpWord_type2(opword) == 0xF) {
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
    } while (0);

    VM_NEXT();

thread_OP_PACKED_VAR_WRITE:

    do {
        {
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
    } while (0);

    VM_NEXT();

thread_OP_PACKED_DORMANT_MINE:

    do {
        {
                VM_SYNC_IP();
                if (tryDormantMine(ctx)) return scriptFallthroughReturnValue(ctx);
                const uint32_t ref = resolveVarOperand(bytecodeBase + ip);
                stackPushTyped(ctx, resolveVariableRead(ctx, INSTANCE_SELF, ref), GML_TYPE_VARIABLE);
                ip += 4;
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PACKED_PHASE_MINE_TRIGGER:

    do {
        {
                bool triggered;
                VM_SYNC_IP();
                if (hldPhaseMineTrigger(ctx, &triggered)) {
                    // The original ChangeState runs at 0x188 when triggered.
                    // The switch selector is still on the stack at either target.
                    ip = triggered ? 0x188 : 0x744;
                    break;
                }
                stackPushTyped(ctx, RValue_makeInt32(OpWord_extra(opword)), GML_TYPE_VARIABLE);
                ip += 4;
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PACKED_LITERAL_VALUE:

    do {
        stackPushTyped(ctx, RValue_makeInt32(OpWord_extra(opword)), GML_TYPE_VARIABLE);
                ip += 4;
                break;
    } while (0);

    VM_NEXT();

thread_OP_PACKED_LITERAL_STORE:

    do {
        {
                OpWord pop = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                uint32_t ref = resolveVarOperand(bytecodeBase + ip + 4);
                ip += 8;
                resolveVariableWrite(ctx, OpWord_extra(pop), ref, RValue_makeReal(OpWord_extra(opword)));
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PACKED_LITERAL_BRANCH:

    do {
        {
                const RValue* value = &slots[ctx->stack.top - 1];
                uint32_t next = ip;
                if (branchOnPackedLiteral(ctx, value, opword, bytecodeBase, codeLength, &next)) {
                    ip = next;
                    --ctx->stack.top;
                } else {
                    handlePushImmediate(ctx, opword);
                }
                break;
            }
            // Push instructions
    } while (0);

    VM_NEXT();

thread_OP_PUSH:

    do {
        {
                if (instrAddr == 0x0530 && useHldSceneryCull && hldSceneryDrawLists && appendSceneryDrawList(ctx)) {
                    ip = HLD_SCENERY_NEXT_INSTANCE;
                    break;
                }
                if (instrAddr == HLD_SCENERY_CULL_START && useHldSceneryCull && hldSceneryCull(ctx, bytecodeBase)) {
                    ip = hldSceneryCanSkipUpdate(ctx) ? HLD_SCENERY_NEXT_INSTANCE : HLD_SCENERY_CULL_END;
                    break;
                }
                uint8_t type1 = OpWord_type1(opword);
                // Inline fast paths for variable reads (not ints, doubles, etc, only VARIABLES) that are "normal" type (not arrays, not stacktop, and not the new fangled BC17 array reads)
                if (type1 == GML_TYPE_VARIABLE) {
                    uint32_t varRef = resolveVarOperand(extraData);
                    uint8_t varType = (uint8_t) ((varRef >> 24) & 0xF8);
                    if (varType == VARTYPE_NORMAL) {
                        Variable* varDef = resolveVarDef(ctx, varRef);
                        if (varDef->varID >= 0) {
                            int32_t instanceType = (int32_t) OpWord_extra(opword);
                            RValue val;
                            if (tryFastVarRead(ctx, instanceType, varDef, &val)) {
                                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
#ifdef ENABLE_VM_TRACING
                                switch (instanceType) {
                                    case INSTANCE_SELF: {
                                        Instance* inst = (Instance*) ctx->currentCallFrame->self;
                                        VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, val, false, -1, inst->instanceId, "");
                                        break;
                                    }
                                    case INSTANCE_LOCAL:
                                        VM_checkIfVariableShouldBeTracedAndLog(ctx, "local", nullptr, varDef->name, val, false, -1, -1, "");
                                        break;
                                    case INSTANCE_GLOBAL:
                                        VM_checkIfVariableShouldBeTracedAndLog(ctx, "global", nullptr, varDef->name, val, false, -1, -1, "");
                                        break;
                                    case INSTANCE_OTHER: {
                                        Instance* inst = (Instance*) ctx->currentCallFrame->other;
                                        VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, val, false, -1, inst->instanceId, "");
                                        break;
                                    }
                                }
#endif
                                break;
                            }
                        }
                    }
                }
                handlePush(ctx, opword, extraData, type1);
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PUSH_LOCAL:

    do {
        {
                uint32_t varRef = resolveVarOperand(extraData);
#if IS_WAD17_OR_HIGHER_ENABLED
                uint8_t varType = (uint8_t) ((varRef >> 24) & 0xF8);
                if (varType == VARTYPE_ARRAYPUSHAF || varType == VARTYPE_ARRAYPOPAF) {
                    Variable* varDef = resolveVarDef(ctx, varRef);
                    uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
                    require(ctx->currentCallFrame->localVarsCount > localSlot);
                    pushTopLevelArrayRef(ctx, &ctx->currentCallFrame->localVars[localSlot], varType == VARTYPE_ARRAYPOPAF);
                    break;
                }
#endif
                // Locals are always non-builtin (varID >= 0); inline the read straight from localVars[].
                Variable* varDef = resolveVarDef(ctx, varRef);
                uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
                require(ctx->currentCallFrame->localVarsCount > localSlot);
                RValue val = ctx->currentCallFrame->localVars[localSlot];
                val.ownsReference = false;
                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
#ifdef ENABLE_VM_TRACING
                VM_checkIfVariableShouldBeTracedAndLog(ctx, "local", nullptr, varDef->name, val, false, -1, -1, "");
#endif
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PUSH_GLOBAL:

    do {
        {
                uint32_t varRef = resolveVarOperand(extraData);
                uint8_t varType = (uint8_t) ((varRef >> 24) & 0xF8);
                if (varType == VARTYPE_NORMAL) {
                    Variable* varDef = resolveVarDef(ctx, varRef);                                                                           
                    if (varDef->varID >= 0) {
                        Instance* inst = ctx->globalScopeInstance;
                        if (inst != nullptr) {
                            RValue* slot = IntRValueHashMap_findSlot(&inst->selfVars, varDef->varID);
                            if (slot != nullptr) {
                                RValue val = *slot;
                                val.ownsReference = false;
                                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
#ifdef ENABLE_VM_TRACING
                                VM_checkIfVariableShouldBeTracedAndLog(ctx, "global", nullptr, varDef->name, val, false, -1, -1, "");
#endif
                                break;
                            }
                        }
                    }
                }
                RValue val = resolveVariableRead(ctx, INSTANCE_GLOBAL, varRef);
                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PUSH_BUILTIN:

    do {
        handlePushBuiltin(ctx, opword, extraData);
                break;
    } while (0);

    VM_NEXT();

thread_OP_PACKED_FLAME_VENT:

    do {
        {
                if (fuseInstructions && canSkipFlameVentLoop(ctx)) {
#ifdef HLD_VERIFY_FLAME_VENT
                    flameVentStack = ctx->stack.top;
#else
                    Instance_setSelfVar(ctx->currentCallFrame->self, flameVentGuard.object, RValue_makeReal(437.0));
                    ip = HLD_FLAME_VENT_LOOP_END;
#ifdef HLD_COMPARE_FLAME_VENT
                    ++flameVentSkips;
#endif
                    break;
#endif
                }
                handlePushImmediate(ctx, (OpWord){0x840f0000u});
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PUSH_IMMEDIATE:

    do {
        {
                if (fuseInstructions && ip <= codeLength && codeLength - ip >= 4) {
                    OpWord next = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                    int32_t immediate = OpWord_extra(opword);
                    if (next.value == 0x07520000u) {
                        // The adjacent INT32-to-variant conversion only retags the literal.
                        stackPushTyped(ctx, RValue_makeInt32(immediate), GML_TYPE_VARIABLE);
                        ip += 4;
                        break;
                    }
                    // Format-15/16 array reads commonly push a fixed scope and
                    // index, only to pop both on the next instruction.
                    if (OpWord_opcode(next) == OP_PUSH_IMMEDIATE && codeLength - ip >= 12 &&
                        (immediate == INSTANCE_SELF || immediate == INSTANCE_OTHER ||
                         immediate == INSTANCE_LOCAL || immediate == INSTANCE_GLOBAL) &&
                        IS_WAD15_OR_HIGHER(ctx) && IS_WAD16_OR_BELOW(ctx)) {
                        OpWord read = {BinaryUtils_readUint32Aligned(bytecodeBase + ip + 4)};
                        if (OpWord_opcode(read) == OP_PUSH && OpWord_type1(read) == GML_TYPE_VARIABLE) {
                            uint32_t ref = resolveVarOperand(bytecodeBase + ip + 8);
                            if (((ref >> 24) & 0xF8) == VARTYPE_ARRAY) {
                                ArrayAccess access = {.arrayIndex = OpWord_extra(next), .instanceType = immediate,
                                    .isArray = true, .hasInstanceType = true};
                                RValue value = resolveVariableReadWithAccess(ctx, OpWord_extra(read), ref, access);
                                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                                ip += 12;
                                break;
                            }
                        }
                    }
                }
                handlePushImmediate(ctx, opword);
                break;
            }

            // Pop instructions
    } while (0);

    VM_NEXT();

thread_OP_POP:

    do {
        {
                uint8_t type1 = OpWord_type1(opword);
                uint32_t varRef = resolveVarOperand(extraData);
                uint8_t varType = (uint8_t) ((varRef >> 24) & 0xF8);
                int32_t instanceType = OpWord_extra(opword);
                // BC17: VARTYPE_INSTANCE encodes (instanceId - INSTANCE_ID_BASE) in the instruction's lower 16 bits.
                if (varType == VARTYPE_INSTANCE) instanceType += INSTANCE_ID_BASE;
                int32_t type2 = OpWord_type2(opword); // source type (what's on stack)
                if (type1 == GML_TYPE_VARIABLE && varType == VARTYPE_NORMAL) {
                    // Inline fast path for the simple variable-assignment case: type1==VARIABLE, which is ~99.998% of all Pops in real workloads
                    RValue val = stackPop(ctx);
                    val = coerceIntStoreToReal(val, type2);
                    resolveVariableWrite(ctx, instanceType, varRef, val);
                } else {
                    handlePop(ctx, type1, type2, varRef, varType, instanceType);
                }
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_POPZ:

    do {
        handlePopz(ctx);
                break;

            // Arithmetic
            // We keep the number + number operations inlined in executeBytecodeInstructions, keeping only the slow path for string concat/repetition
    } while (0);

    VM_NEXT();

thread_OP_ADD:

    do {
        {
                RValue* slotA = &slots[ctx->stack.top - 2];
                RValue* slotB = &slots[ctx->stack.top - 1];
                uint8_t aType = slotA->type;
                uint8_t bType = slotB->type;
                if ((aType == RVALUE_INT32 || aType == RVALUE_REAL) && (bType == RVALUE_INT32 || bType == RVALUE_REAL)) {
                    if (aType == RVALUE_INT32 && bType == RVALUE_INT32) {
                        slotA->int32 = slotA->int32 + slotB->int32;
                    } else {
                        // Read both operands as locals before writing back, since the union means
                        // slotA->real and slotA->int32 share storage.
                        GMLReal aVal = (aType == RVALUE_INT32) ? (GMLReal) slotA->int32 : slotA->real;
                        GMLReal bVal = (bType == RVALUE_INT32) ? (GMLReal) slotB->int32 : slotB->real;
                        slotA->real = aVal + bVal;
                        slotA->type = RVALUE_REAL;
                    }
                    slotA->gmlStackType = OpWord_type2(opword);
                    ctx->stack.top--;
                } else {
                    uint8_t resultType = OpWord_type2(opword);
                    RValue b = stackPop(ctx);
                    RValue a = stackPop(ctx);
                    if (a.type == RVALUE_STRING || b.type == RVALUE_STRING) {
                        handleAddString(ctx, a, b, resultType);
                        break;
                    }
#ifndef NO_RVALUE_INT64
                    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) {
                        stackPushTyped(ctx, RValue_makeInt64(a.int64 + b.int64), resultType);
                        break;
                    }
#endif
                    GMLReal result = RValue_toReal(a) + RValue_toReal(b);
                    RValue_free(&a);
                    RValue_free(&b);
                    stackPushTyped(ctx, RValue_makeReal(result), resultType);
                }
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_SUB:

    do {
        {
                RValue* slotA = &slots[ctx->stack.top - 2];
                RValue* slotB = &slots[ctx->stack.top - 1];
                uint8_t aType = slotA->type;
                uint8_t bType = slotB->type;
                if ((aType == RVALUE_INT32 || aType == RVALUE_REAL) && (bType == RVALUE_INT32 || bType == RVALUE_REAL)) {
                    if (aType == RVALUE_INT32 && bType == RVALUE_INT32) {
                        slotA->int32 = slotA->int32 - slotB->int32;
                    } else {
                        GMLReal aVal = (aType == RVALUE_INT32) ? (GMLReal) slotA->int32 : slotA->real;
                        GMLReal bVal = (bType == RVALUE_INT32) ? (GMLReal) slotB->int32 : slotB->real;
                        slotA->real = aVal - bVal;
                        slotA->type = RVALUE_REAL;
                    }
                    slotA->gmlStackType = OpWord_type2(opword);
                    ctx->stack.top--;
                } else {
                    uint8_t resultType = OpWord_type2(opword);
                    RValue b = stackPop(ctx);
                    RValue a = stackPop(ctx);
#ifndef NO_RVALUE_INT64
                    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) {
                        stackPushTyped(ctx, RValue_makeInt64(a.int64 - b.int64), resultType);
                        break;
                    }
#endif
                    GMLReal result = RValue_toReal(a) - RValue_toReal(b);
                    RValue_free(&a);
                    RValue_free(&b);
                    stackPushTyped(ctx, RValue_makeReal(result), resultType);
                }
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_MUL:

    do {
        {
                RValue* slotA = &slots[ctx->stack.top - 2];
                RValue* slotB = &slots[ctx->stack.top - 1];
                uint8_t aType = slotA->type;
                uint8_t bType = slotB->type;
                if ((aType == RVALUE_INT32 || aType == RVALUE_REAL) && (bType == RVALUE_INT32 || bType == RVALUE_REAL)) {
                    if (aType == RVALUE_INT32 && bType == RVALUE_INT32) {
                        slotA->int32 = slotA->int32 * slotB->int32;
                    } else {
                        GMLReal aVal = (aType == RVALUE_INT32) ? (GMLReal) slotA->int32 : slotA->real;
                        GMLReal bVal = (bType == RVALUE_INT32) ? (GMLReal) slotB->int32 : slotB->real;
                        slotA->real = aVal * bVal;
                        slotA->type = RVALUE_REAL;
                    }
                    slotA->gmlStackType = OpWord_type2(opword);
                    ctx->stack.top--;
                } else {
                    uint8_t resultType = OpWord_type2(opword);
                    RValue b = stackPop(ctx);
                    RValue a = stackPop(ctx);
                    if (a.type == RVALUE_STRING) {
                        handleMulString(ctx, a, b, resultType);
                        break;
                    }
#ifndef NO_RVALUE_INT64
                    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) {
                        stackPushTyped(ctx, RValue_makeInt64(a.int64 * b.int64), resultType);
                        break;
                    }
#endif
                    GMLReal result = RValue_toReal(a) * RValue_toReal(b);
                    RValue_free(&a);
                    RValue_free(&b);
                    stackPushTyped(ctx, RValue_makeReal(result), resultType);
                }
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_DIV:

    do {
        handleDiv(ctx, opword); break;
    } while (0);

    VM_NEXT();

thread_OP_REM:

    do {
        handleRem(ctx, opword); break;
    } while (0);

    VM_NEXT();

thread_OP_MOD:

    do {
        handleMod(ctx, opword); break;

            // Bitwise / Logical
    } while (0);

    VM_NEXT();

thread_OP_AND:

    do {
        handleAnd(ctx, opword); break;
    } while (0);

    VM_NEXT();

thread_OP_OR:

    do {
        handleOr(ctx, opword);  break;
    } while (0);

    VM_NEXT();

thread_OP_XOR:

    do {
        handleXor(ctx, opword); break;
    } while (0);

    VM_NEXT();

thread_OP_SHL:

    do {
        handleShl(ctx, opword); break;
    } while (0);

    VM_NEXT();

thread_OP_SHR:

    do {
        handleShr(ctx, opword); break;

            // Unary
    } while (0);

    VM_NEXT();

thread_OP_NEG:

    do {
        handleNeg(ctx, opword); break;
    } while (0);

    VM_NEXT();

thread_OP_NOT:

    do {
        handleNot(ctx, opword); break;

            // Type conversion
    } while (0);

    VM_NEXT();

thread_OP_CONV:

    do {
        {
                uint8_t srcType = OpWord_type1(opword);
                uint8_t dstType = OpWord_type2(opword);
                uint8_t convKey = (uint8_t) ((dstType << 4) | srcType);
                RValue* top = &slots[ctx->stack.top - 1];
                bool fastHit = false;

                // Inline fast paths for the four conversions that account for ~93% of all Conv opcodes in real workloads
                switch (convKey) {
                    case 0x52: // Int32 -> Variable (pure passthrough; just retag stack slot)
                        fastHit = true;
                        break;
                    case 0x45: // Variable -> Bool
                        switch (top->type) {
                            case RVALUE_INT32:
                                top->int32 = top->int32 > 0 ? 1 : 0;
                                top->type = RVALUE_BOOL;
                                fastHit = true;
                                break;
                            case RVALUE_BOOL:
                                // Already 0/1; nothing to do
                                fastHit = true;
                                break;
                            case RVALUE_REAL:
                                top->int32 = top->real > (GMLReal) 0.5 ? 1 : 0;
                                top->type = RVALUE_BOOL;
                                fastHit = true;
                                break;
                        }
                        break;
                    case 0x25: // Variable -> Int32
                        switch (top->type) {
                            case RVALUE_INT32:
                                fastHit = true;
                                break;
                            case RVALUE_BOOL:
                                top->type = RVALUE_INT32;
                                fastHit = true;
                                break;
                            case RVALUE_REAL:
                                top->int32 = (int32_t) top->real;
                                top->type = RVALUE_INT32;
                                fastHit = true;
                                break;
                        }
                        break;
                    case 0x02: // Int32 -> Double (Real)
                        top->real = (GMLReal) top->int32;
                        top->type = RVALUE_REAL;
                        fastHit = true;
                        break;
                }

                if (fastHit) {
                    if (fuseInstructions && dstType == GML_TYPE_BOOL &&
                        consumeConditionalBranch(bytecodeBase, codeLength, &ip, top->int32 != 0)) {
                        --ctx->stack.top;
                        break;
                    }
                    top->gmlStackType = dstType;
                } else {
                    handleConv(ctx, srcType, dstType, convKey);
                }
                break;
            }

            // Comparison
    } while (0);

    VM_NEXT();

thread_OP_CMP:

    do {
        {
                RValue* slotA = &slots[ctx->stack.top - 2];
                RValue* slotB = &slots[ctx->stack.top - 1];
                uint8_t aType = slotA->type;
                uint8_t bType = slotB->type;

                // Inline fast path for INT32|REAL/INT32|REAL
                if ((aType == RVALUE_INT32 || aType == RVALUE_REAL) && (bType == RVALUE_INT32 || bType == RVALUE_REAL)) {
                    GMLReal a = aType == RVALUE_INT32 ? (GMLReal)slotA->int32 : slotA->real;
                    GMLReal b = bType == RVALUE_INT32 ? (GMLReal)slotB->int32 : slotB->real;
                    GMLReal diff = a - b;
                    GMLReal epsilon = (ctx->runner != NULL) ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
                    int cmp = (GMLReal_fabs(diff) <= epsilon) ? 0 : (diff < 0) ? -1 : 1;
                    bool result;
                    switch (OpWord_comparisonFunction(opword)) {
                        case CMPOP_LESS_THAN:  result = cmp < 0;  break;
                        case CMPOP_LESS_THAN_OR_EQUAL: result = cmp <= 0; break;
                        case CMPOP_EQUAL:  result = cmp == 0; break;
                        case CMPOP_NOT_EQUAL: result = cmp != 0; break;
                        case CMPOP_GREATER_THAN_OR_EQUAL: result = cmp >= 0; break;
                        case CMPOP_GREATER_THAN:  result = cmp > 0;  break;
                        default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
                    }
                    if (fuseInstructions && consumeConditionalBranch(bytecodeBase, codeLength, &ip, result)) {
                        ctx->stack.top -= 2;
                        break;
                    }
                    slotA->int32 = result ? 1 : 0;
                    slotA->type = RVALUE_BOOL;
                    slotA->gmlStackType = GML_TYPE_BOOL;
                    ctx->stack.top--;
                } else {
                    handleCmp(ctx, opword);
                }
                break;
            }

            // Duplicate
    } while (0);

    VM_NEXT();

thread_OP_PACKED_SWITCH:

    do {
        {
                uint32_t resume = instrAddr;
                if (fuseInstructions && trySwitchChain(ctx, bytecodeBase, (uint16_t)OpWord_extra(opword), &resume)) {
                    ip = resume;
                } else {
                    handleDup(ctx, (OpWord){SWITCH_DUP_WORD});
                }
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_DUP:

    do {
        handleDup(ctx, opword);
                break;

            // Branches
            // The reason why these (the branches opcodes) are inlined is because they access ctx->ip
            // So, because they are short n' sweet, we prefer to keep them inlined to avoid any reloading shenanigans that the compiler may do
    } while (0);

    VM_NEXT();

thread_OP_B:

    do {
        {
                int32_t offset = OpWord_branchOffset(opword);
                ip = instrAddr + offset;
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_BT:

    do {
        {
                bool condition = stackPopInt32(ctx) != 0;
                if (condition == true) {
                    int32_t offset = OpWord_branchOffset(opword);
                    ip = instrAddr + offset;
                }
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_BF:

    do {
        {
                bool condition = stackPopInt32(ctx) != 0;
                if (condition == false) {
                    int32_t offset = OpWord_branchOffset(opword);
                    ip = instrAddr + offset;
                }
                break;
            }

            // Function call
    } while (0);

    VM_NEXT();

thread_OP_PACKED_PURE_GUARD:

    do {
        {
                const int32_t argc = OpWord_extra(opword);
                const OpWord originalCall = {UINT32_C(0xd9020000) | (uint16_t)argc};
                bool answer;
                if (fuseInstructions && pureGuardResult(ctx, argc, &answer)) {
#ifdef HLD_VERIFY_PURE_GUARDS
                    VM_SYNC_IP();
                    handleCall(ctx, originalCall, extraData, false);
                    const RValue result = ctx->stack.slots[ctx->stack.top - 1];
                    require(result.type == RVALUE_INT32 && result.int32 == (int)answer);
                    notePureGuard(argc);
                    break;
#else
                    const bool negated = OpWord_type2(opword) != 0;
                    uint32_t branch = ip + (negated ? 8 : 4);
                    if (consumeConditionalBranch(bytecodeBase, codeLength, &branch, answer != negated)) {
                        ctx->stack.top -= argc;
                        ip = branch;
                        notePureGuard(argc);
                        break;
                    }
#endif
                }
                VM_SYNC_IP();
                handleCall(ctx, originalCall, extraData, false);
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_CALL:

    do {
        VM_SYNC_IP();
                handleCall(ctx, opword, extraData, false);
                if (instrAddr == 0x7c && finishRestingScenery(ctx, code))
                    return scriptFallthroughReturnValue(ctx);
                break;

            // Return
    } while (0);

    VM_NEXT();

thread_OP_RET:

    do {
        {
                RValue retVal = stackPop(ctx);
                return retVal;
            }

            // Exit (no return value)
    } while (0);

    VM_NEXT();

thread_OP_EXIT:

    do {
        return scriptFallthroughReturnValue(ctx);

            // Environment (with-statements)
    } while (0);

    VM_NEXT();

thread_OP_PACKED_COPY_BLOCK:

    do {
        {
                const uint32_t distance = opword.value & COPY_BLOCK_MASK;
                if (fuseInstructions && tryCopyBlock(ctx, (opword.value >> COPY_BLOCK_BITS) & COPY_BLOCK_MASK)) {
                    ip = instrAddr + (distance + 1) * sizeof(uint32_t);
                } else {
                    VM_SYNC_IP();
                    handlePushEnv(ctx, (OpWord){((uint32_t)OP_PUSH_ENV << 24) | distance}, instrAddr);
                    VM_RELOAD_IP();
                }
                break;
            }
    } while (0);

    VM_NEXT();

thread_OP_PUSH_ENV:

    do {
        VM_SYNC_IP();
                handlePushEnv(ctx, opword, instrAddr);
                VM_RELOAD_IP();
                break;
    } while (0);

    VM_NEXT();

thread_OP_POP_ENV:

    do {
        VM_SYNC_IP();
                if (hldSceneryCursor && useHldSceneryCull && instrAddr == HLD_SCENERY_NEXT_INSTANCE &&
                    (uint32_t)(instrAddr + OpWord_branchOffset(opword)) == HLD_SCENERY_CULL_START)
                    popSceneryEnv(ctx, opword, instrAddr, bytecodeBase);
                else
                    handlePopEnv(ctx, opword, instrAddr);
                VM_RELOAD_IP();
                break;

            // Break (extended opcodes in V17+, no-op/debug in V16)
    } while (0);

    VM_NEXT();

thread_OP_BREAK:

    do {
        #if IS_WAD17_OR_HIGHER_ENABLED
                handleBreak(ctx, opword, instrAddr, extraData);
#endif
                break;
    } while (0);

    VM_NEXT();

thread_unknown:
    logError("VM: Unknown opcode 0x%02X at offset %u\n", opcode, instrAddr);
    abort();
#undef VM_NEXT

}
#endif
static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {
#if !IS_WAD17_OR_HIGHER_ENABLED && !defined(ENABLE_VM_TRACING) &&     !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (ctx->dataWin->gen8.wadVersion == 15 && code != ctx->hldDistortionCode) {
        return executeThreadedInstructions(ctx, code);
    }
#endif
    // codeLength and bytecodeBase are invariant for the lifetime of this executeBytecodeInstructions call, so let's hoist them to avoid the compiler emitting code to
    // reload the values at the end of every iteration.
    const uint32_t codeLength = code->length;
    ctx->bytecodeBase = codeBytes(ctx, code);
    const uint8_t* const bytecodeBase = ctx->bytecodeBase;
    // If you just joined the stream: ip is short for instruction pointer chat
    // The ip is mutable, so we need to use VM_SYNC_IP and VM_RELOAD_IP every time an opcode handler may access it or write to it
    uint32_t ip = code->offset;
    bool useHldDistortionLoop = code == ctx->hldDistortionCode && !useOriginalDistortion;
    bool fuseInstructions = true;
#ifdef ENABLE_VM_TRACING
    fuseInstructions = shlen(ctx->opcodesToBeTraced) == 0 && shlen(ctx->stackToBeTraced) == 0;
#endif
#ifdef ENABLE_VM_GML_PROFILER
    fuseInstructions = fuseInstructions && ctx->profiler == nullptr;
#endif
#ifdef ENABLE_VM_OPCODE_PROFILER
    fuseInstructions = fuseInstructions && !ctx->opcodeProfilerEnabled;
#endif
    bool useHldSceneryCull = fuseInstructions && code == ctx->hldSceneryStepCode;
#ifdef ENABLE_VM_TRACING
    useHldSceneryCull = useHldSceneryCull && shlen(ctx->functionCallsToBeTraced) == 0 &&
        shlen(ctx->varReadsToBeTraced) == 0 && shlen(ctx->varWritesToBeTraced) == 0;
#endif

    RValue* restrict slots = ctx->stack.slots;

    unsigned enemyShadowGroups = 0;
    bool enemyShadowGroupsValid = false;
    // Some opcodes have their handler or parts of their handler inlined
    // Those are opcodes that during real gameplay (using "--profile-opcodes") shown that, with inlining and keeping only the frequently called handle parts, we could squeeze MORE performance from the interpreter!
    while (codeLength > ip) {
        if (useHldDistortionLoop && ip == HLD_DISTORTION_LOOP_START && drawHldDistortionLoop(ctx)) {
            ip = HLD_DISTORTION_LOOP_END;
        }
#ifdef ENABLE_WAD17
        if (ctx->exception != nullptr) {
#ifdef ENABLE_VM_EXCEPTIONS_LOGS
            logError("VM: Exception thrown! Stack Top is %d\n", ctx->exceptionHandlerStackTop);
#endif
            if (ctx->exceptionHandlerStackTop == 0) {
                // TODO: When Butterscotch is better, we could have a strict mode that DOES throw a error
                logError("VM: The exception handler frame stack is 0, but we have a pending exception to be dispatched! This would've technically crashed the game in the original runner... or we aren't handling exceptions correctly. We'll swallow the exception and hope for the best... (Exception: %s)\n", ctx->exception->message);
                free(ctx->exception->message);
                free(ctx->exception);
                ctx->exception = nullptr;
                return RValue_makeUndefined();
            }

            // Restore caller frame
            ExceptionHandlerFrame* exceptionHandlerFrame = &ctx->exceptionHandlerFrameStack[ctx->exceptionHandlerStackTop - 1];

            // Not for us, propagate the exception!
            if (exceptionHandlerFrame->boundToCallFrame != ctx->currentCallFrame) {
#ifdef ENABLE_VM_EXCEPTIONS_LOGS
                logWarn("VM: We wanted %d but we are %d - Propagating...\n", exceptionHandlerFrame->boundToCallDepth, ctx->callDepth);
#endif
                return RValue_makeUndefined();
            }

            // We DO NOT want to pop the handler, it will be popped by @@try_unhook@@
            bool isException = exceptionHandlerFrame->jumpToOnException != -1;
            ip = isException ? exceptionHandlerFrame->jumpToOnException : exceptionHandlerFrame->jumpToOnSuccess;
            unwindVMStack(ctx, exceptionHandlerFrame->stackTop);

            VM_SYNC_IP();

#ifdef ENABLE_VM_EXCEPTIONS_LOGS
            logWarn("VM: Jumped to %d due to exception handler, is this a exception? %d\n", ip, isException);
#endif

            if (isException) {
                // On a exception, GameMaker pops the struct and stores it in the variable of the "catch (_nameOfTheExceptionHere) {"
                Instance* gmlStruct = Runner_createStruct(ctx->runner);
                // TODO: longMessage/script/stacktrace
                VM_structSet(ctx, gmlStruct, "message", RValue_makeOwnedString(safeStrdup(ctx->exception->message)), -1);
                stackPush(ctx, RValue_makeStructAndIncRef(gmlStruct));
                free(ctx->exception->message);
                free(ctx->exception);
            } else {
                // Will be unparked by finish_finally
                ctx->parkedException = ctx->exception;
            }

            ctx->exception = nullptr;
        }
#endif

#ifdef ENABLE_VM_GML_PROFILER
        if (ctx->profiler != nullptr)
            Profiler_tickInstruction(ctx->profiler);
#endif
        uint32_t instrAddr = ip;
        OpWord opword = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
        ip += 4;

        const uint8_t* extraData = bytecodeBase + ip;
        // Two bits per first-operand type store its width in 32-bit words.
        enum {
            OPERAND_WIDTH_WORDS =
                (2u << (2 * GML_TYPE_DOUBLE)) | (1u << (2 * GML_TYPE_FLOAT)) |
                (1u << (2 * GML_TYPE_INT32)) | (2u << (2 * GML_TYPE_INT64)) |
                (1u << (2 * GML_TYPE_BOOL)) | (1u << (2 * GML_TYPE_VARIABLE)) |
                (1u << (2 * GML_TYPE_STRING)),
        };
        if (OpWord_hasOperand(opword))
            ip += ((OPERAND_WIDTH_WORDS >> (2 * OpWord_type1(opword))) & 3u) * sizeof(uint32_t);

        uint8_t opcode = OpWord_opcode(opword);

#ifdef ENABLE_VM_OPCODE_PROFILER
        if (ctx->opcodeProfilerEnabled) {
            ctx->opcodeCounts[opcode]++;
            ctx->opcodeVariantCounts[opcode * 256 + OpWord_type1(opword) * 16 + OpWord_type2(opword)]++;
            if (opcode == OP_BREAK) {
                int16_t breakType = OpWord_extra(opword);
                int idx = -breakType;
                if (idx >= 0 && 64 > idx) {
                    ctx->breakSubOpCounts[idx]++;
                }
            }
            // Capture actual runtime RValue types for arithmetic/comparison/conversion ops.
            // typeB = 0xF sentinel for unary ops (no second operand).
            uint8_t rvTypeA = 0xFF, rvTypeB = 0xF;
            switch (opcode) {
                case OP_MUL: case OP_DIV: case OP_REM: case OP_MOD:
                case OP_ADD: case OP_SUB: case OP_AND: case OP_OR:
                case OP_XOR: case OP_SHL: case OP_SHR: case OP_CMP:
                    if (ctx->stack.top >= 2) {
                        rvTypeA = slots[ctx->stack.top - 2].type;
                        rvTypeB = slots[ctx->stack.top - 1].type;
                    }
                    break;
                case OP_NEG: case OP_NOT: case OP_CONV:
                    if (ctx->stack.top >= 1) {
                        rvTypeA = slots[ctx->stack.top - 1].type;
                    }
                    break;
            }
            if (rvTypeA != 0xFF) {
                ctx->opcodeRValueTypeCounts[opcode * 256 + (rvTypeA & 0xF) * 16 + (rvTypeB & 0xF)]++;
            }
        }
#endif

#ifdef ENABLE_VM_TRACING
        if (shlen(ctx->opcodesToBeTraced) > 0 && ctx->runner->frameCount >= ctx->traceBytecodeAfterFrame) {
            if (shgeti(ctx->opcodesToBeTraced, "*") != -1 || shgeti(ctx->opcodesToBeTraced, VM_getCurrentCallFrameCodeName(ctx)) != -1) {
                char opcodeStr[32], operandStr[256] = "", commentStr[128] = "";
                formatInstruction(ctx, ctx->bytecodeBase, instrAddr, opword, extraData, opcodeStr, sizeof(opcodeStr), operandStr, sizeof(operandStr), commentStr, sizeof(commentStr));

                char* stackBuf = formatStackContents(ctx);

                if (operandStr[0] != '\0') {
                    logInfo("VM: [%s] @%04X (%d) [0x%08X] %s %s [stack=%d] %s\n", VM_getCurrentCallFrameCodeName(ctx), instrAddr, instrAddr, opword.value, opcodeStr, operandStr, ctx->stack.top, stackBuf);
                } else {
                    logInfo("VM: [%s] @%04X (%d) [0x%08X] %s [stack=%d] %s\n", VM_getCurrentCallFrameCodeName(ctx), instrAddr, instrAddr, opword.value, opcodeStr, ctx->stack.top, stackBuf);
                }
                free(stackBuf);
            }
        }
#endif

        switch (opcode) {
            case OP_PACKED_CALL_DISCARD:

                if (fuseInstructions && instrAddr == PALETTE_TAIL && runPaletteTail(ctx, bytecodeBase)) {
                    ip = codeLength;
                    break;
                }
#ifdef HLD_CHECK_PALETTE_TAIL
                checkPaletteCall(ctx, bytecodeBase, opword, extraData);
#endif
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
            case OP_PACKED_ARGUMENT:
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
            case OP_PACKED_ENEMY_SHADOW: {
                const uint32_t slot = resolveLocalSlot(ctx, (uint16_t)OpWord_extra(opword) & PACKED_VAR_ID_MASK);
                RValue value = ctx->currentCallFrame->localVars[slot];
#ifdef HLD_VERIFY_ENEMY_SHADOW
                verifyShadowMiss = false;
#endif
                if (fuseInstructions) {
                    const GMLReal index = RValue_toReal(value);
                    if (index >= 0 && index < 8 && index == (unsigned)index) {
                        const unsigned group = (unsigned)index;
                        if (!enemyShadowGroupsValid)
                            enemyShadowGroupsValid = findEnemyShadowGroups(ctx, &enemyShadowGroups);
                        const bool miss = enemyShadowGroupsValid && (enemyShadowGroups & (1u << group)) == 0;
#ifdef HLD_VERIFY_ENEMY_SHADOW
                        verifyShadowMiss = miss;
                        if (miss && ++enemyShadowSkips == 1000)
                            logInfo("HLD: checked 1,000 skipped enemy shadow platform groups\n");
                        enemyShadowGroupsValid = false;
#else
                        if (miss) {
#ifdef HLD_COMPARE_ENEMY_SHADOW
                            enemyShadowSkips += enemyShadowGroups == 0 ? 8 - group : 1;
#endif
                            // The original misses only write locals. Continue at the original increment.
                            ip = enemyShadowGroups == 0 ? codeLength : 0x2608;
                            break;
                        }
                        // The original hit path can call game code. Recheck the grid after it.
                        enemyShadowGroupsValid = false;
#endif
                    }
                }
                value.ownsReference = false;
                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                break;
            }
            case OP_PACKED_SLOT_CONVERTED_READ: {
                const uint16_t operand = (uint16_t)OpWord_extra(opword);
                const unsigned scope = operand >> 14;
                const int32_t id = operand & PACKED_VAR_ID_MASK;
                Instance* instance = packedScopeInstance(ctx, scope);
                RValue value = instance != nullptr
                    ? readInstanceSlot(instance, id, extraData)
                    : resolveVariableRead(ctx, packedScopeTag[scope], resolveVarOperand(extraData));
                const uint8_t type = OpWord_type2(opword);
                if (!convertPackedReadValue(&value, type)) {
                    ++convertedReadFallback;
                    stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                    break;
                }
                ++convertedReadFast;
                ip += 4;
                if (!(type == GML_TYPE_BOOL && fuseInstructions &&
                      consumeConditionalBranch(bytecodeBase, codeLength, &ip, value.int32 != 0)))
                    stackPushTyped(ctx, value, type);
                break;
            }
            case OP_PACKED_CONVERTED_READ: {
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
                const uint8_t type = OpWord_type2(opword);
                if (!convertPackedReadValue(&value, type)) {
                    ++convertedReadFallback;
                    stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                    break;
                }
                ++convertedReadFast;
                ip += 4;
                if (!(type == GML_TYPE_BOOL && fuseInstructions &&
                      consumeConditionalBranch(bytecodeBase, codeLength, &ip, value.int32 != 0)))
                    stackPushTyped(ctx, value, type);
                break;
            }
            case OP_PACKED_SLOT_READ: {
                const uint16_t operand = (uint16_t)OpWord_extra(opword);
                const unsigned scope = operand >> 14;
                const int32_t id = operand & PACKED_VAR_ID_MASK;
                Instance* instance = packedScopeInstance(ctx, scope);
                RValue value = instance != nullptr
                    ? readInstanceSlot(instance, id, extraData)
                    : resolveVariableRead(ctx, packedScopeTag[scope], resolveVarOperand(extraData));
                if (OpWord_type2(opword) == 0xF) {
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
            case OP_PACKED_VAR_READ: {
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
                if (OpWord_type2(opword) == 0xF) {
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
            case OP_PACKED_DORMANT_MINE: {
                VM_SYNC_IP();
                if (tryDormantMine(ctx)) return scriptFallthroughReturnValue(ctx);
                const uint32_t ref = resolveVarOperand(bytecodeBase + ip);
                stackPushTyped(ctx, resolveVariableRead(ctx, INSTANCE_SELF, ref), GML_TYPE_VARIABLE);
                ip += 4;
                break;
            }
            case OP_PACKED_PHASE_MINE_TRIGGER: {
                bool triggered;
                VM_SYNC_IP();
                if (hldPhaseMineTrigger(ctx, &triggered)) {
                    // The original ChangeState runs at 0x188 when triggered.
                    // The switch selector is still on the stack at either target.
                    ip = triggered ? 0x188 : 0x744;
                    break;
                }
                stackPushTyped(ctx, RValue_makeInt32(OpWord_extra(opword)), GML_TYPE_VARIABLE);
                ip += 4;
                break;
            }
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
                uint32_t next = ip;
                if (branchOnPackedLiteral(ctx, value, opword, bytecodeBase, codeLength, &next)) {
                    ip = next;
                    --ctx->stack.top;
                } else {
                    handlePushImmediate(ctx, opword);
                }
                break;
            }
            // Push instructions
            case OP_PUSH: {
                if (instrAddr == 0x0530 && useHldSceneryCull && hldSceneryDrawLists && appendSceneryDrawList(ctx)) {
                    ip = HLD_SCENERY_NEXT_INSTANCE;
                    break;
                }
                if (instrAddr == HLD_SCENERY_CULL_START && useHldSceneryCull && hldSceneryCull(ctx, bytecodeBase)) {
                    ip = hldSceneryCanSkipUpdate(ctx) ? HLD_SCENERY_NEXT_INSTANCE : HLD_SCENERY_CULL_END;
                    break;
                }
                uint8_t type1 = OpWord_type1(opword);
                // Inline fast paths for variable reads (not ints, doubles, etc, only VARIABLES) that are "normal" type (not arrays, not stacktop, and not the new fangled BC17 array reads)
                if (type1 == GML_TYPE_VARIABLE) {
                    uint32_t varRef = resolveVarOperand(extraData);
                    uint8_t varType = (uint8_t) ((varRef >> 24) & 0xF8);
                    if (varType == VARTYPE_NORMAL) {
                        Variable* varDef = resolveVarDef(ctx, varRef);
                        if (varDef->varID >= 0) {
                            int32_t instanceType = (int32_t) OpWord_extra(opword);
                            RValue val;
                            if (tryFastVarRead(ctx, instanceType, varDef, &val)) {
                                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
#ifdef ENABLE_VM_TRACING
                                switch (instanceType) {
                                    case INSTANCE_SELF: {
                                        Instance* inst = (Instance*) ctx->currentCallFrame->self;
                                        VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, val, false, -1, inst->instanceId, "");
                                        break;
                                    }
                                    case INSTANCE_LOCAL:
                                        VM_checkIfVariableShouldBeTracedAndLog(ctx, "local", nullptr, varDef->name, val, false, -1, -1, "");
                                        break;
                                    case INSTANCE_GLOBAL:
                                        VM_checkIfVariableShouldBeTracedAndLog(ctx, "global", nullptr, varDef->name, val, false, -1, -1, "");
                                        break;
                                    case INSTANCE_OTHER: {
                                        Instance* inst = (Instance*) ctx->currentCallFrame->other;
                                        VM_checkIfVariableShouldBeTracedAndLog(ctx, instanceObjectName(ctx, inst), "self", varDef->name, val, false, -1, inst->instanceId, "");
                                        break;
                                    }
                                }
#endif
                                break;
                            }
                        }
                    }
                }
                handlePush(ctx, opword, extraData, type1);
                break;
            }
            case OP_PUSH_LOCAL: {
                uint32_t varRef = resolveVarOperand(extraData);
#if IS_WAD17_OR_HIGHER_ENABLED
                uint8_t varType = (uint8_t) ((varRef >> 24) & 0xF8);
                if (varType == VARTYPE_ARRAYPUSHAF || varType == VARTYPE_ARRAYPOPAF) {
                    Variable* varDef = resolveVarDef(ctx, varRef);
                    uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
                    require(ctx->currentCallFrame->localVarsCount > localSlot);
                    pushTopLevelArrayRef(ctx, &ctx->currentCallFrame->localVars[localSlot], varType == VARTYPE_ARRAYPOPAF);
                    break;
                }
#endif
                // Locals are always non-builtin (varID >= 0); inline the read straight from localVars[].
                Variable* varDef = resolveVarDef(ctx, varRef);
                uint32_t localSlot = resolveLocalSlot(ctx, varDef->varID);
                require(ctx->currentCallFrame->localVarsCount > localSlot);
                RValue val = ctx->currentCallFrame->localVars[localSlot];
                val.ownsReference = false;
                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
#ifdef ENABLE_VM_TRACING
                VM_checkIfVariableShouldBeTracedAndLog(ctx, "local", nullptr, varDef->name, val, false, -1, -1, "");
#endif
                break;
            }
            case OP_PUSH_GLOBAL: {
                uint32_t varRef = resolveVarOperand(extraData);
                uint8_t varType = (uint8_t) ((varRef >> 24) & 0xF8);
                if (varType == VARTYPE_NORMAL) {
                    Variable* varDef = resolveVarDef(ctx, varRef);                                                                           
                    if (varDef->varID >= 0) {
                        Instance* inst = ctx->globalScopeInstance;
                        if (inst != nullptr) {
                            RValue* slot = IntRValueHashMap_findSlot(&inst->selfVars, varDef->varID);
                            if (slot != nullptr) {
                                RValue val = *slot;
                                val.ownsReference = false;
                                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
#ifdef ENABLE_VM_TRACING
                                VM_checkIfVariableShouldBeTracedAndLog(ctx, "global", nullptr, varDef->name, val, false, -1, -1, "");
#endif
                                break;
                            }
                        }
                    }
                }
                RValue val = resolveVariableRead(ctx, INSTANCE_GLOBAL, varRef);
                stackPushTyped(ctx, val, GML_TYPE_VARIABLE);
                break;
            }
            case OP_PUSH_BUILTIN:
                handlePushBuiltin(ctx, opword, extraData);
                break;
            case OP_PACKED_FLAME_VENT: {
                if (fuseInstructions && canSkipFlameVentLoop(ctx)) {
#ifdef HLD_VERIFY_FLAME_VENT
                    flameVentStack = ctx->stack.top;
#else
                    Instance_setSelfVar(ctx->currentCallFrame->self, flameVentGuard.object, RValue_makeReal(437.0));
                    ip = HLD_FLAME_VENT_LOOP_END;
#ifdef HLD_COMPARE_FLAME_VENT
                    ++flameVentSkips;
#endif
                    break;
#endif
                }
                handlePushImmediate(ctx, (OpWord){0x840f0000u});
                break;
            }
            case OP_PUSH_IMMEDIATE: {
                if (fuseInstructions && ip <= codeLength && codeLength - ip >= 4) {
                    OpWord next = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};
                    int32_t immediate = OpWord_extra(opword);
                    if (next.value == 0x07520000u) {
                        // The adjacent INT32-to-variant conversion only retags the literal.
                        stackPushTyped(ctx, RValue_makeInt32(immediate), GML_TYPE_VARIABLE);
                        ip += 4;
                        break;
                    }
                    // Format-15/16 array reads commonly push a fixed scope and
                    // index, only to pop both on the next instruction.
                    if (OpWord_opcode(next) == OP_PUSH_IMMEDIATE && codeLength - ip >= 12 &&
                        (immediate == INSTANCE_SELF || immediate == INSTANCE_OTHER ||
                         immediate == INSTANCE_LOCAL || immediate == INSTANCE_GLOBAL) &&
                        IS_WAD15_OR_HIGHER(ctx) && IS_WAD16_OR_BELOW(ctx)) {
                        OpWord read = {BinaryUtils_readUint32Aligned(bytecodeBase + ip + 4)};
                        if (OpWord_opcode(read) == OP_PUSH && OpWord_type1(read) == GML_TYPE_VARIABLE) {
                            uint32_t ref = resolveVarOperand(bytecodeBase + ip + 8);
                            if (((ref >> 24) & 0xF8) == VARTYPE_ARRAY) {
                                ArrayAccess access = {.arrayIndex = OpWord_extra(next), .instanceType = immediate,
                                    .isArray = true, .hasInstanceType = true};
                                RValue value = resolveVariableReadWithAccess(ctx, OpWord_extra(read), ref, access);
                                stackPushTyped(ctx, value, GML_TYPE_VARIABLE);
                                ip += 12;
                                break;
                            }
                        }
                    }
                }
                handlePushImmediate(ctx, opword);
                break;
            }

            // Pop instructions
            case OP_POP: {
                uint8_t type1 = OpWord_type1(opword);
                uint32_t varRef = resolveVarOperand(extraData);
                uint8_t varType = (uint8_t) ((varRef >> 24) & 0xF8);
                int32_t instanceType = OpWord_extra(opword);
                // BC17: VARTYPE_INSTANCE encodes (instanceId - INSTANCE_ID_BASE) in the instruction's lower 16 bits.
                if (varType == VARTYPE_INSTANCE) instanceType += INSTANCE_ID_BASE;
                int32_t type2 = OpWord_type2(opword); // source type (what's on stack)
                if (type1 == GML_TYPE_VARIABLE && varType == VARTYPE_NORMAL) {
                    // Inline fast path for the simple variable-assignment case: type1==VARIABLE, which is ~99.998% of all Pops in real workloads
                    RValue val = stackPop(ctx);
                    val = coerceIntStoreToReal(val, type2);
                    resolveVariableWrite(ctx, instanceType, varRef, val);
                } else {
                    handlePop(ctx, type1, type2, varRef, varType, instanceType);
                }
                break;
            }
            case OP_POPZ:
                handlePopz(ctx);
                break;

            // Arithmetic
            // We keep the number + number operations inlined in executeBytecodeInstructions, keeping only the slow path for string concat/repetition
            case OP_ADD: {
                RValue* slotA = &slots[ctx->stack.top - 2];
                RValue* slotB = &slots[ctx->stack.top - 1];
                uint8_t aType = slotA->type;
                uint8_t bType = slotB->type;
                if ((aType == RVALUE_INT32 || aType == RVALUE_REAL) && (bType == RVALUE_INT32 || bType == RVALUE_REAL)) {
                    if (aType == RVALUE_INT32 && bType == RVALUE_INT32) {
                        slotA->int32 = slotA->int32 + slotB->int32;
                    } else {
                        // Read both operands as locals before writing back, since the union means
                        // slotA->real and slotA->int32 share storage.
                        GMLReal aVal = (aType == RVALUE_INT32) ? (GMLReal) slotA->int32 : slotA->real;
                        GMLReal bVal = (bType == RVALUE_INT32) ? (GMLReal) slotB->int32 : slotB->real;
                        slotA->real = aVal + bVal;
                        slotA->type = RVALUE_REAL;
                    }
                    slotA->gmlStackType = OpWord_type2(opword);
                    ctx->stack.top--;
                } else {
                    uint8_t resultType = OpWord_type2(opword);
                    RValue b = stackPop(ctx);
                    RValue a = stackPop(ctx);
                    if (a.type == RVALUE_STRING || b.type == RVALUE_STRING) {
                        handleAddString(ctx, a, b, resultType);
                        break;
                    }
#ifndef NO_RVALUE_INT64
                    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) {
                        stackPushTyped(ctx, RValue_makeInt64(a.int64 + b.int64), resultType);
                        break;
                    }
#endif
                    GMLReal result = RValue_toReal(a) + RValue_toReal(b);
                    RValue_free(&a);
                    RValue_free(&b);
                    stackPushTyped(ctx, RValue_makeReal(result), resultType);
                }
                break;
            }
            case OP_SUB: {
                RValue* slotA = &slots[ctx->stack.top - 2];
                RValue* slotB = &slots[ctx->stack.top - 1];
                uint8_t aType = slotA->type;
                uint8_t bType = slotB->type;
                if ((aType == RVALUE_INT32 || aType == RVALUE_REAL) && (bType == RVALUE_INT32 || bType == RVALUE_REAL)) {
                    if (aType == RVALUE_INT32 && bType == RVALUE_INT32) {
                        slotA->int32 = slotA->int32 - slotB->int32;
                    } else {
                        GMLReal aVal = (aType == RVALUE_INT32) ? (GMLReal) slotA->int32 : slotA->real;
                        GMLReal bVal = (bType == RVALUE_INT32) ? (GMLReal) slotB->int32 : slotB->real;
                        slotA->real = aVal - bVal;
                        slotA->type = RVALUE_REAL;
                    }
                    slotA->gmlStackType = OpWord_type2(opword);
                    ctx->stack.top--;
                } else {
                    uint8_t resultType = OpWord_type2(opword);
                    RValue b = stackPop(ctx);
                    RValue a = stackPop(ctx);
#ifndef NO_RVALUE_INT64
                    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) {
                        stackPushTyped(ctx, RValue_makeInt64(a.int64 - b.int64), resultType);
                        break;
                    }
#endif
                    GMLReal result = RValue_toReal(a) - RValue_toReal(b);
                    RValue_free(&a);
                    RValue_free(&b);
                    stackPushTyped(ctx, RValue_makeReal(result), resultType);
                }
                break;
            }
            case OP_MUL: {
                RValue* slotA = &slots[ctx->stack.top - 2];
                RValue* slotB = &slots[ctx->stack.top - 1];
                uint8_t aType = slotA->type;
                uint8_t bType = slotB->type;
                if ((aType == RVALUE_INT32 || aType == RVALUE_REAL) && (bType == RVALUE_INT32 || bType == RVALUE_REAL)) {
                    if (aType == RVALUE_INT32 && bType == RVALUE_INT32) {
                        slotA->int32 = slotA->int32 * slotB->int32;
                    } else {
                        GMLReal aVal = (aType == RVALUE_INT32) ? (GMLReal) slotA->int32 : slotA->real;
                        GMLReal bVal = (bType == RVALUE_INT32) ? (GMLReal) slotB->int32 : slotB->real;
                        slotA->real = aVal * bVal;
                        slotA->type = RVALUE_REAL;
                    }
                    slotA->gmlStackType = OpWord_type2(opword);
                    ctx->stack.top--;
                } else {
                    uint8_t resultType = OpWord_type2(opword);
                    RValue b = stackPop(ctx);
                    RValue a = stackPop(ctx);
                    if (a.type == RVALUE_STRING) {
                        handleMulString(ctx, a, b, resultType);
                        break;
                    }
#ifndef NO_RVALUE_INT64
                    if (a.type == RVALUE_INT64 && b.type == RVALUE_INT64) {
                        stackPushTyped(ctx, RValue_makeInt64(a.int64 * b.int64), resultType);
                        break;
                    }
#endif
                    GMLReal result = RValue_toReal(a) * RValue_toReal(b);
                    RValue_free(&a);
                    RValue_free(&b);
                    stackPushTyped(ctx, RValue_makeReal(result), resultType);
                }
                break;
            }
            case OP_DIV: handleDiv(ctx, opword); break;
            case OP_REM: handleRem(ctx, opword); break;
            case OP_MOD: handleMod(ctx, opword); break;

            // Bitwise / Logical
            case OP_AND: handleAnd(ctx, opword); break;
            case OP_OR:  handleOr(ctx, opword);  break;
            case OP_XOR: handleXor(ctx, opword); break;
            case OP_SHL: handleShl(ctx, opword); break;
            case OP_SHR: handleShr(ctx, opword); break;

            // Unary
            case OP_NEG: handleNeg(ctx, opword); break;
            case OP_NOT: handleNot(ctx, opword); break;

            // Type conversion
            case OP_CONV: {
                uint8_t srcType = OpWord_type1(opword);
                uint8_t dstType = OpWord_type2(opword);
                uint8_t convKey = (uint8_t) ((dstType << 4) | srcType);
                RValue* top = &slots[ctx->stack.top - 1];
                bool fastHit = false;

                // Inline fast paths for the four conversions that account for ~93% of all Conv opcodes in real workloads
                switch (convKey) {
                    case 0x52: // Int32 -> Variable (pure passthrough; just retag stack slot)
                        fastHit = true;
                        break;
                    case 0x45: // Variable -> Bool
                        switch (top->type) {
                            case RVALUE_INT32:
                                top->int32 = top->int32 > 0 ? 1 : 0;
                                top->type = RVALUE_BOOL;
                                fastHit = true;
                                break;
                            case RVALUE_BOOL:
                                // Already 0/1; nothing to do
                                fastHit = true;
                                break;
                            case RVALUE_REAL:
                                top->int32 = top->real > (GMLReal) 0.5 ? 1 : 0;
                                top->type = RVALUE_BOOL;
                                fastHit = true;
                                break;
                        }
                        break;
                    case 0x25: // Variable -> Int32
                        switch (top->type) {
                            case RVALUE_INT32:
                                fastHit = true;
                                break;
                            case RVALUE_BOOL:
                                top->type = RVALUE_INT32;
                                fastHit = true;
                                break;
                            case RVALUE_REAL:
                                top->int32 = (int32_t) top->real;
                                top->type = RVALUE_INT32;
                                fastHit = true;
                                break;
                        }
                        break;
                    case 0x02: // Int32 -> Double (Real)
                        top->real = (GMLReal) top->int32;
                        top->type = RVALUE_REAL;
                        fastHit = true;
                        break;
                }

                if (fastHit) {
                    if (fuseInstructions && dstType == GML_TYPE_BOOL &&
                        consumeConditionalBranch(bytecodeBase, codeLength, &ip, top->int32 != 0)) {
                        --ctx->stack.top;
                        break;
                    }
                    top->gmlStackType = dstType;
                } else {
                    handleConv(ctx, srcType, dstType, convKey);
                }
                break;
            }

            // Comparison
            case OP_CMP: {
                RValue* slotA = &slots[ctx->stack.top - 2];
                RValue* slotB = &slots[ctx->stack.top - 1];
                uint8_t aType = slotA->type;
                uint8_t bType = slotB->type;

                // Inline fast path for INT32|REAL/INT32|REAL
                if ((aType == RVALUE_INT32 || aType == RVALUE_REAL) && (bType == RVALUE_INT32 || bType == RVALUE_REAL)) {
                    GMLReal a = aType == RVALUE_INT32 ? (GMLReal)slotA->int32 : slotA->real;
                    GMLReal b = bType == RVALUE_INT32 ? (GMLReal)slotB->int32 : slotB->real;
                    GMLReal diff = a - b;
                    GMLReal epsilon = (ctx->runner != NULL) ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
                    int cmp = (GMLReal_fabs(diff) <= epsilon) ? 0 : (diff < 0) ? -1 : 1;
                    bool result;
                    switch (OpWord_comparisonFunction(opword)) {
                        case CMPOP_LESS_THAN:  result = cmp < 0;  break;
                        case CMPOP_LESS_THAN_OR_EQUAL: result = cmp <= 0; break;
                        case CMPOP_EQUAL:  result = cmp == 0; break;
                        case CMPOP_NOT_EQUAL: result = cmp != 0; break;
                        case CMPOP_GREATER_THAN_OR_EQUAL: result = cmp >= 0; break;
                        case CMPOP_GREATER_THAN:  result = cmp > 0;  break;
                        default: abort(); // TODO: Maybe use a pretty message here? "Unsupported comparsion function"
                    }
                    if (fuseInstructions && consumeConditionalBranch(bytecodeBase, codeLength, &ip, result)) {
                        ctx->stack.top -= 2;
                        break;
                    }
                    slotA->int32 = result ? 1 : 0;
                    slotA->type = RVALUE_BOOL;
                    slotA->gmlStackType = GML_TYPE_BOOL;
                    ctx->stack.top--;
                } else {
                    handleCmp(ctx, opword);
                }
                break;
            }

            // Duplicate
            case OP_PACKED_SWITCH: {
                uint32_t resume = instrAddr;
                if (fuseInstructions && trySwitchChain(ctx, bytecodeBase, (uint16_t)OpWord_extra(opword), &resume)) {
                    ip = resume;
                } else {
                    handleDup(ctx, (OpWord){SWITCH_DUP_WORD});
                }
                break;
            }
            case OP_DUP:
                handleDup(ctx, opword);
                break;

            // Branches
            // The reason why these (the branches opcodes) are inlined is because they access ctx->ip
            // So, because they are short n' sweet, we prefer to keep them inlined to avoid any reloading shenanigans that the compiler may do
            case OP_B: {
                int32_t offset = OpWord_branchOffset(opword);
                ip = instrAddr + offset;
                break;
            }
            case OP_BT: {
                bool condition = stackPopInt32(ctx) != 0;
                if (condition == true) {
                    int32_t offset = OpWord_branchOffset(opword);
                    ip = instrAddr + offset;
                }
                break;
            }
            case OP_BF: {
                bool condition = stackPopInt32(ctx) != 0;
                if (condition == false) {
                    int32_t offset = OpWord_branchOffset(opword);
                    ip = instrAddr + offset;
                }
                break;
            }

            // Function call
            case OP_PACKED_PURE_GUARD: {
                const int32_t argc = OpWord_extra(opword);
                const OpWord originalCall = {UINT32_C(0xd9020000) | (uint16_t)argc};
                bool answer;
                if (fuseInstructions && pureGuardResult(ctx, argc, &answer)) {
#ifdef HLD_VERIFY_PURE_GUARDS
                    VM_SYNC_IP();
                    handleCall(ctx, originalCall, extraData, false);
                    const RValue result = ctx->stack.slots[ctx->stack.top - 1];
                    require(result.type == RVALUE_INT32 && result.int32 == (int)answer);
                    notePureGuard(argc);
                    break;
#else
                    const bool negated = OpWord_type2(opword) != 0;
                    uint32_t branch = ip + (negated ? 8 : 4);
                    if (consumeConditionalBranch(bytecodeBase, codeLength, &branch, answer != negated)) {
                        ctx->stack.top -= argc;
                        ip = branch;
                        notePureGuard(argc);
                        break;
                    }
#endif
                }
                VM_SYNC_IP();
                handleCall(ctx, originalCall, extraData, false);
                break;
            }
            case OP_CALL:
                VM_SYNC_IP();
                handleCall(ctx, opword, extraData, false);
                if (instrAddr == 0x7c && finishRestingScenery(ctx, code))
                    return scriptFallthroughReturnValue(ctx);
                break;
#if IS_WAD17_OR_HIGHER_ENABLED
            case OP_CALLV:
                VM_SYNC_IP();
                handleCallV(ctx, opword);
                break;
#endif

            // Return
            case OP_RET: {
                RValue retVal = stackPop(ctx);
                return retVal;
            }

            // Exit (no return value)
            case OP_EXIT:
                return scriptFallthroughReturnValue(ctx);

            // Environment (with-statements)
            case OP_PACKED_COPY_BLOCK: {
                const uint32_t distance = opword.value & COPY_BLOCK_MASK;
                if (fuseInstructions && tryCopyBlock(ctx, (opword.value >> COPY_BLOCK_BITS) & COPY_BLOCK_MASK)) {
                    ip = instrAddr + (distance + 1) * sizeof(uint32_t);
                } else {
                    VM_SYNC_IP();
                    handlePushEnv(ctx, (OpWord){((uint32_t)OP_PUSH_ENV << 24) | distance}, instrAddr);
                    VM_RELOAD_IP();
                }
                break;
            }
            case OP_PUSH_ENV:
                VM_SYNC_IP();
                handlePushEnv(ctx, opword, instrAddr);
                VM_RELOAD_IP();
                break;
            case OP_POP_ENV:
                VM_SYNC_IP();
                if (hldSceneryCursor && useHldSceneryCull && instrAddr == HLD_SCENERY_NEXT_INSTANCE &&
                    (uint32_t)(instrAddr + OpWord_branchOffset(opword)) == HLD_SCENERY_CULL_START)
                    popSceneryEnv(ctx, opword, instrAddr, bytecodeBase);
                else
                    handlePopEnv(ctx, opword, instrAddr);
                VM_RELOAD_IP();
                break;

            // Break (extended opcodes in V17+, no-op/debug in V16)
            case OP_BREAK:
#if IS_WAD17_OR_HIGHER_ENABLED
                handleBreak(ctx, opword, instrAddr, extraData);
#endif
                break;

            default:
                logError("VM: Unknown opcode 0x%02X at offset %u\n", opcode, instrAddr);
                abort();
        }
    }

    return scriptFallthroughReturnValue(ctx);
}

// Rewrites WAD 14 bytecode opcodes to use WAD 16 bytecode opcodes
static void rewriteBytecode14To16(VMContext* ctx) {
    DataWin* dw = ctx->dataWin;
    uint8_t* buf = dw->bytecodeBuffer;
    size_t base = dw->bytecodeBufferBase;

    // Synthesize varIDs for each variable
    repeat(dw->vari.variableCount, i) {
        dw->vari.variables[i].varID = (int32_t) i;
    }

    repeat(dw->code.count, codeIdx) {
        CodeEntry* entry = &dw->code.entries[codeIdx];
        if (entry->length == 0) continue;

        uint32_t ip = entry->bytecodeAbsoluteOffset;
        uint32_t end = ip + entry->length;

        while (end > ip) {
            uint32_t instrAddr = ip;
            uint32_t instr = BinaryUtils_readUint32(&buf[instrAddr - base]);
            uint8_t oldKind = (instr >> 24) & 0xFF;
            uint8_t type1 = (instr >> 16) & 0x0F;

            uint8_t newKind = oldKind;
            switch (oldKind) {
                case 0x03: newKind = OP_CONV; break;
                case 0x04: newKind = OP_MUL; break;
                case 0x05: newKind = OP_DIV; break;
                case 0x06: newKind = OP_REM; break;
                case 0x07: newKind = OP_MOD; break;
                case 0x08: newKind = OP_ADD; break;
                case 0x09: newKind = OP_SUB; break;
                case 0x0A: newKind = OP_AND; break;
                case 0x0B: newKind = OP_OR; break;
                case 0x0C: newKind = OP_XOR; break;
                case 0x0D: newKind = OP_NEG; break;
                case 0x0E: newKind = OP_NOT; break;
                case 0x0F: newKind = OP_SHL; break;
                case 0x10: newKind = OP_SHR; break;
                case 0x11: case 0x12: case 0x13: case 0x14: case 0x15: case 0x16:
                    newKind = OP_CMP;
                    instr = (instr & 0xFF0000FF) | ((uint32_t)(oldKind - 0x10) << 8);
                    break;
                case 0x41: newKind = OP_POP; break;
                case 0x82: newKind = OP_DUP; break;
                case 0x9D: newKind = OP_RET; break;
                case 0x9E: newKind = OP_EXIT; break;
                case 0x9F: newKind = OP_POPZ; break;
                case 0xB7: newKind = OP_B; break;
                case 0xB8: newKind = OP_BT; break;
                case 0xB9: newKind = OP_BF; break;
                case 0xBB: newKind = OP_PUSH_ENV; break;
                case 0xBC: newKind = OP_POP_ENV; break;
                case 0xDA: newKind = OP_CALL; break;
                case 0xC0:
                    if (type1 == GML_TYPE_INT16) {
                        newKind = OP_PUSH_IMMEDIATE;
                    }
                    break;
            }

            if (newKind != oldKind || oldKind == 0x11 || oldKind == 0x12 || oldKind == 0x13 || oldKind == 0x14 || oldKind == 0x15 || oldKind == 0x16) {
                instr = (instr & 0x00FFFFFF) | ((uint32_t)newKind << 24);
                BinaryUtils_writeUint32(&buf[instrAddr - base], instr);
            }

            // BC13/14 encodes the globalvar flag as bit 30 of the operand.
            // We will strip the globalvar flag and replace it with a proper BC16+ INSTANCE_GLOBAL instanceType
            if ((newKind == OP_PUSH || newKind == OP_POP) && type1 == GML_TYPE_VARIABLE) {
                uint32_t operandAddr = instrAddr + 4;
                uint32_t operand = BinaryUtils_readUint32(&buf[operandAddr - base]);
                // 0x40000000u = "globalvar"
                if (operand & 0x40000000u) {
                    // Remove the globalvar state from the operand
                    operand &= ~0x40000000u;
                    BinaryUtils_writeUint32(&buf[operandAddr - base], operand);
                    // And replace it to set the instanceType to "INSTANCE_GLOBAL"
                    instr = (instr & 0xFFFF0000u) | (uint16_t) INSTANCE_GLOBAL;
                    BinaryUtils_writeUint32(&buf[instrAddr - base], instr);
                }
            }

            uint32_t size = 4;
            switch (newKind) {
                case OP_PUSH: case OP_PUSH_LOCAL: case OP_PUSH_GLOBAL: case OP_PUSH_BUILTIN:
                    if (type1 == GML_TYPE_INT16) size = 4;
                    else if (type1 == GML_TYPE_DOUBLE || type1 == GML_TYPE_INT64) size = 12;
                    else size = 8;
                    break;
                case OP_POP:
                    size = (type1 == GML_TYPE_INT16) ? 4 : 8;
                    break;
                case OP_CALL:
                    size = 8;
                    break;
                case OP_BREAK:
                    size = (type1 == GML_TYPE_INT32) ? 8 : 4;
                    break;
            }
            ip += size;
        }
    }
}

// ===[ Public API ]===

static const CodeEntry* matchHldCode(VMContext* ctx, const char* name, uint32_t length, uint32_t expected) {
    ptrdiff_t slot = shgeti(ctx->codeIndexByName, (char*) name);
    if (slot < 0) return nullptr;
    const CodeEntry* code = &ctx->dataWin->code.entries[ctx->codeIndexByName[slot].value];
    if (code->length != length || code->offset != 0) return nullptr;
    const uint8_t* bytes = codeBytes(ctx, code);
    uint32_t fingerprint = 2166136261u;
    for (uint32_t offset = 0; offset < length; offset += 4) {
        fingerprint = (fingerprint ^ BinaryUtils_readUint32Aligned(bytes + offset)) * 16777619u;
    }
    return fingerprint == expected ? code : nullptr;
}

typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t usesCase, canEdit, selector, state, check, caseArgument, links, linkCase;
    int32_t noObjectsCase, touchCase, linkedCase;
#ifdef HLD_LINK_CASES
    int32_t brokenLinkCase;
    bool knownLinkTypes;
#endif
} StableCases;

static StableCases stableCases;

#ifdef HLD_CASE_CHECK_LAUNCH
extern bool hldCaseChecks;
static unsigned activeCaseChecks, brokenLinkChecks;

void hldReportCaseChecks(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita case checks frame=%d enabled=%d calls=%u\n",
            frame, (int)hldCaseChecks, activeCaseChecks + brokenLinkChecks);
    logInfo("Vita case check counts frame=%d active=%u linked=%u\n",
            frame, activeCaseChecks, brokenLinkChecks);
    activeCaseChecks = brokenLinkChecks = 0;
}
#endif

static void bindStableCases(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_CaseCheck", 2720, 0x7d91a362u);
    if (code == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint16_t operands[] = {0x0008, 0x0010, 0x00f0, 0x0120, 0x0988, 0x04cc, 0x0460, 0x0594,
                                 0x0274, 0x0288, 0x029c,
#ifdef HLD_LINK_CASES
                                 0x02b0,
#endif
    };
    int32_t varIds[sizeof(operands) / sizeof(*operands)];
    repeat(sizeof(operands) / sizeof(*operands), i) {
        varIds[i] = resolveVarDef(ctx, resolveVarOperand(bytes + operands[i]))->varID;
        if (varIds[i] < 0) return;
    }
    stableCases = (StableCases){
        .context = ctx, .code = code, .usesCase = varIds[0], .canEdit = varIds[1],
        .selector = varIds[2], .state = varIds[3], .check = varIds[4], .caseArgument = varIds[5],
        .links = varIds[6], .linkCase = varIds[7], .noObjectsCase = varIds[8],
        .touchCase = varIds[9], .linkedCase = varIds[10],
    };
#ifdef HLD_LINK_CASES
    stableCases.brokenLinkCase = varIds[11];
    stableCases.knownLinkTypes = matchHldCode(ctx, "gml_Script_IsType", 416, 0xc748538fu) != nullptr;
#endif
    logInfo("HLD: bound unchanged case-query results\n");
}

#ifdef HLD_COMPARE_STABLE_CASES
static unsigned stableCaseReturns;

static inline bool useStableCases(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportStableCases(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita stable cases frame=%d enabled=%d returns=%u\n",
            frame, (int)useStableCases(frame - 1), stableCaseReturns);
    stableCaseReturns = 0;
}
#define COUNT_STABLE_CASE() (++stableCaseReturns)
#elif defined(HLD_VERIFY_STABLE_CASES)
static unsigned stableCaseVerified;
#define COUNT_STABLE_CASE() do { \
    if (++stableCaseVerified == 1000) logInfo("HLD: checked 1,000 unchanged case-query results\n"); \
} while (0)
#else
#define COUNT_STABLE_CASE() ((void)0)
#endif

static inline bool caseNumber(RValue value, GMLReal* result) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *result = RValue_toReal(value);
    return isfinite(*result);
}

// A successful link would assign linkArg and possibly change the owning
// instance's case state. Only handle the read-only no-match path here.
static bool caseLinksUnchanged(VMContext* ctx, Instance* inst, GMLReal epsilon) {
    const RValue links = Instance_getSelfVar(inst, stableCases.links);
    if (links.type != RVALUE_ARRAY) return true;
    if (links.array == nullptr) return false;

    GMLReal index;
    if (!caseNumber(Instance_getSelfVar(inst, stableCases.caseArgument), &index)) return false;
    const bool matchIndex = index >= -epsilon;
    const int32_t length = GMLArray_length1D(links.array);
    for (int32_t i = 0; i < length; ++i) {
        const RValue* link = GMLArray_slot(links.array, i);
        GMLReal id;
        if (link == nullptr || !caseNumber(*link, &id) || !(id >= INSTANCE_ID_BASE && id <= INT32_MAX)) return false;
        const int32_t target = (int32_t)id;
        if ((GMLReal)target != id || (uint32_t)target < ctx->dataWin->objt.count) return false;
        if (matchIndex && GMLReal_fabs(i - index) > epsilon) continue;
        Instance* candidate = hldLookupInstanceId(ctx->runner, target);
        if (candidate == nullptr || !candidate->active) continue;
        GMLReal state;
        if (!caseNumber(Instance_getSelfVar(candidate, stableCases.linkCase), &state) ||
            GMLReal_fabs(state - 2.0) <= epsilon) return false;
    }
    return true;
}

#ifdef HLD_LINK_CASES
// Script-specific spawners and collectibles have extra liveness checks.
static bool isOrdinaryCaseLink(VMContext* ctx, const Instance* target) {
    enum { CASE_SPAWNER_OBJECT = 46, CASE_COLLECTIBLE_OBJECT = 131 };
    if (target->objectIndex == CASE_SPAWNER_OBJECT) return false;
    const uint32_t count = ctx->dataWin->objt.count;
    int32_t object = target->objectIndex;
    for (uint32_t i = 0; i < count && (uint32_t)object < count; ++i) {
        if (object == CASE_COLLECTIBLE_OBJECT) return false;
        object = ctx->dataWin->objt.objects[object].parentId;
        if (object == -1) return true;
    }
    return false;
}

static bool caseBrokenLinkReturnsZero(VMContext* ctx, Instance* inst, GMLReal epsilon) {
    const RValue links = Instance_getSelfVar(inst, stableCases.links);
    if (links.type != RVALUE_ARRAY) return true;
    if (links.array == nullptr || !stableCases.knownLinkTypes) return false;
    GMLReal arg;
    if (!caseNumber(Instance_getSelfVar(inst, stableCases.caseArgument), &arg)) return false;
    const bool any = GMLReal_fabs(arg + 999999.0) <= epsilon;
    const bool all = GMLReal_fabs(arg + 1.0) <= epsilon;
    const int32_t length = GMLArray_length1D(links.array);
    for (int32_t i = 0; i < length; ++i) {
        const RValue* link = GMLArray_slot(links.array, i);
        GMLReal id;
        if (link == nullptr || !caseNumber(*link, &id) || !(id >= INSTANCE_ID_BASE && id < 16777216) ||
            (uint32_t)id < ctx->dataWin->objt.count || id != (int32_t)id) return false;
        const Instance* target = hldLookupInstanceId(ctx->runner, (int32_t)id);
        if (target == nullptr || !target->active) {
            if (all) return false;
            continue;
        }
        if (!isOrdinaryCaseLink(ctx, target)) return false;
        if (any || (arg >= -epsilon && GMLReal_fabs(arg - i) <= epsilon)) return true;
    }
    return all && length > 0;
}
#endif

static bool caseLinkResultUnchanged(VMContext* ctx, Instance* inst, GMLReal selector,
                                    GMLReal check, GMLReal epsilon) {
    Instance* global = ctx->globalScopeInstance;
    GMLReal linked;
    if (!caseNumber(Instance_getSelfVar(global, stableCases.linkedCase), &linked)) return false;
    if (GMLReal_fabs(selector - linked) <= epsilon) return caseLinksUnchanged(ctx, inst, epsilon);
#ifdef HLD_LINK_CASES
#ifdef HLD_CASE_CHECK_LAUNCH
    if (!hldCaseChecks) return false;
#endif
    GMLReal broken;
    if (GMLReal_fabs(check - 1.0) > epsilon ||
        !caseNumber(Instance_getSelfVar(global, stableCases.brokenLinkCase), &broken) ||
        GMLReal_fabs(selector - broken) > epsilon || !caseBrokenLinkReturnsZero(ctx, inst, epsilon)) return false;
#ifdef HLD_VERIFY_STABLE_CASES
    static unsigned checkedBrokenLinkCases;
    if (++checkedBrokenLinkCases == 100) logInfo("HLD: checked 100 unchanged broken-link cases\n");
#endif
#ifdef HLD_CASE_CHECK_LAUNCH
    ++brokenLinkChecks;
#endif
    return true;
#else
    (void)check;
    return false;
#endif
}

#define HLD_COMPARE_REGION_CASES
typedef struct {
    const VMContext* context;
    int32_t target, width, height;
} RegionCase;

static RegionCase regionCase;

#ifdef HLD_COMPARE_REGION_CASES
static unsigned regionMisses;

static inline bool useRegionCases(int frame) {
    extern bool hldRefinedRuntime;
    (void)frame;
    return hldRefinedRuntime;
}

void hldReportRegionCases(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita region cases frame=%d enabled=%d misses=%u\n",
            frame, (int)useRegionCases(frame - 1), regionMisses);
    regionMisses = 0;
}
#endif

static void bindRegionCase(VMContext* ctx) {
    if (stableCases.context != ctx) return;
    const CodeEntry* rectangle = matchHldCode(ctx, "gml_Script_PointInRegion", 276, 0x3ad4ebe2u);
    if (rectangle == nullptr || rectangle->localsCount != 7) return;

    const uint8_t* code = codeBytes(ctx, stableCases.code);
    const uint32_t call = resolveFuncOperand(code + 0x03a0);
    if (call >= ctx->funcCallCacheCount || ctx->funcCallCache[call].builtin != nullptr ||
        ctx->funcCallCache[call].scriptCodeIndex != rectangle - ctx->dataWin->code.entries) return;

    const int32_t target = resolveVarDef(ctx, resolveVarOperand(code + 0x0348))->varID;
    const int32_t height = resolveVarDef(ctx, resolveVarOperand(code + 0x0360))->varID;
    const int32_t width = resolveVarDef(ctx, resolveVarOperand(code + 0x0374))->varID;
    if (target < 0 || width < 0 || height < 0) return;
    regionCase = (RegionCase){ctx, target, width, height};
    logInfo("HLD: bound the direct region case checks\n");
}

static inline bool outsideCaseRegion(const Instance* target, GMLReal x, GMLReal y,
                                     GMLReal right, GMLReal bottom, GMLReal epsilon) {
    const GMLReal px = target->x, py = target->y;
    if (!isfinite(px) || !isfinite(py)) return false;
    return !(px - x > epsilon && px - right < -epsilon && py - y > epsilon && py - bottom < -epsilon);
}

static bool caseRegionMiss(VMContext* ctx, Instance* inst, GMLReal epsilon) {
    if (regionCase.context != ctx || inst->objectIndex != 58) return false;
#ifdef HLD_COMPARE_REGION_CASES
    if (!useRegionCases(ctx->runner->frameCount)) return false;
#endif

    const RValue reference = Instance_getSelfVar(inst, regionCase.target);
    int32_t target;
    if (reference.type == RVALUE_INT32) {
        target = reference.int32;
    } else {
        if (reference.type != RVALUE_REAL || !(reference.real >= 0 && (double)reference.real < 2147483648.0))
            return false;
        target = (int32_t)reference.real;
        if ((GMLReal)target != reference.real) return false;
    }
    GMLReal width, height;
    if (target < 0 || !caseNumber(Instance_getSelfVar(inst, regionCase.width), &width) ||
        !caseNumber(Instance_getSelfVar(inst, regionCase.height), &height)) return false;
    const GMLReal x = inst->x, y = inst->y, right = x + width, bottom = y + height;
    if (!isfinite(x) || !isfinite(y) || !isfinite(right) || !isfinite(bottom)) return false;

    Runner* runner = ctx->runner;
    if (target >= INSTANCE_ID_BASE) {
        Instance* actor = hldLookupInstanceId(runner, target);
        if (actor != nullptr && actor->active && !outsideCaseRegion(actor, x, y, right, bottom, epsilon)) return false;
    } else {
        if ((uint32_t)target >= ctx->dataWin->objt.count || runner->instancesByObject == nullptr) return false;
        Instance** actors = runner->instancesByObject[target];
        repeat(arrlen(actors), i) {
            const Instance* actor = actors[i];
            if (actor->active && !outsideCaseRegion(actor, x, y, right, bottom, epsilon)) return false;
        }
    }

#ifdef HLD_VERIFY_STABLE_CASES
    static unsigned checkedRegionCases;
    if (++checkedRegionCases == 10000) logInfo("HLD: checked 10,000 empty region cases\n");
#endif
#ifdef HLD_COMPARE_REGION_CASES
    ++regionMisses;
#endif
    return true;
}


static unsigned eventlessCaseReturns;
static const VMContext* eventlessCaseContext;

void hldReportEventlessCaseAtExit(void) {
    logInfo("HLD: eventless linked case returns=%u\n", eventlessCaseReturns);
}

static void bindEventlessCase(VMContext* ctx) {
    if (stableCases.context != ctx || stableCases.code == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, stableCases.code);
    const uint32_t event = resolveFuncOperand(bytes + 0x15c);
    const BuiltinFunc builtin = VM_findBuiltin(ctx, "event_user");
    if (event < ctx->funcCallCacheCount && builtin != nullptr &&
        ctx->funcCallCache[event].builtin == (void*)builtin && ctx->funcCallCache[event].scriptCodeIndex < 0)
        eventlessCaseContext = ctx;
}

static bool hldEventlessLinkedCase(VMContext* ctx, Instance* inst, GMLReal selector, GMLReal epsilon) {
    // The first write is performed by the existing stable-case caller.
    // With no callback and a scalar link, the linked test yields zero.
    Runner* runner = ctx->runner;
    if (eventlessCaseContext != ctx || inst->objectIndex < 0 ||
        (uint32_t)inst->objectIndex >= ctx->dataWin->objt.count ||
        !(GMLReal_fabs(selector) < 16777216.0)) return false;
    const int32_t slot = EventSlotMap_lookup(&runner->eventSlotMap, EVENT_OTHER, OTHER_USER0 + 10);
    if (slot >= 0 && ResolvedEventTable_lookup(&runner->eventTable, inst->objectIndex, slot, nullptr) >= 0)
        return false;
    GMLReal check, empty, touch, linked;
    Instance* global = ctx->globalScopeInstance;
    return caseNumber(Instance_getSelfVar(inst, stableCases.check), &check) && GMLReal_fabs(check) <= epsilon &&
        caseNumber(Instance_getSelfVar(global, stableCases.noObjectsCase), &empty) && GMLReal_fabs(empty) < 16777216.0 &&
        caseNumber(Instance_getSelfVar(global, stableCases.touchCase), &touch) && GMLReal_fabs(touch) < 16777216.0 &&
        caseNumber(Instance_getSelfVar(global, stableCases.linkedCase), &linked) && GMLReal_fabs(linked) < 16777216.0 &&
        GMLReal_fabs(selector - empty) > epsilon && GMLReal_fabs(selector - touch) > epsilon &&
        GMLReal_fabs(selector - linked) <= epsilon &&
        Instance_getSelfVar(inst, stableCases.links).type != RVALUE_ARRAY;
}

extern bool hldEventlessCase;

void hldReportEventlessCase(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita eventless linked cases frame=%d enabled=%d returns=%u\n",
            frame, (int)hldEventlessCase, eventlessCaseReturns);
    eventlessCaseReturns = 0;
}

static bool tryStableCase(VMContext* ctx, Instance* inst, RValue* result) {
    if (ctx->runner == nullptr || inst == nullptr || ctx->globalScopeInstance == nullptr) return false;
#ifdef HLD_COMPARE_STABLE_CASES
    if (!useStableCases(ctx->runner->frameCount)) return false;
#endif
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0 && epsilon < 0.5)) return false;
    const RValue editor = Instance_getSelfVar(ctx->globalScopeInstance, stableCases.canEdit);
    if (RValue_toBool(editor)) return false;

    GMLReal selector;
    if (!caseNumber(Instance_getSelfVar(inst, stableCases.selector), &selector)) return false;
    int32_t returned = -1;
    if (GMLReal_fabs(selector) > epsilon) {
        GMLReal state;
        if (!caseNumber(Instance_getSelfVar(inst, stableCases.state), &state)) return false;

        if (hldEventlessCase && GMLReal_fabs(state - 2.0) <= epsilon && hldEventlessLinkedCase(ctx, inst, selector, epsilon)) {
            *result = RValue_makeInt32(1);
            result->gmlStackType = GML_TYPE_VARIABLE;
            ++eventlessCaseReturns;
            return true;
        }
        if (GMLReal_fabs(state - 3.0) > epsilon) {
            // State 2 invokes an event. State 0 with an unmet case check
            // returns zero without invoking the event or updating timers.
            if (GMLReal_fabs(state) > epsilon) return false;
            GMLReal check, empty, touch;
            Instance* global = ctx->globalScopeInstance;
            if (!caseNumber(Instance_getSelfVar(inst, stableCases.check), &check) || GMLReal_fabs(check) <= epsilon ||
                !caseNumber(Instance_getSelfVar(global, stableCases.noObjectsCase), &empty) ||
                !caseNumber(Instance_getSelfVar(global, stableCases.touchCase), &touch) ||
                GMLReal_fabs(selector - empty) <= epsilon) return false;
            if (GMLReal_fabs(selector - touch) <= epsilon) {
                if (!caseRegionMiss(ctx, inst, epsilon)) return false;
            } else if (!caseLinkResultUnchanged(ctx, inst, selector, check, epsilon)) {
                return false;
            }
        }
        returned = 0;
    }
    *result = RValue_makeInt32(returned);
    result->gmlStackType = GML_TYPE_VARIABLE;
    return true;
}

typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t inWall, canEdit, caseLink, canSpawn, spawned, spawnedAmount, spawnAmount, child;
    uint32_t block;
    bool copyBound;
} IdleSpawner;

static IdleSpawner idleSpawner;

static void bindIdleSpawner(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Object_Spawner_Step_0", 2784, 0x5b7fb46eu);
    if (code == nullptr) return;
    // The visibility and pause helpers perform no writes. Only omit their
    // evaluation when the loaded versions match the audited routines.
    if (matchHldCode(ctx, "gml_Script_onscreen", 316, 0xa0a24addu) == nullptr ||
        matchHldCode(ctx, "gml_Script_ActionIsPaused", 96, 0x14535ff1u) == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint16_t operands[] = {0x08, 0x10, 0x188, 0x1bc, 0x318, 0x8c8, 0x8d0, 0x940};
    int32_t ids[sizeof(operands) / sizeof(*operands)];
    repeat(sizeof(operands) / sizeof(*operands), i) {
        ids[i] = resolveVarDef(ctx, resolveVarOperand(bytes + operands[i]))->varID;
        if (ids[i] < 0) return;
    }
    idleSpawner = (IdleSpawner){.context = ctx, .code = code, .inWall = ids[0], .canEdit = ids[1],
        .caseLink = ids[2], .canSpawn = ids[3], .spawned = ids[4], .spawnedAmount = ids[5],
        .spawnAmount = ids[6], .child = ids[7]};
}

static void bindIdleSpawnerCopies(VMContext* ctx) {
    if (idleSpawner.context != ctx) return;
    const OpWord env = {BinaryUtils_readUint32Aligned(codeBytes(ctx, idleSpawner.code) + 0x948)};
    if (OpWord_opcode(env) != OP_PACKED_COPY_BLOCK) return;
    idleSpawner.block = (env.value >> COPY_BLOCK_BITS) & COPY_BLOCK_MASK;
    idleSpawner.copyBound = true;
    logInfo("HLD: bound the finished spawner path and its ordered field copies\n");
}

#ifdef HLD_COMPARE_IDLE_SPAWNERS
static unsigned idleSpawnerSteps;

static inline bool useIdleSpawner(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportIdleSpawners(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita idle spawners frame=%d enabled=%d steps=%u\n",
            frame, (int)useIdleSpawner(frame - 1), idleSpawnerSteps);
    idleSpawnerSteps = 0;
}
#elif defined(HLD_CHECK_IDLE_SPAWNER_REACH)
static unsigned idleSpawnerSteps;
#endif

static inline bool spawnerNumber(Instance* inst, int32_t id, GMLReal* out) {
    const RValue value = Instance_getSelfVar(inst, id);
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *out = RValue_toReal(value);
    return isfinite(*out);
}

static bool tryIdleSpawner(VMContext* ctx, Instance* inst) {
    if (!idleSpawner.copyBound || copyBlockContext != ctx || ctx->runner == nullptr ||
        inst == nullptr || inst->objectIndex == STRUCT_OBJECT_INDEX || ctx->globalScopeInstance == nullptr) return false;
#ifdef HLD_COMPARE_IDLE_SPAWNERS
    if (!useIdleSpawner(ctx->runner->frameCount)) return false;
#endif
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5) ||
        RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, idleSpawner.canEdit))) return false;

    // Keep the movement and spawn paths in the interpreter. Doing this
    // before any write lets changes in the guard take effect on this step.
    if (!(GMLReal_fabs((GMLReal)inst->x - inst->xprevious) <= epsilon &&
          GMLReal_fabs((GMLReal)inst->y - inst->yprevious) <= epsilon)) return false;
    GMLReal link, spawned, already, total, target;
    if (!spawnerNumber(inst, idleSpawner.caseLink, &link) || GMLReal_fabs(link + 999999.0) > epsilon ||
        !spawnerNumber(inst, idleSpawner.spawned, &spawned) || GMLReal_fabs(spawned) <= epsilon ||
        !spawnerNumber(inst, idleSpawner.spawnedAmount, &already) ||
        !spawnerNumber(inst, idleSpawner.spawnAmount, &total) || total - already > epsilon ||
        !spawnerNumber(inst, idleSpawner.child, &target) || target < INSTANCE_ID_BASE || target > INT32_MAX) return false;
    const int32_t id = (int32_t)target;
    if ((GMLReal)id != target) return false;
    Instance* child = hldLookupInstanceId(ctx->runner, id);
    if (child != nullptr && child->objectIndex == STRUCT_OBJECT_INDEX) return false;

    Instance_setSelfVar(inst, idleSpawner.inWall, RValue_makeReal(0.0));
    VMBuiltins_setVariable(ctx, inst, BUILTIN_VAR_VISIBLE, "visible", RValue_makeBool(false), -1);
    Instance_setSelfVar(inst, idleSpawner.canSpawn, RValue_makeReal(1.0));
    if (child != nullptr && child->active) copyInstanceFields(ctx, inst, child, idleSpawner.block);
#ifdef HLD_COMPARE_IDLE_SPAWNERS
    ++idleSpawnerSteps;
#elif defined(HLD_CHECK_IDLE_SPAWNER_REACH)
    if (++idleSpawnerSteps == 1000) logInfo("HLD: executed 1,000 finished spawner steps directly\n");
#endif
    return true;
}

typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t cursor, indent;
    bool enabled;
} AsciiParser;

static AsciiParser asciiParser;

static void bindAsciiParser(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_ParseString", 612, 0xacd2e87fu);
    if (code == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const int32_t cursor = resolveVarDef(ctx, resolveVarOperand(bytes + 0x50))->varID;
    const int32_t indent = resolveVarDef(ctx, resolveVarOperand(bytes + 0x3c))->varID;
    if (cursor < 0 || indent < 0 || cursor == indent) return;
    bool enabled = true;
#ifdef HLD_COMPARE_ASCII_PARSER
    FILE* flag = fopen("ux0:data/hld-vm/ascii-parser", "r");
    enabled = flag != nullptr;
    if (flag != nullptr) fclose(flag);
#endif
    asciiParser = (AsciiParser){ctx, code, cursor, indent, enabled};
    logInfo("HLD ASCII parser enabled=%d\n", (int)enabled);
}

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
#define HLD_ASCII_PARSER_AVAILABLE

typedef struct {
    const char* token;
    size_t bytes;
    RValue cursor;
    bool markIndented;
} AsciiParse;

static bool scanAsciiToken(VMContext* ctx, Instance* inst, RValue text, AsciiParse* parsed) {
    if (text.type != RVALUE_STRING || text.string == nullptr || inst == nullptr ||
        inst->objectIndex == STRUCT_OBJECT_INDEX || ctx->runner == nullptr) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    RValue cursor = Instance_getSelfVar(inst, asciiParser.cursor);
    const RValue indent = Instance_getSelfVar(inst, asciiParser.indent);
    if ((cursor.type != RVALUE_INT32 && cursor.type != RVALUE_REAL) ||
        (indent.type != RVALUE_INT32 && indent.type != RVALUE_REAL)) return false;
    const GMLReal source = RValue_toReal(cursor);
    const GMLReal indentValue = RValue_toReal(indent);
    if (!(source >= 1.0 && source <= 1048576.0) || (indentValue != 0.0 && indentValue != 1.0)) return false;
    size_t position = (size_t)source;
    if ((GMLReal)position != source) return false;
    size_t length = strlen(text.string);
    if (length > 1048576u) return false;
    size_t i = 0;
    for (; length - i >= 4; i += 4) {
        uint32_t word;
        memcpy(&word, text.string + i, sizeof(word));
        if ((word & UINT32_C(0x80808080)) != 0) return false;
    }
    for (; i < length; ++i) if ((unsigned char)text.string[i] >= 128u) return false;

    if (indentValue == 0.0) {
        // The original leaves one character at the end even on an
        // all-space line. Positions are one-based in the game state.
        while (position < length && text.string[position - 1u] == ' ') ++position;
    }
    const char* token = text.string + (position <= length ? position - 1u : length);
    const size_t bytes = strcspn(token, "/,]|");
    // The original advances by the token length and one delimiter, even
    // when the current position is past the string or at a comment.
    const int32_t next = (int32_t)(position + bytes + 1u);
    RValue nextCursor = cursor.type == RVALUE_INT32 ? RValue_makeInt32(next) : RValue_makeReal(next);
    nextCursor.gmlStackType = GML_TYPE_VARIABLE;
    *parsed = (AsciiParse){token, bytes, nextCursor, indentValue == 0.0};
    return true;
}
#endif

#if defined(HLD_COMPARE_ASCII_PARSER) || defined(HLD_VERIFY_ASCII_PARSER)
static unsigned parsedAsciiTokens;
#endif

#ifdef HLD_ASCII_PARSER_AVAILABLE
static inline void noteAsciiParse(void) {
#ifdef HLD_COMPARE_ASCII_PARSER
    ++parsedAsciiTokens;
#elif defined(HLD_VERIFY_ASCII_PARSER)
    if (++parsedAsciiTokens == 1000) logInfo("HLD: verified 1,000 ASCII tokens and cursor updates\n");
#endif
}
#endif

#ifdef HLD_COMPARE_ASCII_PARSER
void hldReportAsciiParser(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita ASCII parser frame=%d enabled=%d tokens=%u\n", frame, (int)asciiParser.enabled, parsedAsciiTokens);
    parsedAsciiTokens = 0;
}
#endif

typedef struct {
    const VMContext* context;
    const CodeEntry* setDepth;
    const CodeEntry* modDepth;
    int32_t roomHeight;
} DepthHelpers;

typedef struct {
    RValue result;
    GMLReal depth;
    bool writeDepth;
} DepthUpdate;

static DepthHelpers depthHelpers;
static unsigned setDepthCalls, modDepthCalls;

static bool useDepthHelpers(int frame) {
#ifdef HLD_COMPARE_DEPTH_HELPERS
    return frame >= 180 && (frame / 180) % 2 != 0;
#else
    (void)frame;
    return true;
#endif
}

#ifdef HLD_COMPARE_DEPTH_HELPERS
void hldReportDepthHelpers(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita depth helpers frame=%d enabled=%d set=%u mod=%u\n", frame,
            (int)useDepthHelpers(frame - 1), setDepthCalls, modDepthCalls);
    setDepthCalls = modDepthCalls = 0;
}
#endif

static void bindDepthHelpers(VMContext* ctx) {
    if (DataWin_isVersionAtLeast(ctx->dataWin, 2, 0, 0, 0)) return;
    const CodeEntry* setDepth = matchHldCode(ctx, "gml_Script_setdepth", 212, 0x25a833e5u);
    const CodeEntry* modDepth = matchHldCode(ctx, "gml_Script_moddepth", 48, 0xcb3ca6d8u);
    if (setDepth == nullptr || modDepth == nullptr || setDepth->localsCount != 1 || modDepth->localsCount != 1)
        return;
    const Variable* setRoom = resolveVarDef(ctx, resolveVarOperand(codeBytes(ctx, setDepth) + 0x88));
    const Variable* modRoom = resolveVarDef(ctx, resolveVarOperand(codeBytes(ctx, modDepth) + 0x1c));
    if (setRoom != modRoom || setRoom->instanceType != INSTANCE_GLOBAL || setRoom->varID < 0 ||
        strcmp(setRoom->name, "roomHei") != 0) return;
    depthHelpers = (DepthHelpers){.context = ctx, .setDepth = setDepth, .modDepth = modDepth, .roomHeight = setRoom->varID};
    logInfo("HLD: bound setdepth and moddepth numeric helpers\n");
}

static bool tryDepthHelper(VMContext* ctx, const CodeEntry* code, Instance* inst, const RValue* args,
                           int32_t count, DepthUpdate* update) {
    if (code != depthHelpers.setDepth && code != depthHelpers.modDepth) return false;
    if (ctx != depthHelpers.context || ctx->runner == nullptr || inst == nullptr ||
        inst->objectIndex == STRUCT_OBJECT_INDEX || ctx->globalScopeInstance == nullptr ||
        !useDepthHelpers(ctx->runner->frameCount) || !isfinite(inst->depth)) return false;

    const bool set = code == depthHelpers.setDepth;
    if (set ? count != 0 : count != 1 || args == nullptr) return false;
    const RValue room = Instance_getSelfVar(ctx->globalScopeInstance, depthHelpers.roomHeight);
    if (room.type != RVALUE_REAL && room.type != RVALUE_INT32) return false;
    const GMLReal height = RValue_toReal(room);
    if (!(height >= 1.0) || !isfinite(height)) return false;

    if (set) {
        const GMLReal epsilon = ctx->runner->mathEpsilon;
        if (!(epsilon >= 0.0) || !isfinite(epsilon) || !isfinite(inst->y)) return false;
        const GMLReal depth = -inst->y / height;
        const bool write = !(GMLReal_fabs(inst->depth - depth) <= epsilon);
        update->result = RValue_makeInt32((int32_t)write);
        update->result.gmlStackType = GML_TYPE_VARIABLE;
        update->depth = depth;
        update->writeDepth = write;
    } else {
        const RValue adjustment = args[0];
        if (adjustment.type != RVALUE_REAL && adjustment.type != RVALUE_INT32) return false;
        const GMLReal offset = RValue_toReal(adjustment);
        const GMLReal depth = inst->depth - offset / height;
        if (!isfinite(offset) || !isfinite(depth)) return false;
        update->result = scriptFallthroughReturnValue(ctx);
        update->depth = depth;
        update->writeDepth = true;
    }
    unsigned* calls = set ? &setDepthCalls : &modDepthCalls;
    ++*calls;
#ifndef PLATFORM_VITA
    if (*calls == 100) logInfo("HLD: exercised 100 numeric calls to %s\n", code->name);
#endif
    return true;
}

#define HLD_COMPARE_RESTING_SCENERY
typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t caseStore, height, maxHeight, hideTime;
} RestingScenery;

static RestingScenery restingScenery;

#if defined(HLD_COMPARE_RESTING_SCENERY) || defined(HLD_VERIFY_RESTING_SCENERY)
static unsigned restingSceneryCalls;
#endif

#ifdef HLD_VERIFY_RESTING_SCENERY
static const CallFrame* restingSceneryCheck;
static RValue restingSceneryExpected;
#endif

#ifdef HLD_COMPARE_RESTING_SCENERY
static bool useRestingScenery(int frame) {
    extern bool hldRefinedRuntime;
    (void)frame;
    return hldRefinedRuntime;
}

void hldReportRestingScenery(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita resting scenery frame=%d enabled=%d calls=%u\n",
            frame, (int)useRestingScenery(frame - 1), restingSceneryCalls);
    restingSceneryCalls = 0;
}
#endif

static void bindRestingScenery(VMContext* ctx) {
    if (stableCases.context != ctx || pureGuards.context != ctx || pureGuards.pause == nullptr) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_RecessingScenery_Step", 3068, 0x5fa3a06du);
    if (code == nullptr || code->localsCount != 12 || code->argumentsCount != 0) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const uint32_t caseCall = resolveFuncOperand(bytes + 0x80);
    const uint32_t pauseCall = resolveFuncOperand(bytes + 0x90);
    if (caseCall >= ctx->funcCallCacheCount || pauseCall >= ctx->funcCallCacheCount ||
        ctx->funcCallCache[caseCall].builtin != nullptr || ctx->funcCallCache[pauseCall].builtin != nullptr ||
        ctx->funcCallCache[caseCall].scriptCodeIndex != stableCases.code - ctx->dataWin->code.entries ||
        ctx->funcCallCache[pauseCall].scriptCodeIndex != pureGuards.pause - ctx->dataWin->code.entries) return;
    static const uint16_t offsets[] = {0x88, 0xa4, 0x108, 0xe0};
    static const char* const names[] = {"caseCheckStore", "height", "maxHeight", "hideT"};
    int32_t ids[4];
    repeat(4, i) {
        const uint32_t operand = resolveVarOperand(bytes + offsets[i]);
        const Variable* variable = resolveVarDef(ctx, operand);
        if (((operand >> 24) & 0xf8) != VARTYPE_NORMAL ||
            variable->varID < 0 || strcmp(variable->name, names[i]) != 0) return;
        ids[i] = variable->varID;
    }
    if (pureGuards.roomSelect == ids[0]) return;
    restingScenery = (RestingScenery){ctx, code, ids[0], ids[1], ids[2], ids[3]};
    logInfo("HLD: bound the resting scenery update\n");
}

static bool sceneryAtRest(VMContext* ctx, Instance* inst, RValue condition, GMLReal number, GMLReal epsilon) {
    bool paused;
    if (!pureGuardPause(ctx, epsilon, &paused)) return false;
    if (paused) return true;

    GMLReal height;
    if (!caseNumber(Instance_getSelfVar(inst, restingScenery.height), &height)) return false;
    if (GMLReal_fabs(height) <= epsilon && GMLReal_fabs(number) <= epsilon && inst->maskIndex == 11) {
        GMLReal hiddenTime;
        if (!caseNumber(Instance_getSelfVar(inst, restingScenery.hideTime), &hiddenTime)) return false;
        if (hiddenTime - 50.0 > epsilon) return true;
    }
    if (RValue_toBool(condition)) {
        GMLReal limit;
        return caseNumber(Instance_getSelfVar(inst, restingScenery.maxHeight), &limit) &&
            GMLReal_fabs(height) <= 16777216.0 && GMLReal_fabs(limit) <= 16777216.0 &&
            GMLReal_fabs(height - limit) <= epsilon &&
            GMLReal_fabs((GMLReal)inst->maskIndex - (GMLReal)inst->spriteIndex) <= epsilon;
    }
    return false;
}

static bool finishRestingScenery(VMContext* ctx, const CodeEntry* code) {
    if (code != restingScenery.code || ctx != restingScenery.context ||
        ctx->runner == nullptr || ctx->stack.top <= 0) return false;
#ifdef HLD_COMPARE_RESTING_SCENERY
    if (!useRestingScenery(ctx->runner->frameCount)) return false;
#endif
    Instance* inst = ctx->currentCallFrame->self;
    if (inst == nullptr || inst == ctx->globalScopeInstance || inst->objectIndex == STRUCT_OBJECT_INDEX) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    const RValue condition = ctx->stack.slots[ctx->stack.top - 1];
    GMLReal number;
    if (!(epsilon >= 0.0 && epsilon < 0.5) || !caseNumber(condition, &number) ||
        !sceneryAtRest(ctx, inst, condition, number, epsilon)) return false;

#ifdef HLD_VERIFY_RESTING_SCENERY
    require(restingSceneryCheck == nullptr);
    restingSceneryCheck = ctx->currentCallFrame;
    restingSceneryExpected = condition;
    return false;
#else
    Instance_setSelfVar(inst, restingScenery.caseStore, condition);
    --ctx->stack.top;
#ifdef HLD_COMPARE_RESTING_SCENERY
    ++restingSceneryCalls;
#endif
    return true;
#endif
}

#define HLD_COMPARE_LEAF_EVENTS
typedef enum {
    LEAF_NONE,
    LEAF_ENEMY_STEP,
    LEAF_ENEMY_BEGIN,
    LEAF_DECOR_DRAW,
    LEAF_SCENERY_DRAW,
    LEAF_EVENT_COUNT
} LeafEvent;

typedef struct {
    const VMContext* context;
    const CodeEntry *enemyStep, *enemyBegin, *decorDraw, *sceneryDraw;
    int32_t deactivate, hidden, edit, selected, damage, animated, debugTools;
} LeafEvents;

static LeafEvents leafEvents;

#if defined(HLD_COMPARE_LEAF_EVENTS) || defined(HLD_VERIFY_LEAF_EVENTS)
static unsigned leafEventCalls[LEAF_EVENT_COUNT];
#endif

#ifdef HLD_COMPARE_LEAF_EVENTS
static bool useLeafEvents(int frame) {
    extern bool hldConsolidatedRuntime;
    (void)frame;
    return hldConsolidatedRuntime;
}

void hldReportLeafEvents(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita leaf events frame=%d enabled=%d enemy_step=%u enemy_begin=%u decor_draw=%u scenery_draw=%u\n",
            frame, (int)useLeafEvents(frame - 1), leafEventCalls[LEAF_ENEMY_STEP], leafEventCalls[LEAF_ENEMY_BEGIN],
            leafEventCalls[LEAF_DECOR_DRAW], leafEventCalls[LEAF_SCENERY_DRAW]);
    memset(leafEventCalls, 0, sizeof(leafEventCalls));
}
#endif

static int32_t leafField(VMContext* ctx, const CodeEntry* code, uint32_t offset, const char* name) {
    const uint32_t operand = resolveVarOperand(codeBytes(ctx, code) + offset);
    if (((operand >> 24) & 0xf8) != VARTYPE_NORMAL) return -1;
    const Variable* field = resolveVarDef(ctx, operand);
    return field->varID >= 0 && strcmp(field->name, name) == 0 ? field->varID : -1;
}

static bool leafBuiltin(VMContext* ctx, const CodeEntry* code, uint32_t offset, const char* name) {
    const uint32_t call = resolveFuncOperand(codeBytes(ctx, code) + offset);
    return call < ctx->funcCallCacheCount && ctx->funcCallCache[call].scriptCodeIndex < 0 &&
        ctx->funcCallCache[call].builtin != nullptr &&
        ctx->funcCallCache[call].builtin == (void*)VM_findBuiltin(ctx, name);
}

static void bindLeafEvents(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* step = matchHldCode(ctx, "gml_Object_enemy_Step_0", 9764, 0xd21da724u);
    const CodeEntry* begin = matchHldCode(ctx, "gml_Object_enemy_Step_1", 3636, 0xcb135e2bu);
    const CodeEntry* decor = matchHldCode(ctx, "gml_Object_DecorXRayBlock_Draw_0", 72, 0x61de5b2eu);
    const CodeEntry* scenery = matchHldCode(ctx, "gml_Object_Scenery_Draw_0", 512, 0xead1a060u);
    LeafEvents bindings = {.context = ctx};
    if (step != nullptr) {
        const int32_t deactivate = leafField(ctx, step, 0x04, "deactivateInstance");
        const int32_t hidden = leafField(ctx, step, 0x14, "editorHidden");
        if (deactivate >= 0 && hidden >= 0) {
            bindings.enemyStep = step;
            bindings.deactivate = deactivate;
            bindings.hidden = hidden;
        }
    }
    if (bindings.enemyStep != nullptr && begin != nullptr &&
        leafBuiltin(ctx, begin, 0x18, "instance_exists") &&
        leafField(ctx, begin, 0x74, "deactivateInstance") == bindings.deactivate &&
        leafField(ctx, begin, 0x84, "editorHidden") == bindings.hidden) {
        const int32_t edit = leafField(ctx, begin, 0x44, "canEdit");
        const int32_t selected = leafField(ctx, begin, 0x08, "selectedInEditor");
        const int32_t damage = leafField(ctx, begin, 0x6c, "damageThisFrame");
        if (edit >= 0 && selected >= 0 && damage >= 0 &&
            selected != bindings.deactivate && selected != bindings.hidden &&
            damage != bindings.deactivate && damage != bindings.hidden) {
            bindings.enemyBegin = begin;
            bindings.edit = edit;
            bindings.selected = selected;
            bindings.damage = damage;
        }
    }
    if (decor != nullptr && leafBuiltin(ctx, decor, 0x0c, "sprite_exists") &&
        leafBuiltin(ctx, decor, 0x1c, "draw_self")) {
        bindings.decorDraw = decor;
    }
    if (bindings.decorDraw != nullptr && scenery != nullptr &&
        leafBuiltin(ctx, scenery, 0x1f8, "event_inherited")) {
        const int32_t animated = leafField(ctx, scenery, 0x04, "animated");
        const int32_t debugTools = leafField(ctx, scenery, 0x44, "showDebugTools");
        if (animated >= 0 && debugTools >= 0) {
            bindings.sceneryDraw = scenery;
            bindings.animated = animated;
            bindings.debugTools = debugTools;
        }
    }
    leafEvents = bindings;
    logInfo("HLD: bound leaf events enemy_step=%d enemy_begin=%d decor_draw=%d scenery_draw=%d\n",
            bindings.enemyStep != nullptr, bindings.enemyBegin != nullptr,
            bindings.decorDraw != nullptr, bindings.sceneryDraw != nullptr);
}

static inline LeafEvent planLeafEvent(VMContext* ctx, const CodeEntry* code, Instance* inst,
                                      int32_t owner, int32_t type, int32_t subtype, int32_t count) {
    if (code != leafEvents.enemyStep && code != leafEvents.enemyBegin &&
        code != leafEvents.decorDraw && code != leafEvents.sceneryDraw) return LEAF_NONE;
    if (ctx != leafEvents.context || inst == nullptr || inst->objectIndex == STRUCT_OBJECT_INDEX ||
        ctx->runner == nullptr || count != 0) return LEAF_NONE;
#ifdef HLD_COMPARE_LEAF_EVENTS
    if (!useLeafEvents(ctx->runner->frameCount)) return LEAF_NONE;
#endif
    if (type == EVENT_STEP) {
        if (!RValue_toBool(Instance_getSelfVar(inst, leafEvents.deactivate)) &&
            !RValue_toBool(Instance_getSelfVar(inst, leafEvents.hidden))) return LEAF_NONE;
        if (code == leafEvents.enemyStep && subtype == STEP_NORMAL) return LEAF_ENEMY_STEP;
        if (code == leafEvents.enemyBegin && subtype == STEP_BEGIN && ctx->globalScopeInstance != nullptr &&
            !RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, leafEvents.edit))) return LEAF_ENEMY_BEGIN;
        return LEAF_NONE;
    }
    if (type != EVENT_DRAW || subtype != DRAW_NORMAL || inst->spriteIndex < 0 ||
        (uint32_t)inst->spriteIndex >= ctx->dataWin->sprt.count) return LEAF_NONE;
    if (code == leafEvents.decorDraw) return LEAF_DECOR_DRAW;
    if (code != leafEvents.sceneryDraw || ctx->globalScopeInstance == nullptr ||
        ctx->traceEventInherited || owner < 0 || (uint32_t)owner >= ctx->dataWin->objt.count ||
        RValue_toBool(Instance_getSelfVar(inst, leafEvents.animated)) ||
        RValue_toBool(Instance_getSelfVar(ctx->globalScopeInstance, leafEvents.debugTools))) return LEAF_NONE;

    const int32_t parent = ctx->dataWin->objt.objects[owner].parentId;
    if (parent < 0 || (uint32_t)parent >= ctx->dataWin->objt.count) return LEAF_NONE;
    const int32_t slot = EventSlotMap_lookup(&ctx->runner->eventSlotMap, EVENT_DRAW, DRAW_NORMAL);
    if (slot < 0) return LEAF_NONE;
    const int32_t inherited = ResolvedEventTable_lookup(&ctx->runner->eventTable, parent, slot, nullptr);
    return inherited == leafEvents.decorDraw - ctx->dataWin->code.entries ? LEAF_SCENERY_DRAW : LEAF_NONE;
}

#ifndef HLD_VERIFY_LEAF_EVENTS
static void applyLeafEvent(VMContext* ctx, LeafEvent event, Instance* inst, Instance* other, int32_t owner) {
    switch (event) {
        case LEAF_ENEMY_BEGIN:
            Instance_setSelfVar(inst, leafEvents.selected, RValue_makeReal(0.0));
            Instance_setSelfVar(inst, leafEvents.damage, RValue_makeReal(0.0));
            break;
        case LEAF_DECOR_DRAW:
            if (ctx->runner->renderer != nullptr) Renderer_drawSelf(ctx->runner->renderer, inst);
            break;
        case LEAF_SCENERY_DRAW:
            Runner_executeEventFromObject(ctx->runner, inst, other, ctx->dataWin->objt.objects[owner].parentId,
                                          EVENT_DRAW, DRAW_NORMAL);
            break;
        default:
            break;
    }
}
#endif

#define HLD_COMPARE_MOVEMENT_HELPERS
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
    Instance* first = hldLookupInstanceId(runner, from);
    if (first == nullptr || !first->active || first->objectIndex == STRUCT_OBJECT_INDEX) return false;
    Instance* second = hldLookupInstanceId(runner, to);
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
    Instance* instance = hldLookupInstanceId(runner, id);
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


#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
typedef struct {
    const VMContext* context;
    const CodeEntry* bounds;
    const CodeEntry* viewport;
    int32_t boundsIds[4];
} SceneQueries;

typedef struct {
    RValue result;
    GMLReal bounds[4];
    bool writesBounds;
} SceneQueryResult;

typedef struct {
    uint16_t address;
    const char* name;
} SceneBuiltin;

static SceneQueries sceneQueries;
static unsigned sceneBoundsCalls, sceneViewportCalls;

static bool sceneBuiltinCalls(VMContext* ctx, const CodeEntry* entry, const SceneBuiltin* calls, size_t count) {
    if (entry == nullptr) return false;
    const uint8_t* program = codeBytes(ctx, entry);
    for (size_t i = 0; i < count; ++i) {
        const uint32_t function = resolveFuncOperand(program + calls[i].address + 4);
        const BuiltinFunc expected = VM_findBuiltin(ctx, calls[i].name);
        if (function >= ctx->funcCallCacheCount || expected == nullptr ||
            ctx->funcCallCache[function].builtin != (void*)expected ||
            ctx->funcCallCache[function].scriptCodeIndex >= 0) return false;
    }
    return true;
}

static void bindSceneQueries(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    SceneQueries binding = {.context = ctx};
    const CodeEntry* bounds = matchHldCode(ctx, "gml_Script_GetCollisionBounds", 312, 0x08a0b168u);
    static const SceneBuiltin boundsCalls[] = {
        {0x020, "sprite_get_xoffset"}, {0x040, "sprite_get_bbox_left"},
        {0x070, "sprite_get_yoffset"}, {0x090, "sprite_get_bbox_top"},
        {0x0c0, "sprite_get_bbox_right"}, {0x0d0, "sprite_get_bbox_left"},
        {0x104, "sprite_get_bbox_bottom"}, {0x114, "sprite_get_bbox_top"},
    };
    if (sceneBuiltinCalls(ctx, bounds, boundsCalls, sizeof(boundsCalls) / sizeof(*boundsCalls))) {
        static const uint16_t writes[] = {0x05c, 0x0ac, 0x0f0, 0x134};
        static const char* const names[] = {"x1", "y1", "x2", "y2"};
        const uint8_t* program = codeBytes(ctx, bounds);
        binding.bounds = bounds;
        for (unsigned i = 0; i < 4; ++i) {
            const Variable* variable = resolveVarDef(ctx, resolveVarOperand(program + writes[i]));
            if (variable->varID < 0 || strcmp(variable->name, names[i]) != 0) binding.bounds = nullptr;
            for (unsigned j = 0; j < i; ++j)
                if (binding.boundsIds[j] == variable->varID) binding.bounds = nullptr;
            binding.boundsIds[i] = variable->varID;
        }
    }
    const CodeEntry* viewport = matchHldCode(ctx, "gml_Script_onscreen_sprite", 572, 0x9176a1b5u);
    static const SceneBuiltin viewportCalls[] = {
        {0x058, "sprite_get_xoffset"}, {0x098, "sprite_get_yoffset"},
        {0x0d0, "sprite_get_width"}, {0x108, "sprite_get_height"},
    };
    if (sceneBuiltinCalls(ctx, viewport, viewportCalls, sizeof(viewportCalls) / sizeof(*viewportCalls)))
        binding.viewport = viewport;
    sceneQueries = binding;
    logInfo("HLD: bound scene queries bounds=%d viewport=%d\n",
            binding.bounds != nullptr, binding.viewport != nullptr);
}

static bool sceneQueryNumber(const RValue value, GMLReal* number) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *number = RValue_toReal(value);
    return isfinite(*number);
}

// The interpreter stores the multiply before applying the next arithmetic
// opcode. Preserve this rounding boundary when the host can contract FMA.
static inline GMLReal sceneRoundedProduct(GMLReal a, GMLReal b) {
    volatile GMLReal product = a * b;
    return product;
}

static bool planSceneQuery(VMContext* ctx, const CodeEntry* code, Instance* instance,
                           const RValue* args, int32_t argCount, SceneQueryResult* plan) {
    if (code != sceneQueries.bounds && code != sceneQueries.viewport) return false;
    if (sceneQueries.context != ctx || ctx->runner == nullptr) return false;
    const Sprt* sprites = &ctx->dataWin->sprt;
    if (code == sceneQueries.bounds && argCount == 0 && instance != nullptr &&
        instance->objectIndex != STRUCT_OBJECT_INDEX) {
        const int32_t spriteId = instance->spriteIndex;
        if (spriteId < 0 || (uint32_t)spriteId >= sprites->count) return false;
        const Sprite* sprite = &sprites->sprites[spriteId];
        const GMLReal sx = instance->imageXscale, sy = instance->imageYscale;
        const GMLReal x = instance->x - sceneRoundedProduct(sprite->originX, sx);
        const GMLReal y = instance->y - sceneRoundedProduct(sprite->originY, sy);
        const GMLReal left = x + sceneRoundedProduct(sprite->marginLeft, sx);
        const GMLReal top = y + sceneRoundedProduct(sprite->marginTop, sy);
        const GMLReal right = left + sceneRoundedProduct((GMLReal)sprite->marginRight - sprite->marginLeft, sx);
        const GMLReal bottom = top + sceneRoundedProduct((GMLReal)sprite->marginBottom - sprite->marginTop, sy);
        if (!isfinite(left) || !isfinite(top) || !isfinite(right) || !isfinite(bottom)) return false;
        *plan = (SceneQueryResult){scriptFallthroughReturnValue(ctx), {left, top, right, bottom}, true};
        ++sceneBoundsCalls;
        return true;
    }
    if (code != sceneQueries.viewport || args == nullptr || (argCount != 3 && argCount != 4)) return false;
    GMLReal x, y, spriteIndex, scale = 1;
    if (!sceneQueryNumber(args[0], &x) || !sceneQueryNumber(args[1], &y) ||
        !sceneQueryNumber(args[2], &spriteIndex) || !(spriteIndex >= 0 && spriteIndex < sprites->count) ||
        (argCount == 4 && !sceneQueryNumber(args[3], &scale))) return false;
    const Sprite* sprite = &sprites->sprites[(int32_t)spriteIndex];
    const GMLCamera* camera = Runner_getCameraForView(ctx->runner, 0);
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (camera == nullptr || !(epsilon >= 0 && epsilon < 0.5)) return false;
    const GMLReal left = x - sceneRoundedProduct(sprite->originX, scale);
    const GMLReal top = y - sceneRoundedProduct(sprite->originY, scale);
    const GMLReal right = left + sceneRoundedProduct(sprite->width, scale);
    const GMLReal bottom = top + sceneRoundedProduct(sprite->height, scale);
    if (!isfinite(left) || !isfinite(top) || !isfinite(right) || !isfinite(bottom)) return false;
    const GMLReal viewX = camera->viewX, viewY = camera->viewY;
    const GMLReal viewRight = viewX + camera->viewWidth, viewBottom = viewY + camera->viewHeight;
    const bool visible = viewRight - left > epsilon && right - viewX > epsilon &&
        bottom - viewY > epsilon && viewBottom - top > epsilon;
    plan->result = RValue_makeInt32(visible);
    plan->result.gmlStackType = GML_TYPE_VARIABLE;
    plan->writesBounds = false;
    ++sceneViewportCalls;
    return true;
}

void hldReportSceneQueriesAtExit(void) {
    logInfo("HLD: scene queries bounds=%u viewport=%u\n", sceneBoundsCalls, sceneViewportCalls);
}
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
extern bool hldSceneQueries;

void hldReportSceneQueries(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scene queries frame=%d enabled=%d bounds=%u viewport=%u\n",
            frame, (int)hldSceneQueries, sceneBoundsCalls, sceneViewportCalls);
    sceneBoundsCalls = sceneViewportCalls = 0;
}
#else
void hldReportSceneQueries(int frame) { (void)frame; }
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)

typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    BuiltinFunc collision;
    int32_t debugVariable;
} LineScript;

static LineScript lineScript;
static unsigned lineScriptCalls;
extern bool hldNativeLineScript;

void hldReportLineScript(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita line script frame=%d enabled=%d calls=%u\n", frame, (int)hldNativeLineScript, lineScriptCalls);
    lineScriptCalls = 0;
}


static void bindLineScript(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_CollisionLine", 336, 0xae3ffd23u);
    if (code == nullptr) return;
    const uint8_t* program = codeBytes(ctx, code);
    const uint32_t function = resolveFuncOperand(program + 0xb4);
    const BuiltinFunc collision = VM_findBuiltin(ctx, "collision_line");
    if (function >= ctx->funcCallCacheCount || collision == nullptr ||
        ctx->funcCallCache[function].builtin != (void*)collision ||
        ctx->funcCallCache[function].scriptCodeIndex >= 0) return;
    const Variable* debug = resolveVarDef(ctx, resolveVarOperand(program + 0xfc));
    if (debug->varID < 0 || strcmp(debug->name, "showDebugTools") != 0) return;
    lineScript = (LineScript){ctx, code, collision, debug->varID};
    logInfo("HLD: bound direct line script\n");
}

static bool runLineScript(VMContext* ctx, const CodeEntry* code, Instance* self, Instance* other,
                          const RValue* args, int32_t count, RValue* result) {
    if (code != lineScript.code || ctx != lineScript.context || ctx->runner == nullptr ||
        ctx->globalScopeInstance == nullptr || args == nullptr || count != 5) return false;
    const RValue debug = Instance_getSelfVar(ctx->globalScopeInstance, lineScript.debugVariable);
    if ((debug.type != RVALUE_REAL && debug.type != RVALUE_INT32 && debug.type != RVALUE_BOOL) ||
        RValue_toBool(debug)) return false;
    for (int32_t i = 0; i < count; ++i) {
        if (args[i].type != RVALUE_REAL && args[i].type != RVALUE_INT32 && args[i].type != RVALUE_BOOL)
            return false;
    }

    // This builtin resolves self and other and makes no nested script calls.
    CallFrame frame = {.self = self, .other = other};
    CallFrame* saved = ctx->currentCallFrame;
    RValue params[] = {args[0], args[1], args[2], args[3], args[4],
                       RValue_makeInt32(1), RValue_makeInt32(1)};
    ctx->currentCallFrame = &frame;
    const RValue hit = lineScript.collision(ctx, params, 7);
    ctx->currentCallFrame = saved;
    *result = RValue_makeReal(RValue_toBool(hit));
    result->gmlStackType = GML_TYPE_VARIABLE;
    ++lineScriptCalls;
    return true;
}

#endif


typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t background;
    uint32_t draw;
} FloorPatternBinding;

typedef struct {
    int32_t sprite, spriteWidth, spriteHeight;
    int32_t sourceStartX, sourceX, sourceY;
    int32_t width, widthLeft, heightLeft;
    GMLReal rowStartX, drawX, drawY;
} FloorPattern;

typedef struct {
    int32_t sprite;
    float left, top, width, height, x, y;
} FloorFragment;

static FloorPatternBinding floorPatternBinding;
static unsigned floorPatternCalls;
extern bool hldFloorPattern;

void hldReportFloorPattern(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita floor pattern frame=%d enabled=%d calls=%u\n", frame, (int)hldFloorPattern, floorPatternCalls);
    floorPatternCalls = 0;
}


static void bindFloorPattern(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_DrawFloorPattern", 800, 0xe7f88ee6u);
    if (code == nullptr || code->localsCount != 13) return;
    const uint8_t* program = codeBytes(ctx, code);
    const Variable* background = resolveVarDef(ctx, resolveVarOperand(program + 4));
    if (background->varID < 0 || strcmp(background->name, "floorBakedBackground") != 0) return;
    static const struct { uint16_t offset; const char* builtin; } calls[] = {
        {0x008, "sprite_exists"}, {0x050, "sprite_get_width"}, {0x068, "sprite_get_height"},
        {0x148, "min"}, {0x150, "max"}, {0x184, "min"}, {0x18c, "max"}, {0x1dc, "draw_sprite_part"},
    };
    for (size_t i = 0; i < sizeof(calls) / sizeof(*calls); ++i) {
        const uint32_t index = resolveFuncOperand(program + calls[i].offset + 4);
        const BuiltinFunc builtin = VM_findBuiltin(ctx, calls[i].builtin);
        if (index >= ctx->funcCallCacheCount || builtin == nullptr ||
            ctx->funcCallCache[index].builtin != (void*)builtin ||
            ctx->funcCallCache[index].scriptCodeIndex >= 0) return;
    }
    floorPatternBinding = (FloorPatternBinding){
        ctx, code, background->varID, resolveFuncOperand(program + 0x1e0)
    };
    logInfo("HLD: bound the floor pattern layout\n");
}

static bool floorPatternNumber(RValue value, GMLReal* number) {
    if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) return false;
    *number = RValue_toReal(value);
    return isfinite(*number);
}

static bool prepareFloorPattern(VMContext* ctx, const CodeEntry* code,
                               const RValue* args, int32_t argCount, FloorPattern* plan) {
    if (code != floorPatternBinding.code || ctx != floorPatternBinding.context ||
        args == nullptr || argCount < 6 || ctx->runner == nullptr || ctx->runner->renderer == nullptr ||
        ctx->globalScopeInstance == nullptr) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    GMLReal sprite;
    if (!floorPatternNumber(Instance_getSelfVar(ctx->globalScopeInstance, floorPatternBinding.background), &sprite) ||
        !(sprite >= 0 && sprite < ctx->dataWin->sprt.count) || sprite != (int32_t)sprite) return false;
    const Sprite* texture = &ctx->dataWin->sprt.sprites[(int32_t)sprite];
    if (texture->width == 0 || texture->width > 8192 || texture->height == 0 || texture->height > 8192) return false;
    GMLReal values[6];
    for (unsigned i = 0; i < 6; ++i) {
        if (!floorPatternNumber(args[i], &values[i]) || GMLReal_fabs(values[i]) > 131072.0) return false;
        if (i < 4 && (values[i] < 0 || values[i] != (int32_t)values[i])) return false;
    }
    if (values[2] < 1 || values[2] > 4096 || values[3] < 1 || values[3] > 4096) return false;
    const int32_t width = (int32_t)values[2], height = (int32_t)values[3];
    const int32_t spriteWidth = (int32_t)texture->width, spriteHeight = (int32_t)texture->height;
    const int32_t sourceX = (int32_t)values[0] % spriteWidth;
    const int32_t sourceY = (int32_t)values[1] % spriteHeight;
    *plan = (FloorPattern){
        .sprite = (int32_t)sprite, .spriteWidth = spriteWidth, .spriteHeight = spriteHeight,
        .sourceStartX = sourceX, .sourceX = sourceX, .sourceY = sourceY,
        .width = width, .widthLeft = width, .heightLeft = height,
        .rowStartX = values[0], .drawX = values[4], .drawY = values[5],
    };
    return true;
}

static FloorFragment nextFloorFragment(FloorPattern* plan) {
    const int32_t spriteWidthLeft = plan->spriteWidth - plan->sourceX;
    const int32_t spriteHeightLeft = plan->spriteHeight - plan->sourceY;
    const int32_t w = plan->widthLeft < spriteWidthLeft ? plan->widthLeft : spriteWidthLeft;
    const int32_t h = plan->heightLeft < spriteHeightLeft ? plan->heightLeft : spriteHeightLeft;
    const FloorFragment fragment = {plan->sprite, (float)plan->sourceX, (float)plan->sourceY,
                                    (float)w, (float)h, (float)plan->drawX, (float)plan->drawY};
    plan->widthLeft -= w;
    if (plan->widthLeft != 0) {
        plan->drawX += w;
        plan->sourceX = (plan->sourceX + w) % plan->spriteWidth;
    } else {
        plan->drawX = plan->rowStartX;
        plan->sourceX = plan->sourceStartX;
        plan->widthLeft = plan->width;
        plan->drawY += h;
        plan->sourceY = (plan->sourceY + h) % plan->spriteHeight;
        plan->heightLeft -= h;
    }
    return fragment;
}

#define HLD_COMPARE_ZERO_MOVEMENT
#define HLD_MANTLE_SWEEP
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
    int32_t z, locked;
#endif
} ZeroMovement;

typedef struct {
    RValue fields[ZERO_MOVEMENT_FIELD_COUNT];
    float x, y;
#ifdef HLD_MANTLE_SWEEP
    float penultimateX, penultimateY;
    GMLReal horizontalSteps, verticalSteps;
    char blockedAxis;
#endif
} ZeroMovementResult;

static ZeroMovement zeroMovement;

static unsigned terrainSweepStationary, terrainSweepMoved, terrainSweepStopped;
static bool terrainSweepEnabled;

void hldReportTerrainSweep(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita terrain sweep frame=%d enabled=%d calls=%u stationary=%u stopped=%u\n",
            frame, (int)terrainSweepEnabled, terrainSweepMoved + terrainSweepStationary,
            terrainSweepStationary, terrainSweepStopped);
    terrainSweepStationary = terrainSweepMoved = terrainSweepStopped = 0;
}


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
    bound.locked = zeroMovementVariable(ctx, bytes, 0x0444, "lockedToSafePlatform");
    if (bound.locked < 0) return;
    FILE* terrainFlag = fopen("ux0:data/hld-vm/terrain-sweep", "r");
    terrainSweepEnabled = terrainFlag != nullptr;
    if (terrainFlag != nullptr) fclose(terrainFlag);
    logInfo("Vita terrain sweep enabled=%d\n", (int)terrainSweepEnabled);
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


typedef struct {
    float x, y, lastProbeX, lastProbeY;
    char blockedAxis;
} MantleTerrainPath;

bool hldMantleSolveTerrain(Runner* runner, Instance* inst, float endX, float endY, bool needsSupport,
                           MantleTerrainPath* path);

static const char* mantleMovementResult(const ZeroMovementResult* result) {
    if (result->blockedAxis == 'h') return "h";
    if (result->blockedAxis == 'v') return "v";
    return "none";
}

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
    if (terrainSweepEnabled && !RValue_toBool(result->fields[ZERO_SLOPES])) {
        const RValue z = Instance_getSelfVar(inst, zeroMovement.z);
        if (zeroMovementNumber(z) && RValue_toReal(z) == 0) {
            // IsType follows the parent chain. A locked descendant may
            // reject movement even with a valid point on the platform.
            if (needsSupport && RValue_toBool(Instance_getSelfVar(inst, zeroMovement.locked))) return false;
            MantleTerrainPath path;
            if (hldMantleSolveTerrain(ctx->runner, inst, endX, endY, needsSupport, &path)) {
                result->x = path.x;
                result->y = path.y;
                result->penultimateX = path.lastProbeX;
                result->penultimateY = path.lastProbeY;
                result->horizontalSteps = h;
                result->verticalSteps = v;
                result->blockedAxis = path.blockedAxis;
                ++terrainSweepMoved;
                terrainSweepStopped += path.blockedAxis != 0;
                return true;
            }
            return false;
        }
    }
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
    const bool stationary = h == 0.0 && v == 0.0;
    if (stationary && !terrainSweepEnabled) return false;
    if (!stationary) COUNT_SWEEP(SWEEP_MOVING);
    result->blockedAxis = 0;
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
    if (stationary) {
        const float x = inst->x, y = inst->y;
        const float roundedX = GMLReal_bankersRound(x), roundedY = GMLReal_bankersRound(y);
        result->x = roundedX == x ? x : roundedX;
        result->y = roundedY == y ? y : roundedY;
        result->penultimateX = x;
        result->penultimateY = y;
        result->horizontalSteps = h;
        result->verticalSteps = v;
        ++terrainSweepStationary;
        return true;
    }
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
    require(strcmp(result.string, mantleMovementResult(expected)) == 0);
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

VMContext* VM_create(DataWin* dataWin) {
#ifdef PLATFORM_PS2
    // Place VMContext in scratchpad RAM
    requireMessage(16384 >= sizeof(VMContext), "VMContext exceeds PS2 scratchpad size (16 KB)");
    VMContext* ctx = (VMContext*) 0x70000000;
    memset(ctx, 0, sizeof(VMContext));
#else
    VMContext* ctx = (VMContext *)safeCalloc(1, sizeof(VMContext));
#endif
    ctx->dataWin = dataWin;
    ctx->stack.top = 0;

    ctx->profiler = nullptr; // lazily allocated by Profiler_setEnabled(&ctx->profiler, true)

    if (IS_WAD14_OR_BELOW(ctx)) {
        rewriteBytecode14To16(ctx);
    }

    VMBuiltins_checkIfBuiltinVarTableIsSorted();

    // Pre-resolve built-in variable IDs (replaces runtime strcmp chains with O(1) switch dispatch)
    repeat(dataWin->vari.variableCount, i) {
        Variable* var = &dataWin->vari.variables[i];
        // varID == -6 is the BC16 built-in sentinel.
        // In BC17, argument variables have instanceType == -6 (Builtin) with varID >= 0, so we also check instanceType.
        if (IS_WAD15_OR_HIGHER(ctx) && (var->varID == VARIABLE_BUILTIN || var->instanceType == -6)) {
            var->builtinVarId = VMBuiltins_resolveBuiltinVarId(var->name);
        } else if (IS_WAD14_OR_BELOW(ctx)) {
            // BC13/14 has no -6 sentinel in the file. Detect builtins by name and switch the VARI entry to use the BC16 sentinel so downstream dispatch paths treat it as a builtin.
            int16_t builtinId = VMBuiltins_resolveBuiltinVarId(var->name);
            if (builtinId != BUILTIN_VAR_UNKNOWN) {
                var->varID = -6;
                var->builtinVarId = builtinId;
            } else {
                var->builtinVarId = BUILTIN_VAR_UNKNOWN;
            }
        } else {
            var->builtinVarId = BUILTIN_VAR_UNKNOWN;
        }
    }

    // Build reference lookup maps (file buffer stays read-only)
    patchReferenceOperands(ctx);

    // Scan VARI entries to find max varID for global scope
    // Built-in variables have varID == -6 (sentinel), skip those
    // BC13/BC14: no eager scan, the sparse globalVarsSlotMap grows on first touch via resolveGlobalSlot.
    uint32_t maxGlobalVarID = 0;
    if (!IS_WAD14_OR_BELOW(ctx)) {
        forEach(Variable, v, dataWin->vari.variables, dataWin->vari.variableCount) {
            if (0 > v->varID) continue;
            // In BC17 any varID can be used as a global variable
            if (IS_WAD17_OR_HIGHER(ctx) || v->instanceType == INSTANCE_GLOBAL) {
                if ((uint32_t) v->varID + 1 > maxGlobalVarID) maxGlobalVarID = (uint32_t) v->varID + 1;
            }
        }
    }

    ctx->currentCallFrame = nullptr;

    // V17+ static initialization tracking
    if (dataWin->gen8.wadVersion >= 17) {
        ctx->staticInitialized = (bool *)safeCalloc(dataWin->code.count, sizeof(bool));
        ctx->staticStructs = (Instance **)safeCalloc(dataWin->code.count, sizeof(Instance*));
    } else {
        ctx->staticInitialized = nullptr;
        ctx->staticStructs = nullptr;
    }
    ctx->currentArrayOwner = nullptr;
    ctx->savearefBalance = 0;

    // Find the varID for "creator" self variable (used by instance_create)
    ctx->creatorVarID = -1;
    forEach(Variable, cv, dataWin->vari.variables, dataWin->vari.variableCount) {
        if (cv->instanceType == INSTANCE_SELF && cv->varID >= 0 && strcmp(cv->name, "creator") == 0) {
            ctx->creatorVarID = cv->varID;
            break;
        }
    }

    // Build selfVarNameMap: varName -> varID for self/instance-scoped variables.
    ctx->varNameMap = nullptr;
    int32_t maxSelfVarID = 0;
    forEach(Variable, variable, dataWin->vari.variables, dataWin->vari.variableCount) {
        if (variable->varID >= 0) {
            ptrdiff_t existing = shgeti(ctx->varNameMap, (char*) variable->name);
            if (0 > existing) {
                shput(ctx->varNameMap, (char*) safeStrdup(variable->name), variable->varID);
            }
            if (variable->varID > maxSelfVarID)
                maxSelfVarID = variable->varID;
        }
    }
    ctx->nextDynamicVarID = maxSelfVarID + 1;

    // Build funcName -> codeIndex hash map from SCPT chunk
    ctx->codeIndexByName = nullptr;
    forEach(Script, s, dataWin->scpt.scripts, dataWin->scpt.count) {
        if (s->name != nullptr && s->codeId >= 0) {
            if (dataWin->code.count > (uint32_t) s->codeId) {
                const char* codeName = dataWin->code.entries[s->codeId].name;
                // Map the full code entry name (e.g. "gml_Script_SCR_GAMESTART")
                shput(ctx->codeIndexByName, (char*) codeName, s->codeId);
                // Also map the bare script name (e.g. "SCR_GAMESTART")
                // since the FUNC chunk references use bare names in CALL instructions
                shput(ctx->codeIndexByName, (char*) s->name, s->codeId);
            }
        }
    }

    // Also map code entry names directly for non-script code (object events, room creation codes, etc.)
    {
    repeat(dataWin->code.count, i) {
        const char* codeName = dataWin->code.entries[i].name;
        ptrdiff_t existing = shgeti(ctx->codeIndexByName, (char*) codeName);
        if (0 > existing) {
            shput(ctx->codeIndexByName, (char*) codeName, (int32_t) i);
        }
    }
    }

    // Build codeName -> CodeLocals* hash map
    ctx->codeLocalsMap = nullptr;
    {
    repeat(dataWin->func.codeLocalsCount, i) {
        CodeLocals* cl = &dataWin->func.codeLocals[i];
        shput(ctx->codeLocalsMap, safeStrdup(cl->name), cl);
        // In WAD 17+, CodeLocals uses "gml_GlobalScript_" prefix but callable CODE entries use "gml_Script_", so we'll map the "gml_Script_" variant too
        if (dataWin->gen8.wadVersion >= 17) {
            if (strncmp(cl->name, "gml_GlobalScript_", 17) == 0) {
                char scriptName[512];
                snprintf(scriptName, sizeof(scriptName), "gml_Script_%s", cl->name + 17);
                shput(ctx->codeLocalsMap, safeStrdup(scriptName), cl);
            }
        }
    }
    }

    // BC13/BC14/BC17+: build per-CodeLocals varID -> slot hmap so resolveLocalSlot is O(1)
    // BC17+ NEEDS "code.count" size entries because YoYo Games in their infinite wisdom thought "what if... we just didn't include some local variables in the localVars map? heck, sometimes we can just NOT include any CodeLocals!"... fun!
    // BC13/BC14 NEEDS the per-code map because BC<=14 has no CodeLocals chunk at all, so slots are allocated on first reference.
    ctx->codeLocalsSlotMaps = nullptr;
    if (dataWin->gen8.wadVersion >= 17 || 14 >= dataWin->gen8.wadVersion) {
        ctx->codeLocalsSlotMaps = (IntIntHashMap *)safeCalloc(dataWin->code.count, sizeof(*ctx->codeLocalsSlotMaps));
    }

    // Register built-in functions
    VMBuiltins_registerAll(ctx);

    if (dataWin->gen8.gameID == 742741103 && dataWin->gen8.wadVersion == 15) {
        // August 2016 Steam format-15 archive after resolving references.
        bindStableCases(ctx);
        bindIdleSpawner(ctx);
        bindAsciiParser(ctx);
        bindPureGuards(ctx);
        bindFlameVentGuard(ctx);
        bindDepthHelpers(ctx);
        bindMovementHelpers(ctx);
        bindZeroMovement(ctx);
        ctx->hldSceneryVisibilityCode = matchHldCode(ctx, "gml_Script_Scenery_IsVisible", 192, 0x11fe0f7bu);
        if (ctx->hldSceneryVisibilityCode != nullptr) {
            ctx->hldSceneryStepCode = matchHldCode(ctx, "gml_Object_SCREEN_Step_2", 2880, 0xe641aed1u);
            if (ctx->hldSceneryStepCode != nullptr && ctx->hldSceneryStepCode->localsCount != 19) {
                ctx->hldSceneryStepCode = nullptr;
            }
            if (ctx->hldSceneryStepCode != nullptr) {
                const uint8_t* scenery = codeBytes(ctx, ctx->hldSceneryStepCode);
                ctx->hldSceneryInactiveVarId = resolveVarDef(ctx, resolveVarOperand(scenery + 0x04f8))->varID;
                ctx->hldRoomTimeVarId = resolveVarDef(ctx, resolveVarOperand(scenery + 0x050c))->varID;
                bindSceneryPass(ctx, scenery, codeBytes(ctx, ctx->hldSceneryVisibilityCode));
                bindSceneryDrawLists(ctx, scenery);
                logInfo("HLD: accelerated scenery visibility updates\n");
            }
        }
        ptrdiff_t index = shgeti(ctx->codeIndexByName, "gml_Script_DistortionSetup");
        if (index >= 0) {
            const CodeEntry* code = &dataWin->code.entries[ctx->codeIndexByName[index].value];
            if (code->length == 3124 && code->offset == 0 && code->localsCount == 27) {
                const uint8_t* bytes = dataWin->bytecodeBuffer + (code->bytecodeAbsoluteOffset - dataWin->bytecodeBufferBase);
                uint32_t fingerprint = 2166136261u;
                for (uint32_t offset = 0; offset < code->length; offset += 4) {
                    fingerprint = (fingerprint ^ BinaryUtils_readUint32Aligned(bytes + offset)) * 16777619u;
                }
                // August 2016 Steam format-15 archive after resolving references.
                if (fingerprint == 0xea5c80f6u) {
                    ctx->hldDistortionCode = code;
                    FILE* reference = fopen("ux0:data/hld-vm/distortion-interpreter", "r");
                    useOriginalDistortion = reference != NULL;
                    if (reference != NULL) fclose(reference);
                    logInfo("Vita distortion reference original=%d\n", (int)useOriginalDistortion);
                    logInfo("HLD: accelerated distortion noise generation\n");
                }
            }
        }
#ifdef PLATFORM_VITA
        FILE* nativeComposite = fopen("ux0:data/hld-vm/native-compositor", "r");
        if (nativeComposite != nullptr) {
            fclose(nativeComposite);
            ptrdiff_t compositor = shgeti(ctx->codeIndexByName, "gml_Object_SCREEN_Draw_72");
            ptrdiff_t refresh = shgeti(ctx->codeIndexByName, "gml_Script_RefreshScreenSurface");
            if (compositor >= 0 && refresh >= 0) {
                const CodeEntry* code = &dataWin->code.entries[ctx->codeIndexByName[compositor].value];
                if (code->length == 4516 && code->offset == 0) {
                    uint8_t* bytes = dataWin->bytecodeBuffer + (code->bytecodeAbsoluteOffset - dataWin->bytecodeBufferBase);
                    uint32_t fingerprint = 2166136261u;
                    for (uint32_t offset = 0; offset < code->length; offset += 4) {
                        fingerprint = (fingerprint ^ BinaryUtils_readUint32Aligned(bytes + offset)) * 16777619u;
                    }
                    if (fingerprint == 0x647a6344u) {
                        // The scene and the effects arrive here on a 480×270
                        // pixel grid. Move nearest-neighbor upscaling to the final display draw.
                        BinaryUtils_writeUint32(bytes + 0x1c, 0x840f01e0u);
                        BinaryUtils_writeUint32(bytes + 0x78, 0x840f010eu);
                        ctx->hldScreenRefreshCode = &dataWin->code.entries[ctx->codeIndexByName[refresh].value];
                        logInfo("HLD: compositing on the original 480x270 pixel grid\n");
                    }
                }
            }
        }
#endif
    }

    // Pre-resolve all FUNC entries to cached builtin pointers or script code indices.
    // This eliminates per-call string hash lookups in handleCall.
    ctx->funcCallCacheCount = dataWin->func.functionCount;
    ctx->funcCallCache = (FuncCallCache *)safeMalloc(dataWin->func.functionCount * sizeof(FuncCallCache));
    {
    repeat(dataWin->func.functionCount, i) {
        const char* name = dataWin->func.functions[i].name;
        BuiltinFunc builtin = VM_findBuiltin(ctx, name);
        ptrdiff_t mapIdx = shgeti(ctx->codeIndexByName, (char*)name);
        // GameMaker Studio 1 scripts can have names that became builtins in later releases.
        if (mapIdx >= 0 && dataWin->gen8.wadVersion <= 15) builtin = nullptr;
        ctx->funcCallCache[i].builtin = (void*)builtin;
        ctx->funcCallCache[i].scriptCodeIndex =
            builtin == nullptr && mapIdx >= 0 ? ctx->codeIndexByName[mapIdx].value : -1;
#ifdef PLATFORM_VITA
        if (dataWin->gen8.gameID == 742741103 && dataWin->gen8.wadVersion == 15
            && dataWin->shdr.count > 7 && dataWin->shdr.shaders[7].name != nullptr
            && strcmp(dataWin->shdr.shaders[7].name, "shd_DistortScreen") == 0
            && ctx->funcCallCache[i].scriptCodeIndex >= 0 && strcmp(name, "DistortionSetup") == 0) {
            ctx->hldDistortionCallPlusOne = i + 1;
        }
#endif
    }
    }

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    bindEnemyShadow(ctx);
    bindSceneQueries(ctx);
    bindRestingScenery(ctx);
    bindEventlessCase(ctx);
    bindLeafEvents(ctx);
    bindIdleScenery(ctx);
    packHldLiteralInstructions(ctx);
    packHldVariableInstructions(ctx);
    packHldArguments(ctx);
    packPureGuards(ctx);
    packCopyBlocks(ctx);
    bindIdleSpawnerCopies(ctx);
    packSwitchChains(ctx);
    packHldMathInstructions(ctx);
    bindPhaseMineTrigger(ctx);
    bindFloorPattern(ctx);
    bindPaletteTail(ctx);
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    bindLineScript(ctx);
#endif
    bindRegionCase(ctx);
    if (hldReducedVMWork) packScalarReadConversions(ctx);
    installEnemyShadow(ctx);
#endif
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    extern bool hldInstanceSlotReads;
    if (hldInstanceSlotReads) packInstanceSlotReads(ctx);
#endif
    logInfo("VM: Initialized with %u functions mapped\n", (uint32_t) shlen(ctx->codeIndexByName));

    return ctx;
}

void VM_reset(VMContext* ctx) {
    // All of these are things that are only "active" inside a script
    // If VM_reset is called while a script is executing, then we did something terribly wrong
    requireMessageFormatted(__FILE__, __LINE__, ctx->currentCallFrame == nullptr, "You can't call VM_reset while there's an active CallFrame!");
    requireMessageFormatted(__FILE__, __LINE__, ctx->stack.top == 0, "You can't call VM_reset while there are elements on the stack!");
    requireMessageFormatted(__FILE__, __LINE__, ctx->envStack == nullptr, "You can't call VM_reset while there's an active EnvironmentFrame!");

    // Reset execution state
    ctx->actionRelativeFlag = false;

    if (ctx->dataWin->gen8.wadVersion >= 17) {
        free(ctx->staticInitialized);
        free(ctx->staticStructs);
        ctx->staticInitialized = (bool *)safeCalloc(ctx->dataWin->code.count, sizeof(bool));
        ctx->staticStructs = (Instance **)safeCalloc(ctx->dataWin->code.count, sizeof(Instance*));
    }

    // Create the instance used for "self" in GLOB scripts
    Instance_free(ctx->globalScopeInstance);
    ctx->globalScopeInstance = Instance_create(0, STRUCT_OBJECT_INDEX, 0, 0);

#ifdef ENABLE_WAD17
    if (ctx->parkedException != nullptr) {
        free(ctx->parkedException->message);
        free(ctx->parkedException);
    }
    if (ctx->exception != nullptr) {
        free(ctx->exception->message);
        free(ctx->exception);
    }
#endif

    logInfo("VM: Reset complete\n");
}

static CodeLocals* resolveCodeLocals(VMContext* ctx, const char* codeName) {
    return shget(ctx->codeLocalsMap, (char*) codeName);
}

RValue VM_executeCode(VMContext* ctx, int32_t codeIndex, Instance* selfInstance, Instance* otherInstance, int32_t eventObjectIndex, int32_t eventType, int32_t eventSubtype, RValue* args, int32_t argCount) {
    require(codeIndex >= 0 && ctx->dataWin->code.count > (uint32_t) codeIndex);
    CodeEntry* code = &ctx->dataWin->code.entries[codeIndex];

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    RValue stableResult;
    const bool stable = code == stableCases.code && ctx == stableCases.context && argCount == 0 &&
        tryStableCase(ctx, selfInstance, &stableResult);
    if (stable) {
        COUNT_STABLE_CASE();
#ifndef HLD_VERIFY_STABLE_CASES
        Instance_setSelfVar(selfInstance, stableCases.usesCase, RValue_makeReal(1.0));
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return stableResult;
#endif
    }
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (code == idleSpawner.code && ctx == idleSpawner.context && argCount == 0 &&
        tryIdleSpawner(ctx, selfInstance)) {
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
#endif

#ifdef HLD_ASCII_PARSER_AVAILABLE
    AsciiParse parsed;
    const bool parsedAscii = code == asciiParser.code && ctx == asciiParser.context &&
        argCount == 1 && args != nullptr && asciiParser.enabled &&
        scanAsciiToken(ctx, selfInstance, args[0], &parsed);
    if (parsedAscii) {
        noteAsciiParse();
#ifndef HLD_VERIFY_ASCII_PARSER
        char* token = (char*)safeMalloc(parsed.bytes + 1u);
        memcpy(token, parsed.token, parsed.bytes);
        token[parsed.bytes] = '\0';
        if (parsed.markIndented) Instance_setSelfVar(selfInstance, asciiParser.indent, RValue_makeReal(1.0));
        Instance_setSelfVar(selfInstance, asciiParser.cursor, parsed.cursor);
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        RValue result = RValue_makeOwnedString(token);
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
#endif
    }
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    DepthUpdate depthUpdate;
    const bool numericDepth = tryDepthHelper(ctx, code, selfInstance, args, argCount, &depthUpdate);
#ifdef HLD_VERIFY_DEPTH_HELPERS
    GMLReal expectedDepth = 0.0;
    bool expectedSortDirty = false;
    if (numericDepth) {
        const bool changed = depthUpdate.writeDepth && depthUpdate.depth != selfInstance->depth;
        expectedDepth = changed ? depthUpdate.depth : selfInstance->depth;
        expectedSortDirty = ctx->runner->drawableListSortDirty || changed;
    }
#else
    if (numericDepth) {
        if (depthUpdate.writeDepth) {
            VMBuiltins_setVariable(ctx, selfInstance, BUILTIN_VAR_DEPTH, "depth", RValue_makeReal(depthUpdate.depth), -1);
        }
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return depthUpdate.result;
    }
#endif
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    const LeafEvent leaf = planLeafEvent(ctx, code, selfInstance, eventObjectIndex, eventType, eventSubtype, argCount);
#ifndef HLD_VERIFY_LEAF_EVENTS
    if (leaf != LEAF_NONE) {
        applyLeafEvent(ctx, leaf, selfInstance, otherInstance, eventObjectIndex);
#ifdef HLD_COMPARE_LEAF_EVENTS
        ++leafEventCalls[leaf];
#endif
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
#endif
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    RValue movementResult;
    const MovementHelper movementHelper = tryMovementHelper(ctx, code, args, argCount, &movementResult);
#ifndef HLD_VERIFY_MOVEMENT_HELPERS
    if (movementHelper != MOVEMENT_HELPER_NONE) {
#ifdef HLD_COMPARE_MOVEMENT_HELPERS
        ++movementHelperCalls[movementHelper];
#endif
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return movementResult;
    }
#endif
#endif


#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    SceneQueryResult sceneQuery;
    const bool nativeQuery = hldSceneQueries && planSceneQuery(ctx, code, selfInstance, args, argCount, &sceneQuery);

    if (nativeQuery) {
        if (sceneQuery.writesBounds) {
            for (unsigned i = 0; i < 4; ++i) {
                RValue value = RValue_makeReal(sceneQuery.bounds[i]);
                value.gmlStackType = GML_TYPE_VARIABLE;
                Instance_setSelfVar(selfInstance, sceneQueries.boundsIds[i], value);
            }
        }
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return sceneQuery.result;
    }
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    RValue lineResult;
    const bool directLine = hldNativeLineScript && runLineScript(ctx, code, selfInstance, otherInstance, args, argCount, &lineResult);
    if (directLine) {
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return lineResult;
    }
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    FloorPattern plan;
    if (hldFloorPattern && prepareFloorPattern(ctx, code, args, argCount, &plan)) {
        Renderer* renderer = ctx->runner->renderer;
        while (plan.heightLeft > 0) {
            const FloorFragment tile = nextFloorFragment(&plan);
            Renderer_drawSpritePart(renderer, tile.sprite, 0, tile.left, tile.top,
                                    tile.width, tile.height, tile.x, tile.y);
        }
        ++floorPatternCalls;
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    GMLReal mineEntryTimer = 0;
    bool mineEntryPaused = true;
    const bool useMineEntry = hldMineEntry && code == dormantMineEntry && pureGuards.context == ctx &&
        ctx->runner != nullptr && eventType == EVENT_STEP && eventSubtype == STEP_NORMAL && argCount == 0 &&
        ctx->runner->mathEpsilon >= 0 && ctx->runner->mathEpsilon < 0.5 &&
        pureGuardPause(ctx, ctx->runner->mathEpsilon, &mineEntryPaused) &&
        !mineEntryPaused && planDormantMine(ctx, selfInstance, &mineEntryTimer);
    if (useMineEntry) {
        applyDormantMine(selfInstance, mineEntryTimer);
        ++mineEntryReturns;
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    ZeroMovementResult zeroMoveResult;
    const bool zeroMove = code == zeroMovement.code && ctx == zeroMovement.context &&
        predictZeroMovement(ctx, selfInstance, args, argCount, &zeroMoveResult);
    if (zeroMove) {
#ifndef HLD_VERIFY_ZERO_MOVEMENT
        applyZeroMovement(ctx, selfInstance, &zeroMoveResult);
#ifdef HLD_COMPARE_ZERO_MOVEMENT
        ++zeroMovementCalls;
#endif
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return RValue_makeOwnedString(safeStrdup(mantleMovementResult(&zeroMoveResult)));
#endif
    }
#endif

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)

    const bool skipIdleScenery = hldIdleScenery && code == idleScenery.code && ctx == idleScenery.context &&
        argCount == 0 && sceneryHasNoUpdate(ctx, selfInstance);

    if (skipIdleScenery) {
        ++idleSceneryCalls;
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
#endif

    // Create a CallFrame for this script
    CallFrame callFrame = {0};
    callFrame.parent = ctx->currentCallFrame;
    callFrame.codeIndex = codeIndex;
    callFrame.initialSelf = selfInstance;
    callFrame.initialOther = otherInstance;
    callFrame.self = selfInstance;
    callFrame.other = otherInstance;
    callFrame.eventObjectIndex = eventObjectIndex;
    callFrame.eventType = eventType;
    callFrame.eventSubtype = eventSubtype;
    uint32_t localsCount = code->localsCount;
    if (IS_WAD17_OR_HIGHER(ctx) || IS_WAD14_OR_BELOW(ctx)) {
        callFrame.currentCodeLocalsSlotMap = &ctx->codeLocalsSlotMaps[codeIndex];
        // BC13/BC14/BC17+ (GM:S 2.3+): we can't trust the CODE entry's localsCount field (BC13/BC14 has no CodeLocals chunk, BC17+ sometimes omits entries from it), so we will get our cached map
        // It is NOT the "right" localsCount because it may increase during runtime, but for now, this shall do
        localsCount = IntIntHashMap_count(callFrame.currentCodeLocalsSlotMap);
    }

    RValue inlineLocals[VM_INLINE_LOCAL_VALUES];
    callFrame.localVarsOnHeap = localsCount > VM_INLINE_LOCAL_VALUES;
    if (callFrame.localVarsOnHeap) {
        callFrame.localVars = (RValue*)safeCalloc(localsCount, sizeof(RValue));
    } else {
        callFrame.localVars = inlineLocals;
        memset(inlineLocals, 0, localsCount * sizeof(RValue));
    }
    callFrame.localVarsCount = localsCount;

    // Store arguments in scriptArgs (mirrors GMS 1.4's global argument stack).
    // Callee takes an INDEPENDENT reference for strings (strdup) and arrays (incRef) so
    // the caller's original args remain valid and owner-tracked by the caller.
    RValue inlineArgs[VM_INLINE_FRAME_VALUES];
    RValue* scriptArgs = nullptr;
    if (argCount > 0 && args != nullptr) {
        callFrame.scriptArgsOnHeap = argCount > VM_INLINE_FRAME_VALUES;
        scriptArgs = callFrame.scriptArgsOnHeap
            ? (RValue*)safeMalloc((size_t)argCount * sizeof(RValue)) : inlineArgs;
        repeat(argCount, argIdx) {
            RValue argCopy = RValue_makeIndependent(args[argIdx]);
            scriptArgs[argIdx] = argCopy;
        }
    }
    callFrame.scriptArgs = scriptArgs;
    callFrame.scriptArgsCount = argCount;

    // Store some informations so that we can restore the CallFrame state after the newly created CallFrame is popped
    // The instruction pointer before this CallFrame was created
    uint32_t instructionPointerWhenTheCallFrameWasCreated = ctx->ip;
    uint32_t stackTopWhenTheCallFrameWasCreated = ctx->stack.top;

    ctx->savearefBalance = 0;

    // We don't need to worry about allocating it on the stack because this CallFrame's lifecycle is always active while this script is executing
    ctx->currentCallFrame = &callFrame;

    // Execute the callee
#ifdef ENABLE_VM_GML_PROFILER
    Profiler_enter(ctx->profiler, code->name);
#endif
    RValue result = executeBytecodeInstructions(ctx, code);
#ifdef HLD_VERIFY_DORMANT_MINE
    checkDormantMine(&callFrame, result, ctx);
#endif

#if defined(HLD_VERIFY_MOVEMENT_HELPERS) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (movementHelper != MOVEMENT_HELPER_NONE) {
        require(result.type == movementResult.type && result.gmlStackType == movementResult.gmlStackType &&
                result.ownsReference == movementResult.ownsReference && result.assetRefType == movementResult.assetRefType);
        if (result.type == RVALUE_REAL) require(memcmp(&result.real, &movementResult.real, sizeof(result.real)) == 0);
        else require(result.int32 == movementResult.int32);
        if (++movementHelperCalls[movementHelper] == 100) {
            logInfo("HLD: checked 100 movement helper calls to %s\n", code->name);
        }
    }
#endif
#if defined(HLD_VERIFY_LEAF_EVENTS) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (leaf != LEAF_NONE) {
        require(result.type == RVALUE_REAL && result.real == 0.0 &&
                result.gmlStackType == GML_TYPE_DOUBLE && !result.ownsReference);
        if (leaf == LEAF_ENEMY_BEGIN) {
            const RValue selected = Instance_getSelfVar(selfInstance, leafEvents.selected);
            const RValue damage = Instance_getSelfVar(selfInstance, leafEvents.damage);
            require(selected.type == RVALUE_REAL && selected.real == 0.0 && selected.gmlStackType == GML_TYPE_DOUBLE);
            require(damage.type == RVALUE_REAL && damage.real == 0.0 && damage.gmlStackType == GML_TYPE_DOUBLE);
        }
        if (++leafEventCalls[leaf] == 1000) {
            logInfo("HLD: checked 1,000 calls to leaf event %s\n", code->name);
        }
    }
#endif
#ifdef HLD_VERIFY_RESTING_SCENERY
    if (restingSceneryCheck == &callFrame) {
        restingSceneryCheck = nullptr;
        const RValue expected = scriptFallthroughReturnValue(ctx);
        const RValue stored = Instance_getSelfVar(selfInstance, restingScenery.caseStore);
        const RValue condition = restingSceneryExpected;
        require(result.type == expected.type && result.real == expected.real &&
            result.gmlStackType == expected.gmlStackType && result.ownsReference == expected.ownsReference);
        require(stored.type == condition.type && RValue_toReal(stored) == RValue_toReal(condition) &&
            stored.gmlStackType == condition.gmlStackType && stored.assetRefType == condition.assetRefType);
        if (++restingSceneryCalls == 1000) logInfo("HLD: checked 1,000 resting scenery updates\n");
    }
#endif
#if defined(HLD_VERIFY_DEPTH_HELPERS) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (numericDepth) {
        require(result.type == depthUpdate.result.type && result.gmlStackType == depthUpdate.result.gmlStackType);
        if (result.type == RVALUE_REAL) require(memcmp(&result.real, &depthUpdate.result.real, sizeof(result.real)) == 0);
        else require(result.type == RVALUE_INT32 && result.int32 == depthUpdate.result.int32);
        require(memcmp(&selfInstance->depth, &expectedDepth, sizeof(expectedDepth)) == 0);
        require(ctx->runner->drawableListSortDirty == expectedSortDirty);
    }
#endif
#if defined(HLD_ASCII_PARSER_AVAILABLE) && defined(HLD_VERIFY_ASCII_PARSER)
    if (parsedAscii) {
        require(result.type == RVALUE_STRING && result.string != nullptr && result.gmlStackType == GML_TYPE_VARIABLE);
        require(strlen(result.string) == parsed.bytes && memcmp(result.string, parsed.token, parsed.bytes) == 0);
        const RValue cursor = Instance_getSelfVar(selfInstance, asciiParser.cursor);
        require(cursor.type == parsed.cursor.type && cursor.gmlStackType == parsed.cursor.gmlStackType &&
                RValue_toReal(cursor) == RValue_toReal(parsed.cursor));
        const RValue indent = Instance_getSelfVar(selfInstance, asciiParser.indent);
        require(RValue_toReal(indent) == 1.0);
        if (parsed.markIndented) require(indent.type == RVALUE_REAL && indent.gmlStackType == GML_TYPE_DOUBLE);
    }
#endif
#if defined(HLD_VERIFY_STABLE_CASES) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (stable) {
        require(result.type == stableResult.type && result.int32 == stableResult.int32 &&
                result.gmlStackType == stableResult.gmlStackType && result.ownsReference == stableResult.ownsReference);
        const RValue enabled = Instance_getSelfVar(selfInstance, stableCases.usesCase);
        require(enabled.type == RVALUE_REAL && enabled.real == 1.0 && enabled.gmlStackType == GML_TYPE_DOUBLE);
    }
#endif
#ifdef ENABLE_VM_GML_PROFILER
    Profiler_exit(ctx->profiler);
#endif

    requireMessage(ctx->savearefBalance == 0, "SAVEAREF/RESTOREAREF imbalance at end of VM_executeCode (unpaired SAVEAREF)");

    // Strengthen result BEFORE freeing callee locals/scriptArgs: if result is a weak view into callee state, the upcoming frees would leave a dangling pointer.
    // For owning results, the refCount/string buffer stays valid (the callee transferred one ownership slot to us).
    result = RValue_stealOwnershipOrCopy(result);
#if defined(HLD_VERIFY_ZERO_MOVEMENT) && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (zeroMove) {
#ifdef HLD_MANTLE_SWEEP
        const GMLReal expectedH = zeroMoveResult.horizontalSteps, expectedV = zeroMoveResult.verticalSteps;
#else
        const GMLReal expectedH = 0.0, expectedV = 0.0;
#endif
        require(callFrame.localVars[1].type == RVALUE_REAL && callFrame.localVars[1].real == expectedH);
        require(callFrame.localVars[2].type == RVALUE_REAL && callFrame.localVars[2].real == expectedV);
        verifyZeroMovement(selfInstance, &zeroMoveResult, result);
    }
#endif

    // Now that we have executed the current script, we can free the current CallFrame and "pop" it
    // Free callee locals
    repeat(callFrame.localVarsCount, i) {
        RValue_free(&callFrame.localVars[i]);
    }

    if (callFrame.localVarsOnHeap) free(callFrame.localVars);

    // Free callee script args
    {
        repeat(callFrame.scriptArgsCount, i) {
            RValue_free(&callFrame.scriptArgs[i]);
        }
    }

    if (callFrame.scriptArgsOnHeap) free(callFrame.scriptArgs);

    ctx->currentCallFrame = callFrame.parent;

    // Restore caller frame
    ctx->ip = instructionPointerWhenTheCallFrameWasCreated;

    // Reset all values in the stack (see issue #137)
    // Keep in mind that recent GameMaker versions do seem to emit Pop/Popz when exiting loops (example: when using a repeat + return) but older versions DO need it
    unwindVMStack(ctx, stackTopWhenTheCallFrameWasCreated);

    return result;
}

// ===[ Disassembler ]===

static char gmlTypeChar(uint8_t type) {
    switch (type) {
        case GML_TYPE_DOUBLE:   return 'd';
        case GML_TYPE_FLOAT:    return 'f';
        case GML_TYPE_INT32:    return 'i';
        case GML_TYPE_INT64:    return 'l';
        case GML_TYPE_BOOL:     return 'b';
        case GML_TYPE_VARIABLE: return 'v';
        case GML_TYPE_STRING:   return 's';
        case GML_TYPE_INT16:    return 'e';
        default:                return '?';
    }
}

static const char* varTypeName(uint32_t varRef) {
    uint8_t varType = (varRef >> 24) & 0xF8;
    switch (varType) {
        case VARTYPE_ARRAY:    return "Array";
        case VARTYPE_STACKTOP: return "StackTop";
        case VARTYPE_NORMAL:   return "Normal";
        case VARTYPE_INSTANCE: return "Instance";
        default:               return "Unknown";
    }
}

static const char* disasmScopeName(VMContext* ctx, int32_t instanceType) {
    switch (instanceType) {
        case INSTANCE_SELF:      return "self";
        case INSTANCE_OTHER:     return "other";
        case INSTANCE_ALL:       return "all";
        case INSTANCE_NOONE:     return "noone";
        case INSTANCE_GLOBAL:    return "global";
        case INSTANCE_LOCAL:     return "local";
        case INSTANCE_STACKTOP:  return "stacktop";
        case INSTANCE_BUILTIN:   return "builtin";
        case INSTANCE_STATIC:    return "static";
        default:
            if (instanceType >= 0 && ctx->dataWin->objt.count > (uint32_t) instanceType) {
                return ctx->dataWin->objt.objects[instanceType].name;
            }
            return "unknown";
    }
}

// Formats a variable operand for disassembly: "scope.varName [varType]"
// If scopeOverride is set (e.g. "local", "global"), uses that instead of resolving instrInstType.
// Shows VARI instanceType mismatch annotation when scopeOverride is nullptr and types differ.
static void disasmFormatVar(VMContext* ctx, const uint8_t* extraData, const char* scopeOverride, int32_t instrInstType, char* buf, size_t bufSize) {
    uint32_t varRef = resolveVarOperand(extraData);
    Variable* varDef = resolveVarDef(ctx, varRef);
    const char* vType = varTypeName(varRef);

    // For StackTop and Array variable types, the actual instance type comes from the stack at runtime, not from the instruction operand.
    // Use the VARI entry's instanceType instead, since the instruction's instanceType is meaningless for these access types.
    uint8_t varType = (varRef >> 24) & 0xF8;
    if (varType == VARTYPE_STACKTOP || varType == VARTYPE_ARRAY) {
        const char* scope = scopeOverride != nullptr ? scopeOverride : disasmScopeName(ctx, varDef->instanceType);
        snprintf(buf, bufSize, "%s.%s [%s]", scope, varDef->name, vType);
        return;
    }

    const char* scope = scopeOverride != nullptr ? scopeOverride : disasmScopeName(ctx, instrInstType);

    if (scopeOverride == nullptr && varDef->instanceType != instrInstType) {
        const char* variScope = disasmScopeName(ctx, varDef->instanceType);
        snprintf(buf, bufSize, "%s.%s [%s] (VARI: %s, instr: %s)", scope, varDef->name, vType, variScope, scope);
    } else {
        snprintf(buf, bufSize, "%s.%s [%s]", scope, varDef->name, vType);
    }
}

// Returns stack effect comment for a variable access instruction
static void disasmFormatVarComment(const uint8_t* extraData, bool isPop, char* buf, size_t bufSize) {
    uint32_t varRef = resolveVarOperand(extraData);
    uint8_t varType = (varRef >> 24) & 0xF8;
    if (isPop) {
        switch (varType) {
            case VARTYPE_ARRAY:    snprintf(buf, bufSize, "// pops: [arrayIndex, instanceType, value]"); break;
            case VARTYPE_STACKTOP: snprintf(buf, bufSize, "// pops: [instanceType, value]"); break;
            default:               snprintf(buf, bufSize, "// pops: [value]"); break;
        }
    } else {
        switch (varType) {
            case VARTYPE_ARRAY:    snprintf(buf, bufSize, "// pops: [arrayIndex, instanceType] -> pushes: [value]"); break;
            case VARTYPE_STACKTOP: snprintf(buf, bufSize, "// pops: [instanceType] -> pushes: [value]"); break;
            default:               snprintf(buf, bufSize, "// pushes: [value]"); break;
        }
    }
}

// Formats a single instruction into opcodeStr, operandStr, and commentStr buffers.
// Used by both VM_disassemble and --trace-opcodes.
// bytecodeBase is needed because the disassembler and trace have it from different sources.
static void formatInstruction(VMContext* ctx, const uint8_t* bytecodeBase, uint32_t instrAddr, OpWord opword, const uint8_t* extraData,
                              char* opcodeStr, size_t opcodeSize, char* operandStr, size_t operandSize, char* commentStr, size_t commentSize) {
    DataWin* dw = ctx->dataWin;
    uint8_t opcode = OpWord_opcode(opword);
    uint8_t type1 = OpWord_type1(opword);
    uint8_t type2 = OpWord_type2(opword);
    int16_t instType = OpWord_extra(opword);

    switch (opcode) {
        // Binary arithmetic/logic
        case OP_ADD: case OP_SUB: case OP_MUL: case OP_DIV:
        case OP_REM: case OP_MOD: case OP_AND: case OP_OR:
        case OP_XOR: case OP_SHL: case OP_SHR:
            snprintf(opcodeStr, opcodeSize, "%s.%c.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1), gmlTypeChar(type2));
            snprintf(commentStr, commentSize, "// pops: [a, b] -> pushes: [result]");
            break;

        // Unary
        case OP_NEG:
            snprintf(opcodeStr, opcodeSize, "%s.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1));
            snprintf(commentStr, commentSize, "// pops: [a] -> pushes: [result]");
            break;
        case OP_NOT:
            snprintf(opcodeStr, opcodeSize, "%s.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1));
            if (type1 == GML_TYPE_BOOL) {
                snprintf(commentStr, commentSize, "// pops: [a] -> pushes: [bool] (logical NOT)");
            } else {
                snprintf(commentStr, commentSize, "// pops: [a] -> pushes: [int] (bitwise NOT)");
            }
            break;

        // Type conversion
        case OP_CONV:
            snprintf(opcodeStr, opcodeSize, "%s.%c.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1), gmlTypeChar(type2));
            snprintf(commentStr, commentSize, "// pops: [%c] -> pushes: [%c]", gmlTypeChar(type2), gmlTypeChar(type1));
            break;

        // Comparison
        case OP_CMP:
            snprintf(opcodeStr, opcodeSize, "%s.%c.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1), gmlTypeChar(type2));
            snprintf(operandStr, operandSize, "%s", CmpOp_getCmpOpcodeName((CmpOp) OpWord_comparisonFunction(opword)));
            snprintf(commentStr, commentSize, "// pops: [a, b] -> pushes: [bool]");
            break;

        // Push
        case OP_PUSH: {
            switch (type1) {
                case GML_TYPE_DOUBLE:
                    snprintf(opcodeStr, opcodeSize, "%s.d", Op_getOpcodeName((Op) opcode));
                    snprintf(operandStr, operandSize, "%g", BinaryUtils_readFloat64(extraData));
                    snprintf(commentStr, commentSize, "// pushes: [double]");
                    break;
                case GML_TYPE_FLOAT:
                    snprintf(opcodeStr, opcodeSize, "%s.f", Op_getOpcodeName((Op) opcode));
                    snprintf(operandStr, operandSize, "%g", (double) BinaryUtils_readFloat32(extraData));
                    snprintf(commentStr, commentSize, "// pushes: [float]");
                    break;
                case GML_TYPE_INT32:
                    snprintf(opcodeStr, opcodeSize, "%s.i", Op_getOpcodeName((Op) opcode));
                    snprintf(operandStr, operandSize, "%d", BinaryUtils_readInt32(extraData));
                    snprintf(commentStr, commentSize, "// pushes: [int32]");
                    break;
                case GML_TYPE_INT64:
                    snprintf(opcodeStr, opcodeSize, "%s.l", Op_getOpcodeName((Op) opcode));
                    snprintf(operandStr, operandSize, "%lld", (longlong) BinaryUtils_readInt64(extraData));
                    snprintf(commentStr, commentSize, "// pushes: [int64]");
                    break;
                case GML_TYPE_BOOL:
                    snprintf(opcodeStr, opcodeSize, "%s.b", Op_getOpcodeName((Op) opcode));
                    snprintf(operandStr, operandSize, "%s", BinaryUtils_readInt32(extraData) != 0 ? "true" : "false");
                    snprintf(commentStr, commentSize, "// pushes: [bool]");
                    break;
                case GML_TYPE_STRING: {
                    snprintf(opcodeStr, opcodeSize, "%s.s", Op_getOpcodeName((Op) opcode));
                    int32_t strIdx = BinaryUtils_readInt32(extraData);
                    if (strIdx >= 0 && dw->strg.count > (uint32_t) strIdx) {
                        const char* str = dw->strg.strings[strIdx];
                        if (strlen(str) > 60) {
                            snprintf(operandStr, operandSize, "\"%.57s...\"", str);
                        } else {
                            snprintf(operandStr, operandSize, "\"%s\"", str);
                        }
                    } else {
                        snprintf(operandStr, operandSize, "[string:%d]", strIdx);
                    }
                    snprintf(commentStr, commentSize, "// pushes: [string]");
                    break;
                }
                case GML_TYPE_VARIABLE:
                    snprintf(opcodeStr, opcodeSize, "%s.v", Op_getOpcodeName((Op) opcode));
                    disasmFormatVar(ctx, extraData, nullptr, (int32_t) instType, operandStr, operandSize);
                    disasmFormatVarComment(extraData, false, commentStr, commentSize);
                    break;
                case GML_TYPE_INT16:
                    snprintf(opcodeStr, opcodeSize, "%s.e", Op_getOpcodeName((Op) opcode));
                    snprintf(operandStr, operandSize, "%d", (int32_t) instType);
                    snprintf(commentStr, commentSize, "// pushes: [int16]");
                    break;
                default:
                    snprintf(opcodeStr, opcodeSize, "%s.?", Op_getOpcodeName((Op) opcode));
                    snprintf(operandStr, operandSize, "(unknown type 0x%X)", type1);
                    break;
            }
            break;
        }

        // Scoped pushes
        case OP_PUSH_LOCAL:
            snprintf(opcodeStr, opcodeSize, "%s.v", Op_getOpcodeName((Op) opcode));
            disasmFormatVar(ctx, extraData, "local", (int32_t) instType, operandStr, operandSize);
            disasmFormatVarComment(extraData, false, commentStr, commentSize);
            break;
        case OP_PUSH_GLOBAL:
            snprintf(opcodeStr, opcodeSize, "%s.v", Op_getOpcodeName((Op) opcode));
            disasmFormatVar(ctx, extraData, "global", (int32_t) instType, operandStr, operandSize);
            disasmFormatVarComment(extraData, false, commentStr, commentSize);
            break;
        case OP_PUSH_BUILTIN:
            snprintf(opcodeStr, opcodeSize, "%s.v", Op_getOpcodeName((Op) opcode));
            disasmFormatVar(ctx, extraData, nullptr, (int32_t) instType, operandStr, operandSize);
            disasmFormatVarComment(extraData, false, commentStr, commentSize);
            break;

        // PushI (int16 immediate)
        case OP_PUSH_IMMEDIATE:
            snprintf(opcodeStr, opcodeSize, "%s.e", Op_getOpcodeName((Op) opcode));
            snprintf(operandStr, operandSize, "%d", (int32_t) instType);
            snprintf(commentStr, commentSize, "// pushes: [int16]");
            break;

        // Pop (store to variable)
        case OP_POP:
            snprintf(opcodeStr, opcodeSize, "%s.%c.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1), gmlTypeChar(type2));
            disasmFormatVar(ctx, extraData, nullptr, (int32_t) instType, operandStr, operandSize);
            disasmFormatVarComment(extraData, true, commentStr, commentSize);
            break;

        // Unconditional branch
        case OP_B: {
            snprintf(opcodeStr, opcodeSize, "%s", Op_getOpcodeName((Op) opcode));
            int32_t offset = OpWord_branchOffset(opword);
            uint32_t target = (uint32_t) ((int32_t) instrAddr + offset);
            snprintf(operandStr, operandSize, "L_%04X (offset: %+d)", target, offset);
            break;
        }

        // Conditional branches
        case OP_BT: {
            snprintf(opcodeStr, opcodeSize, "%s", Op_getOpcodeName((Op) opcode));
            int32_t offset = OpWord_branchOffset(opword);
            uint32_t target = (uint32_t) ((int32_t) instrAddr + offset);
            snprintf(operandStr, operandSize, "L_%04X (offset: %+d)", target, offset);
            snprintf(commentStr, commentSize, "// pops: [bool]");
            break;
        }
        case OP_BF: {
            snprintf(opcodeStr, opcodeSize, "%s", Op_getOpcodeName((Op) opcode));
            int32_t offset = OpWord_branchOffset(opword);
            uint32_t target = (uint32_t) ((int32_t) instrAddr + offset);
            snprintf(operandStr, operandSize, "L_%04X (offset: %+d)", target, offset);
            snprintf(commentStr, commentSize, "// pops: [bool]");
            break;
        }

        // With-statement: PushEnv
        case OP_PUSH_ENV: {
            snprintf(opcodeStr, opcodeSize, "%s", Op_getOpcodeName((Op) opcode));
            int32_t offset = OpWord_branchOffset(opword);
            uint32_t target = (uint32_t) ((int32_t) instrAddr + offset);
            // Peek at previous instruction to identify the target object
            const char* targetName = nullptr;
            if (instrAddr >= 4) {
                uint32_t prevInstr = BinaryUtils_readUint32(bytecodeBase + instrAddr - 4);
                OpWord prevOpword = {prevInstr};
                if (OpWord_opcode(prevOpword) == OP_PUSH_IMMEDIATE) {
                    int16_t objIdx = (int16_t) (prevInstr & 0xFFFF);
                    targetName = disasmScopeName(ctx, (int32_t) objIdx);
                }
            }
            if (targetName != nullptr) {
                snprintf(operandStr, operandSize, "%s (target: L_%04X, offset: %+d)", targetName, target, offset);
            } else {
                snprintf(operandStr, operandSize, "(target: L_%04X, offset: %+d)", target, offset);
            }
            snprintf(commentStr, commentSize, "// pops: [target]");
            break;
        }

        // With-statement: PopEnv
        case OP_POP_ENV: {
            snprintf(opcodeStr, opcodeSize, "%s", Op_getOpcodeName((Op) opcode));
            if (opword.value != 0xBBF00000) {
                snprintf(operandStr, operandSize, "[exit]");
            } else {
                int32_t offset = OpWord_branchOffset(opword);
                uint32_t target = (uint32_t) ((int32_t) instrAddr + offset);
                snprintf(operandStr, operandSize, "(target: L_%04X, offset: %+d)", target, offset);
            }
            break;
        }

        // Function call
        case OP_CALL: {
            snprintf(opcodeStr, opcodeSize, "%s.i", Op_getOpcodeName((Op) opcode));
            int32_t argCount = OpWord_extra(opword);
            uint32_t funcIdx = resolveFuncOperand(extraData);
            const char* funcName = (dw->func.functionCount > funcIdx) ? dw->func.functions[funcIdx].name : "???";
            snprintf(operandStr, operandSize, "%s(argCount=%d)", funcName, argCount);
            if (argCount > 0) {
                char argList[128] = "";
                int32_t pos = 0;
                for (int32_t i = 0; 8 > i && argCount > i; i++) {
                    if (i > 0) pos += snprintf(argList + pos, sizeof(argList) - pos, ", ");
                    pos += snprintf(argList + pos, sizeof(argList) - pos, "arg%d", i);
                }
                if (argCount > 8) snprintf(argList + pos, sizeof(argList) - pos, ", ...");
                snprintf(commentStr, commentSize, "// pops: [%s] -> pushes: [result]", argList);
            } else {
                snprintf(commentStr, commentSize, "// pushes: [result]");
            }
            break;
        }

        // Dynamic call through variable/method reference (BC17+)
        case OP_CALLV: {
            int32_t argCount = OpWord_extra(opword);
            snprintf(opcodeStr, opcodeSize, "%s.v", Op_getOpcodeName((Op) opcode));
            snprintf(operandStr, operandSize, "argCount=%d", argCount);
            snprintf(commentStr, commentSize, "// pops: [func, instance, %d args] -> pushes: [result]", argCount);
            break;
        }

        // Duplicate stack items
        case OP_DUP: {
            uint8_t extra = OpWord_extra(opword);
            int32_t count = (int32_t) extra + 1;
            snprintf(opcodeStr, opcodeSize, "%s.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1));
            if (count > 1) {
                snprintf(operandStr, operandSize, "%d", count);
                snprintf(commentStr, commentSize, "// duplicates %d items", count);
            } else {
                snprintf(commentStr, commentSize, "// duplicates top item");
            }
            break;
        }

        // Control flow
        case OP_RET:
            snprintf(opcodeStr, opcodeSize, "%s.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1));
            snprintf(commentStr, commentSize, "// pops: [value] (return)");
            break;
        case OP_EXIT:
            snprintf(opcodeStr, opcodeSize, "%s.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1));
            snprintf(commentStr, commentSize, "// (end of code)");
            break;
        case OP_POPZ:
            snprintf(opcodeStr, opcodeSize, "%s.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1));
            snprintf(commentStr, commentSize, "// pops: [value]");
            break;

        // Break (extended opcodes in V17+)
        case OP_BREAK: {
            int16_t breakType = (int16_t) instType;
            const char* mnemonic;
            switch (breakType) {
                case BREAK_CHKINDEX:    mnemonic = "chkindex"; break;
                case BREAK_PUSHAF:      mnemonic = "pushaf"; break;
                case BREAK_POPAF:       mnemonic = "popaf"; break;
                case BREAK_PUSHAC:      mnemonic = "pushac"; break;
                case BREAK_SETOWNER:    mnemonic = "setowner"; break;
                case BREAK_ISSTATICOK:  mnemonic = "isstaticok"; break;
                case BREAK_SETSTATIC:   mnemonic = "setstatic"; break;
                case BREAK_SAVEAREF:    mnemonic = "savearef"; break;
                case BREAK_RESTOREAREF: mnemonic = "restorearef"; break;
                case BREAK_ISNULLISH:   mnemonic = "isnullish"; break;
                case BREAK_PUSHREF:     mnemonic = "pushref"; break;
                default:                mnemonic = nullptr; break;
            }
            if (mnemonic != nullptr) {
                snprintf(opcodeStr, opcodeSize, "%s.%c", mnemonic, gmlTypeChar(type1));
                if (breakType == BREAK_PUSHREF) {
                    uint32_t operand = BinaryUtils_readUint32Aligned(extraData);
                    uint8_t assetType = (uint8_t) ((operand >> 24) & 0xFF);
                    int32_t index = (int32_t) (operand & 0x00FFFFFF);
                    if (assetType == ASSET_TYPE_SCRIPT) {
                        const char* funcName = (dw->func.functionCount > (uint32_t) index) ? dw->func.functions[index].name : "???";
                        snprintf(operandStr, operandSize, "script %s", funcName);
                    } else {
                        snprintf(operandStr, operandSize, "asset type=%d index=%d", assetType, index);
                    }
                }
            } else {
                snprintf(opcodeStr, opcodeSize, "%s.%c", Op_getOpcodeName((Op) opcode), gmlTypeChar(type1));
                snprintf(operandStr, operandSize, "%d", (int32_t) breakType);
            }
            break;
        }

        default:
            snprintf(opcodeStr, opcodeSize, "%s (0x%02X)", Op_getOpcodeName((Op) opcode), opcode);
            break;
    }
}

void VM_buildCrossReferences(VMContext* ctx) {
    DataWin* dw = ctx->dataWin;
    ctx->crossRefMap = nullptr;

    repeat(dw->code.count, callerIdx) {
        CodeEntry* code = &dw->code.entries[callerIdx];
        const uint8_t* base = dw->bytecodeBuffer + (code->bytecodeAbsoluteOffset - dw->bytecodeBufferBase);
        uint32_t ip = 0;

        while (code->length > ip) {
            uint32_t instr = BinaryUtils_readUint32(base + ip);
            OpWord opword = {instr};
            ip += 4;
            const uint8_t* ed = base + ip;
            ip += OpWord_operandSize(opword);

            if (OpWord_opcode(opword) == OP_CALL) {
                uint32_t funcIdx = resolveFuncOperand(ed);
                if (dw->func.functionCount > funcIdx) {
                    const char* funcName = dw->func.functions[funcIdx].name;
                    ptrdiff_t codeMapIdx = shgeti(ctx->codeIndexByName, (char*) funcName);
                    if (codeMapIdx >= 0) {
                        int32_t targetIdx = ctx->codeIndexByName[codeMapIdx].value;
                        ptrdiff_t mapIdx = hmgeti(ctx->crossRefMap, targetIdx);
                        if (0 > mapIdx) {
                            int32_t* callers = nullptr;
                            arrput(callers, (int32_t) callerIdx);
                            hmput(ctx->crossRefMap, targetIdx, callers);
                        } else {
                            // Deduplicate: don't add the same caller twice
                            int32_t* callers = ctx->crossRefMap[mapIdx].value;
                            bool found = false;
                            for (ptrdiff_t k = 0; arrlen(callers) > k; k++) {
                                if (callers[k] == (int32_t) callerIdx) { found = true; break; }
                            }
                            if (!found) {
                                arrput(ctx->crossRefMap[mapIdx].value, (int32_t) callerIdx);
                            }
                        }
                    }
                }
            }
        }
    }
}

struct VMDisasOpcodeEntry { uint32_t key; bool value; };

void VM_disassemble(VMContext* ctx, int32_t codeIndex) {
    DataWin* dw = ctx->dataWin;
    require(dw->code.count > (uint32_t) codeIndex);
    CodeEntry* code = &dw->code.entries[codeIndex];

    // Header
    logInfo("=== %s (length=%u, locals=%u, args=%u) ===\n", code->name, code->length, code->localsCount, code->argumentsCount);

    // CodeLocals
    CodeLocals* locals = resolveCodeLocals(ctx, code->name);
    if (locals != nullptr && locals->localVarCount > 0) {
        logInfo("Locals:");
        repeat(locals->localVarCount, i) {
            if (i > 0) logInfo(",");
            logInfo(" [%u] %s", locals->locals[i].varID, locals->locals[i].name);
        }
        logInfo("\n");
    }

    // Cross-references
    if (ctx->crossRefMap != nullptr) {
        ptrdiff_t mapIdx = hmgeti(ctx->crossRefMap, codeIndex);
        if (mapIdx >= 0) {
            int32_t* callers = ctx->crossRefMap[mapIdx].value;
            logInfo("Called by:");
            for (ptrdiff_t i = 0; arrlen(callers) > i; i++) {
                if (i > 0) logInfo(",");
                logInfo(" %s", dw->code.entries[callers[i]].name);
            }
            logInfo("\n");
        }
    }

    logInfo("\n");

    const uint8_t* bytecodeBase = dw->bytecodeBuffer + (code->bytecodeAbsoluteOffset - dw->bytecodeBufferBase);
    uint32_t codeLength = code->length;

    // Pass 1: collect branch targets for labels
    struct VMDisasOpcodeEntry *branchTargets = nullptr;
    {
        uint32_t ip = 0;
        while (codeLength > ip) {
            uint32_t instrAddr = ip;
            uint32_t instr = BinaryUtils_readUint32(bytecodeBase + ip);
            OpWord opword = {instr};
            ip += 4;
            ip += OpWord_operandSize(opword);

            uint8_t opcode = OpWord_opcode(opword);
            if (opcode == OP_B || opcode == OP_BT || opcode == OP_BF || opcode == OP_PUSH_ENV) {
                int32_t offset = OpWord_branchOffset(opword);
                uint32_t target = (uint32_t) ((int32_t) instrAddr + offset);
                hmput(branchTargets, target, true);
            }
            if (opcode == OP_POP_ENV) {
                if (opword.value == 0xBBF00000) {
                    int32_t offset = OpWord_branchOffset(opword);
                    uint32_t target = (uint32_t) ((int32_t) instrAddr + offset);
                    hmput(branchTargets, target, true);
                }
            }
        }
    }

    // Pass 2: print instructions
    uint32_t ip = 0;
    int32_t envDepth = 0;

    while (codeLength > ip) {
        uint32_t instrAddr = ip;
        uint32_t instr = BinaryUtils_readUint32(bytecodeBase + ip);
        OpWord opword = {instr};
        ip += 4;
        const uint8_t* extraData = bytecodeBase + ip;
        ip += OpWord_operandSize(opword);

        uint8_t opcode = OpWord_opcode(opword);

        // PopEnv decreases depth before printing
        if (opcode == OP_POP_ENV && envDepth > 0) envDepth--;

        // Print label if this address is a branch target
        if (hmgeti(branchTargets, instrAddr) >= 0) {
            logInfo("  %04X: L_%04X:\n", instrAddr, instrAddr);
        }

        int32_t indent = 2 + envDepth * 4;
        char opcodeStr[32];
        char operandStr[256] = "";
        char commentStr[256] = "";

        formatInstruction(ctx, bytecodeBase, instrAddr, opword, extraData, opcodeStr, sizeof(opcodeStr), operandStr, sizeof(operandStr), commentStr, sizeof(commentStr));

        // Print the formatted line
        if (commentStr[0] != '\0') {
            logInfo("%*s%04X (%6d): [0x%08X] %-16s %-45s %s\n", indent, "", instrAddr, instrAddr, instr, opcodeStr, operandStr, commentStr);
        } else {
            logInfo("%*s%04X (%6d): [0x%08X] %-16s %s\n", indent, "", instrAddr, instrAddr, instr, opcodeStr, operandStr);
        }

        // PushEnv increases depth after printing
        if (opcode == OP_PUSH_ENV) envDepth++;
    }

    hmfree(branchTargets);
    logInfo("\n");
}

#undef VM_registerBuiltin
void VM_registerBuiltin(VMContext* ctx, const char* name, BuiltinFunc func) {
    requireMessageFormatted(__FILE__, __LINE__, shgeti(ctx->builtinMap, name) == -1, "Trying to register an already registered builtin function! Function Name: %s", name);
    shput(ctx->builtinMap, (char*) name, func);
}

BuiltinFunc VM_findBuiltin(VMContext* ctx, const char* name) {
    ptrdiff_t idx = shgeti(ctx->builtinMap, (char*) name);
    if (0 > idx) return nullptr;
    return ctx->builtinMap[idx].value;
}

void hldReleaseLineEnvelopes(const DataWin* data);

void VM_free(VMContext* ctx) {
    if (eventlessCaseContext == ctx) {
        eventlessCaseContext = nullptr;
        eventlessCaseReturns = 0;
    }
    if (sceneryDrawLists.context == ctx) {
        logInfo("HLD: scenery draw-list visits=%u appends=%u\n", sceneryDrawListVisits, sceneryDrawListAppends);
        sceneryDrawLists = (SceneryDrawLists){0};
        sceneryDrawListVisits = sceneryDrawListAppends = 0;
    }
    if (idleScenery.context == ctx) idleScenery = (IdleScenery){0};
    clearEnvSnapshotPool(ctx);
#if defined(HLD_MANTLE_SWEEP) && defined(HLD_VERIFY_ZERO_MOVEMENT)
    reportMantleSweepChecks();
#endif
    if (zeroMovement.context == ctx) zeroMovement = (ZeroMovement){0};
    if (pureGuards.context == ctx) {
        dormantMineEntry = nullptr;
        mineEntryReturns = 0;
    }

    if (floorPatternBinding.context == ctx) {
        floorPatternBinding = (FloorPatternBinding){0};
        floorPatternCalls = 0;
    }

    if (paletteTail.context == ctx) {
        paletteTail = (PaletteTail){0};
        paletteTails = 0;
    }

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (lineScript.context == ctx) {
        lineScript = (LineScript){0};
        lineScriptCalls = 0;
    }
#endif
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (sceneQueries.context == ctx) sceneQueries = (SceneQueries){0};
#endif
    if (movementHelpers.context == ctx) movementHelpers = (MovementHelpers){0};
    if (leafEvents.context == ctx) leafEvents = (LeafEvents){0};
    if (sceneryPass.context == ctx) sceneryPass = (SceneryPass){0};
    if (restingScenery.context == ctx) restingScenery = (RestingScenery){0};
    if (regionCase.context == ctx) regionCase = (RegionCase){0};
    if (enemyShadowContext == ctx) enemyShadowContext = nullptr;
    if (depthHelpers.context == ctx) depthHelpers = (DepthHelpers){0};
    if (flameVentGuard.context == ctx) flameVentGuard = (FlameVentGuard){0};
    if (pureGuards.context == ctx) pureGuards = (PureGuards){0};
    if (asciiParser.context == ctx) asciiParser = (AsciiParser){0};
    hldReleaseLineEnvelopes(ctx->dataWin);
    if (idleSpawner.context == ctx) idleSpawner = (IdleSpawner){0};
    if (copyBlockContext == ctx) releaseCopyBlocks();
    if (stableCases.context == ctx) stableCases = (StableCases){0};
    if (ctx == nullptr) return;

#ifdef ENABLE_SPRITE_DECODE_CACHE
    SpriteDecodeCache_free(ctx->spriteDecodeCache);
#endif

    // Reset mutable runtime state
    VM_reset(ctx);

    // Free profiler (no-op if never enabled)
    Profiler_destroy(ctx->profiler);
    ctx->profiler = nullptr;

#ifdef ENABLE_VM_OPCODE_PROFILER
    free(ctx->opcodeVariantCounts);
    ctx->opcodeVariantCounts = nullptr;
    free(ctx->opcodeRValueTypeCounts);
    ctx->opcodeRValueTypeCounts = nullptr;
#endif

    // Free hash maps
    shfree(ctx->codeIndexByName);
    repeat(shlen(ctx->varNameMap), i) {
        free(ctx->varNameMap[i].key);
    }
    shfree(ctx->varNameMap);
    {
    repeat(shlen(ctx->codeLocalsMap), i) {
        free(ctx->codeLocalsMap[i].key);
    }
    }
    shfree(ctx->codeLocalsMap);

    // Free dedup key strings before freeing the hashmaps
    {
    repeat(shlen(ctx->loggedUnknownFuncs), i) {
        free(ctx->loggedUnknownFuncs[i].key);
    }
    }
    shfree(ctx->loggedUnknownFuncs);
    {
    repeat(shlen(ctx->loggedStubbedFuncs), i) {
        free(ctx->loggedStubbedFuncs[i].key);
    }
    }
    shfree(ctx->loggedStubbedFuncs);
#ifdef ENABLE_VM_TRACING
    shfree(ctx->varReadsToBeTraced);
    shfree(ctx->varWritesToBeTraced);
    shfree(ctx->functionCallsToBeTraced);
    shfree(ctx->alarmsToBeTraced);
    shfree(ctx->instanceLifecyclesToBeTraced);
    shfree(ctx->eventsToBeTraced);
    shfree(ctx->collisionsToBeTraced);
    shfree(ctx->opcodesToBeTraced);
    shfree(ctx->stackToBeTraced);
#endif

    // Free function call cache
    free(ctx->funcCallCache);

    // Free cross-reference map
    if (ctx->crossRefMap != nullptr) {
        for (ptrdiff_t i = 0; hmlen(ctx->crossRefMap) > i; i++) {
            arrfree(ctx->crossRefMap[i].value);
        }
        hmfree(ctx->crossRefMap);
    }

    // Free builtin map
    shfree(ctx->builtinMap);
    ctx->registeredBuiltinFunctions = false;

    // Free V17+ static tracking
    free(ctx->staticInitialized);
    free(ctx->staticStructs);

    // Free per-code varID -> slot maps (BC17+ only; nullptr otherwise).
    if (ctx->codeLocalsSlotMaps != nullptr) {
        repeat(ctx->dataWin->code.count, i) {
            IntIntHashMap_free(&ctx->codeLocalsSlotMaps[i]);
        }
        free(ctx->codeLocalsSlotMaps);
        ctx->codeLocalsSlotMaps = nullptr;
    }

    // Free the global scope instance
    Instance_free(ctx->globalScopeInstance);

#ifndef PLATFORM_PS2
    extern void hldClearArrayPool(void);
    hldClearArrayPool();
    free(ctx);
#endif
}
