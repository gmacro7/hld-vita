"""Compile hot instruction locations in the combined runtime's busy events."""

import re

from event_profiles_source import EVENTS, combined_programs
from native_enemy_source import (GUARD, MATCH, ROOT, decode_instructions, emit_event_execution,
                                 emit_words, select_hot_offsets, word_fingerprint)
from threaded_vm_source import opcode_handlers


def read_event_profiles():
    profiles = {name: [] for name in EVENTS.values()}
    for path in sorted((ROOT / "event-coverage").glob("*/counts.log")):
        blocks = re.findall(r"^HLD event coverage (gml_\w+) (0x[0-9a-f]{8})\n"
                            r"([0-9 \n]*)^HLD event coverage end$", path.read_text(), flags=re.M)
        assert len(blocks) == len(profiles), path
        assert {name for name, _, _ in blocks} == profiles.keys(), path
        for name, fingerprint, body in blocks:
            counts = {int(pc): int(hits) for pc, hits in (line.split() for line in body.splitlines())}
            profiles[name].append((path.parent.name, int(fingerprint, 16), counts))
    return profiles


def emit_compiled_events(source, interpreter, compare):
    profiles = read_event_profiles()
    handlers = opcode_handlers(interpreter)
    programs = combined_programs()
    selected = []
    for key, words in programs.items():
        name = EVENTS[key]
        fingerprint = word_fingerprint(words)
        samples = profiles[name]
        assert all(receipt == fingerprint for _, receipt, _ in samples), name
        instructions = list(decode_instructions(words, source, handlers))
        hot = select_hot_offsets(instructions, [(room, counts) for room, _, counts in samples], key)
        if hot:
            selected.append((key, words, instructions, hot, fingerprint))
    assert 0 < len(selected) < 256, "Collect event execution profiles before building the compiled events"

    lines = [MATCH, "static const VMContext* nativeEventsContext;", "static uint8_t* nativeEventSlots;",
             f"static unsigned nativeEventCalls[{len(selected)}];",
             f"static unsigned nativeEventCold[{len(selected)}];", ""]
    for index, (key, words, instructions, hot, _) in enumerate(selected):
        lines.extend(emit_words(words, f"nativeWords{key}"))
        entry = [f"    ++nativeEventCalls[{index}];"]
        cold = [f"    ++nativeEventCold[{index}];"]
        if not compare:
            entry = [f'    if (++nativeEventCalls[{index}] == 100) logInfo("HLD: compiled {key} reached 100 calls\\n");']
            cold = [f'    if (++nativeEventCold[{index}] == 100) logInfo("HLD: compiled {key} interpreted 100 instructions\\n");']
        lines.extend(emit_event_execution(instructions, handlers, hot, len(words) * 4,
                                          function=f"executeNative{key}", words=f"nativeWords{key}",
                                          compact=True, entry_lines=entry, cold_lines=cold, preserve_scenery=True))

    lines.extend([r'''
static const struct {
    const char* name;
    const uint32_t* words;
    uint32_t length;
    uint32_t fingerprint;
    RValue (*execute)(VMContext*, CodeEntry*);
} nativeEvents[] = {'''])
    for key, words, _, _, fingerprint in selected:
        lines.append(f'    {{"{EVENTS[key]}", nativeWords{key}, {len(words) * 4}, '
                     f'0x{fingerprint:08x}u, executeNative{key}}},')
    lines.extend(["};", ""])
    if compare:
        lines.extend([r'''static bool nativeEventsEnabled;

void hldReportCompiledEvents(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    repeat(sizeof(nativeEvents) / sizeof(*nativeEvents), i) {
        if (nativeEventCalls[i] == 0) continue;
        logInfo("Vita compiled event frame=%d enabled=%d calls=%u cold=%u name=%s\n",
                frame, (int)nativeEventsEnabled, nativeEventCalls[i], nativeEventCold[i], nativeEvents[i].name);
        nativeEventCalls[i] = nativeEventCold[i] = 0;
    }
    logInfo("Vita compiled events frame=%d enabled=%d\n", frame, (int)nativeEventsEnabled);
}
'''])
    lines.extend([r'''static void bindNativeEvents(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    if (nativeEventSlots != nullptr) return;
    nativeEventSlots = safeCalloc(ctx->dataWin->code.count, sizeof(*nativeEventSlots));
    nativeEventsContext = ctx;'''])
    if compare:
        lines.extend([r'''    FILE* flag = fopen("ux0:data/hld-vm/native-events", "r");
    nativeEventsEnabled = flag != nullptr;
    if (flag != nullptr) fclose(flag);'''])
    lines.extend([r'''    repeat(sizeof(nativeEvents) / sizeof(*nativeEvents), i) {
        const CodeEntry* code = matchHldCode(ctx, nativeEvents[i].name, nativeEvents[i].length,
                                           nativeEvents[i].fingerprint);
        if (code == nullptr || memcmp(codeBytes(ctx, code), nativeEvents[i].words, nativeEvents[i].length) != 0) continue;
        const size_t codeIndex = code - ctx->dataWin->code.entries;
        nativeEventSlots[codeIndex] = (uint8_t)(i + 1);
        logInfo("HLD: bound compiled event %s\n", nativeEvents[i].name);
    }
}

static void releaseNativeEvents(VMContext* ctx) {
    if (ctx != nativeEventsContext) return;
    free(nativeEventSlots);
    nativeEventSlots = nullptr;
    nativeEventsContext = nullptr;
    memset(nativeEventCalls, 0, sizeof(nativeEventCalls));
    memset(nativeEventCold, 0, sizeof(nativeEventCold));
}
'''])
    return "\n".join(lines)


def with_compiled_events(code, compare=False):
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    start = code.index(anchor)
    end = code.index("\n}\n", start) + 3
    generated = emit_compiled_events(code, code[start:end], compare)
    enabled = "nativeEventsEnabled && " if compare else ""
    select = f"""
#if {GUARD}
    if ({enabled}ctx == nativeEventsContext) {{
        const uint8_t slot = nativeEventSlots[code - ctx->dataWin->code.entries];
        if (slot != 0) return nativeEvents[slot - 1].execute(ctx, code);
    }}
#endif"""
    code = code[:start] + f"#if {GUARD}\n{generated}\n#endif\n\n" + anchor + select + code[start + len(anchor):]
    bind = "    packHldMathInstructions(ctx);"
    assert code.count(bind) == 1
    code = code.replace(bind, bind + f"\n#if {GUARD}\n    bindNativeEvents(ctx);\n#endif")
    release = "void VM_free(VMContext* ctx) {"
    assert code.count(release) == 1
    return code.replace(release, release + f"\n#if {GUARD}\n    releaseNativeEvents(ctx);\n#endif")
