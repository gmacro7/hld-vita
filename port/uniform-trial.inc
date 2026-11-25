typedef struct {
    Matrix4f matrices[MATRICES_MAX];
    uint32_t fogColor;
    float scalars[3];
    bool fog;
    bool alphaTest;
    bool valid;
} BuiltinUniformSnapshot;

static BuiltinUniformSnapshot defaultUniforms;
static BuiltinUniformSnapshot gameUniforms[64];
static unsigned retainedUniforms;

static BuiltinUniformSnapshot* gameUniformSnapshot(GLModernRenderer* gl, const GMLShader* shader) {
    if (gl->gmlShaders == nullptr) return nullptr;
    uintptr_t offset = (uintptr_t)shader - (uintptr_t)gl->gmlShaders;
    if (offset % sizeof(*shader) != 0) return nullptr;
    size_t index = offset / sizeof(*shader);
    if (index >= gl->gmlShaderCount || index >= sizeof(gameUniforms) / sizeof(*gameUniforms)) return nullptr;
    return &gameUniforms[index];
}

static bool canReuseBuiltinUniforms(GLModernRenderer* gl, BuiltinUniformSnapshot* cached) {
    if (cached == nullptr) return false;
    const Renderer* renderer = &gl->base.base;
#ifdef PLATFORM_VITA
    int frame = renderer->runner != nullptr ? renderer->runner->frameCount : 0;
    if (frame <= 180 || ((frame - 1) / 180) % 2 == 0) {
        cached->valid = false;
        return false;
    }
#endif
    const float scalars[] = { gl->fogStart, gl->fogEnd, gl->base.alphaTestRef };
    if (cached->valid && gl->fogColor == cached->fogColor && gl->fogEnable == cached->fog &&
        gl->base.alphaTestEnable == cached->alphaTest && memcmp(cached->scalars, scalars, sizeof(scalars)) == 0 &&
        memcmp(cached->matrices, renderer->gmlMatrices, sizeof(cached->matrices)) == 0) {
        ++retainedUniforms;
        return true;
    }
    memcpy(cached->matrices, renderer->gmlMatrices, sizeof(cached->matrices));
    memcpy(cached->scalars, scalars, sizeof(scalars));
    cached->fogColor = gl->fogColor;
    cached->fog = gl->fogEnable;
    cached->alphaTest = gl->base.alphaTestEnable;
    cached->valid = true;
    return false;
}

static void invalidateGameUniforms(GLModernRenderer* gl, const GMLShader* shader) {
    BuiltinUniformSnapshot* cached = gameUniformSnapshot(gl, shader);
    if (cached != nullptr) cached->valid = false;
}
