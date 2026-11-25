"""Lower the settled per-instance SCREEN update and list append."""

from runtime_verification_source import replace_once
from scenery_cursor_source import with_scenery_cursor


BODY = r'''
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

'''


def with_native_scenery_body(source, *, launch_flag=None):
    source = with_scenery_cursor(source)
    anchor = "static unsigned sceneryCursorEntries, sceneryCursorSkipped;"
    body = BODY
    finish = "finishSceneryBody(ctx, bytes)"
    if launch_flag:
        finish = f"{launch_flag} ? {finish} : HLD_SCENERY_CULL_END"
        body += f'''extern bool {launch_flag};

void hldReportNativeSceneryBody(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita native scenery body frame=%d enabled=%d calls=%u lists=%u\\n",
            frame, (int){launch_flag}, nativeSceneryCalls, nativeSceneryLists);
    nativeSceneryCalls = nativeSceneryLists = 0;
}}

'''
    source = replace_once(source, anchor, body + anchor)
    old = """        if (!hldSceneryCanSkipUpdate(ctx)) {
            ctx->ip = HLD_SCENERY_CULL_END;
            return;
        }
        ++sceneryCursorSkipped;"""
    new = f"""        if (!hldSceneryCanSkipUpdate(ctx)) {{
            ctx->ip = {finish};
            if (ctx->ip != HLD_SCENERY_NEXT_INSTANCE) return;
        }} else {{
            ++sceneryCursorSkipped;
        }}"""
    return replace_once(source, old, new)


def with_native_scenery_body_exit(source):
    anchor = "    Runner_free(runner);"
    return replace_once(source, anchor, "    extern void hldReportNativeSceneryBodyAtExit(void);\n"
                        "    hldReportNativeSceneryBodyAtExit();\n" + anchor)
