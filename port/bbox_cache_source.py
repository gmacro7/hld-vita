"""Keep collision bounding-box cache hits on a small, inline path."""


def with_bbox_cache(code, compare=False):
    signature = "static inline InstanceBBox Collision_computeBBox(Runner* runner, Instance* inst) {"
    start = code.index(signature)
    stop = code.index("\n}\n", start) + 2
    original = code[start:stop]
    reference = original.replace("Collision_computeBBox(", "Collision_referenceBBox(", 1)

    # Share the full calculation as the miss path. The caller has already
    # resolved the sprite, so only the original arithmetic and stores remain.
    shape = original[original.index("    GMLReal marginL =") : original.index("    if (inst->bboxCache.bbox.valid")]
    calculation = original[original.index("    GMLReal left, right, top, bottom;") :]
    cold = ("static __attribute__((noinline)) InstanceBBox Collision_recomputeBBox(\n"
            "        Runner* runner, Instance* inst, const Sprite* spr) {\n"
            "    InstanceBBox ret;\n" + shape + calculation)

    hit = r'''
static inline bool Collision_boxKeyMatches(const Runner* runner, const Instance* inst, const Sprite* spr) {
    if (!inst->bboxCache.bbox.valid || inst->bboxCache.compatibilityMode != runner->collisionCompatibilityMode ||
        inst->bboxCache.x != inst->x || inst->bboxCache.y != inst->y ||
        inst->bboxCache.xscale != inst->imageXscale || inst->bboxCache.yscale != inst->imageYscale ||
        inst->bboxCache.angle != inst->imageAngle || inst->bboxCache.originX != (GMLReal)spr->originX ||
        inst->bboxCache.originY != (GMLReal)spr->originY) return false;

    if (spr->bboxMode == 1) {
        return inst->bboxCache.marginLeft == 0.0 && inst->bboxCache.marginRight == (GMLReal)spr->width &&
               inst->bboxCache.marginTop == 0.0 && inst->bboxCache.marginBottom == (GMLReal)spr->height;
    }
    return inst->bboxCache.marginLeft == (GMLReal)spr->marginLeft &&
           inst->bboxCache.marginRight == (GMLReal)(spr->marginRight + 1) &&
           inst->bboxCache.marginTop == (GMLReal)spr->marginTop &&
           inst->bboxCache.marginBottom == (GMLReal)(spr->marginBottom + 1);
}
'''
    if compare:
        hit += r'''
static inline bool Collision_useInlineBBox(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}
'''
    fast = r'''
static inline InstanceBBox Collision_inlineBBox(Runner* runner, Instance* inst) {
    const Sprite* spr = Collision_getSprite(runner->dataWin, inst);
    if (spr == nullptr) return (InstanceBBox){0};
    if (Collision_boxKeyMatches(runner, inst, spr)) return inst->bboxCache.bbox;
    return Collision_recomputeBBox(runner, inst, spr);
}
'''
    wrapper = signature + "\n"
    if compare:
        wrapper += r'''    if (!Collision_useInlineBBox(runner->frameCount)) return Collision_referenceBBox(runner, inst);
'''
    wrapper += r'''#ifdef HLD_VERIFY_INLINE_BBOX
    const __typeof__(inst->bboxCache) previous = inst->bboxCache;
    const InstanceBBox expected = Collision_referenceBBox(runner, inst);
    inst->bboxCache = previous;
    const InstanceBBox actual = Collision_inlineBBox(runner, inst);
    require(expected.valid == actual.valid);
    require(!actual.valid ||
        ((expected.left == actual.left || (isnan(expected.left) && isnan(actual.left))) &&
         (expected.right == actual.right || (isnan(expected.right) && isnan(actual.right))) &&
         (expected.top == actual.top || (isnan(expected.top) && isnan(actual.top))) &&
         (expected.bottom == actual.bottom || (isnan(expected.bottom) && isnan(actual.bottom)))));
    return actual;
#else
    return Collision_inlineBBox(runner, inst);
#endif
}'''
    if not compare:
        reference = "#ifdef HLD_VERIFY_INLINE_BBOX\n" + reference + "\n#endif"
    refresh = r'''
static inline bool Collision_updateBBoxCache(Runner* runner, Instance* inst) {
#ifdef HLD_VERIFY_INLINE_BBOX
    return Collision_computeBBox(runner, inst).valid;
#else
''' + ("    if (!Collision_useInlineBBox(runner->frameCount)) return Collision_referenceBBox(runner, inst).valid;\n"
       if compare else "") + r'''    const Sprite* spr = Collision_getSprite(runner->dataWin, inst);
    if (spr == nullptr) return false;
    if (!Collision_boxKeyMatches(runner, inst, spr)) (void)Collision_recomputeBBox(runner, inst, spr);
    return true;
#endif
}'''
    return code[:start] + "\n\n".join((reference, cold, hit, fast, wrapper, refresh)) + code[stop:]


def use_bbox_cache_refresh(code):
    original = "instance->collisionDispatchHasBounds = Collision_computeBBox(runner, instance).valid;"
    assert code.count(original) == 1
    return code.replace(original, "instance->collisionDispatchHasBounds = Collision_updateBBoxCache(runner, instance);")
