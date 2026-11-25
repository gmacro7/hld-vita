// The source bounds and cell order are the same as in the placement
// builtins. Returning true only means absence has been proven.
bool hldFlameVentHasNoProbeCandidate(Runner* runner, Instance* caller) {
    SpatialGrid* grid = runner->spatialGrid;
    const InstanceBBox bounds = Collision_computeBBox(runner, caller);
    if (!bounds.valid) return true;
    if (!isfinite(bounds.left) || !isfinite(bounds.top) ||
        !isfinite(bounds.right) || !isfinite(bounds.bottom)) return false;
    const SpatialGridRange range = SpatialGrid_computeCellRange(grid, bounds.left, bounds.top, bounds.right, bounds.bottom);
    for (int32_t gx = range.minGridX; gx <= range.maxGridX; gx++) {
        for (int32_t gy = range.minGridY; gy <= range.maxGridY; gy++) {
            const int32_t cellIndex = SpatialGrid_cellIndex(grid, gx, gy);
            Instance** cell = grid->grid[cellIndex];
            if (arrlen(cell) == 0) continue;
            Instance** candidates = placementCellTargets(runner, cell, cellIndex, FLAME_VENT_PROBE_TARGETS);
            repeat(arrlen(candidates), i) {
                Instance* other = candidates[i];
                if (!other->active || other == caller) continue;
                const InstanceBBox target = Collision_computeBBox(runner, other);
                if (!target.valid || bounds.left >= target.right || target.left >= bounds.right ||
                    bounds.top >= target.bottom || target.top >= bounds.bottom) continue;
                return false;
            }
        }
    }
    return true;
}
