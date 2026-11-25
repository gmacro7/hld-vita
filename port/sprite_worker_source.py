"""Hand completed default-sprite batches to the serialized Vita graphics worker."""


def replace_once(code, before, after):
    assert code.count(before) == 1, before
    return code.replace(before, after)


def with_sprite_worker(code):
    declarations = "static uint64_t swapWaitNanos;"
    code = replace_once(code, declarations, declarations + r'''
static bool batchWorkerEnabled;
static void (*batchOperation)(void*);
static void* batchPacket;
static unsigned batchSubmissions;
static uint64_t batchWaitNanos;

bool hldVitaBatchWorkerEnabled(void) {
    return batchWorkerEnabled && swapWorker >= 0 && activeRunner != NULL && activeRunner->frameCount > 180;
}''')
    finish = "    hldSwapPending = 0;\n    ++swapWaited;"
    code = replace_once(code, finish, '''    hldSwapPending = 0;
    if (__atomic_load_n(&batchOperation, __ATOMIC_RELAXED) != NULL) {
        __atomic_store_n(&batchOperation, NULL, __ATOMIC_RELAXED);
        batchWaitNanos += nowNanos() - start;
        return;
    }
    ++swapWaited;''')
    thread = "static int runSwapWorker(SceSize size, void* arg) {"
    code = replace_once(code, thread, r'''bool hldQueueVitaBatch(void (*submit)(void*), void* packet) {
    if (!hldVitaBatchWorkerEnabled() || hldSwapPending) return false;
    batchPacket = packet;
    __atomic_store_n(&batchOperation, submit, __ATOMIC_RELEASE);
    hldSwapPending = 1;
    if (sceKernelSignalSema(swapStart, 1) < 0) {
        hldSwapPending = 0;
        __atomic_store_n(&batchOperation, NULL, __ATOMIC_RELAXED);
        return false;
    }
    ++batchSubmissions;
    return true;
}

void hldReportVitaBatchWorker(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita sprite worker frame=%d enabled=%d batches=%u wait_ms=%.3f\n",
            frame, (int)hldVitaBatchWorkerEnabled(), batchSubmissions, batchWaitNanos / 90e6);
    batchSubmissions = 0;
    batchWaitNanos = 0;
}

''' + thread)
    code = replace_once(code, "        swapNow();\n        sceKernelSignalSema(swapDone, 1);", '''        void (*submit)(void*) = __atomic_load_n(&batchOperation, __ATOMIC_ACQUIRE);
        if (submit == NULL) swapNow();
        else submit(batchPacket);
        sceKernelSignalSema(swapDone, 1);''')
    return replace_once(code, '    swapMain = sceKernelGetThreadId();', r'''    FILE* batchFlag = fopen("ux0:data/hld-vm/native-sprite-worker", "r");
    batchWorkerEnabled = batchFlag != NULL;
    if (batchFlag != NULL) fclose(batchFlag);
    swapMain = sceKernelGetThreadId();''')


def with_sprite_packets(code):
    flush = "static void flushBatch(GLRenderer* gl) {"
    code = replace_once(code, flush, r'''#ifdef PLATFORM_VITA
extern bool hldVitaBatchWorkerEnabled(void);
extern bool hldQueueVitaBatch(void (*submit)(void*), void* packet);
extern void hldWaitForSwap(void);

typedef struct {
    const GlVertex* vertices;
    size_t bytes;
    GLuint buffer, vao, restoreProgram;
    int32_t indexCount;
} VitaSpritePacket;

static VitaSpritePacket spritePacket;
static GLRenderer* spritePacketOwner;
static GlVertex* spareSpriteVertices;

static void drawVitaSpritePacket(void* data) {
    const VitaSpritePacket* packet = data;
    glBindBuffer(GL_ARRAY_BUFFER, packet->buffer);
    void* mapped = vglAllocFromScratch(packet->bytes);
    if (mapped == NULL) {
        logError("GL: unable to reserve %zu bytes for a Vita vertex batch\n", packet->bytes);
        abort();
    }
    memcpy(mapped, packet->vertices, packet->bytes);
    vglBufferData(GL_ARRAY_BUFFER, mapped);
    glBindVertexArray(packet->vao);
    glDrawElements(GL_TRIANGLES, packet->indexCount, GL_UNSIGNED_SHORT, nullptr);
    if (packet->restoreProgram != 0) glUseProgram(packet->restoreProgram);
}

static bool queueVitaSpritePacket(GLRenderer* gl, size_t bytes, int32_t indexCount, GLuint restoreProgram) {
    GLModernRenderer* modernGl = (GLModernRenderer*) gl;
    if (modernGl->batchCount < 24 || !hldVitaBatchWorkerEnabled() || !hasVAO() ||
        gl->base.currentShader != -1 || modernGl->batchLocalUV || modernGl->batchType != BATCHTYPE_QUAD) return false;
    if (spritePacketOwner != NULL && spritePacketOwner != gl) return false;

    hldWaitForSwap();
    if (spareSpriteVertices == NULL) {
        spareSpriteVertices = safeMalloc(MAX_QUADS * VERTICES_PER_QUAD * sizeof(GlVertex));
        spritePacketOwner = gl;
    }
    spritePacket = (VitaSpritePacket){gl->vertexData, bytes, modernGl->vbo, modernGl->vao,
                                    restoreProgram, indexCount};
    if (!hldQueueVitaBatch(drawVitaSpritePacket, &spritePacket)) return false;

    // The worker reads the completed vertex array. Game draw scripts can fill
    // the other allocation until a graphics call joins the packet.
    GlVertex* submitted = gl->vertexData;
    gl->vertexData = spareSpriteVertices;
    spareSpriteVertices = submitted;
    modernGl->batchCount = 0;
    return true;
}
#endif

''' + flush)
    code = replace_once(code, "    glBindBuffer(GL_ARRAY_BUFFER, modernGl->vbo);\n    int32_t verticesPerItem",
                        "    int32_t verticesPerItem")
    anchor = "    // Each submitted batch owns only the mapped scratch bytes it actually uses."
    code = replace_once(code, anchor, '''    if (queueVitaSpritePacket(gl, vertexBytes, indexCount, restoreProgram)) return;
    glBindBuffer(GL_ARRAY_BUFFER, modernGl->vbo);
''' + anchor)
    anchor = "    glBufferData(GL_ARRAY_BUFFER, vertexBytes, gl->vertexData, GL_STREAM_DRAW);"
    code = replace_once(code, anchor, "    glBindBuffer(GL_ARRAY_BUFFER, modernGl->vbo);\n" + anchor)
    destroy = "static void glDestroy(Renderer* renderer) {"
    return replace_once(code, destroy, destroy + r'''
#ifdef PLATFORM_VITA
    if (spritePacketOwner == (GLRenderer*) renderer) {
        hldWaitForSwap();
        free(spareSpriteVertices);
        spareSpriteVertices = NULL;
        spritePacketOwner = NULL;
    }
#endif''')
