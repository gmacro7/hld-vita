"""Resolve the original platform underfoot before resuming its material script."""

from runtime_verification_source import replace_once


MATERIAL_ENTRY = r'''
enum {
    OP_PACKED_WALKING_MATERIAL = 0x95,
    WALKING_MATERIAL_BODY = 0x158,
    WALKING_PLAYER = 419,
};

static const CodeEntry* walkingMaterialEntry;
static unsigned walkingMaterialCalls, walkingMaterialHits;

static void bindWalkingMaterial(VMContext* ctx) {
    walkingMaterialEntry = nullptr;
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15 ||
        ctx->dataWin->objt.count <= WALKING_PLAYER) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_GetWalkingMaterial", 1508, 0x31292e22u);
    if (code == nullptr || code->localsCount != 4 || code->argumentsCount != 0) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    if (resolveVarDef(ctx, resolveVarOperand(bytes + 8))->varID != 1 ||
        resolveVarDef(ctx, resolveVarOperand(bytes + 0x100))->varID != 2) return;
    const BuiltinFunc point = VM_findBuiltin(ctx, "collision_point");
    const BuiltinFunc exists = VM_findBuiltin(ctx, "instance_exists");
    if (point == nullptr || exists == nullptr) return;
    static const uint16_t calls[] = {0xf8, 0x14c, 0x110};
    for (unsigned i = 0; i < sizeof(calls) / sizeof(*calls); ++i) {
        const uint32_t function = resolveFuncOperand(bytes + calls[i]);
        if (function >= ctx->funcCallCacheCount ||
            ctx->funcCallCache[function].builtin != (void*)(i < 2 ? point : exists) ||
            ctx->funcCallCache[function].scriptCodeIndex >= 0) return;
    }
    walkingMaterialEntry = code;
}

static void installWalkingMaterial(VMContext* ctx) {
    if (walkingMaterialEntry == nullptr) return;
    uint8_t* bytes = codeBytes(ctx, walkingMaterialEntry);
    const OpWord first = {BinaryUtils_readUint32Aligned(bytes)};
    if (OpWord_opcode(first) != OP_PACKED_LITERAL_STORE || OpWord_extra(first) != 0) return;
    BinaryUtils_writeUint32(bytes, (first.value & 0x00ffffffu) | ((uint32_t)OP_PACKED_WALKING_MATERIAL << 24));
    logInfo("HLD: bound native walking material probes\n");
}

bool hldFindWalkingPlatform(Runner* runner, const Instance* self, int32_t* result);

#ifdef HLD_CHECK_WALKING_MATERIAL
static const CallFrame* pendingWalkingCheck;
static int32_t predictedWalkingId, predictedWalkingStackTop;

static void checkWalkingMaterial(VMContext* ctx) {
    const CallFrame* frame = ctx->currentCallFrame;
    if (pendingWalkingCheck != frame) return;
    const RValue stepped = frame->localVars[1], floor = frame->localVars[2];
    require(ctx->stack.top == predictedWalkingStackTop);
    require(stepped.type == RVALUE_REAL && stepped.gmlStackType == GML_TYPE_DOUBLE &&
            stepped.real == 0 && !signbit(stepped.real) && !stepped.ownsReference);
    require(floor.type == RVALUE_REAL && floor.gmlStackType == GML_TYPE_VARIABLE &&
            floor.real == (GMLReal)predictedWalkingId && !floor.ownsReference);
    pendingWalkingCheck = nullptr;
    ++walkingMaterialCalls;
    walkingMaterialHits += predictedWalkingId != INSTANCE_NOONE;
}
#endif

static bool prepareWalkingMaterial(VMContext* ctx) {
    CallFrame* frame = ctx->currentCallFrame;
    Instance* self = frame->self;
    if (self == nullptr || self->objectIndex < 0 || self->objectIndex == WALKING_PLAYER ||
        self->objectIndex == STRUCT_OBJECT_INDEX || frame->scriptArgsCount != 0 ||
        frame->localVarsCount < 3 || ctx->runner == nullptr ||
        !(ctx->runner->mathEpsilon >= 0 && ctx->runner->mathEpsilon < 0.5) ||
        !isfinite(self->x) || !isfinite(self->y)) return false;
    int32_t floorId;
    if (!hldFindWalkingPlatform(ctx->runner, self, &floorId)) return false;
#ifdef HLD_CHECK_WALKING_MATERIAL
    require(pendingWalkingCheck == nullptr);
    pendingWalkingCheck = frame;
    predictedWalkingId = floorId;
    predictedWalkingStackTop = ctx->stack.top;
    return false;
#else
    RValue floor = RValue_makeReal((GMLReal)floorId);
    floor.gmlStackType = GML_TYPE_VARIABLE;
    RValue_writeIntoSlotStealingOwnershipOrCopying(&frame->localVars[1], RValue_makeReal(0));
    RValue_writeIntoSlotStealingOwnershipOrCopying(&frame->localVars[2], floor);
    ++walkingMaterialCalls;
    walkingMaterialHits += floorId != INSTANCE_NOONE;
    return true;
#endif
}
'''


PLATFORM_QUERY = r'''
bool hldFindWalkingPlatform(Runner* runner, const Instance* self, int32_t* result) {
    enum { WALKING_PROTECTION_PLATFORM = 409, WALKING_SAFE_PLATFORM = 408 };
    GMLReal x = self->x, y = self->y;
    if (runner->collisionCompatibilityMode) {
        x = compatRoundCoord(x);
        y = compatRoundCoord(y);
    }

    // The two original queries use descendant-inclusive order, precise
    // masks, and notme. They have no callbacks or writes to instance lists.
    const int32_t targets[] = {WALKING_PROTECTION_PLATFORM, WALKING_SAFE_PLATFORM};
    for (unsigned type = 0; type < sizeof(targets) / sizeof(*targets); ++type) {
        Instance** instances = runner->instancesByObject[targets[type]];
        const size_t count = arrlenu(instances);
        for (size_t i = 0; i < count; ++i) {
            Instance* other = instances[i];
            if (other == self || !other->active || !pointQueryHits(runner, other, x, y, true)) continue;
            // This hit passes the original instance_exists test without
            // copying the bucket. Unexpected IDs retain the old prologue.
            if (other->instanceId < INSTANCE_ID_BASE || other->instanceId >= (1u << 24)) return false;
            *result = (int32_t)other->instanceId;
            return true;
        }
    }
    *result = INSTANCE_NOONE;
    return true;
}
'''


def with_walking_material_query(code):
    anchor = "static RValue builtin_collision_point(VMContext* ctx, RValue* args, int32_t argCount) {"
    start = code.index(anchor)
    end = code.index("\n}\n", start) + len("\n}\n")
    original = code[start:end]
    low = original.index("        if (!Collision_pointInsideInstanceBox")
    high = original.index("\n        resultId = inst->instanceId;", low)
    predicates = original[low:high].replace("ctx->runner", "runner").replace("ctx->dataWin", "runner->dataWin")
    predicates = predicates.replace("continue;", "return false;")
    predicate = """static inline bool pointQueryHits(Runner* runner, Instance* inst, GMLReal px, GMLReal py, int32_t prec) {
""" + "\n".join(line.removeprefix("    ") for line in predicates.rstrip().splitlines()) + """
    return true;
}
"""
    builtin = original[:low] + "        if (!pointQueryHits(runner, inst, px, py, prec)) continue;\n" + original[high:]
    return code[:start] + predicate + PLATFORM_QUERY + "\n" + builtin + code[end:]


def with_walking_material(code, *, checked=False, launch_flag=None):
    helper = MATERIAL_ENTRY
    if checked:
        helper = "#define HLD_CHECK_WALKING_MATERIAL\n" + helper
    if launch_flag:
        anchor = "    walkingMaterialEntry = nullptr;"
        helper = f"extern bool {launch_flag};\n" + replace_once(helper, anchor, anchor + f"\n    if (!{launch_flag}) return;")
        helper += f'''
void hldReportWalkingMaterial(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita walking material frame=%d enabled=%d calls=%u hits=%u\\n",
            frame, (int){launch_flag}, walkingMaterialCalls, walkingMaterialHits);
    walkingMaterialCalls = walkingMaterialHits = 0;
}}
'''
    else:
        helper += r'''
void hldReportWalkingMaterialAtExit(void) {
    logInfo("HLD: walking material calls=%u hits=%u\n", walkingMaterialCalls, walkingMaterialHits);
}
'''
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    code = replace_once(code, anchor, helper + "\n" + anchor)
    anchor = "    packHldLiteralInstructions(ctx);"
    code = replace_once(code, anchor, "    bindWalkingMaterial(ctx);\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    code = replace_once(code, anchor, anchor + "\n    installWalkingMaterial(ctx);")

    anchor = "            case OP_PACKED_LITERAL_STORE: {\n"
    start = code.index(anchor) + len(anchor)
    end = code.index("\n            }\n", start)
    fallback = code[start:end]
    code = replace_once(code, anchor, r'''            case OP_PACKED_WALKING_MATERIAL: {
                if (prepareWalkingMaterial(ctx)) {
                    ip = WALKING_MATERIAL_BODY;
                    break;
                }
''' + fallback + "\n            }\n" + anchor)
    if checked:
        anchor = "            case OP_PACKED_VAR_READ: {\n"
        code = replace_once(code, anchor, anchor + r'''                if (code == walkingMaterialEntry && instrAddr == WALKING_MATERIAL_BODY)
                    checkWalkingMaterial(ctx);
''')
    return code


def with_walking_material_exit(code):
    anchor = "    Runner_free(runner);"
    return replace_once(code, anchor, "    extern void hldReportWalkingMaterialAtExit(void);\n"
                        "    hldReportWalkingMaterialAtExit();\n" + anchor)
