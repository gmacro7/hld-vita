"""Record ordered VitaGL state and copied sprite vertices for the render worker."""

from pathlib import Path

from runtime_verification_source import replace_once
from graphics_draw_state_source import with_draw_state_mirrors
from graphics_stream_source import with_graphics_stream


# Limit recording to state calls with by-value arguments, uniform calls whose
# inputs fit in one copied vector/matrix, and explicit owned geometry packets.
SCALARS = {
    "glActiveTexture": ("GLenum",),
    "glBindBuffer": ("GLenum", "GLuint"),
    "glBindFramebuffer": ("GLenum", "GLuint"),
    "glBindTexture": ("GLenum", "GLuint"),
    "glBindVertexArray": ("GLuint",),
    "glBlendEquation": ("GLenum",),
    "glBlendFunc": ("GLenum", "GLenum"),
    "glBlendFuncSeparate": ("GLenum",) * 4,
    "glClear": ("GLbitfield",),
    "glClearColor": ("GLfloat",) * 4,
    "glColorMask": ("GLboolean",) * 4,
    "glCullFace": ("GLenum",),
    "glDepthFunc": ("GLenum",),
    "glDepthMask": ("GLboolean",),
    "glDisable": ("GLenum",),
    "glEnable": ("GLenum",),
    "glFrontFace": ("GLenum",),
    "glScissor": ("GLint", "GLint", "GLsizei", "GLsizei"),
    "glUniform1f": ("GLint", "GLfloat"),
    "glUniform1i": ("GLint", "GLint"),
    "glUniform2f": ("GLint", "GLfloat", "GLfloat"),
    "glUniform3f": ("GLint", "GLfloat", "GLfloat", "GLfloat"),
    "glUniform4f": ("GLint", "GLfloat", "GLfloat", "GLfloat", "GLfloat"),
    "glUseProgram": ("GLuint",),
    "glViewport": ("GLint", "GLint", "GLsizei", "GLsizei"),
}
EXTRA_SCALARS = {
    "glClearDepth": ("GLdouble",),
    "glClearDepthf": ("GLfloat",),
    "glPixelStorei": ("GLenum", "GLint"),
    "glTexParameteri": ("GLenum", "GLenum", "GLint"),
    "glUniform2i": ("GLint",) * 3,
    "glUniform3i": ("GLint",) * 4,
    "glUniform4i": ("GLint",) * 5,
}
VECTORS = {
    "glUniform1fv": (1, False),
    "glUniform2fv": (2, False),
    "glUniform3fv": (3, False),
    "glUniform4fv": (4, False),
    "glUniformMatrix2fv": (4, True),
    "glUniformMatrix3fv": (9, True),
    "glUniformMatrix4fv": (16, True),
}

GEOMETRY = "bool hldRecordVitaGeometry(const void* data, size_t bytes, GLuint vbo, GLuint vao, GLsizei count, bool indexed)"


def scalar_signature(types):
    return ", ".join(f"{typ} x{index}" for index, typ in enumerate(types))


def vector_signature(matrix):
    suffix = "GLboolean transpose, " if matrix else ""
    return f"GLint location, GLsizei count, {suffix}const GLfloat* value"


def declarations(scalars=SCALARS):
    return [
        *(f"void hldRecord_{name}({scalar_signature(types)});" for name, types in scalars.items()),
        *(f"void hldRecord_{name}({vector_signature(matrix)});" for name, (_, matrix) in VECTORS.items()),
        GEOMETRY + ";",
    ]


def command_fields(scalars=SCALARS):
    return [
        *(f"    struct {{ {scalar_signature(types).replace(', ', '; ')}; }} {name};" for name, types in scalars.items()),
        "    struct { GLint location; GLboolean transpose; GLfloat value[16]; } uniform;",
    ]


def command_cases(scalars=SCALARS):
    for name, types in scalars.items():
        values = ", ".join(f"command->args.{name}.x{index}" for index in range(len(types)))
        yield f"            case CMD_{name}: __real_{name}({values}); break;"
    for name, (_, matrix) in VECTORS.items():
        transpose = "command->args.uniform.transpose, " if matrix else ""
        yield (f"            case CMD_{name}: __real_{name}(command->args.uniform.location, 1, "
               f"{transpose}command->args.uniform.value); break;")


def recording_functions(scalars=SCALARS):
    for name, types in scalars.items():
        values = ", ".join(f"x{index}" for index in range(len(types)))
        yield f'''void hldRecord_{name}({scalar_signature(types)}) {{
    VitaGraphicsCommand* command = reserveGraphicsCommand(CMD_{name}, 0);
    if (command == NULL) {{
        {name}({values});
        return;
    }}
    command->args.{name} = (__typeof__(command->args.{name})){{{values}}};
}}'''
    for name, (elements, matrix) in VECTORS.items():
        transpose = "transpose, " if matrix else ""
        set_transpose = "    command->args.uniform.transpose = transpose;\n" if matrix else ""
        yield f'''void hldRecord_{name}({vector_signature(matrix)}) {{
    VitaGraphicsCommand* command = count == 1 && value != NULL ? reserveGraphicsCommand(CMD_{name}, 0) : NULL;
    if (command == NULL) {{
        {name}(location, count, {transpose}value);
        return;
    }}
    command->args.uniform.location = location;
{set_transpose}    memcpy(command->args.uniform.value, value, {elements} * sizeof(*value));
}}'''


def graphics_command_source(*, framebuffer_shadow=False, render_tail=False, draw_state=False, fence_apis=None,
                            matrix_arrays=False, stream=False):
    scalars = SCALARS | EXTRA_SCALARS if fence_apis is not None else SCALARS
    driver = Path(__file__).with_name("vita-graphics-commands.inc").read_text()
    prototypes = [
        *(f"void __real_{name}({scalar_signature(types)});" for name, types in scalars.items()),
        *(f"void __real_{name}({vector_signature(matrix)});" for name, (_, matrix) in VECTORS.items()),
    ]
    driver = replace_once(driver, "/* GL declarations */", "\n".join(prototypes))
    driver = replace_once(driver, "/* GL command kinds */",
                          "\n".join(f"    CMD_{name}," for name in (*scalars, *VECTORS)))
    driver = replace_once(driver, "/* GL command arguments */", "\n".join(command_fields(scalars)))
    driver = replace_once(driver, "/* GL command cases */", "\n".join(command_cases(scalars)))
    driver += "\n\n" + "\n\n".join(recording_functions(scalars)) + "\n"
    if draw_state:
        driver = with_draw_state_mirrors(driver, extended=fence_apis is not None)
        if fence_apis is None:
            driver = replace_once(driver, "VITA_GRAPHICS_CHUNK_COMMANDS = 32", "VITA_GRAPHICS_CHUNK_COMMANDS = 128")
    elif framebuffer_shadow:
        driver = with_framebuffer_shadow(driver)
    if render_tail:
        driver = with_render_tail(driver)
    if matrix_arrays:
        driver = with_matrix_array_packets(driver)
    if fence_apis is not None:
        driver = profile_graphics_fences(driver, fence_apis)
    return with_graphics_stream(driver) if stream else driver


def with_matrix_array_packets(code):
    anchor = "    CMD_GEOMETRY,"
    code = replace_once(code, anchor, anchor + "\n    CMD_MATRIX_ARRAY,")
    anchor = "typedef struct {\n    size_t count, bytes;"
    code = replace_once(code, anchor, r'''
enum { VITA_GRAPHICS_MAX_MATRICES = 5, VITA_GRAPHICS_MATRIX_ELEMENTS = 16 };
typedef struct {
    GLint location;
    GLsizei count;
    GLboolean transpose;
    GLfloat values[VITA_GRAPHICS_MAX_MATRICES * VITA_GRAPHICS_MATRIX_ELEMENTS];
} VitaGraphicsMatrixArray;

''' + anchor)
    anchor = "    VitaGraphicsCommand commands[VITA_GRAPHICS_CHUNK_COMMANDS];"
    code = replace_once(code, anchor, anchor + """
    VitaGraphicsMatrixArray matrixArrays[VITA_GRAPHICS_CHUNK_COMMANDS];""")
    anchor = "static uint64_t graphicsFenceNanos;"
    code = replace_once(code, anchor, anchor + "\nstatic unsigned recordedMatrixArrays;")
    anchor = "            case CMD_GEOMETRY: {"
    code = replace_once(code, anchor, r'''            case CMD_MATRIX_ARRAY: {
                const VitaGraphicsMatrixArray* upload = &chunk->matrixArrays[i];
                __real_glUniformMatrix4fv(upload->location, upload->count, upload->transpose, upload->values);
                break;
            }
''' + anchor)
    anchor = "void hldRecord_glUniformMatrix4fv(GLint location, GLsizei count, GLboolean transpose, const GLfloat* value) {"
    code = replace_once(code, anchor, r'''static bool recordMatrixArray(GLint location, GLsizei count, GLboolean transpose, const GLfloat* value) {
    if (count < 2 || count > VITA_GRAPHICS_MAX_MATRICES || value == NULL) return false;
    if (reserveGraphicsCommand(CMD_MATRIX_ARRAY, 0) == NULL) return false;
    VitaGraphicsMatrixArray* upload = &graphicsRecording->matrixArrays[graphicsRecording->count - 1];
    upload->location = location;
    upload->count = count;
    upload->transpose = transpose;
    memcpy(upload->values, value, (size_t)count * VITA_GRAPHICS_MATRIX_ELEMENTS * sizeof(*value));
    ++recordedMatrixArrays;
    return true;
}

''' + anchor + "\n    if (recordMatrixArray(location, count, transpose, value)) return;")
    anchor = "    graphicsFenceNanos = 0;"
    return replace_once(code, anchor, anchor + r'''
    logInfo("Vita matrix arrays frame=%d recorded=%u\n", frame, recordedMatrixArrays);
    recordedMatrixArrays = 0;''')


def with_framebuffer_shadow(code):
    anchor = "static bool insideGraphicsDrain;"
    code = replace_once(code, anchor, anchor + r'''
static GLuint graphicsDrawFramebuffer;
static bool graphicsDrawFramebufferValid;
static unsigned framebufferReads, framebufferReadbacks;
''')
    anchor = "    if (sceKernelGetThreadId() != swapMain || insideGraphicsDrain) return;"
    # Calls through the ordinary bridge may mutate VitaGL binding state.
    # Invalidate the mirror before the real call, even for rare GL operations
    # that affect the binding indirectly.
    code = replace_once(code, anchor, anchor + "\n    graphicsDrawFramebufferValid = false;")
    anchor = "    hldVitaGraphicsRecording = true;"
    code = replace_once(code, anchor, "    graphicsDrawFramebufferValid = false;\n" + anchor)
    anchor = "    command->args.glBindFramebuffer = (__typeof__(command->args.glBindFramebuffer)){x0, x1};"
    code = replace_once(code, anchor, anchor + r'''
    if (x0 == GL_FRAMEBUFFER || x0 == GL_DRAW_FRAMEBUFFER) {
        graphicsDrawFramebuffer = x1;
        graphicsDrawFramebufferValid = true;
    }''')
    anchor = "    graphicsFenceNanos = 0;"
    code = replace_once(code, anchor, anchor + r'''
    logInfo("Vita framebuffer binding frame=%d cached=%u queries=%u\n",
            frame, framebufferReads, framebufferReadbacks);
    framebufferReads = framebufferReadbacks = 0;''')
    return code + r'''
void hldRecordedGetIntegerv(GLenum pname, GLint* values) {
    if (!hldVitaGraphicsRecording || pname != GL_FRAMEBUFFER_BINDING || values == NULL) {
        glGetIntegerv(pname, values);
        return;
    }
    if (!graphicsDrawFramebufferValid) {
        ++framebufferReadbacks;
        glGetIntegerv(pname, values);
        graphicsDrawFramebuffer = (GLuint)*values;
        graphicsDrawFramebufferValid = true;
        return;
    }
    ++framebufferReads;
    *values = (GLint)graphicsDrawFramebuffer;
}
'''


def profile_graphics_fences(code, apis):
    names = "\n".join(f'    "{api}",' for api in apis)
    anchor = "static bool insideGraphicsDrain;"
    code = replace_once(code, anchor, anchor + f"""
static const char* const graphicsFenceNames[] = {{
{names}
}};
enum {{ GRAPHICS_FENCE_COUNT = sizeof(graphicsFenceNames) / sizeof(graphicsFenceNames[0]) }};
static struct {{
    unsigned calls, geometry;
}} graphicsFenceReasons[GRAPHICS_FENCE_COUNT];
""")
    anchor = """void hldDrainVitaGraphics(void) {
    if (sceKernelGetThreadId() != swapMain || insideGraphicsDrain) return;"""
    code = replace_once(code, anchor, """void hldDrainVitaGraphicsForCall(unsigned reason) {
    if (sceKernelGetThreadId() != swapMain || insideGraphicsDrain) return;
    if (reason < GRAPHICS_FENCE_COUNT && graphicsRecording->count != 0) {
        ++graphicsFenceReasons[reason].calls;
        graphicsFenceReasons[reason].geometry += graphicsRecording->geometryCount;
    }""")
    anchor = "static void submitGraphicsChunk(void) {"
    code = replace_once(code, anchor, """static void hldDrainVitaGraphics(void) {
    hldDrainVitaGraphicsForCall(GRAPHICS_FENCE_COUNT);
}

""" + anchor)
    anchor = "    graphicsFenceNanos = 0;"
    return replace_once(code, anchor, anchor + r'''
    for (unsigned rank = 0; rank < 8; ++rank) {
        unsigned best = GRAPHICS_FENCE_COUNT;
        for (unsigned i = 0; i < GRAPHICS_FENCE_COUNT; ++i) {
            if (graphicsFenceReasons[i].calls == 0) continue;
            if (best == GRAPHICS_FENCE_COUNT ||
                graphicsFenceReasons[i].geometry > graphicsFenceReasons[best].geometry ||
                (graphicsFenceReasons[i].geometry == graphicsFenceReasons[best].geometry &&
                 graphicsFenceReasons[i].calls > graphicsFenceReasons[best].calls))
                best = i;
        }
        if (best == GRAPHICS_FENCE_COUNT) break;
        logInfo("Vita graphics fence frame=%d api=%s calls=%u geometry=%u\n", frame, graphicsFenceNames[best],
                graphicsFenceReasons[best].calls, graphicsFenceReasons[best].geometry);
        graphicsFenceReasons[best].calls = 0;
    }
    memset(graphicsFenceReasons, 0, sizeof(graphicsFenceReasons));''')


def with_render_tail(code):
    anchor = "static uint64_t graphicsFenceNanos;"
    code = replace_once(code, anchor, anchor + r'''
extern bool hldRenderTailEnabled;
static VitaGraphicsChunk* presentationTail;
static unsigned presentationTails, presentationTailCommands, presentationTailGeometry;
''')
    anchor = "void hldReportVitaGraphics(int frame) {"
    code = replace_once(code, anchor, r'''
static void prepareRenderTailForPresentation(void) {
    if (!hldVitaGraphicsRecording) return;
    if (!hldRenderTailEnabled) {
        hldEndVitaGraphics();
        return;
    }
    waitForGraphicsChunk();
    hldVitaGraphicsRecording = false;
    if (graphicsRecording->count == 0) return;

    ++presentationTails;
    presentationTailCommands += graphicsRecording->count;
    presentationTailGeometry += graphicsRecording->geometryCount;
    __atomic_store_n(&presentationTail, graphicsRecording, __ATOMIC_RELEASE);
    graphicsRecording = &graphicsChunks[graphicsRecording == &graphicsChunks[0] ? 1 : 0];
    graphicsRecording->count = graphicsRecording->bytes = 0;
    graphicsRecording->geometryCount = 0;
}

static void drawRenderTailBeforeSwap(void) {
    VitaGraphicsChunk* tail = __atomic_exchange_n(&presentationTail, NULL, __ATOMIC_ACQ_REL);
    if (tail != NULL) executeGraphicsCommands(tail);
}

''' + anchor)
    anchor = "    graphicsFenceNanos = 0;"
    return replace_once(code, anchor, anchor + r'''
    logInfo("Vita presentation commands frame=%d enabled=%d tails=%u commands=%u geometry=%u\n",
            frame, (int)hldRenderTailEnabled, presentationTails, presentationTailCommands, presentationTailGeometry);
    presentationTails = presentationTailCommands = presentationTailGeometry = 0;''')


def with_render_tail_platform(code, swap_extension):
    declare = "static void swapNow(void);"
    code = replace_once(code, declare, declare + r'''
static void prepareRenderTailForPresentation(void);
static void drawRenderTailBeforeSwap(void);''')
    start = "static void swapNow(void) {"
    code = replace_once(code, start, start + "\n    drawRenderTailBeforeSwap();")
    anchor = "void platformSwapBuffers(void) {"
    swap_extension = replace_once(swap_extension, anchor, anchor + "\n    prepareRenderTailForPresentation();")
    return code, swap_extension


def with_graphics_recording(code, *, framebuffer_shadow=False, draw_state=False, extended=False):
    scalars = SCALARS | EXTRA_SCALARS if extended else SCALARS
    macros = [*(f"#define {name} hldRecord_{name}" for name in (*scalars, *VECTORS))]
    overrides = declarations(scalars) + macros
    if framebuffer_shadow or draw_state:
        overrides.extend(["void hldRecordedGetIntegerv(GLenum pname, GLint* values);",
                          "#define glGetIntegerv hldRecordedGetIntegerv"])
    if draw_state:
        overrides.extend(["GLboolean hldRecordedIsEnabled(GLenum cap);",
                          "void hldRecordedGetBooleanv(GLenum pname, GLboolean* values);",
                          "#define glIsEnabled hldRecordedIsEnabled",
                          "#define glGetBooleanv hldRecordedGetBooleanv"])
    anchor = '#include "vm_builtins.h"'
    return replace_once(code, anchor, anchor + "\n\n#ifdef PLATFORM_VITA\n" +
                        "\n".join(overrides) + "\n#endif")


def with_graphics_command_renderer(code, *, framebuffer_shadow=False, draw_state=False, extended=False):
    code = with_graphics_recording(code, framebuffer_shadow=framebuffer_shadow, draw_state=draw_state, extended=extended)
    anchor = """    glBindBuffer(GL_ARRAY_BUFFER, modernGl->vbo);
    int32_t verticesPerItem = modernGl->batchType == BATCHTYPE_QUAD ? VERTICES_PER_QUAD : VERTICES_PER_TRIANGLE;
    size_t vertexBytes = (size_t)modernGl->batchCount * verticesPerItem * sizeof(GlVertex);
#ifdef PLATFORM_VITA"""
    return replace_once(code, anchor, """    int32_t verticesPerItem = modernGl->batchType == BATCHTYPE_QUAD ? VERTICES_PER_QUAD : VERTICES_PER_TRIANGLE;
    size_t vertexBytes = (size_t)modernGl->batchCount * verticesPerItem * sizeof(GlVertex);
#ifdef PLATFORM_VITA
    if (hasVAO() && hldRecordVitaGeometry(gl->vertexData, vertexBytes, modernGl->vbo, modernGl->vao,
                                        modernGl->batchType == BATCHTYPE_QUAD ? indexCount : modernGl->batchCount * VERTICES_PER_TRIANGLE,
                                        modernGl->batchType == BATCHTYPE_QUAD)) {
        if (restoreProgram != 0) glUseProgram(restoreProgram);
        modernGl->batchCount = 0;
        if (gl->base.currentShader == -1 && modernGl->batchLocalUV) glUniform1i(modernGl->uLocalUV, 0);
        return;
    }
    glBindBuffer(GL_ARRAY_BUFFER, modernGl->vbo);""").replace(
        "#else\n    glBufferData(GL_ARRAY_BUFFER, vertexBytes, gl->vertexData, GL_STREAM_DRAW);",
        "#else\n    glBindBuffer(GL_ARRAY_BUFFER, modernGl->vbo);\n"
        "    glBufferData(GL_ARRAY_BUFFER, vertexBytes, gl->vertexData, GL_STREAM_DRAW);", 1)


def with_graphics_command_loop(code, *, render_tail=False):
    anchor = "                const uint64_t preStart = nowNanos();"
    code = replace_once(code, anchor, """                extern void hldBeginVitaGraphics(void);
                hldBeginVitaGraphics();
""" + anchor)
    anchor = "            uint64_t swapStart = nowNanos();"
    ending = """            extern void hldEndVitaGraphics(void);
            extern void hldReportVitaGraphics(int frame);
            hldEndVitaGraphics();
            hldReportVitaGraphics(runner->frameCount);
"""
    if render_tail:
        ending = ending.replace("            hldEndVitaGraphics();", """            extern bool hldRenderTailEnabled;
            if (!hldRenderTailEnabled || runner->pendingRoom != -1 || !shouldRender)
                hldEndVitaGraphics();""")
    return replace_once(code, anchor, ending + anchor)
