"""Reuse exact renderer uniform state when the program still holds it."""
from pathlib import Path


def with_uniform_trial(code):
    def replace(before, after, count=1):
        nonlocal code
        assert code.count(before) == count, before
        code = code.replace(before, after)

    anchor = "static void glUploadGameShaderBuiltins(GLRenderer* gl, const GMLShader* gmlShader) {"
    prefix = Path(__file__).with_name("uniform-trial.inc").read_text()
    replace(anchor, prefix + "\n" + anchor)
    replace("    GLShaderUniform* gmMatricesUniform = gmlShader->gmMatrices;",
            "    if (canReuseBuiltinUniforms(modernGl, gameUniformSnapshot(modernGl, gmlShader))) return;\n"
            "    GLShaderUniform* gmMatricesUniform = gmlShader->gmMatrices;")
    replace("        glUseProgram(modernGl->defaultShaderProgram->shaderId);\n\n        Matrix4f flippedClip[MATRICES_MAX];",
            "        glUseProgram(modernGl->defaultShaderProgram->shaderId);\n"
            "        if (canReuseBuiltinUniforms(modernGl, &defaultUniforms)) return;\n\n"
            "        Matrix4f flippedClip[MATRICES_MAX];")
    anchor = "    GMLShader* shader = &modernGl->gmlShaders[renderer->currentShader];\n    GLenum type = glShaderGetUniformTypeByLocation(shader, handle);"
    replace(anchor, "    GMLShader* shader = &modernGl->gmlShaders[renderer->currentShader];\n"
            "    invalidateGameUniforms(modernGl, shader);\n    GLenum type = glShaderGetUniformTypeByLocation(shader, handle);", 3)
    replace("Renderer* GLRenderer_create(void) {",
            "Renderer* GLRenderer_create(void) {\n    defaultUniforms.valid = false;\n"
            "    memset(gameUniforms, 0, sizeof(gameUniforms));\n    retainedUniforms = 0;")
    replace("    if (modernGl->reportRenderPaths && frame > 0 && frame % 90 == 0) {",
            "    if (frame > 0 && frame % 90 == 0) {\n"
            "        logInfo(\"Vita uniform reuse frame=%d reuses=%u\\n\", frame, retainedUniforms);\n"
            "        retainedUniforms = 0;\n    }\n"
            "    if (modernGl->reportRenderPaths && frame > 0 && frame % 90 == 0) {")
    return code
