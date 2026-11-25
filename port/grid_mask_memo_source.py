"""Reuse exact overlap results for pixel-aligned mask windows."""

from runtime_verification_source import replace_once


def with_grid_mask_memo(code, *, checked, compare=False, dynamic=False):
    anchor = "static inline bool Collision_tryGridMaskOverlap("
    start = code.index(anchor)
    scan_start = code.index("    for (; y < stopY; ++y) {", start)
    scan_end = code.index("\n    return true;\n}", scan_start)
    original_scan = code[scan_start:scan_end]
    scan = original_scan
    for side in ("A", "B"):
        old = (f"        const uint8_t* row{side} = {side.lower()}->mask != nullptr ? "
               f"{side.lower()}->mask + (size_t)((int64_t)y + y{side}) * {side.lower()}->stride : nullptr;\n")
        assert scan.count(old) == 1
        scan = scan.replace(old, "")
    scan = scan.replace("for (; y < stopY; ++y)", "for (uint32_t y = 0; y < window->height; ++y)")
    scan = scan.replace("        uint32_t col", """        const uint8_t* rowA = window->rowA ? window->rowA + (size_t)y * window->strideA : nullptr;
        const uint8_t* rowB = window->rowB ? window->rowB + (size_t)y * window->strideB : nullptr;
        uint32_t col""")
    scan = scan.replace("                *result = true;\n                return true;", "                return true;")
    assert "*result" not in scan
    helper = """
typedef struct {
    const uint8_t *rowA, *rowB;
    uint32_t strideA, strideB, firstA, firstB, width, height;
} CollisionMaskWindow;

typedef struct {
    CollisionMaskWindow window;
    bool overlaps;
} CollisionMaskMemo;

static CollisionMaskMemo collisionMaskMemo[256];
static unsigned collisionMaskMemoQueries, collisionMaskMemoHits;

static inline bool Collision_sameMaskWindow(const CollisionMaskWindow* a, const CollisionMaskWindow* b) {
    return a->rowA == b->rowA && a->rowB == b->rowB &&
           a->strideA == b->strideA && a->strideB == b->strideB &&
           a->firstA == b->firstA && a->firstB == b->firstB &&
           a->width == b->width && a->height == b->height;
}

static inline unsigned Collision_maskMemoSlot(const CollisionMaskWindow* window) {
    const uintptr_t a = (uintptr_t)window->rowA, b = (uintptr_t)window->rowB;
    uint32_t hash = (uint32_t)(a ^ (a >> 11)) * 31u + (uint32_t)(b ^ (b >> 11));
    hash = hash * 31u + window->width + window->height * 17u;
    hash = hash * 31u + window->firstA + window->firstB * 13u;
    return (hash ^ (hash >> 16)) & 255u;
}

static inline bool Collision_scanMaskWindow(const CollisionMaskWindow* window) {
    const uint32_t width = window->width, firstA = window->firstA, firstB = window->firstB;
""" + scan + """
    return false;
}

"""
    checked_hit = """
        require(memo->overlaps == Collision_scanMaskWindow(&window));""" if checked else ""
    use = """
    const CollisionMaskWindow window = {
        .rowA = a->mask ? a->mask + (size_t)((int64_t)y + yA) * a->stride + firstA / 8 : nullptr,
        .rowB = b->mask ? b->mask + (size_t)((int64_t)y + yB) * b->stride + firstB / 8 : nullptr,
        .strideA = a->stride, .strideB = b->stride,
        .firstA = firstA % 8, .firstB = firstB % 8,
        .width = width, .height = (uint32_t)(stopY - y),
    };
    const Sprt* sprites = &dataWin->sprt;
    // Sprite masks created by the game can be replaced or freed.
    if ((size_t)(a->sprite - sprites->sprites) >= sprites->parsedCount ||
        (size_t)(b->sprite - sprites->sprites) >= sprites->parsedCount) {
        *result = Collision_scanMaskWindow(&window);
        return true;
    }
    ++collisionMaskMemoQueries;
    CollisionMaskMemo* memo = &collisionMaskMemo[Collision_maskMemoSlot(&window)];
    if (Collision_sameMaskWindow(&memo->window, &window)) {
        ++collisionMaskMemoHits;""" + checked_hit + """
        *result = memo->overlaps;
        return true;
    }
    *result = Collision_scanMaskWindow(&window);
    *memo = (CollisionMaskMemo){ .window = window, .overlaps = *result };"""
    if dynamic:
        metrics = "static unsigned collisionMaskMemoQueries, collisionMaskMemoHits;"
        helper = replace_once(helper, metrics, metrics + """
extern uint64_t hldCollisionMaskEpoch;
static uint64_t collisionMaskMemoEpoch;
static unsigned dynamicMaskMemoQueries, dynamicMaskMemoHits;
""")
        old = """
    // Sprite masks created by the game can be replaced or freed.
    if ((size_t)(a->sprite - sprites->sprites) >= sprites->parsedCount ||
        (size_t)(b->sprite - sprites->sprites) >= sprites->parsedCount) {
        *result = Collision_scanMaskWindow(&window);
        return true;
    }"""
        use = replace_once(use, old, """
    // Dynamic sprite masks keep their pixel data until a registered
    // mutation or deletion advances the epoch.
    if (collisionMaskMemoEpoch != hldCollisionMaskEpoch) {
        memset(collisionMaskMemo, 0, sizeof(collisionMaskMemo));
        collisionMaskMemoEpoch = hldCollisionMaskEpoch;
    }
    const bool generated = (size_t)(a->sprite - sprites->sprites) >= sprites->parsedCount ||
        (size_t)(b->sprite - sprites->sprites) >= sprites->parsedCount;
    dynamicMaskMemoQueries += generated;""")
        use = replace_once(use, "        ++collisionMaskMemoHits;", "        ++collisionMaskMemoHits;\n"
                           "        dynamicMaskMemoHits += generated;")
    if compare:
        helper = "extern bool hldGridMaskMemo;\n" + helper
        cached = "\n".join("    " + line for line in use.strip("\n").splitlines())
        use = "    if (hldGridMaskMemo) {\n" + cached + "\n        return true;\n    }\n" + original_scan
    code = code[:scan_start] + use.strip("\n") + code[scan_end:]
    code = code[:start] + helper + code[start:]
    code = code.replace(anchor, anchor + "const DataWin* dataWin, ", 1)
    call = "Collision_tryGridMaskOverlap(&queryA, &queryB,"
    assert code.count(call) == 1
    return code.replace(call, "Collision_tryGridMaskOverlap(dataWin, &queryA, &queryB,", 1)


def with_grid_mask_memo_runner(code, *, compare=False, dynamic=False):
    anchor = "void Runner_free(Runner* runner) {"
    assert code.count(anchor) == 1
    report = """
void hldReportGridMaskMemo(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita grid mask memo frame=%d enabled=%d hits=%u queries=%u\\n",
            frame, (int)hldGridMaskMemo, collisionMaskMemoHits, collisionMaskMemoQueries);
    collisionMaskMemoHits = collisionMaskMemoQueries = 0;
}

""" if compare else ""
    exit_report = "" if compare else """
    logInfo("HLD: grid mask memo hits=%u queries=%u\\n", collisionMaskMemoHits, collisionMaskMemoQueries);"""
    if dynamic:
        report += """uint64_t hldCollisionMaskEpoch = 1;
void hldReportBuiltinDynamicMasks(void);

"""
        if compare:
            report = "void hldReadBuiltinMaskReuse(unsigned* hits, unsigned* queries);\n\n" + report
            reset = "    collisionMaskMemoHits = collisionMaskMemoQueries = 0;"
            report = replace_once(report, reset, r'''    unsigned builtinHits, builtinQueries;
    hldReadBuiltinMaskReuse(&builtinHits, &builtinQueries);
    logInfo("Vita generated mask reuse frame=%d enabled=%d hits=%u queries=%u\n",
            frame, (int)hldGridMaskMemo, dynamicMaskMemoHits + builtinHits,
            dynamicMaskMemoQueries + builtinQueries);
    dynamicMaskMemoHits = dynamicMaskMemoQueries = 0;
''' + reset)
        exit_report += r'''
    ++hldCollisionMaskEpoch;
    logInfo("HLD: dynamic grid mask memo runner hits=%u queries=%u\n", dynamicMaskMemoHits, dynamicMaskMemoQueries);
    hldReportBuiltinDynamicMasks();'''
    end = code.index("\n", code.index("    if (runner == nullptr) return;", code.index(anchor)))
    code = code[:end] + exit_report + """
    memset(collisionMaskMemo, 0, sizeof(collisionMaskMemo));
    collisionMaskMemoHits = collisionMaskMemoQueries = 0;""" + code[end:]
    return code.replace(anchor, report + anchor, 1)


def with_dynamic_mask_memo_builtins(code, *, compare=False):
    anchor = "static RValue builtin_sprite_collision_mask("
    helper = r'''void hldReportBuiltinDynamicMasks(void) {
    logInfo("HLD: dynamic grid mask memo builtins hits=%u queries=%u\n", dynamicMaskMemoHits, dynamicMaskMemoQueries);
}

'''
    if compare:
        helper += r'''void hldReadBuiltinMaskReuse(unsigned* hits, unsigned* queries) {
    *hits = dynamicMaskMemoHits;
    *queries = dynamicMaskMemoQueries;
    dynamicMaskMemoHits = dynamicMaskMemoQueries = 0;
}

void hldReportBuiltinMaskOverlap(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita builtin split masks frame=%d enabled=%d calls=%u\n",
            frame, (int)hldBuiltinMaskOverlap, builtinSplitMaskCalls);
    builtinSplitMaskCalls = 0;
}

'''
    code = replace_once(code, anchor, helper + anchor)
    anchor = "    sprite->sepMasks = 1;"
    return replace_once(code, anchor, "    ++hldCollisionMaskEpoch;\n" + anchor)


def with_dynamic_mask_memo_renderer(code):
    anchor = "        repeat(sprite->maskCount, i) free(sprite->masks[i]);"
    code = replace_once(code, anchor, "        ++hldCollisionMaskEpoch;\n" + anchor)
    return "#include <stdint.h>\nextern uint64_t hldCollisionMaskEpoch;\n" + code


def with_builtin_mask_overlap_trial(code):
    anchor = "static inline bool Collision_instancesOverlapPrecise("
    code = replace_once(code, anchor, r'''#ifdef HLD_BUILTIN_MASK_OVERLAP_TRIAL
extern bool hldBuiltinMaskOverlap;
static unsigned builtinSplitMaskCalls;
#endif

''' + anchor)
    start = code.index(anchor)
    predicate = "#ifdef HLD_SPLIT_MASK_OVERLAP\n    if (Collision_canSplitMaskAxes"
    assert code.count(predicate, start) == 1
    replacement = r'''#ifdef HLD_SPLIT_MASK_OVERLAP
#ifdef HLD_BUILTIN_MASK_OVERLAP_TRIAL
    if (!hldBuiltinMaskOverlap)
        return Collision_referenceMaskOverlap(&queryA, &queryB, startX, startY, endX, endY, compatMode, sampleOffset);
    ++builtinSplitMaskCalls;
#endif
    if (Collision_canSplitMaskAxes'''
    return code[:start] + code[start:].replace(predicate, replacement, 1)
