// The archive's object ancestry is static between explicit parent writes.
// Build target-major rows so a point query can filter a small cell without
// hashing object pairs or walking a parent chain for each candidate.
static DataWin* pointAncestorArchive;
static uint8_t* pointAncestorBits;
static size_t pointAncestorStride;

static void releasePointAncestors(void) {
    free(pointAncestorBits);
    pointAncestorBits = nullptr;
    pointAncestorArchive = nullptr;
    pointAncestorStride = 0;
}

void hldReleasePointAncestors(const DataWin* data) {
    if (pointAncestorArchive == data) releasePointAncestors();
}

static const uint8_t* pointAncestorRow(DataWin* data, int32_t target) {
    const uint32_t count = data->objt.count;
    if (target < 0 || (uint32_t)target >= count || count > 4096) return nullptr;
    if (pointAncestorArchive != data) {
        releasePointAncestors();
        pointAncestorStride = (count + 7u) / 8u;
        pointAncestorBits = (uint8_t*)safeCalloc(count, pointAncestorStride);
        for (uint32_t object = 0; object < count; ++object) {
            int32_t ancestor = (int32_t)object;
            uint8_t bit = (uint8_t)(1u << (object & 7u));
            for (unsigned depth = 0; depth < 32 && ancestor >= 0 && (uint32_t)ancestor < count; ++depth) {
                pointAncestorBits[(size_t)ancestor * pointAncestorStride + object / 8u] |= bit;
                ancestor = data->objt.objects[ancestor].parentId;
            }
        }
        pointAncestorArchive = data;
    }
    return pointAncestorBits + (size_t)target * pointAncestorStride;
}

static RValue positionMeetingIndexed(VMContext* ctx, RValue* args, int32_t argCount) {
    REQUIRE_ARGC_AT_LEAST("position_meeting", 3, RValue_makeBool(false));
    Runner* runner = ctx->runner;
    GMLReal px = RValue_toReal(args[0]);
    GMLReal py = RValue_toReal(args[1]);
    const int32_t target = VM_resolveInstanceTarget(ctx, RValue_toInt32(args[2]));
    if (target == INSTANCE_NOONE) return RValue_makeBool(false);
    if (runner->collisionCompatibilityMode) {
        px = compatRoundCoord(px);
        py = compatRoundCoord(py);
    }

    SpatialGrid* grid = runner->spatialGrid;
    SpatialGrid_syncGrid(runner, grid);
    const SpatialGridQuery query = SpatialGrid_prepareQuery(runner, px, py, px, py, target);
    const int32_t cellIndex = SpatialGrid_cellIndex(grid, query.range.minGridX, query.range.minGridY);
    Instance** cell = grid->grid[cellIndex];
    const bool indexed = query.filterByObject && arrlen(cell) >= 8;
    if (indexed) cell = placementCellTargets(runner, cell, cellIndex, target);
    const uint8_t* objects = !indexed && query.filterByObject && arrlen(cell) != 0
        ? pointAncestorRow(runner->dataWin, target) : nullptr;
    const uint32_t objectCount = runner->dataWin->objt.count;
    repeat(arrlen(cell), i) {
        Instance* other = cell[i];
        if (!other->active || other->lastCollisionQueryId == query.queryId) continue;
        other->lastCollisionQueryId = query.queryId;
        if (!indexed && query.filterByObject) {
            const uint32_t object = (uint32_t)other->objectIndex;
            const bool match = objects != nullptr
                ? object < objectCount && (objects[object / 8u] & (1u << (object & 7u))) != 0
                : VM_isObjectOrDescendant(runner->dataWin, other->objectIndex, target);
#ifdef HLD_VERIFY_POINT_QUERIES
            require(match == VM_isObjectOrDescendant(runner->dataWin, other->objectIndex, target));
#endif
            if (!match) continue;
        }
        if (query.filterByInstanceId && other->instanceId != (uint32_t)target) continue;
        if (!Collision_pointInsideInstanceBox(runner, other, px, py)) continue;
        Sprite* sprite = Collision_getSprite(runner->dataWin, other);
        if (Collision_hasFrameMasks(sprite) && !Collision_pointInInstance(sprite, other, px, py)) continue;
        return RValue_makeBool(true);
    }
    return RValue_makeBool(false);
}

static RValue builtin_position_meeting(VMContext* ctx, RValue* args, int32_t argCount) {
    if (ctx->runner == nullptr || !usePlacementCells(ctx->runner->frameCount))
        return positionMeetingReference(ctx, args, argCount);
#ifdef HLD_VERIFY_POINT_QUERIES
    // Each call has its own query stamp. Running the reference first leaves
    // both the membership and the exact ordered candidates unchanged.
    const RValue expected = positionMeetingReference(ctx, args, argCount);
    const RValue candidate = positionMeetingIndexed(ctx, args, argCount);
    require(expected.type == candidate.type && expected.int32 == candidate.int32);
    return candidate;
#else
    return positionMeetingIndexed(ctx, args, argCount);
#endif
}
