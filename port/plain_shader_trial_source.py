"""Render through the built-in textured sprite path for an isolated comparison."""

from runtime_verification_source import replace_once


def with_plain_shader_trial(code):
    anchor = "static GLShaderUniform* getShaderUniform("
    code = replace_once(code, anchor, r'''static bool plainShaderRequested, plainShaderTrial;
static unsigned originalShaderDraws, plainShaderDraws;

static bool gameShaderForDraw(const Renderer* renderer) {
    return !plainShaderTrial && renderer->currentShader != -1;
}

static void reportShaderDraw(const Renderer* renderer) {
    if (renderer->currentShader == -1) return;
    if (plainShaderTrial) ++plainShaderDraws;
    else ++originalShaderDraws;
}

''' + anchor)
    anchor = "static void glInit(Renderer* renderer, DataWin* dataWin) {"
    code = replace_once(code, anchor, anchor + r'''
#ifdef PLATFORM_VITA
    FILE* plainShaderMarker = fopen("ux0:data/hld-vm/benchmark-plain-shaders", "r");
    plainShaderRequested = plainShaderMarker != nullptr;
    if (plainShaderMarker != nullptr) fclose(plainShaderMarker);
#else
    plainShaderRequested = getenv("HLD_TEST_PLAIN_SHADERS") != nullptr;
#endif
    originalShaderDraws = plainShaderDraws = 0;
    plainShaderTrial = false;
    logInfo("HLD plain shaders requested=%d\n", plainShaderRequested);
''')
    code = replace_once(code, "    if (gl->base.currentShader != -1) {\n        GMLShader* shader",
                        """    reportShaderDraw(&gl->base);
    if (gameShaderForDraw(&gl->base)) {
        GMLShader* shader""")
    code = code.replace("gl->base.currentShader == -1 && modernGl->batchLocalUV",
                        "!gameShaderForDraw(&gl->base) && modernGl->batchLocalUV")

    code = replace_once(code, "        GLShaderUniform* sampler = renderer->currentShader == -1 ? nullptr",
                        """        reportShaderDraw(renderer);
        const bool customShader = gameShaderForDraw(renderer);
        GLShaderUniform* sampler = !customShader ? nullptr""")
    code = replace_once(code, "        if (renderer->currentShader == -1 && modernGl->primitiveLocalUV) {",
                        "        if (!customShader && modernGl->primitiveLocalUV) {")
    code = replace_once(code, "        if (renderer->currentShader == -1) glUniform1i(modernGl->uLocalUV, 0);",
                        "        if (!customShader) glUniform1i(modernGl->uLocalUV, 0);")
    code = replace_once(code, "    glDrawArrays(\n        mode,\n        offset,",
                        "    reportShaderDraw(renderer);\n    glDrawArrays(\n        mode,\n        offset,")

    anchor = "    GMLShader* shader = &modernGl->gmlShaders[shaderIndex];\n    glUseProgram(shader->shaderId);"
    code = replace_once(code, anchor, """    if (plainShaderTrial) {
        glUseProgram(modernGl->defaultShaderProgram->shaderId);
        glApplyCullWinding(renderer);
        renderer->currentShader = shaderIndex;
        return;
    }
""" + anchor)
    code = replace_once(code, "    if (renderer->currentShader != -1) {\n        glGpuSetShader(",
                        "    if (gameShaderForDraw(renderer)) {\n        glGpuSetShader(")
    code = replace_once(code, "    int32_t shader = gl->base.currentShader;\n    if (shader == -1) {",
                        "    int32_t shader = gameShaderForDraw(&gl->base) ? gl->base.currentShader : -1;\n    if (shader == -1) {")
    anchor = "    if (handle == -1 || renderer->currentShader == -1"
    assert code.count(anchor) == 3
    code = code.replace(anchor, "    if (plainShaderTrial || handle == -1 || renderer->currentShader == -1")
    anchor = "    GLCommon_beginFrame(gl, gameW, gameH, windowW, windowH);"
    code = replace_once(code, anchor, anchor + r'''
    // Let the original shaders build room resources before timing starts.
    if (plainShaderRequested && !plainShaderTrial && renderer->runner != nullptr &&
        renderer->runner->frameCount > 540) {
        plainShaderTrial = true;
        glShaderSettingsRefresh(renderer);
        logInfo("HLD plain shaders began frame=%d\n", renderer->runner->frameCount);
    }''')
    anchor = "static void glEndFrameEnd(Renderer* renderer) {"
    return replace_once(code, anchor, anchor + r'''
    const int shaderFrame = renderer->runner->frameCount;
    if (shaderFrame > 0 && shaderFrame % 90 == 0) {
        logInfo("HLD shader draws frame=%d original=%u plain=%u\n",
                shaderFrame, originalShaderDraws, plainShaderDraws);
        originalShaderDraws = plainShaderDraws = 0;
    }
''')
