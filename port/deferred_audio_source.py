"""Let the audio worker finish during rendering, with fences on audio access."""
import re


def replace(code, before, after):
    assert code.count(before) == 1, before
    return code.replace(before, after)


def with_deferred_audio_worker(code):
    code = replace(code, "static unsigned audioJobCount;", "static unsigned audioJobCount;\n"
                   "static bool audioUpdatePending;\nstatic uint64_t completedAudioNanos;")
    code = replace(code, "    return sceKernelSignalSema(audioUpdateStart, 1) >= 0;",
                   "    audioUpdatePending = sceKernelSignalSema(audioUpdateStart, 1) >= 0;\n"
                   "    return audioUpdatePending;")
    code = replace(code, "uint64_t hldFinishAudioUpdate(void) {", "static uint64_t hldFinishAudioUpdate(void) {\n"
                   "    if (!audioUpdatePending) return 0;\n    audioUpdatePending = false;")
    code = replace(code, "void hldReportAudioUpdate(int frame) {", """void hldAudioFence(void) {
    completedAudioNanos += hldFinishAudioUpdate();
}

uint64_t hldCollectAudioUpdate(void) {
    hldAudioFence();
    uint64_t completed = completedAudioNanos;
    completedAudioNanos = 0;
    return completed;
}

void hldReportAudioUpdate(int frame) {""")
    return replace(code, "static void closeAudioUpdateWorker(void) {",
                   "static void closeAudioUpdateWorker(void) {\n    hldAudioFence();")


def with_deferred_audio_loop(code):
    original = """                uint64_t audioStart = nowNanos();
                runner->audioSystem->vtable->update(runner->audioSystem, dt);
                audioTotal += nowNanos() - audioStart;"""
    code = replace(code, original, """                extern bool hldQueueAudioUpdate(Runner* runner, float dt);
                if (!shouldRender || !hldQueueAudioUpdate(runner, dt)) {
                    uint64_t audioStart = nowNanos();
                    runner->audioSystem->vtable->update(runner->audioSystem, dt);
                    audioTotal += nowNanos() - audioStart;
                }""")
    anchor = "            if (args.exitAtFrame >= 0 && runner->frameCount >= args.exitAtFrame) {"
    return replace(code, anchor, """            extern uint64_t hldCollectAudioUpdate(void);
            extern void hldReportAudioUpdate(int frame);
            audioTotal += hldCollectAudioUpdate();
            hldReportAudioUpdate(runner->frameCount);
""" + anchor)


def with_deferred_audio_builtins(code):
    anchor = '#include "gettime.h"'
    code = replace(code, anchor, anchor + "\n\nextern void hldAudioFence(void);\n"
                   "#define HLD_AUDIO_SYSTEM(runner) (hldAudioFence(), (runner)->audioSystem)\n")
    code, count = re.subn(r"(?<!->)\b(ctx->runner|runner)->audioSystem\b", r"HLD_AUDIO_SYSTEM(\1)", code)
    assert count >= 60, count
    return code


def with_deferred_audio_runner(code):
    anchor = "void Runner_setPaused(Runner* runner, bool paused) {"
    return replace(code, anchor, anchor + "\n    extern void hldAudioFence(void);\n    hldAudioFence();")
