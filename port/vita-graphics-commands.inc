/* Ordered packets share the existing single VitaGL submission worker. */

/* GL declarations */
extern void* __real_vglAllocFromScratch(size_t bytes);
extern void __real_vglBufferData(GLenum target, const GLvoid* data);
extern void __real_glDrawElements(GLenum mode, GLsizei count, GLenum type, const GLvoid* indices);
extern void __real_glDrawArrays(GLenum mode, GLint first, GLsizei count);

enum VitaGraphicsCommandKind {
    CMD_GEOMETRY,
/* GL command kinds */
};

typedef struct {
    enum VitaGraphicsCommandKind kind;
    union {
/* GL command arguments */
        struct {
            size_t offset, bytes;
            GLuint vbo, vao;
            GLsizei count;
            bool indexed;
        } geometry;
    } args;
} VitaGraphicsCommand;

enum { VITA_GRAPHICS_CHUNK_COMMANDS = 32, VITA_GRAPHICS_CHUNK_BYTES = 128 * 1024 };
typedef struct {
    size_t count, bytes;
    unsigned geometryCount;
    VitaGraphicsCommand commands[VITA_GRAPHICS_CHUNK_COMMANDS];
    unsigned char vertices[VITA_GRAPHICS_CHUNK_BYTES];
} VitaGraphicsChunk;

static VitaGraphicsChunk graphicsChunks[2];
static VitaGraphicsChunk* graphicsRecording = &graphicsChunks[0];
volatile bool hldVitaGraphicsRecording;
static bool insideGraphicsDrain;
static unsigned queuedGraphicsChunks, queuedGraphicsCommands, queuedGeometry, recordedGeometry, drainedGraphicsChunks;
static uint64_t graphicsFenceNanos;

static void executeGraphicsCommands(void* data) {
    const VitaGraphicsChunk* chunk = data;
    for (size_t i = 0; i < chunk->count; ++i) {
        const VitaGraphicsCommand* command = &chunk->commands[i];
        switch (command->kind) {
            case CMD_GEOMETRY: {
                const __typeof__(command->args.geometry)* geometry = &command->args.geometry;
                __real_glBindBuffer(GL_ARRAY_BUFFER, geometry->vbo);
                void* scratch = __real_vglAllocFromScratch(geometry->bytes);
                if (scratch == NULL) {
                    logError("GL: unable to reserve %zu bytes for a Vita vertex batch\n", geometry->bytes);
                    abort();
                }
                memcpy(scratch, chunk->vertices + geometry->offset, geometry->bytes);
                __real_vglBufferData(GL_ARRAY_BUFFER, scratch);
                __real_glBindVertexArray(geometry->vao);
                if (geometry->indexed) __real_glDrawElements(GL_TRIANGLES, geometry->count, GL_UNSIGNED_SHORT, NULL);
                else __real_glDrawArrays(GL_TRIANGLES, 0, geometry->count);
                break;
            }
/* GL command cases */
        }
    }
}

static void waitForGraphicsChunk(void) {
    if (!hldSwapPending) return;
    const uint64_t start = nowNanos();
    hldWaitForSwap();
    graphicsFenceNanos += nowNanos() - start;
}

static void executeRecordedGraphicsOnMain(void) {
    if (graphicsRecording->count == 0) return;
    insideGraphicsDrain = true;
    executeGraphicsCommands(graphicsRecording);
    insideGraphicsDrain = false;
    graphicsRecording->count = graphicsRecording->bytes = 0;
    graphicsRecording->geometryCount = 0;
    ++drainedGraphicsChunks;
}

// Return-value calls, resource changes and uncopied pointers stay synchronous.
// The graphics library can call its own entry points while executing a chunk.
#if defined(__GNUC__) && !defined(__clang__)
__attribute__((externally_visible))
#endif
__attribute__((used))
void hldDrainVitaGraphics(void) {
    if (sceKernelGetThreadId() != swapMain || insideGraphicsDrain) return;
    waitForGraphicsChunk();
    executeRecordedGraphicsOnMain();
}

static void submitGraphicsChunk(void) {
    waitForGraphicsChunk();
    if (hldQueueVitaBatch(executeGraphicsCommands, graphicsRecording)) {
        ++queuedGraphicsChunks;
        queuedGraphicsCommands += graphicsRecording->count;
        queuedGeometry += graphicsRecording->geometryCount;
        graphicsRecording = &graphicsChunks[graphicsRecording == &graphicsChunks[0] ? 1 : 0];
        graphicsRecording->count = graphicsRecording->bytes = 0;
        graphicsRecording->geometryCount = 0;
    } else {
        executeRecordedGraphicsOnMain();
    }
}

static VitaGraphicsCommand* reserveGraphicsCommand(enum VitaGraphicsCommandKind kind, size_t bytes) {
    if (!hldVitaGraphicsRecording || bytes > VITA_GRAPHICS_CHUNK_BYTES) return NULL;
    if (graphicsRecording->count == VITA_GRAPHICS_CHUNK_COMMANDS ||
        bytes > VITA_GRAPHICS_CHUNK_BYTES - graphicsRecording->bytes) {
        submitGraphicsChunk();
    }
    VitaGraphicsCommand* command = &graphicsRecording->commands[graphicsRecording->count++];
    command->kind = kind;
    return command;
}

bool hldRecordVitaGeometry(const void* data, size_t bytes, GLuint vbo, GLuint vao, GLsizei count, bool indexed) {
    VitaGraphicsCommand* command = reserveGraphicsCommand(CMD_GEOMETRY, bytes);
    if (command == NULL) return false;
    command->args.geometry = (__typeof__(command->args.geometry)){
        graphicsRecording->bytes, bytes, vbo, vao, count, indexed
    };
    memcpy(graphicsRecording->vertices + graphicsRecording->bytes, data, bytes);
    graphicsRecording->bytes += bytes;
    ++graphicsRecording->geometryCount;
    ++recordedGeometry;
    return true;
}

void hldBeginVitaGraphics(void) {
    if (!hldVitaBatchWorkerEnabled()) return;
    // A pending present may have an exact-frame capture callback. Complete it
    // before enabling recording so the callback sees only the previous frame.
    hldWaitForSwap();
    hldVitaGraphicsRecording = true;
}

void hldEndVitaGraphics(void) {
    if (!hldVitaGraphicsRecording) return;
    hldDrainVitaGraphics();
    hldVitaGraphicsRecording = false;
}

void hldReportVitaGraphics(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita graphics packets frame=%d enabled=%d queued=%u commands=%u geometry=%u queued_geometry=%u drains=%u fence_ms=%.3f\n",
            frame, (int)hldVitaBatchWorkerEnabled(), queuedGraphicsChunks, queuedGraphicsCommands,
            recordedGeometry, queuedGeometry, drainedGraphicsChunks, graphicsFenceNanos / 90e6);
    queuedGraphicsChunks = queuedGraphicsCommands = queuedGeometry = recordedGeometry = drainedGraphicsChunks = 0;
    graphicsFenceNanos = 0;
}
