extern void hldPrepareDrawList(Runner* runner);

static SceUID drawListWorker = -1;
static SceUID drawListStart = -1;
static SceUID drawListDone = -1;
static Runner* drawListJob;
static bool drawListPending;
static unsigned drawListFrames;
static uint64_t drawListWaitNanos;

static int runDrawListWorker(SceSize size, void* args) {
    (void)size;
    (void)args;
    while (sceKernelWaitSema(drawListStart, 1, NULL) >= 0) {
        Runner* runner = __atomic_load_n(&drawListJob, __ATOMIC_ACQUIRE);
        if (runner == nullptr) break;
        hldPrepareDrawList(runner);
        sceKernelSignalSema(drawListDone, 1);
    }
    return 0;
}

void hldQueueDrawList(Runner* runner) {
    const int frame = runner->frameCount;
    if (drawListWorker < 0 || frame <= 180 || ((frame - 1) / 180) % 2 == 0) return;
    if (!runner->drawableListStructureDirty && !runner->drawableListSortDirty) return;

    __atomic_store_n(&drawListJob, runner, __ATOMIC_RELEASE);
    drawListPending = sceKernelSignalSema(drawListStart, 1) >= 0;
}

void hldFinishDrawList(int frame) {
    if (drawListPending) {
        const uint64_t start = nowNanos();
        int result = sceKernelWaitSema(drawListDone, 1, NULL);
        if (result < 0) {
            logError("Vita draw list wait failed: %08x\n", (unsigned)result);
            abort();
        }
        drawListPending = false;
        ++drawListFrames;
        drawListWaitNanos += nowNanos() - start;
    }
    if (frame > 0 && frame % 90 == 0) {
        logInfo("Vita draw list frame=%d prepared=%u wait_ms=%.3f\n",
                frame, drawListFrames, drawListWaitNanos / (90.0 * 1e6));
        drawListFrames = 0;
        drawListWaitNanos = 0;
    }
}

static void closeDrawListWorker(void) {
    if (drawListWorker >= 0) {
        hldFinishDrawList(-1);
        __atomic_store_n(&drawListJob, nullptr, __ATOMIC_RELEASE);
        sceKernelSignalSema(drawListStart, 1);
        sceKernelWaitThreadEnd(drawListWorker, NULL, NULL);
        sceKernelDeleteThread(drawListWorker);
        drawListWorker = -1;
    }
    if (drawListStart >= 0) sceKernelDeleteSema(drawListStart);
    if (drawListDone >= 0) sceKernelDeleteSema(drawListDone);
    drawListStart = drawListDone = -1;
}

static void openDrawListWorker(void) {
    drawListStart = sceKernelCreateSema("HldDrawListStart", 0, 0, 1, NULL);
    drawListDone = sceKernelCreateSema("HldDrawListDone", 0, 0, 1, NULL);
    if (drawListStart >= 0 && drawListDone >= 0) {
        drawListWorker = sceKernelCreateThread("HldDrawListWorker", runDrawListWorker, 96, 0x10000, 0, 0, NULL);
        if (drawListWorker >= 0 && sceKernelStartThread(drawListWorker, 0, NULL) >= 0) {
            logInfo("Vita draw list worker active, alternating 180-frame windows\n");
            return;
        }
        if (drawListWorker >= 0) sceKernelDeleteThread(drawListWorker);
        drawListWorker = -1;
    }
    logError("Vita draw list worker could not start\n");
    closeDrawListWorker();
}
