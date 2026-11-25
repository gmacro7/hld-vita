static inline void cachedStackPush(RValue* slots, int32_t* top, RValue value) {
    assert(VM_STACK_SIZE > *top);
    slots[(*top)++] = value;
}

static inline void cachedStackPushTyped(RValue* slots, int32_t* top, RValue value, uint8_t stackType) {
    value.gmlStackType = stackType;
    cachedStackPush(slots, top, value);
}

static inline RValue cachedStackPop(RValue* slots, int32_t* top) {
    assert(*top > 0);
    return slots[--*top];
}

static inline RValue cachedStackReadVariable(VMContext* ctx, int32_t* top, int32_t scope, uint32_t operand) {
    ctx->stack.top = *top;
    RValue result = resolveVariableRead(ctx, scope, operand);
    *top = ctx->stack.top;
    return result;
}

static inline void cachedStackWriteVariable(VMContext* ctx, int32_t* top, int32_t scope, uint32_t operand, RValue value) {
    ctx->stack.top = *top;
    resolveVariableWrite(ctx, scope, operand, value);
    *top = ctx->stack.top;
}
