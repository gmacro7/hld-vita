"""Compile the packed enemy Step event with the interpreter's own handlers."""

from pathlib import Path
import re

from threaded_vm_source import opcode_handlers


ROOT = Path(__file__).resolve().parent
EVENT = "gml_Object_enemy_Step_0"
GUARD = ("!IS_WAD17_OR_HIGHER_ENABLED && !defined(ENABLE_VM_TRACING) && "
         "!defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)")
MATCH = "static const CodeEntry* matchHldCode(VMContext*, const char*, uint32_t, uint32_t);"


def packed_program(name, path=ROOT / "packed-runtime-programs.log"):
    source = path.read_text()
    prepared = re.search(rf"^HLD packed program {name} (\d+)\n([0-9a-f \n]+)^HLD packed program end$",
                         source, flags=re.M)
    assert prepared is not None, name
    words = [int(word, 16) for word in prepared[2].split()]
    assert len(words) * 4 == int(prepared[1])
    return words


def decode_instructions(words, code, handlers):
    definitions = re.findall(r"X\((OP_\w+),\s*(0x[0-9A-Fa-f]+)\)",
                             (ROOT.parent / "engine/src/op.h").read_text())
    definitions += re.findall(r"\b(OP_PACKED_\w+)\s*=\s*(0x[0-9A-Fa-f]+)", code)
    opcodes = {int(value, 16): name for name, value in definitions}
    operand_words = (2, 1, 1, 2, 1, 1, 1)
    pc = 0
    while pc < len(words):
        word = words[pc]
        opcode = word >> 24
        name = opcodes[opcode]
        assert name in handlers, (pc, name)
        value_type = (word >> 16) & 15
        operand_count = operand_words[value_type] if opcode & 0x40 and value_type < len(operand_words) else 0
        next_pc = pc + 1 + operand_count
        assert next_pc <= len(words)
        yield pc * 4, next_pc * 4, word, name
        pc = next_pc


def word_fingerprint(words):
    fingerprint = 2166136261
    for word in words:
        fingerprint = ((fingerprint ^ word) * 16777619) & 0xFFFFFFFF
    return fingerprint


def with_enemy_execution_profile(code):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    words = packed_program(EVENT)
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    replace(anchor, MATCH + f"""
static const CodeEntry* enemyCoverageCode;
static const VMContext* enemyCoverageContext;
static uint32_t enemyCoverage[{len(words)}];

static void bindEnemyCoverage(VMContext* ctx) {{
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    enemyCoverageCode = matchHldCode(ctx, "{EVENT}", {4 * len(words)}, 0x{word_fingerprint(words):08x}u);
    if (enemyCoverageCode != nullptr) enemyCoverageContext = ctx;
}}

static void writeEnemyCoverage(VMContext* ctx) {{
    if (ctx != enemyCoverageContext) return;
    const char* path = getenv("HLD_ENEMY_COVERAGE_OUT");
    FILE* file = path == nullptr ? nullptr : fopen(path, "w");
    if (file != nullptr) {{
        fprintf(file, "HLD enemy coverage 0x{word_fingerprint(words):08x}\\n");
        repeat(sizeof(enemyCoverage) / sizeof(*enemyCoverage), i) {{
            if (enemyCoverage[i] != 0) fprintf(file, "%u %u\\n", (unsigned)i * 4, enemyCoverage[i]);
        }}
        fclose(file);
    }}
    enemyCoverageCode = nullptr;
    enemyCoverageContext = nullptr;
    memset(enemyCoverage, 0, sizeof(enemyCoverage));
}}

""" + anchor)
    anchor = "    RValue* restrict slots = ctx->stack.slots;\n\n    // Some opcodes"
    replace(anchor, """    const bool recordEnemyCoverage = ctx == enemyCoverageContext && code == enemyCoverageCode &&
        ctx->runner != nullptr && ctx->runner->frameCount >= 180;
""" + anchor)
    anchor = "        OpWord opword = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};"
    replace(anchor, "        if (recordEnemyCoverage) ++enemyCoverage[instrAddr / 4];\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    replace(anchor, anchor + "\n    bindEnemyCoverage(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    writeEnemyCoverage(ctx);")
    return code


def select_hot_offsets(instructions, profiles, name):
    """Keep the most frequently used instructions in each recorded room."""
    hot = {0}
    pcs = {pc for pc, _, _, _ in instructions}
    samples = []
    for room, counts in profiles:
        assert counts.keys() <= pcs and all(hits > 0 for hits in counts.values()), (name, room)
        total = sum(counts.values())
        covered = 0
        for pc, hits in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
            hot.add(pc)
            covered += hits
            if covered * 200 >= total * 197:
                break
        if total > 0:
            samples.append((room, len(counts), total))
    if not samples:
        return set()
    assert 1 < len(hot) <= len(instructions), name
    for room, distinct, total in samples:
        print(f"{name} coverage {room}: {distinct} visited instructions, {total} executions", flush=True)
    print(f"{name} compilation: {len(hot)}/{len(instructions)} instruction offsets", flush=True)
    return hot


def hot_enemy_offsets(instructions, fingerprint):
    profiles = []
    for profile in sorted((ROOT / "enemy-coverage").glob("*/hits.txt")):
        header, *lines = profile.read_text().splitlines()
        assert header == f"HLD enemy coverage 0x{fingerprint:08x}", profile
        counts = {int(pc): int(hits) for pc, hits in (line.split() for line in lines)}
        profiles.append((profile.parent.name, counts))
    hot = select_hot_offsets(instructions, profiles, "Enemy")
    assert hot, "Collect enemy execution profiles before building the compact event"
    return hot


def emit_words(words, name):
    lines = [f"static const uint32_t {name}[{len(words)}] = {{"]
    for start in range(0, len(words), 12):
        lines.append("    " + ", ".join(f"0x{word:08x}u" for word in words[start:start + 12]) + ",")
    return lines + ["};", ""]


def emit_event_execution(instructions, handlers, compiled, length, *, function, words,
                         compact, entry_lines, cold_lines, preserve_scenery=False):
    boundaries = {pc for pc, _, _, _ in instructions} | {length}
    resolved_handlers = {}
    last_body = None
    for name, body in reversed(handlers.items()):
        if body:
            last_body = body
        assert last_body is not None
        resolved_handlers[name] = last_body

    scenery = "code == ctx->hldSceneryStepCode" if preserve_scenery else "false"
    lines = ["__attribute__((noinline))",
             f"static RValue {function}(VMContext* ctx, CodeEntry* code) {{",
             "    ctx->bytecodeBase = codeBytes(ctx, code);",
             f"    const uint8_t* const bytecodeBase = (const uint8_t*){words};",
             f"    const uint32_t codeLength = {length};",
             "    RValue* restrict slots = ctx->stack.slots;",
             "    const bool fuseInstructions = true;",
             f"    const bool useHldSceneryCull = {scenery};",
             "    uint32_t ip = 0;", *entry_lines]
    lines.extend([
                  "    goto native_pc_0000;",
                  "native_resume:",
                  "    if (ip >= codeLength) return scriptFallthroughReturnValue(ctx);",
                  "    switch (ip) {"])
    for pc in sorted(compiled):
        lines.append(f"        case 0x{pc:04x}: goto native_pc_{pc:04x};")
    if compact:
        lines.extend(["        default: goto native_interpret;", "    }",
                      "native_interpret: {",
                      "    const uint32_t instrAddr = ip;",
                      "    const OpWord opword = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};",
                      "    ip += sizeof(uint32_t) + OpWord_operandSize(opword);",
                      "    const uint8_t* const extraData = bytecodeBase + instrAddr + sizeof(uint32_t);",
                      "    const uint8_t opcode = OpWord_opcode(opword);"])
        lines.extend(cold_lines)
        lines.append("    switch (opcode) {")
        for name, body in handlers.items():
            lines.extend([f"        case {name}:", "            " + body])
        lines.extend(["        default:", "            logError(\"Invalid compiled event opcode 0x%x at 0x%x\\n\", opcode, instrAddr);",
                      "            abort();", "    }", "    goto native_resume;", "}"])
    else:
        lines.extend(["        default:", "            logError(\"Invalid compiled event offset 0x%x\\n\", ip);",
                      "            abort();", "    }"])
    for pc, next_pc, word, name in instructions:
        if pc not in compiled:
            continue
        body = resolved_handlers[name]
        assert not re.search(r"\bcontinue\s*;", body), name
        lines.extend([f"native_pc_{pc:04x}: {{",
                      f"    const uint32_t instrAddr MAYBE_UNUSED = 0x{pc:04x};",
                      f"    const OpWord opword MAYBE_UNUSED = {{0x{word:08x}u}};",
                      f"    const uint8_t* const extraData MAYBE_UNUSED = bytecodeBase + 0x{pc + 4:04x};",
                      f"    const uint8_t opcode MAYBE_UNUSED = 0x{word >> 24:02x};",
                      f"    ip = 0x{next_pc:04x};", "    do {", "        " + body, "    } while (0);"])
        if name in ("OP_B", "OP_BF", "OP_BT"):
            relative_words = (word & 0x3FFFFF) - (word & 0x400000)
            branch = pc + 4 * relative_words
            assert branch in boundaries, (pc, branch)
            if branch == length or branch in compiled:
                lines.append(f"    if (ip == 0x{branch:04x}) goto native_pc_{branch:04x};")
        if next_pc == length or next_pc in compiled:
            lines.append(f"    if (ip == 0x{next_pc:04x}) goto native_pc_{next_pc:04x};")
        lines.extend(["    goto native_resume;", "}"])
    lines.extend([f"native_pc_{length:04x}:", "    return scriptFallthroughReturnValue(ctx);", "}"])
    return lines


def emit_enemy_program(source, interpreter, compare, compact):
    words = packed_program(EVENT)
    handlers = opcode_handlers(interpreter)
    instructions = list(decode_instructions(words, source, handlers))
    length = len(words) * 4
    fingerprint = word_fingerprint(words)
    compiled = hot_enemy_offsets(instructions, fingerprint) if compact else {pc for pc, _, _, _ in instructions}
    lines = emit_words(words, "nativeEnemyWords")
    lines.extend(["static const CodeEntry* nativeEnemyCode;",
                  "static const VMContext* nativeEnemyContext;", MATCH, ""])
    if compare:
        lines.extend(["static bool nativeEnemyEnabled;", "static unsigned nativeEnemyCalls;",
                      "static unsigned nativeEnemyColdInstructions;",
                      "void hldReportNativeEnemy(int frame) {",
                      "    if (frame <= 0 || frame % 90 != 0) return;",
                      "    logInfo(\"Vita compiled enemy frame=%d enabled=%d calls=%u cold=%u\\n\",",
                      "            frame, (int)nativeEnemyEnabled, nativeEnemyCalls, nativeEnemyColdInstructions);",
                      "    nativeEnemyCalls = nativeEnemyColdInstructions = 0;", "}", ""])
    lines.extend(["static void bindNativeEnemy(VMContext* ctx) {",
                  "    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;"])
    if compare:
        lines.extend(["    FILE* flag = fopen(\"ux0:data/hld-vm/native-enemy-step\", \"r\");",
                      "    nativeEnemyEnabled = flag != nullptr;",
                      "    if (flag != nullptr) fclose(flag);"])
    lines.extend([
        f"    nativeEnemyCode = matchHldCode(ctx, \"{EVENT}\", {length}, 0x{fingerprint:08x}u);",
        "    if (nativeEnemyCode == nullptr) return;",
        "    if (memcmp(codeBytes(ctx, nativeEnemyCode), nativeEnemyWords, sizeof(nativeEnemyWords)) != 0) {",
        "        nativeEnemyCode = nullptr;", "        return;", "    }", "    nativeEnemyContext = ctx;",
        "    logInfo(\"HLD: bound compiled enemy Step\\n\");", "}", ""])
    if compare:
        entry = ["    ++nativeEnemyCalls;"]
        cold = ["    ++nativeEnemyColdInstructions;"]
    else:
        mode = "compact" if compact else "compiled"
        entry = ["    static unsigned executions;",
                 f"    if (++executions == 100) logInfo(\"HLD: executed 100 {mode} enemy Steps\\n\");"]
        cold = ["    static unsigned cold;",
                "    if (++cold == 100) logInfo(\"HLD: interpreted 100 cold enemy instructions\\n\");"]
    lines.extend(emit_event_execution(instructions, handlers, compiled, length, function="executeNativeEnemy",
                                      words="nativeEnemyWords", compact=compact, entry_lines=entry, cold_lines=cold))
    return "\n".join(lines)


def with_native_enemy_event(code, compare=False, compact=False):
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    starts = code.index(anchor)
    finish = code.index("\n}\n", starts) + 3
    native = emit_enemy_program(code, code[starts:finish], compare, compact)
    source = f"#if {GUARD}\n{native}\n#endif\n\n"
    enabled = "nativeEnemyEnabled && " if compare else ""
    select = f"""
#if {GUARD}
    if ({enabled}ctx == nativeEnemyContext && code == nativeEnemyCode) return executeNativeEnemy(ctx, code);
#endif"""
    code = code[:starts] + source + code[starts:finish].replace(anchor, anchor + select) + code[finish:]
    bind = "    packHldMathInstructions(ctx);"
    assert code.count(bind) == 1
    code = code.replace(bind, bind + f"\n#if {GUARD}\n    bindNativeEnemy(ctx);\n#endif")
    free = "void VM_free(VMContext* ctx) {"
    assert code.count(free) == 1
    return code.replace(free, free + f"\n#if {GUARD}\n    if (ctx == nativeEnemyContext) {{\n"
                        "        nativeEnemyContext = nullptr;\n        nativeEnemyCode = nullptr;\n    }\n#endif")
