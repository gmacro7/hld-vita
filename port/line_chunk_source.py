"""Cache bounding boxes for contiguous groups in ordered terrain queries."""

from runtime_verification_source import replace_once


def with_line_chunks(code, *, verify=False, launch_flag=None, retain_order=False, persistent=False, report=False,
                     obstacle_buckets=False):
    helper = r'''
enum { LINE_CHUNK = 16, LINE_MEMBER_LIMIT = 16384 };

typedef struct {
    const DataWin* archive;
    const Runner* runner;
    const SpatialGrid* grid;
    Instance** source;
    uint64_t revision;
    size_t count;
    int frame;
    bool compatibility, valid;
    uint16_t memberGroup[LINE_MEMBER_LIMIT];
    uint16_t dirtyGroups;
    bool dirty[LINE_MEMBER_LIMIT / LINE_CHUNK];
    InstanceBBox bounds[LINE_MEMBER_LIMIT / LINE_CHUNK];
} LineChunks;

static LineChunks lineChunks;
static unsigned lineChunkQueries, lineChunkBuilds, lineChunkRefreshes, lineChunkRejects, lineChunkInstances;

static void invalidateLineChunks(const DataWin* data) {
    if (lineChunks.archive == data) lineChunks.valid = false;
}

void hldInvalidateLineChunkMember(const SpatialGrid* grid, const Instance* inst) {
    const uint32_t member = inst->instanceId - INSTANCE_ID_BASE;
    if (!lineChunks.valid || lineChunks.grid != grid || member >= LINE_MEMBER_LIMIT) return;
    const unsigned group = lineChunks.memberGroup[member];
    if (group != 0 && !lineChunks.dirty[group - 1]) {
        lineChunks.dirty[group - 1] = true;
        ++lineChunks.dirtyGroups;
    }
}

static InstanceBBox computeLineChunkBounds(Runner* runner, Instance** source, size_t count, size_t group) {
    InstanceBBox bound = { .left = 1048576, .top = 1048576,
                          .right = -1048576, .bottom = -1048576, .valid = true };
    for (size_t j = group * LINE_CHUNK; j < count && j < (group + 1) * LINE_CHUNK; ++j) {
        Instance* instance = source[j];
        const InstanceBBox box = Collision_computeBBox(runner, instance);
        if (!box.valid) continue;
        if (instance->imageAngle != 0.0 || !(GMLReal_fabs(box.left) < 1048576 &&
            GMLReal_fabs(box.right) < 1048576 && GMLReal_fabs(box.top) < 1048576 &&
            GMLReal_fabs(box.bottom) < 1048576)) {
            bound.valid = false;
            break;
        }
        if (box.left < bound.left) bound.left = box.left;
        if (box.top < bound.top) bound.top = box.top;
        if (box.right > bound.right) bound.right = box.right;
        if (box.bottom > bound.bottom) bound.bottom = box.bottom;
    }
    return bound;
}

static const InstanceBBox* getLineChunks(Runner* runner, Instance** source, size_t count, int32_t target) {
    if (count < 2 * LINE_CHUNK || count > LINE_MEMBER_LIMIT) return nullptr;
    const char* name = runner->dataWin->objt.objects[target].name;
    if (name == nullptr || strcmp(name, "block") != 0) return nullptr;

    ++lineChunkQueries;
    const size_t chunkCount = (count + LINE_CHUNK - 1) / LINE_CHUNK;
    if (lineChunks.valid && lineChunks.runner == runner && lineChunks.source == source &&
        lineChunks.grid == runner->spatialGrid && lineChunks.count == count &&
        lineChunks.frame == runner->frameCount && lineChunks.revision == runner->instanceListRevision &&
        lineChunks.compatibility == runner->collisionCompatibilityMode) {
        for (size_t group = 0; lineChunks.dirtyGroups != 0 && group < chunkCount; ++group) {
            if (!lineChunks.dirty[group]) continue;
            lineChunks.bounds[group] = computeLineChunkBounds(runner, source, count, group);
            lineChunks.dirty[group] = false;
            --lineChunks.dirtyGroups;
            ++lineChunkRefreshes;
        }
        return lineChunks.bounds;
    }

    ++lineChunkBuilds;
    lineChunks.valid = false;
    memset(lineChunks.memberGroup, 0, sizeof(lineChunks.memberGroup));
    memset(lineChunks.dirty, 0, sizeof(lineChunks.dirty));
    lineChunks.dirtyGroups = 0;
    for (size_t ordinal = 0; ordinal < count; ++ordinal) {
        const uint32_t member = source[ordinal]->instanceId - INSTANCE_ID_BASE;
        if (member >= LINE_MEMBER_LIMIT) return nullptr;
        lineChunks.memberGroup[member] = (uint16_t)(ordinal / LINE_CHUNK + 1);
    }
    for (size_t group = 0; group < chunkCount; ++group)
        lineChunks.bounds[group] = computeLineChunkBounds(runner, source, count, group);
    lineChunks.archive = runner->dataWin;
    lineChunks.runner = runner;
    lineChunks.grid = runner->spatialGrid;
    lineChunks.source = source;
    lineChunks.count = count;
    lineChunks.frame = runner->frameCount;
    lineChunks.revision = runner->instanceListRevision;
    lineChunks.compatibility = runner->collisionCompatibilityMode;
    lineChunks.valid = true;
    return lineChunks.bounds;
}

void hldReportLineChunks(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("HLD line chunks frame=%d queries=%u builds=%u refreshes=%u rejected=%u instances=%u\n",
            frame, lineChunkQueries, lineChunkBuilds, lineChunkRefreshes, lineChunkRejects, lineChunkInstances);
    lineChunkQueries = lineChunkBuilds = lineChunkRefreshes = lineChunkRejects = lineChunkInstances = 0;
}
'''
    if launch_flag:
        anchor = "static const InstanceBBox* getLineChunks(Runner* runner, Instance** source, size_t count, int32_t target) {"
        helper = replace_once(helper, anchor,
                              f"extern bool {launch_flag};\n\n" + anchor +
                              f"\n    if (!{launch_flag}) return nullptr;")
    if retain_order:
        helper = replace_once(helper, "    uint16_t memberGroup[LINE_MEMBER_LIMIT];",
                              "    uint32_t orderedIds[LINE_MEMBER_LIMIT];\n    uint16_t memberGroup[LINE_MEMBER_LIMIT];")
        anchor = "static const InstanceBBox* getLineChunks("
        helper = replace_once(helper, anchor, r'''
void hldInvalidateLineChunkGrid(const SpatialGrid* grid) {
    if (lineChunks.grid == grid) lineChunks.valid = false;
}

static unsigned lineChunkOrderReuses;

static bool keepLineChunkOrder(const Runner* runner, Instance** source, size_t count) {
    if (lineChunks.revision == runner->instanceListRevision) return true;
    for (size_t ordinal = 0; ordinal < count; ++ordinal) {
        if (lineChunks.orderedIds[ordinal] != source[ordinal]->instanceId) return false;
    }
    lineChunks.revision = runner->instanceListRevision;
    ++lineChunkOrderReuses;
    return true;
}

''' + anchor)
        helper = replace_once(helper, "lineChunks.frame == runner->frameCount && lineChunks.revision == runner->instanceListRevision &&\n        lineChunks.compatibility == runner->collisionCompatibilityMode)",
                              "lineChunks.frame == runner->frameCount && lineChunks.compatibility == runner->collisionCompatibilityMode &&\n        keepLineChunkOrder(runner, source, count))")
        anchor = "        lineChunks.memberGroup[member] = (uint16_t)(ordinal / LINE_CHUNK + 1);"
        helper = replace_once(helper, anchor, "        lineChunks.orderedIds[ordinal] = source[ordinal]->instanceId;\n" + anchor)
        anchor = "    lineChunkQueries = lineChunkBuilds"
        helper = replace_once(helper, anchor, r'''    logInfo("HLD line chunk order frame=%d reuses=%u\n", frame, lineChunkOrderReuses);
    lineChunkOrderReuses = 0;
''' + anchor)
    if persistent:
        helper = replace_once(helper, "    uint64_t revision;", "    int32_t target;")
        helper = replace_once(helper, "    int frame;\n", "")
        helper = replace_once(helper, "lineChunks.frame == runner->frameCount && lineChunks.revision == runner->instanceListRevision &&",
                              "lineChunks.target == target &&")
        helper = replace_once(helper, "    lineChunks.frame = runner->frameCount;\n    lineChunks.revision = runner->instanceListRevision;",
                              "    lineChunks.target = target;")
        anchor = "static const InstanceBBox* getLineChunks("
        helper = replace_once(helper, anchor, r'''
void hldInvalidateLineChunkGrid(const SpatialGrid* grid) {
    if (lineChunks.grid == grid) lineChunks.valid = false;
}

void hldInvalidateLineChunkBucket(const Runner* runner, int32_t target) {
    if (lineChunks.runner == runner && (target < 0 || lineChunks.target == target))
        lineChunks.valid = false;
}

''' + anchor)
    if obstacle_buckets:
        assert persistent and not retain_order
        helper = with_obstacle_line_buckets(helper)
    anchor = "void hldReleaseLineEnvelopes(const DataWin* data) {"
    code = replace_once(code, anchor, helper + "\n" + anchor + "\n    invalidateLineChunks(data);")
    anchor = "static RValue builtin_sprite_add("
    code = replace_once(code, anchor, "static void invalidateLineChunks(const DataWin* data);\n\n" + anchor)
    for method in ("sprite_add", "sprite_collision_mask", "sprite_create_from_surface", "sprite_delete", "sprite_set_bbox_mode"):
        start = code.index(f"static RValue builtin_{method}(")
        brace = code.index("{", start)
        code = code[:brace + 1] + "\n    invalidateLineChunks(ctx->dataWin);" + code[brace + 1:]
    anchor = "static RValue builtin_action_sprite_set("
    code = replace_once(code, anchor, "void hldInvalidateLineChunkMember(const SpatialGrid* grid, const Instance* inst);\n\n" + anchor)
    anchor = "    inst->spriteIndex = spriteId;"
    code = replace_once(code, anchor,
                        "    if (inst->maskIndex < 0 && inst->spriteIndex != spriteId)\n"
                        "        hldInvalidateLineChunkMember(ctx->runner->spatialGrid, inst);\n" + anchor)

    anchor = "    for (size_t candidate = 0; candidateCount > candidate; ++candidate) {"
    check = r'''
                for (size_t ordinal = candidate; ordinal < end; ++ordinal) {
                    Instance* instance = candidates[ordinal];
                    if (instance->active && (!notme || instance != self))
                        require(!Collision_lineOverlapsInstance(runner, instance, lx1, ly1, lx2, ly2));
                }
''' if verify else ""
    sample = r'''
    static int lastLineReport = -1;
    if (runner->frameCount != lastLineReport && runner->frameCount % 90 == 0) {
        lastLineReport = runner->frameCount;
        hldReportLineChunks(lastLineReport);
    }
''' if verify or report else ""
    loop = sample + r'''    const InstanceBBox* groups = snapBase < 0 && envelope.sprites != nullptr
        ? getLineChunks(runner, candidates, candidateCount, targetObjIndex) : nullptr;
''' + anchor + r'''
        if (groups != nullptr && candidate % LINE_CHUNK == 0) {
            const InstanceBBox* group = &groups[candidate / LINE_CHUNK];
            if (group->valid && (group->right < envelope.left || group->left > envelope.right ||
                                 group->bottom < envelope.top || group->top > envelope.bottom)) {
                const size_t end = candidate + LINE_CHUNK < candidateCount ? candidate + LINE_CHUNK : candidateCount;
''' + check + r'''                ++lineChunkRejects;
                lineChunkInstances += (unsigned)(end - candidate);
                candidate = end - 1;
                continue;
            }
        }'''
    if obstacle_buckets:
        loop = replace_once(loop, "const InstanceBBox* groups =", "LineChunks* groups =")
        loop = replace_once(loop, "&groups[candidate / LINE_CHUNK]", "&groups->bounds[candidate / LINE_CHUNK]")
        loop = replace_once(loop, "                ++lineChunkRejects;",
                            "                groups->skipped += (unsigned)(end - candidate);\n"
                            "                ++lineChunkRejects;")
    return replace_once(code, anchor, loop)


def with_obstacle_line_buckets(helper):
    """Keep independent bounds for the measured line-query families."""
    helper = replace_once(helper, "} LineChunks;", "    unsigned queries, skipped;\n} LineChunks;")
    helper = replace_once(helper, "static LineChunks lineChunks;", r'''static const char* const lineChunkBucketNames[] = {"block", "PathFindObstacle", "DecorXRayBlock"};
enum { LINE_CHUNK_BUCKETS = sizeof(lineChunkBucketNames) / sizeof(*lineChunkBucketNames) };
static LineChunks lineChunkBuckets[LINE_CHUNK_BUCKETS];''')
    helper = replace_once(helper, "    if (lineChunks.archive == data) lineChunks.valid = false;", '''    for (unsigned bucket = 0; bucket < LINE_CHUNK_BUCKETS; ++bucket) {
        LineChunks* cache = &lineChunkBuckets[bucket];
        if (cache->archive == data) cache->valid = false;
    }''')
    helper = replace_once(helper, r'''    if (!lineChunks.valid || lineChunks.grid != grid || member >= LINE_MEMBER_LIMIT) return;
    const unsigned group = lineChunks.memberGroup[member];
    if (group != 0 && !lineChunks.dirty[group - 1]) {
        lineChunks.dirty[group - 1] = true;
        ++lineChunks.dirtyGroups;
    }''', '''    if (member >= LINE_MEMBER_LIMIT) return;
    for (unsigned bucket = 0; bucket < LINE_CHUNK_BUCKETS; ++bucket) {
        LineChunks* cache = &lineChunkBuckets[bucket];
        if (!cache->valid || cache->grid != grid) continue;
        const unsigned group = cache->memberGroup[member];
        if (group != 0 && !cache->dirty[group - 1]) {
            cache->dirty[group - 1] = true;
            ++cache->dirtyGroups;
        }
    }''')
    helper = replace_once(helper, "    if (lineChunks.grid == grid) lineChunks.valid = false;", '''    for (unsigned bucket = 0; bucket < LINE_CHUNK_BUCKETS; ++bucket) {
        LineChunks* cache = &lineChunkBuckets[bucket];
        if (cache->grid == grid) cache->valid = false;
    }''')
    helper = replace_once(helper, '''    if (lineChunks.runner == runner && (target < 0 || lineChunks.target == target))
        lineChunks.valid = false;''', '''    for (unsigned bucket = 0; bucket < LINE_CHUNK_BUCKETS; ++bucket) {
        LineChunks* cache = &lineChunkBuckets[bucket];
        if (cache->runner == runner && (target < 0 || cache->target == target)) cache->valid = false;
    }''')
    start = helper.index("static const InstanceBBox* getLineChunks(")
    end = helper.index("\n}\n", start)
    lookup = helper[start:end]
    lookup = replace_once(lookup, "static const InstanceBBox*", "static LineChunks*")
    lookup = replace_once(lookup, '''    if (name == nullptr || strcmp(name, "block") != 0) return nullptr;''', '''    if (name == nullptr) return nullptr;
    unsigned bucket = 0;
    while (bucket < LINE_CHUNK_BUCKETS && strcmp(name, lineChunkBucketNames[bucket]) != 0) ++bucket;
    if (bucket == LINE_CHUNK_BUCKETS) return nullptr;
    LineChunks* cache = &lineChunkBuckets[bucket];''')
    lookup = lookup.replace("lineChunks.", "cache->")
    lookup = replace_once(lookup, "        return cache->bounds;", "        ++cache->queries;\n        return cache;")
    lookup = replace_once(lookup, "    cache->valid = true;\n    return cache->bounds;",
                          "    cache->valid = true;\n    ++cache->queries;\n    return cache;")
    helper = helper[:start] + lookup + helper[end:]
    return replace_once(helper, "    lineChunkQueries = lineChunkBuilds", r'''    for (unsigned bucket = 0; bucket < LINE_CHUNK_BUCKETS; ++bucket) {
        LineChunks* cache = &lineChunkBuckets[bucket];
        if (cache->queries == 0) continue;
        logInfo("HLD line chunk bucket frame=%d target=%s queries=%u skipped=%u\n", frame,
                lineChunkBucketNames[bucket], cache->queries, cache->skipped);
        cache->queries = cache->skipped = 0;
    }
    lineChunkQueries = lineChunkBuilds''')


def with_line_chunk_grid_hook(code, *, retain_order=False, persistent=False):
    anchor = "void SpatialGrid_markInstanceAsDirty(SpatialGrid* grid, Instance* dirtyInstance) {"
    declaration = "void hldInvalidateLineChunkMember(const SpatialGrid* grid, const Instance* inst);\n\n"
    code = replace_once(code, anchor, declaration + anchor + "\n    hldInvalidateLineChunkMember(grid, dirtyInstance);")
    if retain_order or persistent:
        anchor = "void SpatialGrid_free(SpatialGrid* grid) {"
        code = replace_once(code, anchor, "void hldInvalidateLineChunkGrid(const SpatialGrid* grid);\n\n" +
                            anchor + "\n    hldInvalidateLineChunkGrid(grid);")
        anchor = "void SpatialGrid_removeInstance(SpatialGrid* grid, Instance* instance) {"
        code = replace_once(code, anchor, declaration + anchor + "\n    hldInvalidateLineChunkMember(grid, instance);")
    return code


def with_line_chunk_bucket_hook(code):
    anchor = "void Runner_addInstanceToObjectLists(Runner* runner, Instance* inst) {"
    code = replace_once(code, anchor, "void hldInvalidateLineChunkBucket(const Runner* runner, int32_t target);\n\n" + anchor)
    for anchor in ("        arrput(runner->instancesByObject[currentObj], inst);",
                   "        removeInstanceFromList(&runner->instancesByObject[currentObj], inst);"):
        code = replace_once(code, anchor, "        hldInvalidateLineChunkBucket(runner, currentObj);\n" + anchor)
    anchor = "void Runner_clearAllObjectLists(Runner* runner) {"
    return replace_once(code, anchor, anchor + "\n    hldInvalidateLineChunkBucket(runner, -1);")
