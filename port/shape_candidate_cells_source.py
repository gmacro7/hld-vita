"""Reuse stable object-filtered cells in circle and ellipse collision queries."""

from runtime_verification_source import replace_once


def with_shape_candidate_cells(code, *, launch_flag=None):
    cell = (
        "            Instance** cell = runner->spatialGrid->grid[SpatialGrid_cellIndex(runner->spatialGrid, gx, gy)];\n"
        "            int32_t cellLen = (int32_t) arrlen(cell);"
    )
    indexed_cell = """            const int32_t cellIndex = SpatialGrid_cellIndex(runner->spatialGrid, gx, gy);
            Instance** cell = runner->spatialGrid->grid[cellIndex];
            const size_t unfilteredCount = arrlen(cell);
            const bool indexed = useCells && query.filterByObject && unfilteredCount >= 8;
            if (indexed) {
                cell = placementCellTargets(runner, cell, cellIndex, targetObjIndex);
                ++indexedShapeCells;
                rejectedShapeCandidates += unfilteredCount - arrlen(cell);
            }
            int32_t cellLen = (int32_t)arrlen(cell);"""
    marker = "static RValue builtin_collision_circle("
    report = """static unsigned indexedShapeCells;
static size_t rejectedShapeCandidates;

void hldReportShapeCandidateCells(void) {
    logInfo("HLD: indexed shape cells=%u rejected candidates=%zu\\n",
            indexedShapeCells, rejectedShapeCandidates);
}

"""
    if launch_flag:
        report = f"""extern bool {launch_flag};
static unsigned indexedShapeCells;
static size_t rejectedShapeCandidates;

void hldReportShapeCandidateCells(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita indexed shape cells frame=%d enabled=%d cells=%u rejected=%u\\n",
            frame, (int){launch_flag}, indexedShapeCells, (unsigned)rejectedShapeCandidates);
    indexedShapeCells = 0;
    rejectedShapeCandidates = 0;
}}

"""
    code = replace_once(code, marker, report + marker)

    for shape in ("circle", "ellipse"):
        name = f"builtin_collision_{shape}"
        signature = f"static RValue {name}(VMContext* ctx, RValue* args, int32_t argCount)"
        start = code.index(signature)
        end = code.index("\n}\n", start) + 2
        old = code[start:end]
        new = replace_once(old, signature, f"static RValue {name}InCells("
                           "VMContext* ctx, RValue* args, int32_t argCount, bool useCells)")
        new = replace_once(new, cell, indexed_cell)
        new = replace_once(new, "if (!query.matchAll && query.filterByObject &&",
                           "if (!indexed && !query.matchAll && query.filterByObject &&")
        dispatch = f"    return {name}InCells(ctx, args, argCount, {launch_flag});" if launch_flag else f"""\
    const RValue reference = {name}InCells(ctx, args, argCount, false);
    const RValue result = {name}InCells(ctx, args, argCount, true);
    require(reference.type == result.type && reference.gmlStackType == result.gmlStackType
            && reference.real == result.real);
    return result;"""
        new += f"""

{signature} {{
{dispatch}
}}"""
        code = code[:start] + new + code[end:]
    return code
