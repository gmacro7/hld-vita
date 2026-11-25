"""Batch the periodic timing lines without changing their order."""

from runtime_verification_source import replace_once


def with_frame_log_batch(code):
    anchor = "static bool batchingRoomLog;"
    code = replace_once(code, anchor, anchor + r'''
static bool bufferFrameReports;
static bool batchingFrameLog;

void vitaBeginFrameLog(bool reportFrame) {
    if (bufferFrameReports && reportFrame)
        __atomic_store_n(&batchingFrameLog, true, __ATOMIC_RELEASE);
}

void vitaEndFrameLog(void) {
    if (bufferFrameReports && __atomic_exchange_n(&batchingFrameLog, false, __ATOMIC_ACQ_REL)
        && vitaLog != NULL) fflush(vitaLog);
}
''')
    code = replace_once(code, "    if (vitaLog != NULL) fflush(vitaLog);",
                        "    if (vitaLog != NULL && !__atomic_load_n(&batchingFrameLog, __ATOMIC_ACQUIRE))"
                        " fflush(vitaLog);")
    anchor = '    vitaLog = fopen("ux0:data/hld-vm/runtime.log", "w");'
    code = replace_once(code, anchor, r'''    FILE* flag = fopen("ux0:data/hld-vm/buffered-frame-reports", "r");
    bufferFrameReports = flag != NULL;
    if (flag != NULL) fclose(flag);
''' + anchor)
    code = replace_once(code, "if (vitaLog != NULL && bufferLogDuringRoomChange &&",
                        "if (vitaLog != NULL && (bufferLogDuringRoomChange || bufferFrameReports) &&")
    code = replace_once(code, """        bufferLogDuringRoomChange = false;
    }""", r'''        bufferLogDuringRoomChange = bufferFrameReports = false;
    }
    logInfo("Vita frame reports buffered=%d\n", (int)bufferFrameReports);''')
    return replace_once(code, "if (type == LOG_TYPE_ERROR || !__atomic_load_n(&batchingRoomLog, __ATOMIC_ACQUIRE))",
                        """if (type == LOG_TYPE_ERROR ||
        (!__atomic_load_n(&batchingRoomLog, __ATOMIC_ACQUIRE) &&
         !__atomic_load_n(&batchingFrameLog, __ATOMIC_ACQUIRE)))""")


def with_frame_log_loop(code):
    anchor = "            uint64_t frameStartNow = nowNanos();"
    code = replace_once(code, anchor, anchor + """
            extern void vitaBeginFrameLog(bool reportFrame);
            extern void vitaEndFrameLog(void);
            const int reportPhase = runner->frameCount % 90;
            // Input reports the preceding frame, while simulation and draw
            // can advance the frame number. Keep both boundaries buffered.
            vitaBeginFrameLog(reportPhase == 0 || reportPhase == 89 || sampledFrames == 89);""")
    anchor = """            if (platformHandleEvents()) {
                shouldWindowClose = true;
                continue;"""
    code = replace_once(code, anchor, anchor.replace("                continue;",
                                                    "                vitaEndFrameLog();\n                continue;"))
    anchor = "            lastFrameTime = frameEnd;"
    return replace_once(code, anchor, "            vitaEndFrameLog();\n" + anchor)
