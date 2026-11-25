"""Keep repeated scenery flag writes on the existing scalar slot."""

from runtime_verification_source import replace_once


WRITES = r'''
static unsigned sceneryScalarWrites, sceneryUnchangedWrites, scenerySetterFallbacks;

static inline bool putSceneryInactive(Instance* instance, int32_t key, bool inactive) {
    IntRValueHashMap* map = &instance->selfVars;
    // The ordinary setter grows a sparse table at this boundary even for an existing key.
    bool keepsBucketOrder = (uint64_t)(map->count + 1) * 4 <= (uint64_t)map->capacity * 3;
#ifdef SHARED_FIELD_MASK
    keepsBucketOrder |= hasSharedFields(map);
#endif
    if (!keepsBucketOrder) return false;
    RValue* slot = IntRValueHashMap_findSlot(map, key);
    if (slot == nullptr || slot->ownsReference) return false;

    const RValue canonical = RValue_makeReal(inactive);
    if (slot->type == canonical.type && slot->gmlStackType == canonical.gmlStackType &&
        slot->assetRefType == canonical.assetRefType && memcmp(&slot->real, &canonical.real, sizeof(GMLReal)) == 0) {
        ++sceneryUnchangedWrites;
    } else {
        *slot = canonical;
        ++sceneryScalarWrites;
    }
    return true;
}

void hldReportSceneryScalarWritesAtExit(void) {
    logInfo("HLD: scenery scalar writes changed=%u unchanged=%u fallback=%u\n",
            sceneryScalarWrites, sceneryUnchangedWrites, scenerySetterFallbacks);
}
'''


def with_scenery_scalar_writes(code, *, verify=False, launch_flag=None):
    anchor = "static inline bool hldSceneryCull(VMContext* ctx, const uint8_t* step) {"
    helpers = WRITES
    if launch_flag:
        helpers += f"\nextern bool {launch_flag};\n" + r'''
void hldReportSceneryScalarWrites(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita scenery scalar writes frame=%d changed=%u unchanged=%u fallback=%u\n",
            frame, sceneryScalarWrites, sceneryUnchangedWrites, scenerySetterFallbacks);
    sceneryScalarWrites = sceneryUnchangedWrites = scenerySetterFallbacks = 0;
}
'''
    code = replace_once(code, anchor, helpers + "\n" + anchor)
    write = r'''        const bool inPlace = putSceneryInactive(instance, sceneryPass.inactive, change.inactive);
        if (!inPlace) {
            ++scenerySetterFallbacks;
            Instance_setSelfVar(instance, sceneryPass.inactive, RValue_makeReal(change.inactive));
        }
        instance->visible = change.visible;'''
    if verify:
        write += r'''
        if (inPlace) {
            const RValue actual = Instance_getSelfVar(instance, sceneryPass.inactive);
            Instance_setSelfVar(instance, sceneryPass.inactive, RValue_makeReal(change.inactive));
            const RValue expected = Instance_getSelfVar(instance, sceneryPass.inactive);
            require(actual.type == expected.type && actual.gmlStackType == expected.gmlStackType &&
                    actual.assetRefType == expected.assetRefType && actual.real == expected.real &&
                    signbit(actual.real) == signbit(expected.real));
        }
        VMBuiltins_setVariable(ctx, instance, BUILTIN_VAR_VISIBLE, "visible", RValue_makeReal(change.visible), -1);
        require(instance->visible == change.visible);'''
    anchor = '''        Instance_setSelfVar(instance, sceneryPass.inactive, RValue_makeReal(change.inactive));
        VMBuiltins_setVariable(ctx, instance, BUILTIN_VAR_VISIBLE, "visible", RValue_makeReal(change.visible), -1);'''
    if launch_flag:
        write = (f"        if ({launch_flag}) {{\n" + "\n".join("    " + line for line in write.splitlines()) +
                 "\n        } else {\n" + "\n".join("    " + line for line in anchor.splitlines()) + "\n        }")
    return replace_once(code, anchor, write)


def with_scenery_scalar_writes_exit(code):
    anchor = "            if (args.exitAtFrame >= 0 && runner->frameCount >= args.exitAtFrame) {"
    return replace_once(code, anchor, anchor + """
                extern void hldReportSceneryScalarWritesAtExit(void);
                hldReportSceneryScalarWritesAtExit();""")
