"""Reuse bounded single-row legacy array storage after its last release."""

from pathlib import Path


def with_array_pool(code, *, compare=False, report=False, launch_flag=None):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helpers = Path(__file__).with_name("array-pool.inc").read_text()
    if compare:
        helpers = "#define HLD_COMPARE_ARRAY_POOL\n" + helpers
    if launch_flag is not None:
        assert compare
        gate = "frame >= 180 && (frame / 180) % 2 != 0"
        assert helpers.count(gate) == 1
        helpers = f"extern bool {launch_flag};\n" + helpers.replace(gate, launch_flag)
    if report:
        helpers = "#define HLD_REPORT_ARRAY_POOL\n" + helpers
    anchor = "static void ensureLegacyRowCapacity(GMLArray* arr, int32_t minRows) {"
    replace(anchor, helpers + "\n" + anchor)
    replace("    GMLArray* arr = (GMLArray *)safeCalloc(1, sizeof(GMLArray));",
            "    GMLArray* arr = allocateGmlArray(type, initialLength);")
    replace("    GMLArray* dst = (GMLArray *)safeCalloc(1, sizeof(GMLArray));",
            """    const int32_t hint = src->type == GML_LEGACY_ARRAY && src->legacy.rowCount == 1
        ? src->legacy.rows[0].length : ARRAY_POOL_MAX_CAPACITY + 1;
    GMLArray* dst = allocateGmlArray(src->type, hint);""")
    replace("""            free(row->data);
        }
        free(arr->legacy.rows);""", """        }
        if (savePooledArray(arr)) return;
        // A recycled array can own a row buffer before growing to length one.
        if (arr->legacy.rows != nullptr) {
            const int32_t allocatedRows = arr->legacy.rowCount > 0 ? arr->legacy.rowCount : 1;
            for (int32_t r = 0; r < allocatedRows; ++r) free(arr->legacy.rows[r].data);
        }
        free(arr->legacy.rows);""")
    if compare or report:
        anchor = "        row->data = (RValue *)safeRealloc(row->data, (uint32_t) newCap * sizeof(RValue));"
        replace(anchor, anchor + "\n        __atomic_fetch_add(&arrayGrowths, 1, __ATOMIC_RELAXED);")
    return code


def with_array_pool_cleanup(code):
    anchor = "    free(ctx);"
    assert code.count(anchor) == 1
    return code.replace(anchor, "    extern void hldClearArrayPool(void);\n    hldClearArrayPool();\n" + anchor)
