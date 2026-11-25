"""Omit certified transparent quads in the original premultiply shader."""

from pathlib import Path


def with_zero_alpha_quads(code, compare=False, transparent_batches=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    # Keep the same blend-state proof for an empty surface and a zero-alpha quad.
    start = code.index("    if ((GLuint)bound == gl->surfaces[sourceId] || glIsEnabled(GL_DEPTH_TEST)")
    end = code.index("\n}", start)
    before = code[start:end]
    state = before.replace("    if ((GLuint)bound == gl->surfaces[sourceId] || glIsEnabled(GL_DEPTH_TEST)",
                           "    if (glIsEnabled(GL_DEPTH_TEST)")
    replace(before, "    return (GLuint)bound != gl->surfaces[sourceId] && glPremultipliedZeroIsIdentity();")
    helper = Path(__file__).with_name("zero-alpha-quads.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_ZERO_ALPHA_QUADS\n" + helper
    if transparent_batches:
        helper = "#define HLD_SKIP_TRANSPARENT_BATCHES\n" + helper
    anchor = "static void flushBatch(GLRenderer* gl) {"
    replace(anchor, "static bool glPremultipliedZeroIsIdentity(void) {\n" + state + "\n}\n\n" + helper + "\n" + anchor)
    anchor = "    if (modernGl->batchCount == 0) return;"
    replace(anchor, anchor + "\n    removeZeroAlphaQuads(modernGl);")
    if transparent_batches:
        anchor = ("    if (modernGl->batchType == BATCHTYPE_QUAD) {\n"
                  "        glDrawElements(GL_TRIANGLES, indexCount, GL_UNSIGNED_SHORT, nullptr);")
        replace(anchor, """    if (modernGl->batchType == BATCHTYPE_QUAD) {
        // Preserve all original GL bindings and omit only the identity draw.
        if (!skipTransparentQuadBatch(modernGl))
            glDrawElements(GL_TRIANGLES, indexCount, GL_UNSIGNED_SHORT, nullptr);""")
    anchor = "        if (patchedVertexSource) free(patchedVertexSource);"
    replace(anchor, """        if (gmlShader->compiled && dataWin->gen8.gameID == 742741103 && dataWin->gen8.wadVersion == 15 &&
            strcmp(shdr->name, "shd_PreMultiplyAlpha") == 0) {
            bindZeroAlphaQuads(modernGl, i, shdr);
        }
""" + anchor)
    anchor = "static void glDestroy(Renderer* renderer) {\n    GLRenderer* gl = (GLRenderer*) renderer;\n    GLModernRenderer* modernGl = (GLModernRenderer*) gl;"
    replace(anchor, anchor + "\n    if (zeroAlphaQuads.renderer == modernGl) zeroAlphaQuads = (ZeroAlphaQuads){0};")
    return code
