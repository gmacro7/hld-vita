"""Generate direct dispatch from the VM's existing opcode handlers."""

import re
from pathlib import Path

from stack_register_source import STACK_HELPERS, cached_stack_handler


def opcode_handlers(function):
    dispatch = function[function.index("        switch (opcode) {\n            case OP_PACKED_CALL_DISCARD:"):
                        function.rindex("\n            default:")]
    dispatch, count = re.subn(r"(?m)^#if IS_WAD17_OR_HIGHER_ENABLED\n"
                               r"            case OP_CALLV:[\s\S]*?^#endif\n", "", dispatch)
    assert count == 1
    cases = list(re.finditer(r"(?m)^ {12}case (OP_[A-Z0-9_]+):", dispatch))
    assert len(cases) > 30
    bodies = {}
    for index, case in enumerate(cases):
        stop = cases[index + 1].start() if index + 1 < len(cases) else len(dispatch)
        bodies[case[1]] = dispatch[case.end():stop].strip()
    return bodies


def with_threaded_vm(code, compare=False, decode_at_handler=False, alternate_operands=False,
                     cache_stack=False, compare_cached_stack=False):
    assert not alternate_operands or (decode_at_handler and not compare)
    assert not compare_cached_stack or (cache_stack and not alternate_operands and not compare)
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    start = code.index(anchor)
    end = code.index("\n}\n", start) + len("\n}")
    original = code[start:end]
    init = original[:original.index("    // Some opcodes")]
    init = init.replace("executeBytecodeInstructions(", "executeThreadedInstructions(")
    init = init.replace("    bool useHldDistortionLoop = code == ctx->hldDistortionCode;\n", "")
    fetch = original[original.index("        uint32_t instrAddr = ip;"):
                     original.index("\n#ifdef ENABLE_VM_OPCODE_PROFILER\n        if", original.index("        uint32_t instrAddr = ip;"))]
    for declaration, assignment in (
        ("uint32_t instrAddr = ip;", "instrAddr = ip;"),
        ("OpWord opword = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};",
         "opword.value = BinaryUtils_readUint32Aligned(bytecodeBase + ip);"),
        ("const uint8_t* extraData = bytecodeBase + ip;", "extraData = bytecodeBase + ip;"),
        ("uint8_t opcode = OpWord_opcode(opword);", "opcode = OpWord_opcode(opword);"),
    ):
        assert fetch.count(declaration) == 1, declaration
        fetch = fetch.replace(declaration, assignment)
    # Keep the existing operand rules and next instruction bounds check.
    fetch = "\n".join(line for line in fetch.strip("\n").splitlines() if not line.lstrip().startswith("//"))
    bodies = opcode_handlers(original)
    fixed_words = {"OP_PACKED_VAR_READ", "OP_PACKED_CONVERTED_READ", "OP_PACKED_SLOT_READ",
                   "OP_PACKED_SLOT_CONVERTED_READ", "OP_PACKED_SLOT_WRITE", "OP_PACKED_VAR_WRITE", "OP_PACKED_PROPERTY_READ",
                   "OP_PACKED_ARGUMENT", "OP_PACKED_SCALAR_PAIR", "OP_PACKED_PURE_GUARD", "OP_PACKED_ENEMY_SHADOW"}
    mixed_width = {"OP_PUSH", "OP_PUSH_LOCAL", "OP_PUSH_GLOBAL", "OP_PUSH_BUILTIN", "OP_POP",
                   "OP_CALL", "OP_BREAK", "OP_PACKED_CALL_DISCARD", "OP_PACKED_MATH"}
    if decode_at_handler:
        opcode_header = Path(__file__).resolve().parent.parent / "engine/src/op.h"
        definitions = dict(re.findall(r"X\((OP_\w+),\s*(0x[0-9A-Fa-f]+)\)", opcode_header.read_text()))
        definitions.update(re.findall(r"\b(OP_PACKED_\w+)\s*=\s*(0x[0-9A-Fa-f]+)", code))
        for name in bodies:
            assert bool(int(definitions[name], 16) & 0x40) == (name in fixed_words or name in mixed_width), name

    def emit(function, at_handler, use_cache=cache_stack):
        fetch_code = fetch
        widths = ""
        if at_handler:
            begins = fetch.index("        enum {")
            ends = fetch.index("        if (OpWord_hasOperand(opword))", begins)
            opcode = fetch.index("        opcode = OpWord_opcode(opword);", ends)
            widths = fetch[begins:ends]
            fetch_code = fetch[:begins] + fetch[opcode:]
        labels = []
        handlers = []
        aliases = []
        cached = 0
        for name, body in bodies.items():
            label = f"thread_{name}"
            labels.append(f"        [{name}] = &&{label},")
            prep = ""
            if at_handler and name in fixed_words:
                prep = "    ip += sizeof(uint32_t);\n"
            elif at_handler and name in mixed_width:
                prep = "    ip += ((OPERAND_WIDTH_WORDS >> (2 * OpWord_type1(opword))) & 3u) * sizeof(uint32_t);\n"
            if not body:
                aliases.append((label, prep))
                continue
            assert re.search(r"\b(?:break|return)\b", body) and not re.search(r"\bcontinue\s*;", body), name
            if aliases and at_handler:
                # An alias can have a different encoded width from the
                # opcode whose handler it shares.
                for alias, alias_prep in aliases:
                    handlers.append(f"{alias}:\n{alias_prep}    goto body_{name};\n")
                handlers.append(f"{label}:\n{prep}body_{name}:\n")
            else:
                handlers.extend(f"{alias}:" for alias, _ in aliases)
                handlers.append(f"{label}:\n{prep}")
            aliases.clear()
            if at_handler:
                handlers.append("#ifdef HLD_VERIFY_OPERAND_WIDTH\n"
                                "    require(ip == instrAddr + 4 + OpWord_operandSize(opword));\n"
                                "#endif\n")
            if use_cache:
                local_body = cached_stack_handler(name, body)
                if local_body is None:
                    handlers.append("    VM_FLUSH_STACK();\n")
                else:
                    body = local_body
                    cached += 1
            handlers.append(f"    do {{\n        {body}\n    }} while (0);\n")
            if use_cache and local_body is None:
                handlers.append("    VM_RELOAD_STACK();\n")
            handlers.append("    VM_NEXT();\n")
        assert not aliases
        rendered_init = init.replace("executeThreadedInstructions(", function + "(")
        finish = "    if (ip >= codeLength) return scriptFallthroughReturnValue(ctx);"
        cleanup = ""
        if use_cache:
            assert cached > 0
            print(f"HLD cached operand-stack index in {cached} handlers", flush=True)
            rendered_init += """    int32_t stackTop = ctx->stack.top;
#define VM_FLUSH_STACK() (ctx->stack.top = stackTop)
#define VM_RELOAD_STACK() (stackTop = ctx->stack.top)
"""
            finish = "    if (ip >= codeLength) { VM_FLUSH_STACK(); return scriptFallthroughReturnValue(ctx); }"
            cleanup = "#undef VM_FLUSH_STACK\n#undef VM_RELOAD_STACK\n"

        next_lines = [finish,
                      fetch_code,
                      "    void* target = jump[opcode];",
                      "    goto *(target != nullptr ? target : &&thread_unknown);"]
        macro = "#define VM_NEXT() do {\n" + "\n".join(next_lines) + "\n} while (0)"
        macro = " \\\n".join(macro.splitlines())
        return """
#if defined(__GNUC__) && !defined(__clang__)
__attribute__((noinline, noclone, optimize("no-crossjumping")))
#endif
""" + rendered_init + widths + """
    uint32_t instrAddr;
    OpWord opword;
    const uint8_t* extraData;
    uint8_t opcode;
    static void* const jump[256] = {
""" + "\n".join(labels) + """
    };
""" + macro + """
    VM_NEXT();

""" + "\n".join(handlers) + r"""
thread_unknown:
    logError("VM: Unknown opcode 0x%02X at offset %u\n", opcode, instrAddr);
    abort();
#undef VM_NEXT
""" + cleanup + """
}
"""
    trial = ""
    if compare:
        trial = r'''
static bool useThreadedVM(void) {
    static bool initialized;
    static bool enabled;
    if (!initialized) {
        FILE* flag = fopen("ux0:data/hld-vm/threaded-vm", "r");
        enabled = flag != nullptr;
        if (flag != nullptr) fclose(flag);
        initialized = true;
        logInfo("Vita bytecode dispatch threaded=%d\n", (int)enabled);
    }
    return enabled;
}
'''
    guard = """#if !IS_WAD17_OR_HIGHER_ENABLED && !defined(ENABLE_VM_TRACING) && \
    !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)"""
    threaded = guard + "\n" + trial
    if cache_stack:
        threaded += STACK_HELPERS.read_text() + "\n"
    if alternate_operands:
        threaded += r'''
static bool useOperandHandlers(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportOperandHandlers(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita operand handlers frame=%d enabled=%d\n", frame, (int)useOperandHandlers(frame - 1));
}
'''
        threaded += emit("executeThreadedInstructions", False) + emit("executeOperandHandlers", True)
    elif compare_cached_stack:
        threaded += r'''
static unsigned stackRegisterCalls;

static bool useStackRegister(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportStackRegister(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita stack register frame=%d enabled=%d calls=%u\n",
            frame, (int)useStackRegister(frame - 1), stackRegisterCalls);
    stackRegisterCalls = 0;
}
'''
        threaded += emit("executeThreadedInstructions", decode_at_handler, False)
        threaded += emit("executeCachedStackInstructions", decode_at_handler, True)
    else:
        threaded += emit("executeThreadedInstructions", decode_at_handler)
    threaded += "#endif\n"
    # Preserve the existing path for the distortion generator and diagnostic builds.
    select = """    if (ctx->dataWin->gen8.wadVersion == 15 && code != ctx->hldDistortionCode"""
    if compare:
        select += " && useThreadedVM()"
    select += ") {\n"
    if alternate_operands:
        select += "        if (ctx->runner != nullptr && useOperandHandlers(ctx->runner->frameCount))\n"
        select += "            return executeOperandHandlers(ctx, code);\n"
    if compare_cached_stack:
        select += "        if (ctx->runner != nullptr && useStackRegister(ctx->runner->frameCount)) {\n"
        select += "            ++stackRegisterCalls;\n            return executeCachedStackInstructions(ctx, code);\n        }\n"
    select += "        return executeThreadedInstructions(ctx, code);\n    }"
    return code[:start] + threaded + original.replace(anchor, anchor + "\n" + guard + "\n" + select + "\n#endif") + code[end:]
