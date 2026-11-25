enum {
    SPARSE_WIDTH = 480, SPARSE_HEIGHT = 270, SPARSE_TILE = 16,
    SPARSE_COLUMNS = SPARSE_WIDTH / SPARSE_TILE, SPARSE_ROWS = (SPARSE_HEIGHT + SPARSE_TILE - 1) / SPARSE_TILE,
    SPARSE_CLIP_LIMIT = 8
};

typedef struct {
    GLRenderer* renderer;
    int32_t surface;
    GLuint framebuffer;
    uint32_t rows[SPARSE_ROWS];
    bool known;
} SparseCoverage;

typedef struct { int32_t x, y, width, height; } SparseClip;

static SparseCoverage sparseCoverage;
static SparseClip sparseClips[SPARSE_CLIP_LIMIT];
static int32_t sparseClipCount;
static bool sparseClipPending;
static unsigned sparseClipPixelsSaved;
static GLuint sparseClipFramebuffer;
static GLint sparseClipViewport[4];
static Matrix4f sparseClipProjection;
static bool sparseUnexpectedUniform;
static unsigned sparseCompositeCount, sparsePixelsSaved;
#ifdef HLD_SPARSE_TRANSPARENT_COVERAGE
static unsigned sparseTransparentQuads;
#endif

static bool sparseCompositeEnabled(void) {
#ifdef HLD_COMPARE_SPARSE_COMPOSITE
    static int enabled = -1;
    if (enabled < 0) {
        FILE* flag = fopen("ux0:data/hld-vm/sparse-composite", "r");
        enabled = flag != nullptr;
        if (flag != nullptr) fclose(flag);
    }
    return enabled != 0;
#else
    return true;
#endif
}

#ifdef HLD_COMPARE_SPARSE_COMPOSITE
void hldReportSparseComposite(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita sparse composite frame=%d enabled=%d draws=%u pixels=%u\n", frame,
            (int)sparseCompositeEnabled(), sparseCompositeCount, sparsePixelsSaved);
    sparseCompositeCount = sparsePixelsSaved = 0;
#ifdef HLD_SPARSE_TRANSPARENT_COVERAGE
    logInfo("Vita sparse coverage frame=%d ignored=%u\n", frame, sparseTransparentQuads);
    sparseTransparentQuads = 0;
#endif
}
#endif

static GLuint sparseBoundFramebuffer(void) {
    GLint bound = 0;
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &bound);
    return (GLuint)bound;
}

static bool sparseIsTrackedTarget(GLRenderer* gl) {
    return gl == sparseCoverage.renderer && sparseCoverage.framebuffer != 0 &&
           sparseBoundFramebuffer() == sparseCoverage.framebuffer;
}

static void sparseInvalidateCurrent(GLRenderer* gl) {
    if (sparseCoverage.known && sparseIsTrackedTarget(gl)) sparseCoverage.known = false;
}

static void sparseForgetSurface(GLRenderer* gl, int32_t surface) {
    if (gl == sparseCoverage.renderer && surface == sparseCoverage.surface) sparseCoverage = (SparseCoverage){0};
}

static void sparseTrackClear(GLRenderer* gl, float alpha) {
    if (!sparseIsTrackedTarget(gl)) return;
    sparseCoverage.known = false;
    if (alpha != 0.0f || glIsEnabled(GL_SCISSOR_TEST)) return;
    GLboolean write[4] = {0};
    glGetBooleanv(GL_COLOR_WRITEMASK, write);
    if (!write[3]) return;
    memset(sparseCoverage.rows, 0, sizeof(sparseCoverage.rows));
    sparseCoverage.known = true;
}

static bool sparseAffine(const float* m) {
    return m[3] == 0.0f && m[7] == 0.0f && m[11] == 0.0f && m[15] == 1.0f;
}

static bool sparseMarkQuad(const float* m, const GlVertex* vertices) {
    float left = INFINITY, right = -INFINITY, low = INFINITY, high = -INFINITY;
    for (int32_t i = 0; i < VERTICES_PER_QUAD; ++i) {
        const GlVertex* v = &vertices[i];
        const float clipX = m[0] * v->x + m[4] * v->y + m[8] * v->z + m[12];
        const float clipY = m[1] * v->x + m[5] * v->y + m[9] * v->z + m[13];
        if (!isfinite(clipX) || !isfinite(clipY) || fabsf(clipX) > 32.0f || fabsf(clipY) > 32.0f) return false;
        const float x = (clipX + 1.0f) * (SPARSE_WIDTH * 0.5f);
        const float y = (1.0f - clipY) * (SPARSE_HEIGHT * 0.5f);
        left = fminf(left, x);
        right = fmaxf(right, x);
        low = fminf(low, y);
        high = fmaxf(high, y);
    }
    // Cover rasterization edge rounding and the footprint of a filtered sample.
    left = fmaxf(left - 2.0f, 0.0f);
    low = fmaxf(low - 2.0f, 0.0f);
    right = fminf(right + 2.0f, SPARSE_WIDTH);
    high = fminf(high + 2.0f, SPARSE_HEIGHT);
    if (left >= right || low >= high) return true;
    const int32_t x0 = (int32_t)floorf(left / SPARSE_TILE);
    const int32_t x1 = (int32_t)ceilf(right / SPARSE_TILE);
    const int32_t y0 = (int32_t)floorf(low / SPARSE_TILE);
    const int32_t y1 = (int32_t)ceilf(high / SPARSE_TILE);
    const uint32_t mask = ((UINT32_C(1) << (x1 - x0)) - 1) << x0;
    for (int32_t row = y0; row < y1; ++row) sparseCoverage.rows[row] |= mask;
    return true;
}

static void sparseTrackBatch(GLModernRenderer* modernGl) {
    GLRenderer* gl = &modernGl->base;
    if (!sparseCoverage.known || !sparseIsTrackedTarget(gl)) return;
    if (sparseUnexpectedUniform || zeroAlphaQuads.renderer != modernGl || gl->base.currentShader != zeroAlphaQuads.shaderIndex ||
        modernGl->batchType != BATCHTYPE_QUAD) {
        sparseCoverage.known = false;
        return;
    }
    GLint viewport[4] = {0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    const float* m = gl->base.gmlMatrices[MATRIX_WORLD_VIEW_PROJECTION].m;
    if (!sparseAffine(m) || viewport[0] != 0 || viewport[1] != 0 ||
        viewport[2] != SPARSE_WIDTH || viewport[3] != SPARSE_HEIGHT) {
        sparseCoverage.known = false;
        return;
    }
#ifdef HLD_SPARSE_TRANSPARENT_COVERAGE
    int32_t transparentKeepsTarget = -1;
#endif
    for (int32_t i = 0; i < modernGl->batchCount; ++i) {
        const GlVertex* quad = gl->vertexData + i * VERTICES_PER_QUAD;
#ifdef HLD_SPARSE_TRANSPARENT_COVERAGE
        // Singleton and wholly transparent batches retain their original submissions.
        // The same blend proof permits omitting their bounds from this mask.
        if (zeroAlphaQuad(quad)) {
            if (transparentKeepsTarget < 0) transparentKeepsTarget = (int32_t)zeroAlphaBatchKeepsTarget(modernGl);
            if (transparentKeepsTarget != 0) {
                ++sparseTransparentQuads;
                continue;
            }
        }
#endif
        if (sparseMarkQuad(m, quad)) continue;
        sparseCoverage.known = false;
        return;
    }
}

static bool sparseIntegerScale(float value, int32_t* result) {
    if (!isfinite(value) || value < 0.99f || value > 4.01f) return false;
    *result = (int32_t)roundf(value);
    return fabsf(value - *result) < 0.00001f;
}

static bool sparseBuildClips(int32_t scaleX, int32_t scaleY) {
    uint32_t rows[SPARSE_ROWS];
    memcpy(rows, sparseCoverage.rows, sizeof(rows));
    // A linear sample at an edge may also read the opposite edge with repeat.
    rows[0] = rows[SPARSE_ROWS - 1] = rows[0] | rows[SPARSE_ROWS - 1];
    const uint32_t edgeColumns = UINT32_C(1) | (UINT32_C(1) << (SPARSE_COLUMNS - 1));
    for (int32_t row = 0; row < SPARSE_ROWS; ++row) {
        if (rows[row] & edgeColumns) rows[row] |= edgeColumns;
    }
    sparseClipCount = 0;
    unsigned area = 0;
    for (int32_t y0 = 0; y0 < SPARSE_ROWS; ++y0) {
        while (rows[y0] != 0) {
            if (sparseClipCount == SPARSE_CLIP_LIMIT) return false;
            const int32_t x0 = __builtin_ctz(rows[y0]);
            const int32_t tilesWide = __builtin_ctz(~(rows[y0] >> x0));
            const uint32_t mask = ((UINT32_C(1) << tilesWide) - 1) << x0;
            int32_t y1 = y0;
            do {
                rows[y1] &= ~mask;
                ++y1;
            } while (y1 < SPARSE_ROWS && (rows[y1] & mask) == mask);
            const int32_t endY = y1 < SPARSE_ROWS ? y1 * SPARSE_TILE : SPARSE_HEIGHT;
            const int32_t width = tilesWide * SPARSE_TILE;
            const int32_t height = endY - y0 * SPARSE_TILE;
            sparseClips[sparseClipCount++] = (SparseClip){
                x0 * SPARSE_TILE * scaleX, y0 * SPARSE_TILE * scaleY, width * scaleX, height * scaleY
            };
            area += (unsigned)(width * height);
        }
    }
    if (sparseClipCount == 0 || area * 2 >= SPARSE_WIDTH * SPARSE_HEIGHT) return false;
    sparseClipPixelsSaved = (SPARSE_WIDTH * SPARSE_HEIGHT - area) * (unsigned)(scaleX * scaleY);
    return true;
}

static bool sparsePrepareComposite(GLRenderer* gl, int32_t surface, int32_t width, int32_t height,
                                   int32_t left, int32_t top, int32_t cropWidth, int32_t cropHeight,
                                   float x, float y, float xscale, float yscale, float angle, float alpha) {
    if (sparseUnexpectedUniform || width != SPARSE_WIDTH || height != SPARSE_HEIGHT ||
        gl->base.currentShader != zeroAlphaQuads.shaderIndex ||
        zeroAlphaQuads.renderer != (GLModernRenderer*)gl || !sparseCompositeEnabled()) return false;
    if (sparseCoverage.framebuffer == 0) {
        sparseCoverage = (SparseCoverage){.renderer = gl, .surface = surface, .framebuffer = gl->surfaces[surface]};
        return false;
    }
    if (!sparseCoverage.known || sparseCoverage.renderer != gl || sparseCoverage.surface != surface ||
        sparseCoverage.framebuffer != gl->surfaces[surface] ||
        left != 0 || top != 0 || cropWidth != width || cropHeight != height ||
        x != 0.0f || y != 0.0f || angle != 0.0f || !(alpha > 0.0f && alpha <= 1.0f)) return false;
    const GLenum format = GLCommon_surfaceInternalFormat(gl);
    if (format != GL_RGBA && format != GL_RGBA8) return false;
    int32_t scaleX, scaleY;
    if (!sparseIntegerScale(xscale, &scaleX) || !sparseIntegerScale(yscale, &scaleY)) return false;
    const GLuint framebuffer = sparseBoundFramebuffer();
    if (framebuffer == sparseCoverage.framebuffer) return false;

    const float* m = gl->base.gmlMatrices[MATRIX_WORLD_VIEW_PROJECTION].m;
    if (!sparseAffine(m) || m[1] != 0.0f || m[4] != 0.0f || m[8] != 0.0f || m[9] != 0.0f ||
        m[12] != -1.0f || m[13] != 1.0f) return false;
    GLint viewport[4] = {0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    if (viewport[0] != 0 || viewport[1] != 0 ||
        viewport[2] != width * scaleX || viewport[3] != height * scaleY ||
        !((fabsf(m[0] * viewport[2] - 2.0f) < 0.000001f) &&
          (fabsf(m[5] * viewport[3] + 2.0f) < 0.000001f))) return false;

    if (!sparseBuildClips(scaleX, scaleY)) return false;
    sparseClipFramebuffer = framebuffer;
    memcpy(sparseClipViewport, viewport, sizeof(viewport));
    sparseClipProjection = gl->base.gmlMatrices[MATRIX_WORLD_VIEW_PROJECTION];
    return true;
}

static bool sparseClipStillValid(const GLModernRenderer* modernGl) {
    const GLRenderer* gl = &modernGl->base;
    if (sparseUnexpectedUniform || modernGl != zeroAlphaQuads.renderer ||
        gl->base.currentShader != zeroAlphaQuads.shaderIndex ||
        sparseBoundFramebuffer() != sparseClipFramebuffer || glIsEnabled(GL_SCISSOR_TEST) ||
        !glPremultipliedZeroIsIdentity() || !zeroAlphaLuminosityInRange(modernGl)) return false;
    const Matrix4f* projection = &gl->base.gmlMatrices[MATRIX_WORLD_VIEW_PROJECTION];
    if (memcmp(projection->m, sparseClipProjection.m, sizeof(projection->m)) != 0) return false;
    GLint viewport[4] = {0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    return memcmp(viewport, sparseClipViewport, sizeof(viewport)) == 0;
}

static void sparseRecordComposite(void) {
    sparsePixelsSaved += sparseClipPixelsSaved;
#ifdef PLATFORM_VITA
    ++sparseCompositeCount;
#else
    if (++sparseCompositeCount == 100) {
        logInfo("GL: clipped 100 sparse composites, skipped pixels=%u\n", sparsePixelsSaved);
    }
#endif
}

#ifdef HLD_SPARSE_GEOMETRY
static void sparseExpandComposite(GLModernRenderer* modernGl, bool pending) {
    if (!pending || modernGl->batchType != BATCHTYPE_QUAD || modernGl->batchCount != 1 ||
        !sparseClipStillValid(modernGl)) return;

    GlVertex* vertices = modernGl->base.vertexData;
    const GlVertex original = vertices[0];
    const float width = (float)sparseClipViewport[2];
    const float height = (float)sparseClipViewport[3];
    // Preserve the original affine texture mapping, depth, color, and winding.
    for (int32_t corner = 0; corner < VERTICES_PER_QUAD; ++corner) {
        const bool right = corner == 1 || corner == 2;
        const bool bottom = corner >= 2;
        const GlVertex* vertex = &vertices[corner];
        if (vertex->x != (right ? width : 0.0f) || vertex->y != (bottom ? height : 0.0f) ||
            vertex->u != (float)right || vertex->v != (float)bottom || vertex->z != original.z ||
            vertex->r != original.r || vertex->g != original.g || vertex->b != original.b ||
            vertex->a != original.a) return;
    }

    for (int32_t i = 0; i < sparseClipCount; ++i) {
        const SparseClip* clip = &sparseClips[i];
        for (int32_t corner = 0; corner < VERTICES_PER_QUAD; ++corner) {
            const bool right = corner == 1 || corner == 2;
            const bool bottom = corner >= 2;
            GlVertex vertex = original;
            vertex.x = (float)(clip->x + (right ? clip->width : 0));
            vertex.y = (float)(clip->y + (bottom ? clip->height : 0));
            vertex.u = vertex.x / width;
            vertex.v = vertex.y / height;
            vertices[i * VERTICES_PER_QUAD + corner] = vertex;
        }
    }
    modernGl->batchCount = sparseClipCount;
    sparseRecordComposite();
}
#else
static void sparseDrawElements(const GLModernRenderer* modernGl, int32_t indexCount, bool pending) {
    if (!pending || indexCount != INDICES_PER_QUAD || !sparseClipStillValid(modernGl)) {
        glDrawElements(GL_TRIANGLES, indexCount, GL_UNSIGNED_SHORT, nullptr);
        return;
    }
    sparseRecordComposite();
    GLint original[4] = {0};
    glGetIntegerv(GL_SCISSOR_BOX, original);
    glEnable(GL_SCISSOR_TEST);
    for (int32_t i = 0; i < sparseClipCount; ++i) {
        const SparseClip* clip = &sparseClips[i];
        glScissor(clip->x, clip->y, clip->width, clip->height);
        glDrawElements(GL_TRIANGLES, indexCount, GL_UNSIGNED_SHORT, nullptr);
    }
    glDisable(GL_SCISSOR_TEST);
    glScissor(original[0], original[1], original[2], original[3]);
}
#endif
