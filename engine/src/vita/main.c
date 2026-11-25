#include <loop.h>
#include <vitaGL.h>
#include <psp2/ctrl.h>
#include <psp2/power.h>
#include <psp2/kernel/sysmem.h>
#include <psp2/kernel/threadmgr.h>
#include <psp2/io/fcntl.h>

#include "log.h"
#include "boot_progress.h"

void vitaOpenLog(void);
void vitaCloseLog(void);

static SceUID mainThread;

static int sampleBootThread(SceSize size, void* arg) {
    (void)size;
    (void)arg;
    SceUID fd = sceIoOpen("ux0:data/hld-vm/boot-thread.log",
        SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0666);
    if (fd < 0) return 0;
    for (unsigned i = 0; i < 180; ++i) {
        sceKernelDelayThread(2000000);
        SceKernelThreadInfo info = {.size = sizeof(info)};
        int result = sceKernelGetThreadInfo(mainThread, &info);
        char line[192];
        int length = snprintf(line, sizeof(line),
            "%u status=%x wait=%x waitId=%x cpuTime=%llu stackFree=%d result=%d\n",
            i + 1, info.status, info.waitType, (unsigned)info.waitId,
            (unsigned long long)info.runClocks, sceKernelGetThreadStackFreeSize(mainThread), result);
        if (length > 0 && (size_t)length < sizeof(line)) sceIoWrite(fd, line, (SceSize)length);
    }
    sceIoClose(fd);
    return 0;
}

int main(int argc, char* argv[]) {
    (void)argc;
    setbuf(stderr, NULL);
    setbuf(stdout, NULL);

    vitaOpenLog();
#if defined(ENABLE_VITA_ASYNC_PRESENT) && defined(ENABLE_SPRITE_DECODE_CACHE)
    logInfo("HLD 2016 00.72 starting VitaGL\n");
#elif defined(ENABLE_VITA_ASYNC_PRESENT)
    logInfo("HLD 2016 00.68 starting VitaGL\n");
#else
    logInfo("HLD 2016 00.49 starting VitaGL\n");
#endif
    mainThread = sceKernelGetThreadId();
    SceUID sampler = sceKernelCreateThread("HldBootSample", sampleBootThread, 96, 0x6000, 0, 0, NULL);
    int samplerStart = sampler < 0 ? sampler : sceKernelStartThread(sampler, 0, NULL);
    logInfo("Vita boot sampler create=%d start=%d\n", sampler, samplerStart);
    scePowerSetArmClockFrequency(444);
    scePowerSetBusClockFrequency(222);
    scePowerSetGpuClockFrequency(222);
    vglSetSemanticBindingMode(VGL_MODE_SHADER_PAIR);
    vglSetupRuntimeShaderCompiler(SHARK_OPT_SAFE, 0, 0, 0);
    vglSetupGarbageCollector(127, 0x20000);
    SceUID doubleBufferFlag = sceIoOpen("ux0:data/hld-vm/double-buffer", SCE_O_RDONLY, 0);
    bool tripleBuffer = doubleBufferFlag < 0;
    if (!tripleBuffer) sceIoClose(doubleBufferFlag);
    vglUseTripleBuffering(tripleBuffer ? GL_TRUE : GL_FALSE);
    logInfo("Vita display buffers: %d\n", tripleBuffer ? 3 : 2);
    vglSetCircularPoolSize(8 * 1024 * 1024);
    vglSetupRenderTargetScenesNum(4, 4);
    vglSetParamBufferSize(6 * 1024 * 1024);
    vglSetShaderCachePath("ux0:data/hld-vm/shader_cache");
    // VitaGL needs a mapped vertex pool for the fixed-function renderer.
    vglInitWithCustomThreshold(2 * 1024 * 1024, 960, 544,
        32 * 1024 * 1024, 0, 0, 0, SCE_GXM_MULTISAMPLE_NONE);

    SceUID vblankFlag = sceIoOpen("ux0:data/hld-vm/no-vblank", SCE_O_RDONLY, 0);
    if (vblankFlag >= 0) {
        sceIoClose(vblankFlag);
        // VitaGL still queues the scanout for the next refresh. This only
        // removes the extra wait after the presentation callback queues it.
        vglWaitVblankStart(GL_FALSE);
        logInfo("Vita display callback VBlank wait disabled for this run\n");
    }

    // Give the system a visible frame while the archive and game shaders load.
    glClearColor(0.065f, 0.085f, 0.11f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    vglSwapBuffers(GL_FALSE);
    VitaBootProgress_begin();
    glClearColor(0.0f, 0.0f, 0.0f, 1.0f);
    logInfo("Vita startup frame presented\n");

    SceKernelFreeMemorySizeInfo memory = {.size = sizeof(memory)};
    if (sceKernelGetFreeMemorySize(&memory) >= 0) {
        logInfo("VitaGL initialized, free KiB user=%d cdram=%d contiguous=%d\n",
            memory.size_user / 1024, memory.size_cdram / 1024, memory.size_phycont / 1024);
    }
    logInfo("Vita clocks ARM=%d GPU=%d MHz\n", scePowerGetArmClockFrequency(), scePowerGetGpuClockFrequency());
    sceCtrlSetSamplingMode(SCE_CTRL_MODE_ANALOG);

    CommandLineArgs args = {0};

    args.exitAtFrame = -1;
#ifdef ENABLE_VM_TRACING
    args.traceBytecodeAfterFrame = 0;
#endif
    args.speedMultiplier = 1.0;
    args.fastForwardSpeed = 0.0;
    args.osType = OS_WINDOWS;
    args.profilerFramesBetween = 0;
    args.loadType = DATAWINLOADTYPE_LOAD_PER_CHUNK;
    args.lazyRooms = true;
    args.lazyTextures = true;
    args.lazyAudio = true;
#if defined(ENABLE_MODERN_GL)
    args.renderer = MODERN_GL;
#ifdef ENABLE_LEGACY_GL
    FILE* legacyMode = fopen("ux0:data/hld-vm/use-legacy", "rb");
    if (legacyMode != NULL) {
        fclose(legacyMode);
        args.renderer = LEGACY_GL;
        logInfo("Using the fixed-function renderer for this run\n");
    }
#endif
#elif defined(ENABLE_LEGACY_GL)
    args.renderer = LEGACY_GL;
#else
    args.renderer = SOFTWARE;
#endif
    args.dataWinPath = "ux0:data/hld-vm/data.win";
    args.saveFolder = "ux0:data/hld-vm";

    int ret = loop(args, argv[0]);
    freeCommandLineArgs(&args);
    vitaCloseLog();
    return ret;
}
