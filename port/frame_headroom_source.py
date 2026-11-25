"""Run the isolated 30/60 FPS pacing comparison at fixed simulation delta."""

from runtime_verification_source import replace_once


def with_frame_headroom(code):
    anchor = '#include "stb_ds.h"'
    code = replace_once(code, anchor, anchor + "\n\nextern bool hldHigherPacingLimit;\n")
    anchor = "            runner->deltaTime = (int64_t)(frameStartNow - lastFrameStartTime) / 1000.0;"
    code = replace_once(code, anchor, anchor + r'''
            const double simulationRate = Runner_getEffectiveGameSpeed(runner);
            if (simulationRate >= 1.0 && simulationRate <= 1000.0)
                runner->deltaTime = (int64_t)(1000000.0 / simulationRate);''')
    anchor = "            const bool useFrameDeadline = runner->frameCount > 180;"
    code = replace_once(code, anchor, r'''            const bool higherLimit = hldHigherPacingLimit && runner->frameCount > 450 && runner->frameCount <= 1080;
''' + anchor)
    anchor = "                uint64_t targetFrameTime = 1000000000 / (effectiveGameSpeed * effectiveSpeed);"
    code = replace_once(code, anchor, r'''                const double pacingRate = higherLimit ? 60.0 : effectiveGameSpeed * effectiveSpeed;
                uint64_t targetFrameTime = 1000000000 / pacingRate;''')
    anchor = "            if (++sampledFrames == 90) {"
    return replace_once(code, anchor, anchor + r'''
                logInfo("Vita headroom frame=%d pacing_fps=%.1f simulation_fps=%.1f delta_us=%d\n",
                        runner->frameCount, higherLimit ? 60.0 : effectiveGameSpeed,
                        effectiveGameSpeed, (int)runner->deltaTime);''')
