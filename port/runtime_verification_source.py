"""Isolated Vita fixtures for measuring room changes and active distortion."""


def replace_once(code, before, after):
    assert code.count(before) == 1, before
    return code.replace(before, after)


def with_runtime_verification_loop(code):
    anchor = "                Runner_drawPost(runner, fbWidth, fbHeight);"
    code = replace_once(code, anchor, r'''                if (runner->frameCount == 300 &&
                    strcmp(runner->currentRoom->name, "rm_IN_01_brokenshallows") == 0) {
                    FILE* fixture = fopen("ux0:data/hld-vm/fixture-active-distortion", "r");
                    if (fixture != NULL) {
                        fclose(fixture);
                        Instance* screen = VM_findInstanceByTarget(vm, 32);
                        ptrdiff_t field = shgeti(vm->varNameMap, "distortion");
                        if (screen != NULL && field >= 0) {
                            Instance_setSelfVar(screen, vm->varNameMap[field].value, RValue_makeReal(0.6));
                            logInfo("Vita fixture: forcing the original distortion trigger on frame 300\n");
                        }
                    }
                }
''' + anchor)
    return with_room_log_buffer_loop(code, measure=True)


def with_room_log_buffer_loop(code, measure=False):
    anchor = "                Runner_handlePendingRoomChange(runner);"
    start = "                    uint64_t start = nowNanos();\n" if measure else ""
    report = r'''                    logInfo("Vita room change total frame=%d room=%s ms=%.2f\n",
                            runner->frameCount, runner->currentRoom->name, (nowNanos() - start) * 1e-6);
''' if measure else ""
    return replace_once(code, anchor, '''                if (runner->pendingRoom != -1) {
                    extern void vitaBeginRoomLog(void);
                    extern void vitaEndRoomLog(void);
''' + start + '''                    vitaBeginRoomLog();
                    Runner_handlePendingRoomChange(runner);
                    vitaEndRoomLog();
''' + report + "                }")


def with_room_phase_timings(code):
    anchor = "        Runner_executeEventForAll(runner, EVENT_OTHER, OTHER_ROOM_END);"
    code = replace_once(code, anchor, "        uint64_t roomEndStart = nowNanos();\n" + anchor +
                        "\n        uint64_t roomEndNanos = nowNanos() - roomEndStart;")
    anchor = "        initRoom(runner, newRoomIndex);"
    code = replace_once(code, anchor, "        uint64_t initStart = nowNanos();\n" + anchor +
                        "\n        uint64_t initNanos = nowNanos() - initStart;")
    anchor = """        Runner_executeEventForAll(runner, EVENT_OTHER, OTHER_ROOM_START);

        Runner_cleanupDestroyedInstances(runner);
        Runner_sweepDeadStructs(runner);"""
    return replace_once(code, anchor, r'''        uint64_t roomStart = nowNanos();
        Runner_executeEventForAll(runner, EVENT_OTHER, OTHER_ROOM_START);
        uint64_t roomStartNanos = nowNanos() - roomStart;
        uint64_t cleanupStart = nowNanos();
        Runner_cleanupDestroyedInstances(runner);
        Runner_sweepDeadStructs(runner);
        logInfo("Vita room change phases frame=%d end_ms=%.2f init_ms=%.2f start_ms=%.2f cleanup_ms=%.2f\n",
                runner->frameCount, roomEndNanos * 1e-6, initNanos * 1e-6, roomStartNanos * 1e-6,
                (nowNanos() - cleanupStart) * 1e-6);''')


def with_distortion_reference(code):
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    code = replace_once(code, anchor, "static bool useOriginalDistortion;\n\n" + anchor)
    code = replace_once(code, "    bool useHldDistortionLoop = code == ctx->hldDistortionCode;",
                        "    bool useHldDistortionLoop = code == ctx->hldDistortionCode && !useOriginalDistortion;")
    anchor = "                    ctx->hldDistortionCode = code;"
    return replace_once(code, anchor, anchor + r'''
                    FILE* reference = fopen("ux0:data/hld-vm/distortion-interpreter", "r");
                    useOriginalDistortion = reference != NULL;
                    if (reference != NULL) fclose(reference);
                    logInfo("Vita distortion reference original=%d\n", (int)useOriginalDistortion);''')


def with_distortion_draw_trace(code):
    anchor = "        GMLShader* shader = &modernGl->gmlShaders[gl->base.currentShader];"
    return replace_once(code, anchor, anchor + r'''
        if (gl->base.currentShader == 7 && gl->base.runner->frameCount == 300) {
            logInfo("Distortion draw frame=%d quads=%d\n", gl->base.runner->frameCount, modernGl->batchCount);
        }''')
