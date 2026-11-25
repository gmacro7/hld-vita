"""Reject precise collision windows proven empty by the mask tile index."""

from pathlib import Path
import re
from textwrap import dedent, indent

from runtime_verification_source import replace_once


def with_mask_tiles_header(code, *, launch_flag=None):
    anchor = "static inline bool Collision_instancesOverlapPrecise("
    declaration = """#ifdef HLD_SPLIT_MASK_OVERLAP
bool hldEmptyMaskTiles(const CollisionPointQuery*, GMLReal left, GMLReal top, GMLReal right, GMLReal bottom);

static inline bool Collision_canCheckMaskTiles(const CollisionPointQuery* query) {
    if (query->mask == nullptr || query->rotated) return false;
    const uint32_t width = query->sprite->maskWidth, height = query->sprite->maskHeight;
    return width > 0 && width <= 4096 && height > 0 && height <= 4096 && width * height >= 32768;
}
#endif

"""
    if launch_flag:
        declaration = replace_once(declaration, "#ifdef HLD_SPLIT_MASK_OVERLAP",
                                   f"#ifdef HLD_SPLIT_MASK_OVERLAP\nextern bool {launch_flag};")
    code = replace_once(code, anchor, declaration + anchor)
    split_start = "    if (Collision_canSplitMaskAxes(&queryA)"
    reject = """\
    if (startX <= endX && startY <= endY &&
        (compatMode || (startX < endX && startY < endY))) {
        const GMLReal left = (GMLReal)startX + sampleOffset;
        const GMLReal top = (GMLReal)startY + sampleOffset;
        const GMLReal right = (GMLReal)(endX - !compatMode) + sampleOffset;
        const GMLReal bottom = (GMLReal)(endY - !compatMode) + sampleOffset;
        if ((Collision_canCheckMaskTiles(&queryA) && hldEmptyMaskTiles(&queryA, left, top, right, bottom)) ||
            (Collision_canCheckMaskTiles(&queryB) && hldEmptyMaskTiles(&queryB, left, top, right, bottom))) {
            return false;
        }
    }
"""
    if launch_flag:
        reject = replace_once(reject, "    if (startX", f"    if ({launch_flag} && startX")
    else:
        reject = replace_once(reject, "            return false;", """\
            require(!Collision_referenceMaskOverlap(&queryA, &queryB,
                    startX, startY, endX, endY, compatMode, sampleOffset));
            return false;""")
    return replace_once(code, split_start, reject + split_start)


def with_mask_tiles_builtins(code, *, launch_flag=None):
    helper = Path(__file__).with_name("mask-tiles.inc").read_text()
    if launch_flag:
        helper += f"""
void hldReportMaskTiles(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita mask tile index frame=%d enabled=%d rejects=%u lookups=%u builds=%u\\n",
            frame, (int){launch_flag}, maskTileRejects, maskTileLookups, maskTileBuilds);
    maskTileRejects = maskTileLookups = maskTileBuilds = 0;
}}
"""
    return code + "\n" + helper


def with_mask_tiles_runner(code):
    anchor = "void Runner_free(Runner* runner) {\n    if (runner == nullptr) return;\n"
    return replace_once(code, anchor, anchor + """
    extern void hldFreeMaskTileIndex(void);
    hldFreeMaskTileIndex();
""")


def with_mask_tiles_renderer(code):
    anchor = "        repeat(sprite->maskCount, i) free(sprite->masks[i]);"
    return replace_once(code, anchor, """        extern void hldForgetMaskTiles(const Sprite*);
        hldForgetMaskTiles(sprite);
""" + anchor)


def with_mask_line_tiles(code, *, checked, launch_flag=None):
    marker = "static RValue builtin_collision_line(VMContext* ctx, RValue* args, int32_t argCount) {"
    start = code.index(marker)
    stop = code.index("\n}\n", start) + 2
    original = code[start:stop]
    updated = original
    if checked:
        updated = replace_once(updated, "        bool found = false;",
                               "        bool found = false, emptyLineTiles = false;")
    for axis, setup, points in (
        ("Y", "xt, yt, vdx, vdy", "first + 0.5, (GMLReal)startY + 0.5, last + 0.5, (GMLReal)endY + 0.5"),
        ("X", "yl, xl, cdy, cdx", "(GMLReal)startX + 0.5, first + 0.5, (GMLReal)endX + 0.5, last + 0.5"),
    ):
        # Use the original loop rather than the alternate prepared-mask
        # helper, which can also be present in a Vita comparison build.
        pattern = rf"^( +)for \(int32_t p{axis.lower()} = start{axis};"
        match = re.search(pattern, updated, re.M)
        assert match
        anchor = match[0]
        # The rounded tangential coordinate is monotone between actual
        # samples at the two endpoints of the unchanged scalar loop.
        enabled = f"{launch_flag} && " if launch_flag else ""
        reject = (f"emptyLineTiles = emptyPreciseLineTiles(spr, inst, {points});" if checked else
                  f"""if (emptyPreciseLineTiles(spr, inst, {points})) {{
        ++maskTileLineRejects;
        maskTileLineSamples += (uint64_t)((int64_t)end{axis} - start{axis} + 1);
        continue;
    }}""")
        check = f"""\
if ({enabled}start{axis} <= end{axis} && largeLineMask(spr)) {{
    const GMLReal first = lineTileEndpoint(start{axis}, {setup});
    const GMLReal last = lineTileEndpoint(end{axis}, {setup});
    {reject}
}}
"""
        updated = replace_once(updated, anchor, indent(check, match[1]) + anchor)
    if checked:
        updated = replace_once(updated, "        if (!found) continue;", """\
        if (emptyLineTiles) {
            require(!found);
            ++maskTileLineRejects;
        }
        if (!found) continue;""")
    helper = """\
static unsigned maskTileLineChecks, maskTileLineRejects;
static bool emptyMaskTiles(const CollisionPointQuery*, GMLReal left, GMLReal top,
                           GMLReal right, GMLReal bottom, bool countOverlap);

static inline bool largeLineMask(const Sprite* sprite) {
    const uint32_t width = sprite->maskWidth, height = sprite->maskHeight;
    return width > 0 && width <= 4096 && height > 0 && height <= 4096 && width * height >= 32768;
}

static inline GMLReal lineTileEndpoint(int32_t pixel, GMLReal cross, GMLReal along,
                                       GMLReal crossDelta, GMLReal alongDelta) {
    return GMLReal_fabs(alongDelta) > 0.0001 ? cross + ((GMLReal)pixel - along) * crossDelta / alongDelta : cross;
}

static bool emptyPreciseLineTiles(Sprite* sprite, Instance* inst, GMLReal x1, GMLReal y1, GMLReal x2, GMLReal y2) {
    if (!isfinite(x1) || !isfinite(y1) || !isfinite(x2) || !isfinite(y2)) return false;
    const CollisionPointQuery query = Collision_preparePointQuery(sprite, inst);
    if (!Collision_canCheckMaskTiles(&query)) return false;
    ++maskTileLineChecks;
    return emptyMaskTiles(&query, GMLReal_fmin(x1, x2), GMLReal_fmin(y1, y2),
                          GMLReal_fmax(x1, x2), GMLReal_fmax(y1, y2), false);
}

"""
    metrics = ""
    if not checked:
        helper = "static uint64_t maskTileLineSamples;\n" + helper
        metrics = "    maskTileLineSamples = 0;\n"
    if launch_flag:
        helper = f"""\
extern bool {launch_flag};
""" + helper + f"""\
void hldReportLineMaskTiles(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita mask tile lines frame=%d enabled=%d rejects=%u checks=%u samples=%llu\\n",
            frame, (int){launch_flag}, maskTileLineRejects, maskTileLineChecks,
            (unsigned long long)maskTileLineSamples);
    maskTileLineChecks = maskTileLineRejects = 0;
{metrics}}}

"""
    else:
        samples = "" if checked else "    logInfo(\"HLD: mask tile line saved samples=%llu\\n\", (unsigned long long)maskTileLineSamples);\n"
        helper += """\
void hldReportLineMaskTiles(void) {
    logInfo("HLD: mask tile line checks=%u rejects=%u\\n", maskTileLineChecks, maskTileLineRejects);
""" + samples + """\
    maskTileLineChecks = maskTileLineRejects = 0;
""" + metrics + "}\n\n"
    return code[:start] + helper + updated + code[stop:]
