typedef uint16_t CollisionBoundsLanes __attribute__((vector_size(16)));
typedef uint8_t CollisionBoundsBytes __attribute__((vector_size(8)));

struct CollisionQuantizedPack {
    uint16_t left[8], right[8], top[8], bottom[8], active[8];
};

static inline bool collisionFiniteRect(const InstanceBBox* box) {
    return isfinite(box->left) && isfinite(box->right) &&
           isfinite(box->top) && isfinite(box->bottom);
}

static inline uint16_t quantizeCollisionBound(GMLReal value) {
    if (value <= -32768.0) return 0;
    if (value >= 32767.0) return UINT16_MAX;
    return (uint16_t)(value + 32768.0);
}

static inline CollisionBoundsLanes collisionSplat(uint16_t bound) {
    return (CollisionBoundsLanes){bound, bound, bound, bound, bound, bound, bound, bound};
}

static inline uint32_t collisionPackMask(const CollisionQuantizedPack* pack,
        CollisionBoundsLanes left, CollisionBoundsLanes right,
        CollisionBoundsLanes top, CollisionBoundsLanes bottom) {
    CollisionBoundsLanes pl, pr, pt, pb, active;
    // The backing array is only aligned for its scalar fields.
    memcpy(&pl, pack->left, sizeof(pl));
    memcpy(&pr, pack->right, sizeof(pr));
    memcpy(&pt, pack->top, sizeof(pt));
    memcpy(&pb, pack->bottom, sizeof(pb));
    memcpy(&active, pack->active, sizeof(active));

    // Quantization is monotone. A strict separation after quantization
    // implies a separation in the original bounds. Equal buckets go through.
    const CollisionBoundsLanes disjoint = (left > pr) | (pl > right) | (top > pb) | (pt > bottom);
    const CollisionBoundsBytes matches = __builtin_convertvector(active & ~disjoint, CollisionBoundsBytes);
    uint64_t bytes;
    memcpy(&bytes, &matches, sizeof(bytes));
#if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    bytes = __builtin_bswap64(bytes);
#endif
    return (uint32_t)(((bytes & UINT64_C(0x0101010101010101)) * UINT64_C(0x0102040810204080)) >> 56);
}

static const uint32_t* findCollisionGridCandidates(Runner* runner, int32_t target,
        int32_t base, int32_t count, const InstanceBBox* bounds) {
    if (count < 128 || count > 4096 || target < 0 || (uint32_t)target >= runner->dataWin->objt.count ||
        bounds == nullptr || !collisionFiniteRect(bounds)) return nullptr;

#ifdef HLD_VERIFY_COLLISION_GRID
    static bool loggedVerification;
    if (!loggedVerification) {
        logInfo("Quantized collision verification active, target count=%d\n", count);
        loggedVerification = true;
    }
#endif

    CollisionTargetSnapshot* entry = &runner->collisionTargets[target];
    const unsigned packs = ((unsigned)count + 7) / 8;
    const unsigned words = (packs + 3) / 4;
    if (entry->gridEpoch != runner->collisionDispatchEpoch) {
        arrsetlen(entry->quantizedBoxes, packs);
        arrsetlen(entry->queryBits, words);
        for (unsigned index = 0; index < packs; ++index) {
            CollisionQuantizedPack* pack = &entry->quantizedBoxes[index];
            for (unsigned lane = 0; lane < 8; ++lane) {
                const unsigned offset = index * 8 + lane;
                const InstanceBBox* box = nullptr;
                if (offset < (unsigned)count) {
                    Instance* inst = runner->instanceSnapshots[base + offset];
                    if (inst->active) box = collisionDispatchBounds(runner, inst);
                }
                pack->active[lane] = box != nullptr ? UINT16_MAX : 0;
                if (box != nullptr && collisionFiniteRect(box)) {
                    pack->left[lane] = quantizeCollisionBound(box->left);
                    pack->right[lane] = quantizeCollisionBound(box->right);
                    pack->top[lane] = quantizeCollisionBound(box->top);
                    pack->bottom[lane] = quantizeCollisionBound(box->bottom);
                } else {
                    pack->left[lane] = pack->top[lane] = 0;
                    pack->right[lane] = pack->bottom[lane] = UINT16_MAX;
                }
            }
        }
        entry->gridEpoch = runner->collisionDispatchEpoch;
        COUNT_GRID_BUILD();
    }

    const CollisionBoundsLanes left = collisionSplat(quantizeCollisionBound(bounds->left));
    const CollisionBoundsLanes right = collisionSplat(quantizeCollisionBound(bounds->right));
    const CollisionBoundsLanes top = collisionSplat(quantizeCollisionBound(bounds->top));
    const CollisionBoundsLanes bottom = collisionSplat(quantizeCollisionBound(bounds->bottom));
    for (unsigned word = 0; word < words; ++word) {
        uint32_t candidateBits = 0;
        const unsigned start = word * 4;
        for (unsigned group = 0; group < 4 && start + group < packs; ++group) {
            const uint32_t hits = collisionPackMask(&entry->quantizedBoxes[start + group], left, right, top, bottom);
            candidateBits |= hits << (group * 8);
        }
        entry->queryBits[word] = candidateBits;
    }
    COUNT_GRID_QUERY();
    return entry->queryBits;
}

