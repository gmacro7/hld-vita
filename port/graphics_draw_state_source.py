"""Read locally known GL state without draining pending draw commands."""

from runtime_verification_source import replace_once


DRAW_STATE = r'''
typedef enum {
    DRAW_QUERY_SCISSOR, DRAW_QUERY_BLEND, DRAW_QUERY_DEPTH, DRAW_QUERY_STENCIL,
    DRAW_QUERY_COLOR_MASK, DRAW_QUERY_FRAMEBUFFER,
    DRAW_QUERY_SRC_RGB, DRAW_QUERY_DST_RGB, DRAW_QUERY_SRC_ALPHA, DRAW_QUERY_DST_ALPHA,
    DRAW_QUERY_EQUATION_RGB, DRAW_QUERY_EQUATION_ALPHA,
    DRAW_QUERY_COUNT
} DrawStateQuery;

static uint32_t knownDrawState;
static GLint drawState[DRAW_QUERY_COUNT];
static GLboolean drawColorMask[4];
static unsigned mirroredDrawQueries, synchronousDrawQueries;

static DrawStateQuery drawCapabilityQuery(GLenum cap) {
    switch (cap) {
        case GL_SCISSOR_TEST: return DRAW_QUERY_SCISSOR;
        case GL_BLEND: return DRAW_QUERY_BLEND;
        case GL_DEPTH_TEST: return DRAW_QUERY_DEPTH;
        case GL_STENCIL_TEST: return DRAW_QUERY_STENCIL;
        default: return DRAW_QUERY_COUNT;
    }
}

static DrawStateQuery drawIntegerQuery(GLenum pname) {
    switch (pname) {
        case GL_FRAMEBUFFER_BINDING: return DRAW_QUERY_FRAMEBUFFER;
        case GL_BLEND_SRC_RGB: return DRAW_QUERY_SRC_RGB;
        case GL_BLEND_DST_RGB: return DRAW_QUERY_DST_RGB;
        case GL_BLEND_SRC_ALPHA: return DRAW_QUERY_SRC_ALPHA;
        case GL_BLEND_DST_ALPHA: return DRAW_QUERY_DST_ALPHA;
        case GL_BLEND_EQUATION: return DRAW_QUERY_EQUATION_RGB;
        case GL_BLEND_EQUATION_ALPHA: return DRAW_QUERY_EQUATION_ALPHA;
        default: return DRAW_QUERY_COUNT;
    }
}

static void rememberDrawState(DrawStateQuery query, GLint value) {
    drawState[query] = value;
    knownDrawState |= UINT32_C(1) << query;
}

static bool drawStateKnown(DrawStateQuery query) {
    return (knownDrawState & (UINT32_C(1) << query)) != 0;
}

static void rememberCapability(GLenum cap, GLboolean enabled) {
    const DrawStateQuery query = drawCapabilityQuery(cap);
    if (query != DRAW_QUERY_COUNT) rememberDrawState(query, enabled);
}

static void rememberColorMask(GLboolean r, GLboolean g, GLboolean b, GLboolean a) {
    drawColorMask[0] = r != GL_FALSE;
    drawColorMask[1] = g != GL_FALSE;
    drawColorMask[2] = b != GL_FALSE;
    drawColorMask[3] = a != GL_FALSE;
    knownDrawState |= UINT32_C(1) << DRAW_QUERY_COLOR_MASK;
}

static void invalidateBlendFactors(void) {
    knownDrawState &= ~((UINT32_C(1) << DRAW_QUERY_SRC_RGB) |
                        (UINT32_C(1) << DRAW_QUERY_DST_RGB) |
                        (UINT32_C(1) << DRAW_QUERY_SRC_ALPHA) |
                        (UINT32_C(1) << DRAW_QUERY_DST_ALPHA));
}
'''


READ_QUERIES = r'''
GLboolean __real_glIsEnabled(GLenum cap);
void __real_glGetBooleanv(GLenum pname, GLboolean* values);
void __real_glGetIntegerv(GLenum pname, GLint* values);

static void synchronizeDrawQuery(void) {
    waitForGraphicsChunk();
    executeRecordedGraphicsOnMain();
    ++synchronousDrawQueries;
}

GLboolean hldRecordedIsEnabled(GLenum cap) {
    const DrawStateQuery query = drawCapabilityQuery(cap);
    if (!hldVitaGraphicsRecording || query == DRAW_QUERY_COUNT) return glIsEnabled(cap);
    if (!drawStateKnown(query)) {
        synchronizeDrawQuery();
        rememberDrawState(query, __real_glIsEnabled(cap));
    } else {
        ++mirroredDrawQueries;
    }
    return (GLboolean)drawState[query];
}

void hldRecordedGetBooleanv(GLenum pname, GLboolean* values) {
    if (!hldVitaGraphicsRecording || pname != GL_COLOR_WRITEMASK || values == NULL) {
        glGetBooleanv(pname, values);
        return;
    }
    if (!drawStateKnown(DRAW_QUERY_COLOR_MASK)) {
        synchronizeDrawQuery();
        __real_glGetBooleanv(pname, drawColorMask);
        knownDrawState |= UINT32_C(1) << DRAW_QUERY_COLOR_MASK;
    } else {
        ++mirroredDrawQueries;
    }
    memcpy(values, drawColorMask, sizeof(drawColorMask));
}

void hldRecordedGetIntegerv(GLenum pname, GLint* values) {
    const DrawStateQuery query = drawIntegerQuery(pname);
    if (!hldVitaGraphicsRecording || query == DRAW_QUERY_COUNT || values == NULL) {
        glGetIntegerv(pname, values);
        return;
    }
    if (!drawStateKnown(query)) {
        synchronizeDrawQuery();
        __real_glGetIntegerv(pname, values);
        rememberDrawState(query, *values);
    } else {
        ++mirroredDrawQueries;
        *values = drawState[query];
    }
}
'''


STATE_WRITES = {
    "glEnable": ("{x0}", "rememberCapability(x0, GL_TRUE);"),
    "glDisable": ("{x0}", "rememberCapability(x0, GL_FALSE);"),
    "glColorMask": ("{x0, x1, x2, x3}", "rememberColorMask(x0, x1, x2, x3);"),
    "glBindFramebuffer": ("{x0, x1}", """if (x0 == GL_FRAMEBUFFER || x0 == GL_DRAW_FRAMEBUFFER)
        rememberDrawState(DRAW_QUERY_FRAMEBUFFER, (GLint)x1);"""),
    "glBlendFunc": ("{x0, x1}", "invalidateBlendFactors();"),
    "glBlendFuncSeparate": ("{x0, x1, x2, x3}", "invalidateBlendFactors();"),
    "glBlendEquation": ("{x0}", """knownDrawState &= ~((UINT32_C(1) << DRAW_QUERY_EQUATION_RGB) |
                        (UINT32_C(1) << DRAW_QUERY_EQUATION_ALPHA));"""),
}


def with_draw_state_mirrors(code, *, extended=False):
    state = DRAW_STATE
    writes = STATE_WRITES
    reads = READ_QUERIES
    if extended:
        state = replace_once(state, "    DRAW_QUERY_COUNT",
                             "    DRAW_QUERY_ACTIVE_TEXTURE, DRAW_QUERY_CULL_FACE,\n    DRAW_QUERY_COUNT")
        anchor = "        case GL_STENCIL_TEST: return DRAW_QUERY_STENCIL;"
        state = replace_once(state, anchor, anchor + "\n        case GL_CULL_FACE: return DRAW_QUERY_CULL_FACE;")
        anchor = "        case GL_FRAMEBUFFER_BINDING: return DRAW_QUERY_FRAMEBUFFER;"
        state = replace_once(state, anchor, anchor + "\n        case GL_ACTIVE_TEXTURE: return DRAW_QUERY_ACTIVE_TEXTURE;")
        state += r'''
static bool canonicalBlendFactor(GLenum factor) {
    return factor == GL_ZERO || factor == GL_ONE || factor == GL_SRC_ALPHA || factor == GL_ONE_MINUS_SRC_ALPHA;
}

static void rememberBlendFactors(GLenum src, GLenum dst, GLenum srcAlpha, GLenum dstAlpha) {
    if (!canonicalBlendFactor(src) || !canonicalBlendFactor(dst) ||
        !canonicalBlendFactor(srcAlpha) || !canonicalBlendFactor(dstAlpha)) {
        invalidateBlendFactors();
        return;
    }
    rememberDrawState(DRAW_QUERY_SRC_RGB, src);
    rememberDrawState(DRAW_QUERY_DST_RGB, dst);
    rememberDrawState(DRAW_QUERY_SRC_ALPHA, srcAlpha);
    rememberDrawState(DRAW_QUERY_DST_ALPHA, dstAlpha);
}

static unsigned synchronousQueryGeometry;
'''
        equation_args, equation_invalidate = STATE_WRITES["glBlendEquation"]
        writes = STATE_WRITES | {
            "glActiveTexture": ("{x0}", """if (x0 >= GL_TEXTURE0 && x0 <= GL_TEXTURE15)
        rememberDrawState(DRAW_QUERY_ACTIVE_TEXTURE, (GLint)x0);
    else
        knownDrawState &= ~(UINT32_C(1) << DRAW_QUERY_ACTIVE_TEXTURE);"""),
            "glBlendFunc": ("{x0, x1}", "rememberBlendFactors(x0, x1, x0, x1);"),
            "glBlendFuncSeparate": ("{x0, x1, x2, x3}", "rememberBlendFactors(x0, x1, x2, x3);"),
            "glBlendEquation": (equation_args, """if (x0 == GL_FUNC_ADD) {
        rememberDrawState(DRAW_QUERY_EQUATION_RGB, GL_FUNC_ADD);
        rememberDrawState(DRAW_QUERY_EQUATION_ALPHA, GL_FUNC_ADD);
    } else {
        """ + equation_invalidate + """
    }"""),
        }
        anchor = "static void synchronizeDrawQuery(void) {"
        reads = replace_once(reads, anchor, anchor + "\n    synchronousQueryGeometry += graphicsRecording->geometryCount;")

    anchor = "static bool insideGraphicsDrain;"
    code = replace_once(code, anchor, anchor + "\n" + state)
    anchor = "    if (sceKernelGetThreadId() != swapMain || insideGraphicsDrain) return;"
    code = replace_once(code, anchor, anchor + "\n    knownDrawState = 0;")
    anchor = "    hldVitaGraphicsRecording = true;"
    code = replace_once(code, anchor, "    knownDrawState = 0;\n" + anchor)
    for name, (arguments, update) in writes.items():
        anchor = f"    command->args.{name} = (__typeof__(command->args.{name})){arguments};"
        code = replace_once(code, anchor, anchor + "\n    " + update)
    anchor = "    graphicsFenceNanos = 0;"
    code = replace_once(code, anchor, anchor + r'''
    logInfo("Vita draw state queries frame=%d mirrored=%u synchronized=%u\n",
            frame, mirroredDrawQueries, synchronousDrawQueries);
    mirroredDrawQueries = synchronousDrawQueries = 0;''')
    if extended:
        anchor = "    mirroredDrawQueries = synchronousDrawQueries = 0;"
        code = replace_once(code, anchor, anchor + r'''
    logInfo("Vita draw query drain frame=%d geometry=%u\n", frame, synchronousQueryGeometry);
    synchronousQueryGeometry = 0;''')
    return code + "\n" + reads
