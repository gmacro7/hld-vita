"""Speed up placement-query filtering while preserving grid and hit order."""

import re


def with_placement_filters(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = r'''
enum { PLACEMENT_ANCESTOR_CACHE_SIZE = 512 };

typedef struct {
    int32_t object;
    int32_t target;
    uint8_t match;
} PlacementAncestor;

static PlacementAncestor placementAncestors[PLACEMENT_ANCESTOR_CACHE_SIZE];
static const DataWin* placementAncestorArchive;

static void resetPlacementAncestors(const DataWin* dataWin) {
    memset(placementAncestors, 0, sizeof(placementAncestors));
    placementAncestorArchive = dataWin;
}

static inline bool placementObjectMatches(DataWin* dataWin, int32_t object, int32_t target) {
    if (placementAncestorArchive != dataWin) resetPlacementAncestors(dataWin);
    const uint32_t hash = (uint32_t)object * 0x9e3779b9u ^ (uint32_t)target * 0x85ebca6bu;
    PlacementAncestor* entry = &placementAncestors[(hash ^ (hash >> 16)) & (PLACEMENT_ANCESTOR_CACHE_SIZE - 1)];
    if (entry->match == 0 || entry->object != object || entry->target != target) {
        const bool match = VM_isObjectOrDescendant(dataWin, object, target);
        *entry = (PlacementAncestor){object, target, match ? 2 : 1};
    }
#ifdef HLD_VERIFY_PLACEMENT_FILTERS
    require((entry->match == 2) == VM_isObjectOrDescendant(dataWin, object, target));
#endif
    return entry->match == 2;
}
'''
    if compare:
        helper = r'''
static unsigned placementRejectedBoxes;
static unsigned placementAncestorMisses;

static inline bool usePlacementFilters(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportPlacementFilters(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita placement filters frame=%d enabled=%d box_rejects=%u ancestor_misses=%u\n",
            frame, (int)usePlacementFilters(frame - 1), placementRejectedBoxes, placementAncestorMisses);
    placementRejectedBoxes = placementAncestorMisses = 0;
}
''' + helper.replace("        const bool match = VM_isObjectOrDescendant", "        ++placementAncestorMisses;\n"
                         "        const bool match = VM_isObjectOrDescendant")

    anchor = "static RValue builtin_place_meeting("
    replace(anchor, helper + "\n" + anchor)
    anchor = "        ctx->dataWin->objt.objects[id].parentId = parentId;"
    replace(anchor, anchor + "\n        resetPlacementAncestors(ctx->dataWin);")
    anchor = "void VMBuiltins_registerAll(VMContext* ctx) {"
    replace(anchor, anchor + "\n    resetPlacementAncestors(ctx->dataWin);")

    mode = "usePlacementFilters(runner->frameCount)" if compare else "true"
    for name, target in (("place_meeting", "target"), ("instance_place", "targetObjIndex")):
        match = re.search(rf"^static RValue builtin_{name}\([^\n]+\) \{{.*?^\}}", code, re.M | re.S)
        before = match[0]
        after = before.replace("    Runner* runner = ctx->runner;", "    Runner* runner = ctx->runner;\n"
                               f"    const bool filterPlacement = {mode};")
        check = f"!VM_isObjectOrDescendant(runner->dataWin, other->objectIndex, {target})"
        assert after.count(check) == 1
        after = after.replace(check, f"\n                        !(filterPlacement ? placementObjectMatches(runner->dataWin, other->objectIndex, {target})\n"
                              f"                                          : VM_isObjectOrDescendant(runner->dataWin, other->objectIndex, {target}))")
        check = "                    if (Collision_instancesOverlapPrecise(runner, caller, other, callerBBox, otherBBox)) {"
        assert after.count(check) == 1
        after = after.replace(check, r'''                    if (filterPlacement &&
                        (callerBBox.left >= otherBBox.right || otherBBox.left >= callerBBox.right ||
                         callerBBox.top >= otherBBox.bottom || otherBBox.top >= callerBBox.bottom)) {
#ifdef HLD_VERIFY_PLACEMENT_FILTERS
                        require(!Collision_instancesOverlapPrecise(runner, caller, other, callerBBox, otherBBox));
#endif
''' + ("                        ++placementRejectedBoxes;\n" if compare else "") + "                        continue;\n"
                              "                    }\n\n" + check)
        replace(before, after)
    return code
