#ifdef HLD_COMPARE_SCENERY_BATCHES
static unsigned batchedSceneryRuns, batchedSceneryInstances;

static inline bool useSceneryBatch(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportSceneryBatches(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery batches frame=%d enabled=%d batches=%u instances=%u\n",
            frame, (int)useSceneryBatch(frame - 1), batchedSceneryRuns, batchedSceneryInstances);
    batchedSceneryRuns = batchedSceneryInstances = 0;
}
#else
static unsigned checkedSceneryInstances;
#endif

static bool advanceSceneryBatch(VMContext* ctx, const uint8_t* code, uint32_t* ip) {
    if (!hldSceneryCull(ctx, code)) return false;
    if (!hldSceneryCanSkipUpdate(ctx)) {
        *ip = HLD_SCENERY_CULL_END;
        return true;
    }

    *ip = HLD_SCENERY_NEXT_INSTANCE;
#ifdef HLD_COMPARE_SCENERY_BATCHES
    if (ctx->runner == nullptr || !useSceneryBatch(ctx->runner->frameCount)) return true;
#endif
    EnvFrame* env = ctx->envStack;
    if (env == nullptr || env->instanceList == nullptr) return true;
#ifdef HLD_COMPARE_SCENERY_BATCHES
    ++batchedSceneryRuns;
#endif
    // The caller matched the whole SCREEN End Step bytecode. Its scenery
    // environment ends with a POP_ENV that jumps back to CULL_START. The
    // snapshot and its active checks remain in exactly that order here.
    while (env->currentIndex + 1 < env->instanceCount) {
        Instance* instance = env->instanceList[++env->currentIndex];
        if (!instance->active) continue;
        switchToInstance(ctx, instance);
        if (!hldSceneryCull(ctx, code)) {
            *ip = HLD_SCENERY_CULL_START;
            return true;
        }
        if (!hldSceneryCanSkipUpdate(ctx)) {
            *ip = HLD_SCENERY_CULL_END;
            return true;
        }
#ifdef HLD_COMPARE_SCENERY_BATCHES
        ++batchedSceneryInstances;
#else
        if (++checkedSceneryInstances == 1000) logInfo("HLD: advanced 1,000 skipped scenery instances in batches\n");
#endif
    }
    // Leave the last POP_ENV to restore self/other and release the frame.
    return true;
}
