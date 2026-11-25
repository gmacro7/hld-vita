"""Reuse empty-cell proofs for the existing grouped collision probes."""

from runtime_verification_source import replace_once


STORAGE = r'''
static uint8_t* placementProbeCells;
static uint8_t* placementProbeObjects;
static size_t placementProbeCellCount, placementProbeObjectCount;
'''

PROBES = r'''
enum {
    PROBE_FLAME = 1u << 0,
    PROBE_SHADOW = 1u << 1,
    PROBE_MINE = 1u << 2,
    PROBE_KNOWN = 1u << 7,
};
static const uint8_t placementProbeGroups = HLD_PROBE_GROUPS;
static unsigned placementProbeChecks[3], placementProbeMisses[3];
static unsigned placementProbeCellsBuilt, placementProbeClassesBuilt;

static uint8_t placementProbeGroup(int32_t target) {
    if (target == FLAME_VENT_PROBE_TARGETS) return PROBE_FLAME;
    if (target == PHASE_MINE_PROBE_TARGETS) return PROBE_MINE;
    SHADOW_GROUP
    return 0;
}

static uint8_t placementProbeClassMask(DataWin* archive, int32_t object) {
    // Unknown object indices always use the normal list filter.
    if ((uint32_t)object >= placementProbeObjectCount) return placementProbeGroups | PROBE_KNOWN;
    uint8_t* value = &placementProbeObjects[object];
    if ((*value & PROBE_KNOWN) == 0) {
        uint8_t mask = PROBE_KNOWN;
        if (flameVentProbeObject(archive, object, false)) mask |= PROBE_FLAME;
        if (phaseMineProbeObject(archive, object, false)) mask |= PROBE_MINE;
        SHADOW_CLASS
        *value = mask;
        ++placementProbeClassesBuilt;
    }
    return *value;
}

static bool placementProbeCellEmpty(Runner* runner, Instance** instances, int32_t cell, uint8_t group) {
    if (placementProbeCells == nullptr) {
        SpatialGrid* grid = runner->spatialGrid;
        placementProbeCellCount = (size_t)grid->gridWidth * grid->gridHeight;
        placementProbeObjectCount = runner->dataWin->objt.count;
        placementProbeCells = safeCalloc(placementProbeCellCount, sizeof(*placementProbeCells));
        placementProbeObjects = safeCalloc(placementProbeObjectCount, sizeof(*placementProbeObjects));
    }
    const unsigned ordinal = (unsigned)__builtin_ctz((unsigned)group);
    ++placementProbeChecks[ordinal];
    uint8_t mask = placementProbeCells[cell];
    if ((mask & PROBE_KNOWN) == 0) {
        mask = PROBE_KNOWN;
        repeat(arrlen(instances), i) {
            mask |= placementProbeClassMask(runner->dataWin, instances[i]->objectIndex);
            if ((mask & placementProbeGroups) == placementProbeGroups) break;
        }
        placementProbeCells[cell] = mask;
        ++placementProbeCellsBuilt;
    }
    if ((mask & group) != 0) return false;
    ++placementProbeMisses[ordinal];
    return true;
}

void hldReportPlacementProbeMasks(int frame) {
    REPORT_GUARD
    logInfo("HLD placement probe masks frame=%d flame=%u/%u shadow=%u/%u mine=%u/%u cells=%u classes=%u\n",
            frame, placementProbeMisses[0], placementProbeChecks[0], placementProbeMisses[1], placementProbeChecks[1],
            placementProbeMisses[2], placementProbeChecks[2], placementProbeCellsBuilt, placementProbeClassesBuilt);
    memset(placementProbeChecks, 0, sizeof(placementProbeChecks));
    memset(placementProbeMisses, 0, sizeof(placementProbeMisses));
    placementProbeCellsBuilt = placementProbeClassesBuilt = 0;
}
'''

CHECK_SIMPLE = r'''
        repeat(arrlen(instances), i) {
            const int32_t object = instances[i]->objectIndex;
            const bool matches = target == PHASE_MINE_PROBE_TARGETS
                ? phaseMineProbeObject(runner->dataWin, object, true)
                : flameVentProbeObject(runner->dataWin, object, true);
            require(!matches);
        }
'''

CHECK_ALL = r'''
        repeat(arrlen(instances), i) {
            require(!placementCellObjectMatches(runner->dataWin, instances[i]->objectIndex, target, true));
        }
'''


def with_placement_probe_masks(code, *, checked=False, windowed_report=False, mine_only=False):
    grid = "static SpatialGrid* cachedPlacementGrid;"
    code = replace_once(code, grid, grid + "\n" + STORAGE)
    invalidation = "static void invalidatePlacementCells(void) {"
    code = replace_once(code, invalidation, invalidation + r'''
    if (placementProbeCells != nullptr) {
        memset(placementProbeCells, 0, placementProbeCellCount * sizeof(*placementProbeCells));
        memset(placementProbeObjects, 0, placementProbeObjectCount * sizeof(*placementProbeObjects));
    }''')
    reset = "    free(placementCellRevisions);"
    code = replace_once(code, reset, r'''    free(placementProbeObjects);
    free(placementProbeCells);
    placementProbeObjects = placementProbeCells = nullptr;
''' + reset)
    change = "    if (++placementCellRevisions[index] == 0) invalidatePlacementCells();"
    code = replace_once(code, change, "    if (placementProbeCells != nullptr) placementProbeCells[index] = 0;\n" + change)
    full_filters = "static bool placementCellObjectMatches(" in code
    shadow = not mine_only and "static bool shadowProbeObject(" in code
    groups = ["PROBE_MINE"] if mine_only else ["PROBE_FLAME", "PROBE_MINE"]
    if shadow:
        groups.append("PROBE_SHADOW")
    helper = PROBES.replace("HLD_PROBE_GROUPS", " | ".join(groups))
    helper = helper.replace("SHADOW_GROUP", "if (target == ENEMY_SHADOW_TARGETS) return PROBE_SHADOW;" if shadow else "")
    helper = helper.replace("SHADOW_CLASS", "if (shadowProbeObject(archive, object, false)) mask |= PROBE_SHADOW;" if shadow else "")
    if mine_only:
        helper = replace_once(helper, "    if (target == FLAME_VENT_PROBE_TARGETS) return PROBE_FLAME;\n", "")
        helper = replace_once(helper, "        if (flameVentProbeObject(archive, object, false)) mask |= PROBE_FLAME;\n", "")
    guard = "if (frame <= 0 || frame % 90 != 0) return;" if windowed_report else ""
    helper = helper.replace("REPORT_GUARD", guard)
    lookup = "static Instance** placementCellTargets(Runner* runner, Instance** instances, int32_t cell, int32_t target) {"
    code = replace_once(code, lookup, helper + "\n" + lookup)
    anchor = "    const uint32_t hash = (uint32_t)cell * 0x9e3779b9u ^ (uint32_t)target * 0x85ebca6bu;"
    check = (CHECK_ALL if full_filters else CHECK_SIMPLE) if checked else ""
    code = replace_once(code, anchor, r'''    const uint8_t group = placementProbeGroup(target);
    if (group != 0 && placementProbeCellEmpty(runner, instances, cell, group)) {
PROBE_CHECK
        return nullptr;
    }
'''.replace("PROBE_CHECK", check) + anchor)
    return code
