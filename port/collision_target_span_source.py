"""Reuse the frozen collision target list for the current dispatch."""


def with_collision_target_span(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    anchor = "static int32_t pushSortedCollisionTargets(Runner* runner, int32_t target) {"
    support = ""
    if compare:
        support = r'''
static unsigned borrowedCollisionLists, borrowedCollisionPointers;

static inline bool borrowCollisionTargets(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportCollisionTargets(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita collision targets frame=%d enabled=%d borrowed=%u pointers=%u\n",
            frame, (int)borrowCollisionTargets(frame - 1), borrowedCollisionLists, borrowedCollisionPointers);
    borrowedCollisionLists = borrowedCollisionPointers = 0;
}

'''
    replace(anchor, support + """static int32_t pushSortedCollisionTargets(Runner* runner, int32_t target,
                                        bool allowBorrow, Instance* const** borrowed) {""")
    anchor = "    // An in-flight snapshot remains valid if a handler spawns, destroys, or"
    replace(anchor, r'''    // The sorted array is owned by this dispatcher. Callbacks can change
    // the live lists, but only the next dispatch refreshes this sorted entry.
    // A single-instance list still uses the ordinary snapshot path.
    if (allowBorrow && count >= COLLISION_WIDE_SIZE) {
        *borrowed = source;
#ifdef HLD_VERIFY_COLLISION_TARGET_ORDER
        const int32_t check = Runner_pushInstancesOfObject(runner, target);
        require(arrlenu(runner->instanceSnapshots) - check == count);
        qsort(runner->instanceSnapshots + check, count, sizeof(Instance*),
              sortInstancesByObjectIndexThenInstanceIdAscending);
        require(memcmp(source, runner->instanceSnapshots + check, count * sizeof(Instance*)) == 0);
        Runner_popInstanceSnapshot(runner, check);
#endif
''' + ("        ++borrowedCollisionLists;\n        borrowedCollisionPointers += (unsigned)count;\n" if compare else "") +
            "        return base;\n    }\n\n" + anchor)

    replace("int32_t target, int32_t snapshotBase, int32_t count, bool wide) {",
            "int32_t target, Instance* const* targets, int32_t count, bool wide) {")
    replace("            const int32_t start = snapshotBase + blockIndex * COLLISION_BLOCK_SIZE;",
            "            const int32_t start = blockIndex * COLLISION_BLOCK_SIZE;")
    replace("                Instance* targetInstance = runner->instanceSnapshots[start + offset];",
            "                Instance* targetInstance = targets[start + offset];")

    mode = "borrowCollisionTargets(runner->frameCount)" if compare else "true"
    replace("    const bool enableWide = true;", "    const bool enableWide = true;\n"
            f"    const bool allowBorrow = {mode};")
    replace("""                int32_t snapBase = pushSortedCollisionTargets(runner, targetObjIndex);
                int32_t snapEnd = (int32_t) arrlen(runner->instanceSnapshots);""", """                Instance* const* borrowed = nullptr;
                int32_t snapBase = pushSortedCollisionTargets(runner, targetObjIndex, allowBorrow, &borrowed);
                const int32_t count = borrowed != nullptr ? (int32_t)arrlen(borrowed)
                    : (int32_t)arrlen(runner->instanceSnapshots) - snapBase;
                if (count == 0) continue;
                int32_t snapEnd = snapBase + count;""")
    replace("? collisionBoundsBlocks(runner, targetObjIndex, snapBase, snapEnd - snapBase, enableWide) : nullptr;",
            "? collisionBoundsBlocks(runner, targetObjIndex,\n"
            "                        borrowed != nullptr ? borrowed : runner->instanceSnapshots + snapBase, count, enableWide) : nullptr;")
    replace("Instance* skipped = runner->instanceSnapshots[snapIdx + i];",
            "Instance* skipped = borrowed != nullptr ? borrowed[offset + i] : runner->instanceSnapshots[snapIdx + i];")
    replace("                    Instance* other = runner->instanceSnapshots[snapIdx];",
            "                    Instance* other = borrowed != nullptr ? borrowed[offset] : runner->instanceSnapshots[snapIdx];")
    return code
