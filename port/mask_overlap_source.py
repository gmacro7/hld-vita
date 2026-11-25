"""Factor the precise overlap scan while retaining the original pixel test."""

from pathlib import Path


def with_mask_overlap(code, *, verify=False, compare=False, reference_marker=False, grid=None):
    assert not reference_marker or compare
    assert grid in (None, "direct", "checked", "compare") and not (grid and compare)
    anchor = "static inline bool Collision_instancesOverlapPrecise("
    assert code.count(anchor) == 1
    start = code.index("    for (int32_t py = startY;", code.index(anchor))
    end = code.index("\n}", start)
    original = code[start:end]
    reference = original.replace("&queryA,", "a,").replace("&queryB,", "b,")
    helpers = """
static inline bool Collision_referenceMaskOverlap(const CollisionPointQuery* a, const CollisionPointQuery* b,
                                                  int32_t startX, int32_t startY, int32_t endX, int32_t endY,
                                                  bool compatMode, GMLReal sampleOffset) {
""" + reference + "\n}\n\n"
    helpers += Path(__file__).with_name("mask-overlap.inc").read_text() + "\n"
    if verify:
        helpers += "static unsigned checkedMaskOverlaps;\n\n"
    if grid:
        helpers += Path(__file__).with_name("grid-mask-overlap.inc").read_text() + "\n"
    if grid == "checked":
        helpers += "static unsigned checkedGridMaskOverlaps;\n\n"
    if grid == "compare":
        helpers += """
static unsigned gridMaskOverlaps, genericMaskOverlaps;

static inline bool useGridMaskOverlap(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportGridMaskOverlaps(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita grid masks frame=%d enabled=%d direct=%u generic=%u\\n",
            frame, (int)useGridMaskOverlap(frame - 1), gridMaskOverlaps, genericMaskOverlaps);
    gridMaskOverlaps = genericMaskOverlaps = 0;
}

"""
    if compare:
        helpers += """
static unsigned splitMaskOverlaps, referenceMaskOverlaps;

static inline bool useSplitMaskOverlap(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportMaskOverlaps(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita mask overlaps frame=%d enabled=%d split=%u reference=%u\\n",
            frame, (int)useSplitMaskOverlap(frame - 1), splitMaskOverlaps, referenceMaskOverlaps);
    splitMaskOverlaps = referenceMaskOverlaps = 0;
}

"""
        if reference_marker:
            helpers = helpers.replace("    return frame >= 180 && (frame / 180) % 2 != 0;", """    (void)frame;
    static int accelerated = -1;
    if (accelerated < 0) {
        FILE* marker = fopen("ux0:data/hld-vm/reference-mask-overlap", "r");
        accelerated = marker == nullptr;
        if (marker != nullptr) fclose(marker);
    }
    return accelerated;""")
    fallback = "Collision_referenceMaskOverlap(&queryA, &queryB, startX, startY, endX, endY, compatMode, sampleOffset)"
    enabled = "useSplitMaskOverlap(runner->frameCount) && " if compare else ""
    replacement = f"""    if ({enabled}Collision_canSplitMaskAxes(&queryA) && Collision_canSplitMaskAxes(&queryB) &&
        (int64_t)endX - startX + compatMode >= 8 && (int64_t)endY - startY + compatMode >= 4) {{
"""
    if compare:
        replacement += "        ++splitMaskOverlaps;\n"
    if grid:
        guard = "useGridMaskOverlap(runner->frameCount) && " if grid == "compare" else ""
        replacement += f"""        bool gridResult;
        if ({guard}Collision_tryGridMaskOverlap(&queryA, &queryB,
                startX, startY, endX, endY, compatMode, sampleOffset, &gridResult)) {{
"""
        if grid == "checked":
            replacement += f"""            require(gridResult == {fallback});
            if (++checkedGridMaskOverlaps == 1000) logInfo("HLD: checked 1,000 grid mask overlaps\\n");
"""
        if grid == "compare":
            replacement += "            ++gridMaskOverlaps;\n"
        replacement += "            return gridResult;\n        }\n"
        if grid == "compare":
            replacement += "        ++genericMaskOverlaps;\n"
    replacement += """        const bool result = Collision_axisMaskOverlap(&queryA, &queryB,
                                                          startX, startY, endX, endY, compatMode, sampleOffset);
"""
    if verify:
        replacement += f"""        require(result == {fallback});
        if (++checkedMaskOverlaps == 1000) logInfo("HLD: checked 1,000 split-axis mask overlaps\\n");
"""
    replacement += "        return result;\n    }\n"
    if compare:
        replacement += "    ++referenceMaskOverlaps;\n"
    body = f"#ifdef HLD_SPLIT_MASK_OVERLAP\n{replacement}    return {fallback};\n#else\n{original}\n#endif"
    code = code[:start] + body + code[end:]
    return code.replace(anchor, "#ifdef HLD_SPLIT_MASK_OVERLAP\n" + helpers + "#endif\n\n" + anchor, 1)
