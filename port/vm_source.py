"""Build a trial that removes redundant stack and lookup work in the VM."""


def with_fast_vm_values(code):
    def replace(before, after, count=1):
        nonlocal code
        assert code.count(before) == count, before
        code = code.replace(before, after)

    read = """            RValue* slot = IntRValueHashMap_findSlot(&inst->selfVars, varDef->varID);
            if (slot == nullptr)
                return false;
            *out = *slot;
            out->ownsReference = false;
            return true;"""
    replace(read, """#if IS_WAD17_OR_HIGHER_ENABLED
            if (IS_WAD17_OR_HIGHER(ctx)) {
                RValue* slot = IntRValueHashMap_findSlot(&inst->selfVars, varDef->varID);
                if (slot == nullptr) return false; // Static struct fallback.
                *out = *slot;
                out->ownsReference = false;
                return true;
            }
#endif
            *out = IntRValueHashMap_get(&inst->selfVars, varDef->varID);
            return true;""", 3)

    replace("enum { VM_INLINE_FRAME_VALUES = 8 };", "enum { VM_INLINE_FRAME_VALUES = 8, VM_INLINE_LOCAL_VALUES = 24 };")
    replace("!frame->localVarsOnHeap && count <= VM_INLINE_FRAME_VALUES",
            "!frame->localVarsOnHeap && count <= VM_INLINE_LOCAL_VALUES")
    replace("RValue inlineLocals[VM_INLINE_FRAME_VALUES];", "RValue inlineLocals[VM_INLINE_LOCAL_VALUES];")
    replace("callFrame.localVarsOnHeap = localsCount > VM_INLINE_FRAME_VALUES;",
            "callFrame.localVarsOnHeap = localsCount > VM_INLINE_LOCAL_VALUES;")

    first = """\tRValue stackArgs[VM_MAX_STACK_ARGS] = {0};
\tbool useStackArgs = argCount <= VM_MAX_STACK_ARGS;
    if (argCount > 0) {
        args = useStackArgs ? stackArgs : (RValue *)safeCalloc(argCount, sizeof(RValue));
        repeat(argCount, i) {
            args[i] = stackPop(ctx);
        }
    }"""
    second = """    RValue stackArgs[VM_MAX_STACK_ARGS];
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
    }"""
    replace(first, second, 2)
    return code
