"""Keep collision bounds across callbacks that cannot change their inputs."""

WATCH = "extern bool hldWatchCollisionMutation;\nextern bool hldCollisionMutated;\n\n"


def with_collision_epoch_vm(code):
    start = "static void handleCall(VMContext* ctx, OpWord opword, const uint8_t* extraData"
    anchor = "        BuiltinFunc builtin = (BuiltinFunc) cache->builtin;"
    assert code.count(start) == code.count(anchor) == 1
    code = code.replace(start, WATCH + start)
    return code.replace(anchor, "        if (hldWatchCollisionMutation) hldCollisionMutated = true;\n" + anchor)


def with_collision_epoch_builtins(code):
    start = "void VMBuiltins_setVariable(VMContext* ctx, Instance* inst, int16_t builtinVarId, const char* name, RValue val, int32_t arrayIndex) {"
    assert code.count(start) == 1
    return code.replace(start, WATCH + start + "\n    if (hldWatchCollisionMutation) hldCollisionMutated = true;")


def with_collision_epoch_runner(code, compare=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    support = r'''
bool hldWatchCollisionMutation;
bool hldCollisionMutated;

#ifdef HLD_VERIFY_COLLISION_EPOCH
static void verifyRetainedCollisionBounds(Runner* runner) {
    static bool reported;
    if (!reported) {
        logInfo("Verified an unchanged collision callback against existing bounds\n");
        reported = true;
    }
    for (size_t i = 0; i < arrlenu(runner->instances); ++i) {
        Instance* instance = runner->instances[i];
        if (instance->collisionDispatchEpoch != runner->collisionDispatchEpoch) continue;
        const InstanceBBox cached = instance->bboxCache.bbox;
        const InstanceBBox actual = Collision_computeBBox(runner, instance);
        require(actual.valid == instance->collisionDispatchHasBounds);
        if (actual.valid) {
            require((cached.left == actual.left || (isnan(cached.left) && isnan(actual.left))) &&
                    (cached.right == actual.right || (isnan(cached.right) && isnan(actual.right))) &&
                    (cached.top == actual.top || (isnan(cached.top) && isnan(actual.top))) &&
                    (cached.bottom == actual.bottom || (isnan(cached.bottom) && isnan(actual.bottom))));
        }
    }
}
#endif
'''
    if compare:
        support += r'''
static unsigned collisionCallbacks;
static unsigned collisionEpochRetained;

static inline bool useRetainedCollisionEpoch(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportCollisionEpoch(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita collision epoch frame=%d enabled=%d callbacks=%u retained=%u\n",
            frame, (int)useRetainedCollisionEpoch(frame - 1), collisionCallbacks, collisionEpochRetained);
    collisionCallbacks = collisionEpochRetained = 0;
}
'''
    anchor = "static void dispatchCollisionEvents(Runner* runner) {"
    replace(anchor, support + "\n" + anchor)
    mode = "useRetainedCollisionEpoch(runner->frameCount)" if compare else "true"
    before = "    bool rejectDistantPairs = true;"
    replace(before, before + f"\n    const bool keepUnchangedCollisionBounds = {mode};")
    anchor = "                    executeCollisionEvent(runner, self, other, targetObjIndex, evt->codeId, evt->ownerObjectIndex);"
    replace(anchor, r'''                    // Solid resolution moves participants in native code. For non-solid
                    // events only VM built-ins and writes to built-in variables can change
                    // collision geometry. Ordinary scalar instance/local writes are safe.
                    const bool watching = keepUnchangedCollisionBounds && !hadSolid;
                    const uint64_t priorRevision = runner->instanceListRevision;
                    if (watching) {
                        hldCollisionMutated = false;
                        hldWatchCollisionMutation = true;
                    }
''' + anchor + r'''
                    if (watching) hldWatchCollisionMutation = false;
'''.rstrip())
    before = """                    ++runner->collisionDispatchEpoch;
                    if (rejectDistantPairs) dispatchSelf = collisionDispatchBounds(runner, self);"""
    replace(before, r'''                    if (!watching || hldCollisionMutated || priorRevision != runner->instanceListRevision) {
                        ++runner->collisionDispatchEpoch;
                        if (rejectDistantPairs) dispatchSelf = collisionDispatchBounds(runner, self);
                    } else {
#ifdef HLD_VERIFY_COLLISION_EPOCH
                        verifyRetainedCollisionBounds(runner);
#endif
''' + ("                        ++collisionEpochRetained;\n" if compare else "") + "                    }" +
            ("\n                    ++collisionCallbacks;" if compare else ""))
    return code
