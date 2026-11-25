#include <AL/alc.h>

static SceUID audioUpdateWorker = -1;
static SceUID audioUpdateStart = -1;
static SceUID audioUpdateDone = -1;
static struct {
    AudioSystem* audio;
    ALCcontext* context;
    float dt;
} audioJob;
static uint64_t audioJobNanos;
static unsigned audioJobCount;
static uint64_t audioWaitNanos;
static bool audioJobSucceeded;
static bool audioWorkerContextUnavailable;

static int runAudioUpdateWorker(SceSize size, void* args) {
    (void)size;
    (void)args;
    while (sceKernelWaitSema(audioUpdateStart, 1, NULL) >= 0) {
        AudioSystem* audio = __atomic_load_n(&audioJob.audio, __ATOMIC_ACQUIRE);
        if (audio == nullptr) break;
        audioJobSucceeded = alcGetCurrentContext() == audioJob.context;
        if (audioJobSucceeded) {
            uint64_t start = nowNanos();
            audio->vtable->update(audio, audioJob.dt);
            audioJobNanos = nowNanos() - start;
        }
        sceKernelSignalSema(audioUpdateDone, 1);
    }
    return 0;
}

bool hldQueueAudioUpdate(Runner* runner, float dt) {
    const int frame = runner->frameCount;
    if (audioUpdateWorker < 0 || audioWorkerContextUnavailable || frame <= 180 || ((frame - 1) / 180) % 2 == 0) return false;
    audioJob.context = alcGetCurrentContext();
    if (audioJob.context == nullptr) return false;
    audioJob.dt = dt;
    __atomic_store_n(&audioJob.audio, runner->audioSystem, __ATOMIC_RELEASE);
    return sceKernelSignalSema(audioUpdateStart, 1) >= 0;
}

uint64_t hldFinishAudioUpdate(void) {
    const uint64_t beforeWait = nowNanos();
    int result = sceKernelWaitSema(audioUpdateDone, 1, NULL);
    if (result < 0) {
        logError("Vita audio update wait failed: %08x\n", (unsigned)result);
        abort();
    }
    audioWaitNanos += nowNanos() - beforeWait;
    if (!audioJobSucceeded) {
        AudioSystem* audio = audioJob.audio;
        audioWorkerContextUnavailable = true;
        logWarn("Vita audio update requires the main thread's OpenAL context\n");
        const uint64_t start = nowNanos();
        audio->vtable->update(audio, audioJob.dt);
        return nowNanos() - start;
    }
    ++audioJobCount;
    return audioJobNanos;
}

void hldReportAudioUpdate(int frame) {
    if (frame > 0 && frame % 90 == 0) {
        logInfo("Vita audio worker frame=%d updates=%u wait_ms=%.3f\n", frame, audioJobCount,
                audioWaitNanos / (90.0 * 1e6));
        audioJobCount = 0;
        audioWaitNanos = 0;
    }
}

static void closeAudioUpdateWorker(void) {
    if (audioUpdateWorker >= 0) {
        __atomic_store_n(&audioJob.audio, nullptr, __ATOMIC_RELEASE);
        sceKernelSignalSema(audioUpdateStart, 1);
        sceKernelWaitThreadEnd(audioUpdateWorker, NULL, NULL);
        sceKernelDeleteThread(audioUpdateWorker);
        audioUpdateWorker = -1;
    }
    if (audioUpdateStart >= 0) sceKernelDeleteSema(audioUpdateStart);
    if (audioUpdateDone >= 0) sceKernelDeleteSema(audioUpdateDone);
    audioUpdateStart = audioUpdateDone = -1;
}

static void openAudioUpdateWorker(void) {
    audioUpdateStart = sceKernelCreateSema("HldAudioUpdateStart", 0, 0, 1, NULL);
    audioUpdateDone = sceKernelCreateSema("HldAudioUpdateDone", 0, 0, 1, NULL);
    if (audioUpdateStart >= 0 && audioUpdateDone >= 0) {
        audioUpdateWorker = sceKernelCreateThread("HldAudioUpdateWorker", runAudioUpdateWorker, 96, 0x18000, 0, 0, NULL);
        if (audioUpdateWorker >= 0 && sceKernelStartThread(audioUpdateWorker, 0, NULL) >= 0) {
            logInfo("Vita audio worker active, alternating 180-frame windows\n");
            return;
        }
        if (audioUpdateWorker >= 0) sceKernelDeleteThread(audioUpdateWorker);
        audioUpdateWorker = -1;
    }
    logError("Vita audio update worker could not start\n");
    closeAudioUpdateWorker();
}
