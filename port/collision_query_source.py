"""Reuse ordered candidates, bounds and mask setup in exact collision queries."""
from pathlib import Path
import re


def with_collision_queries(code, compare=False, reject_bounds=False):
    names = ("line", "rectangle", "line_list", "rectangle_list")
    sources = {
        name: re.search(rf"^static RValue builtin_collision_{name}\([^\n]+\) \{{.*?^\}}", code, re.S | re.M).group()
        for name in names
    }

    def replace(text, before, after):
        assert text.count(before) == 1, before
        return text.replace(before, after)

    line = sources["line"]
    rect = sources["rectangle"]
    candidate_list = rect[rect.index("    Instance* singleInstance"):rect.index("    int32_t resultId")]
    line = line.replace("    int32_t resultId = INSTANCE_NOONE;", candidate_list + "    int32_t resultId = INSTANCE_NOONE;")
    line = replace(line, """    int32_t snapBase = Runner_pushInstancesForTarget(runner, targetObjIndex);
    int32_t snapEnd  = (int32_t) arrlen(runner->instanceSnapshots);
    for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {
        Instance* inst = runner->instanceSnapshots[snapIdx];""", """    // No callback in this loop can mutate the instance lists.
    for (int32_t index = 0; index < candidateCount; ++index) {
        Instance* inst = candidates[index];""")
    line = replace(line, """        if (!Collision_lineOverlapsInstance(ctx->runner, inst, lx1, ly1, lx2, ly2)) continue;
        InstanceBBox bbox = Collision_computeBBox(ctx->runner, inst);""", """        const InstanceBBox bbox = Collision_computeBBox(runner, inst);
        if (!bbox.valid) continue;
        Sprite* spr = Collision_getSprite(ctx->dataWin, inst);
        if (!Collision_lineOverlapsBounds(spr, inst, bbox, lx1, ly1, lx2, ly2)) continue;""")
    line = replace(line, "        Sprite* spr = Collision_getSprite(ctx->dataWin, inst);\n        if (spr == nullptr",
                   "        if (spr == nullptr")
    line = replace(line, "        // Recompute dx/dy for the clipped segment",
                   "        const CollisionPointQuery pointQuery = Collision_preparePointQuery(spr, inst);\n\n"
                   "        // Recompute dx/dy for the clipped segment")
    assert line.count("Collision_pointInInstance(spr, inst,") == 2
    line = line.replace("Collision_pointInInstance(spr, inst,", "Collision_testPoint(&pointQuery,")
    line = replace(line, "    Runner_popInstanceSnapshot(runner, snapBase);\n", "")

    def rectangle(text):
        pattern = (r"(?m)^( +)bool found = false;\n"
                   r"(\1int32_t startX[^\n]*\n\1int32_t endX[^\n]*\n"
                   r"\1int32_t startY[^\n]*\n\1int32_t endY[^\n]*\n)"
                   r"\n\1for \(int32_t py = startY;.*?^\1if \(!found\) continue;")
        text, count = re.subn(pattern, r"\2\1if (!collisionRectPixels(spr, inst, startX, startY, endX, endY)) continue;",
                              text, flags=re.S)
        assert count == 1
        return text

    line_list = replace(sources["line_list"], "                        bool found = false;",
                        "                        const CollisionPointQuery pointQuery = Collision_preparePointQuery(spr, inst);\n"
                        "                        bool found = false;")
    line_list = replace(line_list, "Collision_pointInInstance(spr, inst, px, py)",
                        "Collision_testPoint(&pointQuery, px, py)")
    alternatives = dict(line=line, rectangle=rectangle(rect), line_list=line_list,
                        rectangle_list=rectangle(sources["rectangle_list"]))
    include = Path(__file__).with_name("collision-query.inc").read_text()
    if reject_bounds:
        sources = alternatives.copy()
        line = replace(line, "    Instance* self = ctx->currentCallFrame->self;",
                       """    Instance* self = ctx->currentCallFrame->self;
    const bool boundedLine = GMLReal_fabs(lx1) < 1048576 && GMLReal_fabs(lx2) < 1048576 &&
        GMLReal_fabs(ly1) < 1048576 && GMLReal_fabs(ly2) < 1048576;
    const GMLReal minX = lx1 < lx2 ? lx1 : lx2, maxX = lx1 < lx2 ? lx2 : lx1;
    const GMLReal minY = ly1 < ly2 ? ly1 : ly2, maxY = ly1 < ly2 ? ly2 : ly1;""")
        check = "        if (!Collision_lineOverlapsBounds(spr, inst, bbox, lx1, ly1, lx2, ly2)) continue;"
        line = replace(line, check, """        // Leave a whole pixel of separation before rejecting a hit. This
        // preserves the legacy clipping result close to a rounded boundary.
        if (boundedLine && (maxX < bbox.left - 1 || minX > bbox.right + 1 ||
                            maxY < bbox.top - 1 || minY > bbox.bottom + 1) &&
            GMLReal_fabs(bbox.left) < 1048576 && GMLReal_fabs(bbox.right) < 1048576 &&
            GMLReal_fabs(bbox.top) < 1048576 && GMLReal_fabs(bbox.bottom) < 1048576 &&
            !Collision_obbNeedsSAT(spr, inst)) {
#ifdef HLD_VERIFY_COLLISION_QUERIES
            require(!Collision_lineOverlapsBounds(spr, inst, bbox, lx1, ly1, lx2, ly2));
#endif
            ++collisionRejectedLines;
            continue;
        }
""" + check)
        alternatives["line"] = line
    include += "\nstatic unsigned collisionRejectedLines;\n"
    if compare:
        include += r'''
typedef struct {
    uint64_t elapsed;
    unsigned calls;
} CollisionQueryTiming;

static CollisionQueryTiming collisionTimings[5];
static bool collisionQueriesEnabled;
static bool collisionQueriesInitialized;

static bool usePreparedCollisionQueries(void) {
    if (!collisionQueriesInitialized) {
        FILE* flag = fopen("ux0:data/hld-vm/prepared-collisions", "r");
        collisionQueriesEnabled = flag != nullptr;
        if (flag != nullptr) fclose(flag);
        collisionQueriesInitialized = true;
    }
    return collisionQueriesEnabled;
}

void hldReportCollisionQueries(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita collision queries frame=%d prepared=%d line=%u/%.3f rect=%u/%.3f line_list=%u/%.3f rect_list=%u/%.3f packed_rects=%u rejected=%u circle=%u/%.3f\n",
            frame, (int)collisionQueriesEnabled,
            collisionTimings[0].calls, collisionTimings[0].elapsed / 90000000.0,
            collisionTimings[1].calls, collisionTimings[1].elapsed / 90000000.0,
            collisionTimings[2].calls, collisionTimings[2].elapsed / 90000000.0,
            collisionTimings[3].calls, collisionTimings[3].elapsed / 90000000.0,
            collisionPackedRectangles, collisionRejectedLines,
            collisionTimings[4].calls, collisionTimings[4].elapsed / 90000000.0);
    memset(collisionTimings, 0, sizeof(collisionTimings));
    collisionPackedRectangles = 0;
    collisionRejectedLines = 0;
}

'''
    for index, name in enumerate(names):
        original = re.search(rf"^static RValue builtin_collision_{name}\([^\n]+\) \{{.*?^\}}", code, re.S | re.M).group()
        candidate = alternatives[name]
        if compare:
            entry = f"builtin_collision_{name}"
            original_impl = sources[name].replace(entry + "(", entry + "_original(", 1)
            candidate = candidate.replace(entry + "(", entry + "_prepared(", 1)
            candidate = original_impl + "\n\n" + candidate + f"""

static RValue {entry}(VMContext* ctx, RValue* args, int32_t argCount) {{
    const uint64_t start = nowNanos();
    const bool prepared = usePreparedCollisionQueries();
    const RValue result = prepared ? {entry}_prepared(ctx, args, argCount) : {entry}_original(ctx, args, argCount);
    ++collisionTimings[{index}].calls;
    collisionTimings[{index}].elapsed += nowNanos() - start;
    return result;
}}
"""
        code = replace(code, original, (include + "\n" if index == 0 else "") + candidate)
    if compare and reject_bounds:
        # Count the circle probes used by dash safety and enemy line-of-sight.
        # Both runs execute the same circle code.
        original = re.search(r"^static RValue builtin_collision_circle\([^\n]+\) \{.*?^\}", code, re.S | re.M).group()
        profiled = original.replace("builtin_collision_circle(", "builtin_collision_circle_unprofiled(", 1)
        code = replace(code, original, profiled + r'''

static RValue builtin_collision_circle(VMContext* ctx, RValue* args, int32_t argCount) {
    const uint64_t start = nowNanos();
    const RValue result = builtin_collision_circle_unprofiled(ctx, args, argCount);
    ++collisionTimings[4].calls;
    collisionTimings[4].elapsed += nowNanos() - start;
    return result;
}
''')
    return code
