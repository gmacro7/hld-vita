"""Attribute the start of a positional loop to a game frame and audio stage."""

from runtime_verification_source import replace_once


def with_positional_play_profile(code):
    start = code.index("static RValue builtin_audio_play_sound_at(")
    end = code.index("\nstatic RValue builtin_audio_play_sound_on(", start)
    body = code[start:end]
    anchor = "    AudioSystem* audio = HLD_AUDIO_SYSTEM(ctx->runner);"
    body = replace_once(body, anchor, "    const uint64_t audioStart = nowNanos();\n" + anchor)
    anchor = "    int32_t instanceId = audio->vtable->playSound(audio, soundIndex, priority, loop);"
    body = replace_once(body, anchor, "    const uint64_t beforePlay = nowNanos();\n" + anchor
                        + "\n    const uint64_t afterPlay = nowNanos();")
    anchor = "    return RValue_makeReal((GMLReal)instanceId);"
    body = replace_once(body, anchor, r'''    const uint64_t afterSetup = nowNanos();
    if (loop && afterSetup - audioStart >= 1000000u) {
        const DataWin* data = ctx->runner->dataWin;
        const char* name = soundIndex >= 0 && (uint32_t)soundIndex < data->sond.count
            ? data->sond.sounds[soundIndex].name : nullptr;
        logInfo("Vita positional loop runner_frame=%d name=%s fence_ms=%.2f start_ms=%.2f spatial_ms=%.2f\n",
                ctx->runner->frameCount, name != nullptr ? name : "?",
                (beforePlay - audioStart) * 1e-6, (afterPlay - beforePlay) * 1e-6,
                (afterSetup - afterPlay) * 1e-6);
    }
''' + anchor)
    return code[:start] + body + code[end:]


def with_stream_start_profile(code):
    code = replace_once(code, '#include "data_win.h"', '#include "data_win.h"\n#include "gettime.h"')
    start = code.index("static int32_t maPlaySound(")
    end = code.index("\nstatic void maSetSoundSpatial(", start)
    body = code[start:end]

    def before(anchor, addition):
        nonlocal body
        body = replace_once(body, anchor, addition + "\n" + anchor)

    before("    char* assetStreamPath = nullptr;", """    uint64_t resolverStart = 0, decoderStart = 0, decoderEnd = 0;
    uint64_t bufferStart = 0, primeStart = 0, primeEnd = 0;""")
    before("        assetStreamPath = resolveExternalPath(ma, sound);", "        resolverStart = nowNanos();")
    before("        // Streaming path:", "        if (resolverStart != 0) decoderStart = nowNanos();")
    before("        free(assetStreamPath);\n\n        slot->streaming = true;",
           "        if (decoderStart != 0) decoderEnd = nowNanos();")
    before("        alGenSources(1, &slot->alSource);\n        alGenBuffers(AL_STREAM_BUFFER_COUNT, slot->streamBuffers);",
           "        if (decoderEnd != 0) bufferStart = nowNanos();")
    before("        int primed = 0;", "        if (bufferStart != 0) primeStart = nowNanos();")
    before("        if (primed == 0) {", "        if (primeStart != 0) primeEnd = nowNanos();")
    anchor = '            logInfo("Audio: streaming loop %s\\n", sound->name);'
    body = replace_once(body, anchor, r'''            const uint64_t afterPlay = nowNanos();
            if (primeEnd != 0) {
                logInfo("Audio: streaming loop %s resolve_ms=%.2f decoder_ms=%.2f metadata_ms=%.2f "
                        "buffers_ms=%.2f prime_ms=%.2f start_ms=%.2f\n", sound->name,
                        (decoderStart - resolverStart) * 1e-6, (decoderEnd - decoderStart) * 1e-6,
                        (bufferStart - decoderEnd) * 1e-6, (primeStart - bufferStart) * 1e-6,
                        (primeEnd - primeStart) * 1e-6, (afterPlay - primeEnd) * 1e-6);
            } else {
''' + anchor + "\n            }")
    return code[:start] + body + code[end:]
