"""Sample Vita thread scheduling at existing frame phase boundaries."""

from runtime_verification_source import replace_once


PROFILE = r'''
#include <psp2/kernel/threadmgr.h>
#include "gettime.h"

typedef struct {
    uint64_t wallNanos, runClocks;
    uint32_t interrupts, preemptions, releases;
    bool valid, threadValid;
} CpuPhaseReading;

static bool shouldSampleCpuPhase(int frame) {
    return frame >= 450 && frame < 720 && frame % 15 == 6;
}

static CpuPhaseReading readCpuPhase(bool enabled) {
    CpuPhaseReading reading = {0};
    if (!enabled) return reading;
    static SceUID self = -1;
    if (self < 0) self = sceKernelGetThreadId();
    reading.valid = true;
    reading.wallNanos = nowNanos();
    SceKernelThreadInfo info = {.size = sizeof(info)};
    if (sceKernelGetThreadInfo(self, &info) >= 0) {
        reading.threadValid = true;
        reading.runClocks = info.runClocks;
        reading.interrupts = info.intrPreemptCount;
        reading.preemptions = info.threadPreemptCount;
        reading.releases = info.threadReleaseCount;
    }
    return reading;
}

static void reportCpuPhase(int frame, const char* phase, CpuPhaseReading start, CpuPhaseReading end) {
    if (!start.valid || !end.valid) return;
    const bool threadValid = start.threadValid && end.threadValid && end.runClocks >= start.runClocks;
    const unsigned int runClocks = threadValid ? (unsigned int)(end.runClocks - start.runClocks) : 0;
    logInfo("Vita CPU phase frame=%d phase=%s wall_ms=%.3f run_clocks=%u thread_valid=%d "
            "interrupts=%u preemptions=%u releases=%u\n",
            frame, phase, (end.wallNanos - start.wallNanos) * 1e-6, runClocks, (int)threadValid,
            threadValid ? end.interrupts - start.interrupts : 0,
            threadValid ? end.preemptions - start.preemptions : 0,
            threadValid ? end.releases - start.releases : 0);
}

'''


def with_cpu_phase_profile(source):
    anchor = '#include "stb_ds.h"'
    source = replace_once(source, anchor, anchor + "\n" + PROFILE)
    anchor = "            uint64_t stepStart = nowNanos();"
    source = replace_once(source, anchor, r'''            const int cpuSampleFrame = runner->frameCount + 1;
            const bool sampleCPU = shouldStep && shouldSampleCpuPhase(cpuSampleFrame);
            const CpuPhaseReading stepCPUStart = readCpuPhase(sampleCPU);
''' + anchor)
    anchor = "            uint64_t stepEnd = nowNanos();"
    source = replace_once(source, anchor, anchor + r'''
            const CpuPhaseReading stepCPUEnd = readCpuPhase(sampleCPU);''')
    anchor = "            // Query actual framebuffer size"
    source = replace_once(source, anchor, r'''            const CpuPhaseReading drawCPUStart = readCpuPhase(sampleCPU && shouldRender);
''' + anchor)
    anchor = "            extern uint64_t hldCollectAudioUpdate(void);"
    source = replace_once(source, anchor, r'''            const CpuPhaseReading drawCPUEnd = readCpuPhase(sampleCPU && shouldRender);
            if (sampleCPU) {
                reportCpuPhase(cpuSampleFrame, "step", stepCPUStart, stepCPUEnd);
                reportCpuPhase(cpuSampleFrame, "render", drawCPUStart, drawCPUEnd);
            }
''' + anchor)
    return source
