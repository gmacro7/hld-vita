enum { MASK_TILE_SIZE = 8, MASK_TILE_CACHE_SIZE = 2, MAX_MASK_TILES = 65536 };

typedef struct {
    const uint8_t* mask;
    uint32_t width, height, stride, pitch;
    uint32_t* sums;
} MaskTileIndex;

static MaskTileIndex maskTileCache[MASK_TILE_CACHE_SIZE];
static unsigned maskTileVictim, maskTileBuilds, maskTileLookups, maskTileRejects;

static void freeMaskTileIndex(MaskTileIndex* entry) {
    free(entry->sums);
    *entry = (MaskTileIndex){0};
}

void hldForgetMaskTiles(const Sprite* sprite) {
    for (uint32_t i = 0; i < sprite->maskCount; ++i) {
        for (unsigned slot = 0; slot < MASK_TILE_CACHE_SIZE; ++slot) {
            MaskTileIndex* entry = &maskTileCache[slot];
            if (entry->mask == sprite->masks[i]) freeMaskTileIndex(entry);
        }
    }
}

void hldFreeMaskTileIndex(void) {
    logInfo("HLD: mask tile index builds=%u lookups=%u rejects=%u\n",
            maskTileBuilds, maskTileLookups, maskTileRejects);
    for (unsigned slot = 0; slot < MASK_TILE_CACHE_SIZE; ++slot)
        freeMaskTileIndex(&maskTileCache[slot]);
    maskTileVictim = maskTileBuilds = maskTileLookups = maskTileRejects = 0;
}

static const MaskTileIndex* getMaskTileIndex(const CollisionPointQuery* query) {
    const uint32_t width = query->sprite->maskWidth, height = query->sprite->maskHeight;
    for (unsigned slot = 0; slot < MASK_TILE_CACHE_SIZE; ++slot) {
        const MaskTileIndex* entry = &maskTileCache[slot];
        if (entry->mask == query->mask && entry->width == width &&
            entry->height == height && entry->stride == query->stride) return entry;
    }

    const uint32_t cols = query->stride, rows = (height + MASK_TILE_SIZE - 1) / MASK_TILE_SIZE;
    const uint32_t pitch = cols + 1;
    const size_t entries = (size_t)pitch * (rows + 1);
    if (entries > MAX_MASK_TILES) return nullptr;
    uint32_t* sums = calloc(entries, sizeof(*sums));
    if (sums == nullptr) return nullptr;
    for (uint32_t row = 0; row < rows; ++row) {
        const uint32_t firstY = row * MASK_TILE_SIZE;
        const uint32_t lastY = height - firstY < MASK_TILE_SIZE ? height : firstY + MASK_TILE_SIZE;
        uint32_t rowSum = 0;
        for (uint32_t col = 0; col < cols; ++col) {
            uint8_t bits = 0;
            for (uint32_t y = firstY; y < lastY; ++y)
                bits |= query->mask[(size_t)y * cols + col];
            rowSum += bits != 0;
            sums[(size_t)(row + 1) * pitch + col + 1] = rowSum + sums[(size_t)row * pitch + col + 1];
        }
    }

    MaskTileIndex* entry = &maskTileCache[maskTileVictim++ % MASK_TILE_CACHE_SIZE];
    freeMaskTileIndex(entry);
    *entry = (MaskTileIndex){query->mask, width, height, query->stride, pitch, sums};
    ++maskTileBuilds;
    return entry;
}

static inline GMLReal maskTileLocalCoordinate(CollisionMaskAxis axis, GMLReal world) {
    const GMLReal delta = world - axis.position;
    return (axis.scale == 1.0 ? delta : delta / axis.scale) + (GMLReal)axis.origin;
}

static bool maskTileAxisRange(CollisionMaskAxis axis, GMLReal lo, GMLReal hi, uint32_t* first, uint32_t* stop) {
    GMLReal a = maskTileLocalCoordinate(axis, lo);
    GMLReal b = maskTileLocalCoordinate(axis, hi);
    if (!isfinite(a) || !isfinite(b)) return false;
    if (a > b) { const GMLReal swap = a; a = b; b = swap; }
    if (!(a > -1048576.0 && b < 1048576.0)) return false;
    int32_t min = a <= -1.0 ? -1 : (int32_t)a;
    int32_t max = b <= -1.0 ? -1 : (int32_t)b;
    if (min < axis.first) min = axis.first;
    if (max > axis.last) max = axis.last;
    if (min > max) { *first = *stop = 0; return true; }
    *first = (uint32_t)(min - axis.maskOffset) / MASK_TILE_SIZE;
    *stop = (uint32_t)(max - axis.maskOffset) / MASK_TILE_SIZE + 1;
    return true;
}

static bool emptyMaskTiles(const CollisionPointQuery* query, GMLReal left, GMLReal top,
                           GMLReal right, GMLReal bottom, bool countOverlap) {
    if (query->mask == nullptr || query->rotated) return false;
    const uint32_t width = query->sprite->maskWidth, height = query->sprite->maskHeight;
    if (width == 0 || width > 4096 || height == 0 || height > 4096 ||
        width * height < 32768 || query->stride != (width + 7) / 8) return false;
    if (!Collision_canSplitMaskAxes(query) || query->scaleX == 0 || query->scaleY == 0) return false;

    uint32_t x1, y1, x2, y2;
    if (!maskTileAxisRange(Collision_maskAxis(query, true), left, right, &x1, &x2) ||
        !maskTileAxisRange(Collision_maskAxis(query, false), top, bottom, &y1, &y2)) return false;
    if (x1 == x2 || y1 == y2) return true;

    const MaskTileIndex* tiles = getMaskTileIndex(query);
    if (tiles == nullptr) return false;
    const uint32_t* lower = tiles->sums + (size_t)y1 * tiles->pitch;
    const uint32_t* upper = tiles->sums + (size_t)y2 * tiles->pitch;
    const bool empty = upper[x2] - upper[x1] == lower[x2] - lower[x1];
    if (countOverlap) {
        ++maskTileLookups;
        maskTileRejects += empty;
    }
    return empty;
}

bool hldEmptyMaskTiles(const CollisionPointQuery* query, GMLReal left, GMLReal top, GMLReal right, GMLReal bottom) {
    return emptyMaskTiles(query, left, top, right, bottom, true);
}
