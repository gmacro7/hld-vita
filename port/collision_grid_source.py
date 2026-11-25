"""Index large collision lists by axis while preserving their event order."""

from collision_bounds_source import with_collision_bounds_blocks
from pathlib import Path


def with_collision_grid(code, compare=False, use_existing_blocks=False, alternating=False, cells=8, quantized=False,
                        cell_grid=False):
    if not use_existing_blocks:
        code = with_collision_bounds_blocks(code)
    assert cells >= 2 and (not alternating or compare)
    assert not (quantized and cell_grid)

    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    anchor = "    CollisionBoundsBlock* blocks;"
    replace(anchor, anchor + '''
    uint64_t gridEpoch;
    uint32_t* gridBits;
    uint32_t* queryBits;''')
    if quantized:
        anchor = "typedef struct CollisionTargetSnapshot {"
        replace(anchor, "typedef struct CollisionQuantizedPack CollisionQuantizedPack;\n\n" + anchor)
        replace("    uint32_t* gridBits;", "    CollisionQuantizedPack* quantizedBoxes;")
    if cell_grid:
        replace("    uint32_t* gridBits;", "    uint32_t* gridBits;\n    uint32_t* gridNodes;")

    helpers = r'''
enum { COLLISION_AXIS_CELLS = 8, COLLISION_GRID_MIN = 128, COLLISION_GRID_MAX = 4096 };

static unsigned collisionGridCell(GMLReal value, GMLReal scale) {
    const GMLReal cell = value * scale;
    if (cell <= 0) return 0;
    if (cell >= COLLISION_AXIS_CELLS - 1) return COLLISION_AXIS_CELLS - 1;
    return (unsigned)cell;
}

static bool collisionFiniteRect(const InstanceBBox* bounds) {
    return isfinite(bounds->left) && isfinite(bounds->right) &&
        isfinite(bounds->top) && isfinite(bounds->bottom) &&
        bounds->left <= bounds->right && bounds->top <= bounds->bottom;
}

static const uint32_t* findCollisionGridCandidates(Runner* runner, int32_t target,
        int32_t base, int32_t count, const InstanceBBox* bounds) {
    if (count < COLLISION_GRID_MIN || count > COLLISION_GRID_MAX || target < 0 ||
        (uint32_t)target >= runner->dataWin->objt.count || bounds == nullptr ||
        !collisionFiniteRect(bounds) || !runner->currentRoom->width || !runner->currentRoom->height)
        return nullptr;

    const GMLReal xScale = (GMLReal)COLLISION_AXIS_CELLS / runner->currentRoom->width;
    const GMLReal yScale = (GMLReal)COLLISION_AXIS_CELLS / runner->currentRoom->height;
    const unsigned xStart = collisionGridCell(bounds->left, xScale);
    const unsigned xEnd = collisionGridCell(bounds->right, xScale);
    const unsigned yStart = collisionGridCell(bounds->top, yScale);
    const unsigned yEnd = collisionGridCell(bounds->bottom, yScale);
    if (xEnd - xStart == COLLISION_AXIS_CELLS - 1 && yEnd - yStart == COLLISION_AXIS_CELLS - 1)
        return nullptr;

#ifdef HLD_VERIFY_COLLISION_GRID
    static bool loggedVerification;
    if (!loggedVerification) {
        logInfo("Collision grid verification active, target count=%d\n", count);
        loggedVerification = true;
    }
#endif

    CollisionTargetSnapshot* entry = &runner->collisionTargets[target];
    const unsigned words = ((unsigned)count + 31) / 32;
    if (entry->gridEpoch != runner->collisionDispatchEpoch) {
        const unsigned size = 2 * COLLISION_AXIS_CELLS * words;
        arrsetlen(entry->gridBits, size);
        arrsetlen(entry->queryBits, words);
        memset(entry->gridBits, 0, size * sizeof(*entry->gridBits));
        for (int32_t i = 0; i < count; ++i) {
            Instance* instance = runner->instanceSnapshots[base + i];
            if (!instance->active) continue;
            const InstanceBBox* box = collisionDispatchBounds(runner, instance);
            if (box == nullptr) continue;
            unsigned firstX = 0, lastX = COLLISION_AXIS_CELLS - 1;
            unsigned firstY = 0, lastY = COLLISION_AXIS_CELLS - 1;
            if (collisionFiniteRect(box)) {
                firstX = collisionGridCell(box->left, xScale);
                lastX = collisionGridCell(box->right, xScale);
                firstY = collisionGridCell(box->top, yScale);
                lastY = collisionGridCell(box->bottom, yScale);
            }
            const unsigned word = (unsigned)i / 32;
            const uint32_t bit = UINT32_C(1) << ((unsigned)i % 32);
            for (unsigned x = firstX; x <= lastX; ++x)
                entry->gridBits[x * words + word] |= bit;
            for (unsigned y = firstY; y <= lastY; ++y)
                entry->gridBits[(COLLISION_AXIS_CELLS + y) * words + word] |= bit;
        }
        entry->gridEpoch = runner->collisionDispatchEpoch;
        COUNT_GRID_BUILD();
    }

    // Each rectangle occupies the Cartesian product of its x and y cells.
    // The two axis masks give the same candidate set as a two-dimensional grid.
    for (unsigned word = 0; word < words; ++word) {
        uint32_t xBits = entry->gridBits[xStart * words + word];
        for (unsigned x = xStart + 1; x <= xEnd; ++x)
            xBits |= entry->gridBits[x * words + word];
        uint32_t yBits = entry->gridBits[(COLLISION_AXIS_CELLS + yStart) * words + word];
        for (unsigned y = yStart + 1; y <= yEnd; ++y)
            yBits |= entry->gridBits[(COLLISION_AXIS_CELLS + y) * words + word];
        entry->queryBits[word] = xBits & yBits;
    }
    COUNT_GRID_QUERY();
    return entry->queryBits;
}

static inline int32_t nextCollisionGridCandidate(const uint32_t* bits, int32_t offset, int32_t count) {
    unsigned index = (unsigned)offset / 32;
    const unsigned last = ((unsigned)count - 1) / 32;
    uint32_t word = bits[index] & (UINT32_MAX << ((unsigned)offset % 32));
    while (word == 0) {
        if (index == last) return count;
        word = bits[++index];
    }
    return (int32_t)(index * 32 + (unsigned)__builtin_ctz(word));
}

'''
    helpers = helpers.replace("COLLISION_AXIS_CELLS = 8", f"COLLISION_AXIS_CELLS = {cells}")
    if quantized:
        query = helpers.index("static inline int32_t nextCollisionGridCandidate")
        helpers = Path(__file__).with_name("quantized-collision.inc").read_text() + "\n" + helpers[query:]
    if cell_grid:
        query = helpers.index("static inline int32_t nextCollisionGridCandidate")
        helpers = Path(__file__).with_name("collision-cells.inc").read_text() + "\n" + helpers[query:]
    if compare:
        prelude = r'''
static unsigned collisionGridBuilds, collisionGridQueries, collisionGridSkips;
'''
        if alternating:
            prelude += r'''
static bool collisionGridEnabled(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}
'''
        else:
            prelude += r'''
static bool gridTrialInitialized;
static bool useCollisionGrid;
static bool collisionGridEnabled(void) {
    if (!gridTrialInitialized) {
        FILE* flag = fopen("ux0:data/hld-vm/collision-axis-grid", "r");
        useCollisionGrid = flag != nullptr;
        if (flag != nullptr) fclose(flag);
        gridTrialInitialized = true;
    }
    return useCollisionGrid;
}
'''
        prelude += r'''
void hldReportCollisionGrid(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita collision grid frame=%d enabled=%d builds=%u queries=%u skipped=%u\n",
            frame, (int)GRID_REPORT_MODE, collisionGridBuilds, collisionGridQueries, collisionGridSkips);
    collisionGridBuilds = collisionGridQueries = collisionGridSkips = 0;
}
'''
        report_mode = "collisionGridEnabled(frame - 1)" if alternating else "useCollisionGrid"
        prelude = prelude.replace("GRID_REPORT_MODE", report_mode)
        helpers = helpers.replace("COUNT_GRID_BUILD();", "++collisionGridBuilds;").replace(
            "COUNT_GRID_QUERY();", "++collisionGridQueries;")
    else:
        prelude = ""
        helpers = helpers.replace("        COUNT_GRID_BUILD();\n", "").replace("    COUNT_GRID_QUERY();\n", "")
    anchor = "static void dispatchCollisionEvents(Runner* runner) {"
    replace(anchor, prelude + helpers + anchor)
    mode = "collisionGridEnabled(runner->frameCount)" if alternating else "collisionGridEnabled()" if compare else "true"
    anchor = "    const bool enableBlocks = true;"
    replace(anchor, anchor + f"\n    const bool enableGrid = {mode};")
    anchor = "                const CollisionBoundsBlock* blocks = enableBlocks && rejectDistantPairs"
    replace(anchor, '''                const uint32_t* grid = enableGrid && rejectDistantPairs
                    ? findCollisionGridCandidates(runner, targetObjIndex, snapBase, snapEnd - snapBase, dispatchSelf)
                    : nullptr;
                const CollisionBoundsBlock* blocks = grid == nullptr && enableBlocks && rejectDistantPairs''')
    anchor = "                for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {\n                    const int32_t offset"
    replace(anchor, '''                for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {
                    if (grid != nullptr && runner->collisionDispatchEpoch == blockEpoch) {
                        const int32_t next = snapBase + nextCollisionGridCandidate(grid, snapIdx - snapBase, snapEnd - snapBase);
#ifdef HLD_VERIFY_COLLISION_GRID
                        for (int32_t check = snapIdx; check < next; ++check) {
                            Instance* skipped = runner->instanceSnapshots[check];
                            if (!skipped->active || skipped == self) continue;
                            const InstanceBBox* otherBounds = collisionDispatchBounds(runner, skipped);
                            require(otherBounds == nullptr || dispatchSelf->left >= otherBounds->right ||
                                    otherBounds->left >= dispatchSelf->right || dispatchSelf->top >= otherBounds->bottom ||
                                    otherBounds->top >= dispatchSelf->bottom);
                        }
#endif
''' + ("                        collisionGridSkips += (unsigned)(next - snapIdx);\n" if compare else "") + '''                        if (next >= snapEnd) break;
                        snapIdx = next;
                    }
                    const int32_t offset''')
    anchor = "            arrfree(runner->collisionTargets[i].blocks);"
    grid_storage = "quantizedBoxes" if quantized else "gridBits"
    replace(anchor, anchor + f'''
            arrfree(runner->collisionTargets[i].{grid_storage});
            arrfree(runner->collisionTargets[i].queryBits);''')
    if cell_grid:
        anchor = "            arrfree(runner->collisionTargets[i].gridBits);"
        replace(anchor, anchor + "\n            arrfree(runner->collisionTargets[i].gridNodes);")
    return code
