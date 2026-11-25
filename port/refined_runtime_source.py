"""Select the measured refinements together before an isolated launch."""


def with_refinement_gate(code, *gates, flag="hldRefinedRuntime"):
    for gate in gates:
        before = f"""bool {gate}(int frame) {{
    return frame >= 180 && (frame / 180) % 2 != 0;
}}"""
        after = f"""bool {gate}(int frame) {{
    extern bool {flag};
    (void)frame;
    return {flag};
}}"""
        assert code.count(before) == 1, gate
        code = code.replace(before, after)
    return code


def with_refinement_marker(code):
    start = "int main(int argc, char* argv[]) {"
    begin = "    mainThread = sceKernelGetThreadId();"
    assert code.count(start) == code.count(begin) == 1
    code = code.replace(start, "bool hldRefinedRuntime;\n\n" + start)
    return code.replace(begin, r'''    FILE* reference = fopen("ux0:data/hld-vm/reference-runtime-refinements", "r");
    hldRefinedRuntime = reference == nullptr;
    if (reference != nullptr) fclose(reference);
    logInfo("Vita runtime refinements enabled=%d\n", (int)hldRefinedRuntime);
''' + begin)
