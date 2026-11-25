enum { PLACEMENT_CELL_CACHE_SIZE = 1024 };

typedef struct {
    int32_t cell;
    int32_t target;
    uint32_t revision;
    bool valid;
    Instance** matches;
} PlacementCell;

static SpatialGrid* cachedPlacementGrid;
static uint32_t* placementCellRevisions;
static PlacementCell placementCells[PLACEMENT_CELL_CACHE_SIZE];

static void invalidatePlacementCells(void) {
    repeat(PLACEMENT_CELL_CACHE_SIZE, i) placementCells[i].valid = false;
}

void hldPlacementGridReset(SpatialGrid* grid) {
    if (cachedPlacementGrid != grid) return;
    free(placementCellRevisions);
    placementCellRevisions = nullptr;
    cachedPlacementGrid = nullptr;
    repeat(PLACEMENT_CELL_CACHE_SIZE, i) arrfree(placementCells[i].matches);
    invalidatePlacementCells();
}

void hldPlacementCellChanged(SpatialGrid* grid, int32_t index) {
    if (cachedPlacementGrid != grid) return;
    if (++placementCellRevisions[index] == 0) invalidatePlacementCells();
}

#ifdef HLD_COMPARE_PLACEMENT_CELLS
static unsigned placementCellHits, placementCellBuilds, placementCellSkipped;

static inline bool usePlacementCells(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportPlacementCells(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita placement cells frame=%d enabled=%d hits=%u rebuilds=%u skipped=%u\n",
            frame, (int)usePlacementCells(frame - 1), placementCellHits, placementCellBuilds, placementCellSkipped);
    placementCellHits = placementCellBuilds = placementCellSkipped = 0;
}
#else
static inline bool usePlacementCells(int frame) {
    (void)frame;
    return true;
}
#endif

static Instance** placementCellTargets(Runner* runner, Instance** instances, int32_t cell, int32_t target) {
    SpatialGrid* grid = runner->spatialGrid;
    if (cachedPlacementGrid != grid) {
        if (cachedPlacementGrid != nullptr) hldPlacementGridReset(cachedPlacementGrid);
        const size_t cellCount = (size_t)grid->gridWidth * grid->gridHeight;
        placementCellRevisions = (uint32_t*)safeCalloc(cellCount, sizeof(*placementCellRevisions));
        cachedPlacementGrid = grid;
    }
    const uint32_t hash = (uint32_t)cell * 0x9e3779b9u ^ (uint32_t)target * 0x85ebca6bu;
    PlacementCell* entry = &placementCells[(hash ^ (hash >> 16)) & (PLACEMENT_CELL_CACHE_SIZE - 1)];
    const uint32_t revision = placementCellRevisions[cell];
    if (!entry->valid || entry->cell != cell || entry->target != target || entry->revision != revision) {
        entry->cell = cell;
        entry->target = target;
        entry->revision = revision;
        entry->valid = true;
        arrsetlen(entry->matches, 0);
        repeat(arrlen(instances), i) {
            Instance* inst = instances[i];
            if (placementObjectMatches(runner->dataWin, inst->objectIndex, target)) arrput(entry->matches, inst);
        }
#ifdef HLD_COMPARE_PLACEMENT_CELLS
        ++placementCellBuilds;
    } else {
        ++placementCellHits;
        placementCellSkipped += (uint32_t)(arrlen(instances) - arrlen(entry->matches));
#endif
    }
#ifdef HLD_VERIFY_PLACEMENT_CELLS
    size_t match = 0;
    repeat(arrlen(instances), i) {
        Instance* inst = instances[i];
        if (!VM_isObjectOrDescendant(runner->dataWin, inst->objectIndex, target)) continue;
        require(match < (size_t)arrlen(entry->matches) && entry->matches[match] == inst);
        ++match;
    }
    require(match == (size_t)arrlen(entry->matches));
#endif
    return entry->matches;
}
