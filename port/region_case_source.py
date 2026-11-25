"""Evaluate the common empty-region predicate as a shared native case path."""

from pathlib import Path


def with_region_cases(code, *, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    anchor = "static bool tryStableCase(VMContext* ctx, Instance* inst, RValue* result) {"
    helper = Path(__file__).with_name("region-case.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_REGION_CASES\n" + helper
    replace(anchor, helper + "\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    replace(anchor, anchor + "\n    bindRegionCase(ctx);")
    before = """                !caseNumber(Instance_getSelfVar(global, stableCases.touchCase), &touch) ||
                GMLReal_fabs(selector - empty) <= epsilon || GMLReal_fabs(selector - touch) <= epsilon ||
                !caseLinkResultUnchanged(ctx, inst, selector, check, epsilon)) return false;"""
    after = """                !caseNumber(Instance_getSelfVar(global, stableCases.touchCase), &touch) ||
                GMLReal_fabs(selector - empty) <= epsilon) return false;
            if (GMLReal_fabs(selector - touch) <= epsilon) {
                if (!caseRegionMiss(ctx, inst, epsilon)) return false;
            } else if (!caseLinkResultUnchanged(ctx, inst, selector, check, epsilon)) {
                return false;
            }"""
    replace(before, after)
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    if (regionCase.context == ctx) regionCase = (RegionCase){0};")
    return code
