"""Profiling builds only (HLDC_AUDIO_PROFILE=1): break down slow audio updates.

The boss fight shows 90-140 ms frames where the main thread waits for the audio worker (audio_join_ms). Any maUpdate
that takes over 20 ms logs one line: total, group-gain update, the per-voice loop (AL queries and stream refills),
stream refills (count, total, longest), and the batched stream start. Every 90 frames-worth of updates a summary line
gives the average. Normal builds are unchanged.
"""

from runtime_verification_source import replace_once

FILL_SIGNATURE = "static bool streamFillBuffer(SoundInstance* inst, ALuint buf) {"


def with_audio_update_profile(code):
    start = code.index(FILL_SIGNATURE)
    end = code.index("\n}\n", start) + 3
    inner = code[start:end].replace(FILL_SIGNATURE, "static bool hldStreamFillInner(SoundInstance* inst, ALuint buf) {", 1)
    wrapper = '''
uint64_t nowNanos(void);
static uint64_t hldAudioFillNanos, hldAudioFillMax;
static unsigned hldAudioFills;
static bool streamFillBuffer(SoundInstance* inst, ALuint buf) {
    const uint64_t start = nowNanos();
    const bool filled = hldStreamFillInner(inst, buf);
    const uint64_t spent = nowNanos() - start;
    hldAudioFillNanos += spent;
    if (spent > hldAudioFillMax) hldAudioFillMax = spent;
    ++hldAudioFills;
    return filled;
}
'''
    code = code[:start] + inner + wrapper + code[end:]

    code = replace_once(code, '''static void maUpdate(AudioSystem* audio, float deltaTime) {
    AlAudioSystem* ma = (AlAudioSystem*) audio;
    bool groupChanged = AudioSystem_updateGroupGains(audio, deltaTime);
''', '''static void maUpdate(AudioSystem* audio, float deltaTime) {
    AlAudioSystem* ma = (AlAudioSystem*) audio;
    static uint64_t sumTotal, sumLoop, sumFill;
    static unsigned updates, slow, streamsSeen;
    const uint64_t hldT0 = nowNanos();
    hldAudioFillNanos = hldAudioFillMax = 0;
    hldAudioFills = 0;
    unsigned hldActive = 0, hldStreams = 0;
    bool groupChanged = AudioSystem_updateGroupGains(audio, deltaTime);
    const uint64_t hldT1 = nowNanos();
''')
    code = replace_once(code, '''        SoundInstance* inst = &ma->instances[i];
        if (!inst->active) continue;

        // Handle gain fading''', '''        SoundInstance* inst = &ma->instances[i];
        if (!inst->active) continue;
        ++hldActive;
        hldStreams += inst->streaming;

        // Handle gain fading''')
    code = replace_once(code, '''    // finish loading and refilling before starting this frame's streams together
    ALuint sources[MAX_SOUND_INSTANCES];''', '''    const uint64_t hldT2 = nowNanos();
    // finish loading and refilling before starting this frame's streams together
    ALuint sources[MAX_SOUND_INSTANCES];''')
    code = replace_once(code, '''            logWarn("Audio: alSourcePlayv failed for stream starts\\n");
        }
    }
}''', '''            logWarn("Audio: alSourcePlayv failed for stream starts\\n");
        }
    }
    const uint64_t hldT3 = nowNanos();
    const uint64_t total = hldT3 - hldT0;
    sumTotal += total; sumLoop += hldT2 - hldT1; sumFill += hldAudioFillNanos; streamsSeen += hldStreams;
    ++updates;
    if (total > 20000000u) {
        ++slow;
        logInfo("HLD audio slow total_ms=%.2f group_ms=%.2f loop_ms=%.2f fill_ms=%.2f fills=%u fill_max_ms=%.2f "
                "start_ms=%.2f starts=%d active=%u streams=%u\\n", total * 1e-6, (hldT1 - hldT0) * 1e-6,
                (hldT2 - hldT1) * 1e-6, hldAudioFillNanos * 1e-6, hldAudioFills, hldAudioFillMax * 1e-6,
                (hldT3 - hldT2) * 1e-6, (int) sourceCount, hldActive, hldStreams);
    }
    if (updates == 90) {
        logInfo("HLD audio avg total_ms=%.2f loop_ms=%.2f fill_ms=%.2f streams=%.1f slow=%u\\n", sumTotal * 1e-6 / 90,
                sumLoop * 1e-6 / 90, sumFill * 1e-6 / 90, streamsSeen / 90.0, slow);
        sumTotal = sumLoop = sumFill = 0;
        updates = slow = streamsSeen = 0;
    }
}''')
    return code
