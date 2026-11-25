enum { SPRITE_CACHE_ENTRIES = 4, SPRITE_CACHE_RUN_LIMIT = 65536, SPRITE_CACHE_FILE_LIMIT = 131072 };

typedef struct {
    uint32_t rgba;
    uint32_t count;
} SpritePixelRun;

typedef struct {
    uint8_t* compressed;
    size_t compressedSize;
    SpritePixelRun* runs;
    size_t runCount;
    int width, height;
} CachedSpritePixels;

static CachedSpritePixels spriteCache[SPRITE_CACHE_ENTRIES];
static unsigned nextSpriteCacheEntry;

static void rememberSpritePixels(const uint8_t* file, size_t fileBytes, const uint8_t* pixels, int w, int h) {
    if (w <= 0 || h <= 0 || (size_t)w > SIZE_MAX / sizeof(uint32_t) / (size_t)h) return;
    const size_t pixelCount = (size_t)w * (size_t)h;
    if (pixelCount < 1024 * 1024 || pixelCount > UINT32_MAX || fileBytes > SPRITE_CACHE_FILE_LIMIT) return;

    SpritePixelRun* runs = malloc(SPRITE_CACHE_RUN_LIMIT * sizeof(*runs));
    if (runs == nullptr) return;
    size_t runCount = 1;
    memcpy(&runs[0].rgba, pixels, sizeof(uint32_t));
    runs[0].count = 1;
    for (size_t index = 1; index < pixelCount; ++index) {
        uint32_t rgba;
        memcpy(&rgba, pixels + index * sizeof(rgba), sizeof(rgba));
        if (runs[runCount - 1].rgba == rgba) {
            ++runs[runCount - 1].count;
        } else {
            if (runCount == SPRITE_CACHE_RUN_LIMIT) {
                free(runs);
                return;
            }
            runs[runCount++] = (SpritePixelRun){.rgba = rgba, .count = 1};
        }
    }

    uint8_t* copy = malloc(fileBytes);
    if (copy == nullptr) {
        free(runs);
        return;
    }
    memcpy(copy, file, fileBytes);
    SpritePixelRun* compact = realloc(runs, runCount * sizeof(*runs));
    CachedSpritePixels* entry = &spriteCache[nextSpriteCacheEntry++ % SPRITE_CACHE_ENTRIES];
    free(entry->compressed);
    free(entry->runs);
    *entry = (CachedSpritePixels){copy, fileBytes, compact != nullptr ? compact : runs, runCount, w, h};
}

static uint8_t* decodeHldSprite(VMContext* ctx, const uint8_t* file, size_t fileBytes, int* w, int* h) {
    DataWin* dw = ctx->dataWin;
    if (dw->gen8.gameID != 742741103 || dw->gen8.wadVersion != 15)
        return ImageDecoder_decodeToRgba(file, fileBytes, false, w, h);

    for (unsigned index = 0; index < SPRITE_CACHE_ENTRIES; ++index) {
        const CachedSpritePixels* entry = &spriteCache[index];
        if (entry->compressed == nullptr || entry->compressedSize != fileBytes ||
            memcmp(entry->compressed, file, fileBytes) != 0) continue;
        const uint64_t begin = nowNanos();
        uint32_t* rgba = malloc((size_t)entry->width * (size_t)entry->height * sizeof(*rgba));
        if (rgba == nullptr) break;
        uint32_t* out = rgba;
        for (size_t run = 0; run < entry->runCount; ++run) {
            const SpritePixelRun* span = &entry->runs[run];
            uint32_t* end = out + span->count;
            const uint32_t color = span->rgba;
            while (out != end) *out++ = color;
        }
        *w = entry->width;
        *h = entry->height;
#ifdef HLD_VERIFY_SPRITE_CACHE
        int originalWidth, originalHeight;
        uint8_t* original = ImageDecoder_decodeToRgba(file, fileBytes, false, &originalWidth, &originalHeight);
        if (original == nullptr || originalWidth != *w || originalHeight != *h ||
            memcmp(original, rgba, (size_t)*w * (size_t)*h * sizeof(*rgba)) != 0) {
            logError("Sprite cache changed the original RGBA pixels\n");
            abort();
        }
        free(original);
#endif
        logInfo("HLD sprite cache hit frame=%d rgba=%dx%d runs=%u decode_ms=%.3f\n",
                ctx->runner->frameCount, *w, *h, (unsigned)entry->runCount, (nowNanos() - begin) * 1e-6);
        return (uint8_t*)rgba;
    }

    uint8_t* pixels = ImageDecoder_decodeToRgba(file, fileBytes, false, w, h);
    if (pixels != nullptr) rememberSpritePixels(file, fileBytes, pixels, *w, *h);
    return pixels;
}
