"""Collect instruction counts for the combined runtime's expensive events."""

from native_enemy_source import MATCH, ROOT, packed_program, word_fingerprint


COMBINED_PROGRAMS = ROOT / "packed-combined-programs.log"
EVENTS = {
    "Enemy": "gml_Object_enemy_Step_0",
    "Screen": "gml_Object_SCREEN_Step_2",
    "Vent": "gml_Object_FlameVent_Step_0",
    "Spawner": "gml_Object_Spawner_Step_0",
    "Case": "gml_Script_CaseCheck",
}


def combined_programs():
    return {key: packed_program(name, COMBINED_PROGRAMS) for key, name in EVENTS.items()}


def with_event_execution_profile(code):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    programs = combined_programs()
    declarations = [
        f"static uint32_t profileEvent{key}[{len(words)}];" for key, words in programs.items()
    ]
    declarations += [r'''
static struct {
    const char* name;
    uint32_t length;
    uint32_t fingerprint;
    uint32_t* counts;
    const CodeEntry* code;
} profileEvents[] = {''']
    for key, words in programs.items():
        declarations.append(f'    {{"{EVENTS[key]}", {len(words) * 4}, '
                            f'0x{word_fingerprint(words):08x}u, profileEvent{key}, nullptr}},')
    helper = "\n".join(declarations) + r'''
};
static const VMContext* profileEventsContext;

static void bindEventCoverage(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    profileEventsContext = ctx;
    repeat(sizeof(profileEvents) / sizeof(*profileEvents), i) {
        profileEvents[i].code = matchHldCode(ctx, profileEvents[i].name, profileEvents[i].length,
                                           profileEvents[i].fingerprint);
    }
}

static uint32_t* eventCoverageFor(VMContext* ctx, const CodeEntry* code) {
    if (ctx != profileEventsContext || ctx->runner == nullptr || ctx->runner->frameCount < 180) return nullptr;
    repeat(sizeof(profileEvents) / sizeof(*profileEvents), i) {
        if (code == profileEvents[i].code) return profileEvents[i].counts;
    }
    return nullptr;
}

static void writeEventCoverage(VMContext* ctx) {
    if (ctx != profileEventsContext) return;
    const char* path = getenv("HLD_EVENT_COVERAGE_OUT");
    FILE* file = path == nullptr ? nullptr : fopen(path, "w");
    repeat(sizeof(profileEvents) / sizeof(*profileEvents), i) {
        if (file != nullptr && profileEvents[i].code != nullptr) {
            fprintf(file, "HLD event coverage %s 0x%08x\n", profileEvents[i].name, profileEvents[i].fingerprint);
            repeat(profileEvents[i].length / 4, pc) {
                const uint32_t hits = profileEvents[i].counts[pc];
                if (hits != 0) fprintf(file, "%u %u\n", (unsigned)pc * 4, hits);
            }
            fprintf(file, "HLD event coverage end\n");
        }
        memset(profileEvents[i].counts, 0, profileEvents[i].length);
        profileEvents[i].code = nullptr;
    }
    if (file != nullptr) fclose(file);
    profileEventsContext = nullptr;
}

'''
    anchor = "static RValue executeBytecodeInstructions(VMContext* ctx, CodeEntry* code) {"
    replace(anchor, MATCH + "\n\n" + helper + anchor)
    anchor = "    RValue* restrict slots = ctx->stack.slots;\n\n    // Some opcodes"
    replace(anchor, "    uint32_t* const eventCoverage = eventCoverageFor(ctx, code);\n" + anchor)
    anchor = "        OpWord opword = {BinaryUtils_readUint32Aligned(bytecodeBase + ip)};"
    replace(anchor, "        if (eventCoverage != nullptr) ++eventCoverage[instrAddr / 4];\n" + anchor)
    anchor = "    packHldMathInstructions(ctx);"
    replace(anchor, anchor + "\n    bindEventCoverage(ctx);")
    anchor = "void VM_free(VMContext* ctx) {"
    replace(anchor, anchor + "\n    writeEventCoverage(ctx);")
    return code
