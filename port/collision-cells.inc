enum {
    COLLISION_CELL_AXIS = 32,
    COLLISION_CELL_COUNT = COLLISION_CELL_AXIS * COLLISION_CELL_AXIS,
    COLLISION_CELL_MAX_TARGETS = 4096,
    COLLISION_CELL_MAX_MEMBERSHIP = 12
};

_Static_assert(COLLISION_CELL_MAX_TARGETS * COLLISION_CELL_MAX_MEMBERSHIP <= UINT16_MAX,
               "Collision grid nodes must fit in the packed next index");

typedef struct { unsigned left, right, top, bottom; } CollisionCellRange;

static inline unsigned collisionCell(GMLReal coordinate, GMLReal scale) {
    const GMLReal cell = coordinate * scale;
    if (cell <= 0) return 0;
    if (cell >= COLLISION_CELL_AXIS - 1) return COLLISION_CELL_AXIS - 1;
    return (unsigned)cell;
}

static inline bool collisionFiniteRect(const InstanceBBox* box) {
    return isfinite(box->left) && isfinite(box->right) && isfinite(box->top) && isfinite(box->bottom) &&
           box->left <= box->right && box->top <= box->bottom;
}

static inline CollisionCellRange collisionCellsFor(const InstanceBBox* box, GMLReal sx, GMLReal sy) {
    return (CollisionCellRange){collisionCell(box->left, sx), collisionCell(box->right, sx),
                                collisionCell(box->top, sy), collisionCell(box->bottom, sy)};
}

static inline unsigned collisionCellArea(CollisionCellRange r) {
    return (r.right - r.left + 1) * (r.bottom - r.top + 1);
}

static void buildCollisionCellGrid(Runner* runner, CollisionTargetSnapshot* entry,
                                   int32_t base, int32_t count, unsigned words, GMLReal sx, GMLReal sy) {
    arrsetlen(entry->gridBits, COLLISION_CELL_COUNT + words);
    arrsetlen(entry->queryBits, words);
    arrsetlen(entry->gridNodes, 0);
    memset(entry->gridBits, 0, (COLLISION_CELL_COUNT + words) * sizeof(*entry->gridBits));
    uint32_t* wide = entry->gridBits + COLLISION_CELL_COUNT;
    for (int32_t i = 0; i < count; ++i) {
        Instance* instance = runner->instanceSnapshots[base + i];
        if (!instance->active) continue;
        const InstanceBBox* box = collisionDispatchBounds(runner, instance);
        if (box == nullptr) continue;
        if (collisionFiniteRect(box)) {
            const CollisionCellRange cell = collisionCellsFor(box, sx, sy);
            if (collisionCellArea(cell) <= COLLISION_CELL_MAX_MEMBERSHIP) {
                for (unsigned y = cell.top; y <= cell.bottom; ++y) {
                    for (unsigned x = cell.left; x <= cell.right; ++x) {
                        uint32_t* head = &entry->gridBits[y * COLLISION_CELL_AXIS + x];
                        arrput(entry->gridNodes, ((uint32_t)i << 16) | *head);
                        *head = (uint32_t)arrlen(entry->gridNodes);
                    }
                }
                continue;
            }
        }
        // Unusual and wide bounds stay admissible in every query.
        wide[(unsigned)i >> 5] |= UINT32_C(1) << ((unsigned)i & 31u);
    }
    entry->gridEpoch = runner->collisionDispatchEpoch;
}

static const uint32_t* findCollisionGridCandidates(Runner* runner, int32_t target,
        int32_t base, int32_t count, const InstanceBBox* box) {
    if (count < 128 || count > COLLISION_CELL_MAX_TARGETS || target < 0 ||
        (uint32_t)target >= runner->dataWin->objt.count || box == nullptr || !collisionFiniteRect(box) ||
        !runner->currentRoom->width || !runner->currentRoom->height) return nullptr;
    const GMLReal sx = (GMLReal)COLLISION_CELL_AXIS / runner->currentRoom->width;
    const GMLReal sy = (GMLReal)COLLISION_CELL_AXIS / runner->currentRoom->height;
    const CollisionCellRange range = collisionCellsFor(box, sx, sy);
    if (collisionCellArea(range) > 32) return nullptr;

#ifdef HLD_VERIFY_COLLISION_GRID
    static bool loggedVerification;
    if (!loggedVerification) {
        logInfo("Collision cell grid verification active, target count=%d\n", count);
        loggedVerification = true;
    }
#endif
    CollisionTargetSnapshot* entry = &runner->collisionTargets[target];
    const unsigned words = ((unsigned)count + 31u) >> 5;
    if (entry->gridEpoch != runner->collisionDispatchEpoch) {
        buildCollisionCellGrid(runner, entry, base, count, words, sx, sy);
        COUNT_GRID_BUILD();
    }
    uint32_t* result = entry->queryBits;
    memcpy(result, entry->gridBits + COLLISION_CELL_COUNT, words * sizeof(*result));
    for (unsigned y = range.top; y <= range.bottom; ++y) {
        for (unsigned x = range.left; x <= range.right; ++x) {
            uint32_t next = entry->gridBits[y * COLLISION_CELL_AXIS + x];
            while (next != 0) {
                const uint32_t packed = entry->gridNodes[next - 1];
                const unsigned ordinal = packed >> 16;
                result[ordinal >> 5] |= UINT32_C(1) << (ordinal & 31u);
                next = packed & UINT16_MAX;
            }
        }
    }
    // The dispatcher traverses the set bits in ascending original order.
    COUNT_GRID_QUERY();
    return result;
}
