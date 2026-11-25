typedef struct {
    GMLReal depth;
    uint32_t tie;
    uint32_t index;
} LegacyDrawOrder;

#ifdef HLD_DRAW_ORDER_REPAIR
static bool drawOrderInSequence(LegacyDrawOrder first, LegacyDrawOrder second) {
    return first.depth > second.depth || (first.depth == second.depth && first.tie <= second.tie);
}

static bool repairLegacyDrawOrder(LegacyDrawOrder* keys, size_t count) {
    size_t remainingShifts = count;
    for (size_t i = 1; i < count; ++i) {
        const LegacyDrawOrder key = keys[i];
        if (drawOrderInSequence(keys[i - 1], key)) continue;

        // Insert after equal keys so fallback still sees their original order.
        size_t lo = 0;
        size_t hi = i;
        while (lo < hi) {
            const size_t mid = lo + (hi - lo) / 2;
            if (drawOrderInSequence(keys[mid], key)) lo = mid + 1;
            else hi = mid;
        }
        const size_t shifts = i - lo;
        if (shifts > remainingShifts) return false;
        memmove(keys + lo + 1, keys + lo, shifts * sizeof(*keys));
        keys[lo] = key;
        remainingShifts -= shifts;
    }
    return true;
}
#endif

#ifdef HLD_COMPARE_DRAW_ORDER_REPAIR
extern bool hldDrawOrderRepair;
static unsigned drawRepairAttempts, drawRepairAccepted;

void hldReportDrawOrderRepair(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita draw order repair frame=%d enabled=%d attempts=%u repaired=%u\n",
            frame, (int)hldDrawOrderRepair, drawRepairAttempts, drawRepairAccepted);
    drawRepairAttempts = drawRepairAccepted = 0;
}
#endif

static bool sortLegacyDrawables(Drawable* items, size_t count, bool depthsChanged) {
    static const uint32_t orderMask = UINT32_MAX >> 2;
    if (count > SIZE_MAX / (2 * sizeof(LegacyDrawOrder) + sizeof(Drawable))) return false;
    LegacyDrawOrder* order = safeMalloc(count * (2 * sizeof(*order) + sizeof(Drawable)));
    LegacyDrawOrder* source = order;
    LegacyDrawOrder* target = order + count;
    Drawable* sorted = (Drawable*)(order + 2 * count);

    // Materialize the original keys once. Each merge then reads compact keys
    // without dereferencing the instances or copying the drawable payloads.
    for (size_t index = 0; index < count; index++) {
        const Drawable* drawable = &items[index];
        DrawKey key = drawableKey(drawable);
        if (key.layerOrder != 0 || !isfinite(key.depth) || (uint32_t)key.order > orderMask) {
            free(order);
            return false;
        }
        uint32_t tie = (uint32_t)key.order;
        if (key.type == DRAWABLE_LAYER || key.type == DRAWABLE_PARTICLE_SYSTEM) tie = orderMask - tie;
        source[index] = (LegacyDrawOrder){key.depth, ((uint32_t)key.type << 30) | tie, (uint32_t)index};
    }

    // Only a refresh of cached depths is likely to preserve most of the order.
    bool repaired = false;
#ifdef HLD_DRAW_ORDER_REPAIR
#ifdef HLD_COMPARE_DRAW_ORDER_REPAIR
    if (depthsChanged && hldDrawOrderRepair) {
        ++drawRepairAttempts;
        repaired = repairLegacyDrawOrder(source, count);
        drawRepairAccepted += repaired;
    }
#else
    if (depthsChanged) repaired = repairLegacyDrawOrder(source, count);
#endif
#else
    (void)depthsChanged;
#endif
    for (size_t stride = 1; !repaired && stride < count; stride *= 2) {
        for (size_t start = 0; start < count; start += 2 * stride) {
            size_t middle = start + stride < count ? start + stride : count;
            size_t end = middle + stride < count ? middle + stride : count;
            size_t left = start;
            size_t right = middle;
            size_t dest = start;
            while (left < middle && right < end) {
                LegacyDrawOrder a = source[left];
                LegacyDrawOrder b = source[right];
                if (a.depth > b.depth || (a.depth == b.depth && a.tie <= b.tie)) {
                    target[dest++] = a;
                    ++left;
                } else {
                    target[dest++] = b;
                    ++right;
                }
            }
            if (left < middle) memcpy(target + dest, source + left, (middle - left) * sizeof(*source));
            if (right < end) memcpy(target + dest, source + right, (end - right) * sizeof(*source));
        }
        LegacyDrawOrder* completed = target;
        target = source;
        source = completed;
    }

    for (size_t index = 0; index < count; index++) sorted[index] = items[source[index].index];
    memcpy(items, sorted, count * sizeof(*items));
    free(order);
    return true;
}
