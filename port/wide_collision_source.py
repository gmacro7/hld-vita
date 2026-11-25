"""Retain collision order while rejecting 64 adjacent targets in one check."""

from collision_bounds_source import with_collision_bounds_blocks


def with_wide_collision_blocks(code, compare=False):
    code = with_collision_bounds_blocks(code)

    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    replace("enum { COLLISION_BLOCK_SIZE = 8 };",
            "enum { COLLISION_BLOCK_SIZE = 8, COLLISION_WIDE_SIZE = 64 };")
    helper = r'''
static void collisionMergeBlock(CollisionBoundsBlock* into, const CollisionBoundsBlock* block) {
    if (block->unbounded) into->unbounded = true;
    if (!block->hasBounds) return;
    if (!into->hasBounds) {
        const bool unbounded = into->unbounded;
        *into = *block;
        into->unbounded |= unbounded;
        return;
    }
    if (block->left < into->left) into->left = block->left;
    if (block->right > into->right) into->right = block->right;
    if (block->top < into->top) into->top = block->top;
    if (block->bottom > into->bottom) into->bottom = block->bottom;
}
'''
    if compare:
        helper += r'''
static unsigned wideCollisionSkips;

void hldReportWideCollision(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    const bool wide = frame > 180 && ((frame - 1) / 180) % 2 != 0;
    logInfo("Vita wide collision frame=%d enabled=%d wide_skips=%u\n",
            frame, (int)wide, wideCollisionSkips);
    wideCollisionSkips = 0;
}
'''
    anchor = "static const CollisionBoundsBlock* collisionBoundsBlocks(Runner* runner,"
    replace(anchor, helper + "\n" + anchor)
    replace("int32_t target, int32_t snapshotBase, int32_t count) {",
            "int32_t target, int32_t snapshotBase, int32_t count, bool wide) {")
    replace("    const int32_t blockCount = count / COLLISION_BLOCK_SIZE;",
            "    const int32_t blockCount = count / COLLISION_BLOCK_SIZE;\n"
            "    const int32_t wideCount = wide ? count / COLLISION_WIDE_SIZE : 0;")
    replace("        arrsetlen(entry->blocks, blockCount);",
            "        arrsetlen(entry->blocks, blockCount + wideCount);")
    anchor = "        entry->blockEpoch = runner->collisionDispatchEpoch;"
    replace(anchor, '''        const int32_t blocksPerWide = COLLISION_WIDE_SIZE / COLLISION_BLOCK_SIZE;
        for (int32_t index = 0; index < wideCount; ++index) {
            CollisionBoundsBlock group = {0};
            for (int32_t block = 0; block < blocksPerWide; ++block)
                collisionMergeBlock(&group, &entry->blocks[index * blocksPerWide + block]);
            entry->blocks[blockCount + index] = group;
        }
''' + anchor)
    mode = ("runner->frameCount >= 180 && (runner->frameCount / 180) % 2 != 0"
            if compare else "true")
    anchor = "    const bool enableBlocks = true;"
    replace(anchor, anchor + "\n    const bool enableWide = " + mode + ";")
    anchor = "collisionBoundsBlocks(runner, targetObjIndex, snapBase, snapEnd - snapBase)"
    replace(anchor, "collisionBoundsBlocks(runner, targetObjIndex, snapBase, snapEnd - snapBase, enableWide)")
    anchor = """                    if (blocks != nullptr && runner->collisionDispatchEpoch == blockEpoch &&
                        offset % COLLISION_BLOCK_SIZE == 0 && snapEnd - snapIdx >= COLLISION_BLOCK_SIZE &&
                        collisionBlockIsDistant(&blocks[offset / COLLISION_BLOCK_SIZE], dispatchSelf)) {"""
    replace(anchor, """                    int32_t skipCount = 0;
                    if (blocks != nullptr && runner->collisionDispatchEpoch == blockEpoch &&
                        offset % COLLISION_BLOCK_SIZE == 0 && snapEnd - snapIdx >= COLLISION_BLOCK_SIZE) {
                        const int32_t fineCount = (snapEnd - snapBase) / COLLISION_BLOCK_SIZE;
                        if (enableWide && offset % COLLISION_WIDE_SIZE == 0 && snapEnd - snapIdx >= COLLISION_WIDE_SIZE &&
                            collisionBlockIsDistant(&blocks[fineCount + offset / COLLISION_WIDE_SIZE], dispatchSelf)) {
                            skipCount = COLLISION_WIDE_SIZE;
""" + ("                            ++wideCollisionSkips;\n" if compare else "") + """                        } else if (collisionBlockIsDistant(&blocks[offset / COLLISION_BLOCK_SIZE], dispatchSelf)) {
                            skipCount = COLLISION_BLOCK_SIZE;
                        }
                    }
                    if (skipCount != 0) {""")
    replace("i < COLLISION_BLOCK_SIZE; ++i", "i < skipCount; ++i")
    replace("snapIdx += COLLISION_BLOCK_SIZE - 1;", "snapIdx += skipCount - 1;")
    return code
