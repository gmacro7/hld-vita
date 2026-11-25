#include "present.h"

#include "gettime.h"
#include "log.h"
#include <psp2/kernel/threadmgr.h>
#include <stdio.h>
#include <stdlib.h>

// The assembly wrappers check this before any main-thread call into VitaGL.
unsigned vitaPresentPending;

static struct {
    SceUID mainThread;
    SceUID worker;
    SceUID ready;
    SceUID done;
    bool stopping;
    unsigned submitted;
    unsigned waited;
    uint64_t waitNanos;
    void (*submit)(void);
    void (*finished)(void);
} present = {.mainThread = -1, .worker = -1, .ready = -1, .done = -1};

__attribute__((used, externally_visible))
void vitaWaitForPresent(void) {
    if (!__atomic_load_n(&vitaPresentPending, __ATOMIC_RELAXED) || sceKernelGetThreadId() != present.mainThread) return;
    const uint64_t start = nowNanos();
    int result = sceKernelWaitSema(present.done, 1, NULL);
    if (result < 0) {
        logError("Vita display submission wait failed: %08x\n", (unsigned)result);
        abort();
    }
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    __atomic_store_n(&vitaPresentPending, 0, __ATOMIC_RELAXED);
    ++present.waited;
    present.waitNanos += nowNanos() - start;
    present.finished();
}

static int submitFrames(SceSize size, void* arg) {
    (void)size;
    (void)arg;
    while (sceKernelWaitSema(present.ready, 1, NULL) >= 0) {
        if (__atomic_load_n(&present.stopping, __ATOMIC_ACQUIRE)) break;
        present.submit();
        __atomic_thread_fence(__ATOMIC_RELEASE);
        sceKernelSignalSema(present.done, 1);
    }
    return 0;
}

void VitaPresent_stop(void) {
    if (present.worker >= 0) {
        vitaWaitForPresent();
        __atomic_store_n(&present.stopping, true, __ATOMIC_RELEASE);
        sceKernelSignalSema(present.ready, 1);
        sceKernelWaitThreadEnd(present.worker, NULL, NULL);
        sceKernelDeleteThread(present.worker);
        present.worker = -1;
    }
    if (present.ready >= 0) sceKernelDeleteSema(present.ready);
    if (present.done >= 0) sceKernelDeleteSema(present.done);
    present.ready = present.done = -1;
}

void VitaPresent_start(void (*submit)(void), void (*finished)(void)) {
    FILE* serial = fopen("ux0:data/hld-vm/serial-present", "r");
    if (serial != NULL) {
        fclose(serial);
        return;
    }

    present.mainThread = sceKernelGetThreadId();
    present.submit = submit;
    present.finished = finished;
    present.stopping = false;
    present.submitted = present.waited = 0;
    present.waitNanos = 0;
    present.ready = sceKernelCreateSema("HldPresentReady", 0, 0, 1, NULL);
    present.done = sceKernelCreateSema("HldPresentDone", 0, 0, 1, NULL);
    if (present.ready >= 0 && present.done >= 0) {
        present.worker = sceKernelCreateThread("HldDisplaySubmit", submitFrames, 96, 0x8000, 0, 0, NULL);
        if (present.worker >= 0 && sceKernelStartThread(present.worker, 0, NULL) >= 0) {
            logInfo("Vita threaded display submission enabled\n");
            return;
        }
        if (present.worker >= 0) sceKernelDeleteThread(present.worker);
        present.worker = -1;
    }
    logWarn("Vita display submission worker could not start\n");
    VitaPresent_stop();
}

bool VitaPresent_queue(int frame) {
    vitaWaitForPresent();
    if (present.worker < 0) return false;
    if (frame > 0 && frame % 90 == 0) {
        logInfo("Vita async frames=%d submitted=%u waited=%u barrier_ms=%.3f\n",
                frame, present.submitted, present.waited, present.waitNanos / (90.0 * 1e6));
        present.submitted = present.waited = 0;
        present.waitNanos = 0;
    }
    if (frame <= 180) return false;

    __atomic_store_n(&vitaPresentPending, 1, __ATOMIC_RELEASE);
    int result = sceKernelSignalSema(present.ready, 1);
    if (result < 0) {
        __atomic_store_n(&vitaPresentPending, 0, __ATOMIC_RELAXED);
        logWarn("Vita display submission failed: %08x\n", (unsigned)result);
        return false;
    }
    ++present.submitted;
    return true;
}
