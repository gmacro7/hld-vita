enum {
    COLLISION_GRID_MIN_TARGETS = 256,
    COLLISION_GRID_MAX_CANDIDATES = 32,
    COLLISION_GRID_MAX_CELLS = 16,
    COLLISION_GRID_MAX_MEMBERS = 96
};

static unsigned sharedGridQueries, sharedGridSkipped;

static bool sharedGridEnabled(int frame) {
#ifdef HLD_COMPARE_SHARED_COLLISION_GRID
    return frame >= 180 && (frame / 180) % 2 != 0;
#else
    (void)frame;
    return true;
#endif
}

#ifdef HLD_COMPARE_SHARED_COLLISION_GRID
void hldReportSharedCollisionGrid(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita shared collision grid frame=%d enabled=%d queries=%u skipped=%u\n",
            frame, (int)sharedGridEnabled(frame - 1), sharedGridQueries, sharedGridSkipped);
    sharedGridQueries = sharedGridSkipped = 0;
}
#endif

static bool sharedGridRange(SpatialGrid* grid, const InstanceBBox* bounds, SpatialGridRange* range) {
    // The original grid narrows each cell coordinate to int16_t before clamping.
    const GMLReal limit = (GMLReal)INT16_MAX * SPATIAL_GRID_CELL_SIZE;
    if (!(bounds->left >= -limit && bounds->left <= bounds->right && bounds->right <= limit &&
          bounds->top >= -limit && bounds->top <= bounds->bottom && bounds->bottom <= limit)) return false;
    *range = SpatialGrid_computeCellRange(grid, bounds->left, bounds->top, bounds->right, bounds->bottom);
    return true;
}

static bool sharedGridCoversTargets(Runner* runner, CollisionTargetSnapshot* entry,
                                   Instance* const* targets, int32_t count) {
    if (entry->gridEpoch == runner->collisionDispatchEpoch && entry->gridRevision == runner->instanceListRevision)
        return entry->gridCoversTargets;
    entry->gridEpoch = runner->collisionDispatchEpoch;
    entry->gridRevision = runner->instanceListRevision;
    entry->gridCoversTargets = false;

    SpatialGrid* grid = runner->spatialGrid;
    SpatialGrid_syncGrid(runner, grid);
    for (int32_t i = 0; i < count; ++i) {
        Instance* target = targets[i];
        if (i > 0 && (targets[i - 1]->objectIndex > target->objectIndex ||
                      (targets[i - 1]->objectIndex == target->objectIndex &&
                       targets[i - 1]->instanceId >= target->instanceId))) return false;
        if (!target->active) continue;
        const InstanceBBox* bounds = collisionDispatchBounds(runner, target);
        if (bounds == nullptr) continue;
        SpatialGridRange range;
        const size_t length = arrlenu(target->collisionCells);
        if (length == 0 || !sharedGridRange(grid, bounds, &range)) return false;
        // Insertion records a contiguous rectangle in X-major order. Prove it
        // still covers the live bounds in case a sprite changed without a grid update.
        const uint32_t first = target->collisionCells[0];
        const uint32_t last = target->collisionCells[length - 1];
        if (SpatialGrid_unpackGridX(first) > range.minGridX || SpatialGrid_unpackGridX(last) < range.maxGridX ||
            SpatialGrid_unpackGridY(first) > range.minGridY || SpatialGrid_unpackGridY(last) < range.maxGridY) return false;
    }
    entry->gridCoversTargets = true;
    return true;
}

static int32_t sharedGridTargetOrdinal(Instance* const* targets, int32_t count, const Instance* target) {
    int32_t low = 0, high = count;
    while (low < high) {
        const int32_t middle = low + (high - low) / 2;
        const Instance* candidate = targets[middle];
        if (candidate == target) return middle;
        if (candidate->objectIndex < target->objectIndex ||
            (candidate->objectIndex == target->objectIndex && candidate->instanceId < target->instanceId))
            low = middle + 1;
        else
            high = middle;
    }
    return -1;
}

static int32_t sharedGridCollisionCandidates(Runner* runner, int32_t targetObject, Instance* self,
                                             const InstanceBBox* selfBounds, int32_t start, int32_t count,
                                             int32_t* ordinals) {
    SpatialGrid* grid = runner->spatialGrid;
    if (count < COLLISION_GRID_MIN_TARGETS || grid == nullptr || grid->gridWidth <= 0 || grid->gridHeight <= 0 ||
        selfBounds == nullptr || targetObject < 0 || (uint32_t)targetObject >= runner->dataWin->objt.count) return -1;
    SpatialGridRange range;
    if (!sharedGridRange(grid, selfBounds, &range) ||
        (uint32_t)(range.maxGridX - range.minGridX + 1) * (range.maxGridY - range.minGridY + 1) > COLLISION_GRID_MAX_CELLS)
        return -1;

    Instance* const* targets = runner->instanceSnapshots + start;
    if (!sharedGridCoversTargets(runner, &runner->collisionTargets[targetObject], targets, count)) return -1;
    int32_t found = 0;
    size_t visited = 0;
    for (int32_t y = range.minGridY; y <= range.maxGridY; ++y) {
        for (int32_t x = range.minGridX; x <= range.maxGridX; ++x) {
            Instance* const* cell = grid->grid[SpatialGrid_cellIndex(grid, x, y)];
            const size_t length = arrlenu(cell);
            visited += length;
            if (visited > COLLISION_GRID_MAX_MEMBERS) return -1;
            for (size_t i = 0; i < length; ++i) {
                const Instance* other = cell[i];
                if (other == self || !other->active) continue;
                const int32_t ordinal = sharedGridTargetOrdinal(targets, count, other);
                if (ordinal < 0) continue;
                int32_t insertion = found;
                while (insertion > 0 && ordinals[insertion - 1] > ordinal) --insertion;
                if (insertion > 0 && ordinals[insertion - 1] == ordinal) continue;
                if (found == COLLISION_GRID_MAX_CANDIDATES) return -1;
                if (insertion < found) memmove(ordinals + insertion + 1, ordinals + insertion,
                                               (size_t)(found - insertion) * sizeof(*ordinals));
                ordinals[insertion] = ordinal;
                ++found;
            }
        }
    }
    ++sharedGridQueries;
#ifndef PLATFORM_VITA
    if (sharedGridQueries == 1000) logInfo("Shared collision grid accepted 1000 queries, skipped=%u\n", sharedGridSkipped);
#endif
    return found;
}
