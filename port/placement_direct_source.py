"""place_meeting / instance_place with a specific instance id as the target test that one instance directly.

The engine handled an instance-id target by scanning every instance in the grid cells under the caller's box and
skipping all but that id (East Lab fight: ~24 calls a frame, ~420 instances scanned each). Only that instance can
match, so it is looked up and given the same tests the scan applies to it: active, in the collision grid (not
destroyed, has cells), not the caller, box overlap, then the precise test. Results are hashed (hldPlaceResultHash)
in both this and the baseline build (HLDC_PLACE_DIRECT=0) so the Mac comparison can show they never differ."""

import os

from runtime_verification_source import replace_once

DIRECT = ("        if (target >= INSTANCE_ID_BASE) { Instance* other = hldLookupInstanceId(runner, (uint32_t) target); "
          "if (other != nullptr && other->active && !other->destroyed && other != caller && arrlen(other->collisionCells) > 0) { "
          "InstanceBBox otherBBox = Collision_computeBBox(runner, other); "
          "if (otherBBox.valid && !(callerBBox.left >= otherBBox.right || otherBBox.left >= callerBBox.right || "
          "callerBBox.top >= otherBBox.bottom || otherBBox.top >= callerBBox.bottom) && "
          "Collision_instancesOverlapPrecise(runner, caller, other, callerBBox, otherBBox)) {HIT} } } else ")


def _patch(code, signature, target_name, hit, result_expr):
    start = code.index(signature)
    end = code.index("\n}\n", start)
    body = code[start:end]
    if os.environ.get("HLDC_PLACE_DIRECT", "1") == "1":
        anchor = (f"    if (callerBBox.valid) {{\n        SpatialGridQuery query = SpatialGrid_prepareQuery(runner, callerBBox.left, "
                  f"callerBBox.top, callerBBox.right, callerBBox.bottom, {target_name});")
        direct = DIRECT.replace("target", target_name).replace("{HIT}", hit)
        body = replace_once(body, anchor, "    if (callerBBox.valid) {\n" + direct +
                            f"{{        SpatialGridQuery query = SpatialGrid_prepareQuery(runner, callerBBox.left, callerBBox.top, "
                            f"callerBBox.right, callerBBox.bottom, {target_name});")
        # close the extra brace before the end of the `if (callerBBox.valid)` block: it ends right before restoring position
        restore = "    // Restore original position\n" if "    // Restore original position\n" in body else "    caller->x = savedX;\n"
        idx = body.index(restore)
        close = body.rindex("    }\n", 0, idx)
        body = body[:close] + "    } }\n" + body[close + len("    }\n"):]
    ret = f"    return {result_expr};"
    assert body.count(ret) == 1, ret
    body = body.replace(ret, "    { extern uint64_t hldPlaceResultHash; hldPlaceResultHash = hldPlaceResultHash * 1000003u + "
                             f"(uint64_t) (int64_t) ({hit.split('=')[0].strip()}); }} " + ret.strip())
    return code[:start] + body + code[end:]


PROFILE_HEADER = r"""
// HLDC_PLACE_PROFILE diagnostic: where place_meeting time goes, by target and caller object.
#define HP_KEYS 2048
static uint64_t hpTotal[HP_KEYS], hpSyncT[HP_KEYS], hpPreciseT[HP_KEYS]; static uint32_t hpCalls[HP_KEYS], hpCand[HP_KEYS], hpPreciseN[HP_KEYS];
static uint64_t hpCallerT[HP_KEYS]; static uint32_t hpCallerN[HP_KEYS]; static int hpLastFrame; static uint64_t hpPreciseAcc; static uint32_t hpPreciseCount, hpCandCount;
static void hpReport(Runner* runner) {
    const int frame = runner->frameCount;
    if (frame / 300 == hpLastFrame / 300) return;
    hpLastFrame = frame;
    for (int pass = 0; pass < 2; ++pass) for (int rank = 0; rank < 10; ++rank) {
        int best = -1; uint64_t most = 0;
        for (int i = 0; i < HP_KEYS; ++i) { const uint64_t v = pass ? hpCallerT[i] : hpTotal[i]; if (v > most) { most = v; best = i; } }
        if (best < 0) break;
        const char* name = best == 0 ? "<instance>" : (uint32_t) (best - 1) < runner->dataWin->objt.count ? runner->dataWin->objt.objects[best - 1].name : "?";
        if (pass == 0) { logInfo("HLDPLACE frame=%d target=%s calls=%.1f us=%.1f sync_us=%.1f precise_us=%.1f precise_n=%.1f cand=%.1f\n", frame, name,
            hpCalls[best] / 300.0, hpTotal[best] / 300e3, hpSyncT[best] / 300e3, hpPreciseT[best] / 300e3, hpPreciseN[best] / 300.0, hpCand[best] / 300.0); hpTotal[best] = 0; }
        else { logInfo("HLDPLACE frame=%d caller=%s calls=%.1f us=%.1f\n", frame, name, hpCallerN[best] / 300.0, hpCallerT[best] / 300e3); hpCallerT[best] = 0; }
    }
    memset(hpTotal, 0, sizeof hpTotal); memset(hpSyncT, 0, sizeof hpSyncT); memset(hpPreciseT, 0, sizeof hpPreciseT); memset(hpCalls, 0, sizeof hpCalls);
    memset(hpCand, 0, sizeof hpCand); memset(hpPreciseN, 0, sizeof hpPreciseN); memset(hpCallerT, 0, sizeof hpCallerT); memset(hpCallerN, 0, sizeof hpCallerN);
}
static inline bool hpPrecise(Runner* runner, Instance* a, Instance* b, InstanceBBox ab, InstanceBBox bb) {
    const uint64_t t = nowNanos(); const bool r = Collision_instancesOverlapPrecise(runner, a, b, ab, bb); hpPreciseAcc += nowNanos() - t; ++hpPreciseCount; return r;
}
"""


def _profile(code):
    start = code.index("static RValue builtin_place_meeting(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    body = replace_once(body, "    if (target == INSTANCE_NOONE) return RValue_makeBool(false);\n",
        "    if (target == INSTANCE_NOONE) return RValue_makeBool(false);\n    const uint64_t hpT0 = nowNanos(); hpPreciseAcc = 0; hpPreciseCount = 0; hpCandCount = 0;\n"
        "    const int hpKey = target >= INSTANCE_ID_BASE ? 0 : (target >= 0 && target < HP_KEYS - 1 ? target + 1 : HP_KEYS - 1);\n")
    body = replace_once(body, "    SpatialGrid_syncGrid(runner, runner->spatialGrid);\n",
        "    { const uint64_t hpS = nowNanos(); SpatialGrid_syncGrid(runner, runner->spatialGrid); hpSyncT[hpKey] += nowNanos() - hpS; }\n")
    body = body.replace("Collision_instancesOverlapPrecise(runner, caller, other, callerBBox, otherBBox)", "hpPrecise(runner, caller, other, callerBBox, otherBBox)")
    body = replace_once(body, "                    InstanceBBox otherBBox = Collision_computeBBox(runner, other);\n                    if (!otherBBox.valid) continue;\n",
        "                    InstanceBBox otherBBox = Collision_computeBBox(runner, other); ++hpCandCount;\n                    if (!otherBBox.valid) continue;\n")
    ret = "return RValue_makeBool(found);"
    body = body.replace(ret, "{ const uint64_t hpSpent = nowNanos() - hpT0; hpTotal[hpKey] += hpSpent; ++hpCalls[hpKey]; hpPreciseT[hpKey] += hpPreciseAcc; "
        "hpPreciseN[hpKey] += hpPreciseCount; hpCand[hpKey] += hpCandCount; const int hpC = caller->objectIndex >= 0 && caller->objectIndex < HP_KEYS - 1 ? caller->objectIndex + 1 : HP_KEYS - 1; "
        "hpCallerT[hpC] += hpSpent; ++hpCallerN[hpC]; hpReport(runner); } " + ret)
    return code[:start] + PROFILE_HEADER + body + code[end:]


CELLS_RECT = r"""

// ===[ Placement through line cells ]===
// Ordinals of the bucket's list whose box (as of the last cell build) touches [l,r]x[t,b], plus everything moved since.
const uint32_t* hldLineCellVisitRect(const LineChunks* cache, double l, double t, double r, double b, size_t* outCount) {
    const unsigned bucket = (unsigned) (cache - lineChunkBuckets);
    if (bucket >= LINE_CHUNK_BUCKETS) return nullptr;
    HldLineCells* cells = &hldLineCells[bucket];
    if (!cells->valid || cells->cache != cache || cells->source != cache->source || cells->count != cache->count) {
        if (!cache->valid || hldLineCellsDisabled()) return nullptr;
        hldLineCellBuild((LineChunks*) cache, bucket, (Runner*) cache->runner);
        if (!cells->valid) return nullptr;
    }
    if (!(fabs(l) < 1048576 && fabs(r) < 1048576 && fabs(t) < 1048576 && fabs(b) < 1048576) || l > r || t > b) return nullptr;
    if (++cells->query == 0) { memset(cells->stamp, 0, cells->count * sizeof(uint32_t)); cells->query = 1; }
    const uint32_t query = cells->query;
    size_t n = 0;
    int32_t c0 = (int32_t) floor((l - 2.0 - cells->originX) / HLD_CELL), c1 = (int32_t) floor((r + 2.0 - cells->originX) / HLD_CELL);
    int32_t r0 = (int32_t) floor((t - 2.0 - cells->originY) / HLD_CELL), r1 = (int32_t) floor((b + 2.0 - cells->originY) / HLD_CELL);
    if (c0 < 0) c0 = 0;
    if (r0 < 0) r0 = 0;
    if (c1 >= cells->cols) c1 = cells->cols - 1;
    if (r1 >= cells->rows) r1 = cells->rows - 1;
    for (int32_t row = r0; row <= r1; ++row)
        for (int32_t c = c0; c <= c1; ++c) {
            const size_t cell = (size_t) row * cells->cols + c;
            for (uint32_t k = cells->cellStart[cell]; k < cells->cellStart[cell + 1]; ++k) {
                const uint32_t ordinal = cells->cellItems[k];
                if (cells->stamp[ordinal] == query) continue;
                cells->stamp[ordinal] = query;
                cells->visit[n++] = ordinal;
            }
        }
    for (uint32_t k = 0; k < cells->alwaysCount; ++k) {
        const uint32_t ordinal = cells->always[k];
        if (cells->stamp[ordinal] != query) { cells->stamp[ordinal] = query; cells->visit[n++] = ordinal; }
    }
    for (uint32_t k = 0; k < cells->dirtyCount; ++k) {
        const uint32_t ordinal = cells->dirty[k];
        if (cells->stamp[ordinal] != query) { cells->stamp[ordinal] = query; cells->visit[n++] = ordinal; }
    }
    ++cells->uses;
    cells->visited += (unsigned) n;
    *outCount = n;
    return cells->visit;
}
"""

# place_meeting(x, y, obj) for a line-cell object (block, PathFindObstacle, DecorXRayBlock): the grid cells under the
# caller hold ~165 matching walls each in big rooms. The 64 px line cells give a superset of the walls whose box can
# overlap; each is kept only if the grid query would have visited it (listed in a grid cell of the query range), then
# gets the unchanged tests. place_meeting only reports whether any instance hit, so visiting order does not matter.
CELLS_LOOP = (
    "        const uint32_t* hpcVisit = nullptr; size_t hpcCount = 0; LineChunks* hpcChunks = nullptr;\n"
    "        if (query.filterByObject && !query.filterByInstanceId && !query.matchAll && (uint32_t) target < runner->dataWin->objt.count && hldPlaceCellsOn()) {\n"
    "            Instance** hpcList = runner->instancesByObject[target];\n"
    "            hpcChunks = getLineChunks(runner, hpcList, arrlenu(hpcList), target);\n"
    "            if (hpcChunks != nullptr) hpcVisit = hldLineCellVisitRect(hpcChunks, callerBBox.left, callerBBox.top, callerBBox.right, callerBBox.bottom, &hpcCount);\n"
    "        }\n"
    "        if (hpcVisit != nullptr) {\n"
    "            for (size_t k = 0; k < hpcCount && !found; ++k) {\n"
    "                Instance* other = hpcChunks->source[hpcVisit[k]];\n"
    "                if (!other->active || other == caller) continue;\n"
    "                if (!placementObjectMatches(runner->dataWin, other->objectIndex, target)) continue;\n"
    "                bool listed = false;\n"
    "                repeat(arrlen(other->collisionCells), c) {\n"
    "                    const int32_t cx = SpatialGrid_unpackGridX(other->collisionCells[c]), cy = SpatialGrid_unpackGridY(other->collisionCells[c]);\n"
    "                    if (cx >= query.range.minGridX && cx <= query.range.maxGridX && cy >= query.range.minGridY && cy <= query.range.maxGridY) { listed = true; break; }\n"
    "                }\n"
    "                if (!listed) continue;\n"
    "                other->lastCollisionQueryId = query.queryId;\n"
    "                InstanceBBox otherBBox = Collision_computeBBox(runner, other);\n"
    "                if (!otherBBox.valid) continue;\n"
    "                if (callerBBox.left >= otherBBox.right || otherBBox.left >= callerBBox.right ||\n"
    "                    callerBBox.top >= otherBBox.bottom || otherBBox.top >= callerBBox.bottom) continue;\n"
    "                if (Collision_instancesOverlapPrecise(runner, caller, other, callerBBox, otherBBox)) found = true;\n"
    "            }\n"
    "        } else\n")


def _cells(code):
    start = code.index("static RValue builtin_place_meeting(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    anchor = ("callerBBox.right, callerBBox.bottom, target);\n\n"
              "        for (int32_t gx = query.range.minGridX; query.range.maxGridX >= gx && !found; gx++) {")
    body = replace_once(body, anchor, anchor.replace("\n        for (int32_t gx", "\n" + CELLS_LOOP + "        for (int32_t gx"))
    proto = ("const uint32_t* hldLineCellVisitRect(const LineChunks* cache, double l, double t, double r, double b, size_t* outCount);\n"
             "int hldPlaceCellsForceOff;\nstatic bool hldPlaceCellsOn(void) { static int on = -1; if (on < 0) on = getenv(\"HLD_NO_PLACE_CELLS\") == nullptr; return on && !hldPlaceCellsForceOff; }\n")
    return code[:start] + proto + body + code[end:] + CELLS_RECT


def _position_cells(code):
    """position_meeting(x, y, obj) scanned every instance of the one grid cell under the point, testing ancestry each
    time (East Lab: ~200 us a call). Use the per-cell target lists place_meeting already keeps (same instances, same
    order) and the cached ancestry test (same answer)."""
    start = code.index("static RValue builtin_position_meeting(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    body = replace_once(body,
        "            Instance** cell = runner->spatialGrid->grid[SpatialGrid_cellIndex(runner->spatialGrid, gx, gy)];\n",
        "            const int32_t hpmIndex = SpatialGrid_cellIndex(runner->spatialGrid, gx, gy);\n"
        "            Instance** cell = runner->spatialGrid->grid[hpmIndex];\n"
        "            const bool hpmIndexed = hldPlaceCellsOn() && !query.matchAll && query.filterByObject && arrlen(cell) >= 8 && usePlacementCells(runner->frameCount);\n"
        "            if (hpmIndexed) cell = placementCellTargets(runner, cell, hpmIndex, target);\n")
    body = replace_once(body,
        "if (!query.matchAll && query.filterByObject && !VM_isObjectOrDescendant(runner->dataWin, other->objectIndex, target)) continue;",
        "if (!hpmIndexed && !query.matchAll && query.filterByObject && !(hldPlaceCellsOn() ? placementObjectMatches(runner->dataWin, other->objectIndex, target) : VM_isObjectOrDescendant(runner->dataWin, other->objectIndex, target))) continue;")
    body = replace_once(body, "    return RValue_makeBool(found);",
        "    { extern uint64_t hldPlaceResultHash; hldPlaceResultHash = hldPlaceResultHash * 1000003u + 7u + (uint64_t) found; } return RValue_makeBool(found);")
    return code[:start] + body + code[end:]


def _circle_cells(code):
    """collision_circle(x, y, r, obj, prec, notme) for a line-cell object (block): the dash-over-gap assist makes up
    to ~6,000 of these in one frame, each scanning the grid cells under the circle (hundreds of walls). The line
    cells give a superset of the walls whose box can touch the circle box. Each candidate the grid query would have
    visited gets the unchanged test; the result is the hit the grid traversal meets first: smallest (first grid
    cell of the query range holding it, in gx-then-gy order; its index in that cell's list)."""
    start = code.index("static RValue builtin_collision_circle(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    test_start = body.index("                if (!Collision_circleOverlapsInstance(ctx->runner, inst, cx, cy, radius)) continue;\n")
    test_end = body.index("                resultId = inst->instanceId;\n                break;\n")
    test = body[test_start:test_end]
    anchor = "    SpatialGridQuery query = SpatialGrid_prepareQuery(runner, qx1, qy1, qx2, qy2, targetObjIndex);\n"
    fast = (anchor +
        "    if (hldPlaceCellsOn() && query.filterByObject && !query.matchAll && (uint32_t) targetObjIndex < runner->dataWin->objt.count) {\n"
        "        Instance** hccList = runner->instancesByObject[targetObjIndex];\n"
        "        LineChunks* hccChunks = getLineChunks(runner, hccList, arrlenu(hccList), targetObjIndex);\n"
        "        size_t hccCount = 0;\n"
        "        const uint32_t* hccVisit = hccChunks != nullptr ? hldLineCellVisitRect(hccChunks, qx1, qy1, qx2, qy2, &hccCount) : nullptr;\n"
        "        if (hccVisit != nullptr) {\n"
        "            const int64_t hccRows = (int64_t) query.range.maxGridY - query.range.minGridY + 1;\n"
        "            Instance* hccBest = nullptr; int64_t hccBestCell = INT64_MAX; int64_t hccBestIndex = INT64_MAX;\n"
        "            for (size_t hccK = 0; hccK < hccCount; ++hccK) {\n"
        "                Instance* inst = hccChunks->source[hccVisit[hccK]];\n"
        "                if (!inst->active) continue;\n"
        "                if (notme && inst == self) continue;\n"
        "                if (!placementObjectMatches(runner->dataWin, inst->objectIndex, targetObjIndex)) continue;\n"
        "                int64_t hccCell = INT64_MAX; int32_t hccGX = 0, hccGY = 0;\n"
        "                repeat(arrlen(inst->collisionCells), hccC) {\n"
        "                    const int32_t gx = SpatialGrid_unpackGridX(inst->collisionCells[hccC]), gy = SpatialGrid_unpackGridY(inst->collisionCells[hccC]);\n"
        "                    if (gx < query.range.minGridX || gx > query.range.maxGridX || gy < query.range.minGridY || gy > query.range.maxGridY) continue;\n"
        "                    const int64_t order = (int64_t) (gx - query.range.minGridX) * hccRows + (gy - query.range.minGridY);\n"
        "                    if (order < hccCell) { hccCell = order; hccGX = gx; hccGY = gy; }\n"
        "                }\n"
        "                if (hccCell == INT64_MAX || hccCell > hccBestCell) continue;\n"
        + test +
        "                {\n"
        "                    Instance** hccGridCell = runner->spatialGrid->grid[SpatialGrid_cellIndex(runner->spatialGrid, hccGX, hccGY)];\n"
        "                    int64_t hccIndex = INT64_MAX;\n"
        "                    repeat(arrlen(hccGridCell), hccI) if (hccGridCell[hccI] == inst) { hccIndex = hccI; break; }\n"
        "                    if (hccCell < hccBestCell || hccIndex < hccBestIndex) { hccBest = inst; hccBestCell = hccCell; hccBestIndex = hccIndex; }\n"
        "                }\n"
        "            }\n"
        "            const int32_t hccResult = hccBest != nullptr ? (int32_t) hccBest->instanceId : INSTANCE_NOONE;\n"
        "            { extern uint64_t hldPlaceResultHash; hldPlaceResultHash = hldPlaceResultHash * 1000003u + 11u + (uint64_t) (int64_t) hccResult; }\n"
        "            return RValue_makeReal((GMLReal) hccResult);\n"
        "        }\n"
        "    }\n")
    body = replace_once(body, anchor, fast)
    body = replace_once(body, '    Runner* runner = ctx->runner;\n', '    Runner* runner = ctx->runner;\n    { void hldPlaceSelfTest(VMContext*); static int hldSelfTestOn = -1; if (hldSelfTestOn < 0) hldSelfTestOn = getenv("HLD_PLACE_SELFTEST") != nullptr; if (hldSelfTestOn) hldPlaceSelfTest(ctx); }\n')
    body = replace_once(body, "    return RValue_makeReal((GMLReal) resultId);",
        "    { extern uint64_t hldPlaceResultHash; hldPlaceResultHash = hldPlaceResultHash * 1000003u + 11u + (uint64_t) (int64_t) resultId; } return RValue_makeReal((GMLReal) resultId);")
    return code[:start] + body + code[end:]


SELFTEST = r"""

// ===[ HLD_PLACE_SELFTEST=frame: compare the cell paths with the grid paths over the whole room, once ]===
void hldPlaceSelfTest(VMContext* ctx) {
    static int done;
    Runner* runner = ctx->runner;
    static int at = -2;
    if (at == -2) { const char* v = getenv("HLD_PLACE_SELFTEST"); at = v != nullptr ? atoi(v) : -1; }
    if (done || at < 0 || runner->frameCount < at || ctx->currentCallFrame == nullptr || ctx->currentCallFrame->self == nullptr) return;
    done = 1;
    const char* names[] = {"block", "SafePlatform", "PathFindObstacle", "DecorXRayBlock"};
    int32_t objects[4];
    for (int i = 0; i < 4; ++i) { objects[i] = -1; for (uint32_t o = 0; o < runner->dataWin->objt.count; ++o) if (runner->dataWin->objt.objects[o].name && strcmp(runner->dataWin->objt.objects[o].name, names[i]) == 0) objects[i] = (int32_t) o; }
    const double w = runner->currentRoom->width, h = runner->currentRoom->height;
    unsigned tests = 0, mismatches = 0, hits = 0, iplaceHits = 0;
    static const double radii[] = {4.0, 9.0, 16.0, 22.0, 40.0};
    for (double y = -8; y < h + 8; y += 5.5) for (double x = -8; x < w + 8; x += 5.5) {
        for (int o = 0; o < 4; ++o) {
            if (objects[o] < 0) continue;
            const double r = radii[tests % 5];
            const int prec = (tests / 5) % 2, notme = (tests / 10) % 2;
            RValue circle[6] = {RValue_makeReal(x), RValue_makeReal(y), RValue_makeReal(r), RValue_makeReal(objects[o]), RValue_makeReal(prec), RValue_makeReal(notme)};
            RValue point[3] = {RValue_makeReal(x), RValue_makeReal(y), RValue_makeReal(objects[o])};
            hldPlaceCellsForceOff = 0;
            const RValue c1 = builtin_collision_circle(ctx, circle, 6), p1 = builtin_position_meeting(ctx, point, 3), m1 = builtin_place_meeting(ctx, point, 3); const RValue i1 = builtin_instance_place(ctx, point, 3); RValue cp[5] = {RValue_makeReal(x), RValue_makeReal(y), RValue_makeReal(objects[o]), RValue_makeReal(prec), RValue_makeReal(notme)}; const RValue q1 = builtin_collision_point(ctx, cp, 5);
            hldPlaceCellsForceOff = 1;
            const RValue c2 = builtin_collision_circle(ctx, circle, 6), p2 = builtin_position_meeting(ctx, point, 3), m2 = builtin_place_meeting(ctx, point, 3); const RValue i2 = builtin_instance_place(ctx, point, 3); const RValue q2 = builtin_collision_point(ctx, cp, 5);
            hldPlaceCellsForceOff = 0;
            ++tests;
            hits += RValue_toReal(c1) >= 0; iplaceHits += RValue_toReal(i1) >= 0;
            if (RValue_toReal(c1) != RValue_toReal(c2) || RValue_toBool(p1) != RValue_toBool(p2) || RValue_toBool(m1) != RValue_toBool(m2) || RValue_toReal(i1) != RValue_toReal(i2) || RValue_toReal(q1) != RValue_toReal(q2)) {
                if (++mismatches <= 10) logInfo("HLDPLACETEST mismatch obj=%s x=%g y=%g r=%g prec=%d notme=%d circle=%g/%g pos=%d/%d place=%d/%d\\n", names[o], x, y, r, prec, notme,
                    (double) RValue_toReal(c1), (double) RValue_toReal(c2), (int) RValue_toBool(p1), (int) RValue_toBool(p2), (int) RValue_toBool(m1), (int) RValue_toBool(m2));
            }
        }
    }
    logInfo("HLDPLACETEST frame=%d room=%s tests=%u circle_hits=%u iplace_hits=%u mismatches=%u\\n", runner->frameCount, runner->currentRoom->name, tests, hits, iplaceHits, mismatches);
}
"""


IPLACE_LOOP = (
    "        bool hipDone = false;\n"
    "        if (hldPlaceCellsOn() && query.filterByObject && !query.filterByInstanceId && !query.matchAll && (uint32_t) targetObjIndex < runner->dataWin->objt.count) {\n"
    "            Instance** hipList = runner->instancesByObject[targetObjIndex];\n"
    "            LineChunks* hipChunks = getLineChunks(runner, hipList, arrlenu(hipList), targetObjIndex);\n"
    "            size_t hipCount = 0;\n"
    "            const uint32_t* hipVisit = hipChunks != nullptr ? hldLineCellVisitRect(hipChunks, callerBBox.left, callerBBox.top, callerBBox.right, callerBBox.bottom, &hipCount) : nullptr;\n"
    "            if (hipVisit != nullptr) {\n"
    "                hipDone = true;\n"
    "                const int64_t hipRows = (int64_t) query.range.maxGridY - query.range.minGridY + 1;\n"
    "                Instance* hipBest = nullptr; int64_t hipBestCell = INT64_MAX, hipBestIndex = INT64_MAX;\n"
    "                for (size_t hipK = 0; hipK < hipCount; ++hipK) {\n"
    "                    Instance* other = hipChunks->source[hipVisit[hipK]];\n"
    "                    if (!other->active || other == caller) continue;\n"
    "                    if (!placementObjectMatches(runner->dataWin, other->objectIndex, targetObjIndex)) continue;\n"
    "                    int64_t hipCell = INT64_MAX; int32_t hipGX = 0, hipGY = 0;\n"
    "                    repeat(arrlen(other->collisionCells), hipC) {\n"
    "                        const int32_t gx = SpatialGrid_unpackGridX(other->collisionCells[hipC]), gy = SpatialGrid_unpackGridY(other->collisionCells[hipC]);\n"
    "                        if (gx < query.range.minGridX || gx > query.range.maxGridX || gy < query.range.minGridY || gy > query.range.maxGridY) continue;\n"
    "                        const int64_t order = (int64_t) (gx - query.range.minGridX) * hipRows + (gy - query.range.minGridY);\n"
    "                        if (order < hipCell) { hipCell = order; hipGX = gx; hipGY = gy; }\n"
    "                    }\n"
    "                    if (hipCell == INT64_MAX || hipCell > hipBestCell) continue;\n"
    "                    other->lastCollisionQueryId = query.queryId;\n"
    "                    InstanceBBox otherBBox = Collision_computeBBox(runner, other);\n"
    "                    if (!otherBBox.valid) continue;\n"
    "                    if (callerBBox.left >= otherBBox.right || otherBBox.left >= callerBBox.right ||\n"
    "                        callerBBox.top >= otherBBox.bottom || otherBBox.top >= callerBBox.bottom) continue;\n"
    "                    if (!Collision_instancesOverlapPrecise(runner, caller, other, callerBBox, otherBBox)) continue;\n"
    "                    Instance** hipGridCell = runner->spatialGrid->grid[SpatialGrid_cellIndex(runner->spatialGrid, hipGX, hipGY)];\n"
    "                    int64_t hipIndex = INT64_MAX;\n"
    "                    repeat(arrlen(hipGridCell), hipI) if (hipGridCell[hipI] == other) { hipIndex = hipI; break; }\n"
    "                    if (hipCell < hipBestCell || hipIndex < hipBestIndex) { hipBest = other; hipBestCell = hipCell; hipBestIndex = hipIndex; }\n"
    "                }\n"
    "                if (hipBest != nullptr) resultId = (int32_t) hipBest->instanceId;\n"
    "            }\n"
    "        }\n"
    "        if (!hipDone)\n")


def _iplace_cells(code):
    """instance_place(x, y, obj) for a line-cell object: same candidates as place_meeting's cell path; the result is
    the hit the grid traversal meets first (first grid cell of the range holding it, then its index in that cell;
    the per-cell target lists keep the cell's order, so the full-cell index orders them the same way)."""
    start = code.index("static RValue builtin_instance_place(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    anchor = ("callerBBox.right, callerBBox.bottom, targetObjIndex);\n\n"
              "        for (int32_t gx = query.range.minGridX; query.range.maxGridX >= gx && resultId == INSTANCE_NOONE; gx++) {")
    body = replace_once(body, anchor, anchor.replace("\n        for (int32_t gx", "\n" + IPLACE_LOOP + "        for (int32_t gx"))
    return code[:start] + body + code[end:]


def _point_cells(code):
    """collision_point(x, y, obj, prec, notme) scanned the whole object list (every floor piece for SafePlatform,
    ~100 calls a frame from blood, debris and footsteps) and returns the first hit in list order. For a line-cell
    object the cells under the point (ascending ordinals) hold every instance whose box can contain it."""
    names = 'static const char* const lineChunkBucketNames[] = {"block", "PathFindObstacle", "DecorXRayBlock"};'
    if names in code:
        code = replace_once(code, names, names.replace('"DecorXRayBlock"}', '"DecorXRayBlock", "SafePlatform"}'))
    start = code.index("static RValue builtin_collision_point(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    loop = ("    for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {\n"
            "        Instance* inst = hldSource[snapIdx];\n")
    body = replace_once(body, loop,
        "    const uint32_t* hcpVisit = nullptr; size_t hcpCount = 0;\n"
        "    if (hldInPlace && hldPlaceCellsOn() && snapEnd > 0) {\n"
        "        LineChunks* hcpChunks = getLineChunks(runner, hldSource, (size_t) snapEnd, targetObjIndex);\n"
        "        if (hcpChunks != nullptr) hcpVisit = hldLineCellVisitRect(hcpChunks, px, py, px, py, &hcpCount);\n"
        "        if (hcpVisit != nullptr) qsort((void*) hcpVisit, hcpCount, sizeof(uint32_t), hldOrdinalCompare);\n"
        "    }\n"
        "    for (int32_t hcpStep = 0, snapIdx = snapBase; hcpVisit != nullptr ? hcpStep < (int32_t) hcpCount : snapEnd > snapIdx; ++hcpStep, ++snapIdx) {\n"
        "        if (hcpVisit != nullptr) snapIdx = (int32_t) hcpVisit[hcpStep];\n"
        "        Instance* inst = hldSource[snapIdx];\n")
    body = replace_once(body, "    return RValue_makeReal((GMLReal) resultId);",
        "    { extern uint64_t hldPlaceResultHash; hldPlaceResultHash = hldPlaceResultHash * 1000003u + 13u + (uint64_t) (int64_t) resultId; } return RValue_makeReal((GMLReal) resultId);")
    proto = "static int hldOrdinalCompare(const void* a, const void* b);\n"
    return code[:start] + proto + body + code[end:]


def _position_line_cells(code):
    """position_meeting for a line-cell object: the cells under the point; kept only if listed in the grid cell the
    original scans; same tests. The result is a bool, so order does not matter."""
    start = code.index("static RValue builtin_position_meeting(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    anchor = "    bool found = false;\n\n    for (int32_t gx = query.range.minGridX;"
    fast = ("    bool found = false;\n"
        "    const uint32_t* hplVisit = nullptr; size_t hplCount = 0; LineChunks* hplChunks = nullptr;\n"
        "    if (hldPlaceCellsOn() && query.filterByObject && !query.filterByInstanceId && !query.matchAll && (uint32_t) target < runner->dataWin->objt.count) {\n"
        "        Instance** hplList = runner->instancesByObject[target];\n"
        "        hplChunks = getLineChunks(runner, hplList, arrlenu(hplList), target);\n"
        "        if (hplChunks != nullptr) hplVisit = hldLineCellVisitRect(hplChunks, px, py, px, py, &hplCount);\n"
        "    }\n"
        "    for (size_t hplK = 0; hplVisit != nullptr && hplK < hplCount && !found; ++hplK) {\n"
        "        Instance* other = hplChunks->source[hplVisit[hplK]];\n"
        "        if (!other->active) continue;\n"
        "        if (!placementObjectMatches(runner->dataWin, other->objectIndex, target)) continue;\n"
        "        bool listed = false;\n"
        "        repeat(arrlen(other->collisionCells), c) {\n"
        "            const int32_t cx = SpatialGrid_unpackGridX(other->collisionCells[c]), cy = SpatialGrid_unpackGridY(other->collisionCells[c]);\n"
        "            if (cx >= query.range.minGridX && cx <= query.range.maxGridX && cy >= query.range.minGridY && cy <= query.range.maxGridY) { listed = true; break; }\n"
        "        }\n"
        "        if (!listed) continue;\n"
        "        if (!Collision_pointInsideInstanceBox(ctx->runner, other, px, py)) continue;\n"
        "        Sprite* spr = Collision_getSprite(ctx->dataWin, other);\n"
        "        if (Collision_hasFrameMasks(spr) && !Collision_pointInInstance(spr, other, px, py)) continue;\n"
        "        found = true;\n"
        "    }\n"
        "\n    for (int32_t gx = query.range.minGridX; hplVisit == nullptr &&")
    body = replace_once(body, anchor, fast)
    return code[:start] + body + code[end:]


def with_direct_instance_placement(code):
    if "static RValue builtin_place_meeting(VMContext* ctx, RValue* args, int32_t argCount) {" not in code:
        return code
    code = _patch(code, "static RValue builtin_place_meeting(VMContext* ctx, RValue* args, int32_t argCount) {",
                  "target", "found = true;", "RValue_makeBool(found)")
    code = _patch(code, "static RValue builtin_instance_place(VMContext* ctx, RValue* args, int32_t argCount) {",
                  "targetObjIndex", "resultId = other->instanceId;", "RValue_makeReal((GMLReal) resultId)")
    if os.environ.get("HLDC_PLACE_CELLS", "1") == "1" and "static HldLineCells hldLineCells" in code:
        code = _cells(code)
        code = _position_cells(code)
        code = _iplace_cells(code)
    if os.environ.get("HLDC_PLACE_PROFILE") == "1":
        code = _profile(code)
    return code + "\nuint64_t hldPlaceResultHash;\n"


def with_circle_cells(code):
    """Applied after the engine's collision_circle hooks (engine_speedups_source)."""
    if os.environ.get("HLDC_PLACE_CELLS", "1") == "1" and "static HldLineCells hldLineCells" in code and "hldPlaceCellsOn" in code:
        code = _position_line_cells(_point_cells(_circle_cells(code))) + SELFTEST
    return code
