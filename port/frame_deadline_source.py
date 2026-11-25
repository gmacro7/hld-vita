"""Compare accumulated frame deadlines with a delay measured from frame end."""


def with_frame_deadlines(code, compare=True):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    anchor = "        uint64_t lastFrameStartTime = lastFrameTime; // for delta_time"
    variables = "\n        uint64_t frameDeadline = 0, deadlinePeriod = 0;"
    if compare:
        variables += "\n        unsigned deadlineCorrections = 0, deadlineResets = 0;"
    replace(anchor, anchor + variables)
    anchor = "            double effectiveGameSpeed = Runner_getEffectiveGameSpeed(runner);"
    enabled = "runner->frameCount > 180"
    if compare:
        enabled += " && ((runner->frameCount - 1) / 180) % 2 != 0"
    replace(anchor, anchor + f"\n            const bool useFrameDeadline = {enabled};\n"
            "            if (!useFrameDeadline) deadlinePeriod = 0;")
    anchor = "                uint64_t nextFrameTime = lastFrameTime + targetFrameTime;"
    schedule = r'''
                if (useFrameDeadline) {
                    if (targetFrameTime != deadlinePeriod || lastFrameTime > frameDeadline + targetFrameTime) {
                        frameDeadline = lastFrameTime;
                        deadlinePeriod = targetFrameTime;
                    }
                    frameDeadline += targetFrameTime;
                    // Recover a little lost time per frame and discard large
                    // stalls. Each tick still follows its normal draw and swap.
                    const uint64_t earliest = nextFrameTime - targetFrameTime / 8;
                    nextFrameTime = frameDeadline > earliest ? frameDeadline : earliest;
                }'''
    if compare:
        schedule = schedule.replace("                        deadlinePeriod = targetFrameTime;",
                                    "                        deadlinePeriod = targetFrameTime;\n                        ++deadlineResets;")
        schedule = schedule.replace("                    nextFrameTime = frameDeadline",
                                    "                    if (frameDeadline < nextFrameTime) ++deadlineCorrections;\n"
                                    "                    nextFrameTime = frameDeadline")
    replace(anchor, anchor + schedule)
    if compare:
        anchor = "                const double perFrameMs = 1e-6 / sampledFrames;"
        replace(anchor, """                logInfo("Vita frame deadlines frame=%d enabled=%d corrected=%u resets=%u\\n",
                        runner->frameCount, (int)useFrameDeadline, deadlineCorrections, deadlineResets);
                deadlineCorrections = deadlineResets = 0;
""" + anchor)
    return code
