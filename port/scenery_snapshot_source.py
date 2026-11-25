"""Retain the filtered scenery snapshot between unchanged with-block entries."""

from runtime_verification_source import replace_once


SNAPSHOT = r'''
typedef struct {
    VMContext* owner;
    Instance** items;
    int32_t count, target;
    bool valid, borrowed, disabled;
} ScenerySnapshot;

static ScenerySnapshot scenerySnapshot;
static unsigned scenerySnapshotBuilds, scenerySnapshotHits, scenerySnapshotPointers;

static void bindScenerySnapshot(VMContext* ctx, const uint8_t* step) {
    const OpWord target = {BinaryUtils_readUint32Aligned(step + 0x03b8)};
    if (OpWord_opcode(target) != OP_PUSH_IMMEDIATE || OpWord_type1(target) != GML_TYPE_INT16) return;
    const int32_t object = OpWord_extra(target);
    if (object < 0 || (uint32_t)object >= ctx->dataWin->objt.count) return;
    const char* name = ctx->dataWin->objt.objects[object].name;
    if (name == nullptr || strcmp(name, "Scenery") != 0) return;
    // A different VM uses the ordinary snapshot while the first owns the cache.
    if (scenerySnapshot.owner != nullptr) return;
    scenerySnapshot.owner = ctx;
    scenerySnapshot.target = object;
}

void hldInvalidateScenerySnapshot(const Runner* runner, int32_t target) {
    if (scenerySnapshot.valid && scenerySnapshot.owner->runner == runner &&
        (target < 0 || target == scenerySnapshot.target)) scenerySnapshot.valid = false;
}

void hldInvalidateScenerySnapshotActive(const Runner* runner, const Instance* inst) {
    if (!scenerySnapshot.valid || scenerySnapshot.owner->runner != runner) return;
    int32_t object = inst->objectIndex;
    // Follow the same parent chain used when adding the instance to object buckets.
    for (int depth = 0; depth < 32 && object >= 0 && (uint32_t)object < runner->dataWin->objt.count; ++depth) {
        if (object == scenerySnapshot.target) {
            scenerySnapshot.valid = false;
            return;
        }
        object = runner->dataWin->objt.objects[object].parentId;
    }
}

void hldDisableScenerySnapshot(const Runner* runner) {
    if (scenerySnapshot.owner != nullptr && scenerySnapshot.owner->runner == runner) {
        scenerySnapshot.valid = false;
        scenerySnapshot.disabled = true;
    }
}

static bool takeScenerySnapshot(VMContext* ctx, EnvFrame* frame, Instance** source, int32_t target) {
    if (SCENERY_SNAPSHOT_DISABLED scenerySnapshot.owner != ctx || scenerySnapshot.target != target ||
        scenerySnapshot.borrowed || scenerySnapshot.disabled) return false;
    const size_t size = arrlenu(source);
    if (size <= VM_INLINE_ENV_INSTANCES || size > ENV_SNAPSHOT_MAX_RETAINED) return false;

    if (!scenerySnapshot.valid) {
        if (scenerySnapshot.items != nullptr) releaseEnvSnapshot(ctx, scenerySnapshot.items);
        snapshotEnvInstances(ctx, frame, source, true);
        scenerySnapshot.items = frame->instanceList;
        scenerySnapshot.count = frame->instanceCount;
        scenerySnapshot.valid = true;
        ++scenerySnapshotBuilds;
    } else {
        SCENERY_SNAPSHOT_CHECK
        ++scenerySnapshotHits;
        scenerySnapshotPointers += (unsigned)scenerySnapshot.count;
    }
    frame->instanceList = scenerySnapshot.items;
    frame->instanceCount = scenerySnapshot.count;
    scenerySnapshot.borrowed = true;
    return true;
}

static bool returnScenerySnapshot(VMContext* ctx, const EnvFrame* frame) {
    if (scenerySnapshot.owner != ctx || scenerySnapshot.items != frame->instanceList) return false;
    require(scenerySnapshot.borrowed);
    scenerySnapshot.borrowed = false;
    return true;
}

void hldReportScenerySnapshots(int frame) {
    if (frame >= 0 && (frame == 0 || frame % 90 != 0)) return;
    logInfo("HLD scenery snapshot frame=%d builds=%u reused=%u pointers=%u\n",
            frame, scenerySnapshotBuilds, scenerySnapshotHits, scenerySnapshotPointers);
    scenerySnapshotBuilds = scenerySnapshotHits = scenerySnapshotPointers = 0;
}

static void clearScenerySnapshot(VMContext* ctx) {
    if (scenerySnapshot.owner != ctx) return;
#ifndef PLATFORM_VITA
    hldReportScenerySnapshots(-1);
#endif
    if (scenerySnapshot.items != nullptr) releaseEnvSnapshot(ctx, scenerySnapshot.items);
    scenerySnapshot = (ScenerySnapshot){0};
}
'''


def with_scenery_snapshot(code, *, checked=False, pool_flag=None):
    check = r'''EnvFrame reference = {0};
        snapshotEnvInstances(ctx, &reference, source, true);
        require(reference.instanceCount == scenerySnapshot.count);
        require(memcmp(reference.instanceList, scenerySnapshot.items,
                       (size_t)reference.instanceCount * sizeof(*scenerySnapshot.items)) == 0);
        releaseEnvSnapshot(ctx, reference.instanceList);'''
    helper = SNAPSHOT.replace("        SCENERY_SNAPSHOT_CHECK\n", "        " + check + "\n" if checked else "")
    helper = helper.replace("SCENERY_SNAPSHOT_DISABLED ", f"!{pool_flag} || " if pool_flag else "")
    anchor = "static void handlePushEnv(VMContext* ctx, OpWord opword, uint32_t instrAddr) {"
    code = replace_once(code, anchor, helper + "\n" + anchor)
    anchor = "            snapshotEnvInstances(ctx, frame, runner->instancesByObject[target], true);"
    code = replace_once(code, anchor, '''            Instance** source = runner->instancesByObject[target];
            if (!takeScenerySnapshot(ctx, frame, source, target))
                snapshotEnvInstances(ctx, frame, source, true);''')
    anchor = "    if (frame->instanceList != nullptr && frame->instanceList != frame->inlineInstances) {"
    code = replace_once(code, anchor, '''    if (frame->instanceList != nullptr && frame->instanceList != frame->inlineInstances &&
        !returnScenerySnapshot(ctx, frame)) {''')
    code = replace_once(code, "    sceneryPass = bound;", "    sceneryPass = bound;\n    bindScenerySnapshot(ctx, step);")
    anchor = "    clearEnvSnapshotPool(ctx);"
    return replace_once(code, anchor, "    clearScenerySnapshot(ctx);\n" + anchor)


def with_scenery_snapshot_runner(code):
    anchor = "void Runner_addInstanceToObjectLists(Runner* runner, Instance* inst) {"
    code = replace_once(code, anchor, '''void hldInvalidateScenerySnapshot(const Runner*, int32_t);
void hldInvalidateScenerySnapshotActive(const Runner*, const Instance*);

''' + anchor)
    for anchor in ("        arrput(runner->instancesByObject[currentObj], inst);",
                   "        removeInstanceFromList(&runner->instancesByObject[currentObj], inst);"):
        code = replace_once(code, anchor, "        hldInvalidateScenerySnapshot(runner, currentObj);\n" + anchor)
    anchor = "void Runner_clearAllObjectLists(Runner* runner) {"
    code = replace_once(code, anchor, anchor + "\n    hldInvalidateScenerySnapshot(runner, -1);")
    anchor = "    inst->active = false;"
    return replace_once(code, anchor, "    if (inst->active) hldInvalidateScenerySnapshotActive(runner, inst);\n" + anchor)


def with_scenery_snapshot_builtins(code):
    anchor = "static RValue builtin_instance_deactivate_all("
    assert code.count("Runner_setActiveState(") == 7
    code = code.replace("Runner_setActiveState(", "setSnapshotActiveState(")
    code = replace_once(code, anchor, '''void hldInvalidateScenerySnapshotActive(const Runner*, const Instance*);
void hldDisableScenerySnapshot(const Runner*);

static void setSnapshotActiveState(Runner* runner, Instance* instance, bool active) {
    if (instance->active != active) hldInvalidateScenerySnapshotActive(runner, instance);
    Runner_setActiveState(runner, instance, active);
}

''' + anchor)
    anchor = "        ctx->dataWin->objt.objects[id].parentId = parentId;"
    return replace_once(code, anchor, '''        // Older object buckets keep their original membership when a parent changes.
        if (ctx->dataWin->objt.objects[id].parentId != parentId) hldDisableScenerySnapshot(ctx->runner);
''' + anchor)
