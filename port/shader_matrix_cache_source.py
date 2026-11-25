"""Keep a program's exact game matrix values between unchanged uploads."""

from runtime_verification_source import replace_once


def with_shader_matrix_cache(code):
    anchor = "static GLShaderUniform* getShaderUniform("
    code = replace_once(code, anchor, r'''typedef struct {
    Matrix4f values[MATRICES_MAX];
    uint64_t epoch;
    bool valid;
} ShaderMatrixCache;

typedef struct {
    GLModernRenderer base;
    ShaderMatrixCache* matrices;
    uint64_t uniformEpoch;
    unsigned matrixUploads, matrixReuses;
} MatrixCachedRenderer;

''' + anchor)
    code = replace_once(code,
        "    modernGl->gmlShaders = (GMLShader *)safeCalloc(dataWin->shdr.count, sizeof(GMLShader));",
        """    modernGl->gmlShaders = (GMLShader *)safeCalloc(dataWin->shdr.count, sizeof(GMLShader));
    MatrixCachedRenderer* cached = (MatrixCachedRenderer*)renderer;
    cached->matrices = safeCalloc(dataWin->shdr.count, sizeof(*cached->matrices));""")
    code = replace_once(code, "    free(modernGl->gmlShaders);",
                        "    free(((MatrixCachedRenderer*)renderer)->matrices);\n    free(modernGl->gmlShaders);")
    code = replace_once(code,
        "    GLModernRenderer* modernGl = (GLModernRenderer *)safeCalloc(1, sizeof(GLModernRenderer));",
        """    MatrixCachedRenderer* cached = safeCalloc(1, sizeof(*cached));
    GLModernRenderer* modernGl = &cached->base;""")

    signature = "glUploadGameShaderBuiltins(GLRenderer* gl, const GMLShader* shader)"
    code = replace_once(code, signature, signature[:-1] + ", ShaderMatrixCache* matrices)")
    signature = "glUploadGameShaderBuiltins(GLRenderer* gl, const GMLShader* gmlShader)"
    code = replace_once(code, signature, signature[:-1] + ", ShaderMatrixCache* matrices)")
    code = replace_once(code, "glUploadGameShaderBuiltins(&modernGl->base, &program->program);",
                        "glUploadGameShaderBuiltins(&modernGl->base, &program->program, nullptr);")
    code = replace_once(code, "    glUploadGameShaderBuiltins(gl, shader);",
                        """    MatrixCachedRenderer* cached = (MatrixCachedRenderer*)renderer;
    glUploadGameShaderBuiltins(gl, shader, &cached->matrices[shaderIndex]);""")
    begin = code.index("    Matrix4f flippedClip[MATRICES_MAX];", code.index(signature[:-1]))
    end = code.index("    if (gmFogColourUniform != nullptr)", begin)
    code = code[:begin] + r'''    if (gmMatricesUniform != nullptr) {
        MatrixCachedRenderer* cached = (MatrixCachedRenderer*)renderer;
        const bool unchanged = matrices != nullptr && matrices->valid &&
            matrices->epoch == cached->uniformEpoch &&
            memcmp(matrices->values, renderer->gmlMatrices, sizeof(matrices->values)) == 0;
        if (unchanged) {
            ++cached->matrixReuses;
        } else {
            Matrix4f flippedClip[MATRICES_MAX];
            memcpy(flippedClip, renderer->gmlMatrices, sizeof(flippedClip));
            Matrix4f_flipClipY(&flippedClip[MATRIX_PROJECTION]);
            Matrix4f_flipClipY(&flippedClip[MATRIX_WORLD_VIEW_PROJECTION]);
            glUniformMatrix4fv(gmMatricesUniform->location, 5, GL_FALSE, flippedClip[0].m);
            ++cached->matrixUploads;
            if (matrices != nullptr) {
                memcpy(matrices->values, renderer->gmlMatrices, sizeof(matrices->values));
                matrices->epoch = cached->uniformEpoch;
                matrices->valid = true;
            }
        }
    }
''' + code[end:]

    # A reflected, distinct uniform cannot alias gm_Matrices. Unknown
    # handles can denote an array element or a different program on VitaGL.
    anchor = "static void glShaderSetUniformF(Renderer* renderer,"
    code = replace_once(code, anchor, r'''static void invalidateScriptMatrixCache(Renderer* renderer, const GMLShader* shader,
                                        GLint location, GLenum type) {
    MatrixCachedRenderer* cached = (MatrixCachedRenderer*)renderer;
    if (type == GL_NONE) {
        ++cached->uniformEpoch;
        if (cached->uniformEpoch == 0)
            memset(cached->matrices, 0, cached->base.gmlShaderCount * sizeof(*cached->matrices));
    } else if (shader->gmMatrices != nullptr && shader->gmMatrices->location == location) {
        cached->matrices[renderer->currentShader].valid = false;
    }
}

''' + anchor)
    anchor = "    GLenum type = glShaderGetUniformTypeByLocation(shader, handle);"
    assert code.count(anchor) == 3
    code = code.replace(anchor, anchor + "\n    invalidateScriptMatrixCache(renderer, shader, handle, type);")
    anchor = "    renderer->currentShader = -1;\n    glShaderSettingsRefresh(renderer);"
    code = replace_once(code, "    glUseProgram(modernGl->defaultShaderProgram->shaderId);\n" + anchor, anchor)
    anchor = "static void glGpuResetShader(Renderer* renderer) {\n    GLRenderer* gl = (GLRenderer*) renderer;"
    code = replace_once(code, anchor + "\n    GLModernRenderer* modernGl = (GLModernRenderer*) renderer;", anchor)
    anchor = "    GLModernRenderer* modernGl = (GLModernRenderer*) gl;"
    begin = code.index("static void glEndFrameEnd(Renderer* renderer) {")
    head, body = code[:begin], code[begin:]
    body = body.replace(anchor, anchor + r'''
    MatrixCachedRenderer* cached = (MatrixCachedRenderer*)renderer;
    int32_t matrixFrame = renderer->runner->frameCount;
    if (matrixFrame > 0 && matrixFrame % 90 == 0) {
        logInfo("HLD shader matrices frame=%d uploaded=%u reused=%u\n",
                matrixFrame, cached->matrixUploads, cached->matrixReuses);
        cached->matrixUploads = cached->matrixReuses = 0;
    }
''', 1)
    return head + body
