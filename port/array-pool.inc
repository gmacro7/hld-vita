enum { ARRAY_POOL_SIZE = 12, ARRAY_POOL_MAX_CAPACITY = 1024 };

static GMLArray* arrayPool[ARRAY_POOL_SIZE];
static unsigned arrayPoolCount;
static bool arrayPoolLock;

#ifdef HLD_COMPARE_ARRAY_POOL
static bool arrayPoolEnabled;
#else
static bool arrayPoolEnabled = true;
#endif
#if defined(HLD_COMPARE_ARRAY_POOL) || defined(HLD_REPORT_ARRAY_POOL)
static unsigned arrayFresh, arrayReused, arraySaved, arrayGrowths;
#endif

static inline void lockArrayPool(void) {
    while (__atomic_test_and_set(&arrayPoolLock, __ATOMIC_ACQUIRE)) YIELD();
}

static inline void unlockArrayPool(void) {
    __atomic_clear(&arrayPoolLock, __ATOMIC_RELEASE);
}

static void freePooledArray(GMLArray* arr) {
    free(arr->legacy.rows[0].data);
    free(arr->legacy.rows);
    free(arr);
}

void hldClearArrayPool(void) {
    GMLArray* released[ARRAY_POOL_SIZE];
    lockArrayPool();
    const unsigned count = arrayPoolCount;
    memcpy(released, arrayPool, count * sizeof(*arrayPool));
    arrayPoolCount = 0;
    unlockArrayPool();
    for (unsigned i = 0; i < count; ++i) freePooledArray(released[i]);
#ifdef HLD_REPORT_ARRAY_POOL
    logInfo("HLD: legacy array pool fresh=%u reused=%u saved=%u growths=%u\n",
            __atomic_exchange_n(&arrayFresh, 0, __ATOMIC_RELAXED),
            __atomic_exchange_n(&arrayReused, 0, __ATOMIC_RELAXED),
            __atomic_exchange_n(&arraySaved, 0, __ATOMIC_RELAXED),
            __atomic_exchange_n(&arrayGrowths, 0, __ATOMIC_RELAXED));
#endif
}

static GMLArray* takePooledArray(int32_t minimum) {
    if (minimum < 0 || minimum > ARRAY_POOL_MAX_CAPACITY ||
        !__atomic_load_n(&arrayPoolEnabled, __ATOMIC_RELAXED)) return nullptr;
    GMLArray* result = nullptr;
    lockArrayPool();
    if (__atomic_load_n(&arrayPoolEnabled, __ATOMIC_RELAXED)) {
        for (unsigned i = arrayPoolCount; i > 0; --i) {
            if (arrayPool[i - 1]->legacy.rows[0].capacity < minimum) continue;
            result = arrayPool[i - 1];
            memmove(arrayPool + i - 1, arrayPool + i, (arrayPoolCount - i) * sizeof(*arrayPool));
            --arrayPoolCount;
            break;
        }
    }
    unlockArrayPool();
    return result;
}

static bool savePooledArray(GMLArray* arr) {
    if (arr->legacy.rowCount > 1 || arr->legacy.rows == nullptr ||
        !__atomic_load_n(&arrayPoolEnabled, __ATOMIC_RELAXED)) return false;
    GMLArrayRow* row = &arr->legacy.rows[0];
    if (row->capacity <= 0 || row->capacity > ARRAY_POOL_MAX_CAPACITY) return false;
    lockArrayPool();
    const bool saved = arrayPoolCount < ARRAY_POOL_SIZE && __atomic_load_n(&arrayPoolEnabled, __ATOMIC_RELAXED);
    if (saved) {
        // The destructor has released every live value before reaching here.
        row->length = 0;
        arr->legacy.rowCount = 0;
        arr->owner = nullptr;
        arrayPool[arrayPoolCount++] = arr;
    }
    unlockArrayPool();
#if defined(HLD_COMPARE_ARRAY_POOL) || defined(HLD_REPORT_ARRAY_POOL)
    if (saved) __atomic_fetch_add(&arraySaved, 1, __ATOMIC_RELAXED);
#endif
    return saved;
}

static GMLArray* allocateGmlArray(GMLArrayType type, int32_t minimum) {
    GMLArray* arr = type == GML_LEGACY_ARRAY ? takePooledArray(minimum) : nullptr;
#if defined(HLD_COMPARE_ARRAY_POOL) || defined(HLD_REPORT_ARRAY_POOL)
    __atomic_fetch_add(arr == nullptr ? &arrayFresh : &arrayReused, 1, __ATOMIC_RELAXED);
#endif
    return arr == nullptr ? (GMLArray*)safeCalloc(1, sizeof(GMLArray)) : arr;
}

#ifdef HLD_COMPARE_ARRAY_POOL
void hldUpdateArrayPool(int frame) {
    if (frame % 180 != 0) return;
    const bool enabled = frame >= 180 && (frame / 180) % 2 != 0;
    if (__atomic_exchange_n(&arrayPoolEnabled, enabled, __ATOMIC_RELAXED) && !enabled) {
        hldClearArrayPool();
    }
}

void hldReportArrayPool(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita array pool frame=%d enabled=%d fresh=%u reused=%u saved=%u growths=%u\n",
            frame, (int)__atomic_load_n(&arrayPoolEnabled, __ATOMIC_RELAXED),
            __atomic_exchange_n(&arrayFresh, 0, __ATOMIC_RELAXED),
            __atomic_exchange_n(&arrayReused, 0, __ATOMIC_RELAXED),
            __atomic_exchange_n(&arraySaved, 0, __ATOMIC_RELAXED),
            __atomic_exchange_n(&arrayGrowths, 0, __ATOMIC_RELAXED));
}
#endif
