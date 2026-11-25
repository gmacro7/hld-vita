"""Avoid entering Scenery_Step when gameplay leaves an ordinary prop unchanged."""

from runtime_verification_source import replace_once


IDLE_SCENERY = r'''
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
'''


def with_idle_scenery(code, *, checked=False, launch_flag=None):
    helper = IDLE_SCENERY
    guard = ""
    if launch_flag:
        guard = f"{launch_flag} && "
        helper = f"extern bool {launch_flag};\n" + helper + f'''
void hldReportIdleScenery(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita idle scenery frame=%d enabled=%d calls=%u\\n", frame, (int){launch_flag}, idleSceneryCalls);
    idleSceneryCalls = 0;
}}
'''
    else:
        helper += r'''
void hldReportIdleSceneryAtExit(void) {
    logInfo("HLD: idle scenery calls=%u\n", idleSceneryCalls);
}
'''
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    code = replace_once(code, anchor, helper + "\n" + anchor)
    anchor = "    packHldLiteralInstructions(ctx);"
    code = replace_once(code, anchor, "    bindIdleScenery(ctx);\n" + anchor)
    gate = f'''
    const bool skipIdleScenery = {guard}code == idleScenery.code && ctx == idleScenery.context &&
        argCount == 0 && sceneryHasNoUpdate(ctx, selfInstance);
'''
    if checked:
        gate += r'''
    unsigned char sceneryBefore[sizeof(Instance)];
    IntRValueEntry* fieldsBefore = nullptr;
    size_t fieldBytes = 0;
    if (skipIdleScenery) {
        memcpy(sceneryBefore, selfInstance, sizeof(sceneryBefore));
        const IntRValueHashMap* fields = &selfInstance->selfVars;
        fieldBytes = fields->capacity * sizeof(*fields->entries);
#ifdef SHARED_FIELD_MASK
        if (hasSharedFields(fields)) fieldBytes = 0;
#endif
        if (fieldBytes != 0) {
            fieldsBefore = safeMalloc(fieldBytes);
            memcpy(fieldsBefore, fields->entries, fieldBytes);
        }
    }
'''
        anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
        code = replace_once(code, anchor, anchor + r'''
#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
    if (skipIdleScenery) {
        require(memcmp(sceneryBefore, selfInstance, sizeof(sceneryBefore)) == 0);
        require(fieldBytes == 0 || memcmp(fieldsBefore, selfInstance->selfVars.entries, fieldBytes) == 0);
        require(ctx->stack.top == (int32_t)stackTopWhenTheCallFrameWasCreated);
        const RValue expected = scriptFallthroughReturnValue(ctx);
        require(result.type == expected.type && result.gmlStackType == expected.gmlStackType &&
                !result.ownsReference && RValue_toReal(result) == RValue_toReal(expected));
        free(fieldsBefore);
        ++idleSceneryCalls;
    }
#endif
''')
    else:
        gate += r'''
    if (skipIdleScenery) {
        ++idleSceneryCalls;
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        return scriptFallthroughReturnValue(ctx);
    }
'''
    anchor = "    // Create a CallFrame for this script\n"
    code = replace_once(code, anchor, "#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && "
                        "!defined(ENABLE_VM_OPCODE_PROFILER)\n" + gate + "#endif\n\n" + anchor)
    anchor = "void VM_free(VMContext* ctx) {"
    return replace_once(code, anchor, anchor + "\n    if (idleScenery.context == ctx) idleScenery = (IdleScenery){0};")


def with_idle_scenery_exit(code):
    anchor = "    Runner_free(runner);"
    return replace_once(code, anchor, "    extern void hldReportIdleSceneryAtExit(void);\n"
                        "    hldReportIdleSceneryAtExit();\n" + anchor)
