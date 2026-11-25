// Opt-in test. The worker owns the GL context only until the next API use.
volatile unsigned hldSwapPending;
static SceUID swapMain = -1;
static SceUID swapWorker = -1;
static SceUID swapStart = -1;
static SceUID swapDone = -1;
static bool swapStop;
static int32_t swapFrame = -1;
static uint32_t swapSubmitted;
static uint32_t swapWaited;
static uint64_t swapWaitNanos;

static void swapNow(void);
static void reportFinishedSwap(void);

__attribute__((used, externally_visible))
void hldWaitForSwap(void) {
    if (!hldSwapPending || sceKernelGetThreadId() != swapMain) return;
    const uint64_t start = nowNanos();
    int result = sceKernelWaitSema(swapDone, 1, NULL);
    if (result < 0) {
        logError("Vita async swap completion failed: %08x\n", (unsigned)result);
        abort();
    }
    hldSwapPending = 0;
    ++swapWaited;
    swapWaitNanos += nowNanos() - start;
    reportFinishedSwap();
}

static int runSwapWorker(SceSize size, void* arg) {
    (void)size;
    (void)arg;
    while (sceKernelWaitSema(swapStart, 1, NULL) >= 0) {
        if (swapStop) break;
        swapNow();
        sceKernelSignalSema(swapDone, 1);
    }
    return 0;
}

static void closeSwapWorker(void) {
    if (swapWorker >= 0) {
        hldWaitForSwap();
        swapStop = true;
        sceKernelSignalSema(swapStart, 1);
        sceKernelWaitThreadEnd(swapWorker, NULL, NULL);
        sceKernelDeleteThread(swapWorker);
        swapWorker = -1;
    }
    if (swapStart >= 0) sceKernelDeleteSema(swapStart);
    if (swapDone >= 0) sceKernelDeleteSema(swapDone);
    swapStart = swapDone = -1;
}

static void openSwapWorker(void) {
    FILE* flag = fopen("ux0:data/hld-vm/async-present-ab", "r");
    if (flag == NULL) return;
    fclose(flag);
    swapMain = sceKernelGetThreadId();
    swapStop = false;
    swapStart = sceKernelCreateSema("HldSwapStart", 0, 0, 1, NULL);
    swapDone = sceKernelCreateSema("HldSwapDone", 0, 0, 1, NULL);
    if (swapStart >= 0 && swapDone >= 0) {
        swapWorker = sceKernelCreateThread("HldSwapWorker", runSwapWorker, 96, 0x8000, 0, 0, NULL);
        if (swapWorker >= 0 && sceKernelStartThread(swapWorker, 0, NULL) >= 0) {
            logInfo("Vita async presentation test active, alternating 180-frame windows\n");
            return;
        }
        if (swapWorker >= 0) sceKernelDeleteThread(swapWorker);
        swapWorker = -1;
    }
    logError("Vita async presentation worker could not start\n");
    closeSwapWorker();
}
