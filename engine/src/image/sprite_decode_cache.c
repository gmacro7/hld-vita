#include "sprite_decode_cache.h"
#include "image_decoder.h"

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "log.h"

// Keep only a few large, sparse sprites. Exact compressed-byte comparison makes a
// cache hit independent of the filename, room and caller's sprite parameters.
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

struct SpriteDecodeCache {
    CachedSpritePixels entries[SPRITE_CACHE_ENTRIES];
    unsigned nextEntry;
};

void SpriteDecodeCache_free(SpriteDecodeCache* cache) {
    if (cache == nullptr) return;
    for (unsigned index = 0; index < SPRITE_CACHE_ENTRIES; ++index) {
        free(cache->entries[index].compressed);
        free(cache->entries[index].runs);
    }
    free(cache);
}

// Persistent tier: the same pixel runs on disk, keyed by two independent 64-bit hashes of the compressed
// bytes and their length, so entering a room in a later session skips the PNG decode.
static const char DISK_MAGIC[8] = {'H', 'L', 'D', 'R', 'L', 'E', '1', 0};

static const char* diskDirectory(void) {
#if defined(__VITA__) || defined(__vita__)
    return "ux0:data/hld-vm/decode-cache";
#else
    return getenv("HLD_DECODE_CACHE_DIR");
#endif
}

static void diskPath(char* path, size_t capacity, const char* directory, const uint8_t* file, size_t fileBytes) {
    uint64_t fnv = 1469598103934665603ull, mix = 0x9E3779B97F4A7C15ull;
    for (size_t i = 0; i < fileBytes; ++i) {
        fnv = (fnv ^ file[i]) * 1099511628211ull;
        mix = (mix ^ (file[i] + (mix << 6) + (mix >> 2))) * 0xBF58476D1CE4E5B9ull;
    }
    snprintf(path, capacity, "%s/%016llx%016llx-%llu.rle", directory, (unsigned long long)fnv,
             (unsigned long long)mix, (unsigned long long)fileBytes);
}

static void writeDiskRuns(const uint8_t* file, size_t fileBytes, const SpritePixelRun* runs, size_t runCount, int w, int h) {
    const char* directory = diskDirectory();
    if (directory == nullptr) return;
    char path[512], temporary[520];
    diskPath(path, sizeof(path), directory, file, fileBytes);
    struct stat existing;
    if (stat(path, &existing) == 0) return;
    mkdir(directory, 0777);
    snprintf(temporary, sizeof(temporary), "%s.tmp", path);
    FILE* out = fopen(temporary, "wb");
    if (out == nullptr) return;
    const uint64_t header[3] = {(uint64_t)fileBytes, (uint64_t)(uint32_t)w | ((uint64_t)(uint32_t)h << 32), (uint64_t)runCount};
    bool ok = fwrite(DISK_MAGIC, 1, sizeof(DISK_MAGIC), out) == sizeof(DISK_MAGIC) &&
              fwrite(header, sizeof(header), 1, out) == 1 &&
              fwrite(runs, sizeof(*runs), runCount, out) == runCount;
    ok = fclose(out) == 0 && ok;
    if (!ok || rename(temporary, path) != 0) remove(temporary);
}

static SpritePixelRun* readDiskRuns(const uint8_t* file, size_t fileBytes, size_t* runCount, int* w, int* h) {
    const char* directory = diskDirectory();
    if (directory == nullptr) return nullptr;
    char path[512];
    diskPath(path, sizeof(path), directory, file, fileBytes);
    FILE* in = fopen(path, "rb");
    if (in == nullptr) return nullptr;
    char magic[8];
    uint64_t header[3];
    SpritePixelRun* runs = nullptr;
    if (fread(magic, 1, sizeof(magic), in) == sizeof(magic) && memcmp(magic, DISK_MAGIC, sizeof(magic)) == 0 &&
        fread(header, sizeof(header), 1, in) == 1 && header[0] == fileBytes &&
        header[2] > 0 && header[2] <= SPRITE_CACHE_RUN_LIMIT) {
        *w = (int)(uint32_t)header[1];
        *h = (int)(uint32_t)(header[1] >> 32);
        *runCount = (size_t)header[2];
        runs = malloc(*runCount * sizeof(*runs));
        uint64_t total = 0;
        if (runs != nullptr && fread(runs, sizeof(*runs), *runCount, in) == *runCount) {
            for (size_t i = 0; i < *runCount; ++i) total += runs[i].count;
        }
        if (runs == nullptr || *w <= 0 || *h <= 0 || total != (uint64_t)*w * (uint64_t)*h) {
            free(runs);
            runs = nullptr;
        }
    }
    fclose(in);
    return runs;
}

static uint8_t* expandRuns(const SpritePixelRun* runs, size_t runCount, int w, int h) {
    uint32_t* rgba = malloc((size_t)w * (size_t)h * sizeof(*rgba));
    if (rgba == nullptr) return nullptr;
    uint32_t* out = rgba;
    for (size_t run = 0; run < runCount; ++run) {
        uint32_t* end = out + runs[run].count;
        const uint32_t color = runs[run].rgba;
        while (out != end) *out++ = color;
    }
    return (uint8_t*)rgba;
}

static void rememberSpritePixels(SpriteDecodeCache** cache, const uint8_t* file, size_t fileBytes, const uint8_t* pixels, int w, int h) {
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
    if (*cache == nullptr) {
        *cache = calloc(1, sizeof(**cache));
        if (*cache == nullptr) {
            free(copy);
            free(runs);
            return;
        }
    }
    SpritePixelRun* compact = realloc(runs, runCount * sizeof(*runs));
    if (compact != nullptr) runs = compact;
    writeDiskRuns(file, fileBytes, runs, runCount, w, h);
    CachedSpritePixels* entry = &(*cache)->entries[(*cache)->nextEntry++ % SPRITE_CACHE_ENTRIES];
    free(entry->compressed);
    free(entry->runs);
    *entry = (CachedSpritePixels){copy, fileBytes, runs, runCount, w, h};
}

uint8_t* SpriteDecodeCache_decode(SpriteDecodeCache** cache, const uint8_t* file, size_t fileBytes,
                                  int* w, int* h) {
    for (unsigned index = 0; *cache != nullptr && index < SPRITE_CACHE_ENTRIES; ++index) {
        const CachedSpritePixels* entry = &(*cache)->entries[index];
        if (entry->compressed == nullptr || entry->compressedSize != fileBytes ||
            memcmp(entry->compressed, file, fileBytes) != 0) continue;
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
        return (uint8_t*)rgba;
    }

    if (fileBytes <= SPRITE_CACHE_FILE_LIMIT) {
        size_t runCount;
        SpritePixelRun* runs = readDiskRuns(file, fileBytes, &runCount, w, h);
        if (runs != nullptr) {
            uint8_t* pixels = expandRuns(runs, runCount, *w, *h);
            uint8_t* copy = pixels != nullptr ? malloc(fileBytes) : nullptr;
            if (*cache == nullptr && copy != nullptr) *cache = calloc(1, sizeof(**cache));
            if (copy != nullptr && *cache != nullptr) {
                memcpy(copy, file, fileBytes);
                CachedSpritePixels* entry = &(*cache)->entries[(*cache)->nextEntry++ % SPRITE_CACHE_ENTRIES];
                free(entry->compressed);
                free(entry->runs);
                *entry = (CachedSpritePixels){copy, fileBytes, runs, runCount, *w, *h};
            } else {
                free(copy);
                free(runs);
            }
            if (pixels != nullptr) {
#ifdef HLD_VERIFY_SPRITE_CACHE
                int originalWidth, originalHeight;
                uint8_t* original = ImageDecoder_decodeToRgba(file, fileBytes, false, &originalWidth, &originalHeight);
                if (original == nullptr || originalWidth != *w || originalHeight != *h ||
                    memcmp(original, pixels, (size_t)*w * (size_t)*h * 4) != 0) {
                    logError("Disk sprite cache changed the original RGBA pixels\n");
                    abort();
                }
                free(original);
                logInfo("HLD: disk sprite cache hit %dx%d runs=%u\n", *w, *h, (unsigned)runCount);
#endif
                return pixels;
            }
        }
    }
    uint8_t* pixels = ImageDecoder_decodeToRgba(file, fileBytes, false, w, h);
    if (pixels != nullptr) rememberSpritePixels(cache, file, fileBytes, pixels, *w, *h);
    return pixels;
}
