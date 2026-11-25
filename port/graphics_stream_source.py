"""Let the VitaGL worker consume multiple ordered packets per handoff."""

from pathlib import Path

from runtime_verification_source import replace_once


def with_graphics_stream(code):
    code = replace_once(code, "static VitaGraphicsChunk graphicsChunks[2];",
                        "enum { VITA_GRAPHICS_CHUNKS = 4 };\n"
                        "static VitaGraphicsChunk graphicsChunks[VITA_GRAPHICS_CHUNKS];")
    start = code.index("static void submitGraphicsChunk(void) {")
    end = code.index("static VitaGraphicsCommand* reserveGraphicsCommand(", start)
    stream = Path(__file__).with_name("vita-graphics-stream.inc").read_text()
    code = code[:start] + stream + "\n\n" + code[end:]
    anchor = "    graphicsFenceNanos = 0;"
    return replace_once(code, anchor, anchor + r'''
    logInfo("Vita graphics stream frame=%d streams=%u blocked=%u full_ms=%.3f\n",
            frame, graphicsStreams, graphicsBlockedPublishes, graphicsPublishWaitNanos / 90e6);
    graphicsStreams = graphicsBlockedPublishes = 0;
    graphicsPublishWaitNanos = 0;''')


def with_graphics_stream_lifecycle(code):
    anchor = "static unsigned batchSubmissions;"
    code = replace_once(code, anchor, anchor + """
static bool prepareGraphicsStream(void);
static void finishGraphicsStream(void);
static void releaseGraphicsStream(void);""")
    anchor = "    if (!hldSwapPending || sceKernelGetThreadId() != swapMain) return;"
    code = replace_once(code, anchor, anchor + "\n    finishGraphicsStream();")
    anchor = "    swapStart = swapDone = -1;"
    code = replace_once(code, anchor, anchor + "\n    releaseGraphicsStream();")
    anchor = "    if (batchFlag != NULL) fclose(batchFlag);"
    return replace_once(code, anchor, anchor + """
    if (batchWorkerEnabled) batchWorkerEnabled = prepareGraphicsStream();""")
