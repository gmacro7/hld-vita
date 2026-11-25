"""Keep bounded, immutable PCM for short effects in the 2016 audio runtime."""

from runtime_verification_source import replace_once


CACHE = r'''
enum { EFFECT_CACHE_SLOTS = 24, EFFECT_CACHE_BYTES = 2 * 1024 * 1024, EFFECT_MAX_BYTES = 256 * 1024 };

typedef struct {
    int32_t sound;
    const uint8_t* encoded;
    uint32_t encodedBytes;
    char* path;
    int16_t* pcm;
    int samples, channels, rate;
    unsigned bytes;
    uint64_t used;
} EffectPcm;

extern bool hldCacheSoundEffects;
static AlAudioSystem* effectCacheOwner;
static EffectPcm decodedEffects[EFFECT_CACHE_SLOTS];
static uint64_t effectCacheClock;
static unsigned effectCacheBytes, effectCacheHits, effectCacheMisses, effectCacheStores;
static bool effectLastHit;

bool hldLastEffectCacheHit(void) { return effectLastHit; }

static bool effectCacheEligible(AlAudioSystem* ma, bool loop) {
    const DataWin* archive = ma->base.dw;
    return hldCacheSoundEffects && !loop && archive != nullptr &&
        archive->gen8.gameID == 742741103 && archive->gen8.wadVersion == 15;
}

static void discardEffect(EffectPcm* entry) {
    effectCacheBytes -= entry->bytes;
    free(entry->pcm);
    free(entry->path);
    *entry = (EffectPcm){0};
}

static void clearEffectCache(AlAudioSystem* owner) {
    if (effectCacheOwner != owner) return;
    for (unsigned i = 0; i < EFFECT_CACHE_SLOTS; ++i) discardEffect(&decodedEffects[i]);
    effectCacheOwner = nullptr;
    effectCacheClock = 0;
}

static const EffectPcm* findEffect(AlAudioSystem* owner, int32_t sound, bool eligible,
                                   const uint8_t* encoded, uint32_t encodedBytes, const char* path) {
    if (!eligible || (effectCacheOwner != nullptr && effectCacheOwner != owner)) return nullptr;
    for (unsigned i = 0; i < EFFECT_CACHE_SLOTS; ++i) {
        EffectPcm* entry = &decodedEffects[i];
        if (entry->pcm == nullptr || entry->sound != sound) continue;
        const bool same = path != nullptr ? entry->path != nullptr && strcmp(entry->path, path) == 0
            : entry->path == nullptr && entry->encoded == encoded && entry->encodedBytes == encodedBytes;
        if (same) {
            entry->used = ++effectCacheClock;
            ++effectCacheHits;
            effectLastHit = true;
            return entry;
        }
    }
    ++effectCacheMisses;
    return nullptr;
}

static int oldestEffect(void) {
    int oldest = -1;
    for (int i = 0; i < EFFECT_CACHE_SLOTS; ++i) {
        if (decodedEffects[i].pcm != nullptr &&
            (oldest < 0 || decodedEffects[i].used < decodedEffects[oldest].used)) oldest = i;
    }
    return oldest;
}

// Only successfully uploaded PCM reaches this point. OpenAL already owns
// its copy; eviction cannot change the active voices or queued sources.
static bool retainEffect(AlAudioSystem* owner, int32_t sound, bool eligible,
                         const uint8_t* encoded, uint32_t encodedBytes, const char* path,
                         int16_t* data, int samples, int channels, int rate) {
    if (!eligible || data == nullptr || samples <= 0 || (channels != 1 && channels != 2) ||
        rate <= 0 || (effectCacheOwner != nullptr && effectCacheOwner != owner)) return false;
    const uint64_t size = (uint64_t)samples * (unsigned)channels * sizeof(*data);
    if (size > EFFECT_MAX_BYTES) return false;
    char* savedPath = path != nullptr ? strdup(path) : nullptr;
    if (path != nullptr && savedPath == nullptr) return false;
    // stb_vorbis may allocate more than the number of decoded samples.
    int16_t* trimmed = realloc(data, (size_t)size);
    if (trimmed == nullptr) {
        free(savedPath);
        return false;
    }

    while (effectCacheBytes + size > EFFECT_CACHE_BYTES) discardEffect(&decodedEffects[oldestEffect()]);
    int slot = 0;
    while (slot < EFFECT_CACHE_SLOTS && decodedEffects[slot].pcm != nullptr) ++slot;
    if (slot == EFFECT_CACHE_SLOTS) {
        slot = oldestEffect();
        discardEffect(&decodedEffects[slot]);
    }
    decodedEffects[slot] = (EffectPcm){sound, encoded, encodedBytes, savedPath, trimmed,
        samples, channels, rate, (unsigned)size, ++effectCacheClock};
    effectCacheOwner = owner;
    effectCacheBytes += (unsigned)size;
    ++effectCacheStores;
    return true;
}

void hldReportDecodedEffects(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita decoded effects frame=%d enabled=%d hits=%u misses=%u stores=%u resident_bytes=%u\n",
            frame, (int)hldCacheSoundEffects, effectCacheHits, effectCacheMisses, effectCacheStores, effectCacheBytes);
    effectCacheHits = effectCacheMisses = effectCacheStores = 0;
}

'''


def with_decoded_effect_cache(source):
    anchor = "static void maDestroy(AudioSystem* audio) {"
    source = replace_once(source, anchor, CACHE + anchor)
    anchor = "    stopAmbienceWorker(ma);"
    source = replace_once(source, anchor, anchor + "\n    clearEffectCache(ma);")
    start = source.index("static int32_t maPlaySound(")
    end = source.index("\nstatic void maSetSoundSpatial(", start)
    body = source[start:end]
    anchor = "    AlAudioSystem* ma = (AlAudioSystem*) audio;"
    body = replace_once(body, anchor, anchor + "\n    effectLastHit = false;")
    anchor = "        (void)alGetError(); // band-aid for the undertale 'thud'"
    body = replace_once(body, anchor, "        const bool cacheEffect = !isStream && effectCacheEligible(ma, loop);\n" + anchor)
    anchor = "            int16_t* decodedData = nullptr;"
    body = replace_once(body, anchor, anchor + "\n            int decodedSamples = 0;")
    anchor = "                int decodedSamples = stb_vorbis_decode_memory(entry->data, entry->dataSize, &vorbisChannels, &vorbisSampleRate, &decodedData);"
    body = replace_once(body, anchor, r'''                const EffectPcm* cached = findEffect(ma, soundIndex, cacheEffect, entry->data, entry->dataSize, nullptr);
                const int16_t* pcm;
                if (cached != nullptr) {
                    decodedSamples = cached->samples;
                    vorbisChannels = cached->channels;
                    vorbisSampleRate = cached->rate;
                    pcm = cached->pcm;
                } else {
                    decodedSamples = stb_vorbis_decode_memory(entry->data, entry->dataSize, &vorbisChannels,
                                                             &vorbisSampleRate, &decodedData);
                    pcm = decodedData;
                }''')
    body = replace_once(body, "if (decodedSamples <= 0 || decodedData == nullptr",
                        "if (decodedSamples <= 0 || pcm == nullptr")
    body = replace_once(body, "audioData = (uint8_t*) decodedData;", "audioData = (const uint8_t*) pcm;")
    anchor = "            free(decodedData);\n            decodedData = nullptr;"
    body = replace_once(body, anchor, r'''            if (!retainEffect(ma, soundIndex, cacheEffect, entry->data, entry->dataSize, nullptr,
                              decodedData, decodedSamples, channels, sampleRate))
                free(decodedData);''')
    old = "            int len = stb_vorbis_decode_filename(path, &channels, &sample_rate, &data);"
    body = replace_once(body, old, r'''            const EffectPcm* cached = findEffect(ma, soundIndex, cacheEffect, nullptr, 0, path);
            int len;
            const int16_t* pcm;
            if (cached != nullptr) {
                len = cached->samples;
                channels = cached->channels;
                sample_rate = cached->rate;
                pcm = cached->pcm;
            } else {
                len = stb_vorbis_decode_filename(path, &channels, &sample_rate, &data);
                pcm = data;
            }''')
    body = replace_once(body, "if (len <= 0 || data == nullptr)", "if (len <= 0 || pcm == nullptr)")
    body = replace_once(body, "                (void*)data,", "                pcm,")
    body = replace_once(body, "            if(data != NULL) free(data);\n            free(path);",
                        r'''            if (!retainEffect(ma, soundIndex, cacheEffect, nullptr, 0, path,
                              data, len, channels, sample_rate))
                free(data);
            free(path);''')
    return source[:start] + body + source[end:]


def with_positional_effect_profile(source):
    anchor = "    if (loop && afterSetup - audioStart >= 1000000u) {"
    return replace_once(source, anchor, r'''    if (!loop && (ctx->runner->frameCount == 595 || ctx->runner->frameCount == 633)) {
        const DataWin* data = ctx->runner->dataWin;
        const char* name = soundIndex >= 0 && (uint32_t)soundIndex < data->sond.count
            ? data->sond.sounds[soundIndex].name : nullptr;
        extern bool hldLastEffectCacheHit(void);
        logInfo("Vita positional effect runner_frame=%d name=%s fence_ms=%.2f start_ms=%.2f spatial_ms=%.2f pcm_hit=%d\n",
                ctx->runner->frameCount, name != nullptr ? name : "?", (beforePlay - audioStart) * 1e-6,
                (afterPlay - beforePlay) * 1e-6, (afterSetup - afterPlay) * 1e-6, (int)hldLastEffectCacheHit());
    }
''' + anchor)
