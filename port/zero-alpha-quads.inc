typedef struct {
    GLModernRenderer* renderer;
    int32_t shaderIndex;
    GLint luminosity;
    uint32_t atlasIndex;
    bool enabled;
} ZeroAlphaQuads;

static ZeroAlphaQuads zeroAlphaQuads;
static unsigned zeroAlphaQuadsRemoved;
#if defined(HLD_SKIP_TRANSPARENT_BATCHES)
static bool transparentBatchesEnabled;
static unsigned transparentBatchesSkipped;
#endif

static uint32_t shaderTextHash(const char* source) {
    if (source == nullptr) return 0;
    uint32_t hash = UINT32_C(2166136261);
    for (const unsigned char* c = (const unsigned char*)source; *c != 0; ++c) {
        hash = (hash ^ *c) * UINT32_C(16777619);
    }
    return hash;
}

static void bindZeroAlphaQuads(GLModernRenderer* modernGl, int32_t index, const Shader* shader) {
    const char* vertex = modernGl->isGLES ? shader->glslES_Vertex : shader->glsl_Vertex;
    const char* fragment = modernGl->isGLES ? shader->glslES_Fragment : shader->glsl_Fragment;
    const uint32_t vertexHash = modernGl->isGLES ? 0x4bee0504u : 0x51917264u;
    const uint32_t fragmentHash = modernGl->isGLES ? 0x1c88b7ceu : 0x18427866u;
    if (shaderTextHash(vertex) != vertexHash || shaderTextHash(fragment) != fragmentHash) return;
    const GLint luminosity = glGetUniformLocation(modernGl->gmlShaders[index].shaderId, "luminosity");
    if (luminosity < 0) return;
    bool enabled = true;
#ifdef HLD_COMPARE_ZERO_ALPHA_QUADS
    FILE* flag = fopen("ux0:data/hld-vm/skip-zero-alpha-quads", "r");
    enabled = flag != nullptr;
    if (flag != nullptr) fclose(flag);
#endif
    zeroAlphaQuads = (ZeroAlphaQuads){modernGl, index, luminosity, UINT32_MAX, enabled};
    logInfo("GL: certified zero-alpha quad path enabled=%d\n", (int)enabled);
#ifdef HLD_SKIP_TRANSPARENT_BATCHES
#ifdef PLATFORM_VITA
    FILE* batchFlag = fopen("ux0:data/hld-vm/skip-transparent-batches", "r");
    transparentBatchesEnabled = batchFlag != nullptr;
    if (batchFlag != nullptr) fclose(batchFlag);
#else
    transparentBatchesEnabled = true;
#endif
#endif
}

#ifdef HLD_COMPARE_ZERO_ALPHA_QUADS
void hldReportZeroAlphaQuads(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita zero-alpha quads frame=%d enabled=%d removed=%u\n",
            frame, (int)zeroAlphaQuads.enabled, zeroAlphaQuadsRemoved);
    zeroAlphaQuadsRemoved = 0;
}
#endif

static bool zeroAlphaQuad(const GlVertex* vertices) {
    if ((vertices[0].a | vertices[1].a | vertices[2].a | vertices[3].a) != 0) return false;
    for (int32_t i = 0; i < VERTICES_PER_QUAD; ++i) {
        const GlVertex* vertex = &vertices[i];
        if (!isfinite(vertex->x) || !isfinite(vertex->y) || !isfinite(vertex->z) ||
            !isfinite(vertex->u) || !isfinite(vertex->v)) return false;
    }
    return true;
}

static bool zeroAlphaUsesAtlas(const GLRenderer* gl, GLuint texture) {
    if (texture == 0) return false;
    if (zeroAlphaQuads.atlasIndex < gl->textureCount && gl->glTextures[zeroAlphaQuads.atlasIndex] == texture) return true;
    for (uint32_t i = 0; i < gl->textureCount; ++i) {
        if (gl->glTextures[i] != texture) continue;
        zeroAlphaQuads.atlasIndex = i;
        return true;
    }
    return false;
}

static bool zeroAlphaLuminosityInRange(const GLModernRenderer* modernGl) {
#ifdef PLATFORM_VITA
    if (modernGl->premultiplyShaderIndex != zeroAlphaQuads.shaderIndex) return false;
    const float luminosity = modernGl->premultiplyLuminosity;
#else
    float luminosity = 0.0f;
    glGetUniformfv(modernGl->gmlShaders[zeroAlphaQuads.shaderIndex].shaderId, zeroAlphaQuads.luminosity, &luminosity);
#endif
    return luminosity >= 0.0f && luminosity <= 1.0f;
}

static bool zeroAlphaBatchKeepsTarget(const GLModernRenderer* modernGl) {
    const GLRenderer* gl = &modernGl->base;
    if (!zeroAlphaUsesAtlas(gl, modernGl->currentTextureId) || !glPremultipliedZeroIsIdentity() ||
        !zeroAlphaLuminosityInRange(modernGl)) return false;
    const GLenum format = GLCommon_surfaceInternalFormat(gl);
    return format == GL_RGBA || format == GL_RGBA8;
}

#ifdef HLD_SKIP_TRANSPARENT_BATCHES
static bool skipTransparentQuadBatch(const GLModernRenderer* modernGl) {
    if (!transparentBatchesEnabled || zeroAlphaQuads.renderer != modernGl ||
        modernGl->base.base.currentShader != zeroAlphaQuads.shaderIndex) return false;
    for (int32_t i = 0; i < modernGl->batchCount; ++i) {
        if (!zeroAlphaQuad(modernGl->base.vertexData + i * VERTICES_PER_QUAD)) return false;
    }
    if (!zeroAlphaBatchKeepsTarget(modernGl)) return false;
    ++transparentBatchesSkipped;
#ifndef PLATFORM_VITA
    if (transparentBatchesSkipped == 100) logInfo("GL: skipped 100 wholly transparent quad batches\n");
#endif
    return true;
}

#ifdef PLATFORM_VITA
void hldReportTransparentBatches(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita transparent batches frame=%d enabled=%d skipped=%u\n", frame,
            (int)transparentBatchesEnabled, transparentBatchesSkipped);
    transparentBatchesSkipped = 0;
}
#endif
#endif

static void removeZeroAlphaQuads(GLModernRenderer* modernGl) {
    GLRenderer* gl = &modernGl->base;
    if (!zeroAlphaQuads.enabled || zeroAlphaQuads.renderer != modernGl ||
        gl->base.currentShader != zeroAlphaQuads.shaderIndex ||
        modernGl->batchType != BATCHTYPE_QUAD || modernGl->batchCount < 2) return;

    GlVertex* vertices = gl->vertexData;
    const int32_t count = modernGl->batchCount;
    int32_t firstZero = 0;
    for (; firstZero < count; ++firstZero) if (zeroAlphaQuad(vertices + firstZero * VERTICES_PER_QUAD)) break;
    if (firstZero == count) return;
    // The authored shader premultiplies RGB by alpha before applying luminosity.
    // A normalized atlas sample and zero interpolated alpha produce transparent black.
    if (!zeroAlphaBatchKeepsTarget(modernGl)) return;

    int32_t write = firstZero;
    for (int32_t i = firstZero + 1; i < count; ++i) {
        const GlVertex* source = vertices + i * VERTICES_PER_QUAD;
        if (zeroAlphaQuad(source)) continue;
        memcpy(vertices + write * VERTICES_PER_QUAD, source, VERTICES_PER_QUAD * sizeof(*vertices));
        ++write;
    }
    // Keep the original GL bindings for a batch containing only transparent quads.
    if (write == 0) return;
    modernGl->batchCount = write;
    const unsigned removed = (unsigned)(count - write);
    zeroAlphaQuadsRemoved += removed;
#ifndef HLD_COMPARE_ZERO_ALPHA_QUADS
    if (zeroAlphaQuadsRemoved >= 100 && zeroAlphaQuadsRemoved - removed < 100) {
        logInfo("GL: removed 100 zero-alpha quads\n");
    }
#endif
}
