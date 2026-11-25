"""Link alternate sampler selection while retaining the authored shader math."""

from runtime_verification_source import replace_once


ATLAS_PROGRAM = r'''
static void freeShader(GMLShader* shader);

static bool originalPremultiplyShader(const Shader* asset, bool gles) {
    if (strcmp(asset->name, "shd_PreMultiplyAlpha") != 0 || asset->vertexAttributeCount != 3)
        return false;
    const char* inputs[] = {"in_Position", "in_Colour", "in_TextureCoord"};
    for (unsigned i = 0; i < 3; ++i)
        if (strcmp(asset->vertexAttributes[i], inputs[i]) != 0) return false;
    const char* sources[] = {gles ? asset->glslES_Vertex : asset->glsl_Vertex,
                             gles ? asset->glslES_Fragment : asset->glsl_Fragment};
    const size_t lengths[] = {gles ? 3955 : 3962, gles ? 994 : 976};
    const uint32_t fingerprints[] = {gles ? 0x4bee0504u : 0x51917264u,
                                     gles ? 0x1c88b7ceu : 0x18427866u};
    for (unsigned i = 0; i < 2; ++i) {
        if (sources[i] == nullptr || strlen(sources[i]) != lengths[i]) return false;
        uint32_t fingerprint = 2166136261u;
        for (size_t byte = 0; byte < lengths[i]; ++byte)
            fingerprint = (fingerprint ^ (uint8_t)sources[i][byte]) * 16777619u;
        if (fingerprint != fingerprints[i]) return false;
    }
    return true;
}

static bool compilePremultiplyAtlas(GLModernRenderer* renderer, const Shader* asset,
                                    const char* vertex, const char* fragment,
                                    unsigned slots, GMLShader* program) {
    static const char mainEntry[] = "void main()";
    static const char originalSample[] = "texture2D( gm_BaseTexture, v_vTexcoord )";
    if (slots != 2 && slots != 4) return false;
    if (vertex == nullptr || fragment == nullptr || !originalPremultiplyShader(asset, renderer->isGLES))
        return false;
    const char* vertexMain = strstr(vertex, mainEntry);
    const char* fragmentMain = strstr(fragment, mainEntry);
    const char* sample = fragmentMain != nullptr ? strstr(fragmentMain, originalSample) : nullptr;
    if (vertexMain == nullptr || sample == nullptr) return false;

    const char* precision = renderer->isGLES ? "mediump " : "";
    const size_t vertexCapacity = strlen(vertex) + 256;
    char* vertexVariant = safeMalloc(vertexCapacity);
    snprintf(vertexVariant, vertexCapacity, "%.*sattribute float in_AtlasSlot;\nvarying %sfloat v_AtlasSlot;\n"
             "void atlasOriginalVertex()%s\nvoid main() { v_AtlasSlot = in_AtlasSlot; atlasOriginalVertex(); }\n",
             (int)(vertexMain - vertex), vertex, precision, vertexMain + sizeof(mainEntry) - 1);

    static const char extraSamplerTwo[] = "uniform sampler2D atlasSecond;\n";
    static const char extraSamplerFour[] = "uniform sampler2D atlasSecond;\n"
        "uniform sampler2D atlasThird;\nuniform sampler2D atlasFourth;\n";
    static const char selectTwo[] = "return texture2D(atlasSecond, uv);\n";
    static const char selectFour[] = "if (v_AtlasSlot < 1.5) return texture2D(atlasSecond, uv);\n"
        "if (v_AtlasSlot < 2.5) return texture2D(atlasThird, uv);\nreturn texture2D(atlasFourth, uv);\n";
    const size_t fragmentCapacity = strlen(fragment) + 512;
    char* fragmentVariant = safeMalloc(fragmentCapacity);
    snprintf(fragmentVariant, fragmentCapacity,
             "%.*s%s varying %sfloat v_AtlasSlot;\nvec4 atlasSample(vec2 uv) {\n"
             "if (v_AtlasSlot < 0.5) return texture2D(gm_BaseTexture, uv);\n%s}\n"
             "%.*satlasSample(v_vTexcoord)%s",
             (int)(fragmentMain - fragment), fragment, slots == 2 ? extraSamplerTwo : extraSamplerFour,
             precision, slots == 2 ? selectTwo : selectFour, (int)(sample - fragmentMain), fragmentMain,
             sample + sizeof(originalSample) - 1);

    const char* attributes[] = {"in_Position", "in_Colour", "in_TextureCoord", "in_AtlasSlot"};
    const bool linked = compileProgram(program, "premultiply atlas sampler selection", vertexVariant,
                                        fragmentVariant, 4, attributes);
    free(vertexVariant);
    free(fragmentVariant);
    return linked;
}
'''

ATLAS_PROBE = r'''
static void probePremultiplyAtlas(GLModernRenderer* renderer, const GMLShader* original, const Shader* asset,
                                 const char* vertex, const char* fragment) {
    if (!original->compiled || !originalPremultiplyShader(asset, renderer->isGLES)) return;
    const char* samplers[] = {"gm_BaseTexture", "atlasSecond", "atlasThird", "atlasFourth"};
    for (unsigned slots = 2; slots <= 4; slots += 2) {
        GMLShader shader = {0};
        const bool linked = compilePremultiplyAtlas(renderer, asset, vertex, fragment, slots, &shader);
        bool bindings = linked && glGetAttribLocation(shader.shaderId, "in_AtlasSlot") == 3 &&
            glGetUniformLocation(shader.shaderId, "luminosity") >= 0 && shader.gmMatrices != nullptr;
        for (unsigned index = 0; bindings && index < slots; ++index)
            bindings = glGetUniformLocation(shader.shaderId, samplers[index]) >= 0;
        logInfo("HLD premultiply atlas shader slots=%u linked=%d bindings=%d\n", slots, linked, bindings);
        glUseProgram(original->shaderId);
        freeShader(&shader);
    }
}
'''


def with_premultiply_atlas_probe(code):
    anchor = "static uint32_t glFloatUniformWidth(GLenum type) {"
    # The next helper is inside the Vita-only overlay section. Install the
    # probe before that section so the same source is exercised on the Mac.
    anchor = "#ifdef PLATFORM_VITA\n" + anchor
    code = replace_once(code, anchor, ATLAS_PROGRAM + ATLAS_PROBE + anchor)
    anchor = '        if (patchedVertexSource) free(patchedVertexSource);\n'
    return replace_once(code, anchor,
                        '        probePremultiplyAtlas(modernGl, gmlShader, shdr, vertexShaderSource, fragmentShaderSource);\n'
                        + anchor)


ATLAS_BATCH = r'''
static struct {
    const GLModernRenderer* renderer;
    int32_t originalIndex;
    GMLShader program;
    GLint originalLuminosity, luminosityUniform;
    float luminosity;
    bool valid;

    GLuint textures[2];
    int32_t tailFirst;
    unsigned tailSlot;
    uint8_t slots[MAX_QUADS];
    unsigned joins, draws, prefixDraws;
} premultiplyAtlas;

static void glUploadGameShaderBuiltins(GLRenderer* gl, const GMLShader* shader);
static void flushBatch(GLRenderer* gl);

static bool atlasBatchPending(const GLModernRenderer* renderer) {
    return premultiplyAtlas.renderer == renderer && premultiplyAtlas.textures[1] != 0;
}

static bool isOriginalAtlasTexture(const GLRenderer* gl, GLuint texture) {
    if (texture == 0) return false;
    for (uint32_t index = 0; index < gl->originalTexturePageCount; ++index)
        if (gl->glTextures[index] == texture) return true;
    return false;
}

static bool atlasJoinTexture(GLModernRenderer* renderer, GLuint texture) {
    GLRenderer* gl = &renderer->base;
    if (!premultiplyAtlas.valid || premultiplyAtlas.renderer != renderer ||
        gl->base.currentShader != premultiplyAtlas.originalIndex) return false;

    const GLuint previous = renderer->currentTextureId;
    if (atlasBatchPending(renderer)) {
        if (texture != premultiplyAtlas.textures[0] && texture != premultiplyAtlas.textures[1]) return false;
    } else {
        if (!isOriginalAtlasTexture(gl, previous) || !isOriginalAtlasTexture(gl, texture)) return false;
        premultiplyAtlas.textures[0] = previous;
        premultiplyAtlas.textures[1] = texture;
        memset(premultiplyAtlas.slots, 0, (size_t)renderer->batchCount);
    }
    premultiplyAtlas.tailFirst = renderer->batchCount;
    premultiplyAtlas.tailSlot = texture == premultiplyAtlas.textures[1];
    ++premultiplyAtlas.joins;
#ifdef PLATFORM_VITA
    // The original texture boundary makes transparent-target hints stale.
    glForgetClearedTarget(renderer);
#endif
    return true;
}

static void atlasFlushCompletedRuns(GLRenderer* gl) {
    GLModernRenderer* renderer = (GLModernRenderer*)gl;
    if (!atlasBatchPending(renderer)) return;

    // The ordinary renderer would have submitted everything before the
    // final run. Keep that final run pending across uniform mutations.
    const int32_t first = premultiplyAtlas.tailFirst;
    const int32_t tailCount = renderer->batchCount - first;
    const GLuint tailTexture = renderer->currentTextureId;
    renderer->batchCount = first;
    renderer->currentTextureId = premultiplyAtlas.textures[1 - premultiplyAtlas.tailSlot];
    ++premultiplyAtlas.prefixDraws;
    flushBatch(gl);
    memmove(gl->vertexData, gl->vertexData + first * VERTICES_PER_QUAD,
            (size_t)tailCount * VERTICES_PER_QUAD * sizeof(GlVertex));
    renderer->batchCount = tailCount;
    renderer->currentTextureId = tailTexture;
}

static void atlasSetUserUniform(GLModernRenderer* renderer, GLint handle, bool scalar, float value) {
    GLRenderer* gl = &renderer->base;
    if (!premultiplyAtlas.valid || premultiplyAtlas.renderer != renderer ||
        gl->base.currentShader != premultiplyAtlas.originalIndex) return;
    atlasFlushCompletedRuns(gl);
    if (scalar && handle == premultiplyAtlas.originalLuminosity) {
        premultiplyAtlas.luminosity = value;
    } else {
        // The authored shader has one user uniform. Keep uncommon writes
        // to built-in matrix or sampler locations on the original program.
        premultiplyAtlas.valid = false;
    }
}

static GLint atlasBindProgram(GLModernRenderer* renderer) {
    GMLShader* program = &premultiplyAtlas.program;
    glUseProgram(program->shaderId);
    glUploadGameShaderBuiltins(&renderer->base, program);
    glUniform1f(premultiplyAtlas.luminosityUniform, premultiplyAtlas.luminosity);
    GLint secondaryTexture = 0;
    glActiveTexture(GL_TEXTURE1);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &secondaryTexture);
    glBindTexture(GL_TEXTURE_2D, premultiplyAtlas.textures[1]);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, premultiplyAtlas.textures[0]);
    ++premultiplyAtlas.draws;
    return secondaryTexture;
}

static void atlasRestoreProgram(GLModernRenderer* renderer, GLint secondaryTexture) {
    glDisableVertexAttribArray(3);
    glActiveTexture(GL_TEXTURE1);
    glBindTexture(GL_TEXTURE_2D, (GLuint)secondaryTexture);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, renderer->currentTextureId);
    glUseProgram(renderer->gmlShaders[premultiplyAtlas.originalIndex].shaderId);
    premultiplyAtlas.textures[1] = 0;
}

static void atlasWriteVertexSlots(float* destination, unsigned quads) {
    for (unsigned quad = 0; quad < quads; ++quad)
        for (unsigned vertex = 0; vertex < VERTICES_PER_QUAD; ++vertex)
            *destination++ = premultiplyAtlas.slots[quad];
}

static void atlasReport(const GLModernRenderer* renderer) {
    const int32_t frame = renderer->base.base.runner->frameCount;
    if (premultiplyAtlas.renderer != renderer || frame <= 0 || frame % 90 != 0) return;
    logInfo("HLD atlas batch frame=%d joins=%u draws=%u prefix_draws=%u valid=%d\n", frame,
            premultiplyAtlas.joins, premultiplyAtlas.draws, premultiplyAtlas.prefixDraws, premultiplyAtlas.valid);
    premultiplyAtlas.joins = premultiplyAtlas.draws = premultiplyAtlas.prefixDraws = 0;
}
'''


ATLAS_INIT = r'''
static void initPremultiplyAtlas(GLModernRenderer* renderer, const Shader* asset, int32_t index,
                                 const char* vertex, const char* fragment) {
    const GMLShader* original = &renderer->gmlShaders[index];
    if (!original->compiled || original->gmBaseTexture == nullptr || original->gmBaseTexture->samplerSlot != 0 ||
        !originalPremultiplyShader(asset, renderer->isGLES)) return;
    const GLint originalLuminosity = glGetUniformLocation(original->shaderId, "luminosity");
    if (originalLuminosity < 0) return;
    GMLShader* program = &premultiplyAtlas.program;
    if (compilePremultiplyAtlas(renderer, asset, vertex, fragment, 2, program)) {
        const GLint second = glGetUniformLocation(program->shaderId, "atlasSecond");
        const GLint luminosity = glGetUniformLocation(program->shaderId, "luminosity");
        if (second >= 0 && luminosity >= 0 && program->gmBaseTexture != nullptr && program->gmMatrices != nullptr &&
            glGetAttribLocation(program->shaderId, "in_AtlasSlot") == 3) {
            glUseProgram(program->shaderId);
            glUniform1i(program->gmBaseTexture->location, 0);
            glUniform1i(second, 1);
            premultiplyAtlas.renderer = renderer;
            premultiplyAtlas.originalIndex = index;
            premultiplyAtlas.originalLuminosity = originalLuminosity;
            premultiplyAtlas.luminosityUniform = luminosity;
            premultiplyAtlas.valid = true;
        }
    }
    glUseProgram(original->shaderId);
    if (!premultiplyAtlas.valid) {
        freeShader(program);
        memset(program, 0, sizeof(*program));
    }
    logInfo("HLD premultiply atlas batching enabled=%d\n", premultiplyAtlas.valid);
}
'''


def with_premultiply_atlas_batches(code, *, launch_flag=None):
    begin = code.index("static void flushBatch(GLRenderer* gl) {")
    end = code.index("static void flushIfNeededAndSetActiveState(", begin)
    flush = code[begin:end]
    anchor = "    if (gl->base.currentShader != -1) {"
    flush = replace_once(flush, anchor, '''    const bool mixedAtlas = atlasBatchPending(modernGl);
    GLint previousAtlasTexture = 0;
    if (mixedAtlas) {
        previousAtlasTexture = atlasBindProgram(modernGl);
    } else if (gl->base.currentShader != -1) {''')
    anchor = "#ifdef PLATFORM_VITA\n    // Each submitted batch owns only the mapped scratch bytes it actually uses."
    flush = replace_once(flush, anchor, '''    const size_t slotBytes = mixedAtlas ? (size_t)modernGl->batchCount * VERTICES_PER_QUAD * sizeof(float) : 0;
''' + anchor)
    flush = replace_once(flush, "vglAllocFromScratch(vertexBytes)", "vglAllocFromScratch(vertexBytes + slotBytes)")
    anchor = "    vglBufferData(GL_ARRAY_BUFFER, mappedVertices);\n#else\n    glBufferData(GL_ARRAY_BUFFER, vertexBytes, gl->vertexData, GL_STREAM_DRAW);"
    flush = replace_once(flush, anchor, '''    if (mixedAtlas) atlasWriteVertexSlots((float*)((char*)mappedVertices + vertexBytes), modernGl->batchCount);
    vglBufferData(GL_ARRAY_BUFFER, mappedVertices);
#else
    if (mixedAtlas) {
        glBufferData(GL_ARRAY_BUFFER, vertexBytes + slotBytes, nullptr, GL_STREAM_DRAW);
        glBufferSubData(GL_ARRAY_BUFFER, 0, vertexBytes, gl->vertexData);
        float slots[MAX_QUADS * VERTICES_PER_QUAD];
        atlasWriteVertexSlots(slots, modernGl->batchCount);
        glBufferSubData(GL_ARRAY_BUFFER, vertexBytes, slotBytes, slots);
    } else {
        glBufferData(GL_ARRAY_BUFFER, vertexBytes, gl->vertexData, GL_STREAM_DRAW);
    }''')
    anchor = "    if (modernGl->batchType == BATCHTYPE_QUAD) {"
    flush = replace_once(flush, anchor, '''    if (mixedAtlas) {
        glVertexAttribPointer(3, 1, GL_FLOAT, GL_FALSE, sizeof(float), (void*)(uintptr_t)vertexBytes);
        glEnableVertexAttribArray(3);
    }
''' + anchor)
    anchor = "#ifdef PLATFORM_VITA\n    if (restoreProgram != 0) glUseProgram(restoreProgram);"
    flush = replace_once(flush, anchor, "    if (mixedAtlas) atlasRestoreProgram(modernGl, previousAtlasTexture);\n" + anchor)
    code = code[:begin] + ATLAS_BATCH + flush + code[end:]

    begin = code.index("static void flushIfNeededAndSetActiveState(")
    end = code.index("static bool glResolveTextureHandle(", begin)
    region = code[begin:end]
    first = region.index("    if (modernGl->batchCount != 0) {")
    last = region.index("    modernGl->batchType = batchType;", first)
    region = region[:first] + '''    if (modernGl->batchCount == MAX_QUADS && modernGl->batchType == batchType &&
        modernGl->currentTextureId == textureId && !modernGl->batchLocalUV)
        atlasFlushCompletedRuns(gl);
    if (modernGl->batchCount != 0) {
        if (modernGl->batchType != batchType || modernGl->batchLocalUV || modernGl->batchCount == MAX_QUADS) {
            flushBatch(gl);
        } else if (modernGl->currentTextureId != textureId &&
                   (batchType != BATCHTYPE_QUAD || !atlasJoinTexture(modernGl, textureId))) {
            flushBatch(gl);
        }
    }

''' + region[last:]
    code = code[:begin] + region + code[end:]

    anchor = "    flushIfNeededAndSetActiveState(gl, BATCHTYPE_QUAD, texId);"
    code = replace_once(code, anchor, anchor + '''
    if (atlasBatchPending(modernGl)) premultiplyAtlas.slots[modernGl->batchCount] = (uint8_t)premultiplyAtlas.tailSlot;''')

    anchor = "#ifdef PLATFORM_VITA\nstatic uint32_t glFloatUniformWidth(GLenum type) {"
    code = replace_once(code, anchor, ATLAS_PROGRAM + ATLAS_INIT + anchor)
    anchor = "        if (patchedVertexSource) free(patchedVertexSource);\n"
    enabled = f"{launch_flag} && " if launch_flag else ""
    code = replace_once(code, anchor, f'''        if ({enabled}dataWin->gen8.gameID == 742741103 && dataWin->gen8.wadVersion == 15)
            initPremultiplyAtlas(modernGl, shdr, (int32_t)i, vertexShaderSource, fragmentShaderSource);
''' + anchor)
    if launch_flag:
        begin = code.index("static struct {\n    const GLModernRenderer* renderer;\n    int32_t originalIndex;")
        code = code[:begin] + f"extern bool {launch_flag};\n\n" + code[begin:]

    anchor = "    freeShader(modernGl->defaultShaderProgram);"
    code = replace_once(code, anchor, '''    if (premultiplyAtlas.renderer == modernGl) {
        freeShader(&premultiplyAtlas.program);
        memset(&premultiplyAtlas, 0, sizeof(premultiplyAtlas));
    }
''' + anchor)
    for method in ("glBeginFrame", "glBeginView", "glBeginGUI"):
        begin = code.index(f"static void {method}(")
        end = code.index("\n}\n", begin)
        offset = code.index("    modernGl->batchCount = 0;", begin, end)
        code = code[:offset] + "    atlasFlushCompletedRuns(&modernGl->base);\n" + code[offset:]
    anchor = "static void glEndFrameEnd(Renderer* renderer) {\n    GLRenderer* gl = (GLRenderer*) renderer;\n    GLModernRenderer* modernGl = (GLModernRenderer*) gl;"
    code = replace_once(code, anchor, anchor + "\n    atlasReport(modernGl);")

    for method, hook in (
        ("glShaderSetUniformF", "type == GL_FLOAT, value1"),
        ("glShaderSetUniformFArray", "type == GL_FLOAT && count == 1, values[0]"),
        ("glShaderSetUniformI", "false, 0.0f"),
    ):
        begin = code.index(f"static void {method}(")
        end = code.index("\n}\n", begin)
        declaration = "    GLenum type = glShaderGetUniformTypeByLocation(shader, handle);"
        offset = code.index(declaration, begin, end) + len(declaration)
        code = code[:offset] + f"\n    atlasSetUserUniform(modernGl, handle, {hook});" + code[offset:]
    return code


def with_atlas_batch_upload_cache(code):
    anchor = "    unsigned joins, draws, prefixDraws;\n} premultiplyAtlas;"
    code = replace_once(code, anchor, '''    unsigned joins, draws, prefixDraws;
    int32_t firstJoin;
    bool matricesUploaded, luminosityUploaded;
    Matrix4f uploadedMatrices[MATRICES_MAX];
    float uploadedLuminosity;
    unsigned matrixUploads, luminosityUploads, singleTexturePrefixes;
} premultiplyAtlas;''')
    anchor = "        premultiplyAtlas.textures[1] = texture;"
    code = replace_once(code, anchor, anchor + "\n        premultiplyAtlas.firstJoin = renderer->batchCount;")
    anchor = "    ++premultiplyAtlas.prefixDraws;\n    flushBatch(gl);"
    code = replace_once(code, anchor, '''    ++premultiplyAtlas.prefixDraws;
    if (first == premultiplyAtlas.firstJoin) {
        premultiplyAtlas.textures[1] = 0;
        ++premultiplyAtlas.singleTexturePrefixes;
    }
    flushBatch(gl);''')
    anchor = '''    glUploadGameShaderBuiltins(&renderer->base, program);
    glUniform1f(premultiplyAtlas.luminosityUniform, premultiplyAtlas.luminosity);'''
    code = replace_once(code, anchor, '''    const Matrix4f* matrices = renderer->base.base.gmlMatrices;
    const bool matrixCacheable = program->gmFogColour == nullptr &&
        program->gmAlphaTestEnabled == nullptr && program->gmAlphaRefValue == nullptr;
    if (!matrixCacheable || !premultiplyAtlas.matricesUploaded ||
        memcmp(premultiplyAtlas.uploadedMatrices, matrices, sizeof(premultiplyAtlas.uploadedMatrices)) != 0) {
        glUploadGameShaderBuiltins(&renderer->base, program);
        memcpy(premultiplyAtlas.uploadedMatrices, matrices, sizeof(premultiplyAtlas.uploadedMatrices));
        premultiplyAtlas.matricesUploaded = true;
        ++premultiplyAtlas.matrixUploads;
    }
    if (!premultiplyAtlas.luminosityUploaded ||
        memcmp(&premultiplyAtlas.uploadedLuminosity, &premultiplyAtlas.luminosity, sizeof(float)) != 0) {
        glUniform1f(premultiplyAtlas.luminosityUniform, premultiplyAtlas.luminosity);
        premultiplyAtlas.uploadedLuminosity = premultiplyAtlas.luminosity;
        premultiplyAtlas.luminosityUploaded = true;
        ++premultiplyAtlas.luminosityUploads;
    }''')
    anchor = "    premultiplyAtlas.joins = premultiplyAtlas.draws = premultiplyAtlas.prefixDraws = 0;"
    code = replace_once(code, anchor, '''    logInfo("HLD atlas uploads frame=%d matrices=%u luminosity=%u single_texture_prefixes=%u\\n",
            frame, premultiplyAtlas.matrixUploads, premultiplyAtlas.luminosityUploads,
            premultiplyAtlas.singleTexturePrefixes);
    premultiplyAtlas.matrixUploads = premultiplyAtlas.luminosityUploads = premultiplyAtlas.singleTexturePrefixes = 0;
''' + anchor)

    # VitaGL treats attributes sourced from the same VBO as interleaved.
    # Keep one packed layout for each program on both replay platforms.
    begin = code.index("static void atlasWriteVertexSlots(")
    end = code.index("static void atlasReport(", begin)
    code = code[:begin] + r'''typedef struct {
    GlVertex vertex;
    float slot;
} AtlasVertex;

static void atlasWriteVertices(AtlasVertex* destination, const GlVertex* source, unsigned quads) {
    for (unsigned quad = 0; quad < quads; ++quad) {
        const float slot = premultiplyAtlas.slots[quad];
        for (unsigned index = 0; index < VERTICES_PER_QUAD; ++index) {
            destination->vertex = *source++;
            destination->slot = slot;
            ++destination;
        }
    }
}

static void atlasSetVertexLayout(GLsizei stride) {
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, stride, (void*)offsetof(GlVertex, x));
    glVertexAttribPointer(1, 4, GL_UNSIGNED_BYTE, GL_TRUE, stride, (void*)offsetof(GlVertex, r));
    glVertexAttribPointer(2, 2, GL_FLOAT, GL_FALSE, stride, (void*)offsetof(GlVertex, u));
}

''' + code[end:]
    begin = code.index("    size_t vertexBytes = (size_t)modernGl->batchCount * verticesPerItem * sizeof(GlVertex);")
    end = code.index("\n\n    if (hasVAO()) {", begin)
    code = code[:begin] + r'''    size_t vertexBytes = (size_t)modernGl->batchCount * verticesPerItem *
        (mixedAtlas ? sizeof(AtlasVertex) : sizeof(GlVertex));
#ifdef PLATFORM_VITA
    void* mappedVertices = vglAllocFromScratch(vertexBytes);
    if (mappedVertices == nullptr) {
        logError("GL: unable to reserve %zu bytes for a Vita vertex batch\n", vertexBytes);
        abort();
    }
    if (mixedAtlas) {
        atlasWriteVertices(mappedVertices, gl->vertexData, modernGl->batchCount);
    } else {
        memcpy(mappedVertices, gl->vertexData, vertexBytes);
    }
    vglBufferData(GL_ARRAY_BUFFER, mappedVertices);
#else
    if (mixedAtlas) {
        AtlasVertex* vertices = safeMalloc(vertexBytes);
        atlasWriteVertices(vertices, gl->vertexData, modernGl->batchCount);
        glBufferData(GL_ARRAY_BUFFER, vertexBytes, vertices, GL_STREAM_DRAW);
        free(vertices);
    } else {
        glBufferData(GL_ARRAY_BUFFER, vertexBytes, gl->vertexData, GL_STREAM_DRAW);
    }
#endif
''' + code[end:]
    anchor = "        glVertexAttribPointer(3, 1, GL_FLOAT, GL_FALSE, sizeof(float), (void*)(uintptr_t)vertexBytes);"
    code = replace_once(code, anchor, '''        atlasSetVertexLayout(sizeof(AtlasVertex));
        glVertexAttribPointer(3, 1, GL_FLOAT, GL_FALSE, sizeof(AtlasVertex), (void*)offsetof(AtlasVertex, slot));''')
    anchor = "    if (mixedAtlas) atlasRestoreProgram(modernGl, previousAtlasTexture);"
    return replace_once(code, anchor, '''    if (mixedAtlas) {
        atlasRestoreProgram(modernGl, previousAtlasTexture);
        atlasSetVertexLayout(sizeof(GlVertex));
    }''')
