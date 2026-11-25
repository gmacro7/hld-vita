"""Run the level-load path of gml_Script_SerializeVar natively.

With global.lvlSaveLoad == 1, SerializeVar finds ",<id>=" in global.lvlObjSerialized, returns the value
after it (converted by the argument's type) and removes "<id>=<value>," from the string. The native path
handles ASCII strings and numeric or string arguments with the asset-name, real and string conversions,
and returns before building a call frame. Everything else runs the original script. The checked variant
computes the native answer, lets the script run, and requires an identical result and string.

Generated code is appended after the original source and the hooks share existing lines, so profile
feedback for the unchanged functions still matches.
"""

from runtime_verification_source import replace_once

SIGNATURE = ("RValue VM_executeCode(VMContext* ctx, int32_t codeIndex, Instance* selfInstance, Instance* otherInstance, "
             "int32_t eventObjectIndex, int32_t eventType, int32_t eventSubtype, RValue* args, int32_t argCount) {")

NATIVE = r'''
typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t serializedId, modeId;
    BuiltinFunc toString, toReal, assetIndex;
    unsigned nativeCalls, fallbacks, checked;
} HldSerialize;
static HldSerialize hldSerialize;

static bool hldSerializeBind(VMContext* ctx) {
    if (hldSerialize.context == ctx) return hldSerialize.code != nullptr;
    hldSerialize = (HldSerialize){.context = ctx, .serializedId = -1, .modeId = -1};
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return false;
    repeat(ctx->dataWin->code.count, i) {
        CodeEntry* entry = &ctx->dataWin->code.entries[i];
        if (entry->present && entry->name != nullptr && strcmp(entry->name, "gml_Script_SerializeVar") == 0) hldSerialize.code = entry;
    }
    forEach(Variable, variable, ctx->dataWin->vari.variables, ctx->dataWin->vari.variableCount) {
        if (variable->instanceType != INSTANCE_GLOBAL) continue;
        if (strcmp(variable->name, "lvlObjSerialized") == 0) hldSerialize.serializedId = variable->varID;
        if (strcmp(variable->name, "lvlSaveLoad") == 0) hldSerialize.modeId = variable->varID;
    }
    hldSerialize.toString = VM_findBuiltin(ctx, "string");
    hldSerialize.toReal = VM_findBuiltin(ctx, "real");
    hldSerialize.assetIndex = VM_findBuiltin(ctx, "asset_get_index");
    if (hldSerialize.code == nullptr || hldSerialize.serializedId < 0 || hldSerialize.modeId < 0 ||
        hldSerialize.toString == nullptr || hldSerialize.toReal == nullptr || hldSerialize.assetIndex == nullptr) {
        hldSerialize.code = nullptr;
        return false;
    }
    logInfo("HLD: native SerializeVar bound\n");
    return true;
}

static bool hldAscii(const char* text) {
    for (const unsigned char* c = (const unsigned char*) text; *c != 0; ++c) if (*c >= 0x80) return false;
    return true;
}

// Computes SerializeVar's load-mode result and the new global string without changing any state.
// Returns false when the original script must run.
static bool hldSerializeCompute(VMContext* ctx, const RValue* args, int32_t argCount, RValue* result, char** updated) {
    *updated = nullptr;
    if (args == nullptr || (argCount != 2 && argCount != 3) || ctx->globalScopeInstance == nullptr) return false;
    const RValue mode = Instance_getSelfVar(ctx->globalScopeInstance, hldSerialize.modeId);
    if (!((mode.type == RVALUE_REAL && mode.real == 1.0) || (mode.type == RVALUE_INT32 && mode.int32 == 1))) return false;
    const RValue serialized = Instance_getSelfVar(ctx->globalScopeInstance, hldSerialize.serializedId);
    if (serialized.type != RVALUE_STRING || serialized.string == nullptr || !hldAscii(serialized.string)) return false;
    const RValue arg = args[0];
    const bool argNumeric = arg.type == RVALUE_REAL || arg.type == RVALUE_INT32 || arg.type == RVALUE_BOOL;
    if (!argNumeric && arg.type != RVALUE_STRING) return false;
    GMLReal assetType = -999999.0;
    if (argCount > 2) {
        if (args[2].type == RVALUE_REAL) assetType = args[2].real;
        else if (args[2].type == RVALUE_INT32) assetType = (GMLReal) args[2].int32;
        else return false;
    }
    const bool asset = assetType == 0.0 || assetType == 1.0 || assetType == 2.0 || assetType == 3.0;

    RValue idValue = hldSerialize.toString(ctx, (RValue*) &args[1], 1);
    if (idValue.type != RVALUE_STRING || idValue.string == nullptr || !hldAscii(idValue.string)) {
        RValue_free(&idValue);
        return false;
    }
    const size_t idLength = strlen(idValue.string);
    char* needle = safeMalloc(idLength + 3);
    needle[0] = ',';
    memcpy(needle + 1, idValue.string, idLength);
    needle[idLength + 1] = '=';
    needle[idLength + 2] = 0;
    RValue_free(&idValue);
    const char* text = serialized.string;
    const char* found = strstr(text, needle);
    free(needle);
    if (found == nullptr) {
        // return arg: a copy of the argument, tagged as a variable.
        *result = RValue_makeIndependent(arg);
        result->gmlStackType = result->type == RVALUE_STRING ? GML_TYPE_STRING : GML_TYPE_VARIABLE;
        return true;
    }
    const size_t length = strlen(text);
    const size_t start = (size_t)(found - text) + idLength + 2;
    size_t end = start;
    while (end < length && text[end] != ',') ++end;
    if (!asset && assetType == -999999.0 && memchr(text + start, '&', end - start) != nullptr) return false;

    char* value = safeMalloc(end - start + 1);
    memcpy(value, text + start, end - start);
    value[end - start] = 0;
    const size_t head = (size_t)(found - text) + 1;
    const size_t tail = end + 1 < length ? length - (end + 1) : 0;
    char* next = safeMalloc(head + tail + 1);
    memcpy(next, text, head);
    memcpy(next + head, text + end + 1, tail);
    next[head + tail] = 0;

    if (asset) {
        RValue name = RValue_makeString(value);
        *result = hldSerialize.assetIndex(ctx, &name, 1);
    } else if (assetType == -999999.0 && argNumeric) {
        RValue number = RValue_makeString(value);
        *result = hldSerialize.toReal(ctx, &number, 1);
    } else if (assetType == -999999.0) {
        *result = RValue_makeOwnedString(safeStrdup(value));
    } else {
        *result = RValue_makeReal(-999999.0);
    }
    free(value);
    *result = RValue_stealOwnershipOrCopy(*result);
    // Scripts hand string results back tagged as strings and every other value as a variable.
    result->gmlStackType = result->type == RVALUE_STRING ? GML_TYPE_STRING : GML_TYPE_VARIABLE;
    *updated = next;
    return true;
}

static void hldSerializeApply(VMContext* ctx, char* updated) {
    if (updated == nullptr) return;
    RValue value = RValue_makeString(updated);
    value.gmlStackType = GML_TYPE_VARIABLE;
    Instance_setSelfVar(ctx->globalScopeInstance, hldSerialize.serializedId, value);
    free(updated);
}

#ifdef HLD_CHECK_NATIVE_SERIALIZE
static bool hldSerializePending;
static RValue hldSerializeExpected;
static char* hldSerializeExpectedString;

static void hldSerializeExpect(VMContext* ctx, int32_t codeIndex, const RValue* args, int32_t argCount) {
    if (!hldSerializeBind(ctx) || &ctx->dataWin->code.entries[codeIndex] != hldSerialize.code) return;
    char* updated;
    if (!hldSerializeCompute(ctx, args, argCount, &hldSerializeExpected, &updated)) return;
    if (updated == nullptr) {
        const RValue serialized = Instance_getSelfVar(ctx->globalScopeInstance, hldSerialize.serializedId);
        updated = safeStrdup(serialized.string);
    }
    hldSerializeExpectedString = updated;
    hldSerializePending = true;
}

static void hldSerializeVerify(VMContext* ctx, int32_t codeIndex, RValue result) {
    if (!hldSerializePending || &ctx->dataWin->code.entries[codeIndex] != hldSerialize.code) return;
    hldSerializePending = false;
    const RValue expected = hldSerializeExpected;
    const RValue serialized = Instance_getSelfVar(ctx->globalScopeInstance, hldSerialize.serializedId);
    bool same = result.type == expected.type && result.gmlStackType == expected.gmlStackType &&
        result.ownsReference == expected.ownsReference && result.assetRefType == expected.assetRefType;
    if (same && result.type == RVALUE_STRING) same = strcmp(result.string, expected.string) == 0;
    else if (same && result.type == RVALUE_REAL) same = memcmp(&result.real, &expected.real, sizeof(GMLReal)) == 0;
    else if (same) same = result.int32 == expected.int32;
    same = same && serialized.type == RVALUE_STRING && strcmp(serialized.string, hldSerializeExpectedString) == 0;
    if (!same) {
        logError("Native SerializeVar differs: type %d/%d stack %d/%d owns %d/%d\n", result.type, expected.type,
                 result.gmlStackType, expected.gmlStackType, result.ownsReference, expected.ownsReference);
        abort();
    }
    RValue_free(&hldSerializeExpected);
    free(hldSerializeExpectedString);
    hldSerializeExpectedString = nullptr;
    if (++hldSerialize.checked % 5000 == 0) logInfo("HLD: checked %u native SerializeVar calls\n", hldSerialize.checked);
}
#else
static bool hldSerializeNative(VMContext* ctx, int32_t codeIndex, const RValue* args, int32_t argCount, RValue* result) {
    if (!hldSerializeBind(ctx) || &ctx->dataWin->code.entries[codeIndex] != hldSerialize.code) return false;
    char* updated;
    if (!hldSerializeCompute(ctx, args, argCount, result, &updated)) {
        ++hldSerialize.fallbacks;
        return false;
    }
    hldSerializeApply(ctx, updated);
    ++hldSerialize.nativeCalls;
    return true;
}
#endif
'''


def with_native_serialize(code, checked=False):
    if checked:
        declarations = ("static void hldSerializeExpect(VMContext*, int32_t, const RValue*, int32_t); "
                        "static void hldSerializeVerify(VMContext*, int32_t, RValue); ")
        hook = " hldSerializeExpect(ctx, codeIndex, args, argCount);"
    else:
        declarations = "static bool hldSerializeNative(VMContext*, int32_t, const RValue*, int32_t, RValue*); "
        hook = (" { RValue hldNativeResult; if (hldSerializeNative(ctx, codeIndex, args, argCount, &hldNativeResult)) {"
                " ctx->savearefBalance = 0; ctx->bytecodeBase = codeBytes(ctx, code); return hldNativeResult; } }")
    code = replace_once(code, SIGNATURE, declarations + SIGNATURE)
    anchor = "    CallFrame callFrame = {0};\n"
    start = code.index(SIGNATURE)
    position = code.index(anchor, start)
    code = code[:position] + "    CallFrame callFrame = {0};" + hook + "\n" + code[position + len(anchor):]
    if checked:
        anchor = "    result = RValue_stealOwnershipOrCopy(result);\n"
        position = code.index(anchor, start)
        code = code[:position] + anchor.rstrip("\n") + " hldSerializeVerify(ctx, codeIndex, result);\n" + \
            code[position + len(anchor):]
    prefix = "#define HLD_CHECK_NATIVE_SERIALIZE\n" if checked else ""
    return code + "\n" + prefix + NATIVE
