"""Reuse static terrain collision bounds across dispatch epochs."""

from runtime_verification_source import replace_once


TERRAIN_CACHE = r'''
typedef struct {
    const Runner* runner;
    const SpatialGrid* grid;
    int32_t target, count;
    bool wide, compatibility, valid;
    bool* memberClasses;
    uint32_t classCount;
} TerrainDispatchBounds;

static TerrainDispatchBounds terrainDispatchBounds;
static unsigned terrainBoundsBuilds, terrainBoundsReuses, terrainBoundsInvalidations;

static void invalidateTerrainDispatchBounds(void) {
    if (terrainDispatchBounds.valid) {
        terrainDispatchBounds.valid = false;
        ++terrainBoundsInvalidations;
    }
}

static void terrainDispatchMembershipChanged(const Runner* runner, int32_t target) {
    if (terrainDispatchBounds.runner == runner && terrainDispatchBounds.target == target)
        invalidateTerrainDispatchBounds();
}

void hldTerrainBoundsGridFreed(const SpatialGrid* grid) {
    if (terrainDispatchBounds.grid == grid) invalidateTerrainDispatchBounds();
}

void hldTerrainBoundsGeometryChanged(const SpatialGrid* grid, const Instance* instance) {
    const uint32_t object = (uint32_t)instance->objectIndex;
    if (terrainDispatchBounds.valid && terrainDispatchBounds.grid == grid &&
        object < terrainDispatchBounds.classCount && terrainDispatchBounds.memberClasses[object])
        invalidateTerrainDispatchBounds();
}

void hldTerrainBoundsSpritesChanged(const DataWin* data) {
    if (terrainDispatchBounds.runner != nullptr && terrainDispatchBounds.runner->dataWin == data)
        invalidateTerrainDispatchBounds();
}

static bool terrainDispatchTarget(const Runner* runner, int32_t target) {
    if (runner->physics != nullptr) return false;
    if (terrainDispatchBounds.runner != runner) {
        free(terrainDispatchBounds.memberClasses);
        terrainDispatchBounds = (TerrainDispatchBounds){.runner = runner, .target = -1};
        const DataWin* data = runner->dataWin;
        if (data->gen8.gameID == 742741103 && data->gen8.wadVersion == 15) {
            for (uint32_t index = 0; index < data->objt.count; ++index) {
                const char* name = data->objt.objects[index].name;
                if (name != nullptr && strcmp(name, "block") == 0) {
                    terrainDispatchBounds.target = (int32_t)index;
                    break;
                }
            }
        }
    }
    return terrainDispatchBounds.target == target;
}

static void rememberTerrainDispatchBounds(Runner* runner, Instance* const* candidates, int32_t count, bool wide) {
    terrainDispatchBounds.valid = false;
    if (terrainDispatchBounds.memberClasses == nullptr) {
        terrainDispatchBounds.classCount = runner->dataWin->objt.count;
        terrainDispatchBounds.memberClasses = safeCalloc(terrainDispatchBounds.classCount, sizeof(bool));
    } else {
        memset(terrainDispatchBounds.memberClasses, 0, terrainDispatchBounds.classCount * sizeof(bool));
    }
    for (int32_t index = 0; index < count; ++index) {
        const Instance* member = candidates[index];
        const uint32_t object = (uint32_t)member->objectIndex;
        if (object >= terrainDispatchBounds.classCount || member->physicsBody != nullptr) return;
        terrainDispatchBounds.memberClasses[object] = true;
    }
    terrainDispatchBounds.grid = runner->spatialGrid;
    terrainDispatchBounds.count = count;
    terrainDispatchBounds.wide = wide;
    terrainDispatchBounds.compatibility = runner->collisionCompatibilityMode;
    terrainDispatchBounds.valid = true;
    ++terrainBoundsBuilds;
}

void hldReportTerrainDispatchBounds(int frame) {
    REPORT_GUARD
    logInfo("HLD terrain dispatch bounds frame=%d builds=%u reuses=%u invalidations=%u\n",
            frame, terrainBoundsBuilds, terrainBoundsReuses, terrainBoundsInvalidations);
    terrainBoundsBuilds = terrainBoundsReuses = terrainBoundsInvalidations = 0;
}
'''


def with_persistent_terrain_bounds(code, *, checked=False, launch_flag=None):
    anchor = "static inline void invalidateCollisionTargetOrder(Runner* runner, int32_t object) {"
    support = TERRAIN_CACHE.replace("REPORT_GUARD", "if (frame <= 0 || frame % 90 != 0) return;" if launch_flag else "")
    if launch_flag:
        support = f"extern bool {launch_flag};\n" + support
    code = replace_once(code, anchor, support + "\n" + anchor +
                        "\n    terrainDispatchMembershipChanged(runner, object);")
    anchor = "    inst->active = false;"
    code = replace_once(code, anchor, anchor +
                        "\n    hldTerrainBoundsGeometryChanged(runner->spatialGrid, inst);")
    declaration = "void hldTerrainBoundsGeometryChanged(const SpatialGrid*, const Instance*);"
    anchor = "void Runner_destroyInstance(MAYBE_UNUSED Runner* runner, Instance* inst, bool runDestroyEvent) {"
    code = replace_once(code, anchor, declaration + "\n\n" + anchor)
    anchor = "void Runner_free(Runner* runner) {\n    if (runner == nullptr) return;\n"
    code = replace_once(code, anchor, anchor + '''    if (terrainDispatchBounds.runner == runner) {
        free(terrainDispatchBounds.memberClasses);
        terrainDispatchBounds = (TerrainDispatchBounds){0};
    }
''')

    start = code.index("            CollisionBoundsBlock block = {0};")
    end = code.index("            entry->blocks[blockIndex] = block;", start)
    body = "".join(line[8:] + "\n" for line in code[start:end].splitlines())
    code = code[:start] + "            CollisionBoundsBlock block = buildCollisionGroup(runner, candidates, blockIndex);\n" + code[end:]
    anchor = "static const CollisionBoundsBlock* collisionBoundsBlocks(Runner* runner,"
    code = replace_once(code, anchor, """static CollisionBoundsBlock buildCollisionGroup(Runner* runner, Instance* const* candidates,
                                                int32_t blockIndex) {
""" + body + "    return block;\n}\n\n" + anchor)
    check = r'''
        for (int32_t index = 0; index < blockCount; ++index) {
            const CollisionBoundsBlock old = entry->blocks[index];
            const CollisionBoundsBlock now = buildCollisionGroup(runner, candidates, index);
            require(old.unbounded == now.unbounded && old.hasBounds == now.hasBounds);
            require(!old.hasBounds || (old.left == now.left && old.right == now.right &&
                                       old.top == now.top && old.bottom == now.bottom));
        }
''' if checked else ""
    enabled = f"{launch_flag} && " if launch_flag else ""
    anchor = "    if (entry->blockEpoch != runner->collisionDispatchEpoch) {"
    code = replace_once(code, anchor, f'''    const bool terrainTarget = {enabled}terrainDispatchTarget(runner, target);
    if (terrainTarget && entry->blockEpoch != runner->collisionDispatchEpoch &&
        terrainDispatchBounds.valid && terrainDispatchBounds.grid == runner->spatialGrid &&
        terrainDispatchBounds.count == count && terrainDispatchBounds.wide == wide &&
        terrainDispatchBounds.compatibility == runner->collisionCompatibilityMode) {{
''' + check.lstrip("\n") + '''        ++terrainBoundsReuses;
        entry->blockEpoch = runner->collisionDispatchEpoch;
        return entry->blocks;
    }
''' + anchor)
    anchor = "    return entry->blocks;\n}"
    before_return = "    }\n" + anchor
    return replace_once(code, before_return, '''        if (terrainTarget) rememberTerrainDispatchBounds(runner, candidates, count, wide);
''' + before_return)


def with_terrain_bounds_grid_hooks(code):
    member = "void hldTerrainBoundsGeometryChanged(const SpatialGrid*, const Instance*);"
    for method, instance in (("SpatialGrid_markInstanceAsDirty", "dirtyInstance"), ("SpatialGrid_removeInstance", "instance")):
        anchor = f"void {method}(SpatialGrid* grid, Instance* {instance}) {{"
        code = replace_once(code, anchor, member + "\n\n" + anchor +
                            f"\n    hldTerrainBoundsGeometryChanged(grid, {instance});")
    anchor = "void SpatialGrid_free(SpatialGrid* grid) {"
    return replace_once(code, anchor, "void hldTerrainBoundsGridFreed(const SpatialGrid*);\n\n" +
                        anchor + "\n    hldTerrainBoundsGridFreed(grid);")


def with_terrain_bounds_sprite_hooks(code):
    anchor = "static RValue builtin_sprite_add("
    code = replace_once(code, anchor, "void hldTerrainBoundsSpritesChanged(const DataWin*);\n\n" + anchor)
    for method in ("sprite_add", "sprite_create_from_surface", "sprite_delete", "sprite_collision_mask", "sprite_set_bbox_mode"):
        start = code.index(f"static RValue builtin_{method}(")
        brace = code.index("{", start) + 1
        code = code[:brace] + "\n    hldTerrainBoundsSpritesChanged(ctx->dataWin);" + code[brace:]
    anchor = "static RValue builtin_action_sprite_set("
    code = replace_once(code, anchor, "void hldTerrainBoundsGeometryChanged(const SpatialGrid*, const Instance*);\n\n" + anchor)
    anchor = "    inst->spriteIndex = spriteId;"
    return replace_once(code, anchor, """    if (inst->maskIndex < 0 && inst->spriteIndex != spriteId)
        hldTerrainBoundsGeometryChanged(ctx->runner->spatialGrid, inst);
""" + anchor)
