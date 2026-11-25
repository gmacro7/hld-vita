// The original probes visit these platform types, including descendants.
// The guard accepts precisely this live array before using the union cache.
static const int32_t enemyShadowTargets[] = {207, 208, 230, 209, 135, 142, 143, 146};
enum { ENEMY_SHADOW_TARGETS = INT32_MIN + 1, ENEMY_SHADOW_POINTS = 4 };

static bool shadowProbeObject(DataWin* data, int32_t object, bool reference) {
    repeat(sizeof(enemyShadowTargets) / sizeof(*enemyShadowTargets), i) {
        const int32_t target = enemyShadowTargets[i];
        if (reference ? VM_isObjectOrDescendant(data, object, target) : placementObjectMatches(data, object, target))
            return true;
    }
    return false;
}
