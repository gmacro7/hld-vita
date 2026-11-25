"""Compare compiler layouts of the same threaded Vita interpreter."""

from runtime_verification_source import replace_once


def with_compact_threaded_vm(source):
    signature = "static RValue executeThreadedInstructions(VMContext* ctx, CodeEntry* code) {"
    start = source.index(signature)
    end = source.index("\n}\n", start) + len("\n}")
    threaded = source[start:end]
    assert "static void* const dispatch" in threaded or "&&thread_OP_PUSH" in threaded
    compact = replace_once(
        threaded, signature,
        '__attribute__((noinline, noclone, optimize("Os", "no-crossjumping")))\n'
        "static RValue executeCompactThreadedInstructions(VMContext* ctx, CodeEntry* code) {",
    )
    source = replace_once(source, threaded, threaded + "\n\nextern bool hldCompactVM;\n\n" + compact)
    dispatch = "        return executeThreadedInstructions(ctx, code);"
    return replace_once(source, dispatch,
                        "        return hldCompactVM ? executeCompactThreadedInstructions(ctx, code)\n"
                        "                            : executeThreadedInstructions(ctx, code);")
