"""Continue the bound SCREEN scenery loop without redispatching its cull."""

from runtime_verification_source import replace_once


CURSOR = r'''
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

static void popSceneryEnv(VMContext* ctx, OpWord word, uint32_t instrAddr, const uint8_t* bytes) {
    const uint32_t body = HLD_SCENERY_CULL_START;
    // The caller has already matched the exact SCREEN function and loop.
    // Keep one with-block snapshot, its iteration order and its normal
    // active filtering. Re-evaluate live fields after each scripted callback.
    const uint32_t continuation = ctx->ip;
    while (true) {
        handlePopEnv(ctx, word, instrAddr);
        if (ctx->ip != body) return;
        ++sceneryCursorEntries;
        if (!hldSceneryCull(ctx, bytes)) return;
        if (!hldSceneryCanSkipUpdate(ctx)) {
            ctx->ip = HLD_SCENERY_CULL_END;
            return;
        }
        ++sceneryCursorSkipped;
        ctx->ip = continuation;
    }
}

'''


def with_scenery_cursor(source):
    anchor = "static bool finishRestingScenery(VMContext* ctx, const CodeEntry* code);"
    source = replace_once(source, anchor, CURSOR + anchor)
    old = "                handlePopEnv(ctx, opword, instrAddr);\n"
    new = r'''                if (hldSceneryCursor && useHldSceneryCull && instrAddr == HLD_SCENERY_NEXT_INSTANCE &&
                    (uint32_t)(instrAddr + OpWord_branchOffset(opword)) == HLD_SCENERY_CULL_START)
                    popSceneryEnv(ctx, opword, instrAddr, bytecodeBase);
                else
                    handlePopEnv(ctx, opword, instrAddr);
'''
    if source.count(old) != 2:
        raise ValueError("Expected threaded and switch POP_ENV handlers")
    return source.replace(old, new)


def with_scenery_cursor_exit(source):
    anchor = "    Runner_free(runner);"
    return replace_once(source, anchor, "    extern void hldReportSceneryCursorAtExit(void);\n"
                        "    hldReportSceneryCursorAtExit();\n" + anchor)
