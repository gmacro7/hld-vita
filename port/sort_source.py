"""Build source for an A/B of cached WAD 15 draw sort keys."""
from pathlib import Path


def with_fast_draw_sort(code, always=False):
    anchor = "static void rebuildDrawableCacheIfDirty(Runner* runner) {"
    assert code.count(anchor) == 1
    implementation = Path(__file__).with_name("draw-sort.inc").read_text()
    code = code.replace(anchor, implementation + "\n" + anchor)
    original = "            qsort(runner->cachedDrawables, count, sizeof(Drawable), compareDrawables);"
    assert code.count(original) == 2
    guard = ""
    if not always:
        guard = "((runner->frameCount - 1) / 180) % 2 == 0 || "
    def sort_call(depths_changed):
        return (f"            if ({guard}!sortLegacyDrawables(runner->cachedDrawables, count, {depths_changed})) {{\n"
                "                qsort(runner->cachedDrawables, count, sizeof(Drawable), compareDrawables);\n"
                "            }")

    structure, depths, tail = code.split(original)
    return structure + sort_call("false") + depths + sort_call("true") + tail
