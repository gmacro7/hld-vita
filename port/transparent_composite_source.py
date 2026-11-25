"""Test skipping zero-alpha fragments in the original premultiplied composite."""


def with_transparent_composite(code, compare=False):
    def replace(before, after, count=1):
        nonlocal code
        assert code.count(before) == count, before
        code = code.replace(before, after)

    anchor = "static void flushBatch(GLRenderer* gl) {"
    replace(anchor, """static GMLShader transparentCompositeProgram;
static GMLShader* transparentCompositeOriginal;
static GLint transparentCompositeLuminosity;
static bool transparentCompositeActive;
static float transparentCompositeValue;
static unsigned transparentCompositeDraws;

static void glUploadGameShaderBuiltins(GLRenderer* gl, const GMLShader* shader);

""" + anchor)
    anchor = "    if (gl->base.currentShader != -1) {\n        GMLShader* shader = &modernGl->gmlShaders[gl->base.currentShader];\n"
    replace(anchor, """    GLuint restoreTransparentProgram = 0;
""" + anchor + """        if (transparentCompositeActive && shader == transparentCompositeOriginal) {
            restoreTransparentProgram = shader->shaderId;
            glUseProgram(transparentCompositeProgram.shaderId);
            glUploadGameShaderBuiltins(gl, &transparentCompositeProgram);
            glUniform1f(transparentCompositeLuminosity, transparentCompositeValue);
            ++transparentCompositeDraws;
        }
""")
    anchor = "    if (restoreProgram != 0) glUseProgram(restoreProgram);\n#endif\n"
    replace(anchor, anchor + "    if (restoreTransparentProgram != 0) glUseProgram(restoreTransparentProgram);\n")

    anchor = """#ifdef PLATFORM_VITA
        if (modernGl->skipEmptyComposites && gmlShader->compiled && strcmp(shdr->name, "shd_PreMultiplyAlpha") == 0) {"""
    prepare = r'''        if (gmlShader->compiled && gmlShader->gmBaseTexture != nullptr &&
            fragmentShaderSource != nullptr && strcmp(shdr->name, "shd_PreMultiplyAlpha") == 0 &&
            dataWin->gen8.gameID == 742741103 && dataWin->gen8.wadVersion == 15) {
            static const char insert[] = "if (gl_FragColor.a == 0.0) discard;\n    ";
            const char* at = strstr(fragmentShaderSource, "gl_FragColor.rgb *= gl_FragColor.a;");
            if (at != nullptr) {
                size_t prefix = (size_t)(at - fragmentShaderSource);
                char* variant = safeMalloc(strlen(fragmentShaderSource) + sizeof(insert));
                memcpy(variant, fragmentShaderSource, prefix);
                memcpy(variant + prefix, insert, sizeof(insert) - 1);
                strcpy(variant + prefix + sizeof(insert) - 1, at);
                bool linked = compileProgram(&transparentCompositeProgram, "shd_PreMultiplyAlpha transparent fragments",
                    vertexShaderSource, variant, shdr->vertexAttributeCount, shdr->vertexAttributes);
                free(variant);
                if (linked && transparentCompositeProgram.gmBaseTexture != nullptr &&
                    transparentCompositeProgram.gmMatrices != nullptr) {
                    transparentCompositeLuminosity = glGetUniformLocation(transparentCompositeProgram.shaderId, "luminosity");
                    if (transparentCompositeLuminosity >= 0) {
                        transparentCompositeOriginal = gmlShader;
                        glUseProgram(transparentCompositeProgram.shaderId);
                        glUniform1i(transparentCompositeProgram.gmBaseTexture->location, gmlShader->gmBaseTexture->samplerSlot);
                    }
                }
            }
        }

'''
    if compare:
        prepare = """        if (strcmp(shdr->name, "shd_PreMultiplyAlpha") == 0) {
            FILE* enabled = fopen("ux0:data/hld-vm/transparent-composite", "r");
            transparentCompositeEnabled = enabled != nullptr;
            if (enabled != nullptr) fclose(enabled);
        }
""" + prepare
        replace("static bool transparentCompositeActive;",
                "static bool transparentCompositeActive;\nstatic bool transparentCompositeEnabled;")
    replace(anchor, prepare + anchor)
    anchor = "    freeShader(modernGl->defaultShaderProgram);"
    replace(anchor, """    freeShader(&transparentCompositeProgram);
    memset(&transparentCompositeProgram, 0, sizeof(transparentCompositeProgram));
    transparentCompositeOriginal = nullptr;
    transparentCompositeActive = false;
    transparentCompositeDraws = 0;
""" + anchor)

    # Call order and blending come from the original renderer. Only the sampled
    # full-surface composite can use the variant, with explicit state checks.
    lo = code.index("static void glDrawSurface(Renderer* renderer")
    hi = code.index("static void glDrawSurfaceColor(", lo)
    region = code[lo:hi]
    anchor = "    float u0 = (float) srcLeft / (float) texW;"
    assert region.count(anchor) == 1
    phase = " && transparentCompositeEnabled" if compare else ""
    guard = """    GLModernRenderer* compositeRenderer = (GLModernRenderer*)gl;
    const bool premultiply = transparentCompositeOriginal != nullptr &&
        renderer->currentShader >= 0 &&
        &compositeRenderer->gmlShaders[renderer->currentShader] == transparentCompositeOriginal;
    bool discardTransparent = premultiply && srcLeft == 0 && srcTop == 0 && srcWidth == texW &&
        srcHeight == texH && texW >= 400 && texH >= 240""" + phase + """;
    if (discardTransparent) {
#ifdef PLATFORM_VITA
        discardTransparent = glEmptySurfaceComposite(compositeRenderer, surfaceID);
        transparentCompositeValue = compositeRenderer->premultiplyLuminosity;
#else
        GLint bound, src, dst, srcAlpha, dstAlpha, rgbOp, alphaOp;
        glGetIntegerv(GL_FRAMEBUFFER_BINDING, &bound);
        glGetIntegerv(GL_BLEND_SRC_RGB, &src);
        glGetIntegerv(GL_BLEND_DST_RGB, &dst);
        glGetIntegerv(GL_BLEND_SRC_ALPHA, &srcAlpha);
        glGetIntegerv(GL_BLEND_DST_ALPHA, &dstAlpha);
        glGetIntegerv(GL_BLEND_EQUATION, &rgbOp);
        glGetIntegerv(GL_BLEND_EQUATION_ALPHA, &alphaOp);
        const GLuint program = transparentCompositeOriginal->shaderId;
        GLint lumen = glGetUniformLocation(program, "luminosity");
        if (lumen >= 0) glGetUniformfv(program, lumen, &transparentCompositeValue);
        discardTransparent = (GLuint)bound != gl->surfaces[surfaceID] && lumen >= 0 &&
            isfinite(transparentCompositeValue) && glIsEnabled(GL_BLEND) &&
            !glIsEnabled(GL_STENCIL_TEST) && !glIsEnabled(GL_DEPTH_TEST) &&
            src == GL_ONE && dst == GL_ONE_MINUS_SRC_ALPHA && srcAlpha == GL_ONE &&
            dstAlpha == GL_ONE_MINUS_SRC_ALPHA && rgbOp == GL_FUNC_ADD && alphaOp == GL_FUNC_ADD;
#endif
        if (discardTransparent) {
            flushBatch(gl);
            transparentCompositeActive = true;
        }
    }
"""
    region = region.replace(anchor, guard + "\n" + anchor)
    anchor = "    );\n#ifdef PLATFORM_VITA\n    if (finalTexture"
    assert region.count(anchor) == 1
    region = region.replace(anchor, """    );
    if (discardTransparent) {
        flushBatch(gl);
        transparentCompositeActive = false;
    }
#ifdef PLATFORM_VITA
    if (finalTexture""")
    code = code[:lo] + region + code[hi:]

    anchor = "    GLModernRenderer* modernGl = (GLModernRenderer*) gl;\n\n#ifdef PLATFORM_VITA\n    int32_t frame = renderer->runner->frameCount;\n"
    replace(anchor, """    GLModernRenderer* modernGl = (GLModernRenderer*) gl;
    if (renderer->runner->frameCount > 0 && renderer->runner->frameCount % 90 == 0) {
        logInfo("Transparent composite frame=%d draws=%u\\n", renderer->runner->frameCount, transparentCompositeDraws);
        transparentCompositeDraws = 0;
    }

#ifdef PLATFORM_VITA
    int32_t frame = renderer->runner->frameCount;
""")
    return code
