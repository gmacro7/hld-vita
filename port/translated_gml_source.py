"""Append translated GML (gml_translate) to vm.c and run it in place of the bytecode for chosen entries.

`with_translated_gml(code, entries)` adds:
  * resolveVariableWriteAccess: resolveVariableWrite with the array access passed in instead of popped from the
    stack (generated from the VM source, so the write semantics stay identical);
  * the runtime, one C function per entry and the name table;
  * a hook in VM_executeCode, before the call frame is built, that runs the translated function for a listed
    entry (hybrid mode: everything else stays on the interpreter, so each set can be verified against it).
"""

from pathlib import Path
import re

from gml_symbols import DataWin
from gml_translate import translate
from runtime_verification_source import replace_once

ROOT = Path(__file__).resolve().parent
DECOMPILED = ROOT.parent / "work" / "decompiled"  # made by scripts/prepare_game.py from your data.win
SIGNATURE = ("RValue VM_executeCode(VMContext* ctx, int32_t codeIndex, Instance* selfInstance, Instance* otherInstance, "
             "int32_t eventObjectIndex, int32_t eventType, int32_t eventSubtype, RValue* args, int32_t argCount) {")

HOOK = r'''
static HldcFunction* hldcByCode;
static uint32_t hldcCodeCount;
static const VMContext* hldcBound;

static void hldcBind(VMContext* ctx) {
    free(hldcByCode);
    hldcBound = ctx;
    hldcCodeCount = ctx->dataWin->code.count;
    hldcByCode = safeCalloc(hldcCodeCount, sizeof(*hldcByCode));
    unsigned bound = 0;
    for (unsigned i = 0; hldcTable[i].name != nullptr; ++i) {
        const ptrdiff_t slot = shgeti(ctx->codeIndexByName, (char*) hldcTable[i].name);
        if (slot < 0) continue;
        hldcByCode[ctx->codeIndexByName[slot].value] = hldcTable[i].function;
        ++bound;
    }
    logInfo("HLDC: %u translated code entries bound\n", bound);
}

// Scripts VM_executeCode answers natively before building a frame; translated code still calls these through it.
bool hldcHasShortcut(VMContext* ctx, int32_t codeIndex) {
    const CodeEntry* code = &ctx->dataWin->code.entries[codeIndex];
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (code == stableCases.code || code == idleSpawner.code || code == zeroMovement.code || code == idleScenery.code) return true;
    if (code == depthHelpers.setDepth || code == depthHelpers.modDepth) return true;
    if (hldSceneQueries && (code == sceneQueries.bounds || code == sceneQueries.viewport)) return true;
    if (hldFloorPattern && code == floorPatternBinding.code) return true;
    if (hldNativeLineScript && code == lineScript.code) return true;
#endif
#ifdef HLD_ASCII_PARSER_AVAILABLE
    if (code == asciiParser.code) return true;
#endif
#ifdef HLD_NATIVE_SCRIPTS_HOOKED
    if (hldNativeKind(ctx, codeIndex) != 0) return true;
#endif
    return false;
}

HldcFunction hldcTranslated(VMContext* ctx, int32_t codeIndex) {
    if (hldcBound != ctx) hldcBind(ctx);
    return (uint32_t) codeIndex < hldcCodeCount ? hldcByCode[codeIndex] : nullptr;
}
'''


def write_access_function(code):
    """resolveVariableWrite, taking the ArrayAccess as a parameter."""
    start = code.index("NOINLINE\nstatic void resolveVariableWrite(VMContext* ctx, int32_t instanceType, uint32_t varRef, RValue val) {")
    end = code.index("\n}\n", start) + 3
    body = code[start:end]
    body = body.replace("static void resolveVariableWrite(VMContext* ctx, int32_t instanceType, uint32_t varRef, RValue val) {",
                        "static void resolveVariableWriteAccess(VMContext* ctx, int32_t instanceType, uint32_t varRef, RValue val, "
                        "ArrayAccess access) {")
    body = replace_once(body, "    ArrayAccess access = popArrayAccess(ctx, varRef);\n", "")
    # The fast path is only for plain (non-array, fixed-scope) writes.
    body = replace_once(body, "    if (varDef->varID >= 0) {", "    if (varDef->varID >= 0 && !access.isArray && !access.hasInstanceType) {")
    assert "popArrayAccess(" not in body
    return body


OBJECT_ANCESTRY = """bool VM_isObjectOrDescendant(DataWin* dataWin, int32_t objectIndex, int32_t targetObjectIndex) {
    int32_t currentObj = objectIndex;
    int depth = 0;
    while (currentObj >= 0 && (uint32_t) currentObj < dataWin->objt.count && 32 > depth) {
        if (currentObj == targetObjectIndex) return true;
        currentObj = dataWin->objt.objects[currentObj].parentId;
        depth++;
    }
    return false;
}"""

# The same walk over a compact copy of the parent ids: each step reads a small array instead of a GameObject.
# Line count is unchanged so profile feedback for the functions after it still matches. HLD never calls
# object_set_parent, which is what would make the copy stale.
COMPACT_ANCESTRY = """static const DataWin* hldParentsOwner; static int32_t* hldParents; static uint32_t* hldAncestry; static uint32_t hldAncestryWords; static const int32_t* hldBindParents(DataWin*); bool VM_isObjectOrDescendant(DataWin* dataWin, int32_t objectIndex, int32_t targetObjectIndex) {
    if (dataWin != hldParentsOwner) hldBindParents(dataWin);
    const uint32_t count = dataWin->objt.count;
    if (objectIndex < 0 || (uint32_t) objectIndex >= count || targetObjectIndex < 0 || (uint32_t) targetObjectIndex >= count) return false;
    // One bit per (object, target): set when target is the object or one of the ancestors the 32-step walk reaches.
    return (hldAncestry[(size_t) objectIndex * hldAncestryWords + ((uint32_t) targetObjectIndex >> 5)] >>
            ((uint32_t) targetObjectIndex & 31u)) & 1u;


}"""

BIND_PARENTS = """
static const int32_t* hldBindParents(DataWin* dataWin) {
    free(hldParents);
    hldParents = safeMalloc((dataWin->objt.count + 1) * sizeof(int32_t));
    for (uint32_t i = 0; i < dataWin->objt.count; ++i) hldParents[i] = dataWin->objt.objects[i].parentId;
    // Ancestry bits: the same walk as the GameMaker parent chain (object itself plus up to 31 parents).
    free(hldAncestry);
    hldAncestryWords = (dataWin->objt.count + 31u) / 32u;
    hldAncestry = safeCalloc((size_t) dataWin->objt.count * hldAncestryWords + 1, sizeof(uint32_t));
    for (uint32_t i = 0; i < dataWin->objt.count; ++i) {
        int32_t current = (int32_t) i;
        for (int depth = 0; current >= 0 && (uint32_t) current < dataWin->objt.count && 32 > depth; depth++) {
            hldAncestry[(size_t) i * hldAncestryWords + ((uint32_t) current >> 5)] |= 1u << ((uint32_t) current & 31u);
            current = hldParents[current];
        }
    }
    hldParentsOwner = dataWin;
    return hldParents;
}
"""


def with_translated_gml(code, entries, stats=False):
    assert OBJECT_ANCESTRY.count("\n") == COMPACT_ANCESTRY.count("\n")
    code = replace_once(code, OBJECT_ANCESTRY, COMPACT_ANCESTRY) + BIND_PARENTS
    win = DataWin()
    assets = win.assets()
    variables = {name for name, kind, _ in win.variables() if kind != -7}
    generated, parts = translate(entries, DECOMPILED, assets, variables, stats=stats)
    import os
    fast = os.environ.get("HLDC_FAST_DISPATCH", "1") == "1"
    entry = (" { HldcFunction hldcFastFn = hldcFastDispatch(ctx, codeIndex); if (hldcFastFn != nullptr) return hldcFastFn("
             "ctx, selfInstance, otherInstance, args, argCount, eventObjectIndex, eventType, eventSubtype); }") if fast else ""
    code = replace_once(code, SIGNATURE, "typedef RValue (*HldcFunction)(VMContext*, Instance*, Instance*, RValue*, int32_t, "
                        "int32_t, int32_t, int32_t); HldcFunction hldcTranslated(VMContext*, int32_t); "
                        "HldcFunction hldcFastDispatch(VMContext*, int32_t); " + SIGNATURE + entry)
    start = code.index(SIGNATURE)
    anchor = "    CallFrame callFrame = {0};"
    position = code.index(anchor, start)
    end = code.index("\n", position)
    code = code[:end] + (" { HldcFunction hldcFn = hldcTranslated(ctx, codeIndex); if (hldcFn != nullptr) "
                         "return hldcFn(ctx, selfInstance, otherInstance, args, argCount, eventObjectIndex, eventType, "
                         "eventSubtype); }") + code[end:]
    if "static DepthHelpers depthHelpers;" not in code or "static SceneQueries sceneQueries;" not in code:
        raise SystemExit("translated GML expects the depth helper and scene query shortcuts")
    generated = generated.replace("typedef RValue (*HldcFunction)(VMContext*, Instance*, Instance*, RValue*, int32_t, int32_t, int32_t, int32_t);\n", "")
    return code + "\n" + write_access_function(code) + "\n" + generated + "\n" + HOOK + FAST_DISPATCH, parts


# Translated code with no prologue shortcut is called straight from the top of VM_executeCode: the dozen shortcut
# checks there only ever claim the codes listed below (hldcHasShortcut covers the rest), so skipping them for every
# other translated entry changes nothing but the time spent. Built once the VM's shortcut bindings are in place.
FAST_DISPATCH = r"""
static HldcFunction* hldcFast;
static uint32_t hldcFastCount;
static const VMContext* hldcFastContext;
HldcFunction hldcFastDispatch(VMContext* ctx, int32_t codeIndex) {
#if defined(ENABLE_VM_TRACING) || defined(ENABLE_VM_GML_PROFILER) || defined(ENABLE_VM_OPCODE_PROFILER)
    return nullptr;
#else
    if (ctx != hldcFastContext) {
        if (ctx->runner == nullptr || ctx->runner->frameCount < 2) return nullptr;
        free(hldcFast);
        hldcFastCount = ctx->dataWin->code.count;
        hldcFast = (HldcFunction*) safeCalloc(hldcFastCount + 1, sizeof(HldcFunction));
        unsigned direct = 0;
        for (uint32_t i = 0; i < hldcFastCount; ++i) {
            const CodeEntry* entry = &ctx->dataWin->code.entries[i];
            HldcFunction fn = hldcTranslated(ctx, (int32_t) i);
            if (fn == nullptr || hldcHasShortcut(ctx, (int32_t) i)) continue;
            if (entry == leafEvents.enemyStep || entry == leafEvents.enemyBegin || entry == leafEvents.decorDraw ||
                entry == leafEvents.sceneryDraw) continue;
            if (entry == movementHelpers.approach || entry == movementHelpers.distance || entry == movementHelpers.isType) continue;
            if (entry == dormantMineEntry || entry == lineScript.code || entry == floorPatternBinding.code ||
                entry == idleScenery.code || entry == zeroMovement.code) continue;
            hldcFast[i] = fn;
            ++direct;
        }
        hldcFastContext = ctx;
        logInfo("HLDC: %u translated entries dispatch directly\n", direct);
    }
    return (uint32_t) codeIndex < hldcFastCount ? hldcFast[codeIndex] : nullptr;
#endif
}
"""


def all_entries():
    """Every decompiled entry, less those named in the file at $HLDC_EXCLUDE (left to the interpreter)."""
    import os
    excluded = set()
    if os.environ.get("HLDC_EXCLUDE"):
        excluded = {line.strip() for line in Path(os.environ["HLDC_EXCLUDE"]).read_text().splitlines() if line.strip()}
    return sorted(path.stem for path in DECOMPILED.glob("*.gml") if path.stem not in excluded)
