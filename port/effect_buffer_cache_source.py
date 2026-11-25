"""Reuse the OpenAL buffer of an embedded sound effect instead of decoding and uploading it on every play.

The 2016 runtime decodes the whole Vorbis effect (stb_vorbis_decode_memory) and uploads it with alBufferData each
time audio_play_sound_at starts it, about 2 ms per sound on the Vita, many times per second in a fight. A buffer's
PCM never changes, so the first decode is kept: later plays attach the same buffer to their new source. Cached
buffers are never deleted while the runtime lives; the total is capped, beyond which sounds behave as before.
Hooks sit on existing lines so the profile-guided build keeps its line layout; the helpers go at the end.
"""

from runtime_verification_source import replace_once

HELPERS = r'''
// ===[ Effect buffer cache ]===
enum { HLD_SFX_CACHE_LIMIT = 24 * 1024 * 1024 };
static ALuint* hldSfxBuffers;
static uint32_t hldSfxCount;
static size_t hldSfxBytes;
static unsigned hldSfxHits, hldSfxStores;

static uint64_t hldSfxMissStart;
static bool hldSfxWarmUpload(AlAudioSystem* ma, int32_t soundIndex, SoundInstance* slot);
static void hldSfxWarmStart(AlAudioSystem* ma);
static void hldSfxMarkPlayed(int32_t soundIndex);
static bool hldSfxReuse(AlAudioSystem* ma, int32_t soundIndex, SoundInstance* slot) {
    hldSfxMissStart = nowNanos();
    hldSfxWarmStart(ma);
    if (hldSfxBuffers == NULL) {
        const DataWin* dw = ma->base.audioGroups[0];
        if (dw == NULL) return false;
        hldSfxCount = dw->sond.count;
        hldSfxBuffers = (ALuint*) calloc(hldSfxCount, sizeof(ALuint));
    }
    if (hldSfxBuffers == NULL || soundIndex < 0 || (uint32_t) soundIndex >= hldSfxCount) return false;
    const ALuint cached = hldSfxBuffers[soundIndex];
    if (cached == 0) return hldSfxWarmUpload(ma, soundIndex, slot);
    (void) ma;
    alDeleteBuffers(1, &slot->alBuffer);
    slot->alBuffer = cached;
    alSourcei(slot->alSource, AL_BUFFER, cached);
    if (++hldSfxHits % 500 == 0) logInfo("Audio: effect buffer cache hits=%u stores=%u bytes=%u\n", hldSfxHits, hldSfxStores, (unsigned) hldSfxBytes);
    return true;
}

static void hldSfxRemember(AlAudioSystem* ma, int32_t soundIndex, ALuint buffer, uint32_t bytes) {
    const DataWin* dw = ma->base.audioGroups[0];
    if (soundIndex < 0 || dw == NULL || (uint32_t) soundIndex >= dw->sond.count) return;
    const double decodeMs = (nowNanos() - hldSfxMissStart) * 1e-6;
    if (decodeMs > 3.0) logInfo("Audio: first play decode %s ms=%.1f bytes=%u\n", dw->sond.sounds[soundIndex].name, decodeMs, bytes);
    if (hldSfxBytes + bytes > HLD_SFX_CACHE_LIMIT) return;
    if (hldSfxBuffers == NULL) {
        hldSfxCount = dw->sond.count;
        hldSfxBuffers = (ALuint*) calloc(hldSfxCount, sizeof(ALuint));
        if (hldSfxBuffers == NULL) return;
    }
    if ((uint32_t) soundIndex >= hldSfxCount || hldSfxBuffers[soundIndex] != 0) return;
    hldSfxBuffers[soundIndex] = buffer;
    hldSfxBytes += bytes;
    ++hldSfxStores;
    hldSfxMarkPlayed(soundIndex);
}

// ---- Warm-up: decode effects heard in earlier sessions on a background thread ----
// ux0:data/hld-vm/sfx-warm.txt lists sound names in the order they were first heard. At startup a low-priority
// worker reads each listed embedded Vorbis effect straight from data.win (its own file handle; offsets and sizes
// are immutable) and decodes it to PCM. The first play then only uploads that PCM. New first plays are appended
// to the list by the same worker, so the set grows with what the player actually hears.
enum { HLD_WARM_IDLE = 0, HLD_WARM_QUEUED = 1, HLD_WARM_READY = 2, HLD_WARM_DONE = 3, HLD_WARM_PCM_LIMIT = 16 * 1024 * 1024 };
typedef struct { int state; void* block; const uint8_t* pcm; uint32_t bytes; int channels, rate, bits; } HldWarm;
static HldWarm* hldWarm;
static uint8_t* hldSfxPlayed;   // heard this session or already listed
static int32_t* hldWarmNew;     // indices to append to the list
static volatile int hldWarmNewCount;
static int hldWarmNewCapacity;
static const DataWin* hldWarmData;
static SceUID hldWarmThread = -1;
static size_t hldWarmBytes;
static unsigned hldWarmUploads;

static void hldSfxMarkPlayed(int32_t soundIndex) {
    if (hldSfxPlayed == NULL || soundIndex < 0 || (uint32_t) soundIndex >= hldSfxCount || hldSfxPlayed[soundIndex]) return;
    hldSfxPlayed[soundIndex] = 1;
    if (hldWarmNewCount < hldWarmNewCapacity) {
        hldWarmNew[hldWarmNewCount] = soundIndex;
        __atomic_store_n(&hldWarmNewCount, hldWarmNewCount + 1, __ATOMIC_RELEASE);
    }
}

static int hldSfxWarmWorker(SceSize args, void* argp) {
    (void) args; (void) argp;
    const DataWin* dw = hldWarmData;
    FILE* list = fopen("ux0:data/hld-vm/sfx-warm.txt", "r");
    FILE* archive = fopen("ux0:data/hld-vm/data.win", "rb");
    char name[128];
    unsigned warmed = 0, noName = 0, played = 0, notEmbedded = 0, readFail = 0, notOgg = 0, decodeFail = 0;
    if (archive == NULL) logWarn("Audio: warm worker could not open data.win\n");
    while (list != NULL && archive != NULL && fgets(name, sizeof(name), list) != NULL) {
        name[strcspn(name, "\r\n")] = '\0';
        int32_t index = -1;
        for (uint32_t i = 0; i < dw->sond.count; ++i)
            if (dw->sond.sounds[i].name != NULL && strcmp(dw->sond.sounds[i].name, name) == 0) { index = (int32_t) i; break; }
        if (index < 0) { ++noName; continue; }
        if (hldSfxPlayed[index]) { ++played; continue; }
        hldSfxPlayed[index] = 1;  // listed already: not appended again
        const Sound* sound = &dw->sond.sounds[index];
        if (sound->audioGroup != 0 || sound->audioFile < 0 || (uint32_t) sound->audioFile >= dw->audo.count) { ++notEmbedded; continue; }
        const AudioEntry* entry = &dw->audo.entries[sound->audioFile];
        if (!entry->present || entry->dataSize < 4 || hldWarmBytes > HLD_WARM_PCM_LIMIT) { ++notEmbedded; continue; }
        uint8_t* bytes = (uint8_t*) malloc(entry->dataSize);
        if (bytes == NULL) continue;
        if (fseek(archive, (long) entry->dataOffset, SEEK_SET) != 0 || fread(bytes, 1, entry->dataSize, archive) != entry->dataSize) {
            free(bytes); ++readFail; continue;
        }
        HldWarm* warm = &hldWarm[index];
        uint32_t channels = 0, rate = 0, bits = 0, pcmBytes = 0;
        const uint8_t* pcmData = NULL;
        if (parseWavHeader(bytes, entry->dataSize, &channels, &rate, &bits, &pcmData, &pcmBytes) && pcmData != NULL && pcmBytes > 0 &&
            (channels == 1 || channels == 2) && (bits == 8 || bits == 16)) {
            // Embedded WAV (the Vita's data): the cost was reading it from the card; keep the bytes as read.
            warm->block = bytes; warm->pcm = pcmData; warm->bytes = pcmBytes;
            warm->channels = (int) channels; warm->rate = (int) rate; warm->bits = (int) bits;
            hldWarmBytes += entry->dataSize;
        } else if (memcmp(bytes, "OggS", 4) == 0) {
            int vorbisChannels = 0, vorbisRate = 0;
            short* pcm = NULL;
            const int samples = stb_vorbis_decode_memory(bytes, (int) entry->dataSize, &vorbisChannels, &vorbisRate, &pcm);
            free(bytes);
            if (samples <= 0 || pcm == NULL || (vorbisChannels != 1 && vorbisChannels != 2) || vorbisRate <= 0) { free(pcm); ++decodeFail; continue; }
            warm->block = pcm; warm->pcm = (const uint8_t*) pcm; warm->bytes = (uint32_t) samples * (uint32_t) vorbisChannels * sizeof(int16_t);
            warm->channels = vorbisChannels; warm->rate = vorbisRate; warm->bits = 16;
            hldWarmBytes += warm->bytes;
        } else {
            ++notOgg; free(bytes); continue;
        }
        __atomic_store_n(&warm->state, HLD_WARM_READY, __ATOMIC_RELEASE);
        ++warmed;
        sceKernelDelayThread(2000);
    }
    if (archive != NULL) fclose(archive);
    logInfo("Audio: warmed %u effects (%u KB) list=%d noName=%u played=%u notEmbedded=%u readFail=%u notOgg=%u decodeFail=%u\n", warmed,
            (unsigned) (hldWarmBytes / 1024), list != NULL, noName, played, notEmbedded, readFail, notOgg, decodeFail);
    if (list != NULL) fclose(list);
    int written = 0;
    for (;;) {
        sceKernelDelayThread(5 * 1000 * 1000);
        const int count = __atomic_load_n(&hldWarmNewCount, __ATOMIC_ACQUIRE);
        if (count == written) continue;
        FILE* out = fopen("ux0:data/hld-vm/sfx-warm.txt", "a");
        if (out == NULL) continue;
        for (; written < count; ++written) fprintf(out, "%s\n", dw->sond.sounds[hldWarmNew[written]].name);
        fclose(out);
    }
    return 0;
}

static void hldSfxWarmStart(AlAudioSystem* ma) {
    if (hldWarm != NULL) return;
    const DataWin* dw = ma->base.audioGroups[0];
    if (dw == NULL || dw->gen8.gameID != 742741103 || dw->sond.count == 0) return;
    hldWarm = (HldWarm*) calloc(dw->sond.count, sizeof(HldWarm));
    hldSfxPlayed = (uint8_t*) calloc(dw->sond.count, 1);
    hldWarmNewCapacity = (int) dw->sond.count;
    hldWarmNew = (int32_t*) calloc(dw->sond.count, sizeof(int32_t));
    if (hldWarm == NULL || hldSfxPlayed == NULL || hldWarmNew == NULL) return;
    hldWarmData = dw;
    hldWarmThread = sceKernelCreateThread("HldSfxWarm", hldSfxWarmWorker, 120, 0x20000, 0, SCE_KERNEL_CPU_MASK_USER_2, NULL);
    if (hldWarmThread >= 0) sceKernelStartThread(hldWarmThread, 0, NULL);
}

static bool hldSfxWarmUpload(AlAudioSystem* ma, int32_t soundIndex, SoundInstance* slot) {
    if (hldWarm == NULL || soundIndex < 0 || (uint32_t) soundIndex >= hldSfxCount) return false;
    HldWarm* warm = &hldWarm[soundIndex];
    if (__atomic_load_n(&warm->state, __ATOMIC_ACQUIRE) != HLD_WARM_READY) return false;
    const uint32_t bytes = warm->bytes;
    const ALenum format = warm->channels == 2 ? (warm->bits == 8 ? AL_FORMAT_STEREO8 : AL_FORMAT_STEREO16)
                                              : (warm->bits == 8 ? AL_FORMAT_MONO8 : AL_FORMAT_MONO16);
    alGetError();
    alBufferData(slot->alBuffer, format, warm->pcm, (ALsizei) bytes, warm->rate);
    free(warm->block);
    warm->block = NULL;
    warm->pcm = NULL;
    warm->state = HLD_WARM_DONE;
    if (alGetError() != AL_NO_ERROR) return false;
    alSourcei(slot->alSource, AL_BUFFER, slot->alBuffer);
    hldSfxRemember(ma, soundIndex, slot->alBuffer, bytes);
    if (++hldWarmUploads % 20 == 1) logInfo("Audio: warm uploads=%u\n", hldWarmUploads);
    return true;
}

static bool hldSfxIsCached(ALuint buffer) {
    if (buffer == 0 || hldSfxBuffers == NULL) return false;
    for (uint32_t i = 0; i < hldSfxCount; ++i) if (hldSfxBuffers[i] == buffer) return true;
    return false;
}
'''


def with_effect_buffer_cache(code):
    code = replace_once(code, 'static void releaseInstance(SoundInstance* inst) {',
                        'static bool hldSfxReuse(AlAudioSystem*, int32_t, SoundInstance*); '
                        'static void hldSfxRemember(AlAudioSystem*, int32_t, ALuint, uint32_t); '
                        'static bool hldSfxIsCached(ALuint); '
                        'static void releaseInstance(SoundInstance* inst) {')
    code = replace_once(code, '            AudioEntry* entry = &group->audo.entries[sound->audioFile];',
                        '            AudioEntry* entry = &group->audo.entries[sound->audioFile]; '
                        'if (hldSfxReuse(ma, soundIndex, slot)) goto hldSfxReused;')
    # The first of the two attach lines is the embedded (AUDO) branch.
    attach = '            alSourcei(slot->alSource, AL_BUFFER, slot->alBuffer);'
    first = code.index(attach)
    code = code[:first] + attach + ' hldSfxRemember(ma, soundIndex, slot->alBuffer, audioDataLen);' + code[first + len(attach):]
    code = replace_once(code, '            decodedData = nullptr;', '            decodedData = nullptr; hldSfxReused:;')
    # External effects (the 2016 game's .ogg files): stb_vorbis_decode_filename re-read and decoded the file on every play.
    code = replace_once(code, '            char* path = resolveExternalPath(ma, sound);',
                        '            if (hldSfxReuse(ma, soundIndex, slot)) goto hldSfxExternalReused; '
                        'char* path = resolveExternalPath(ma, sound);')
    second = code.index(attach, code.index('hldSfxExternalReused'))
    code = code[:second] + attach + ' hldSfxRemember(ma, soundIndex, slot->alBuffer, (uint32_t) (len * channels * sizeof(uint16_t)));' + \
        code[second + len(attach):]
    code = replace_once(code, '            free(path);\n        }\n    }\n', '            free(path); hldSfxExternalReused:;\n        }\n    }\n')
    code = replace_once(code, '        alDeleteBuffers(1, &inst->alBuffer);',
                        '        if (!hldSfxIsCached(inst->alBuffer)) alDeleteBuffers(1, &inst->alBuffer);')
    if "static PreparedAmbience ambienceJob, ambienceCache[AMBIENCE_CACHE_COUNT];" in code:
        code += RELEASE_OPTIONAL_FILES
    return code + HELPERS


# Called (main thread) when a file open fails for a reason other than a missing file: closes the prefetched ambience
# streams, which hold file handles only as an optimisation, so the open can be retried.
RELEASE_OPTIONAL_FILES = r"""
void hldReleaseOptionalFiles(void) {
    unsigned released = 0;
    for (unsigned i = 0; i < AMBIENCE_CACHE_COUNT; ++i)
        if (ambienceCache[i].decoder != nullptr) { releasePreparedAmbience(&ambienceCache[i]); ++released; }
    logWarn("FS: released %u prefetched ambience streams to free file handles\n", released);
}
"""
