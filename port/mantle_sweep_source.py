"""Prove that a normal movement path has no terrain contact."""

from runtime_verification_source import replace_once


SWEEP_QUERY = r'''
static bool sweepFinite(InstanceBBox bounds) {
    return bounds.valid && GMLReal_fabs(bounds.left) < 1000000 && GMLReal_fabs(bounds.right) < 1000000 &&
        GMLReal_fabs(bounds.top) < 1000000 && GMLReal_fabs(bounds.bottom) < 1000000;
}

static InstanceBBox sweepUnion(InstanceBBox a, InstanceBBox b) {
    return (InstanceBBox){
        .left = GMLReal_fmin(a.left, b.left), .right = GMLReal_fmax(a.right, b.right),
        .top = GMLReal_fmin(a.top, b.top), .bottom = GMLReal_fmax(a.bottom, b.bottom), .valid = true,
    };
}

static bool sweepBoundsTouch(InstanceBBox swept, InstanceBBox other) {
    return swept.left < other.right && other.left < swept.right &&
        swept.top < other.bottom && other.top < swept.bottom;
}

typedef struct {
    Instance* instance;
    InstanceBBox bounds;
} MantleTerrain;

static bool mantleFindSupport(Runner* runner, Instance* inst, GMLReal x, GMLReal y) {
    // The original collision_point call uses the 408 object list with
    // precise masks enabled. It needs only the presence of a hit here.
    Instance** platforms = runner->instancesByObject[408];
    repeat(arrlen(platforms), i) {
        Instance* platform = platforms[i];
        if (!platform->active || platform == inst || !Collision_pointInsideInstanceBox(runner, platform, x, y))
            continue;
        Sprite* sprite = Collision_getSprite(runner->dataWin, platform);
        if (!Collision_hasFrameMasks(sprite) || Collision_pointInInstance(sprite, platform, x, y)) return true;
    }
    return false;
}

static bool mantleAxisClear(Runner* runner, Instance* inst, const MantleTerrain* terrain, size_t count,
                            float x, float y, float end, bool vertical, bool needsSupport) {
    const float initial = vertical ? y : x;
    const int step = (end > initial) - (end < initial);
    for (float position = initial; position != end; position += step) {
        inst->x = vertical ? x : position + step;
        inst->y = vertical ? position + step : y;
        if (count != 0) {
            const InstanceBBox bounds = Collision_computeBBox(runner, inst);
            for (size_t i = 0; i < count; ++i) {
                if (sweepBoundsTouch(bounds, terrain[i].bounds) &&
                    Collision_instancesOverlapPrecise(runner, inst, terrain[i].instance, bounds, terrain[i].bounds))
                    return false;
            }
        }
        if (needsSupport && !mantleFindSupport(runner, inst,
                vertical ? x : position + 8 * step, vertical ? position + 8 * step : y)) return false;
    }
    return true;
}

bool hldMantleSweepClear(Runner* runner, Instance* inst, float endX, float endY, bool needsSupport) {
    SpatialGrid* grid = runner->spatialGrid;
    if (grid == nullptr || runner->instancesByObject == nullptr || runner->dataWin->objt.count <= 408) return false;
    SpatialGrid_syncGrid(runner, grid);
    const float x = inst->x, y = inst->y;
    const InstanceBBox start = Collision_computeBBox(runner, inst);
    inst->x = endX;
    const InstanceBBox middle = Collision_computeBBox(runner, inst);
    inst->y = endY;
    const InstanceBBox end = Collision_computeBBox(runner, inst);
    inst->x = x;
    inst->y = y;
    if (!sweepFinite(start) || !sweepFinite(middle) || !sweepFinite(end)) return false;

    // Each edge is a monotone translation along its axis, including the
    // engine's collision rounding. The two rectangles cover every probe.
    const InstanceBBox horizontal = sweepUnion(start, middle);
    const InstanceBBox vertical = sweepUnion(middle, end);
    const InstanceBBox bounds = sweepUnion(horizontal, vertical);
    const SpatialGridRange range = SpatialGrid_computeCellRange(
        grid, bounds.left, bounds.top, bounds.right, bounds.bottom);
    MantleTerrain terrain[24];
    size_t count = 0;
    for (int32_t gx = range.minGridX; gx <= range.maxGridX; ++gx) {
        for (int32_t gy = range.minGridY; gy <= range.maxGridY; ++gy) {
            const int32_t index = SpatialGrid_cellIndex(grid, gx, gy);
            Instance** cell = grid->grid[index];
            if (arrlen(cell) == 0) continue;
            Instance** candidates = placementCellTargets(runner, cell, index, 401);
            repeat(arrlen(candidates), i) {
                Instance* other = candidates[i];
                if (!other->active || other == inst) continue;
                const InstanceBBox otherBounds = Collision_computeBBox(runner, other);
                if (!otherBounds.valid) continue;
                if (!sweepFinite(otherBounds)) return false;
                if (!(endX != x && sweepBoundsTouch(horizontal, otherBounds)) &&
                    !(endY != y && sweepBoundsTouch(vertical, otherBounds))) continue;
                size_t prior = 0;
                while (prior < count && terrain[prior].instance != other) ++prior;
                if (prior < count) continue;
                if (count == sizeof(terrain) / sizeof(*terrain)) return false;
                terrain[count++] = (MantleTerrain){other, otherBounds};
            }
        }
    }
    if (count == 0 && !needsSupport) return true;
    // Grooved room masks can fill the broadphase box. Reuse the same
    // candidates for each original sample and keep the precise tests.
    const bool clear = mantleAxisClear(runner, inst, terrain, count, x, y, endX, false, needsSupport) &&
        mantleAxisClear(runner, inst, terrain, count, endX, y, endY, true, needsSupport);
    inst->x = x;
    inst->y = y;
    return clear;
}

'''


def with_mantle_sweep_queries(code):
    anchor = "static RValue builtin_place_meeting("
    return replace_once(code, anchor, SWEEP_QUERY + anchor)
