#include <psp2/kernel/cpu.h>

static SceUID affinityMain = -1;
static int originalMainMask;
static int originalAudioMask;
static bool separateAudioCore;

static void switchAudioAffinity(bool separate) {
    if (affinityMain < 0) {
        affinityMain = sceKernelGetThreadId();
        originalMainMask = sceKernelGetThreadCpuAffinityMask(affinityMain);
        originalAudioMask = sceKernelGetThreadCpuAffinityMask(audioUpdateWorker);
        logInfo("Vita default affinity main=%08x audio=%08x\n", originalMainMask, originalAudioMask);
    }
    if (originalMainMask < 0 || originalAudioMask < 0 || separate == separateAudioCore) return;

    int mainMask = separate ? SCE_KERNEL_CPU_MASK_USER_0 : originalMainMask;
    int audioMask = separate ? SCE_KERNEL_CPU_MASK_USER_1 : originalAudioMask;
    int mainResult = sceKernelChangeThreadCpuAffinityMask(affinityMain, mainMask);
    int audioResult = sceKernelChangeThreadCpuAffinityMask(audioUpdateWorker, audioMask);
    if (mainResult >= 0 && audioResult >= 0) {
        separateAudioCore = separate;
        logInfo("Vita separate audio core=%d main=%08x audio=%08x\n", separate, mainMask, audioMask);
        return;
    }
    logWarn("Vita audio affinity failed main=%08x audio=%08x\n", mainResult, audioResult);
    sceKernelChangeThreadCpuAffinityMask(affinityMain, originalMainMask);
    sceKernelChangeThreadCpuAffinityMask(audioUpdateWorker, originalAudioMask);
    separateAudioCore = false;
}
