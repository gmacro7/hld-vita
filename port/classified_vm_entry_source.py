"""Keep legacy interpreter calls out of the native-entry guard cascade."""

import hashlib
import re

from runtime_verification_source import replace_once


ENTRY = ("RValue VM_executeCode(VMContext* ctx, int32_t codeIndex, Instance* selfInstance, "
         "Instance* otherInstance, int32_t eventObjectIndex, int32_t eventType, "
         "int32_t eventSubtype, RValue* args, int32_t argCount) {")

# The list of eligible code entries must be audited again whenever an
# entry hook changes. These are the combined Mac and Vita preludes.
PRELUDE_HASHES = {
    "154f5b125330e77bacd6de6e2662719d8b67310f3b7929eb4da438792e0c8449",
    "388a482bdc8b103e48fcd98014defad1c403b55605cc266a1e204e506a02466e",
}

TABLE = r'''
static const VMContext* nativeEntryOwner;
static uint8_t* nativeEntryCandidates;
static uint32_t ordinaryEntryCalls, candidateEntryCalls, nativeEntryReturns;

static void bindNativeEntryCandidates(VMContext* ctx) {
    free(nativeEntryCandidates);
    nativeEntryOwner = ctx;
    nativeEntryCandidates = safeCalloc(ctx->dataWin->code.count, sizeof(*nativeEntryCandidates));
#define ENTRY_CODE(binding, member) (binding.context == ctx ? binding.member : nullptr)
    const CodeEntry* candidates[] = {
        ENTRY_CODE(stableCases, code), ENTRY_CODE(idleSpawner, code),
#ifdef HLD_ASCII_PARSER_AVAILABLE
        ENTRY_CODE(asciiParser, code),
#endif
        ENTRY_CODE(depthHelpers, setDepth), ENTRY_CODE(depthHelpers, modDepth),
        ENTRY_CODE(leafEvents, enemyStep), ENTRY_CODE(leafEvents, enemyBegin),
        ENTRY_CODE(leafEvents, decorDraw), ENTRY_CODE(leafEvents, sceneryDraw),
        pureGuards.context == ctx ? dormantMineEntry : nullptr,
        ENTRY_CODE(zeroMovement, code), ENTRY_CODE(idleScenery, code),
        EXTRA_NATIVE_ENTRIES
    };
#undef ENTRY_CODE
    for (size_t i = 0; i < sizeof(candidates) / sizeof(*candidates); ++i) {
        if (candidates[i] == nullptr) continue;
        const ptrdiff_t index = candidates[i] - ctx->dataWin->code.entries;
        require(index >= 0 && index < ctx->dataWin->code.count);
        nativeEntryCandidates[index] = 1;
    }
}

void hldReportClassifiedEntries(int frame) {
    REPORT_GUARD
    logInfo("HLD code entry frame=%d ordinary=%u candidates=%u native_returns=%u\n",
            frame, ordinaryEntryCalls, candidateEntryCalls, nativeEntryReturns);
    ordinaryEntryCalls = candidateEntryCalls = nativeEntryReturns = 0;
}
'''

EXTRA = r'''
        ENTRY_CODE(movementHelpers, approach), ENTRY_CODE(movementHelpers, distance),
        ENTRY_CODE(movementHelpers, isType), ENTRY_CODE(sceneQueries, bounds),
        ENTRY_CODE(sceneQueries, viewport), ENTRY_CODE(lineScript, code),
        ENTRY_CODE(floorPatternBinding, code),
'''


def with_classified_vm_entry(code, *, checked=False, windowed_report=False):
    locate = "    CodeEntry* code = &ctx->dataWin->code.entries[codeIndex];"
    body = "    // Create a CallFrame for this script\n"
    function = code.index(ENTRY)
    begin = code.index(locate, function) + len(locate)
    end = code.index(body, begin)
    prelude = code[begin:end]
    if hashlib.sha256(prelude.encode()).hexdigest() not in PRELUDE_HASHES:
        raise ValueError("Audit the updated VM native-entry hooks before classifying them")
    # These build-time checks require prelude variables in the interpreter
    # body. This trial uses the existing direct forms of the same hooks.
    hook_checks = ("DEPTH_HELPERS", "LEAF_EVENTS", "MOVEMENT_HELPERS", "ASCII_PARSER", "ZERO_MOVEMENT")
    if any(f"#define HLD_VERIFY_{check}" in code for check in hook_checks):
        raise ValueError("Classified VM entry requires direct native hooks")
    extra = EXTRA if "const bool nativeQuery =" in prelude else ""
    guard = "if (frame <= 0 || frame % 90 != 0) return;" if windowed_report else ""
    table = TABLE.replace("EXTRA_NATIVE_ENTRIES", extra).replace("REPORT_GUARD", guard)
    create = "    logInfo(\"VM: Initialized with %u functions mapped\\n\", (uint32_t) shlen(ctx->codeIndexByName));"
    cleanup = "void VM_free(VMContext* ctx) {"
    code = replace_once(code, "VMContext* VM_create(DataWin* dataWin) {", table + "\nVMContext* VM_create(DataWin* dataWin) {")
    code = replace_once(code, create, "    bindNativeEntryCandidates(ctx);\n" + create)
    code = replace_once(code, cleanup, cleanup + "\n    if (nativeEntryOwner == ctx) {\n"
                        "        free(nativeEntryCandidates);\n        nativeEntryCandidates = nullptr;\n"
                        "        nativeEntryOwner = nullptr;\n    }")
    stripped = re.sub(r"^( +)return ([^;\n]+);$", r"\1*entryResult = \2;\n\1return true;", prelude, flags=re.M)
    prototype = ("static __attribute__((noinline)) bool tryNativeCodeEntry(\n"
                 "    VMContext* ctx, CodeEntry* code, Instance* selfInstance, Instance* otherInstance,\n"
                 "    int32_t eventObjectIndex, int32_t eventType, int32_t eventSubtype,\n"
                 "    RValue* args, int32_t argCount, RValue* entryResult) {")
    code = replace_once(code, ENTRY, prototype + stripped + "    return false;\n}\n\n" + ENTRY)
    call = """
    const bool mayHaveNativeEntry = nativeEntryOwner != ctx || nativeEntryCandidates[codeIndex] != 0;
    if (mayHaveNativeEntry) ++candidateEntryCalls;
    else ++ordinaryEntryCalls;
    if (CHECK_OR_CANDIDATE) {
        RValue entryResult;
        if (tryNativeCodeEntry(ctx, code, selfInstance, otherInstance, eventObjectIndex,
                               eventType, eventSubtype, args, argCount, &entryResult)) {
            require(mayHaveNativeEntry);
            ++nativeEntryReturns;
            return entryResult;
        }
    }

""".replace("CHECK_OR_CANDIDATE", "true" if checked else "mayHaveNativeEntry")
    return replace_once(code, locate + prelude + body, locate + "\n" + call + body)
