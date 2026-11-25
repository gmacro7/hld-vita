"""Prepare a precise collision-line mask once per candidate sprite."""

from textwrap import dedent, indent

from runtime_verification_source import replace_once


def with_line_mask_prep(code, *, verify=False, launch_flag=None):
    declaration = "static RValue builtin_collision_line(VMContext* ctx, RValue* args, int32_t argCount) {"
    start = code.index("        // Recompute dx/dy for the clipped segment\n", code.index(declaration))
    end = code.index("\n        if (!found) continue;", start)
    original = code[start:end]
    samples = dedent(original)
    assert samples.count("Collision_pointInInstance(spr, inst, ") == 2
    predicate = "verifyPreparedLinePoint(&mask, spr, inst, " if verify else "Collision_testPoint(&mask, "
    samples = samples.replace("Collision_pointInInstance(spr, inst, ", predicate)

    helper = ""
    if verify:
        helper = r'''static bool verifyPreparedLinePoint(const CollisionPointQuery* mask, Sprite* sprite, Instance* inst,
                                           GMLReal x, GMLReal y) {
    const bool hit = Collision_testPoint(mask, x, y);
    require(hit == Collision_pointInInstance(sprite, inst, x, y));
    static unsigned validated;
    if (++validated == 10000) logInfo("HLD: checked 10,000 prepared line mask samples\n");
    return hit;
}

'''
    if launch_flag:
        helper += f'''extern bool {launch_flag};
static unsigned lineMasksPrepared;

void hldReportLineMaskPrep(int frame) {{
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita prepared line masks frame=%d enabled=%d calls=%u\\n",
            frame, (int){launch_flag}, lineMasksPrepared);
    lineMasksPrepared = 0;
}}

'''
    helper += r'''static bool hldTestPreparedLineMask(Sprite* spr, Instance* inst, InstanceBBox bbox,
                                    GMLReal xl, GMLReal yl, GMLReal xr, GMLReal yr) {
    const CollisionPointQuery mask = Collision_preparePointQuery(spr, inst);
'''
    if launch_flag:
        helper += "    ++lineMasksPrepared;\n"
    helper += indent(samples, "    ") + "    return found;\n}\n\n"

    call = "hldTestPreparedLineMask(spr, inst, bbox, xl, yl, xr, yr)"
    if launch_flag:
        legacy = replace_once(original, "        bool found = false;", "        found = false;")
        replacement = (f"        bool found;\n        if ({launch_flag}) {{\n"
                       f"            found = {call};\n"
                       "        } else {\n" + indent(legacy, "    ") + "        }\n")
    else:
        replacement = f"        const bool found = {call};\n"
    code = replace_once(code, original, replacement)
    return replace_once(code, declaration, helper + declaration)
