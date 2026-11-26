"""Reuse the OpenAL buffer of an embedded sound effect instead of decoding and uploading it on every play.

The 2016 runtime decodes the whole Vorbis effect (stb_vorbis_decode_memory) and uploads it with alBufferData each
time audio_play_sound_at starts it, about 2 ms per sound on the Vita, many times per second in a fight. A buffer's
PCM never changes, so the first decode is kept: later plays attach the same buffer to their new source. Cached
buffers are never deleted while the runtime lives; the total is capped, beyond which sounds behave as before.
Hooks sit on existing lines so the profile-guided build keeps its line layout; the helpers go at the end.

When the cache is full the least recently played buffers no live voice uses are freed. A sound whose first play had
to read and decode on the main thread is listed in sfx-warm.txt with the room it played in; entering that room on
a later run prefetches it on the warm worker, so a boss's big sounds no longer stall its first attack.
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
static uint32_t* hldSfxBytesOf;   // per sound: bytes of its cached buffer
static uint32_t* hldSfxLastUse;   // per sound: hldSfxClock at its last play
static uint32_t hldSfxClock;
static unsigned hldSfxEvictions;
static bool hldSfxWarmUpload(AlAudioSystem* ma, int32_t soundIndex, SoundInstance* slot);
static void hldSfxWarmStart(AlAudioSystem* ma);
static void hldSfxMarkPlayed(int32_t soundIndex, bool slow);
static void hldSfxForgetWarm(int32_t soundIndex);

static bool hldSfxEnsureTables(const DataWin* dw) {
    if (hldSfxBuffers != NULL) return true;
    if (dw == NULL) return false;
    hldSfxCount = dw->sond.count;
    hldSfxBuffers = (ALuint*) calloc(hldSfxCount, sizeof(ALuint));
    hldSfxBytesOf = (uint32_t*) calloc(hldSfxCount, sizeof(uint32_t));
    hldSfxLastUse = (uint32_t*) calloc(hldSfxCount, sizeof(uint32_t));
    if (hldSfxBuffers == NULL || hldSfxBytesOf == NULL || hldSfxLastUse == NULL) {
        free(hldSfxBuffers); free(hldSfxBytesOf); free(hldSfxLastUse);
        hldSfxBuffers = NULL; hldSfxBytesOf = NULL; hldSfxLastUse = NULL;
        return false;
    }
    return true;
}

// Frees least recently played cached buffers (never one a live voice is using) until `bytes` more fit.
static bool hldSfxMakeRoom(AlAudioSystem* ma, size_t bytes) {
    while (hldSfxBytes + bytes > HLD_SFX_CACHE_LIMIT) {
        int32_t oldest = -1;
        for (uint32_t i = 0; i < hldSfxCount; ++i) {
            if (hldSfxBuffers[i] == 0 || (oldest >= 0 && hldSfxLastUse[i] >= hldSfxLastUse[oldest])) continue;
            bool playing = false;
            for (int v = 0; v < MAX_SOUND_INSTANCES && !playing; ++v)
                playing = ma->instances[v].active && !ma->instances[v].streaming && ma->instances[v].alBuffer == hldSfxBuffers[i];
            if (!playing) oldest = (int32_t) i;
        }
        if (oldest < 0) return false;
        alDeleteBuffers(1, &hldSfxBuffers[oldest]);
        hldSfxBuffers[oldest] = 0;
        hldSfxBytes -= hldSfxBytesOf[oldest];
        hldSfxBytesOf[oldest] = 0;
        hldSfxForgetWarm(oldest);
        if (++hldSfxEvictions % 20 == 1) logInfo("Audio: effect cache evictions=%u bytes=%u\n", hldSfxEvictions, (unsigned) hldSfxBytes);
    }
    return true;
}
static bool hldSfxReuse(AlAudioSystem* ma, int32_t soundIndex, SoundInstance* slot) {
    hldSfxMissStart = nowNanos();
    hldSfxWarmStart(ma);
    if (!hldSfxEnsureTables(ma->base.audioGroups[0]) || soundIndex < 0 || (uint32_t) soundIndex >= hldSfxCount) return false;
    const ALuint cached = hldSfxBuffers[soundIndex];
    if (cached == 0) return hldSfxWarmUpload(ma, soundIndex, slot);
    hldSfxLastUse[soundIndex] = ++hldSfxClock;
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
    const bool slow = decodeMs > 3.0;
    if (slow) logInfo("Audio: first play decode %s ms=%.1f bytes=%u\n", dw->sond.sounds[soundIndex].name, decodeMs, bytes);
    hldSfxMarkPlayed(soundIndex, slow);
    if (bytes > HLD_SFX_CACHE_LIMIT / 4 || !hldSfxEnsureTables(dw)) return;
    if ((uint32_t) soundIndex >= hldSfxCount || hldSfxBuffers[soundIndex] != 0) return;
    if (!hldSfxMakeRoom(ma, bytes)) return;
    hldSfxBuffers[soundIndex] = buffer;
    hldSfxBytesOf[soundIndex] = bytes;
    hldSfxLastUse[soundIndex] = ++hldSfxClock;
    hldSfxBytes += bytes;
    ++hldSfxStores;
}

// ---- Warm-up: decode effects heard in earlier sessions on a background thread ----
// ux0:data/hld-vm/sfx-warm.txt lists sound names in the order they were first heard. At startup a low-priority
// worker reads each listed embedded Vorbis effect straight from data.win (its own file handle; offsets and sizes
// are immutable) and decodes it to PCM. The first play then only uploads that PCM. New first plays are appended
// to the list by the same worker, so the set grows with what the player actually hears.
enum { HLD_WARM_IDLE = 0, HLD_WARM_QUEUED = 1, HLD_WARM_READY = 2, HLD_WARM_DONE = 3, HLD_WARM_PCM_LIMIT = 16 * 1024 * 1024,
       HLD_ROOM_PCM_LIMIT = 12 * 1024 * 1024, HLD_ROOM_NAME = 48 };
typedef struct { int state; void* block; const uint8_t* pcm; uint32_t bytes; int channels, rate, bits; } HldWarm;
static HldWarm* hldWarm;
static uint8_t* hldSfxPlayed;   // 1: listed (or heard this session), 2: listed with the room it was first slow in
static char (*hldWarmRoom)[HLD_ROOM_NAME]; // per sound: the room its tagged list line names ("" if none)
static uint8_t* hldWarmByRoom;  // per sound: READY because a room prefetched it (freed when leaving that room)
static int32_t* hldWarmNew;     // indices to append to the list
static char (*hldWarmNewRoom)[HLD_ROOM_NAME];
static volatile int hldWarmNewCount;
static int hldWarmNewCapacity;
static const DataWin* hldWarmData;
static SceUID hldWarmThread = -1;
static size_t hldWarmBytes;
static volatile size_t hldWarmPending;     // room-prefetched PCM not yet uploaded
static char hldSfxCurrentRoom[HLD_ROOM_NAME];
static volatile unsigned hldSfxRoomSerial;
static unsigned hldWarmUploads, hldRoomWarmed;

// Called by the runner when a room starts: remembered for new list lines, and the worker prefetches the room's sounds.
void hldSfxRoomStarted(const char* name) {
    snprintf(hldSfxCurrentRoom, sizeof(hldSfxCurrentRoom), "%s", name != NULL ? name : "");
    __atomic_add_fetch(&hldSfxRoomSerial, 1, __ATOMIC_RELEASE);
}

static void hldSfxMarkPlayed(int32_t soundIndex, bool slow) {
    if (hldSfxPlayed == NULL || soundIndex < 0 || (uint32_t) soundIndex >= hldSfxCount) return;
    // A play that had to read and decode on the main thread is tagged with its room, so later visits prefetch it.
    const uint8_t want = slow && hldSfxCurrentRoom[0] != '\0' ? 2 : 1;
    if (hldSfxPlayed[soundIndex] >= want) return;
    hldSfxPlayed[soundIndex] = want;
    if (want == 2 && hldWarmRoom != NULL) snprintf(hldWarmRoom[soundIndex], HLD_ROOM_NAME, "%s", hldSfxCurrentRoom);
    if (hldWarmNewCount < hldWarmNewCapacity) {
        hldWarmNew[hldWarmNewCount] = soundIndex;
        snprintf(hldWarmNewRoom[hldWarmNewCount], HLD_ROOM_NAME, "%s", want == 2 ? hldSfxCurrentRoom : "");
        __atomic_store_n(&hldWarmNewCount, hldWarmNewCount + 1, __ATOMIC_RELEASE);
    }
}

// An evicted buffer may be prefetched again on a later visit.
static void hldSfxForgetWarm(int32_t soundIndex) {
    if (hldWarm == NULL || soundIndex < 0 || (uint32_t) soundIndex >= hldSfxCount) return;
    int done = HLD_WARM_DONE;
    __atomic_compare_exchange_n(&hldWarm[soundIndex].state, &done, HLD_WARM_IDLE, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
}

typedef enum { HLD_WARM_OK, HLD_WARM_NOT_EMBEDDED, HLD_WARM_READ_FAIL, HLD_WARM_NOT_OGG, HLD_WARM_DECODE_FAIL } HldWarmResult;

// Worker thread: reads one embedded effect from data.win (own handle; offsets and sizes are immutable) into hldWarm.
static HldWarmResult hldWarmOne(FILE* archive, const DataWin* dw, int32_t index, size_t* pcmBytesOut) {
    const Sound* sound = &dw->sond.sounds[index];
    if (sound->audioGroup != 0 || sound->audioFile < 0 || (uint32_t) sound->audioFile >= dw->audo.count) return HLD_WARM_NOT_EMBEDDED;
    const AudioEntry* entry = &dw->audo.entries[sound->audioFile];
    if (!entry->present || entry->dataSize < 4) return HLD_WARM_NOT_EMBEDDED;
    uint8_t* bytes = (uint8_t*) malloc(entry->dataSize);
    if (bytes == NULL) return HLD_WARM_READ_FAIL;
    if (fseek(archive, (long) entry->dataOffset, SEEK_SET) != 0 || fread(bytes, 1, entry->dataSize, archive) != entry->dataSize) {
        free(bytes); return HLD_WARM_READ_FAIL;
    }
    HldWarm* warm = &hldWarm[index];
    uint32_t channels = 0, rate = 0, bits = 0, pcmBytes = 0;
    const uint8_t* pcmData = NULL;
    if (parseWavHeader(bytes, entry->dataSize, &channels, &rate, &bits, &pcmData, &pcmBytes) && pcmData != NULL && pcmBytes > 0 &&
        (channels == 1 || channels == 2) && (bits == 8 || bits == 16)) {
        // Embedded WAV (the Vita's data): the cost was reading it from the card; keep the bytes as read.
        warm->block = bytes; warm->pcm = pcmData; warm->bytes = pcmBytes;
        warm->channels = (int) channels; warm->rate = (int) rate; warm->bits = (int) bits;
        *pcmBytesOut = entry->dataSize;
    } else if (memcmp(bytes, "OggS", 4) == 0) {
        int vorbisChannels = 0, vorbisRate = 0;
        short* pcm = NULL;
        const int samples = stb_vorbis_decode_memory(bytes, (int) entry->dataSize, &vorbisChannels, &vorbisRate, &pcm);
        free(bytes);
        if (samples <= 0 || pcm == NULL || (vorbisChannels != 1 && vorbisChannels != 2) || vorbisRate <= 0) { free(pcm); return HLD_WARM_DECODE_FAIL; }
        warm->block = pcm; warm->pcm = (const uint8_t*) pcm; warm->bytes = (uint32_t) samples * (uint32_t) vorbisChannels * sizeof(int16_t);
        warm->channels = vorbisChannels; warm->rate = vorbisRate; warm->bits = 16;
        *pcmBytesOut = warm->bytes;
    } else {
        free(bytes); return HLD_WARM_NOT_OGG;
    }
    return HLD_WARM_OK;
}

static int32_t hldWarmIndexOf(const DataWin* dw, const char* name) {
    for (uint32_t i = 0; i < dw->sond.count; ++i)
        if (dw->sond.sounds[i].name != NULL && strcmp(dw->sond.sounds[i].name, name) == 0) return (int32_t) i;
    return -1;
}

static int hldSfxWarmWorker(SceSize args, void* argp) {
    (void) args; (void) argp;
    const DataWin* dw = hldWarmData;
    FILE* list = fopen("ux0:data/hld-vm/sfx-warm.txt", "r");
    FILE* archive = fopen("ux0:data/hld-vm/data.win", "rb");
    char line[192];
    unsigned warmed = 0, tagged = 0, noName = 0, played = 0, notEmbedded = 0, readFail = 0, notOgg = 0, decodeFail = 0;
    if (archive == NULL) logWarn("Audio: warm worker could not open data.win\n");
    // Lines are "name" (heard before, warmed at startup up to HLD_WARM_PCM_LIMIT) or "name<TAB>room" (also prefetched
    // on entering that room).
    while (list != NULL && archive != NULL && fgets(line, sizeof(line), list) != NULL) {
        line[strcspn(line, "\r\n")] = '\0';
        char* room = strchr(line, '\t');
        if (room != NULL) *room++ = '\0';
        const int32_t index = hldWarmIndexOf(dw, line);
        if (index < 0) { ++noName; continue; }
        if (room != NULL && room[0] != '\0') {
            snprintf(hldWarmRoom[index], HLD_ROOM_NAME, "%s", room);
            hldSfxPlayed[index] = 2;
            ++tagged;
            continue;
        }
        if (hldSfxPlayed[index]) { ++played; continue; }
        hldSfxPlayed[index] = 1;  // listed already: not appended again
    }
    // Startup pass over untagged names, in the order first heard.
    if (list != NULL && archive != NULL) {
        rewind(list);
        while (fgets(line, sizeof(line), list) != NULL) {
            line[strcspn(line, "\r\n")] = '\0';
            if (strchr(line, '\t') != NULL) continue;
            const int32_t index = hldWarmIndexOf(dw, line);
            if (index < 0 || hldWarm[index].state != HLD_WARM_IDLE) continue;
            if (hldWarmBytes > HLD_WARM_PCM_LIMIT) { ++notEmbedded; continue; }
            size_t pcmBytes = 0;
            switch (hldWarmOne(archive, dw, index, &pcmBytes)) {
                case HLD_WARM_OK: break;
                case HLD_WARM_NOT_EMBEDDED: ++notEmbedded; continue;
                case HLD_WARM_READ_FAIL: ++readFail; continue;
                case HLD_WARM_NOT_OGG: ++notOgg; continue;
                case HLD_WARM_DECODE_FAIL: ++decodeFail; continue;
            }
            hldWarmBytes += pcmBytes;
            __atomic_store_n(&hldWarm[index].state, HLD_WARM_READY, __ATOMIC_RELEASE);
            ++warmed;
            sceKernelDelayThread(2000);
        }
    }
    logInfo("Audio: warmed %u effects (%u KB) list=%d tagged=%u noName=%u played=%u notEmbedded=%u readFail=%u notOgg=%u decodeFail=%u\n",
            warmed, (unsigned) (hldWarmBytes / 1024), list != NULL, tagged, noName, played, notEmbedded, readFail, notOgg, decodeFail);
    if (list != NULL) fclose(list);
    int written = 0;
    unsigned roomSeen = 0, idle = 0;
    char room[HLD_ROOM_NAME];
    for (;;) {
        sceKernelDelayThread(50 * 1000);
        const unsigned serial = __atomic_load_n(&hldSfxRoomSerial, __ATOMIC_ACQUIRE);
        if (serial != roomSeen && archive != NULL) {
            roomSeen = serial;
            snprintf(room, sizeof(room), "%s", hldSfxCurrentRoom);
            // Drop what was prefetched for other rooms and never played (the main thread claims READY entries first).
            for (uint32_t i = 0; i < dw->sond.count; ++i) {
                if (!hldWarmByRoom[i] || (hldWarmRoom[i][0] != '\0' && strcmp(hldWarmRoom[i], room) == 0)) continue;
                int ready = HLD_WARM_READY;
                if (!__atomic_compare_exchange_n(&hldWarm[i].state, &ready, HLD_WARM_QUEUED, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) continue;
                const uint32_t bytes = hldWarm[i].bytes;
                free(hldWarm[i].block);
                hldWarm[i].block = NULL;
                hldWarm[i].pcm = NULL;
                hldWarmByRoom[i] = 0;
                const size_t pending = __atomic_load_n(&hldWarmPending, __ATOMIC_ACQUIRE);
                __atomic_sub_fetch(&hldWarmPending, bytes < pending ? bytes : pending, __ATOMIC_ACQ_REL);
                __atomic_store_n(&hldWarm[i].state, HLD_WARM_IDLE, __ATOMIC_RELEASE);
            }
            unsigned count = 0;
            for (uint32_t i = 0; i < dw->sond.count; ++i) {
                if (hldWarmRoom[i][0] == '\0' || strcmp(hldWarmRoom[i], room) != 0) continue;
                if (__atomic_load_n(&hldWarm[i].state, __ATOMIC_ACQUIRE) != HLD_WARM_IDLE) continue;
                if (hldSfxBuffers != NULL && i < hldSfxCount && hldSfxBuffers[i] != 0) continue;  // already cached
                if (__atomic_load_n(&hldWarmPending, __ATOMIC_ACQUIRE) > HLD_ROOM_PCM_LIMIT) break;
                size_t pcmBytes = 0;
                if (hldWarmOne(archive, dw, (int32_t) i, &pcmBytes) != HLD_WARM_OK) continue;
                __atomic_add_fetch(&hldWarmPending, hldWarm[i].bytes, __ATOMIC_ACQ_REL);
                hldWarmByRoom[i] = 1;
                __atomic_store_n(&hldWarm[i].state, HLD_WARM_READY, __ATOMIC_RELEASE);
                ++count;
                if (__atomic_load_n(&hldSfxRoomSerial, __ATOMIC_ACQUIRE) != roomSeen) break;  // left the room already
            }
            if (count > 0) { hldRoomWarmed += count; logInfo("Audio: prefetched %u sounds for %s (pending %u KB)\n", count, room, (unsigned) (hldWarmPending / 1024)); }
        }
        if (++idle < 100) continue;  // the list is appended every ~5 s
        idle = 0;
        const int count = __atomic_load_n(&hldWarmNewCount, __ATOMIC_ACQUIRE);
        if (count == written) continue;
        FILE* out = fopen("ux0:data/hld-vm/sfx-warm.txt", "a");
        if (out == NULL) continue;
        for (; written < count; ++written) {
            const char* name = dw->sond.sounds[hldWarmNew[written]].name;
            if (hldWarmNewRoom[written][0] != '\0') fprintf(out, "%s\t%s\n", name, hldWarmNewRoom[written]);
            else fprintf(out, "%s\n", name);
        }
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
    hldWarmNewRoom = calloc(dw->sond.count, sizeof(*hldWarmNewRoom));
    hldWarmRoom = calloc(dw->sond.count, sizeof(*hldWarmRoom));
    hldWarmByRoom = (uint8_t*) calloc(dw->sond.count, 1);
    if (hldWarm == NULL || hldSfxPlayed == NULL || hldWarmNew == NULL || hldWarmNewRoom == NULL || hldWarmRoom == NULL ||
        hldWarmByRoom == NULL) return;
    hldWarmData = dw;
    hldWarmThread = sceKernelCreateThread("HldSfxWarm", hldSfxWarmWorker, 120, 0x20000, 0, SCE_KERNEL_CPU_MASK_USER_2, NULL);
    if (hldWarmThread >= 0) sceKernelStartThread(hldWarmThread, 0, NULL);
}

static bool hldSfxWarmUpload(AlAudioSystem* ma, int32_t soundIndex, SoundInstance* slot) {
    if (hldWarm == NULL || soundIndex < 0 || (uint32_t) soundIndex >= hldSfxCount) return false;
    HldWarm* warm = &hldWarm[soundIndex];
    int ready = HLD_WARM_READY;  // claim it: the worker may otherwise free a room prefetch it no longer needs
    if (!__atomic_compare_exchange_n(&warm->state, &ready, HLD_WARM_QUEUED, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return false;
    const uint32_t bytes = warm->bytes;
    const ALenum format = warm->channels == 2 ? (warm->bits == 8 ? AL_FORMAT_STEREO8 : AL_FORMAT_STEREO16)
                                              : (warm->bits == 8 ? AL_FORMAT_MONO8 : AL_FORMAT_MONO16);
    alGetError();
    alBufferData(slot->alBuffer, format, warm->pcm, (ALsizei) bytes, warm->rate);
    if (hldWarmByRoom != NULL && hldWarmByRoom[soundIndex]) {
        hldWarmByRoom[soundIndex] = 0;
        size_t pending = __atomic_load_n(&hldWarmPending, __ATOMIC_ACQUIRE);
        __atomic_sub_fetch(&hldWarmPending, bytes < pending ? bytes : pending, __ATOMIC_ACQ_REL);
    }
    free(warm->block);
    warm->block = NULL;
    warm->pcm = NULL;
    __atomic_store_n(&warm->state, HLD_WARM_DONE, __ATOMIC_RELEASE);
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
