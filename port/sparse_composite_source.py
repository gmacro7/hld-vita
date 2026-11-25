"""Restrict a conservatively tracked overlay to disjoint rectangles."""

from pathlib import Path

from zero_alpha_quads_source import with_zero_alpha_quads


def with_sparse_composite(code, compare=False, geometry=False, transparent_coverage=False):
    code = with_zero_alpha_quads(code)

    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("sparse-composite.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_SPARSE_COMPOSITE\n" + helper
    if geometry:
        helper = "#define HLD_SPARSE_GEOMETRY\n" + helper
    if transparent_coverage:
        helper = "#define HLD_SPARSE_TRANSPARENT_COVERAGE\n" + helper
    anchor = "static void flushBatch(GLRenderer* gl) {"
    replace(anchor, helper + "\n" + anchor)
    anchor = "    removeZeroAlphaQuads(modernGl);"
    replace(anchor, """    const bool pendingSparse = sparseClipPending && modernGl->batchCount == 1;
    sparseClipPending = false;
""" + anchor + "\n    sparseTrackBatch(modernGl);")
    if geometry:
        anchor = "    int32_t indexCount = modernGl->batchCount * INDICES_PER_QUAD;"
        replace(anchor, "    sparseExpandComposite(modernGl, pendingSparse);\n" + anchor)
    else:
        anchor = ("    if (modernGl->batchType == BATCHTYPE_QUAD) {\n"
                  "        glDrawElements(GL_TRIANGLES, indexCount, GL_UNSIGNED_SHORT, nullptr);")
        replace(anchor, """    if (modernGl->batchType == BATCHTYPE_QUAD) {
        sparseDrawElements(modernGl, indexCount, pendingSparse);""")
    anchor = "        glDrawArrays(mode, 0, count);"
    replace(anchor, "        sparseInvalidateCurrent(gl);\n" + anchor)
    anchor = "    glDrawArrays(\n"
    replace(anchor, "    sparseInvalidateCurrent(gl);\n" + anchor)
    anchor = "    glClearColor(r, g, b, alpha);"
    replace(anchor, "    sparseTrackClear(gl, alpha);\n" + anchor)
    for function in ("glSurfaceFree", "glSurfaceResize"):
        start = code.index(f"static void {function}(")
        end = code.index("\n}\n", start)
        body = code[start:end]
        assert body.count("    flushBatch(gl);") == 1
        replace(body, body.replace("    flushBatch(gl);", "    flushBatch(gl);\n    sparseForgetSurface(gl, surfaceID);"))
    start = code.index("static void glSurfaceCopy(")
    end = code.index("\n}\n", start)
    body = code[start:end]
    assert body.count("    flushBatch(gl);") >= 1
    replace(body, body.replace("    flushBatch(gl);", "    flushBatch(gl);\n    sparseForgetSurface(gl, destSurfaceID);", 1))
    anchor = "    if (zeroAlphaQuads.renderer == modernGl) zeroAlphaQuads = (ZeroAlphaQuads){0};"
    replace(anchor, """    if (sparseCoverage.renderer == gl) sparseCoverage = (SparseCoverage){0};
    if (zeroAlphaQuads.renderer == modernGl) {
        sparseClipPending = false;
        sparseUnexpectedUniform = false;
    }
""" + anchor)
    uniform = """    GMLShader* shader = &modernGl->gmlShaders[renderer->currentShader];
    GLenum type = glShaderGetUniformTypeByLocation(shader, handle);"""
    assert code.count(uniform) == 3
    code = code.replace(uniform, """    if (modernGl == zeroAlphaQuads.renderer && renderer->currentShader == zeroAlphaQuads.shaderIndex &&
        handle != zeroAlphaQuads.luminosity) sparseUnexpectedUniform = true;
""" + uniform)
    start = code.index("static void glDrawSurface(Renderer* renderer")
    end = code.index("static void glDrawSurfaceColor(", start)
    body = code[start:end]
    anchor = "    drawTexture(\n"
    assert body.count(anchor) == 1
    body = body.replace(anchor, """    const bool sparse = ((GLModernRenderer*)gl)->batchCount == 0 &&
        sparsePrepareComposite(gl, surfaceID, texW, texH,
        srcLeft, srcTop, srcWidth, srcHeight, x, y, xscale, yscale, angleDeg, alpha);
""" + anchor)
    # The ordinary draw must batch as before unless clip rectangles are selected.
    anchor = "        alpha\n    );\n#ifdef PLATFORM_VITA"
    assert body.count(anchor) == 1
    body = body.replace(anchor, """        alpha
    );
    if (sparse && ((GLModernRenderer*)gl)->batchCount == 1) sparseClipPending = true;
#ifdef PLATFORM_VITA""")
    return code[:start] + body + code[end:]
