"""Reuse unchanged sort keys across legacy drawable-cache rebuilds."""

from runtime_verification_source import replace_once


SORT = r'''
typedef struct {
    Runner* owner;
    size_t capacity, count;
    LegacyDrawOrder* previous;
    LegacyDrawOrder* current;
    LegacyDrawOrder* retained;
    LegacyDrawOrder* changed;
    Drawable* sorted;
    uint32_t* slots;
} RetainedDrawOrder;

static RetainedDrawOrder retainedDrawOrder;
static unsigned retainedDrawSorts, retainedDrawEntries, retainedDrawReused, retainedDrawFallbacks;
static uint64_t retainedDrawOldNanos, retainedDrawNewNanos;

static uint32_t retainedDrawSlot(uint32_t tie, const LegacyDrawOrder* keys, const uint32_t* slots, uint32_t mask) {
    uint32_t hash = (tie ^ (tie >> 16)) * UINT32_C(0x7feb352d);
    uint32_t slot = (hash ^ (hash >> 15)) & mask;
    while (slots[slot] != 0 && keys[slots[slot] - 1].tie != tie) slot = (slot + 1) & mask;
    return slot;
}

static int compareRetainedDrawKeys(const void* left, const void* right) {
    const LegacyDrawOrder* a = left;
    const LegacyDrawOrder* b = right;
    if (a->depth != b->depth) return a->depth > b->depth ? -1 : 1;
    return (a->tie > b->tie) - (a->tie < b->tie);
}

static bool sortWithRetainedDrawKeys(Runner* runner, Drawable* items, size_t count) {
    RetainedDrawOrder* cache = &retainedDrawOrder;
    if (cache->owner != runner) {
        cache->owner = runner;
        cache->count = 0;
    }
    if (count < 64 || count > UINT32_MAX / 4 ||
        count > SIZE_MAX / (8 * sizeof(LegacyDrawOrder) + 2 * sizeof(Drawable) + 4 * sizeof(uint32_t)))
        return false;
    if (cache->capacity < count) {
        size_t capacity = 64;
        while (capacity < count) capacity *= 2;
        cache->previous = safeRealloc(cache->previous, capacity * sizeof(*cache->previous));
        cache->current = safeRealloc(cache->current, capacity * sizeof(*cache->current));
        cache->retained = safeRealloc(cache->retained, capacity * sizeof(*cache->retained));
        cache->changed = safeRealloc(cache->changed, capacity * sizeof(*cache->changed));
        cache->sorted = safeRealloc(cache->sorted, capacity * sizeof(*cache->sorted));
        cache->slots = safeRealloc(cache->slots, 2 * capacity * sizeof(*cache->slots));
        cache->capacity = capacity;
    }

    const uint32_t slotMask = (uint32_t)(2 * cache->capacity - 1);
    const uint32_t orderMask = UINT32_MAX >> 2;
    memset(cache->slots, 0, 2 * cache->capacity * sizeof(*cache->slots));
    for (uint32_t i = 0; i < count; ++i) {
        const DrawKey key = drawableKey(&items[i]);
        if (key.layerOrder != 0 || !isfinite(key.depth) || (uint32_t)key.order > orderMask) return false;
        uint32_t tie = (uint32_t)key.order;
        if (key.type == DRAWABLE_LAYER || key.type == DRAWABLE_PARTICLE_SYSTEM) tie = orderMask - tie;
        tie |= (uint32_t)key.type << 30;
        const uint32_t slot = retainedDrawSlot(tie, cache->current, cache->slots, slotMask);
        // Duplicate IDs can require stable input order. Keep the original sort for those lists.
        if (cache->slots[slot] != 0) return false;
        cache->current[i] = (LegacyDrawOrder){key.depth, tie, i};
        cache->slots[slot] = i + 1;
    }

    size_t retained = 0;
    for (size_t i = 0; i < cache->count; ++i) {
        const LegacyDrawOrder old = cache->previous[i];
        const uint32_t slot = retainedDrawSlot(old.tie, cache->current, cache->slots, slotMask);
        if (cache->slots[slot] == 0) continue;
        LegacyDrawOrder* current = &cache->current[cache->slots[slot] - 1];
        if (current->depth != old.depth) continue;
        cache->retained[retained++] = *current;
        current->index = UINT32_MAX;
    }

    size_t changed = 0;
    for (size_t i = 0; i < count; ++i) {
        const LegacyDrawOrder key = cache->current[i];
        if (key.index != UINT32_MAX) cache->changed[changed++] = key;
    }
    qsort(cache->changed, changed, sizeof(*cache->changed), compareRetainedDrawKeys);

    size_t left = 0;
    size_t right = 0;
    for (size_t i = 0; i < count; ++i) {
        const bool takeRetained = right == changed ||
            (left != retained && compareRetainedDrawKeys(&cache->retained[left], &cache->changed[right]) < 0);
        const LegacyDrawOrder key = takeRetained ? cache->retained[left++] : cache->changed[right++];
        cache->previous[i] = key;
        cache->sorted[i] = items[key.index];
    }
    memcpy(items, cache->sorted, count * sizeof(*items));
    cache->count = count;
    ++retainedDrawSorts;
    retainedDrawEntries += (unsigned)count;
    retainedDrawReused += (unsigned)retained;
    return true;
}

static void freeRetainedDrawOrder(Runner* runner) {
    RetainedDrawOrder* cache = &retainedDrawOrder;
    if (cache->owner != runner) return;
    free(cache->slots);
    free(cache->sorted);
    free(cache->changed);
    free(cache->retained);
    free(cache->current);
    free(cache->previous);
    *cache = (RetainedDrawOrder){0};
}

void hldReportRetainedDrawOrder(int frame) {
    REPORT_GUARD
    logInfo("HLD retained draw order frame=%d sorts=%u entries=%u reused=%u fallbacks=%u old_us=%u new_us=%u\n",
            frame, retainedDrawSorts, retainedDrawEntries, retainedDrawReused, retainedDrawFallbacks,
            (unsigned)(retainedDrawOldNanos / 1000), (unsigned)(retainedDrawNewNanos / 1000));
    retainedDrawSorts = retainedDrawEntries = retainedDrawReused = retainedDrawFallbacks = 0;
    retainedDrawOldNanos = retainedDrawNewNanos = 0;
}
'''


CHECK = r'''
    Drawable* reference = safeMalloc(count * sizeof(*items));
    memcpy(reference, items, count * sizeof(*items));
    const uint64_t begin = nowNanos();
    const bool eligible = sortWithRetainedDrawKeys(runner, items, count);
    const uint64_t finish = nowNanos();
    if (eligible) {
        require(sortLegacyDrawables(reference, count, depthsChanged));
        const uint64_t end = nowNanos();
        retainedDrawNewNanos += finish - begin;
        retainedDrawOldNanos += end - finish;
        for (size_t index = 0; index < count; ++index) {
            const Drawable* a = &items[index];
            const Drawable* b = &reference[index];
            require(a->type == b->type && a->depth == b->depth && a->layerOrder == b->layerOrder &&
                    a->elementOrder == b->elementOrder);
            switch (a->type) {
                case DRAWABLE_INSTANCE: require(a->instance == b->instance); break;
                case DRAWABLE_TILE: require(a->tileIndex == b->tileIndex); break;
                case DRAWABLE_LAYER: require(a->runtimeLayerId == b->runtimeLayerId); break;
                case DRAWABLE_PARTICLE_SYSTEM: require(a->particleSystemId == b->particleSystemId); break;
            }
        }
    }
    free(reference);
'''


def with_retained_draw_order(code, *, checked=False, windowed_report=False):
    wrapper = "\nstatic bool sortRetainedDrawables(Runner* runner, Drawable* items, size_t count, bool depthsChanged) {\n"
    wrapper += CHECK if checked else "    const bool eligible = sortWithRetainedDrawKeys(runner, items, count);\n"
    wrapper += r'''
    if (eligible) return true;
    ++retainedDrawFallbacks;
    retainedDrawOrder.count = 0;
    return sortLegacyDrawables(items, count, depthsChanged);
}
'''
    anchor = "static void rebuildDrawableCacheIfDirty(Runner* runner) {"
    guard = "if (frame <= 0 || frame % 90 != 0) return;" if windowed_report else ""
    code = replace_once(code, anchor, SORT.replace("REPORT_GUARD", guard) + wrapper + "\n" + anchor)
    anchor = "    free(runner);\n"
    code = replace_once(code, anchor, "    freeRetainedDrawOrder(runner);\n" + anchor)
    original = "!sortLegacyDrawables(runner->cachedDrawables, count, "
    assert code.count(original) == 2
    return code.replace(original, "!sortRetainedDrawables(runner, runner->cachedDrawables, count, ")
