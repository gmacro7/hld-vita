"""Replace common, fingerprinted leaf events with the engine operations they use."""

from pathlib import Path


def with_leaf_events(code, *, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("native-leaf-events.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_LEAF_EVENTS\n" + helper
    if verify:
        helper = "#define HLD_VERIFY_LEAF_EVENTS\n" + helper
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, helper + "\n" + anchor)
    replace("    packHldLiteralInstructions(ctx);", "    bindLeafEvents(ctx);\n    packHldLiteralInstructions(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    if (leafEvents.context == ctx) leafEvents = (LeafEvents){0};")
    anchor = "    // Create a CallFrame for this script\n"
    replace(anchor, r'''#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
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

''' + anchor)
    anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
    replace(anchor, anchor + r'''
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
#endif''')
    return code
