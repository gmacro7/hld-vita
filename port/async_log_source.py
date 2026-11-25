"""Vita runtime.log written by a background thread.

Every log line used to be fflush'd to the memory card on the calling thread. During combat the game logs from enemy
hit/death code ("Sword collision was not hit with before", "added state..."), and one flush could block the game
thread 20-130 ms. Lines now go into a ring buffer that a low-priority thread drains every 20 ms; errors still drain
synchronously so a crash keeps its last lines.
"""

ASYNC_LOG = r'''
#include <stdint.h>
#include <string.h>
#include <psp2/kernel/threadmgr.h>
#include <psp2/io/fcntl.h>

#define HLD_LOG_RING (512u * 1024u)
static char hldLogRing[HLD_LOG_RING];
static uint32_t hldLogHead, hldLogTail;  // monotonic byte counters; index = counter % HLD_LOG_RING
static uint32_t hldLogDropped;
static SceKernelLwMutexWork hldLogAppendLock, hldLogDrainLock;
static SceUID hldLogFd = -1, hldLogThread = -1;
static volatile int hldLogStop;

static void hldLogDrain(void) {
    sceKernelLockLwMutex(&hldLogDrainLock, 1, NULL);
    const uint32_t head = __atomic_load_n(&hldLogHead, __ATOMIC_ACQUIRE);
    uint32_t tail = hldLogTail;
    while (tail != head && hldLogFd >= 0) {
        const uint32_t at = tail % HLD_LOG_RING;
        uint32_t bytes = head - tail;
        if (bytes > HLD_LOG_RING - at) bytes = HLD_LOG_RING - at;
        sceIoWrite(hldLogFd, hldLogRing + at, bytes);
        tail += bytes;
    }
    __atomic_store_n(&hldLogTail, tail, __ATOMIC_RELEASE);
    sceKernelUnlockLwMutex(&hldLogDrainLock, 1);
}

static int hldLogWriter(SceSize size, void* arg) {
    (void) size; (void) arg;
    while (!hldLogStop) {
        sceKernelDelayThread(20000);
        if (__atomic_load_n(&hldLogHead, __ATOMIC_ACQUIRE) != hldLogTail) hldLogDrain();
    }
    return 0;
}

static void hldLogAppend(const char* text, uint32_t length) {
    // Never block on the lock: a low-priority thread (page decoder, sound warm-up) preempted while holding it would
    // otherwise stall the game thread for as long as it stays off the CPU. Retry for ~2 ms, then drop the line.
    int tries = 0;
    while (sceKernelTryLockLwMutex(&hldLogAppendLock, 1) < 0) {
        if (++tries > 20) { __atomic_fetch_add(&hldLogDropped, 1, __ATOMIC_RELAXED); return; }
        sceKernelDelayThread(100);
    }
    const uint32_t head = hldLogHead;
    const uint32_t used = head - __atomic_load_n(&hldLogTail, __ATOMIC_ACQUIRE);
    if (length > HLD_LOG_RING - used) {
        ++hldLogDropped;
    } else {
        const uint32_t at = head % HLD_LOG_RING;
        const uint32_t first = length < HLD_LOG_RING - at ? length : HLD_LOG_RING - at;
        memcpy(hldLogRing + at, text, first);
        memcpy(hldLogRing, text + first, length - first);
        __atomic_store_n(&hldLogHead, head + length, __ATOMIC_RELEASE);
    }
    sceKernelUnlockLwMutex(&hldLogAppendLock, 1);
}

void vitaOpenLog(void) {
    sceKernelCreateLwMutex(&hldLogAppendLock, "HldLogAppend", 0, 0, NULL);
    sceKernelCreateLwMutex(&hldLogDrainLock, "HldLogDrain", 0, 0, NULL);
    hldLogFd = sceIoOpen("ux0:data/hld-vm/runtime.log", SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0666);
    hldLogThread = sceKernelCreateThread("HldLogWriter", hldLogWriter, 64, 0x4000, 0, 0, NULL);
    if (hldLogThread >= 0) sceKernelStartThread(hldLogThread, 0, NULL);
    logInfo("Vita log written in background=%d\n", (int) (hldLogThread >= 0));
}

void vitaCloseLog(void) {
    if (hldLogThread >= 0) {
        hldLogStop = 1;
        sceKernelWaitThreadEnd(hldLogThread, NULL, NULL);
        sceKernelDeleteThread(hldLogThread);
        hldLogThread = -1;
    }
    if (hldLogDropped) {
        char note[64];
        const int n = snprintf(note, sizeof(note), "Vita log dropped %u lines\n", (unsigned) hldLogDropped);
        if (n > 0) hldLogAppend(note, (uint32_t) n);
    }
    hldLogDrain();
    if (hldLogFd >= 0) sceIoClose(hldLogFd);
    hldLogFd = -1;
}

void platformLog(const logType type, const char* format, va_list va) {
    if (hldLogFd < 0) return;
    char line[2048];
    int prefix = 0;
    switch (type) {
        case LOG_TYPE_WARNING: prefix = snprintf(line, sizeof(line), "Warning: "); break;
        case LOG_TYPE_ERROR: prefix = snprintf(line, sizeof(line), "Error: "); break;
        case LOG_TYPE_DEBUG: prefix = snprintf(line, sizeof(line), "Debug: "); break;
        case LOG_TYPE_NORMAL: break;
    }
    int length = vsnprintf(line + prefix, sizeof(line) - (size_t) prefix, format, va);
    if (length < 0) return;
    length += prefix;
    if ((size_t) length >= sizeof(line)) length = (int) sizeof(line) - 1;
    hldLogAppend(line, (uint32_t) length);
    if (type == LOG_TYPE_ERROR || hldLogThread < 0) hldLogDrain();
}
'''


def with_async_log(code):
    start = code.index("void vitaOpenLog(void) {")
    body = ASYNC_LOG.strip()
    import os
    if os.environ.get("HLDC_LOG_TIMES") == "1":
        # Diagnostic: every line starts with [t=<ms since launch>], to time boot steps that log nothing themselves.
        plain = "        case LOG_TYPE_NORMAL: break;\n    }\n"
        assert body.count(plain) == 1
        body = body.replace(plain, plain + "    { extern SceUInt64 sceKernelGetProcessTimeWide(void); prefix += snprintf(line + prefix, sizeof(line) - (size_t) prefix, "
                            "\"[t=%u] \", (unsigned) (sceKernelGetProcessTimeWide() / 1000)); }\n")
    code = code[:start] + body + "\n"
    # The frame/room batching helpers above still test vitaLog; it stays NULL so they do nothing.
    assert "static FILE* vitaLog;" in code
    return code
