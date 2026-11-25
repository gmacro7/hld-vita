"""Open short positional Vorbis loops from a bounded in-memory buffer."""

from runtime_verification_source import replace_once


MEMORY_STREAMS = r'''
enum { HLD_AMBIENT_BYTES_LIMIT = 64 * 1024, HLD_AMBIENT_BUFFER_COUNT = 4 };

typedef struct {
    stb_vorbis* decoder;
    unsigned char* compressed;
} AmbientBuffer;

// Audio playback and worker updates already share the audio fence.
static AmbientBuffer ambientBuffers[HLD_AMBIENT_BUFFER_COUNT];

static AmbientBuffer* ambientBufferFor(stb_vorbis* decoder) {
    for (unsigned i = 0; i < HLD_AMBIENT_BUFFER_COUNT; ++i)
        if (ambientBuffers[i].decoder == decoder) return &ambientBuffers[i];
    return nullptr;
}

static stb_vorbis* openMemoryAmbient(const char* path, unsigned* residentBytes) {
    AmbientBuffer* slot = ambientBufferFor(nullptr);
    if (slot == nullptr) return nullptr;
    const size_t pathLength = strlen(path);
    if (pathLength < 4 || strcmp(path + pathLength - 4, ".ogg") != 0) return nullptr;

    FILE* file = fopen(path, "rb");
    if (file == nullptr) return nullptr;
    long length = fseek(file, 0, SEEK_END) == 0 ? ftell(file) : -1;
    if (length <= 0 || length > HLD_AMBIENT_BYTES_LIMIT || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return nullptr;
    }

    const size_t bytes = (size_t)length;
    unsigned char* compressed = (unsigned char*)malloc(bytes);
    const size_t read = compressed != nullptr ? fread(compressed, 1, bytes, file) : 0;
    fclose(file);
    stb_vorbis* decoder = read == bytes ? stb_vorbis_open_memory(compressed, (int)bytes, nullptr, nullptr) : nullptr;
    if (decoder == nullptr) {
        free(compressed);
        return nullptr;
    }

    *slot = (AmbientBuffer){decoder, compressed};
    *residentBytes = (unsigned)bytes;
    return decoder;
}

static void closeAmbientVorbis(stb_vorbis* decoder) {
    AmbientBuffer* slot = ambientBufferFor(decoder);
    stb_vorbis_close(decoder);
    if (slot != nullptr) {
        free(slot->compressed);
        *slot = (AmbientBuffer){0};
    }
}
'''


def with_ambient_memory(code):
    anchor = "static void releaseInstance(SoundInstance* inst) {"
    code = replace_once(code, anchor, "extern bool hldMemoryAmbient;\n" + MEMORY_STREAMS + "\n" + anchor)
    anchor = "            stb_vorbis_close((stb_vorbis*) inst->vorbis);"
    code = replace_once(code, anchor, "            closeAmbientVorbis((stb_vorbis*) inst->vorbis);")
    start = code.index("static int32_t maPlaySound(")
    end = code.index("\nstatic void maSetSoundSpatial(", start)
    body = code[start:end]
    anchor = "    char* assetStreamPath = nullptr;"
    body = replace_once(body, anchor, "    unsigned residentBytes = 0;\n" + anchor)
    anchor = '''        FILE* wavFile = openWavStream(streamPath, &wavChannels, &wavSampleRate, &wavDataStart, &wavDataBytes);

        stb_vorbis* v = nullptr;
        if (wavFile == nullptr) {'''
    body = replace_once(body, anchor, r'''        stb_vorbis* v = hldMemoryAmbient && assetStreamPath != nullptr
            ? openMemoryAmbient(streamPath, &residentBytes) : nullptr;
        FILE* wavFile = v == nullptr
            ? openWavStream(streamPath, &wavChannels, &wavSampleRate, &wavDataStart, &wavDataBytes) : nullptr;
        if (v == nullptr && wavFile == nullptr) {''')
    assert body.count("                stb_vorbis_close(v);") == 2
    body = body.replace("                stb_vorbis_close(v);", "                closeAmbientVorbis(v);")
    anchor = '"buffers_ms=%.2f prime_ms=%.2f start_ms=%.2f\\n", sound->name,'
    body = replace_once(body, anchor, '"buffers_ms=%.2f prime_ms=%.2f start_ms=%.2f resident_bytes=%u\\n", sound->name,')
    anchor = "(primeEnd - primeStart) * 1e-6, (afterPlay - primeEnd) * 1e-6);"
    body = replace_once(body, anchor, "(primeEnd - primeStart) * 1e-6, (afterPlay - primeEnd) * 1e-6, residentBytes);")
    return code[:start] + body + code[end:]
