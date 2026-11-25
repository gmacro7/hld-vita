"""Use the existing room grid for sparse collision dispatch queries."""

from pathlib import Path


def with_shared_collision_grid(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    replace("    uint64_t blockEpoch;", """    uint64_t blockEpoch;
    uint64_t gridEpoch, gridRevision;
    bool gridCoversTargets;""")
    helper = Path(__file__).with_name("shared-collision-grid.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_SHARED_COLLISION_GRID\n" + helper
    anchor = "static void dispatchCollisionEvents(Runner* runner) {"
    replace(anchor, helper + "\n" + anchor)
    anchor = "    const bool enableBlocks = true;"
    replace(anchor, anchor + "\n    const bool enableSharedGrid = sharedGridEnabled(runner->frameCount);")
    replace("                const CollisionBoundsBlock* blocks = enableBlocks && rejectDistantPairs", """                int32_t ordinals[COLLISION_GRID_MAX_CANDIDATES];
                int32_t nextOrdinal = 0;
                const int32_t candidateCount = enableSharedGrid && rejectDistantPairs
                    ? sharedGridCollisionCandidates(runner, targetObjIndex, self, dispatchSelf,
                                                    snapBase, snapEnd - snapBase, ordinals) : -1;
                const CollisionBoundsBlock* blocks = candidateCount < 0 && enableBlocks && rejectDistantPairs""")
    anchor = "                for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {"
    replace(anchor, anchor + """
                    if (candidateCount >= 0 && runner->collisionDispatchEpoch == blockEpoch) {
                        // Once any callback invalidates the epoch, resume the original snapshot walk.
                        const int32_t next = nextOrdinal < candidateCount ? snapBase + ordinals[nextOrdinal++] : snapEnd;
#ifdef HLD_VERIFY_SHARED_COLLISION_GRID
                        for (int32_t check = snapIdx; check < next; ++check) {
                            Instance* skipped = runner->instanceSnapshots[check];
                            if (!skipped->active || skipped == self) continue;
                            const InstanceBBox* bounds = collisionDispatchBounds(runner, skipped);
                            require(bounds == nullptr || dispatchSelf->left >= bounds->right ||
                                    bounds->left >= dispatchSelf->right || dispatchSelf->top >= bounds->bottom ||
                                    bounds->top >= dispatchSelf->bottom);
                        }
#endif
                        sharedGridSkipped += (unsigned)(next - snapIdx);
                        if (next == snapEnd) break;
                        snapIdx = next;
                    }""")
    return code
