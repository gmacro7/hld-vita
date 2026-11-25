"""Keep a cell's filtered candidate list when the objects that changed the cell cannot be in it.

placementCellTargets caches, per (cell, target), the cell's instances that match target in cell order. Any cell
change bumped the cell's revision and dropped every list for it, so each moving enemy forced rebuilds (a scan of
every instance in the cell, scenery included) for all targets probed there. A cell changes only by removing one
instance (order of the rest kept) or appending one; neither alters the filtered list unless that instance matches
the target. Each change records the instance's object; a stale list is kept when none of the recorded objects
since it was built matches its target. instance_change (the only objectIndex change) drops all lists, as a removal
recorded under the new object could otherwise miss the old one. HLD_NO_CELL_RETAIN=1 restores plain rebuilds.
"""

from runtime_verification_source import replace_once

CHANGES = 16


def with_cell_retention_grid(code):
    code = replace_once(code, "void hldPlacementCellChanged(SpatialGrid* grid, int32_t index);",
                        "void hldPlacementCellChanged(SpatialGrid* grid, int32_t index, const Instance* instance);")
    code = replace_once(code, "hldPlacementCellChanged(grid, cellIndex);", "hldPlacementCellChanged(grid, cellIndex, instance);")
    return replace_once(code, "hldPlacementCellChanged(grid, index);", "hldPlacementCellChanged(grid, index, instance);")


def with_cell_retention_builtins(code):
    import os
    if os.environ.get("HLD_VERIFY_CELLS_VITA"):
        code = "#ifndef HLD_VERIFY_PLACEMENT_CELLS\n#define HLD_VERIFY_PLACEMENT_CELLS\n#endif\n" + code
    import os
    code = replace_once(code, "static uint32_t* placementCellRevisions;", f"""static uint32_t* placementCellRevisions;
#define HLD_CELL_RETAIN_DEFAULT {0 if os.environ.get("HLD_CELL_RETAIN_OFF") else 1}
enum {{ PLACEMENT_CELL_CHANGES = {CHANGES} }};
static int32_t* placementCellChanges;  // per cell, the object of each of the last changes, by revision
static int placementCellRetain = -1;
static unsigned placementCellKept, placementCellRebuilt;""")
    code = replace_once(code, "    free(placementCellRevisions);", "    free(placementCellChanges);\n    placementCellChanges = nullptr;\n    free(placementCellRevisions);")
    code = replace_once(code, "void hldPlacementCellChanged(SpatialGrid* grid, int32_t index) {",
                        "void hldPlacementCellChanged(SpatialGrid* grid, int32_t index, const Instance* instance) {")
    anchor = "    if (++placementCellRevisions[index] == 0) invalidatePlacementCells();"
    code = replace_once(code, anchor, anchor + """
    if (placementCellChanges != nullptr)
        placementCellChanges[(size_t)index * PLACEMENT_CELL_CHANGES + placementCellRevisions[index] % PLACEMENT_CELL_CHANGES] =
            instance != nullptr ? instance->objectIndex : -1;""")
    anchor = "        placementCellRevisions = (uint32_t*)safeCalloc(cellCount, sizeof(*placementCellRevisions));"
    code = replace_once(code, anchor, anchor + """
        if (placementCellRetain < 0) placementCellRetain = HLD_CELL_RETAIN_DEFAULT && getenv("HLD_NO_CELL_RETAIN") == nullptr;
        if (placementCellRetain) placementCellChanges = (int32_t*)safeMalloc(cellCount * PLACEMENT_CELL_CHANGES * sizeof(*placementCellChanges));""")
    # Object -> matches(target) answers per target (the object hierarchy is fixed for the data file): 0 unknown,
    # 1 no, 2 yes. Used by both the retention check and the rebuild scan in place of the parent-chain walk.
    anchor = "static Instance** placementCellTargets(Runner* runner, Instance** instances, int32_t cell, int32_t target) {"
    code = replace_once(code, anchor, """enum { PLACEMENT_MATCH_TARGETS = 64 };
static int32_t placementMatchTarget[PLACEMENT_MATCH_TARGETS];
static uint8_t* placementMatchTable[PLACEMENT_MATCH_TARGETS];
static DataWin* placementMatchData;
static uint32_t placementMatchObjects;
static inline bool placementCellMatchesFast(DataWin* data, int32_t object, int32_t target) {
    if (data != placementMatchData) {
        repeat(PLACEMENT_MATCH_TARGETS, i) { free(placementMatchTable[i]); placementMatchTable[i] = nullptr; }
        placementMatchData = data;
        placementMatchObjects = data->objt.count;
    }
    if (object < 0 || (uint32_t)object >= placementMatchObjects) return placementCellObjectMatches(data, object, target, false);
    unsigned slot = ((uint32_t)target * 0x9e3779b9u) >> 26;
    for (unsigned probe = 0; probe < PLACEMENT_MATCH_TARGETS; ++probe, slot = (slot + 1) & (PLACEMENT_MATCH_TARGETS - 1)) {
        if (placementMatchTable[slot] == nullptr) {
            placementMatchTable[slot] = (uint8_t*)safeCalloc(placementMatchObjects, 1);
            placementMatchTarget[slot] = target;
        }
        if (placementMatchTarget[slot] != target) continue;
        uint8_t* value = &placementMatchTable[slot][object];
        if (*value == 0) *value = placementCellObjectMatches(data, object, target, false) ? 2 : 1;
        return *value == 2;
    }
    return placementCellObjectMatches(data, object, target, false);
}

""" + anchor)
    code = replace_once(code, "            const bool matches = placementCellObjectMatches(runner->dataWin, inst->objectIndex, target, false);",
                        "            const bool matches = placementCellMatchesFast(runner->dataWin, inst->objectIndex, target);")
    anchor = "    if (!entry->valid || entry->cell != cell || entry->target != target || entry->revision != revision) {"
    code = replace_once(code, anchor, """    if (entry->valid && entry->cell == cell && entry->target == target && entry->revision != revision &&
        placementCellChanges != nullptr && revision - entry->revision <= PLACEMENT_CELL_CHANGES) {
        bool kept = true;
        for (uint32_t r = entry->revision + 1; r != revision + 1; ++r) {
            const int32_t object = placementCellChanges[(size_t)cell * PLACEMENT_CELL_CHANGES + r % PLACEMENT_CELL_CHANGES];
            if (object < 0 || placementCellMatchesFast(runner->dataWin, object, target)) { kept = false; break; }
        }
        if (kept) { entry->revision = revision; ++placementCellKept; } else ++placementCellRebuilt;
    }
""" + anchor)
    anchor = "        resetPlacementAncestors(ctx->dataWin);\n        invalidatePlacementCells();"
    code = replace_once(code, anchor, anchor + "\n        placementMatchData = nullptr;  // object_set_parent: rebuild the match tables")
    code = replace_once(code, "static RValue builtin_instance_change(", "static void invalidatePlacementCells(void);\n\nstatic RValue builtin_instance_change(")
    return replace_once(code, "    inst->objectIndex = objectIndex;", "    invalidatePlacementCells();\n    inst->objectIndex = objectIndex;")
