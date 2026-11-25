"""Refresh an unchanged grid footprint without removing and reinserting it."""


def with_grid_refresh(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = r'''
static bool refreshGridFootprint(SpatialGrid* grid, Instance* instance, SpatialGridRange range) {
    const int32_t count = (int32_t)arrlen(instance->collisionCells);
    const uint32_t first = SpatialGrid_packGridCoordinates(range.minGridX, range.minGridY);
    const uint32_t last = SpatialGrid_packGridCoordinates(range.maxGridX, range.maxGridY);
    if (count == 0 || (uint32_t)instance->collisionCells[0] != first ||
        (uint32_t)instance->collisionCells[count - 1] != last) return false;

    // Membership is stored in an x-major rectangular traversal. The same
    // endpoints give the same footprint. Reproduce removal and appending by
    // moving just the existing entry to the tail of each cell.
    for (int32_t i = 0; i < count; ++i) {
        const uint32_t coords = instance->collisionCells[i];
        const int32_t index = SpatialGrid_cellIndex(grid, SpatialGrid_unpackGridX(coords),
                                                    SpatialGrid_unpackGridY(coords));
        Instance** cell = grid->grid[index];
        const int32_t tail = (int32_t)arrlen(cell) - 1;
        int32_t at = tail;
        while (at >= 0 && cell[at] != instance) --at;
#ifdef HLD_VERIFY_GRID_REFRESH
        require(at >= 0);
        for (int32_t j = 0; j < at; ++j) require(cell[j] != instance);
#endif
        if (at < 0) return false;
        if (at < tail) {
            memmove(cell + at, cell + at + 1, (size_t)(tail - at) * sizeof(*cell));
            cell[tail] = instance;
        }
    }
    instance->spatialGridDirty = false;
    return true;
}
'''
    if compare:
        replace('#include "collision.h"', '#include "collision.h"\n#include "gettime.h"')
        helper += r'''
static unsigned gridRetainedFootprints;
static unsigned gridSyncCalls;
static uint64_t gridSyncNanos;

static inline bool useGridRefresh(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportGridRefresh(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita grid refresh frame=%d enabled=%d retained=%u syncs=%u sync_ms=%.3f\n",
            frame, (int)useGridRefresh(frame - 1), gridRetainedFootprints, gridSyncCalls,
            (double)gridSyncNanos / 90000000.0);
    gridRetainedFootprints = gridSyncCalls = 0;
    gridSyncNanos = 0;
}
'''
        replace("    if (!requiresResync) return;", "    if (!requiresResync) return;\n"
                "    const uint64_t started = nowNanos();\n    ++gridSyncCalls;")
        replace("    arrsetlen(grid->dirtyInstances, 0);", "    arrsetlen(grid->dirtyInstances, 0);\n"
                "    gridSyncNanos += nowNanos() - started;")
    anchor = "void SpatialGrid_syncGrid(Runner* runner, SpatialGrid* grid) {"
    replace(anchor, helper + "\n" + anchor)
    replace("""        // Remove from old cells
        SpatialGrid_removeInstance(grid, instance);

        InstanceBBox bbox = Collision_computeBBox(runner, instance);

        if (!bbox.valid)
            continue;

        SpatialGridRange range = SpatialGrid_computeCellRange(grid, bbox.left, bbox.top, bbox.right, bbox.bottom);
""", """        InstanceBBox bbox = Collision_computeBBox(runner, instance);
        if (!bbox.valid) {
            SpatialGrid_removeInstance(grid, instance);
            continue;
        }

        SpatialGridRange range = SpatialGrid_computeCellRange(grid, bbox.left, bbox.top, bbox.right, bbox.bottom);
        if (""" + ("useGridRefresh(runner->frameCount) && " if compare else "") + """refreshGridFootprint(grid, instance, range)) {
""" + ("            ++gridRetainedFootprints;\n" if compare else "") + """            continue;
        }

        SpatialGrid_removeInstance(grid, instance);
""")
    return code
