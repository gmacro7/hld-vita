"""Keep the simple sprite program bound across adjacent compatible batches."""


def with_retained_sprite_program(code):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    signature = "static bool glUseSimpleSprite(GLModernRenderer* modernGl) {"
    replace(signature, r'''static bool retainSimpleSpriteProgram;
static GLModernRenderer* retainedSimpleSpriteOwner;
static unsigned simpleSpriteBinds, simpleSpriteReuses;

void hldReportRetainedSpriteProgram(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita simple sprite program frame=%d enabled=%d binds=%u reuses=%u\n",
            frame, (int)retainSimpleSpriteProgram, simpleSpriteBinds, simpleSpriteReuses);
    simpleSpriteBinds = simpleSpriteReuses = 0;
}

static void restoreSimpleSpriteProgram(GLModernRenderer* modernGl) {
    if (retainedSimpleSpriteOwner != modernGl) return;
    glUseProgram(modernGl->defaultShaderProgram->shaderId);
    retainedSimpleSpriteOwner = nullptr;
}

''' + signature)
    replace("    glUseProgram(modernGl->simpleSpriteProgram.shaderId);", """    if (retainedSimpleSpriteOwner == modernGl) {
        ++simpleSpriteReuses;
    } else {
        glUseProgram(modernGl->simpleSpriteProgram.shaderId);
        ++simpleSpriteBinds;
    }""")
    replace("""static void flushBatch(GLRenderer* gl) {
    GLModernRenderer* modernGl = (GLModernRenderer*) gl;

    if (modernGl->batchCount == 0) return;

#ifdef PLATFORM_VITA
    GLuint restoreProgram = 0;""", """static void flushBatchInternal(GLRenderer* gl, bool adjacentBatch) {
    GLModernRenderer* modernGl = (GLModernRenderer*) gl;
#ifdef PLATFORM_VITA
    const bool canRetain = adjacentBatch && retainSimpleSpriteProgram;
#else
    (void)adjacentBatch;
#endif
    if (modernGl->batchCount == 0) {
#ifdef PLATFORM_VITA
        if (!canRetain) restoreSimpleSpriteProgram(modernGl);
#endif
        return;
    }

#ifdef PLATFORM_VITA
    bool retained = false;
    GLuint restoreProgram = 0;""")
    replace("        if (glUseOptimizedOverlay(modernGl, shader)) restoreProgram = shader->shaderId;",
            "        restoreSimpleSpriteProgram(modernGl);\n"
            "        if (glUseOptimizedOverlay(modernGl, shader)) restoreProgram = shader->shaderId;")
    replace("""        if (glUseSimpleSprite(modernGl)) {
            restoreProgram = modernGl->defaultShaderProgram->shaderId;
        } else
#endif
        {
            glUniform1i(modernGl->uLocalUV, modernGl->batchLocalUV);""", """        if (glUseSimpleSprite(modernGl)) {
            retained = canRetain;
            retainedSimpleSpriteOwner = modernGl;
        } else
#endif
        {
#ifdef PLATFORM_VITA
            restoreSimpleSpriteProgram(modernGl);
#endif
            glUniform1i(modernGl->uLocalUV, modernGl->batchLocalUV);""")
    replace("    if (restoreProgram != 0) glUseProgram(restoreProgram);",
            "    if (!retained) restoreSimpleSpriteProgram(modernGl);\n"
            "    if (restoreProgram != 0) glUseProgram(restoreProgram);")
    signature = "static void flushIfNeededAndSetActiveState(GLRenderer* gl, BatchType batchType, GLuint textureId) {"
    replace(signature, """static void flushBatch(GLRenderer* gl) {
    flushBatchInternal(gl, false);
}

""" + signature)
    replace("""            flushBatch(gl);
        }
    }

    modernGl->batchType = batchType;""", """            flushBatchInternal(gl, true);
        }
    }

    modernGl->batchType = batchType;""")
    marker = '    FILE* fullDefaultFlag = fopen("ux0:data/hld-vm/full-default-shader", "r");'
    replace(marker, """    FILE* retainedSpriteFlag = fopen("ux0:data/hld-vm/retain-simple-sprite", "r");
    retainSimpleSpriteProgram = retainedSpriteFlag != nullptr;
    if (retainedSpriteFlag != nullptr) fclose(retainedSpriteFlag);
""" + marker)
    marker = """#ifdef PLATFORM_VITA
    if (modernGl->distortionBaseTexture != 0) glDeleteTextures(1, &modernGl->distortionBaseTexture);"""
    replace(marker, marker.replace("#ifdef PLATFORM_VITA\n", "#ifdef PLATFORM_VITA\n"
                                    "    restoreSimpleSpriteProgram(modernGl);\n"))
    return code
