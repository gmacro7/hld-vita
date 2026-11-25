"""Retain scratch space for large with-block snapshots across dispatches."""

from runtime_verification_source import replace_once


POOL = r'''
typedef struct {
    size_t capacity;
    Instance* items[];
} EnvSnapshotBuffer;

enum { ENV_SNAPSHOT_POOL_SIZE = 4, ENV_SNAPSHOT_MAX_RETAINED = 2048 };
static VMContext* envSnapshotOwner;
static EnvSnapshotBuffer* envSnapshotPool[ENV_SNAPSHOT_POOL_SIZE];
static unsigned envSnapshotReused, envSnapshotAllocated, envSnapshotItems;

static Instance** takeEnvSnapshot(VMContext* ctx, size_t count) {
    if (envSnapshotOwner == nullptr) envSnapshotOwner = ctx;
    if (envSnapshotOwner == ctx && count <= ENV_SNAPSHOT_MAX_RETAINED) {
        int best = -1;
        for (int i = 0; i < ENV_SNAPSHOT_POOL_SIZE; ++i) {
            EnvSnapshotBuffer* buffer = envSnapshotPool[i];
            if (buffer != nullptr && buffer->capacity >= count &&
                (best < 0 || buffer->capacity < envSnapshotPool[best]->capacity)) best = i;
        }
        if (best >= 0) {
            EnvSnapshotBuffer* buffer = envSnapshotPool[best];
            envSnapshotPool[best] = nullptr;
            ++envSnapshotReused;
            return buffer->items;
        }
    }

    EnvSnapshotBuffer* buffer = safeMalloc(sizeof(*buffer) + count * sizeof(*buffer->items));
    buffer->capacity = count;
    ++envSnapshotAllocated;
    return buffer->items;
}

static void releaseEnvSnapshot(VMContext* ctx, Instance** items) {
    EnvSnapshotBuffer* buffer = (EnvSnapshotBuffer*)((char*)items - offsetof(EnvSnapshotBuffer, items));
    if (envSnapshotOwner == ctx && buffer->capacity <= ENV_SNAPSHOT_MAX_RETAINED) {
        int smallest = 0;
        for (int i = 0; i < ENV_SNAPSHOT_POOL_SIZE; ++i) {
            if (envSnapshotPool[i] == nullptr) {
                envSnapshotPool[i] = buffer;
                return;
            }
            if (envSnapshotPool[i]->capacity < envSnapshotPool[smallest]->capacity) smallest = i;
        }
        if (buffer->capacity > envSnapshotPool[smallest]->capacity) {
            free(envSnapshotPool[smallest]);
            envSnapshotPool[smallest] = buffer;
            return;
        }
    }
    free(buffer);
}

static void clearEnvSnapshotPool(VMContext* ctx) {
    if (envSnapshotOwner != ctx) return;
    for (int i = 0; i < ENV_SNAPSHOT_POOL_SIZE; ++i) {
        free(envSnapshotPool[i]);
        envSnapshotPool[i] = nullptr;
    }
    envSnapshotOwner = nullptr;
}

void hldReportEnvSnapshotPoolAtExit(void) {
    logInfo("HLD: with snapshots reused=%u allocated=%u copied_instances=%u\n",
            envSnapshotReused, envSnapshotAllocated, envSnapshotItems);
}
'''

COPY = r'''
    int32_t count = 0;
    if (reverseLargeLists) {
        for (int32_t i = sourceCount - 1; i >= 0; --i) {
            if (source[i]->active) snapshot[count++] = source[i];
        }
        // The legacy order is forward when exactly two instances qualify.
        if (count == 2) {
            Instance* first = snapshot[0];
            snapshot[0] = snapshot[1];
            snapshot[1] = first;
        }
    } else {
        for (int32_t i = 0; i < sourceCount; ++i)
            if (source[i]->active) snapshot[count++] = source[i];
    }
    if (sourceCount > VM_INLINE_ENV_INSTANCES) envSnapshotItems += (unsigned)count;
'''

VERIFY = r'''
    int32_t seen = 0;
    for (int32_t i = 0; i < sourceCount; ++i) {
        if (!source[i]->active) continue;
        require(seen < count);
        const int32_t position = reverseLargeLists && count > 2 ? count - 1 - seen : seen;
        require(snapshot[position] == source[i]);
        ++seen;
    }
    require(seen == count);
'''


def with_env_snapshot_pool(code, *, verify=False, launch_flag=None):
    anchor = "static void snapshotEnvInstances(EnvFrame* frame, Instance** source, bool reverseLargeLists) {"
    start = code.index(anchor)
    end = code.index("\nstatic void handlePushEnv(", start)
    original = code[start:end]
    setup = POOL
    if launch_flag:
        setup += f"\nextern bool {launch_flag};\n"
        setup += original.replace("snapshotEnvInstances(", "legacySnapshotEnvInstances(", 1)
        setup += r'''
void hldReportEnvSnapshotPool(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita with snapshots frame=%d reused=%u allocated=%u copied_instances=%u\n",
            frame, envSnapshotReused, envSnapshotAllocated, envSnapshotItems);
    envSnapshotReused = envSnapshotAllocated = envSnapshotItems = 0;
}
'''
    code = replace_once(code, anchor, setup + r'''
static void snapshotEnvInstances(VMContext* ctx, EnvFrame* frame, Instance** source, bool reverseLargeLists) {''')
    start = code.index("static void snapshotEnvInstances(VMContext*")
    end = code.index("\nstatic void handlePushEnv(", start)
    body = code[start:end]
    begin = body.index("        : (Instance**) safeMalloc(")
    finish = body.index("    frame->instanceList = snapshot;")
    body = body[:begin] + "        : takeEnvSnapshot(ctx, (size_t)sourceCount);\n" + COPY + (
        VERIFY if verify else "") + body[finish:]
    if launch_flag:
        signature = "bool reverseLargeLists) {"
        body = replace_once(body, signature, signature + f'''
    if (!{launch_flag}) {{
        legacySnapshotEnvInstances(frame, source, reverseLargeLists);
        return;
    }}''')
    code = code[:start] + body + code[end:]
    assert code.count("            snapshotEnvInstances(frame,") == 2
    code = code.replace("            snapshotEnvInstances(frame,", "            snapshotEnvInstances(ctx, frame,")
    cleanup = "        releaseEnvSnapshot(ctx, frame->instanceList);"
    if launch_flag:
        cleanup = (f"        if ({launch_flag}) releaseEnvSnapshot(ctx, frame->instanceList);\n"
                   "        else free(frame->instanceList);")
    code = replace_once(code, "        free(frame->instanceList);", cleanup)
    anchor = "void VM_free(VMContext* ctx) {"
    return replace_once(code, anchor, anchor + "\n    clearEnvSnapshotPool(ctx);")


def with_env_snapshot_pool_exit(code):
    anchor = "            if (args.exitAtFrame >= 0 && runner->frameCount >= args.exitAtFrame) {"
    return replace_once(code, anchor, anchor + """
                extern void hldReportEnvSnapshotPoolAtExit(void);
                hldReportEnvSnapshotPoolAtExit();""")
