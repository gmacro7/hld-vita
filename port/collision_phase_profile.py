"""Sample collision dispatch without timing each rejected pair."""


def with_collision_phase_profile(code):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    start = "static void dispatchCollisionEvents(Runner* runner) {"
    replace(start, start + """
    const int frame = runner->frameCount;
    const bool sample = frame >= 560 && frame <= 900 && frame % 66 == 9;
    const uint64_t dispatchStart = sample ? nowNanos() : 0;
    uint64_t snapshots = 0, blocksTime = 0, preciseTime = 0, hitTime = 0;
    unsigned searches = 0, targetCount = 0, blockRejects = 0, boundsOverlaps = 0;
    unsigned precisionChecks = 0, hits = 0;
""")
    anchor = "                int32_t snapBase = pushSortedCollisionTargets(runner, targetObjIndex);"
    replace(anchor, "                uint64_t prepareStart = sample ? nowNanos() : 0;\n" + anchor)
    anchor = "                const uint64_t blockEpoch = runner->collisionDispatchEpoch;"
    replace(anchor, anchor + """
                if (sample) {
                    const uint64_t now = nowNanos();
                    snapshots += now - prepareStart;
                    prepareStart = now;
                    ++searches;
                    targetCount += (unsigned)(snapEnd - snapBase);
                }""")
    anchor = "                    ? collisionBoundsBlocks(runner, targetObjIndex, snapBase, snapEnd - snapBase, enableWide) : nullptr;"
    replace(anchor, anchor + "\n                if (sample) blocksTime += nowNanos() - prepareStart;")
    anchor = "                    if (skipCount != 0) {\n#ifdef HLD_VERIFY_COLLISION_BLOCKS"
    replace(anchor, "                    if (skipCount != 0) {\n"
            "                        if (sample) blockRejects += (unsigned)skipCount;\n#ifdef HLD_VERIFY_COLLISION_BLOCKS")
    anchor = "                    if (mostSpecificCollisionTarget(runner, self, other) != targetObjIndex)"
    replace(anchor, "                    if (sample) ++boundsOverlaps;\n" + anchor)
    anchor = "                        bool preciseHit = Collision_instancesOverlapPrecise(runner, self, other, bboxSelf, bboxOther);"
    replace(anchor, "                        const uint64_t preciseStart = sample ? nowNanos() : 0;\n" + anchor + """
                        if (sample) {
                            preciseTime += nowNanos() - preciseStart;
                            ++precisionChecks;
                        }""")
    anchor = "                    // Collision detected! If either instance is solid, restore both to xprevious/yprevious."
    replace(anchor, "                    const uint64_t hitStart = sample ? nowNanos() : 0;\n" + anchor)
    anchor = "                }\n                Runner_popInstanceSnapshot(runner, snapBase);\n            }\n        }\n\n        arrsetlen(runner->instanceSnapshots, selfSnapBase);\n    }\n}"
    replace(anchor, r'''                    if (sample) {
                        hitTime += nowNanos() - hitStart;
                        ++hits;
                    }
                }
                Runner_popInstanceSnapshot(runner, snapBase);
            }
        }

        arrsetlen(runner->instanceSnapshots, selfSnapBase);
    }
    if (sample) {
        const uint64_t total = nowNanos() - dispatchStart;
        const uint64_t scan = total - snapshots - blocksTime - preciseTime - hitTime;
        logInfo("Vita collision phases frame=%d total_ms=%.3f snapshots_ms=%.3f blocks_ms=%.3f "
                "scan_ms=%.3f precise_ms=%.3f hits_ms=%.3f searches=%u targets=%u "
                "block_rejects=%u bounds_overlaps=%u precision_checks=%u hits=%u\n",
                frame, total * 1e-6, snapshots * 1e-6, blocksTime * 1e-6,
                scan * 1e-6, preciseTime * 1e-6, hitTime * 1e-6, searches,
                targetCount, blockRejects, boundsOverlaps, precisionChecks, hits);
    }
}''')
    return code
