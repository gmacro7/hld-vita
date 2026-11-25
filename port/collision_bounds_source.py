"""Try skipping groups of distant collision targets without changing event order."""


def with_collision_bounds_blocks(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    replace("typedef struct CollisionTargetSnapshot {", """enum { COLLISION_BLOCK_SIZE = 8 };

typedef struct {
    GMLReal left, right, top, bottom;
    bool hasBounds;
    bool unbounded;
} CollisionBoundsBlock;

typedef struct CollisionTargetSnapshot {""")
    replace("    uint64_t revision;\n} CollisionTargetSnapshot;",
            """    uint64_t revision;
    uint64_t blockEpoch;
    CollisionBoundsBlock* blocks;
} CollisionTargetSnapshot;""")
    helpers = r'''
static const CollisionBoundsBlock* collisionBoundsBlocks(Runner* runner,
        int32_t target, int32_t snapshotBase, int32_t count) {
    if (count < COLLISION_BLOCK_SIZE * 2 || target < 0 ||
        (uint32_t)target >= runner->dataWin->objt.count) return nullptr;
    CollisionTargetSnapshot* entry = &runner->collisionTargets[target];
    const int32_t blockCount = count / COLLISION_BLOCK_SIZE;
    if (entry->blockEpoch != runner->collisionDispatchEpoch) {
        arrsetlen(entry->blocks, blockCount);
        for (int32_t blockIndex = 0; blockIndex < blockCount; ++blockIndex) {
            CollisionBoundsBlock block = {0};
            const int32_t start = snapshotBase + blockIndex * COLLISION_BLOCK_SIZE;
            for (int32_t offset = 0; offset < COLLISION_BLOCK_SIZE; ++offset) {
                Instance* targetInstance = runner->instanceSnapshots[start + offset];
                if (!targetInstance->active) continue;
                const InstanceBBox* bounds = collisionDispatchBounds(runner, targetInstance);
                if (bounds == nullptr) continue;
                if (!isfinite(bounds->left) || !isfinite(bounds->right) ||
                    !isfinite(bounds->top) || !isfinite(bounds->bottom)) {
                    block.unbounded = true;
                    break;
                }
                if (!block.hasBounds) {
                    block.left = bounds->left;
                    block.right = bounds->right;
                    block.top = bounds->top;
                    block.bottom = bounds->bottom;
                    block.hasBounds = true;
                } else {
                    if (bounds->left < block.left) block.left = bounds->left;
                    if (bounds->right > block.right) block.right = bounds->right;
                    if (bounds->top < block.top) block.top = bounds->top;
                    if (bounds->bottom > block.bottom) block.bottom = bounds->bottom;
                }
            }
            entry->blocks[blockIndex] = block;
        }
        entry->blockEpoch = runner->collisionDispatchEpoch;
    }
    return entry->blocks;
}

static inline bool collisionBlockIsDistant(const CollisionBoundsBlock* block, const InstanceBBox* bounds) {
    return !block->unbounded && (!block->hasBounds || bounds->left >= block->right ||
          block->left >= bounds->right || bounds->top >= block->bottom || block->top >= bounds->bottom);
}

'''
    if compare:
        helpers += r'''
static bool blockTrialInitialized;
static bool useCollisionBoundsBlocks;
static unsigned collisionSkippedBlocks;

static bool collisionBoundsTrialEnabled(void) {
    if (!blockTrialInitialized) {
        FILE* flag = fopen("ux0:data/hld-vm/collision-bounds-blocks", "r");
        useCollisionBoundsBlocks = flag != nullptr;
        if (flag != nullptr) fclose(flag);
        blockTrialInitialized = true;
    }
    return useCollisionBoundsBlocks;
}

void hldReportCollisionBlocks(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita collision blocks frame=%d enabled=%d skipped=%u\n",
            frame, (int)useCollisionBoundsBlocks, collisionSkippedBlocks);
    collisionSkippedBlocks = 0;
}

'''
    replace("static void dispatchCollisionEvents(Runner* runner) {",
            helpers + "static void dispatchCollisionEvents(Runner* runner) {")
    if compare:
        replace("    bool rejectDistantPairs = true;",
                "    const bool enableBlocks = collisionBoundsTrialEnabled();\n    bool rejectDistantPairs = true;")
    else:
        replace("    bool rejectDistantPairs = true;",
                "    const bool enableBlocks = true;\n    bool rejectDistantPairs = true;")
    anchor = """                int32_t snapEnd = (int32_t) arrlen(runner->instanceSnapshots);

                for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {
                    Instance* other = runner->instanceSnapshots[snapIdx];"""
    replace(anchor, """                int32_t snapEnd = (int32_t) arrlen(runner->instanceSnapshots);
                const uint64_t blockEpoch = runner->collisionDispatchEpoch;
                const CollisionBoundsBlock* blocks = enableBlocks && rejectDistantPairs
                    ? collisionBoundsBlocks(runner, targetObjIndex, snapBase, snapEnd - snapBase) : nullptr;

                for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {
                    const int32_t offset = snapIdx - snapBase;
                    if (blocks != nullptr && runner->collisionDispatchEpoch == blockEpoch &&
                        offset % COLLISION_BLOCK_SIZE == 0 && snapEnd - snapIdx >= COLLISION_BLOCK_SIZE &&
                        collisionBlockIsDistant(&blocks[offset / COLLISION_BLOCK_SIZE], dispatchSelf)) {
#ifdef HLD_VERIFY_COLLISION_BLOCKS
                        for (int32_t i = 0; i < COLLISION_BLOCK_SIZE; ++i) {
                            Instance* skipped = runner->instanceSnapshots[snapIdx + i];
                            if (!skipped->active || skipped == self) continue;
                            const InstanceBBox* bounds = collisionDispatchBounds(runner, skipped);
                            require(bounds == nullptr || dispatchSelf->left >= bounds->right ||
                                    bounds->left >= dispatchSelf->right || dispatchSelf->top >= bounds->bottom ||
                                    bounds->top >= dispatchSelf->bottom);
                        }
#endif
""" + ("                        ++collisionSkippedBlocks;\n" if compare else "") + """                        snapIdx += COLLISION_BLOCK_SIZE - 1;
                        continue;
                    }
                    Instance* other = runner->instanceSnapshots[snapIdx];""")
    replace("        repeat(runner->dataWin->objt.count, i) arrfree(runner->collisionTargets[i].sorted);",
            """        repeat(runner->dataWin->objt.count, i) {
            arrfree(runner->collisionTargets[i].sorted);
            arrfree(runner->collisionTargets[i].blocks);
        }""")
    return code


def with_resumed_collision_blocks(code, compare=False):
    """Refresh stale bounds after a callback if the live list still matches its snapshot."""
    code = with_collision_bounds_blocks(code)

    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    if compare:
        anchor = "static void dispatchCollisionEvents(Runner* runner) {"
        replace(anchor, r'''static unsigned collisionBlockRefreshes;

void hldReportCollisionRefresh(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    const bool enabled = frame > 180 && ((frame - 1) / 180) % 2 != 0;
    logInfo("Vita collision refresh frame=%d enabled=%d refreshed=%u\n",
            frame, (int)enabled, collisionBlockRefreshes);
    collisionBlockRefreshes = 0;
}

''' + anchor)

    mode = ("runner->frameCount >= 180 && (runner->frameCount / 180) % 2 != 0"
            if compare else "true")
    anchor = "    const bool enableBlocks = true;"
    replace(anchor, anchor + "\n    const bool refreshBlocks = " + mode + ";")
    replace("                const uint64_t blockEpoch = runner->collisionDispatchEpoch;",
            """                uint64_t blockEpoch = runner->collisionDispatchEpoch;
                const uint64_t snapshotRevision = runner->instanceListRevision;""")
    anchor = "                    if (rejectDistantPairs) dispatchSelf = collisionDispatchBounds(runner, self);"
    replace(anchor, anchor + """
                    // A changed list could cache bounds for different instances than the next snapshot.
                    // Wait until the next whole block before deciding that rebuilding is worthwhile.
                    const int32_t nextBlock = (snapIdx - snapBase) / COLLISION_BLOCK_SIZE + 1;
                    if (refreshBlocks && blocks != nullptr && dispatchSelf != nullptr &&
                        runner->instanceListRevision == snapshotRevision &&
                        (snapEnd - snapBase) / COLLISION_BLOCK_SIZE > nextBlock) {
                        blocks = collisionBoundsBlocks(runner, targetObjIndex, snapBase, snapEnd - snapBase);
                        blockEpoch = runner->collisionDispatchEpoch;
""" + ("                        ++collisionBlockRefreshes;\n" if compare else "") + "                    }")
    return code
