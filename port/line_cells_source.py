"""Line cells: a per-object 64 px grid for collision_line over the line-chunk targets (block, PathFindObstacle,
DecorXRayBlock).

collision_line returns the first instance, in object-list order, whose shape the segment touches. Codex's line chunks
skip groups of 16 instances whose union box misses the segment's envelope; in a large room a long sight line still
overlaps most groups (Big Bog Lab: ~4,800 instances tested per frame for ~80 hits). The cells list every instance
under each 64 px cell of its box (as of the last build); a query visits the cells the segment crosses (expanded by
a pixel), adds every instance moved, resized or re-created since the build (the same engine notifications that keep
the line chunks exact), and tests that candidate set in ascending list order with the unchanged per-instance test,
so the first hit is the same instance. Everything is added at the end of the file and hooked on existing lines, so
line numbers (and compiler feedback) for the rest of vm_builtins.c are unchanged."""

import os

from runtime_verification_source import replace_once

LINE_CELLS = r"""

// ===[ Line cells ]===
enum { HLD_CELL = 64, HLD_CELL_SPAN_LIMIT = 256, HLD_CELL_DIRTY_LIMIT = 64 };
typedef struct {
    const LineChunks* cache;   // the line-chunk cache this index was built with (same source/count/grid)
    Instance** source;
    size_t count;
    double originX, originY;
    int32_t cols, rows;
    uint32_t* cellStart;       // cols*rows+1 offsets into cellItems
    uint32_t* cellItems;       // ordinals
    uint32_t* always;          // ordinals with no box or a very large box: always tested
    uint32_t alwaysCount;
    uint16_t* ordinalOf;       // instanceId - INSTANCE_ID_BASE -> ordinal + 1
    uint32_t* dirty;           // ordinals changed since the build
    uint32_t dirtyCount;
    uint8_t* dirtyFlag;
    uint32_t* stamp;           // per ordinal: last query that collected it
    uint32_t query;
    uint32_t* visit;
    bool valid;
    unsigned builds, uses, visited;
} HldLineCells;
static HldLineCells hldLineCells[LINE_CHUNK_BUCKETS];

static void hldLineCellsFree(HldLineCells* cells) {
    free(cells->cellStart); free(cells->cellItems); free(cells->always); free(cells->ordinalOf);
    free(cells->dirty); free(cells->dirtyFlag); free(cells->stamp); free(cells->visit);
    const unsigned builds = cells->builds, uses = cells->uses, visited = cells->visited;
    memset(cells, 0, sizeof(*cells));
    cells->builds = builds; cells->uses = uses; cells->visited = visited;
}

static bool hldLineCellsDisabled(void) {
    static int disabled = -1;
    if (disabled < 0) disabled = getenv("HLD_NO_LINE_CELLS") != nullptr;
    return disabled;
}

void hldLineCellBuild(LineChunks* cache, unsigned bucket, Runner* runner) {
    if (bucket >= LINE_CHUNK_BUCKETS || hldLineCellsDisabled()) return;
    HldLineCells* cells = &hldLineCells[bucket];
    hldLineCellsFree(cells);
    const size_t count = cache->count;
    Instance** source = cache->source;
    InstanceBBox* boxes = (InstanceBBox*) malloc((count ? count : 1) * sizeof(InstanceBBox));
    cells->ordinalOf = (uint16_t*) calloc(LINE_MEMBER_LIMIT, sizeof(uint16_t));
    cells->always = (uint32_t*) malloc((count + 1) * sizeof(uint32_t));
    cells->dirty = (uint32_t*) malloc((HLD_CELL_DIRTY_LIMIT + 1) * sizeof(uint32_t));
    cells->dirtyFlag = (uint8_t*) calloc(count + 1, 1);
    cells->stamp = (uint32_t*) calloc(count + 1, sizeof(uint32_t));
    cells->visit = (uint32_t*) malloc((count + 1) * sizeof(uint32_t));
    if (boxes == nullptr || cells->ordinalOf == nullptr || cells->always == nullptr || cells->dirty == nullptr ||
        cells->dirtyFlag == nullptr || cells->stamp == nullptr || cells->visit == nullptr) {
        free(boxes); hldLineCellsFree(cells); return;
    }
    double left = 1e30, top = 1e30, right = -1e30, bottom = -1e30;
    for (size_t i = 0; i < count; ++i) {
        const uint32_t member = source[i]->instanceId - INSTANCE_ID_BASE;
        if (member < LINE_MEMBER_LIMIT) cells->ordinalOf[member] = (uint16_t) (i + 1);
        boxes[i] = Collision_computeBBox(runner, source[i]);
        const InstanceBBox* b = &boxes[i];
        if (!b->valid || !(fabs(b->left) < 1048576 && fabs(b->right) < 1048576 && fabs(b->top) < 1048576 &&
                           fabs(b->bottom) < 1048576)) { boxes[i].valid = false; continue; }
        if (b->left < left) left = b->left;
        if (b->top < top) top = b->top;
        if (b->right > right) right = b->right;
        if (b->bottom > bottom) bottom = b->bottom;
    }
    if (left > right) { left = top = 0; right = bottom = 1; }
    cells->originX = floor(left) - 2.0;
    cells->originY = floor(top) - 2.0;
    cells->cols = (int32_t) ((right + 2.0 - cells->originX) / HLD_CELL) + 1;
    cells->rows = (int32_t) ((bottom + 2.0 - cells->originY) / HLD_CELL) + 1;
    if (cells->cols <= 0 || cells->rows <= 0 || (int64_t) cells->cols * cells->rows > 1 << 20) { free(boxes); hldLineCellsFree(cells); return; }
    const size_t cellCount = (size_t) cells->cols * cells->rows;
    cells->cellStart = (uint32_t*) calloc(cellCount + 1, sizeof(uint32_t));
    if (cells->cellStart == nullptr) { free(boxes); hldLineCellsFree(cells); return; }
    // Two passes: count per cell, then fill (counting sort keeps each cell's ordinals ascending).
    size_t items = 0;
    for (int pass = 0; pass < 2; ++pass) {
        uint32_t* cursor = nullptr;
        if (pass == 1) {
            uint32_t running = 0;
            for (size_t c = 0; c < cellCount; ++c) { const uint32_t n = cells->cellStart[c]; cells->cellStart[c] = running; running += n; }
            cells->cellStart[cellCount] = running;
            cells->cellItems = (uint32_t*) malloc((running + 1) * sizeof(uint32_t));
            cursor = (uint32_t*) malloc((cellCount + 1) * sizeof(uint32_t));
            if (cells->cellItems == nullptr || cursor == nullptr) { free(cursor); free(boxes); hldLineCellsFree(cells); return; }
            memcpy(cursor, cells->cellStart, (cellCount + 1) * sizeof(uint32_t));
            cells->alwaysCount = 0;
        }
        for (size_t i = 0; i < count; ++i) {
            const InstanceBBox* b = &boxes[i];
            int32_t c0 = 0, c1 = -1, r0 = 0, r1 = -1;
            if (b->valid) {
                c0 = (int32_t) floor((b->left - 1.0 - cells->originX) / HLD_CELL);
                c1 = (int32_t) floor((b->right + 1.0 - cells->originX) / HLD_CELL);
                r0 = (int32_t) floor((b->top - 1.0 - cells->originY) / HLD_CELL);
                r1 = (int32_t) floor((b->bottom + 1.0 - cells->originY) / HLD_CELL);
                if (c0 < 0) c0 = 0;
                if (r0 < 0) r0 = 0;
                if (c1 >= cells->cols) c1 = cells->cols - 1;
                if (r1 >= cells->rows) r1 = cells->rows - 1;
            }
            if (!b->valid || (int64_t) (c1 - c0 + 1) * (r1 - r0 + 1) > HLD_CELL_SPAN_LIMIT) {
                if (pass == 1) cells->always[cells->alwaysCount++] = (uint32_t) i;
                continue;
            }
            for (int32_t r = r0; r <= r1; ++r)
                for (int32_t c = c0; c <= c1; ++c) {
                    const size_t cell = (size_t) r * cells->cols + c;
                    if (pass == 0) ++cells->cellStart[cell];
                    else cells->cellItems[cursor[cell]++] = (uint32_t) i;
                }
        }
        free(cursor);
        (void) items;
    }
    free(boxes);
    cells->cache = cache;
    cells->source = source;
    cells->count = count;
    cells->valid = true;
    ++cells->builds;
}

void hldLineCellMoved(unsigned bucket, uint32_t member) {
    if (bucket >= LINE_CHUNK_BUCKETS) return;
    HldLineCells* cells = &hldLineCells[bucket];
    if (!cells->valid || member >= LINE_MEMBER_LIMIT || cells->ordinalOf[member] == 0) return;
    const uint32_t ordinal = cells->ordinalOf[member] - 1u;
    if (cells->dirtyFlag[ordinal]) return;
    if (cells->dirtyCount >= HLD_CELL_DIRTY_LIMIT) { cells->valid = false; return; }  // rebuilt with the next chunk build
    cells->dirtyFlag[ordinal] = 1;
    cells->dirty[cells->dirtyCount++] = ordinal;
}

// Does the segment touch the rectangle [x0,x1]x[y0,y1]? (Liang-Barsky; the rectangle is already expanded.)
static bool hldSegmentTouches(double ax, double ay, double bx, double by, double x0, double y0, double x1, double y1) {
    double t0 = 0.0, t1 = 1.0;
    const double dx = bx - ax, dy = by - ay;
    const double p[4] = {-dx, dx, -dy, dy};
    const double q[4] = {ax - x0, x1 - ax, ay - y0, y1 - ay};
    for (int i = 0; i < 4; ++i) {
        if (p[i] == 0.0) { if (q[i] < 0.0) return false; continue; }
        const double t = q[i] / p[i];
        if (p[i] < 0.0) { if (t > t1) return false; if (t > t0) t0 = t; }
        else { if (t < t0) return false; if (t < t1) t1 = t; }
    }
    return true;
}

static int hldOrdinalCompare(const void* a, const void* b) {
    const uint32_t x = *(const uint32_t*) a, y = *(const uint32_t*) b;
    return x < y ? -1 : x > y;
}

// Ordinals (ascending) that can possibly touch the segment, or nullptr to scan the list as before.
const uint32_t* hldLineCellVisit(const LineChunks* cache, GMLReal ax, GMLReal ay, GMLReal bx, GMLReal by, size_t* outCount) {
    const unsigned bucket = (unsigned) (cache - lineChunkBuckets);
    if (bucket >= LINE_CHUNK_BUCKETS) return nullptr;
    HldLineCells* cells = &hldLineCells[bucket];
    if (!cells->valid || cells->cache != cache || cells->source != cache->source || cells->count != cache->count) {
        if (!cache->valid || hldLineCellsDisabled()) return nullptr;
        hldLineCellBuild((LineChunks*) cache, bucket, (Runner*) cache->runner);
        if (!cells->valid) return nullptr;
    }
    if (!(fabs(ax) < 1048576 && fabs(ay) < 1048576 && fabs(bx) < 1048576 && fabs(by) < 1048576)) return nullptr;
    if (++cells->query == 0) { memset(cells->stamp, 0, cells->count * sizeof(uint32_t)); cells->query = 1; }
    const uint32_t query = cells->query;
    size_t n = 0;
    const double minX = fmin(ax, bx) - 2.0, maxX = fmax(ax, bx) + 2.0, minY = fmin(ay, by) - 2.0, maxY = fmax(ay, by) + 2.0;
    int32_t c0 = (int32_t) floor((minX - cells->originX) / HLD_CELL), c1 = (int32_t) floor((maxX - cells->originX) / HLD_CELL);
    int32_t r0 = (int32_t) floor((minY - cells->originY) / HLD_CELL), r1 = (int32_t) floor((maxY - cells->originY) / HLD_CELL);
    if (c0 < 0) c0 = 0;
    if (r0 < 0) r0 = 0;
    if (c1 >= cells->cols) c1 = cells->cols - 1;
    if (r1 >= cells->rows) r1 = cells->rows - 1;
    for (int32_t r = r0; r <= r1; ++r)
        for (int32_t c = c0; c <= c1; ++c) {
            const double x0 = cells->originX + (double) c * HLD_CELL - 2.0, y0 = cells->originY + (double) r * HLD_CELL - 2.0;
            if (!hldSegmentTouches(ax, ay, bx, by, x0, y0, x0 + HLD_CELL + 4.0, y0 + HLD_CELL + 4.0)) continue;
            const size_t cell = (size_t) r * cells->cols + c;
            for (uint32_t k = cells->cellStart[cell]; k < cells->cellStart[cell + 1]; ++k) {
                const uint32_t ordinal = cells->cellItems[k];
                if (cells->stamp[ordinal] == query) continue;
                cells->stamp[ordinal] = query;
                cells->visit[n++] = ordinal;
            }
        }
    for (uint32_t k = 0; k < cells->alwaysCount; ++k) {
        const uint32_t ordinal = cells->always[k];
        if (cells->stamp[ordinal] != query) { cells->stamp[ordinal] = query; cells->visit[n++] = ordinal; }
    }
    for (uint32_t k = 0; k < cells->dirtyCount; ++k) {
        const uint32_t ordinal = cells->dirty[k];
        if (cells->stamp[ordinal] != query) { cells->stamp[ordinal] = query; cells->visit[n++] = ordinal; }
    }
    qsort(cells->visit, n, sizeof(uint32_t), hldOrdinalCompare);
    ++cells->uses;
    cells->visited += (unsigned) n;
    *outCount = n;
    return cells->visit;
}

// Running hash of collision_line results (HLD_LINE_HASH): two builds that agree on every call print the same values.
uint64_t hldLineResultHash;
"""


def with_line_cells(code):
    if "} LineChunks;" not in code:
        return code
    if os.environ.get("HLDC_LINE_CELLS", "1") != "1":
        # Baseline for comparisons: only the result hash.
        start = code.index("static RValue builtin_collision_line(VMContext* ctx, RValue* args, int32_t argCount) {")
        end = code.index("\n}\n", start)
        body = replace_once(code[start:end], "    return RValue_makeReal((GMLReal) resultId);",
                            "    { extern uint64_t hldLineResultHash; hldLineResultHash = hldLineResultHash * 1000003u + (uint64_t) (int64_t) resultId; }"
                            " return RValue_makeReal((GMLReal) resultId);")
        return code[:start] + body + code[end:] + "\nuint64_t hldLineResultHash;\n"
    code = replace_once(code, "    cache->valid = true;\n    ++cache->queries;\n    return cache;",
                        "    cache->valid = true; { void hldLineCellBuild(LineChunks*, unsigned, Runner*); hldLineCellBuild(cache, bucket, runner); }\n"
                        "    ++cache->queries;\n    return cache;")
    code = replace_once(code, "        const unsigned group = cache->memberGroup[member];",
                        "        const unsigned group = cache->memberGroup[member]; "
                        "if (group != 0) { void hldLineCellMoved(unsigned, uint32_t); hldLineCellMoved(bucket, member); }")
    start = code.index("static RValue builtin_collision_line(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    body = replace_once(body, "    for (size_t candidate = 0; candidateCount > candidate; ++candidate) {",
                        "    const uint32_t* hldVisit = nullptr; size_t hldVisitCount = 0; if (groups != nullptr) { "
                        "const uint32_t* hldLineCellVisit(const LineChunks*, GMLReal, GMLReal, GMLReal, GMLReal, size_t*); "
                        "hldVisit = hldLineCellVisit(groups, lx1, ly1, lx2, ly2, &hldVisitCount); } "
                        "for (size_t hldStep = 0, candidate = 0; hldVisit != nullptr ? hldStep < hldVisitCount : candidateCount > candidate; "
                        "++hldStep, ++candidate) { if (hldVisit != nullptr) candidate = hldVisit[hldStep];")
    body = replace_once(body, "        if (groups != nullptr && candidate % LINE_CHUNK == 0) {",
                        "        if (groups != nullptr && hldVisit == nullptr && candidate % LINE_CHUNK == 0) {")
    body = replace_once(body, "    return RValue_makeReal((GMLReal) resultId);",
                        "    { extern uint64_t hldLineResultHash; hldLineResultHash = hldLineResultHash * 1000003u + (uint64_t) (int64_t) resultId; }"
                        " return RValue_makeReal((GMLReal) resultId);")
    return code[:start] + body + code[end:] + LINE_CELLS
