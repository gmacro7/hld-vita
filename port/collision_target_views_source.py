"""Reuse ordered target lists until their membership changes."""

from runtime_verification_source import replace_once


TARGET_VIEWS = r'''
typedef struct {
    Instance* const* items;
    int32_t base, count;
    bool borrowed;
} CollisionTargetView;

static unsigned targetSorts, targetSortsAvoided, targetBorrows, targetFreezes, targetPointersCopied;

static inline void invalidateCollisionTargetOrder(Runner* runner, int32_t object) {
    if (runner->collisionTargets != nullptr) runner->collisionTargets[object].membershipDirty = true;
}

static void materializeCollisionTargets(Runner* runner, CollisionTargetView* view) {
    arrsetlen(runner->instanceSnapshots, view->base + view->count);
    memcpy(runner->instanceSnapshots + view->base, view->items, (size_t)view->count * sizeof(*view->items));
    targetPointersCopied += (unsigned)view->count;
    view->items = runner->instanceSnapshots + view->base;
    view->borrowed = false;
}

static CollisionTargetView collisionTargetView(Runner* runner, int32_t target) {
    CollisionTargetView view = {.base = (int32_t)arrlen(runner->instanceSnapshots)};
    if (target < 0 || (uint32_t)target >= runner->dataWin->objt.count) {
        Runner_pushInstancesOfObject(runner, target);
        view.count = (int32_t)arrlen(runner->instanceSnapshots) - view.base;
        Instance** copied = runner->instanceSnapshots + view.base;
        if (view.count > 1)
            qsort(copied, (size_t)view.count, sizeof(*copied), sortInstancesByObjectIndexThenInstanceIdAscending);
        view.items = copied;
        targetPointersCopied += (unsigned)view.count;
        return view;
    }
    Instance** source = runner->instancesByObject[target];
    const size_t count = arrlenu(source);
    if (count == 0) return view;
    const bool borrow = HLD_COLLISION_TARGET_VIEWS;
    if (count > 1) {
        CollisionTargetSnapshot* entry = &runner->collisionTargets[target];
        const bool otherBucketChanged = entry->revision != runner->instanceListRevision;
        const bool needsSort = borrow ? entry->membershipDirty : otherBucketChanged;
        if (needsSort || arrlenu(entry->sorted) != count) {
            arrsetlen(entry->sorted, count);
            memcpy(entry->sorted, source, count * sizeof(*source));
            qsort(entry->sorted, count, sizeof(*source), sortInstancesByObjectIndexThenInstanceIdAscending);
            entry->membershipDirty = false;
            ++targetSorts;
        } else if (borrow && otherBucketChanged) {
            ++targetSortsAvoided;
        }
        entry->revision = runner->instanceListRevision;
        source = entry->sorted;
    }
    HLD_CHECK_TARGET_ORDER
    view.count = (int32_t)count;
    view.items = source;
    if (borrow) {
        view.borrowed = true;
        ++targetBorrows;
    } else {
        materializeCollisionTargets(runner, &view);
    }
    return view;
}
'''


VERIFY_ORDER = r'''
    // A fresh legacy snapshot verifies count, membership and order.
    const int32_t check = Runner_pushInstancesOfObject(runner, target);
    require(arrlenu(runner->instanceSnapshots) - (size_t)check == count);
    Instance** sorted = runner->instanceSnapshots + check;
    qsort(sorted, count, sizeof(*sorted), sortInstancesByObjectIndexThenInstanceIdAscending);
    require(memcmp(source, sorted, count * sizeof(*source)) == 0);
    Runner_popInstanceSnapshot(runner, check);
'''


def with_collision_target_views(code, *, launch_flag=None, checked=False):
    anchor = "void Runner_addInstanceToObjectLists(Runner* runner, Instance* inst) {"
    code = replace_once(code, anchor, "static inline void invalidateCollisionTargetOrder(Runner*, int32_t);\n\n" + anchor)
    for operation in ("arrput(runner->instancesByObject[currentObj], inst);",
                      "removeInstanceFromList(&runner->instancesByObject[currentObj], inst);"):
        anchor = "        " + operation
        code = replace_once(code, anchor, anchor + "\n        invalidateCollisionTargetOrder(runner, currentObj);")
    anchor = "        arrsetlen(runner->instancesByObject[i], 0);"
    code = replace_once(code, anchor, anchor + "\n        invalidateCollisionTargetOrder(runner, i);")
    anchor = "    uint64_t revision;\n    uint64_t blockEpoch;"
    code = replace_once(code, anchor, anchor + "\n    bool membershipDirty;")
    old = code.index("static int32_t pushSortedCollisionTargets(")
    end = code.index("static inline const InstanceBBox* collisionDispatchBounds(", old)
    helpers = TARGET_VIEWS.replace("HLD_COLLISION_TARGET_VIEWS", launch_flag or "true")
    helpers = helpers.replace("    HLD_CHECK_TARGET_ORDER\n", VERIFY_ORDER if checked else "")
    if launch_flag:
        helpers = f"extern bool {launch_flag};\n" + helpers
    helpers += r'''
void hldReportCollisionTargetViews(int frame) {
    REPORT_GUARD
    logInfo("HLD collision target views frame=%d sorts=%u retained=%u borrowed=%u frozen=%u copied=%u\n",
            frame, targetSorts, targetSortsAvoided, targetBorrows, targetFreezes, targetPointersCopied);
    targetSorts = targetSortsAvoided = targetBorrows = targetFreezes = targetPointersCopied = 0;
}

'''
    guard = "if (frame <= 0 || frame % 90 != 0) return;" if launch_flag else ""
    code = code[:old] + helpers.replace("REPORT_GUARD", guard) + code[end:]
    # The bounds walker uses the same order and members as the dispatch loop.
    code = replace_once(code, "int32_t snapshotBase, int32_t count, bool wide)",
                        "Instance* const* candidates, int32_t count, bool wide)")
    code = replace_once(code, "const int32_t start = snapshotBase + blockIndex * COLLISION_BLOCK_SIZE;",
                        "const int32_t start = blockIndex * COLLISION_BLOCK_SIZE;")
    code = replace_once(code, "Instance* targetInstance = runner->instanceSnapshots[start + offset];",
                        "Instance* targetInstance = candidates[start + offset];")

    start = code.index("                int32_t snapBase = pushSortedCollisionTargets(runner, targetObjIndex);")
    end = code.index("                    int32_t skipCount = 0;", start)
    code = code[:start] + '''                CollisionTargetView view = collisionTargetView(runner, targetObjIndex);
                const uint64_t blockEpoch = runner->collisionDispatchEpoch;
                const CollisionBoundsBlock* blocks = enableBlocks && rejectDistantPairs
                    ? collisionBoundsBlocks(runner, targetObjIndex, view.items, view.count, enableWide) : nullptr;

                for (int32_t offset = 0; view.count > offset; ++offset) {
''' + code[end:]
    anchor = "                for (int32_t offset = 0; view.count > offset; ++offset) {"
    start = code.index(anchor)
    end = code.index("                Runner_popInstanceSnapshot(runner, snapBase);", start)
    walk = code[start:end]
    for before, after in (
        ("snapEnd - snapIdx", "view.count - offset"),
        ("(snapEnd - snapBase) / COLLISION_BLOCK_SIZE", "view.count / COLLISION_BLOCK_SIZE"),
        ("runner->instanceSnapshots[snapIdx + i]", "view.items[offset + i]"),
        ("snapIdx += skipCount - 1;", "offset += skipCount - 1;"),
        ("runner->instanceSnapshots[snapIdx]", "view.items[offset]"),
    ):
        if before == "snapEnd - snapIdx":
            assert walk.count(before) == 2
            walk = walk.replace(before, after)
        else:
            walk = replace_once(walk, before, after)
    after_collision = "                    // Collision detected! If either instance is solid, restore both to xprevious/yprevious."
    walk = replace_once(walk, after_collision, """                    // Preserve the original target order before any callback
                    // can mutate a bucket or grow the shared snapshot array.
                    if (view.borrowed) {
                        ++targetFreezes;
                        materializeCollisionTargets(runner, &view);
                    }
""" + after_collision)
    assert walk.endswith("                }\n")
    walk = walk.removesuffix("                }\n") + """                    view.items = runner->instanceSnapshots + view.base;
                }
"""
    assert not any(part in walk for part in ("snapBase", "snapEnd", "snapIdx"))
    return code[:start] + walk + code[end:].replace(
        "                Runner_popInstanceSnapshot(runner, snapBase);",
        "                Runner_popInstanceSnapshot(runner, view.base);", 1)


def with_pinned_collision_targets(code, *, checked=False):
    """Retain an ordered list across callbacks, allocating only if a nested lookup replaces it."""
    code = replace_once(code, """    bool membershipDirty;
    CollisionBoundsBlock* blocks;
} CollisionTargetSnapshot;""",
                        """    bool membershipDirty;
    struct CollisionTargetView* pinned;
    CollisionBoundsBlock* blocks;
} CollisionTargetSnapshot;""")
    code = replace_once(code, """typedef struct {
    Instance* const* items;
    int32_t base, count;
    bool borrowed;
} CollisionTargetView;""", """typedef struct CollisionTargetView {
    Instance* const* items;
    int32_t base, count;
    bool borrowed;
    CollisionTargetSnapshot* sortedOwner;
    struct CollisionTargetView* previousPin;
} CollisionTargetView;

static unsigned pinnedCollisionLists, pinnedCollisionPointers, replacedPinnedLists;

static void pinCollisionTargets(CollisionTargetView* view) {
    CollisionTargetSnapshot* owner = view->sortedOwner;
    view->previousPin = owner->pinned;
    owner->pinned = view;
    view->borrowed = false;
    ++pinnedCollisionLists;
    pinnedCollisionPointers += (unsigned)view->count;
}

static void detachPinnedCollisionTargets(CollisionTargetSnapshot* owner) {
    if (owner->pinned != nullptr && owner->pinned->items == owner->sorted) {
        owner->sorted = nullptr;
        ++replacedPinnedLists;
    }
}

static void releaseCollisionTargets(CollisionTargetView* view) {
    CollisionTargetSnapshot* owner = view->sortedOwner;
    if (owner == nullptr || owner->pinned != view) return;
    owner->pinned = view->previousPin;
    if (owner->sorted == view->items ||
        (owner->pinned != nullptr && owner->pinned->items == view->items))
        return;
    Instance** retired = (Instance**)view->items;
    arrfree(retired);
}""")
    code = replace_once(code, """        if (needsSort || arrlenu(entry->sorted) != count) {
            arrsetlen(entry->sorted, count);""", """        if (needsSort || arrlenu(entry->sorted) != count) {
            detachPinnedCollisionTargets(entry);
            arrsetlen(entry->sorted, count);""")
    code = replace_once(code, "        source = entry->sorted;",
                        "        source = entry->sorted;\n        if (borrow) view.sortedOwner = entry;")
    check_snapshot = """
                            arrsetlen(runner->instanceSnapshots, view.base + view.count);
                            memcpy(runner->instanceSnapshots + view.base, view.items,
                                   (size_t)view.count * sizeof(*view.items));""" if checked else ""
    code = replace_once(code, """                        materializeCollisionTargets(runner, &view);
                    }""", """                        if (view.sortedOwner != nullptr) {
                            pinCollisionTargets(&view);""" + check_snapshot + """
                        } else {
                            materializeCollisionTargets(runner, &view);
                        }
                    }""")
    check_order = """
                    else
                        require(memcmp(view.items, runner->instanceSnapshots + view.base,
                                       (size_t)view.count * sizeof(*view.items)) == 0);""" if checked else ""
    code = replace_once(code, """                    view.items = runner->instanceSnapshots + view.base;
                }
                Runner_popInstanceSnapshot(runner, view.base);""", """                    if (view.sortedOwner == nullptr)
                        view.items = runner->instanceSnapshots + view.base;""" + check_order + """
                }
                releaseCollisionTargets(&view);
                Runner_popInstanceSnapshot(runner, view.base);""")
    anchor = "    targetSorts = targetSortsAvoided = targetBorrows = targetFreezes = targetPointersCopied = 0;"
    return replace_once(code, anchor, r'''    logInfo("HLD pinned collision targets frame=%d lists=%u pointers=%u replaced=%u\n",
            frame, pinnedCollisionLists, pinnedCollisionPointers, replacedPinnedLists);
    pinnedCollisionLists = pinnedCollisionPointers = replacedPinnedLists = 0;
''' + anchor)


def with_collision_bounds_inventory(code):
    """Measure how often recomputed groups yield the same bounds."""
    anchor = "static const CollisionBoundsBlock* collisionBoundsBlocks(Runner* runner,"
    code = replace_once(code, anchor, r'''
typedef struct {
    uint32_t queries, builds, unchangedBuilds, groups, unchangedGroups;
} CollisionBoundsWork;

static CollisionBoundsWork* collisionBoundsWork;

static bool sameCollisionGroup(const CollisionBoundsBlock* a, const CollisionBoundsBlock* b) {
    if (a->unbounded || b->unbounded) return a->unbounded == b->unbounded;
    if (a->hasBounds != b->hasBounds) return false;
    return !a->hasBounds || (a->left == b->left && a->right == b->right &&
                             a->top == b->top && a->bottom == b->bottom);
}

static void reportCollisionBoundsWork(Runner* runner) {
    if (collisionBoundsWork == nullptr) return;
    for (uint32_t target = 0; target < runner->dataWin->objt.count; ++target) {
        const CollisionBoundsWork* work = &collisionBoundsWork[target];
        if (work->builds == 0) continue;
        logInfo("HLD collision bounds inventory target=%s queries=%u builds=%u unchanged=%u groups=%u unchanged_groups=%u\n",
                runner->dataWin->objt.objects[target].name, work->queries, work->builds,
                work->unchangedBuilds, work->groups, work->unchangedGroups);
    }
    free(collisionBoundsWork);
    collisionBoundsWork = nullptr;
}

''' + anchor)
    anchor = "    if (entry->blockEpoch != runner->collisionDispatchEpoch) {"
    code = replace_once(code, anchor, '''    if (collisionBoundsWork == nullptr)
        collisionBoundsWork = safeCalloc(runner->dataWin->objt.count, sizeof(*collisionBoundsWork));
    CollisionBoundsWork* work = &collisionBoundsWork[target];
    ++work->queries;
''' + anchor + '''
        ++work->builds;
        const size_t prior = arrlenu(entry->blocks);
        const bool comparable = prior == (size_t)(blockCount + wideCount);
        unsigned unchanged = 0;
'''.rstrip("\n"))
    anchor = "            entry->blocks[blockIndex] = block;"
    code = replace_once(code, anchor, '''            if (comparable && sameCollisionGroup(&entry->blocks[blockIndex], &block)) ++unchanged;
''' + anchor)
    anchor = "        entry->blockEpoch = runner->collisionDispatchEpoch;"
    code = replace_once(code, anchor, '''        work->groups += (unsigned)blockCount;
        work->unchangedGroups += unchanged;
        if (unchanged == (unsigned)blockCount) ++work->unchangedBuilds;
''' + anchor)
    anchor = "void Runner_free(Runner* runner) {\n    if (runner == nullptr) return;\n"
    return replace_once(code, anchor, anchor + "    reportCollisionBoundsWork(runner);\n")
