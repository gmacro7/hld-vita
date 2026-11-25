"""Find terrain line candidates with a persistent spatial tree, in original order."""

from runtime_verification_source import replace_once


INDEX = r'''
enum { LINE_TREE_LEAF = 8, LINE_TREE_WORD_BITS = 32 };

typedef struct {
    InstanceBBox bounds;
    GMLReal centerX, centerY;
    uint16_t ordinal;
} LineTreeMember;

typedef struct {
    InstanceBBox bounds;
    uint16_t start, count, left, right;
} LineTreeNode;

typedef struct {
    LineTreeMember* members;
    LineTreeNode* nodes;
    uint16_t* positions;
    uint16_t* selected;
    uint32_t* unbounded;
    uint32_t* marks;
    size_t capacity, nodeCount;
    bool valid, boundsDirty;
} LineTerrainTree;

static LineTerrainTree lineTerrainTree;
static unsigned lineTreeQueries, lineTreeEntries, lineTreeSelected, lineTreeVisits, lineTreeBuilds, lineTreeUpdates;

static InstanceBBox unionLineTreeBounds(InstanceBBox a, InstanceBBox b) {
    if (!a.valid) return b;
    if (!b.valid) return a;
    if (b.left < a.left) a.left = b.left;
    if (b.right > a.right) a.right = b.right;
    if (b.top < a.top) a.top = b.top;
    if (b.bottom > a.bottom) a.bottom = b.bottom;
    return a;
}

static size_t lineTreeWordCount(size_t count) {
    return (count + LINE_TREE_WORD_BITS - 1) / LINE_TREE_WORD_BITS;
}

static void setLineTreeMember(Runner* runner, Instance** source, LineTreeMember* member, uint16_t ordinal) {
    Instance* instance = source[ordinal];
    const InstanceBBox bounds = Collision_computeBBox(runner, instance);
    const bool bounded = bounds.valid && instance->imageAngle == 0.0 &&
        GMLReal_fabs(bounds.left) < 1048576 && GMLReal_fabs(bounds.right) < 1048576 &&
        GMLReal_fabs(bounds.top) < 1048576 && GMLReal_fabs(bounds.bottom) < 1048576 &&
        bounds.left <= bounds.right && bounds.top <= bounds.bottom;
    const uint32_t bit = UINT32_C(1) << (ordinal % LINE_TREE_WORD_BITS);
    uint32_t* word = &lineTerrainTree.unbounded[ordinal / LINE_TREE_WORD_BITS];
    // Rotated or unsupported boxes always reach the exact original test.
    if (bounded) *word &= ~bit;
    else *word |= bit;
    *member = (LineTreeMember){
        .bounds = bounded ? bounds : (InstanceBBox){0},
        .centerX = bounded ? bounds.left + bounds.right : 0,
        .centerY = bounded ? bounds.top + bounds.bottom : 0,
        .ordinal = ordinal,
    };
}

static int compareLineTreeX(const void* left, const void* right) {
    const LineTreeMember* a = left;
    const LineTreeMember* b = right;
    return (a->centerX > b->centerX) - (a->centerX < b->centerX);
}

static int compareLineTreeY(const void* left, const void* right) {
    const LineTreeMember* a = left;
    const LineTreeMember* b = right;
    return (a->centerY > b->centerY) - (a->centerY < b->centerY);
}

static uint16_t buildLineTreeNode(uint16_t start, uint16_t count) {
    const uint16_t nodeIndex = (uint16_t)lineTerrainTree.nodeCount++;
    LineTreeNode* node = &lineTerrainTree.nodes[nodeIndex];
    *node = (LineTreeNode){.start = start, .count = count};
    for (unsigned i = start; i < (unsigned)start + count; ++i) {
        node->bounds = unionLineTreeBounds(node->bounds, lineTerrainTree.members[i].bounds);
    }
    if (count <= LINE_TREE_LEAF) return nodeIndex;

    const bool splitX = node->bounds.right - node->bounds.left >= node->bounds.bottom - node->bounds.top;
    qsort(lineTerrainTree.members + start, count, sizeof(*lineTerrainTree.members),
          splitX ? compareLineTreeX : compareLineTreeY);
    const uint16_t half = count / 2;
    node->count = 0;
    node->left = buildLineTreeNode(start, half);
    node->right = buildLineTreeNode(start + half, count - half);
    return nodeIndex;
}

static void rebuildLineTerrainTree(Runner* runner, Instance** source, size_t count) {
    LineTerrainTree* tree = &lineTerrainTree;
    if (tree->capacity < count) {
        const size_t words = lineTreeWordCount(count);
        tree->members = safeRealloc(tree->members, count * sizeof(*tree->members));
        // A balanced tree split at eight members uses at most one node per member.
        tree->nodes = safeRealloc(tree->nodes, count * sizeof(*tree->nodes));
        tree->positions = safeRealloc(tree->positions, count * sizeof(*tree->positions));
        tree->selected = safeRealloc(tree->selected, count * sizeof(*tree->selected));
        tree->unbounded = safeRealloc(tree->unbounded, words * sizeof(*tree->unbounded));
        tree->marks = safeRealloc(tree->marks, words * sizeof(*tree->marks));
        tree->capacity = count;
    }
    memset(tree->unbounded, 0, lineTreeWordCount(count) * sizeof(*tree->unbounded));
    for (uint16_t ordinal = 0; ordinal < count; ++ordinal)
        setLineTreeMember(runner, source, &tree->members[ordinal], ordinal);
    tree->nodeCount = 0;
    buildLineTreeNode(0, (uint16_t)count);
    for (uint16_t i = 0; i < count; ++i) tree->positions[tree->members[i].ordinal] = i;
    tree->valid = true;
    tree->boundsDirty = false;
    ++lineTreeBuilds;
}

static void refreshLineTreeChunk(Runner* runner, Instance** source, size_t count, size_t group) {
    if (!lineTerrainTree.valid) return;
    for (size_t ordinal = group * LINE_CHUNK; ordinal < count && ordinal < (group + 1) * LINE_CHUNK; ++ordinal)
        setLineTreeMember(runner, source, &lineTerrainTree.members[lineTerrainTree.positions[ordinal]], (uint16_t)ordinal);
    lineTerrainTree.boundsDirty = true;
    ++lineTreeUpdates;
}

static void refitLineTreeBounds(void) {
    LineTerrainTree* tree = &lineTerrainTree;
    for (size_t i = tree->nodeCount; i > 0; --i) {
        LineTreeNode* node = &tree->nodes[i - 1];
        if (node->count == 0) {
            node->bounds = unionLineTreeBounds(tree->nodes[node->left].bounds, tree->nodes[node->right].bounds);
            continue;
        }
        node->bounds = (InstanceBBox){0};
        for (unsigned j = node->start; j < (unsigned)node->start + node->count; ++j)
            node->bounds = unionLineTreeBounds(node->bounds, tree->members[j].bounds);
    }
    tree->boundsDirty = false;
}

static bool intersectsLineTreeBox(const InstanceBBox* bounds, const LineEnvelopeQuery* envelope) {
    return bounds->valid && !(bounds->right < envelope->left || bounds->left > envelope->right ||
                              bounds->bottom < envelope->top || bounds->top > envelope->bottom);
}

static void markLineTreeCandidates(uint16_t index, const LineEnvelopeQuery* envelope) {
    const LineTreeNode* node = &lineTerrainTree.nodes[index];
    ++lineTreeVisits;
    if (!intersectsLineTreeBox(&node->bounds, envelope)) return;
    if (node->count == 0) {
        markLineTreeCandidates(node->left, envelope);
        markLineTreeCandidates(node->right, envelope);
        return;
    }
    for (unsigned i = node->start; i < (unsigned)node->start + node->count; ++i) {
        const LineTreeMember* member = &lineTerrainTree.members[i];
        if (intersectsLineTreeBox(&member->bounds, envelope))
            lineTerrainTree.marks[member->ordinal / LINE_TREE_WORD_BITS] |= UINT32_C(1) << (member->ordinal % LINE_TREE_WORD_BITS);
    }
}

static size_t selectLineTreeCandidates(Runner* runner, Instance** source, size_t count, const LineEnvelopeQuery* envelope) {
    LineTerrainTree* tree = &lineTerrainTree;
    if (!tree->valid) rebuildLineTerrainTree(runner, source, count);
    if (tree->boundsDirty) refitLineTreeBounds();
    const size_t words = lineTreeWordCount(count);
    memcpy(tree->marks, tree->unbounded, words * sizeof(*tree->marks));
    markLineTreeCandidates(0, envelope);
    size_t selected = 0;
    for (size_t i = 0; i < words; ++i) {
        uint32_t bits = tree->marks[i];
        while (bits != 0) {
            tree->selected[selected++] = (uint16_t)(i * LINE_TREE_WORD_BITS + __builtin_ctz(bits));
            bits &= bits - 1;
        }
    }
    ++lineTreeQueries;
    lineTreeEntries += (unsigned)count;
    lineTreeSelected += (unsigned)selected;
    return selected;
}

static void releaseLineTerrainTree(void) {
    free(lineTerrainTree.marks);
    free(lineTerrainTree.unbounded);
    free(lineTerrainTree.selected);
    free(lineTerrainTree.positions);
    free(lineTerrainTree.nodes);
    free(lineTerrainTree.members);
    lineTerrainTree = (LineTerrainTree){0};
}

static void reportLineTerrainTree(int frame) {
    logInfo("HLD line terrain tree frame=%d queries=%u entries=%u selected=%u node_visits=%u builds=%u chunk_updates=%u\n",
            frame, lineTreeQueries, lineTreeEntries, lineTreeSelected, lineTreeVisits, lineTreeBuilds, lineTreeUpdates);
    lineTreeQueries = lineTreeEntries = lineTreeSelected = lineTreeVisits = lineTreeBuilds = lineTreeUpdates = 0;
}
'''

CHECK = r'''
        for (size_t ordinal = 0; ordinal < originalCandidateCount; ++ordinal) {
            const uint32_t bit = UINT32_C(1) << (ordinal % LINE_TREE_WORD_BITS);
            if (lineTerrainTree.marks[ordinal / LINE_TREE_WORD_BITS] & bit) continue;
            Instance* instance = candidates[ordinal];
            if (instance->active && (!notme || instance != self))
                require(!Collision_lineOverlapsInstance(runner, instance, lx1, ly1, lx2, ly2));
        }
'''


def with_line_terrain_index(code, *, checked=False):
    anchor = "static void invalidateLineChunks(const DataWin* data) {"
    code = replace_once(code, anchor, INDEX + "\n" + anchor)
    anchor = "            lineChunks.bounds[group] = computeLineChunkBounds(runner, source, count, group);"
    code = replace_once(code, anchor, anchor + "\n            refreshLineTreeChunk(runner, source, count, group);")
    anchor = "    ++lineChunkBuilds;\n    lineChunks.valid = false;"
    code = replace_once(code, anchor, anchor + "\n    lineTerrainTree.valid = false;")
    anchor = "void hldReleaseLineEnvelopes(const DataWin* data) {"
    code = replace_once(code, anchor, anchor + "\n    if (lineChunks.archive == data) releaseLineTerrainTree();")
    anchor = "    lineChunkQueries = lineChunkBuilds = lineChunkRefreshes = lineChunkRejects = lineChunkInstances = 0;"
    code = replace_once(code, anchor, "    reportLineTerrainTree(frame);\n" + anchor)
    anchor = "    for (size_t candidate = 0; candidateCount > candidate; ++candidate) {\n        if (groups != nullptr &&"
    setup = r'''    const uint16_t* lineOrdinals = nullptr;
    if (groups != nullptr) {
        const size_t originalCandidateCount = candidateCount;
        candidateCount = selectLineTreeCandidates(runner, candidates, originalCandidateCount, &envelope);
        lineOrdinals = lineTerrainTree.selected;
        groups = nullptr;
TREE_CHECK
    }
'''
    code = replace_once(code, anchor, setup.replace("TREE_CHECK", CHECK if checked else "") + anchor)
    anchor = "        Instance* inst = candidates[candidate];"
    return replace_once(code, anchor,
                        "        Instance* inst = candidates[lineOrdinals == nullptr ? candidate : lineOrdinals[candidate]];")
