"""Prepare nearby file-backed ambience off the game thread without delaying playback."""

from runtime_verification_source import replace_once


WORKER = r'''
#include <psp2/kernel/threadmgr.h>

extern bool hldPrefetchAmbience;

enum { AMBIENCE_IDLE, AMBIENCE_QUEUED, AMBIENCE_READY, AMBIENCE_CACHE_COUNT = 4 };
typedef struct {
    int32_t soundId;
    char* path;
    stb_vorbis* decoder;
} PreparedAmbience;

static AlAudioSystem* ambienceOwner;
static PreparedAmbience ambienceJob, ambienceCache[AMBIENCE_CACHE_COUNT];
static SceUID ambienceThread = -1, ambienceWake = -1;
static int ambienceStatus;
static bool ambienceStopping;
static unsigned ambienceReplace;
static int32_t failedAmbienceId = -1;

static void releasePreparedAmbience(PreparedAmbience* item) {
    if (item->decoder != nullptr) stb_vorbis_close(item->decoder);
    free(item->path);
    *item = (PreparedAmbience){0};
}

static int prepareAmbienceOnWorker(SceSize size, void* args) {
    (void)size;
    (void)args;
    while (sceKernelWaitSema(ambienceWake, 1, nullptr) >= 0) {
        if (__atomic_load_n(&ambienceStopping, __ATOMIC_ACQUIRE)) break;
        if (__atomic_load_n(&ambienceStatus, __ATOMIC_ACQUIRE) != AMBIENCE_QUEUED) continue;
        ambienceJob.decoder = stb_vorbis_open_filename(ambienceJob.path, nullptr, nullptr);
        if (ambienceJob.decoder != nullptr &&
            stb_vorbis_get_info(ambienceJob.decoder).setup_memory_required > 1024 * 1024) {
            stb_vorbis_close(ambienceJob.decoder);
            ambienceJob.decoder = nullptr;
        }
        __atomic_store_n(&ambienceStatus, AMBIENCE_READY, __ATOMIC_RELEASE);
    }
    return 0;
}

static bool startAmbienceWorker(AlAudioSystem* ma) {
    if (ambienceOwner != nullptr) return ambienceOwner == ma;
    ambienceWake = sceKernelCreateSema("HldAmbienceWake", 0, 0, 1, nullptr);
    if (ambienceWake < 0) return false;
    ambienceThread = sceKernelCreateThread("HldAmbiencePrefetch", prepareAmbienceOnWorker,
                                            110, 0x18000, 0, 0, nullptr);
    if (ambienceThread < 0 || sceKernelStartThread(ambienceThread, 0, nullptr) < 0) {
        if (ambienceThread >= 0) sceKernelDeleteThread(ambienceThread);
        sceKernelDeleteSema(ambienceWake);
        ambienceThread = ambienceWake = -1;
        return false;
    }
    ambienceOwner = ma;
    return true;
}

static void stopAmbienceWorker(AlAudioSystem* ma) {
    if (ambienceOwner != ma) return;
    __atomic_store_n(&ambienceStopping, true, __ATOMIC_RELEASE);
    sceKernelSignalSema(ambienceWake, 1);
    require(sceKernelWaitThreadEnd(ambienceThread, nullptr, nullptr) >= 0);
    sceKernelDeleteThread(ambienceThread);
    sceKernelDeleteSema(ambienceWake);
    ambienceThread = ambienceWake = -1;
    releasePreparedAmbience(&ambienceJob);
    for (unsigned i = 0; i < AMBIENCE_CACHE_COUNT; ++i) releasePreparedAmbience(&ambienceCache[i]);
    ambienceStatus = AMBIENCE_IDLE;
    ambienceStopping = false;
    ambienceOwner = nullptr;
    failedAmbienceId = -1;
    ambienceReplace = 0;
}

static void collectPreparedAmbience(void) {
    if (__atomic_load_n(&ambienceStatus, __ATOMIC_ACQUIRE) != AMBIENCE_READY) return;
    if (ambienceJob.decoder != nullptr) {
        unsigned slot = 0;
        while (slot < AMBIENCE_CACHE_COUNT && ambienceCache[slot].decoder != nullptr) ++slot;
        if (slot == AMBIENCE_CACHE_COUNT) slot = ambienceReplace++ % AMBIENCE_CACHE_COUNT;
        releasePreparedAmbience(&ambienceCache[slot]);
        ambienceCache[slot] = ambienceJob;
        ambienceJob = (PreparedAmbience){0};
    } else {
        failedAmbienceId = ambienceJob.soundId;
        releasePreparedAmbience(&ambienceJob);
    }
    __atomic_store_n(&ambienceStatus, AMBIENCE_IDLE, __ATOMIC_RELEASE);
}

static stb_vorbis* takePreparedAmbience(AlAudioSystem* ma, int32_t soundId, const char* path) {
    if (ambienceOwner != ma) return nullptr;
    collectPreparedAmbience();
    for (unsigned i = 0; i < AMBIENCE_CACHE_COUNT; ++i) {
        PreparedAmbience* item = &ambienceCache[i];
        if (item->decoder == nullptr || item->soundId != soundId || strcmp(item->path, path) != 0) continue;
        stb_vorbis* decoder = item->decoder;
        item->decoder = nullptr;
        releasePreparedAmbience(item);
        return decoder;
    }
    return nullptr;
}

bool hldRequestAmbience(AudioSystem* audio, int32_t id) {
    AlAudioSystem* ma = (AlAudioSystem*) audio;
    DataWin* data = ma->base.dw;
    if (!hldPrefetchAmbience || data == nullptr || ma->alContext == nullptr || id < 0 ||
        (uint32_t)id >= data->sond.count) return false;
    Sound* sound = &data->sond.sounds[id];
    if (sound->name == nullptr || strncmp(sound->name, "MUS_AMB_", 8) != 0 ||
        (sound->flags & AUDIO_ENTRY_FLAG_REGULAR) != AUDIO_ENTRY_FLAG_REGULAR ||
        (sound->flags & (AUDIO_ENTRY_FLAG_IS_EMBEDDED | AUDIO_ENTRY_FLAG_IS_COMPRESSED)) != 0) return false;

    if (ambienceOwner == ma) collectPreparedAmbience();
    if (__atomic_load_n(&ambienceStatus, __ATOMIC_ACQUIRE) != AMBIENCE_IDLE || id == failedAmbienceId) return false;
    for (unsigned i = 0; i < AMBIENCE_CACHE_COUNT; ++i)
        if (ambienceCache[i].decoder != nullptr && ambienceCache[i].soundId == id) return false;
    for (unsigned i = 0; i < MAX_SOUND_INSTANCES; ++i)
        if (ma->instances[i].active && ma->instances[i].soundIndex == id) return false;

    char* path = resolveExternalPath(ma, sound);
    if (path == nullptr) return false;
    const size_t length = strlen(path);
    if (length < 4 || strcmp(path + length - 4, ".ogg") != 0 || !startAmbienceWorker(ma)) {
        free(path);
        return false;
    }
    ambienceJob = (PreparedAmbience){id, path, nullptr};
    __atomic_store_n(&ambienceStatus, AMBIENCE_QUEUED, __ATOMIC_RELEASE);
    if (sceKernelSignalSema(ambienceWake, 1) >= 0) return true;
    __atomic_store_n(&ambienceStatus, AMBIENCE_IDLE, __ATOMIC_RELEASE);
    releasePreparedAmbience(&ambienceJob);
    return false;
}
'''


PREFETCH_NEARBY = r'''
extern bool hldPrefetchAmbience;
extern bool hldRequestAmbience(AudioSystem* audio, int32_t id);

static void prefetchNearbyAmbience(Runner* runner) {
    if (!hldPrefetchAmbience || runner->frameCount < 180 || runner->frameCount % 8 != 2 ||
        runner->audioSystem == nullptr || runner->instancesByExactObject == nullptr) return;
    const DataWin* data = runner->dataWin;
    if (data->gen8.gameID != 742741103 || data->gen8.wadVersion != 15 || data->objt.count <= 419) return;
    const char* emitterType = data->objt.objects[62].name;
    const char* playerType = data->objt.objects[419].name;
    if (emitterType == nullptr || playerType == nullptr || strcmp(emitterType, "Ambience") != 0 ||
        strcmp(playerType, "char") != 0) return;

    Instance** actors = runner->instancesByExactObject[419];
    const Instance* player = nullptr;
    for (size_t i = 0; i < arrlenu(actors); ++i) {
        if (!actors[i]->active) continue;
        player = actors[i];
        break;
    }
    if (player == nullptr) return;
    const ptrdiff_t songSlot = shgeti(runner->vmContext->varNameMap, "song");
    if (songSlot < 0) return;
    const int32_t songVar = runner->vmContext->varNameMap[songSlot].value;

    enum { NEARBY_AMBIENCE_COUNT = 4 };
    struct { int32_t id; float distance; } nearby[NEARBY_AMBIENCE_COUNT];
    unsigned count = 0;
    Instance** emitters = runner->instancesByExactObject[62];
    for (size_t i = 0; i < arrlenu(emitters); ++i) {
        Instance* emitter = emitters[i];
        if (!emitter->active) continue;
        const float dx = emitter->x - player->x, dy = emitter->y - player->y;
        const float distance = dx * dx + dy * dy;
        if (!(distance <= 320.0f * 320.0f)) continue;
        const RValue value = Instance_getSelfVar(emitter, songVar);
        if (value.type != RVALUE_REAL && value.type != RVALUE_INT32) continue;
        const GMLReal song = RValue_toReal(value);
        if (!(song >= 0 && song < (GMLReal)data->sond.count && song < (GMLReal)2147483648.0)) continue;
        const int32_t id = (int32_t)song;
        const char* name = data->sond.sounds[id].name;
        if (song != (GMLReal)id || name == nullptr || strncmp(name, "MUS_AMB_", 8) != 0) continue;

        // Keep the nearest emitter for each track. Keep the list ordered for the worker.
        unsigned slot = 0;
        while (slot < count && nearby[slot].id != id) ++slot;
        if (slot < count && nearby[slot].distance <= distance) continue;
        if (slot == NEARBY_AMBIENCE_COUNT) {
            slot = NEARBY_AMBIENCE_COUNT - 1;
            if (nearby[slot].distance <= distance) continue;
        } else if (slot == count) {
            ++count;
        }
        while (slot > 0 && nearby[slot - 1].distance > distance) {
            nearby[slot] = nearby[slot - 1];
            --slot;
        }
        nearby[slot].id = id;
        nearby[slot].distance = distance;
    }
    for (unsigned i = 0; i < count; ++i) {
        if (hldRequestAmbience(runner->audioSystem, nearby[i].id)) break;
    }
}
'''


def with_constant_vorbis_crc(code):
    start = code.index("static uint32 crc_table[256];")
    end = code.index("static __forceinline uint32 crc32_update", start)
    values = []
    for byte in range(256):
        crc = byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04C11DB7 if crc & 0x80000000 else 0)) & 0xFFFFFFFF
        values.append(f"0x{crc:08x}u")
    rows = ("   " + ", ".join(values[i:i + 8]) + ",\n" for i in range(0, len(values), 8))
    code = code[:start] + "static const uint32 crc_table[256] = {\n" + "".join(rows) + "};\n\n" + code[end:]
    return replace_once(code, "   crc32_init(); // always init it, to avoid multithread race conditions\n", "")


def with_ambient_prefetch_loop(code):
    anchor = "#include \"stb_ds.h\""
    code = replace_once(code, anchor, anchor + "\n" + PREFETCH_NEARBY)
    anchor = "                // Update audio system (gain fading, cleanup ended sounds)"
    return replace_once(code, anchor, "                prefetchNearbyAmbience(runner);\n" + anchor)


def with_ambient_prefetch_audio(code):
    anchor = "// ===[ Vtable Implementations ]==="
    code = replace_once(code, anchor, WORKER + "\n" + anchor)
    anchor = ("static void maDestroy(AudioSystem* audio) {\n    Fmod_audioDestroy(audio);\n"
              "    AlAudioSystem* ma = (AlAudioSystem*) audio;")
    code = replace_once(code, anchor, anchor + "\n    stopAmbienceWorker(ma);")
    start = code.index("static int32_t maPlaySound(")
    end = code.index("\nstatic void maSetSoundSpatial(", start)
    body = code[start:end]
    body = replace_once(body, "    char* assetStreamPath = nullptr;", "    bool usedPrefetchedAmbience = false;\n    char* assetStreamPath = nullptr;")
    anchor = '''        FILE* wavFile = openWavStream(streamPath, &wavChannels, &wavSampleRate, &wavDataStart, &wavDataBytes);

        stb_vorbis* v = nullptr;
        if (wavFile == nullptr) {'''
    body = replace_once(body, anchor, r'''        stb_vorbis* v = assetStreamPath != nullptr ? takePreparedAmbience(ma, soundIndex, streamPath) : nullptr;
        usedPrefetchedAmbience = v != nullptr;
        FILE* wavFile = v == nullptr
            ? openWavStream(streamPath, &wavChannels, &wavSampleRate, &wavDataStart, &wavDataBytes) : nullptr;
        if (v == nullptr && wavFile == nullptr) {''')
    anchor = '"buffers_ms=%.2f prime_ms=%.2f start_ms=%.2f\\n", sound->name,'
    body = replace_once(body, anchor, '"buffers_ms=%.2f prime_ms=%.2f start_ms=%.2f prefetched=%u\\n", sound->name,')
    anchor = "(primeEnd - primeStart) * 1e-6, (afterPlay - primeEnd) * 1e-6);"
    body = replace_once(body, anchor,
                        "(primeEnd - primeStart) * 1e-6, (afterPlay - primeEnd) * 1e-6, (unsigned)usedPrefetchedAmbience);")
    return code[:start] + body + code[end:]
