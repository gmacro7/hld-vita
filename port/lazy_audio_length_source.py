"""Avoid scanning an automatic Vorbis loop to its end during sound startup."""

from runtime_verification_source import replace_once


def with_lazy_audio_length(code):
    anchor = "static float maGetStreamLength(AlAudioSystem* ma, int32_t streamIndex);"
    code = replace_once(code, anchor, anchor + r'''

extern bool hldDeferLoopLength;
#define HLD_VORBIS_LENGTH_UNREAD UINT64_MAX

static void ensureVorbisLength(SoundInstance* inst) {
    if (inst->streamLengthSamples != HLD_VORBIS_LENGTH_UNREAD) return;
    const uint64_t start = nowNanos();
    inst->streamLengthSamples = stb_vorbis_stream_length_in_samples((stb_vorbis*)inst->vorbis);
    inst->streamLengthSeconds = (float)inst->streamLengthSamples / (float)inst->streamSampleRate;
    logInfo("Vita deferred loop duration sound=%d samples=%llu ms=%.2f\n",
            inst->soundIndex, (unsigned long long)inst->streamLengthSamples, (nowNanos() - start) * 1e-6);
}''')
    anchor = "        free(assetStreamPath);\n\n        slot->streaming = true;"
    code = replace_once(code, anchor,
                        "        const bool deferLength = hldDeferLoopLength && assetStreamPath != nullptr;\n" + anchor)
    code = replace_once(code, "            slot->streamLengthSamples = stb_vorbis_stream_length_in_samples(v);",
                        """            slot->streamLengthSamples = deferLength
                ? HLD_VORBIS_LENGTH_UNREAD : stb_vorbis_stream_length_in_samples(v);""")
    code = replace_once(code, "        slot->streamLengthSeconds = slot->streamSampleRate > 0",
                        """        slot->streamLengthSeconds = slot->streamSampleRate > 0 &&
            slot->streamLengthSamples != HLD_VORBIS_LENGTH_UNREAD""")
    anchor = "    if (inst->loop) {\n        if (inst->streamLengthSamples > 0)"
    code = replace_once(code, anchor,
                        "    if (inst->loop) {\n        ensureVorbisLength(inst);\n        if (inst->streamLengthSamples > 0)")
    anchor = "    double requestedFrame = (double) positionSeconds * inst->streamSampleRate;"
    code = replace_once(code, anchor, "    ensureVorbisLength(inst);\n" + anchor)
    return replace_once(code, "        if (match->streaming) return match->streamLengthSeconds;",
                        """        if (match->streaming) {
            ensureVorbisLength(match);
            return match->streamLengthSeconds;
        }""")
