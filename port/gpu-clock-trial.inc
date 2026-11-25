static int gpuTrialBaseline, gpuTrialRequested;

static void hldRestoreGpuClock(int frame) {
    if (gpuTrialBaseline <= 0) return;
    const int baseline = gpuTrialBaseline;
    if (frame < 0 && scePowerGetGpuClockFrequency() == baseline) {
        gpuTrialBaseline = 0;
        return;
    }
    const int status = scePowerSetGpuClockFrequency(baseline);
    const int actual = scePowerGetGpuClockFrequency();
    logInfo("Vita GPU trial restored frame=%d target=%d actual=%d status=%d\n",
            frame, baseline, actual, status);
    if (status >= 0 && baseline == actual) gpuTrialBaseline = 0;
}

void hldGpuClockTrial(int frame) {
    if (frame < 360 || frame > 1080 || frame % 90 != 0) return;
    if (frame == 360 && gpuTrialRequested == 0) {
        gpuTrialRequested = -1;
        FILE* marker = fopen("ux0:data/hld-vm/gpu-clock-trial.txt", "r");
        if (marker == nullptr) return;
        int requested = 0;
        const bool found = fscanf(marker, "%d", &requested) == 1;
        fclose(marker);
        if (!found || (requested != 166 && requested != 222)) return;

        const int baseline = scePowerGetGpuClockFrequency();
        if (baseline <= 0) return;
        gpuTrialRequested = requested;
        gpuTrialBaseline = baseline;
        const int status = scePowerSetGpuClockFrequency(requested);
        logInfo("Vita GPU trial applied frame=%d target=%d actual=%d baseline=%d status=%d\n",
                frame, requested, scePowerGetGpuClockFrequency(), baseline, status);
    }
    if (gpuTrialBaseline <= 0) return;
    logInfo("Vita GPU trial clock frame=%d gpu=%d arm=%d bus=%d xbar=%d\n",
            frame, scePowerGetGpuClockFrequency(), scePowerGetArmClockFrequency(),
            scePowerGetBusClockFrequency(), scePowerGetGpuXbarClockFrequency());
    if (frame == 1080) hldRestoreGpuClock(frame);
}
