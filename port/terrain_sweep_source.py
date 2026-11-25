"""Resolve ordinary stopped movement and empty movement calls in the native path."""

from runtime_verification_source import replace_once


PATH = r'''
typedef struct {
    float x, y, lastProbeX, lastProbeY;
    char blockedAxis;
} MantleTerrainPath;
'''

AXIS = r'''
static bool mantleSolveAxis(Runner* runner, Instance* inst, const MantleTerrain* terrain, size_t count,
                            float end, bool vertical, bool needsSupport, MantleTerrainPath* path) {
    float* coordinate = vertical ? &path->y : &path->x;
    const int step = (end > *coordinate) - (end < *coordinate);
    while (*coordinate != end) {
        path->lastProbeX = path->x;
        path->lastProbeY = path->y;
        inst->x = vertical ? path->x : path->x + step;
        inst->y = vertical ? path->y + step : path->y;
        const bool hitBlock = mantleTerrainTouches(runner, inst, terrain, count);
        const bool falls = !hitBlock && needsSupport && !mantleFindSupport(runner, inst,
            vertical ? path->x : path->x + 8 * step, vertical ? path->y + 8 * step : path->y);
        if (hitBlock || falls) {
            inst->x = path->x;
            inst->y = path->y;
            // The script has a separate escape path when the mover is
            // already inside terrain. Keep it in the original script.
            if (mantleTerrainTouches(runner, inst, terrain, count)) return false;
            path->blockedAxis = vertical ? 'v' : 'h';
            return true;
        }
        *coordinate += step;
    }
    return true;
}
'''


def with_terrain_sweep_query(code):
    code = replace_once(code, "static bool mantleAxisClear(", PATH + r'''
static bool mantleTerrainTouches(Runner* runner, Instance* inst, const MantleTerrain* terrain, size_t count) {
    if (count == 0) return false;
    const InstanceBBox bounds = Collision_computeBBox(runner, inst);
    for (size_t i = 0; i < count; ++i) {
        if (sweepBoundsTouch(bounds, terrain[i].bounds) &&
            Collision_instancesOverlapPrecise(runner, inst, terrain[i].instance, bounds, terrain[i].bounds))
            return true;
    }
    return false;
}

''' + AXIS + "\nstatic bool mantleAxisClear(")
    start = code.index("        if (count != 0) {")
    end = code.index("        if (needsSupport && !mantleFindSupport", start)
    old = code[start:end]
    code = replace_once(code, old, "        if (mantleTerrainTouches(runner, inst, terrain, count)) return false;\n")
    code = replace_once(code, "bool hldMantleSweepClear(Runner* runner, Instance* inst, float endX, float endY, bool needsSupport) {",
                        """static bool mantleSweep(Runner* runner, Instance* inst, float endX, float endY, bool needsSupport,
                        MantleTerrainPath* path) {""")
    old = """                if (!(endX != x && sweepBoundsTouch(horizontal, otherBounds)) &&
                    !(endY != y && sweepBoundsTouch(vertical, otherBounds))) continue;"""
    code = replace_once(code, old, """                if (path != nullptr) {
                    // A horizontal stop can put vertical motion anywhere
                    // within the full rectangle. The start and the blocked
                    // position are needed for the terrain escape guard too.
                    if (!sweepBoundsTouch(bounds, otherBounds)) continue;
                } else if (!(endX != x && sweepBoundsTouch(horizontal, otherBounds)) &&
                           !(endY != y && sweepBoundsTouch(vertical, otherBounds))) continue;""")
    code = replace_once(code, "    if (count == 0 && !needsSupport) return true;", r'''    if (path != nullptr) {
        *path = (MantleTerrainPath){.x = x, .y = y, .lastProbeX = x, .lastProbeY = y};
        if (count == 0 && !needsSupport) {
            path->x = endX;
            path->y = endY;
            path->lastProbeX = endY == y ? endX - ((endX > x) - (endX < x)) : endX;
            path->lastProbeY = endY - ((endY > y) - (endY < y));
            return true;
        }
        const bool resolved = mantleSolveAxis(runner, inst, terrain, count, endX, false, needsSupport, path) &&
            mantleSolveAxis(runner, inst, terrain, count, endY, true, needsSupport, path);
        inst->x = x;
        inst->y = y;
        return resolved;
    }
    if (count == 0 && !needsSupport) return true;''')
    return replace_once(code, "static RValue builtin_place_meeting(", r'''
bool hldMantleSweepClear(Runner* runner, Instance* inst, float endX, float endY, bool needsSupport) {
    return mantleSweep(runner, inst, endX, endY, needsSupport, nullptr);
}

bool hldMantleSolveTerrain(Runner* runner, Instance* inst, float endX, float endY, bool needsSupport,
                           MantleTerrainPath* path) {
    return mantleSweep(runner, inst, endX, endY, needsSupport, path);
}

static RValue builtin_place_meeting(''')


def with_terrain_sweep_vm(code, *, compare=False):
    code = replace_once(code, "#ifdef HLD_MANTLE_SWEEP\n    int32_t z;", "#ifdef HLD_MANTLE_SWEEP\n    int32_t z, locked;")
    code = replace_once(code, "    GMLReal horizontalSteps, verticalSteps;", "    GMLReal horizontalSteps, verticalSteps;\n    char blockedAxis;")
    helpers = r'''
static unsigned terrainSweepStationary, terrainSweepMoved, terrainSweepStopped;
static bool terrainSweepEnabled;
'''
    if compare:
        helpers += r'''
void hldReportTerrainSweep(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita terrain sweep frame=%d enabled=%d calls=%u stationary=%u stopped=%u\n",
            frame, (int)terrainSweepEnabled, terrainSweepMoved + terrainSweepStationary,
            terrainSweepStationary, terrainSweepStopped);
    terrainSweepStationary = terrainSweepMoved = terrainSweepStopped = 0;
}
'''
    else:
        helpers += r'''
static void reportTerrainSweep(void) {
    logInfo("HLD: terrain sweep moving=%u stationary=%u stopped=%u\n",
            terrainSweepMoved, terrainSweepStationary, terrainSweepStopped);
}
'''
    code = replace_once(code, "static ZeroMovement zeroMovement;", "static ZeroMovement zeroMovement;\n" + helpers)
    binding = r'''    bound.locked = zeroMovementVariable(ctx, bytes, 0x0444, "lockedToSafePlatform");
    if (bound.locked < 0) return;
'''
    if compare:
        binding += r'''    FILE* terrainFlag = fopen("ux0:data/hld-vm/terrain-sweep", "r");
    terrainSweepEnabled = terrainFlag != nullptr;
    if (terrainFlag != nullptr) fclose(terrainFlag);
    logInfo("Vita terrain sweep enabled=%d\n", (int)terrainSweepEnabled);
'''
    else:
        binding += "    terrainSweepEnabled = true;\n"
    code = replace_once(code, "    zeroMovement = bound;", binding + "    zeroMovement = bound;")
    code = replace_once(code, "static bool predictMantleSweep(", PATH + r'''
bool hldMantleSolveTerrain(Runner* runner, Instance* inst, float endX, float endY, bool needsSupport,
                           MantleTerrainPath* path);

static const char* mantleMovementResult(const ZeroMovementResult* result) {
    if (result->blockedAxis == 'h') return "h";
    if (result->blockedAxis == 'v') return "v";
    return "none";
}

static bool predictMantleSweep(''')
    code = replace_once(code, "    if (!hldMantleSweepClear(ctx->runner, inst, endX, endY, needsSupport)) {", r'''    if (terrainSweepEnabled && !RValue_toBool(result->fields[ZERO_SLOPES])) {
        const RValue z = Instance_getSelfVar(inst, zeroMovement.z);
        if (zeroMovementNumber(z) && RValue_toReal(z) == 0) {
            // IsType follows the parent chain. A locked descendant may
            // reject movement even with a valid point on the platform.
            if (needsSupport && RValue_toBool(Instance_getSelfVar(inst, zeroMovement.locked))) return false;
            MantleTerrainPath path;
            if (hldMantleSolveTerrain(ctx->runner, inst, endX, endY, needsSupport, &path)) {
                result->x = path.x;
                result->y = path.y;
                result->penultimateX = path.lastProbeX;
                result->penultimateY = path.lastProbeY;
                result->horizontalSteps = h;
                result->verticalSteps = v;
                result->blockedAxis = path.blockedAxis;
                ++terrainSweepMoved;
                terrainSweepStopped += path.blockedAxis != 0;
                return true;
            }
            return false;
        }
    }
    if (!hldMantleSweepClear(ctx->runner, inst, endX, endY, needsSupport)) {''')
    code = replace_once(code, "    if (h == 0.0 && v == 0.0) return false;\n    COUNT_SWEEP(SWEEP_MOVING);", r'''    const bool stationary = h == 0.0 && v == 0.0;
    if (stationary && !terrainSweepEnabled) return false;
    if (!stationary) COUNT_SWEEP(SWEEP_MOVING);
    result->blockedAxis = 0;''')
    code = replace_once(code, "    return predictMantleSweep(ctx, inst, args, h, v, result);", r'''    if (stationary) {
        const float x = inst->x, y = inst->y;
        const float roundedX = GMLReal_bankersRound(x), roundedY = GMLReal_bankersRound(y);
        result->x = roundedX == x ? x : roundedX;
        result->y = roundedY == y ? y : roundedY;
        result->penultimateX = x;
        result->penultimateY = y;
        result->horizontalSteps = h;
        result->verticalSteps = v;
        ++terrainSweepStationary;
        return true;
    }
    return predictMantleSweep(ctx, inst, args, h, v, result);''')
    code = replace_once(code, 'require(strcmp(result.string, "none") == 0);',
                        'require(strcmp(result.string, mantleMovementResult(expected)) == 0);')
    code = replace_once(code, 'return RValue_makeOwnedString(safeStrdup("none"));',
                        'return RValue_makeOwnedString(safeStrdup(mantleMovementResult(&zeroMoveResult)));')
    if not compare:
        anchor = "void VM_free(VMContext* ctx) {"
        code = replace_once(code, anchor, anchor + "\n    if (zeroMovement.context == ctx) reportTerrainSweep();")
    return code
