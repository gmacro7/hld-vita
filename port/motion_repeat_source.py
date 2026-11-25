"""Fuse the two bounded movement repeats around the existing collision builtin."""

from runtime_verification_source import replace_once


MOTION_REPEAT = r'''
enum { MOVE_HORIZONTAL = 0xc8, MOVE_VERTICAL = 0x194, MAX_MOVE_REPEAT = 32 };

static struct {
    const VMContext* context;
    const uint8_t* program;
    BuiltinFunc placeMeeting;
} motionRepeat;
static unsigned motionRepeats;

static void bindMotionRepeat(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_movement", 580, 0xb33ab814u);
    if (code == nullptr || code->localsCount != 3) return;
    const uint8_t* program = codeBytes(ctx, code);
    BuiltinFunc collision = VM_findBuiltin(ctx, "place_meeting");
    if (collision == nullptr) return;
    const unsigned callOffsets[] = {0xf4, 0x1c0};
    for (unsigned i = 0; i < sizeof(callOffsets) / sizeof(*callOffsets); ++i) {
        const uint32_t call = resolveFuncOperand(program + callOffsets[i] + 4);
        if (call >= ctx->funcCallCacheCount || ctx->funcCallCache[call].builtin != (void*)collision ||
            ctx->funcCallCache[call].scriptCodeIndex >= 0) return;
    }
    BuiltinFunc sign = VM_findBuiltin(ctx, "sign");
    if (sign == nullptr) return;
    const unsigned signOffsets[] = {0xe8, 0x118, 0x1ac, 0x1e4};
    for (unsigned i = 0; i < sizeof(signOffsets) / sizeof(*signOffsets); ++i) {
        const uint32_t call = resolveFuncOperand(program + signOffsets[i] + 4);
        if (call >= ctx->funcCallCacheCount || ctx->funcCallCache[call].builtin != (void*)sign ||
            ctx->funcCallCache[call].scriptCodeIndex >= 0) return;
    }
    motionRepeat.context = ctx;
    motionRepeat.program = program;
    motionRepeat.placeMeeting = collision;
    logInfo("HLD: bound the movement repeats\n");
}

static uint32_t runMotionRepeat(VMContext* ctx, const uint8_t* program, uint32_t offset) {
    if (ctx != motionRepeat.context || program != motionRepeat.program || ctx->runner == nullptr ||
        ctx->runner->spatialGrid == nullptr || ctx->stack.top == 0) return 0;
    CallFrame* frame = ctx->currentCallFrame;
    Instance* self = frame->self;
    if (self == nullptr || frame->scriptArgs == nullptr || frame->scriptArgsCount < 3) return 0;
    RValue* count = &ctx->stack.slots[ctx->stack.top - 1];
    if (count->type != RVALUE_INT32 || count->int32 <= 0 || count->int32 > MAX_MOVE_REPEAT) return 0;
    const bool horizontal = offset == MOVE_HORIZONTAL;
    const RValue movement = frame->scriptArgs[horizontal ? 0 : 1];
    const RValue target = frame->scriptArgs[2];
    if ((movement.type != RVALUE_REAL && movement.type != RVALUE_INT32) ||
        (target.type != RVALUE_REAL && target.type != RVALUE_INT32)) return 0;
    const GMLReal distance = RValue_toReal(movement);
    if (!isfinite(distance) || !isfinite(self->x) || !isfinite(self->y)) return 0;

    if (++motionRepeats == 100) logInfo("HLD: executed 100 fused movement repeats\n");
    const GMLReal step = distance > 0.0 ? 1.0 : distance < 0.0 ? -1.0 : 0.0;
    float* coordinate = horizontal ? &self->x : &self->y;
    while (count->int32 > 0) {
        const GMLReal proposed = (GMLReal)*coordinate + step;
        RValue args[] = {RValue_makeReal(horizontal ? proposed : self->x),
                         RValue_makeReal(horizontal ? self->y : proposed), target};
        if (hldWatchCollisionMutation) hldCollisionMutated = true;
        if (RValue_toBool(motionRepeat.placeMeeting(ctx, args, 3))) {
            // Resume at the original collision callback with the live frame and counter.
            return horizontal ? 0x130 : 0x1fc;
        }
        const float rounded = (float)proposed;
        if (rounded != *coordinate) {
            *coordinate = rounded;
            SpatialGrid_markInstanceAsDirty(ctx->runner->spatialGrid, self);
        }
        --count->int32;
    }
    return horizontal ? 0x168 : 0x234;
}
'''


def with_motion_repeat(code, *, launch_flag=None):
    helper = MOTION_REPEAT
    if launch_flag:
        anchor = "static unsigned motionRepeats;"
        helper = replace_once(helper, anchor, anchor + f'''
static unsigned motionProbes[2], motionHits;
extern bool {launch_flag};

void hldReportMotionRepeat(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita movement repeats frame=%d enabled=%d calls=%u\\n", frame, (int){launch_flag}, motionRepeats);
    logInfo("Vita movement probes frame=%d horizontal=%u vertical=%u hits=%u\\n",
            frame, motionProbes[0], motionProbes[1], motionHits);
    motionRepeats = motionProbes[0] = motionProbes[1] = motionHits = 0;
}}
''')
        anchor = "    if (ctx != motionRepeat.context"
        helper = replace_once(helper, anchor, f"    if (!{launch_flag}) return 0;\n" + anchor)
        helper = replace_once(helper, '    if (++motionRepeats == 100) logInfo("HLD: executed 100 fused movement repeats\\n");',
                              "    ++motionRepeats;")
        anchor = "        if (RValue_toBool(motionRepeat.placeMeeting(ctx, args, 3))) {"
        helper = replace_once(helper, anchor, "        ++motionProbes[!horizontal];\n" + anchor +
                              "\n            ++motionHits;")
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    code = replace_once(code, anchor, helper + "\n" + anchor)
    anchor = "            case OP_PACKED_ARGUMENT_ARRAY: {"
    code = replace_once(code, anchor, anchor + r'''
                if (fuseInstructions && (instrAddr == MOVE_HORIZONTAL || instrAddr == MOVE_VERTICAL)) {
                    const uint32_t destination = runMotionRepeat(ctx, bytecodeBase, instrAddr);
                    if (destination != 0) {
                        ip = destination;
                        break;
                    }
                }
''')
    anchor = "    packHldMathInstructions(ctx);"
    code = replace_once(code, anchor, anchor + "\n    bindMotionRepeat(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    return replace_once(code, anchor, anchor + r'''
    if (motionRepeat.context == ctx) {
        memset(&motionRepeat, 0, sizeof(motionRepeat));
        motionRepeats = 0;
    }
''')
