"""Report the slowest physical frame in a timing window and its stages."""

from runtime_verification_source import replace_once


def with_present_hitch_profile(code):
    code = replace_once(code, "static uint64_t finalSceneNanos;", """static uint64_t finalSceneNanos;
static struct {
    uint64_t elapsed, finalScene, displayQueue;
} lastPresent;""")
    code = replace_once(code, """    measuringSwap = true;
    vglSwapBuffers(GL_FALSE);
    measuringSwap = false;""", """    const uint64_t sceneBefore = finalSceneNanos;
    const uint64_t queueBefore = displayQueueNanos;
    const uint64_t started = nowNanos();
    measuringSwap = true;
    vglSwapBuffers(GL_FALSE);
    measuringSwap = false;
    lastPresent.elapsed = nowNanos() - started;
    lastPresent.finalScene = finalSceneNanos - sceneBefore;
    lastPresent.displayQueue = displayQueueNanos - queueBefore;""")
    return replace_once(code, """static void reportFinishedSwap(void) {
    VitaBootProgress_gamePresented();""", r"""static void reportFinishedSwap(void) {
    if (lastPresent.elapsed >= 80000000u) {
        const double ms = 1e-6;
        logInfo("Vita present hitch frame=%d elapsed_ms=%.2f final_scene_ms=%.2f queue_ms=%.2f\n",
                swapFrame, lastPresent.elapsed * ms, lastPresent.finalScene * ms, lastPresent.displayQueue * ms);
    }
    VitaBootProgress_gamePresented();""")


def with_frame_hitch_profile(code):
    def insert_after(anchor, addition):
        nonlocal code
        code = replace_once(code, anchor, anchor + "\n" + addition)

    insert_after("        unsigned sampledFrames = 0, presentedFrames = 0;", """        struct {
            int frame;
            uint64_t interval, beforeInput, input, step, audio, audioJoin, reports,
                     hostBind, hostClear, setup, preDraw, frameBegin, view, gui, swap, tail, delay;
        } worstVitaFrame = {0};""")
    insert_after("            uint64_t frameStartNow = nowNanos();", """            __typeof__(worstVitaFrame) vitaFrame = {0};
            vitaFrame.beforeInput = frameStartNow - lastFrameTime;""")
    insert_after("            uint64_t stepEnd = nowNanos();", """            vitaFrame.input = stepStart - frameStartNow;
            vitaFrame.step = stepEnd - stepStart;""")
    anchor = "                if (!shouldRender || !hldQueueAudioUpdate(runner, dt)) {"
    code = replace_once(code, anchor, "                const uint64_t beforeAudio = nowNanos();\n" + anchor)
    insert_after("                    audioTotal += nowNanos() - audioStart;\n                }",
                 "                vitaFrame.audio = nowNanos() - beforeAudio;")
    code = replace_once(code, "            audioTotal += hldCollectAudioUpdate();",
                        """            const uint64_t beforeJoin = nowNanos();
            audioTotal += hldCollectAudioUpdate();
            const uint64_t afterJoin = nowNanos();
            vitaFrame.audioJoin = afterJoin - beforeJoin;""")
    anchor = """                    glBindFramebuffer(GL_FRAMEBUFFER, *hostFramebuffer);
                    glClear(GL_COLOR_BUFFER_BIT);"""
    code = replace_once(code, anchor, """                    const uint64_t beforeHostBind = nowNanos();
                    glBindFramebuffer(GL_FRAMEBUFFER, *hostFramebuffer);
                    const uint64_t beforeHostClear = nowNanos();
                    glClear(GL_COLOR_BUFFER_BIT);
                    vitaFrame.hostBind = beforeHostClear - beforeHostBind;
                    vitaFrame.hostClear = nowNanos() - beforeHostClear;""")
    insert_after("                preTotal += preEnd - preStart;", """                vitaFrame.setup = preStart - stepEnd;
                vitaFrame.preDraw = preEnd - preStart;""")
    insert_after("                beginTotal += drawViewStart - preEnd;",
                 "                vitaFrame.frameBegin = drawViewStart - preEnd;")
    insert_after("                viewTotal += drawGuiStart - drawViewStart;",
                 "                vitaFrame.view = drawGuiStart - drawViewStart;")
    code = replace_once(code, "                guiTotal += nowNanos() - drawGuiStart;",
                        """                vitaFrame.gui = nowNanos() - drawGuiStart;
                guiTotal += vitaFrame.gui;""")
    code = replace_once(code, "            swapTotal += nowNanos() - swapStart;",
                        """            const uint64_t afterSwap = nowNanos();
            vitaFrame.swap = afterSwap - swapStart;
            swapTotal += vitaFrame.swap;""")
    insert_after("            uint64_t swapStart = nowNanos();",
                 "            vitaFrame.reports = swapStart - afterJoin;")
    code = replace_once(code, "            workTotal += nowNanos() - frameStartNow;",
                        """            const uint64_t workEnd = nowNanos();
            vitaFrame.tail = workEnd - afterSwap;
            workTotal += workEnd - frameStartNow;""")
    insert_after("            uint64_t interval = frameEnd - lastFrameTime;", """            if (interval > worstVitaFrame.interval) {
                vitaFrame.frame = runner->frameCount;
                vitaFrame.interval = interval;
                vitaFrame.delay = frameEnd - workEnd;
                worstVitaFrame = vitaFrame;
            }""")
    return replace_once(code, "                inputTotal = audioTotal = setupTotal = preTotal = beginTotal = 0;", r'''                if (worstVitaFrame.interval >= 80000000u) {
                    const double ms = 1e-6;
                    logInfo("Vita hitch window=%d frame=%d interval_ms=%.2f before_input_ms=%.2f input_ms=%.2f "
                            "step_ms=%.2f setup_ms=%.2f audio_block_ms=%.2f audio_join_ms=%.2f reports_ms=%.2f "
                            "host_bind_ms=%.2f host_clear_ms=%.2f pre_draw_ms=%.2f frame_begin_ms=%.2f view_ms=%.2f gui_ms=%.2f "
                            "swap_ms=%.2f tail_ms=%.2f delay_ms=%.2f\n",
                            runner->frameCount, worstVitaFrame.frame, worstVitaFrame.interval * ms,
                            worstVitaFrame.beforeInput * ms, worstVitaFrame.input * ms, worstVitaFrame.step * ms,
                            worstVitaFrame.setup * ms, worstVitaFrame.audio * ms, worstVitaFrame.audioJoin * ms,
                            worstVitaFrame.reports * ms, worstVitaFrame.hostBind * ms, worstVitaFrame.hostClear * ms,
                            worstVitaFrame.preDraw * ms, worstVitaFrame.frameBegin * ms,
                            worstVitaFrame.view * ms, worstVitaFrame.gui * ms,
                            worstVitaFrame.swap * ms, worstVitaFrame.tail * ms, worstVitaFrame.delay * ms);
                }
                worstVitaFrame = (__typeof__(worstVitaFrame)){0};
                inputTotal = audioTotal = setupTotal = preTotal = beginTotal = 0;''')
