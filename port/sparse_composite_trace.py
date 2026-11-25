"""Trace the overlay eligibility checks without reading back GPU surfaces."""


def with_sparse_composite_trace(code, source_quads=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    anchor = "static bool sparseIsTrackedTarget(GLRenderer* gl) {"
    replace(anchor, r'''static bool sparseTraceFrame(const GLRenderer* gl) {
    if (gl == nullptr || gl->base.runner == nullptr) return false;
    const int32_t frame = gl->base.runner->frameCount;
    return frame == 449 || frame == 539 || frame == 629 || frame == 630 ||
           frame == 899 || frame == 1079 || frame == 1080;
}

static void sparseTraceState(const GLRenderer* gl, const char* site, int32_t source) {
    if (!sparseTraceFrame(gl)) return;
    const GLModernRenderer* modernGl = (const GLModernRenderer*)gl;
    GLint viewport[4] = {0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    const float* m = gl->base.gmlMatrices[MATRIX_WORLD_VIEW_PROJECTION].m;
    logInfo("HLD overlay trace frame=%d site=%s source=%d tracked=%d known=%d fbo=%u tracked_fbo=%u "
            "shader=%d premultiply=%d batch=%d clipped=%d uniform=%d viewport=%d,%d,%d,%d wvp=%.7g,%.7g,%.7g,%.7g\n",
            gl->base.runner->frameCount, site, source, sparseCoverage.surface, (int)sparseCoverage.known,
            sparseBoundFramebuffer(), sparseCoverage.framebuffer, gl->base.currentShader, zeroAlphaQuads.shaderIndex,
            modernGl->batchCount,
            (int)glIsEnabled(GL_SCISSOR_TEST), (int)sparseUnexpectedUniform, viewport[0], viewport[1],
            viewport[2], viewport[3], (double)m[0], (double)m[5], (double)m[12], (double)m[13]);
}

static bool sparseTraceReject(const GLRenderer* gl, const char* site) {
    sparseTraceState(gl, site, -1);
    return false;
}

''' + anchor)
    replace("    if (sparseCoverage.known && sparseIsTrackedTarget(gl)) sparseCoverage.known = false;",
            """    if (sparseCoverage.known && sparseIsTrackedTarget(gl)) {
        sparseTraceState(gl, "invalidate-unhandled-draw", -1);
        sparseCoverage.known = false;
    }""")
    replace("    if (gl == sparseCoverage.renderer && surface == sparseCoverage.surface) sparseCoverage = (SparseCoverage){0};",
            """    if (gl == sparseCoverage.renderer && surface == sparseCoverage.surface) {
        sparseTraceState(gl, "forget-surface", surface);
        sparseCoverage = (SparseCoverage){0};
    }""")
    replace("    if (alpha != 0.0f || glIsEnabled(GL_SCISSOR_TEST)) return;", """    if (alpha != 0.0f || glIsEnabled(GL_SCISSOR_TEST)) {
        sparseTraceState(gl, alpha != 0.0f ? "clear-nonzero-alpha" : "clear-scissored", -1);
        return;
    }""")
    replace("    if (!write[3]) return;", """    if (!write[3]) {
        sparseTraceState(gl, "clear-alpha-mask", -1);
        return;
    }
    sparseTraceState(gl, "clear-established", -1);""")
    replace("        modernGl->batchType != BATCHTYPE_QUAD) {\n        sparseCoverage.known = false;",
            """        modernGl->batchType != BATCHTYPE_QUAD) {
        sparseTraceState(gl, "invalidate-shader-or-batch", -1);
        sparseCoverage.known = false;""")
    replace("        viewport[2] != SPARSE_WIDTH || viewport[3] != SPARSE_HEIGHT) {\n        sparseCoverage.known = false;",
            """        viewport[2] != SPARSE_WIDTH || viewport[3] != SPARSE_HEIGHT) {
        sparseTraceState(gl, "invalidate-source-projection", -1);
        sparseCoverage.known = false;""")
    replace("        if (sparseMarkQuad(m, quad)) continue;\n        sparseCoverage.known = false;",
            """        if (sparseMarkQuad(m, quad)) continue;
        sparseTraceState(gl, "invalidate-quad-bounds", -1);
        sparseCoverage.known = false;""")
    replace("            if (sparseClipCount == SPARSE_CLIP_LIMIT) return false;",
            '            if (sparseClipCount == SPARSE_CLIP_LIMIT) return sparseTraceReject(sparseCoverage.renderer, "rectangle-limit");')
    replace("    if (sparseClipCount == 0 || area * 2 >= SPARSE_WIDTH * SPARSE_HEIGHT) return false;",
            """    if (sparseClipCount == 0 || area * 2 >= SPARSE_WIDTH * SPARSE_HEIGHT)
        return sparseTraceReject(sparseCoverage.renderer, sparseClipCount == 0 ? "empty-coverage" : "dense-coverage");""")

    replace("    if (sparseUnexpectedUniform || width != SPARSE_WIDTH || height != SPARSE_HEIGHT ||",
            "    if (width != SPARSE_WIDTH || height != SPARSE_HEIGHT ||")
    replace("        zeroAlphaQuads.renderer != (GLModernRenderer*)gl || !sparseCompositeEnabled()) return false;",
            """        zeroAlphaQuads.renderer != (GLModernRenderer*)gl) return false;
    if (sparseUnexpectedUniform || !sparseCompositeEnabled()) return sparseTraceReject(gl, "disabled-or-untracked-uniform");""")
    replace(".framebuffer = gl->surfaces[surface]};\n        return false;", """.framebuffer = gl->surfaces[surface]};
        return sparseTraceReject(gl, "bind-source");""")
    replace("    if (!sparseCoverage.known || sparseCoverage.renderer != gl || sparseCoverage.surface != surface ||\n"
            "        sparseCoverage.framebuffer != gl->surfaces[surface] ||",
            """    if (sparseCoverage.renderer != gl || sparseCoverage.surface != surface ||
        sparseCoverage.framebuffer != gl->surfaces[surface]) return sparseTraceReject(gl, "different-source");
    if (!sparseCoverage.known) return sparseTraceReject(gl, "unknown-coverage");
    if (""")
    replace("x != 0.0f || y != 0.0f || angle != 0.0f || !(alpha > 0.0f && alpha <= 1.0f)) return false;",
            "x != 0.0f || y != 0.0f || angle != 0.0f || !(alpha > 0.0f && alpha <= 1.0f)) "
            'return sparseTraceReject(gl, "crop-position-rotation-or-alpha");')
    replace("    if (format != GL_RGBA && format != GL_RGBA8) return false;\n    int32_t scaleX, scaleY;",
            '    if (format != GL_RGBA && format != GL_RGBA8) return sparseTraceReject(gl, "surface-format");\n'
            "    int32_t scaleX, scaleY;")
    replace("    if (!sparseIntegerScale(xscale, &scaleX) || !sparseIntegerScale(yscale, &scaleY)) return false;",
            '    if (!sparseIntegerScale(xscale, &scaleX) || !sparseIntegerScale(yscale, &scaleY)) return sparseTraceReject(gl, "nonintegral-scale");')
    replace("    if (framebuffer == sparseCoverage.framebuffer) return false;",
            '    if (framebuffer == sparseCoverage.framebuffer) return sparseTraceReject(gl, "self-composite");')
    replace("        m[12] != -1.0f || m[13] != 1.0f) return false;",
            '        m[12] != -1.0f || m[13] != 1.0f) return sparseTraceReject(gl, "destination-projection");')
    replace("          (fabsf(m[5] * viewport[3] + 2.0f) < 0.000001f))) return false;",
            '          (fabsf(m[5] * viewport[3] + 2.0f) < 0.000001f))) return sparseTraceReject(gl, "destination-viewport");')
    replace("    memcpy(sparseClipViewport, viewport, sizeof(viewport));",
            '    sparseTraceState(gl, "prepared", surface);\n    memcpy(sparseClipViewport, viewport, sizeof(viewport));')
    replace("        !glPremultipliedZeroIsIdentity() || !zeroAlphaLuminosityInRange(modernGl)) return false;\n"
            "    const Matrix4f* projection",
            '        !glPremultipliedZeroIsIdentity() || !zeroAlphaLuminosityInRange(modernGl)) return sparseTraceReject(gl, "flush-state");\n'
            "    const Matrix4f* projection")
    replace("    if (memcmp(projection->m, sparseClipProjection.m, sizeof(projection->m)) != 0) return false;",
            '    if (memcmp(projection->m, sparseClipProjection.m, sizeof(projection->m)) != 0) return sparseTraceReject(gl, "flush-projection");')
    replace("    return memcmp(viewport, sparseClipViewport, sizeof(viewport)) == 0;",
            '    return memcmp(viewport, sparseClipViewport, sizeof(viewport)) == 0 || sparseTraceReject(gl, "flush-viewport");')
    replace("        !sparseClipStillValid(modernGl)) return;\n\n    GlVertex* vertices",
            """        !sparseClipStillValid(modernGl)) return;
    sparseTraceState(&modernGl->base, "checked-flush", -1);

    GlVertex* vertices""")
    replace("            vertex->a != original.a) return;",
            """            vertex->a != original.a) {
            sparseTraceState(&modernGl->base, "quad-geometry", -1);
            return;
        }""")
    replace("    modernGl->batchCount = sparseClipCount;\n    sparseRecordComposite();",
            '    sparseTraceState(&modernGl->base, "submitted", -1);\n'
            "    modernGl->batchCount = sparseClipCount;\n    sparseRecordComposite();")
    replace("    const bool pendingSparse = sparseClipPending && modernGl->batchCount == 1;",
            '    if (sparseClipPending && modernGl->batchCount != 1) sparseTraceState(gl, "joined-batch", -1);\n'
            "    const bool pendingSparse = sparseClipPending && modernGl->batchCount == 1;")
    anchor = "    const bool sparse = ((GLModernRenderer*)gl)->batchCount == 0 &&"
    replace(anchor, r'''    const bool overlayTrace = sparseTraceFrame(gl) && texW == SPARSE_WIDTH && texH == SPARSE_HEIGHT;
    if (overlayTrace) {
        logInfo("HLD overlay geometry frame=%d source=%d crop=%d,%d,%d,%d xy=%.7g,%.7g "
                "scale=%.7g,%.7g angle=%.7g alpha=%.7g\n", renderer->runner->frameCount, surfaceID,
                srcLeft, srcTop, srcWidth, srcHeight, (double)x, (double)y, (double)xscale, (double)yscale,
                (double)angleDeg, (double)alpha);
        const char* site = renderer->currentShader != zeroAlphaQuads.shaderIndex ? "different-shader" :
            (((GLModernRenderer*)gl)->batchCount == 0 ? "candidate" : "busy-batch");
        sparseTraceState(gl, site, surfaceID);
    }
''' + anchor)
    replace("        modernGl->emptyCompositeSkips++;",
            """        if (texW == SPARSE_WIDTH && texH == SPARSE_HEIGHT && renderer->currentShader == zeroAlphaQuads.shaderIndex)
            sparseTraceState(gl, "skipped-cleared-source", surfaceID);
        modernGl->emptyCompositeSkips++;""")
    if source_quads:
        replace("static bool sparseMarkQuad(const float* m, const GlVertex* vertices) {",
                "static bool sparseMarkQuad(const GLModernRenderer* modernGl, const float* m, const GlVertex* vertices) {")
        replace("        if (sparseMarkQuad(m, quad)) continue;",
                "        if (sparseMarkQuad(modernGl, m, quad)) continue;")
        anchor = "    const int32_t x0 = (int32_t)floorf(left / SPARSE_TILE);"
        replace(anchor, r'''    if ((right - left) * (high - low) >= (SPARSE_WIDTH * SPARSE_HEIGHT / 8) && sparseTraceFrame(&modernGl->base)) {
        logInfo("HLD overlay wide quad frame=%d bounds=%.2f,%.2f,%.2f,%.2f alpha=%u,%u,%u,%u "
                "texture=%u atlas=%d blend_identity=%d luminosity=%d\n", modernGl->base.base.runner->frameCount,
                (double)left, (double)low, (double)right, (double)high,
                (unsigned)vertices[0].a, (unsigned)vertices[1].a, (unsigned)vertices[2].a, (unsigned)vertices[3].a,
                modernGl->currentTextureId, (int)zeroAlphaUsesAtlas(&modernGl->base, modernGl->currentTextureId),
                (int)glPremultipliedZeroIsIdentity(), (int)zeroAlphaLuminosityInRange(modernGl));
    }
''' + anchor)
        replace("                ++sparseTransparentQuads;",
                '                sparseTraceState(gl, "ignored-transparent-quad", -1);\n'
                "                ++sparseTransparentQuads;")
    return code
