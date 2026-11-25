"""Cache descendant-filtered placement candidates in spatial cell order."""

from pathlib import Path
from textwrap import indent

from runtime_verification_source import replace_once


def with_placement_ids(code, *, verify=False, launch_flag=None):
    helper = r'''
static unsigned placementIdQueries;

static bool placeByInstanceId(Runner* runner, Instance* caller, InstanceBBox callerBox,
                              SpatialGridQuery query, int32_t id) {
    if (++placementIdQueries == 100) logInfo("HLD: used 100 direct instance placement queries\n");
    Instance* other = hldLookupInstanceId(runner, id);
    if (other == nullptr || !other->active || other == caller || other->lastCollisionQueryId == query.queryId)
        return false;

    // Check the same grid membership as the original query, including empty
    // membership and clamped cells at the room edges.
    const SpatialGridRange range = query.range;
    repeat(arrlen(other->collisionCells), index) {
        const uint32_t cell = other->collisionCells[index];
        const int32_t x = SpatialGrid_unpackGridX(cell), y = SpatialGrid_unpackGridY(cell);
        if (x < range.minGridX || x > range.maxGridX || y < range.minGridY || y > range.maxGridY) continue;
        const InstanceBBox box = Collision_computeBBox(runner, other);
        if (!box.valid || callerBox.left >= box.right || box.left >= callerBox.right ||
            callerBox.top >= box.bottom || box.top >= callerBox.bottom) return false;
        return Collision_instancesOverlapPrecise(runner, caller, other, callerBox, box);
    }
    return false;
}

'''
    if launch_flag:
        anchor = "static unsigned placementIdQueries;"
        helper = replace_once(helper, anchor, anchor + f'''
extern bool {launch_flag};

void hldReportPlacementIds(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita instance placement frame=%d enabled=%d calls=%u\\n", frame, (int){launch_flag}, placementIdQueries);
    placementIdQueries = 0;
}}
''')
        anchor = '    if (++placementIdQueries == 100) logInfo("HLD: used 100 direct instance placement queries\\n");'
        helper = replace_once(helper, anchor, "    ++placementIdQueries;")
    anchor = "static RValue builtin_place_meeting("
    code = replace_once(code, anchor, helper + anchor)
    for function, target, result, expected in (
        ("place_meeting", "target", "found", "hit"),
        ("instance_place", "targetObjIndex", "resultId", "hit ? targetObjIndex : INSTANCE_NOONE"),
    ):
        start = code.index(f"static RValue builtin_{function}(")
        end = code.index("\n}\n", start) + 3
        body = code[start:end]
        anchor = "        for (int32_t gx = query.range.minGridX;"
        enabled = f"{launch_flag} && " if launch_flag else ""
        check = (f"        const bool directId = {enabled}query.filterByInstanceId && !query.matchAll;\n"
                 f"        const bool hit = directId && placeByInstanceId(runner, caller, callerBBox, query, {target});\n")
        if verify:
            body = replace_once(body, anchor, check + anchor)
            anchor = "\n    }\n\n"
            body = replace_once(body, anchor, f"\n        if (directId) require({result} == ({expected}));" + anchor)
        else:
            loop_start = body.index(anchor)
            loop_end = body.index("\n    }\n\n", loop_start)
            body = (body[:loop_start] + check + f"        if (directId) {{\n            {result} = {expected};\n"
                    "        } else {\n" + indent(body[loop_start:loop_end], "    ") + "\n        }" + body[loop_end:])
        code = code[:start] + body + code[end:]
    return code


def with_placement_work_inventory(code):
    anchor = "static RValue builtin_place_meeting("
    start = code.index(anchor)
    end = code.index("\n}\n", start) + 3
    body = code[start:end]
    inserts = {
        "    // ALWAYS SYNC THE GRID BEFORE CHANGING THE INSTANCE POSITION TO AVOID \"SYNCING\" THE TEST POSITION!": (
            "    PlacementWork* work = measurePlacement(runner, caller, target, testX, testY);"
        ),
        "                    InstanceBBox otherBBox = Collision_computeBBox(runner, other);": (
            "                    if (work != nullptr) ++work->bounds;"
        ),
        "                    if (Collision_instancesOverlapPrecise(runner, caller, other, callerBBox, otherBBox)) {": (
            "                    if (work != nullptr) ++work->precise;"
        ),
        "    return RValue_makeBool(found);": "    if (work != nullptr) work->hits += found;",
    }
    for anchor, addition in inserts.items():
        body = replace_once(body, anchor, addition + "\n" + anchor)
    anchor = "                repeat(cellLen, ci) {"
    body = replace_once(body, anchor, anchor + "\n                    if (work != nullptr) ++work->candidates;")
    support = r'''
typedef struct {
    unsigned calls, neighbors, candidates, bounds, precise, hits;
} PlacementWork;

static PlacementWork* placementWork;
static bool placementWorkReported;

static PlacementWork* measurePlacement(Runner* runner, const Instance* self, int32_t target,
                                       GMLReal x, GMLReal y) {
    const int frame = runner->frameCount;
    if (frame < 540 || placementWorkReported) return nullptr;
    const unsigned objects = runner->dataWin->objt.count;
    if (frame >= 720) {
        if (placementWork == nullptr) return nullptr;
        for (unsigned i = 0; i <= objects; ++i) {
            const PlacementWork* work = &placementWork[i];
            if (work->calls == 0) continue;
            const char* name = i < objects ? runner->dataWin->objt.objects[i].name : "<special>";
            logInfo("HLD placement work target=%s calls=%u adjacent=%u candidates=%u bounds=%u precise=%u hits=%u\n",
                    name, work->calls, work->neighbors, work->candidates, work->bounds, work->precise, work->hits);
        }
        free(placementWork);
        placementWork = nullptr;
        placementWorkReported = true;
        return nullptr;
    }
    if (placementWork == nullptr) placementWork = safeCalloc((size_t)objects + 1, sizeof(*placementWork));
    PlacementWork* work = &placementWork[(unsigned)target < objects ? (unsigned)target : objects];
    ++work->calls;
    static int previousFrame = -1;
    static uint32_t previousActor;
    static int32_t previousTarget;
    static GMLReal previousX, previousY;
    if (frame == previousFrame && self->instanceId == previousActor && target == previousTarget &&
        (GMLReal_fabs(x - previousX) + GMLReal_fabs(y - previousY)) <= 1.0) ++work->neighbors;
    previousFrame = frame;
    previousActor = self->instanceId;
    previousTarget = target;
    previousX = x;
    previousY = y;
    return work;
}
'''
    return code[:start] + support + body + code[end:]


def with_placement_cell_grid(code):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    anchor = '#include "utils.h"'
    replace(anchor, anchor + "\n\nvoid hldPlacementGridReset(SpatialGrid* grid);\n"
            "void hldPlacementCellChanged(SpatialGrid* grid, int32_t index);")
    anchor = "void SpatialGrid_free(SpatialGrid* grid) {"
    replace(anchor, anchor + "\n    hldPlacementGridReset(grid);")
    anchor = "                arrdel(grid->grid[cellIndex], j);"
    replace(anchor, anchor + "\n                hldPlacementCellChanged(grid, cellIndex);")
    replace("                arrput(grid->grid[SpatialGrid_cellIndex(grid, gx, gy)], instance);",
            "                const int32_t index = SpatialGrid_cellIndex(grid, gx, gy);\n"
            "                arrput(grid->grid[index], instance);\n"
            "                hldPlacementCellChanged(grid, index);")
    # A query stamp only serves to deduplicate within a query. Reset it on
    # wrap so skipping unrelated instances cannot expose stale equal stamps.
    replace("    uint32_t queryId = ++runner->collisionQueryCounter;", r'''    uint32_t queryId = ++runner->collisionQueryCounter;
    if (queryId == 0) {
        repeat(arrlen(runner->instances), i) runner->instances[i]->lastCollisionQueryId = 0;
        queryId = ++runner->collisionQueryCounter;
    }''')
    return code


def with_placement_cell_queries(code, compare=False, point=False):
    def replace(before, after, count=1):
        nonlocal code
        assert code.count(before) == count, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("placement-cell.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_PLACEMENT_CELLS\n" + helper
    anchor = "static RValue builtin_place_meeting("
    replace(anchor, helper + "\n" + anchor)
    anchor = "        resetPlacementAncestors(ctx->dataWin);"
    replace(anchor, anchor + "\n        invalidatePlacementCells();")

    if point:
        return with_point_queries(code, compare)

    cell = ("                Instance** cell = runner->spatialGrid->grid[SpatialGrid_cellIndex(runner->spatialGrid, gx, gy)];\n"
            "                int32_t cellLen = (int32_t) arrlen(cell);")
    indexed = r'''                const int32_t cellIndex = SpatialGrid_cellIndex(runner->spatialGrid, gx, gy);
                Instance** cell = runner->spatialGrid->grid[cellIndex];
                const bool indexed = query.filterByObject && arrlen(cell) >= 8 && usePlacementCells(runner->frameCount);
                if (indexed) cell = placementCellTargets(runner, cell, cellIndex, TARGET);
                int32_t cellLen = (int32_t)arrlen(cell);'''
    for function, target in (("place_meeting", "target"), ("instance_place", "targetObjIndex")):
        start = code.index(f"static RValue builtin_{function}(")
        stop = code.index("\n}\n", start) + 3
        before = code[start:stop]
        assert before.count(cell) == 1, function
        after = before.replace(cell, indexed.replace("TARGET", target))
        check = "if (!query.matchAll && query.filterByObject &&"
        assert after.count(check) == 1, function
        replace(before, after.replace(check, "if (!indexed && !query.matchAll && query.filterByObject &&"))
    return code


def with_point_queries(code, compare):
    invalidate = "        invalidatePlacementCells();"
    assert code.count(invalidate) == 1
    code = code.replace(invalidate, invalidate + "\n        hldReleasePointAncestors(ctx->dataWin);")
    start = code.index("static RValue builtin_position_meeting(")
    end = code.index("\n}\n", start) + 3
    reference = code[start:end].replace("builtin_position_meeting(", "positionMeetingReference(", 1)
    candidate = Path(__file__).with_name("point-query.inc").read_text()
    if not compare:
        candidate = "#define HLD_VERIFY_POINT_QUERIES\n" + candidate
    return code[:start] + reference + "\n\n" + candidate + code[end:]


def with_point_query_cleanup(code):
    anchor = "void VM_free(VMContext* ctx) {"
    assert code.count(anchor) == 1
    return code.replace(anchor, "void hldReleasePointAncestors(const DataWin* data);\n\n" + anchor +
                        "\n    hldReleasePointAncestors(ctx->dataWin);")
