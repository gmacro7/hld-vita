"""Reject distant line candidates before loading exact collision bounds."""

from pathlib import Path

from runtime_verification_source import replace_once


def with_line_envelopes(code, compare=False, launch_flag=None):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("line-envelope.inc").read_text()
    if launch_flag:
        assert compare
        helper = replace_once(helper, "    return frame >= 180 && (frame / 180) % 2 != 0;",
                              f"    extern bool {launch_flag};\n    (void)frame;\n    return {launch_flag};")
    helper = ("#define HLD_COMPARE_LINE_ENVELOPES\n" if compare else "#define HLD_VERIFY_LINE_ENVELOPES\n") + helper
    anchor = "// sprite_set_bbox_mode(sprite_index, mode)"
    replace(anchor, helper + "\n" + anchor)
    for anchor in (
            "    spr->bboxMode = mode;",
            "        ctx->dataWin->sprt.sprites[spriteIndex].marginBottom = (int32_t) RValue_toReal(args[4]);",
            "    ctx->dataWin->sprt.sprites[spriteIndex].originY = (int32_t) RValue_toReal(args[2]);"):
        replace(anchor, anchor + "\n" + anchor[:len(anchor) - len(anchor.lstrip())] +
                "hldReleaseLineEnvelopes(ctx->dataWin);")

    start = code.index("static RValue builtin_collision_line(")
    stop = code.index("\n}\n", start) + 3
    before = code[start:stop]
    anchor = "    int32_t resultId = INSTANCE_NOONE;"
    assert before.count(anchor) == 1
    after = before.replace(anchor, r'''    const LineEnvelopeQuery envelope = useLineEnvelopes(runner->frameCount)
        ? prepareLineEnvelope(ctx->dataWin, lx1, ly1, lx2, ly2) : (LineEnvelopeQuery){0};
''' + anchor)
    anchor = "        if (!Collision_lineOverlapsInstance(ctx->runner, inst, lx1, ly1, lx2, ly2)) continue;"
    assert after.count(anchor) == 1
    after = after.replace(anchor, r'''        if (missesLineEnvelope(&envelope, inst)) {
#ifdef HLD_VERIFY_LINE_ENVELOPES
            require(!Collision_lineOverlapsInstance(runner, inst, lx1, ly1, lx2, ly2));
            if (++lineEnvelopeVerified == 10000) logInfo("HLD: verified 10,000 exact line-query rejections\n");
#endif
#ifdef HLD_COMPARE_LINE_ENVELOPES
            ++lineEnvelopeSkips;
#endif
            continue;
        }
''' + anchor)
    replace(before, after)
    return code


def with_line_envelope_cleanup(code):
    anchor = "void VM_free(VMContext* ctx) {"
    assert code.count(anchor) == 1
    return code.replace(anchor, "void hldReleaseLineEnvelopes(const DataWin* data);\n\n" + anchor +
                        "\n    hldReleaseLineEnvelopes(ctx->dataWin);")


def with_ordered_line_candidates(code, *, verify=False):
    """Borrow the original target order and reuse bounds inside the pure query."""
    anchor = "static RValue builtin_collision_line("
    start = code.index(anchor)
    stop = code.index("\n}\n", start) + 3
    body = code[start:stop]
    snapshot = """    int32_t snapBase = Runner_pushInstancesForTarget(runner, targetObjIndex);
    int32_t snapEnd  = (int32_t) arrlen(runner->instanceSnapshots);
    for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {
        Instance* inst = runner->instanceSnapshots[snapIdx];"""
    body = replace_once(body, snapshot, """    const bool accelerated = useLineEnvelopes(runner->frameCount);
    int32_t snapBase = -1;
    Instance** candidates;
    size_t candidateCount;
    if (accelerated && targetObjIndex >= 0 && targetObjIndex < INSTANCE_ID_BASE &&
        (uint32_t)targetObjIndex < runner->dataWin->objt.count) {
        // The exact line test below never executes user code or mutates the buckets.
        candidates = runner->instancesByObject[targetObjIndex];
        candidateCount = arrlenu(candidates);
    } else {
        snapBase = Runner_pushInstancesForTarget(runner, targetObjIndex);
        candidateCount = arrlenu(runner->instanceSnapshots) - (size_t)snapBase;
        candidates = candidateCount ? runner->instanceSnapshots + snapBase : nullptr;
    }
    for (size_t candidate = 0; candidateCount > candidate; ++candidate) {
        Instance* inst = candidates[candidate];""")
    clip = """        if (!Collision_lineOverlapsInstance(ctx->runner, inst, lx1, ly1, lx2, ly2)) continue;
        InstanceBBox bbox = Collision_computeBBox(ctx->runner, inst);"""
    body = replace_once(body, clip, """        InstanceBBox bbox;
        Sprite* spr = nullptr;
        if (accelerated) {
            bbox = Collision_computeBBox(runner, inst);
            if (!bbox.valid) continue;
            if (envelope.sprites != nullptr && inst->imageAngle == 0.0 &&
                GMLReal_fabs(bbox.left) < 1048576 && GMLReal_fabs(bbox.right) < 1048576 &&
                GMLReal_fabs(bbox.top) < 1048576 && GMLReal_fabs(bbox.bottom) < 1048576 &&
                (bbox.right < envelope.left || bbox.left > envelope.right ||
                 bbox.bottom < envelope.top || bbox.top > envelope.bottom)) continue;
            spr = Collision_getSprite(ctx->dataWin, inst);
            if (!Collision_lineOverlapsBounds(spr, inst, bbox, lx1, ly1, lx2, ly2)) continue;
        } else {
            if (!Collision_lineOverlapsInstance(runner, inst, lx1, ly1, lx2, ly2)) continue;
            bbox = Collision_computeBBox(runner, inst);
        }""")
    body = replace_once(body, "        Sprite* spr = Collision_getSprite(ctx->dataWin, inst);\n",
                        "        if (!accelerated) spr = Collision_getSprite(ctx->dataWin, inst);\n")
    body = replace_once(body, "    Runner_popInstanceSnapshot(runner, snapBase);",
                        "    if (snapBase >= 0) Runner_popInstanceSnapshot(runner, snapBase);")
    if verify:
        # Compare hit identity for every query, including queries on moving,
        # scaled, rotated, or dynamically created sprites.
        original = code[start:stop]
        envelope_start = original.index("    const LineEnvelopeQuery envelope")
        envelope_end = original.index("    int32_t resultId", envelope_start)
        original = original[:envelope_start] + original[envelope_end:]
        filter_start = original.index("        if (missesLineEnvelope(")
        filter_end = original.index("        if (!Collision_lineOverlapsInstance(", filter_start)
        original = original[:filter_start] + original[filter_end:]
        original = original.replace(anchor, "static RValue hldOriginalLineQuery(", 1)
        body = body.replace(anchor, "static RValue hldOrderedLineQuery(", 1)
        body = original + "\n" + body + r'''
static RValue builtin_collision_line(VMContext* ctx, RValue* args, int32_t argCount) {
    static uint32_t checked;
    RValue result = hldOrderedLineQuery(ctx, args, argCount);
    RValue expected = hldOriginalLineQuery(ctx, args, argCount);
    require(result.type == expected.type && result.real == expected.real);
    if (++checked == 1000) logInfo("HLD: checked 1,000 ordered line queries\n");
    return result;
}
'''
    return code[:start] + body + code[stop:]


def with_line_work_inventory(code):
    """Count the work of the original builtin in the desktop replay."""
    anchor = "static RValue builtin_collision_line("
    start = code.index(anchor)
    stop = code.index("\n}\n", start) + 3
    body = code[start:stop]
    additions = {
        "    int32_t resultId = INSTANCE_NOONE;":
            "    LineWorkSample* sample = lineWorkSample(runner, targetObjIndex);",
        "        if (!Collision_lineOverlapsInstance(ctx->runner, inst, lx1, ly1, lx2, ly2)) continue;":
            "        if (sample) ++sample->candidates;",
        "        GMLReal cdx = xr - xl;":
            "        if (sample) ++sample->maskScans;",
        "                if (Collision_pointInInstance(spr, inst, px + 0.5, (GMLReal) py + 0.5)) {":
            "                if (sample) ++sample->pixels;",
        "                if (Collision_pointInInstance(spr, inst, (GMLReal) px + 0.5, py + 0.5)) {":
            "                if (sample) ++sample->pixels;",
    }
    for line, inserted in additions.items():
        body = replace_once(body, line, inserted + "\n" + line)
    support = r'''
typedef struct {
    unsigned calls, candidates, maskScans, pixels;
} LineWorkSample;

static LineWorkSample* lineWorkSamples;
static bool reportedLineWork;

static LineWorkSample* lineWorkSample(Runner* runner, int32_t target) {
    const int frame = runner->frameCount;
    if (frame < 540 || reportedLineWork) return nullptr;
    const uint32_t objects = runner->dataWin->objt.count;
    if (frame >= 720) {
        if (lineWorkSamples == nullptr) return nullptr;
        for (uint32_t i = 0; i <= objects; ++i) {
            const LineWorkSample* sample = &lineWorkSamples[i];
            if (sample->calls == 0) continue;
            const char* name = i < objects ? runner->dataWin->objt.objects[i].name : "<special>";
            logInfo("HLD line work target=%s calls=%u candidates=%u masks=%u pixels=%u\n",
                    name, sample->calls, sample->candidates, sample->maskScans, sample->pixels);
        }
        free(lineWorkSamples);
        lineWorkSamples = nullptr;
        reportedLineWork = true;
        return nullptr;
    }
    if (lineWorkSamples == nullptr)
        lineWorkSamples = safeCalloc((size_t)objects + 1, sizeof(*lineWorkSamples));
    LineWorkSample* sample = &lineWorkSamples[(uint32_t)target < objects ? (uint32_t)target : objects];
    ++sample->calls;
    return sample;
}
'''
    return code[:start] + support + body + code[stop:]
