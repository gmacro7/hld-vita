static SceUID graphicsReady = -1, graphicsReusable = -1;
static unsigned graphicsPublished, graphicsReleased, graphicsStopIndex;
static bool graphicsStreamActive;
static unsigned graphicsStreams, graphicsBlockedPublishes;
static uint64_t graphicsPublishWaitNanos;

static void releaseGraphicsStream(void) {
    if (graphicsReady >= 0) sceKernelDeleteSema(graphicsReady);
    if (graphicsReusable >= 0) sceKernelDeleteSema(graphicsReusable);
    graphicsReady = graphicsReusable = -1;
}

static bool prepareGraphicsStream(void) {
    graphicsReady = sceKernelCreateSema("HldGraphicsReady", 0, 0, VITA_GRAPHICS_CHUNKS, NULL);
    graphicsReusable = sceKernelCreateSema("HldGraphicsReusable", 0,
                                           VITA_GRAPHICS_CHUNKS - 1, VITA_GRAPHICS_CHUNKS - 1, NULL);
    if (graphicsReady >= 0 && graphicsReusable >= 0) return true;
    releaseGraphicsStream();
    logError("Vita graphics stream could not allocate its semaphores\n");
    return false;
}

static void checkGraphicsStreamSema(int result) {
    if (result < 0) {
        logError("Vita graphics stream semaphore failed: %08x\n", (unsigned)result);
        abort();
    }
}

static unsigned nextGraphicsSlot(unsigned slot) {
    return (slot + 1) % VITA_GRAPHICS_CHUNKS;
}

static void executeGraphicsStream(void* first) {
    unsigned slot = (VitaGraphicsChunk*)first - graphicsChunks;
    for (;;) {
        checkGraphicsStreamSema(sceKernelWaitSema(graphicsReady, 1, NULL));
        if (slot == __atomic_load_n(&graphicsStopIndex, __ATOMIC_ACQUIRE)) return;
        // A producer release makes this chunk visible. An empty stream can
        // only consume a token after the stop index has been published.
        if (slot == __atomic_load_n(&graphicsPublished, __ATOMIC_ACQUIRE)) abort();
        executeGraphicsCommands(&graphicsChunks[slot]);
        slot = nextGraphicsSlot(slot);
        __atomic_store_n(&graphicsReleased, slot, __ATOMIC_RELEASE);
        checkGraphicsStreamSema(sceKernelSignalSema(graphicsReusable, 1));
    }
}

static void finishGraphicsStream(void) {
    if (!graphicsStreamActive) return;
    graphicsStreamActive = false;
    // The producer always holds one unpublished slot. A token marking that
    // index ends the stream after all earlier packets have been executed.
    __atomic_store_n(&graphicsStopIndex, graphicsRecording - graphicsChunks, __ATOMIC_RELEASE);
    checkGraphicsStreamSema(sceKernelSignalSema(graphicsReady, 1));
}

static void submitGraphicsChunk(void) {
    const unsigned slot = graphicsRecording - graphicsChunks;
    if (!graphicsStreamActive) {
        waitForGraphicsChunk();
        __atomic_store_n(&graphicsStopIndex, VITA_GRAPHICS_CHUNKS, __ATOMIC_RELAXED);
        __atomic_store_n(&graphicsPublished, slot, __ATOMIC_RELAXED);
        __atomic_store_n(&graphicsReleased, slot, __ATOMIC_RELAXED);
        if (!hldQueueVitaBatch(executeGraphicsStream, graphicsRecording)) {
            executeRecordedGraphicsOnMain();
            return;
        }
        graphicsStreamActive = true;
        ++graphicsStreams;
    }

    // Reserve the next recording slot before publishing this one. Only a
    // full ring stalls the producer. Fences still join the graphics worker.
    if (sceKernelPollSema(graphicsReusable, 1) < 0) {
        const uint64_t start = nowNanos();
        checkGraphicsStreamSema(sceKernelWaitSema(graphicsReusable, 1, NULL));
        graphicsPublishWaitNanos += nowNanos() - start;
        ++graphicsBlockedPublishes;
    }
    const unsigned next = nextGraphicsSlot(slot);
    if (next == __atomic_load_n(&graphicsReleased, __ATOMIC_ACQUIRE)) abort();
    ++queuedGraphicsChunks;
    queuedGraphicsCommands += graphicsRecording->count;
    queuedGeometry += graphicsRecording->geometryCount;
    __atomic_store_n(&graphicsPublished, next, __ATOMIC_RELEASE);
    checkGraphicsStreamSema(sceKernelSignalSema(graphicsReady, 1));
    graphicsRecording = &graphicsChunks[next];
    graphicsRecording->count = graphicsRecording->bytes = 0;
    graphicsRecording->geometryCount = 0;
}
