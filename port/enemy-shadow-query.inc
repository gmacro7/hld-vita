static GMLReal shadowProbeDX[ENEMY_SHADOW_POINTS], shadowProbeDY[ENEMY_SHADOW_POINTS];

bool hldBindShadowProbes(VMContext* ctx, uint32_t x, uint32_t y, uint32_t meeting) {
    if (x >= ctx->funcCallCacheCount || y >= ctx->funcCallCacheCount || meeting >= ctx->funcCallCacheCount ||
        ctx->funcCallCache[x].builtin != (void*)builtin_lengthdir_x ||
        ctx->funcCallCache[y].builtin != (void*)builtin_lengthdir_y ||
        ctx->funcCallCache[meeting].builtin != (void*)builtin_position_meeting) return false;

    // Compute the four offsets with the same math builtins and precision as
    // the interpreter, including their near-zero rounding.
    repeat(ENEMY_SHADOW_POINTS, i) {
        RValue xArgs[] = {RValue_makeReal(8.0), RValue_makeReal(i * 90.0)};
        RValue yArgs[] = {RValue_makeReal(4.0), RValue_makeReal(i * 90.0)};
        shadowProbeDX[i] = builtin_lengthdir_x(ctx, xArgs, 2).real;
        shadowProbeDY[i] = builtin_lengthdir_y(ctx, yArgs, 2).real;
    }
    return true;
}

bool hldShadowProbesMiss(Runner* runner, Instance* instance, const RValue* platforms) {
    if (platforms->type != RVALUE_ARRAY || platforms->array == nullptr ||
        GMLArray_length1D(platforms->array) != sizeof(enemyShadowTargets) / sizeof(*enemyShadowTargets)) return false;
    repeat(sizeof(enemyShadowTargets) / sizeof(*enemyShadowTargets), i) {
        const RValue* item = GMLArray_slot(platforms->array, i);
        if (item == nullptr || (item->type != RVALUE_INT32 && item->type != RVALUE_REAL) ||
            RValue_toReal(*item) != enemyShadowTargets[i]) return false;
    }

    SpatialGrid* grid = runner->spatialGrid;
    GMLReal px[ENEMY_SHADOW_POINTS], py[ENEMY_SHADOW_POINTS];
    int32_t cellIndex[ENEMY_SHADOW_POINTS];
    repeat(ENEMY_SHADOW_POINTS, i) {
        px[i] = (GMLReal)instance->x + shadowProbeDX[i];
        py[i] = (GMLReal)instance->y + shadowProbeDY[i];
        if (!isfinite(px[i]) || !isfinite(py[i])) return false;
        if (runner->collisionCompatibilityMode) {
            px[i] = compatRoundCoord(px[i]);
            py[i] = compatRoundCoord(py[i]);
        }
        const SpatialGridRange range = SpatialGrid_computeCellRange(grid, px[i], py[i], px[i], py[i]);
        if (range.minGridX != range.maxGridX || range.minGridY != range.maxGridY) return false;
        cellIndex[i] = SpatialGrid_cellIndex(grid, range.minGridX, range.minGridY);
    }

    SpatialGrid_syncGrid(runner, grid);
    unsigned visited = 0;
    repeat(ENEMY_SHADOW_POINTS, i) {
        if ((visited & (1u << i)) != 0) continue;
        unsigned points = 0;
        for (unsigned j = i; j < ENEMY_SHADOW_POINTS; ++j) {
            if (cellIndex[i] == cellIndex[j]) points |= 1u << j;
        }
        visited |= points;
        Instance** cell = grid->grid[cellIndex[i]];
        if (arrlen(cell) == 0) continue;
        Instance** candidates = placementCellTargets(runner, cell, cellIndex[i], ENEMY_SHADOW_TARGETS);
        repeat(arrlen(candidates), index) {
            Instance* candidate = candidates[index];
            if (!candidate->active) continue;
            for (unsigned remaining = points; remaining != 0; remaining &= remaining - 1) {
                const unsigned probe = (unsigned)__builtin_ctz(remaining);
                if (Collision_pointInsideInstanceBox(runner, candidate, px[probe], py[probe])) return false;
            }
        }
    }
    return true;
}
