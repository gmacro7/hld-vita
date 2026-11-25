import os
from pathlib import Path
"""Engine builtin speedups for the translated build. Each keeps its function's line count, so compiler
feedback for the code after it still matches."""

from runtime_verification_source import replace_once

INSTANCE_EXISTS_SNAPSHOT = """        int32_t snapBase = Runner_pushInstancesOfObject(runner, id);
        int32_t snapEnd  = (int32_t) arrlen(runner->instanceSnapshots);
        for (int32_t i = snapBase; snapEnd > i; i++) {
            if (runner->instanceSnapshots[i]->active) { found = true; break; }
        }
        Runner_popInstanceSnapshot(runner, snapBase);
"""

# No user code runs while instance_exists looks, so the object's bucket is scanned in place, not copied.
INSTANCE_EXISTS_IN_PLACE = """        Instance** source = runner->instancesByObject[id];
        const int32_t sourceCount = (int32_t) arrlen(source);
        for (int32_t i = 0; sourceCount > i; i++) {
            if (source[i]->active) { found = true; break; }
        }
        // (scanned in place: no snapshot)
"""


LINE_CALL = "            found = hldTestPreparedLineMask(spr, inst, bbox, xl, yl, xr, yr);"
LINE_DECLARATION = "static bool hldTestPreparedLineMask(Sprite* spr, Instance* inst, InstanceBBox bbox,"

# collision_line's precise walk, skipping runs of steps that can only land on empty mask cells. A coarse map
# marks each 8x8 block of a large mask that has any bit set. Steps go in chunks of eight; the chunk's first and
# last points are computed with the walk's own expressions, and because each step's coordinate is a monotonic
# function of the step, every step in between lands between them. When every mask cell in that range (widened
# by a pixel for rounding) is empty, each of those point tests would return false, so the chunk is skipped.
SKIPPING_WALK = r"""
enum { HLD_LINE_COARSE_MIN = 64 * 64, HLD_LINE_COARSE_SLOTS = 64 };
typedef struct { const uint8_t* mask; const Room* room; uint32_t width, height, coarseWidth; uint8_t* coarse; } HldCoarseMask;
static HldCoarseMask hldCoarseMasks[HLD_LINE_COARSE_SLOTS];

static const HldCoarseMask* hldCoarseMaskFor(Runner* runner, const Sprite* spr, const uint8_t* mask, uint32_t stride) {
    if (mask == nullptr || (uint64_t) spr->maskWidth * spr->maskHeight < HLD_LINE_COARSE_MIN) return nullptr;
    const uint32_t slot = (uint32_t)(((uintptr_t) mask >> 4) * 2654435761u) % HLD_LINE_COARSE_SLOTS;
    HldCoarseMask* entry = &hldCoarseMasks[slot];
    if (entry->mask == mask && entry->room == runner->currentRoom && entry->width == spr->maskWidth &&
        entry->height == spr->maskHeight) return entry;
    free(entry->coarse);
    const uint32_t coarseWidth = stride, coarseHeight = (spr->maskHeight + 7) / 8;
    uint8_t* coarse = safeCalloc((size_t) coarseWidth * coarseHeight, 1);
    for (uint32_t row = 0; row < spr->maskHeight; ++row) {
        const uint8_t* bits = mask + (size_t) row * stride;
        uint8_t* out = coarse + (size_t)(row / 8) * coarseWidth;
        for (uint32_t column = 0; column < stride; ++column) out[column] |= bits[column];
    }
    *entry = (HldCoarseMask){mask, runner->currentRoom, spr->maskWidth, spr->maskHeight, coarseWidth, coarse};
    return entry;
}

// Local pixel of a world coordinate on one axis, as Collision_testPoint computes it (unrotated queries only).
static inline int32_t hldLocalPixel(GMLReal world, GMLReal origin, GMLReal scale, int32_t spriteOrigin) {
    const GMLReal delta = world - origin;
    return (int32_t)((scale == 1.0 ? delta : delta / scale) + (GMLReal) spriteOrigin);
}

// True when no mask bit is set for local pixels [x0, x1] x [y0, y1] (any order), widened by one pixel.
static bool hldCoarseEmpty(const HldCoarseMask* coarse, const Sprite* spr, int32_t x0, int32_t x1, int32_t y0, int32_t y1) {
    int32_t left = (x0 < x1 ? x0 : x1) - 1 - spr->maskOffsetX, right = (x0 < x1 ? x1 : x0) + 1 - spr->maskOffsetX;
    int32_t top = (y0 < y1 ? y0 : y1) - 1 - spr->maskOffsetY, bottom = (y0 < y1 ? y1 : y0) + 1 - spr->maskOffsetY;
    if (left < 0) left = 0;
    if (top < 0) top = 0;
    if (right >= (int32_t) coarse->width) right = (int32_t) coarse->width - 1;
    if (bottom >= (int32_t) coarse->height) bottom = (int32_t) coarse->height - 1;
    if (left > right || top > bottom) return true;
    for (int32_t by = top / 8; by <= bottom / 8; ++by) {
        const uint8_t* row = coarse->coarse + (size_t) by * coarse->coarseWidth;
        for (int32_t bx = left / 8; bx <= right / 8; ++bx) if (row[bx] != 0) return false;
    }
    return true;
}

static bool hldTestSkippingLineMask(Runner* runner, Sprite* spr, Instance* inst, InstanceBBox bbox,
                                    GMLReal xl, GMLReal yl, GMLReal xr, GMLReal yr) {
    const CollisionPointQuery mask = Collision_preparePointQuery(spr, inst);
    const HldCoarseMask* coarse = mask.sprite != nullptr && !mask.rotated ? hldCoarseMaskFor(runner, spr, mask.mask, mask.stride) : nullptr;
    if (coarse == nullptr) return hldTestPreparedLineMask(spr, inst, bbox, xl, yl, xr, yr);
    ++lineMasksPrepared;
    GMLReal cdx = xr - xl;
    GMLReal cdy = yr - yl;
    bool found = false;

    if (GMLReal_fabs(cdy) >= GMLReal_fabs(cdx)) {
        GMLReal xt = xl, yt = yl, xb = xr, yb = yr;
        if (yt > yb) { GMLReal tmp = xt; xt = xb; xb = tmp; tmp = yt; yt = yb; yb = tmp; }
        GMLReal vdx = xb - xt;
        GMLReal vdy = yb - yt;

        int32_t startY = (int32_t) GMLReal_fmax(bbox.top, yt);
        int32_t endY   = (int32_t) GMLReal_fmin(bbox.bottom, yb);
        for (int32_t py = startY; endY >= py && !found;) {
            const int32_t last = endY - py >= 7 ? py + 7 : endY;
            const GMLReal first = (GMLReal_fabs(vdy) > 0.0001) ? xt + ((GMLReal) py - yt) * vdx / vdy : xt;
            const GMLReal final = (GMLReal_fabs(vdy) > 0.0001) ? xt + ((GMLReal) last - yt) * vdx / vdy : xt;
            if (hldCoarseEmpty(coarse, spr, hldLocalPixel(first + 0.5, mask.x, mask.scaleX, spr->originX),
                               hldLocalPixel(final + 0.5, mask.x, mask.scaleX, spr->originX),
                               hldLocalPixel((GMLReal) py + 0.5, mask.y, mask.scaleY, spr->originY),
                               hldLocalPixel((GMLReal) last + 0.5, mask.y, mask.scaleY, spr->originY))) {
                py = last + 1;
                continue;
            }
            for (; last >= py && !found; py++) {
                GMLReal px = (GMLReal_fabs(vdy) > 0.0001) ? xt + ((GMLReal) py - yt) * vdx / vdy : xt;
                if (Collision_testPoint(&mask, px + 0.5, (GMLReal) py + 0.5)) found = true;
            }
        }
    } else {
        int32_t startX = (int32_t) GMLReal_fmax(bbox.left, xl);
        int32_t endX   = (int32_t) GMLReal_fmin(bbox.right, xr);
        for (int32_t px = startX; endX >= px && !found;) {
            const int32_t last = endX - px >= 7 ? px + 7 : endX;
            const GMLReal first = (GMLReal_fabs(cdx) > 0.0001) ? yl + ((GMLReal) px - xl) * cdy / cdx : yl;
            const GMLReal final = (GMLReal_fabs(cdx) > 0.0001) ? yl + ((GMLReal) last - xl) * cdy / cdx : yl;
            if (hldCoarseEmpty(coarse, spr, hldLocalPixel((GMLReal) px + 0.5, mask.x, mask.scaleX, spr->originX),
                               hldLocalPixel((GMLReal) last + 0.5, mask.x, mask.scaleX, spr->originX),
                               hldLocalPixel(first + 0.5, mask.y, mask.scaleY, spr->originY),
                               hldLocalPixel(final + 0.5, mask.y, mask.scaleY, spr->originY))) {
                px = last + 1;
                continue;
            }
            for (; last >= px && !found; px++) {
                GMLReal py = (GMLReal_fabs(cdx) > 0.0001) ? yl + ((GMLReal) px - xl) * cdy / cdx : yl;
                if (Collision_testPoint(&mask, (GMLReal) px + 0.5, py + 0.5)) found = true;
            }
        }
    }
#ifdef HLD_VERIFY_SKIPPING_LINES
    require(found == hldTestPreparedLineMask(spr, inst, bbox, xl, yl, xr, yr));
    static unsigned verified;
    if (++verified == 100 || verified % 2000 == 0) logInfo("HLD: verified %u skipping line walks\n", verified);
#endif
    return found;
}
"""


CIRCLE_SYNC = """    SpatialGrid_syncGrid(runner, runner->spatialGrid);
    SpatialGridQuery query = SpatialGrid_prepareQuery(runner, qx1, qy1, qx2, qy2, targetObjIndex);

    int32_t resultId = INSTANCE_NOONE;
    for (int32_t gx = query.range.minGridX; query.range.maxGridX >= gx && resultId == INSTANCE_NOONE; gx++) {"""

# collision_circle against one object: scan that object's own instances (descendants included) with the same tests.
# No hit means no hit in the grid either; a single hit is the answer whatever the grid order; two or more hits
# leave the choice to the original grid scan. Called from the grid sync line so later line numbers stay put.
CIRCLE_BY_OBJECT = r"""
bool hldCircleByObject(VMContext* ctx, GMLReal cx, GMLReal cy, GMLReal radius, GMLReal radiusSq, GMLReal qx1,
                       GMLReal qy1, GMLReal qx2, GMLReal qy2, int32_t target, int32_t prec, int32_t notme,
                       Instance* self, int32_t* result) {
    Runner* runner = ctx->runner;
    if (target < 0 || target >= INSTANCE_ID_BASE || (uint32_t) target >= runner->dataWin->objt.count ||
        runner->instancesByObject == nullptr) return false;
    Instance** bucket = runner->instancesByObject[target];
    const int32_t count = (int32_t) arrlen(bucket);
    if (count > 64) return false;
    Instance* hit = nullptr;
    for (int32_t i = 0; i < count; ++i) {
        Instance* inst = bucket[i];
        if (!inst->active) continue;
        if (notme && inst == self) continue;
        if (!VM_isObjectOrDescendant(ctx->dataWin, inst->objectIndex, target)) continue;
        if (!Collision_circleOverlapsInstance(runner, inst, cx, cy, radius)) continue;
        if (prec != 0) {
            Sprite* spr = Collision_getSprite(ctx->dataWin, inst);
            if (Collision_hasFrameMasks(spr)) {
                InstanceBBox bbox = Collision_computeBBox(runner, inst);
                GMLReal iLeft   = GMLReal_fmax(qx1, bbox.left);
                GMLReal iRight  = GMLReal_fmin(qx2, bbox.right);
                GMLReal iTop    = GMLReal_fmax(qy1, bbox.top);
                GMLReal iBottom = GMLReal_fmin(qy2, bbox.bottom);
                bool found = false;
                int32_t startX = (int32_t) GMLReal_floor(iLeft);
                int32_t endX   = (int32_t) GMLReal_ceil(iRight);
                int32_t startY = (int32_t) GMLReal_floor(iTop);
                int32_t endY   = (int32_t) GMLReal_ceil(iBottom);
                const CollisionPointQuery pointQuery = Collision_preparePointQuery(spr, inst);
                for (int32_t py = startY; endY > py && !found; py++) {
                    for (int32_t px = startX; endX > px && !found; px++) {
                        GMLReal wpx = (GMLReal) px + 0.5;
                        GMLReal wpy = (GMLReal) py + 0.5;
                        GMLReal ddx = wpx - cx;
                        GMLReal ddy = wpy - cy;
                        if (ddx * ddx + ddy * ddy > radiusSq) continue;
                        if (Collision_testPoint(&pointQuery, wpx, wpy)) found = true;
                    }
                }
                if (!found) continue;
            }
        }
        if (hit != nullptr) return false;
        hit = inst;
    }
    *result = hit != nullptr ? (int32_t) hit->instanceId : INSTANCE_NOONE;
    return true;
}
"""


POINT_SNAPSHOT = """    int32_t snapBase = Runner_pushInstancesForTarget(runner, targetObjIndex);
    int32_t snapEnd  = (int32_t) arrlen(runner->instanceSnapshots);
    for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {
        Instance* inst = runner->instanceSnapshots[snapIdx];
"""
# collision_point runs no user code while it scans, so an object target's bucket is read in place, not copied.
POINT_IN_PLACE = """    const bool hldInPlace = targetObjIndex >= 0 && targetObjIndex < INSTANCE_ID_BASE && (uint32_t) targetObjIndex < runner->dataWin->objt.count; int32_t snapBase = hldInPlace ? 0 : Runner_pushInstancesForTarget(runner, targetObjIndex);
    Instance** hldSource = hldInPlace ? runner->instancesByObject[targetObjIndex] : runner->instanceSnapshots; int32_t snapEnd  = hldInPlace ? (int32_t) arrlen(hldSource) : (int32_t) arrlen(runner->instanceSnapshots);
    for (int32_t snapIdx = snapBase; snapEnd > snapIdx; snapIdx++) {
        Instance* inst = hldSource[snapIdx];
"""


def with_engine_speedups_builtins(code, verify=False):
    from cell_retention_source import with_cell_retention_builtins
    code = with_cell_retention_builtins(code)
    from line_cells_source import with_line_cells
    code = with_line_cells(code)
    from map_keys_source import with_numeric_map_keys
    code = with_numeric_map_keys(code)
    from placement_direct_source import with_direct_instance_placement
    code = with_direct_instance_placement(code)
    assert INSTANCE_EXISTS_SNAPSHOT.count("\n") == INSTANCE_EXISTS_IN_PLACE.count("\n")
    start = code.index("static RValue builtin_instance_exists(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = replace_once(code[start:end], INSTANCE_EXISTS_SNAPSHOT, INSTANCE_EXISTS_IN_PLACE)
    code = code[:start] + body + code[end:]
    start = code.index("static RValue builtin_collision_point(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = replace_once(code[start:end], POINT_SNAPSHOT, POINT_IN_PLACE)
    body = replace_once(body, "    Runner_popInstanceSnapshot(runner, snapBase);", "    if (!hldInPlace) Runner_popInstanceSnapshot(runner, snapBase);")
    code = code[:start] + body + code[end:]
    start = code.index("static RValue builtin_collision_circle(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    declaration = ("extern bool hldCircleByObject(VMContext*, GMLReal, GMLReal, GMLReal, GMLReal, GMLReal, GMLReal, GMLReal, "
                   "GMLReal, int32_t, int32_t, int32_t, Instance*, int32_t*);")
    call = ("hldCircleByObject(ctx, cx, cy, radius, radiusSq, qx1, qy1, qx2, qy2, targetObjIndex, prec, notme, self, "
            "&hldFast)")
    if verify:
        # The grid scan still runs and must return the same instance.
        hook = (f"    SpatialGrid_syncGrid(runner, runner->spatialGrid); int32_t hldFast = 0; {declaration} "
                f"const bool hldCircleFast = {call};")
    else:
        hook = (f"    SpatialGrid_syncGrid(runner, runner->spatialGrid); {{ {declaration} int32_t hldFast; "
                f"if ({call}) return RValue_makeReal((GMLReal) hldFast); }}")
    body = replace_once(code[start:end], CIRCLE_SYNC, CIRCLE_SYNC.replace(
        "    SpatialGrid_syncGrid(runner, runner->spatialGrid);", hook, 1))
    if verify:
        final = "    return RValue_makeReal((GMLReal) resultId);"
        assert body.rstrip().endswith(final)
        cut = body.rindex(final)
        body = body[:cut] + ("    require(!hldCircleFast || hldFast == resultId); { static unsigned checked; if (++checked % 5000 == 0) "
                             "logInfo(\"HLD: verified %u object circle queries\\n\", checked); } " + final) + body[cut + len(final):]
    code = code[:start] + body + code[end:] + CIRCLE_BY_OBJECT
    from placement_direct_source import with_circle_cells
    code = with_circle_cells(code)
    code = with_texture_flush_builtins(code)
    code = with_secure_save_appended(code)
    code = with_file_queue(code)
    code = with_sprite_add_timing(code)
    if LINE_CALL in code:
        code = replace_once(code, LINE_CALL, LINE_CALL.replace("hldTestPreparedLineMask(spr,", "hldTestSkippingLineMask(runner, spr,"))
        code = replace_once(code, LINE_DECLARATION, "static bool hldTestSkippingLineMask(Runner*, Sprite*, Instance*, InstanceBBox, "
                            "GMLReal, GMLReal, GMLReal, GMLReal); " + LINE_DECLARATION)
        code += ("#define HLD_VERIFY_SKIPPING_LINES\n" if verify else "") + SKIPPING_WALK
    return code


# draw_texture_flush, which HLD calls on every level load, only flushed the draw batch here, so each room's texture
# pages stayed resident until vitaGL's pools spilled into the heap and large decodes failed (missing textures,
# rooms without collision masks). As in GameMaker it now unloads the data.win texture pages; each keeps its GL
# name with a 1x1 placeholder and reloads on its next draw. Dynamic pages (sprite_add, surfaces) are untouched.
TEXTURE_FLUSH_CALL = "    if (ctx->runner->renderer) ctx->runner->renderer->vtable->flush(ctx->runner->renderer);"

UNLOAD_PAGES = r"""
void hldUnloadTexturePages(Renderer* renderer) {
    GLRenderer* gl = (GLRenderer*) renderer;
    flushBatch(gl);
    hldPagePrefetchSettle(gl);
    // A flush in the same room as the last one (a respawn or room restart) would only unload the pages this room is
    // about to draw again, ~0.3-0.6 s each: keep them all.
    static int32_t lastFlushRoom = -1;
    const int32_t flushRoom = renderer->runner != NULL ? renderer->runner->currentRoomIndex : -1;
    if (flushRoom >= 0 && flushRoom == lastFlushRoom && getenv("HLD_FLUSH_SAME_ROOM") == NULL) {
        logInfo("GL: texture flush kept all pages (same room)\n");
        return;
    }
    lastFlushRoom = flushRoom;
    GLint previous = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
    static const uint8_t placeholder[4] = {0, 0, 0, 0};
    // Pages the game keeps coming back to (the player, HUD and shared effect sheets) were re-decoded after every
    // room change, ~0.3 s each. Keep up to HLD_KEEP_PAGES of the most often reloaded ones while memory is plentiful.
    enum { HLD_KEEP_PAGES = 4, HLD_KEEP_AFTER = 2 };
    static uint16_t unloadCounts[4096];
    int32_t keep[HLD_KEEP_PAGES];
    unsigned kept = 0;
    for (int k = 0; k < HLD_KEEP_PAGES; ++k) keep[k] = -1;
    bool roomy = true;
#if PLATFORM_VITA
    roomy = vglMemFree(VGL_MEM_VRAM) + vglMemFree(VGL_MEM_RAM) >= (size_t) 96 << 20;
#endif
    if (roomy && getenv("HLD_NO_KEEP_PAGES") == NULL) {
        for (int k = 0; k < HLD_KEEP_PAGES; ++k) {
            int32_t best = -1;
            for (uint32_t i = 0; i < gl->originalTexturePageCount && i < gl->textureCount && i < 4096; ++i) {
                if (!gl->textureLoaded[i] || gl->glTextures[i] == 0 || unloadCounts[i] < HLD_KEEP_AFTER) continue;
                bool taken = false;
                for (int j = 0; j < k; ++j) taken |= keep[j] == (int32_t) i;
                if (!taken && (best < 0 || unloadCounts[i] > unloadCounts[best])) best = (int32_t) i;
            }
            keep[k] = best;
        }
    }
    // Keep, beyond the above: pages the next room has drawn before (page-learn), and then the most recently used
    // others, as long as the memory left still covers the next room's other learned pages (or 96 MB for a room
    // never seen) plus a 40 MB margin. Only the oldest pages are unloaded to make room. HLD_FLUSH_ALL: old behaviour.
    static uint8_t keepLru[4096];
    memset(keepLru, 0, sizeof keepLru);
    size_t lruKept = 0;
#if PLATFORM_VITA
    if (getenv("HLD_FLUSH_ALL") == NULL && flushRoom >= 0 && hldPageKnown != NULL && (uint32_t) flushRoom < hldPageRooms) {
        size_t need = 0;
        unsigned learned = 0;
        for (uint32_t i = 0; i < hldPageCount && i < 4096; ++i) {
            if (!hldPageKnown[(size_t) flushRoom * hldPageCount + i]) continue;
            ++learned;
            if (i < gl->textureCount && gl->textureLoaded[i]) keepLru[i] = 1;
            else need += hldPageBytesSeen[i] ? hldPageBytesSeen[i] : (size_t) 16 << 20;
        }
        if (learned == 0) need = (size_t) 96 << 20;
        const size_t margin = (size_t) 40 << 20;
        size_t free = vglMemFree(VGL_MEM_VRAM) + vglMemFree(VGL_MEM_RAM);
        // Unload the least recently used of the remaining pages until free >= need + margin; keep the rest.
        for (;;) {
            int32_t oldest = -1;
            for (uint32_t i = 0; i < gl->originalTexturePageCount && i < gl->textureCount && i < 4096; ++i) {
                if (!gl->textureLoaded[i] || gl->glTextures[i] == 0 || keepLru[i] != 0) continue;
                bool fixed = false;
                for (int k = 0; k < HLD_KEEP_PAGES; ++k) fixed |= keep[k] == (int32_t) i;
                if (fixed || hldUiPageKeep(i)) continue;
                if (oldest < 0 || hldPageLastUse[i] < hldPageLastUse[oldest]) oldest = (int32_t) i;
            }
            if (oldest < 0) break;
            if (free >= need + margin) {
                // Enough room: everything still unmarked stays.
                for (uint32_t i = 0; i < gl->originalTexturePageCount && i < gl->textureCount && i < 4096; ++i)
                    if (gl->textureLoaded[i] && keepLru[i] == 0) { keepLru[i] = 2; ++lruKept; }
                break;
            }
            keepLru[oldest] = 3;  // unload
            free += hldPageBytesSeen[oldest] ? hldPageBytesSeen[oldest] : (size_t) 16 << 20;
        }
        logInfo("GL: page cache room=%d learned=%u need_mb=%.0f free_mb=%.0f\n", flushRoom, learned, need / 1048576.0,
                (vglMemFree(VGL_MEM_VRAM) + vglMemFree(VGL_MEM_RAM)) / 1048576.0);
    }
#endif
    unsigned unloaded = 0;
    for (uint32_t i = 0; i < gl->originalTexturePageCount && i < gl->textureCount; ++i) {
        if (!gl->textureLoaded[i] || gl->glTextures[i] == 0) continue;
        bool keepPage = false;
        for (int k = 0; k < HLD_KEEP_PAGES; ++k) keepPage |= keep[k] == (int32_t) i;
        if (keepPage) { ++kept; continue; }
        if (hldUiPageKeep(i)) { ++kept; continue; }
        if (i < 4096 && (keepLru[i] == 1 || keepLru[i] == 2)) { ++kept; continue; }
        if (i < 4096 && unloadCounts[i] < UINT16_MAX) ++unloadCounts[i];
        glBindTexture(GL_TEXTURE_2D, gl->glTextures[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, placeholder);
        gl->textureLoaded[i] = false;
        gl->textureWidths[i] = 0;
        gl->textureHeights[i] = 0;
        ++unloaded;
    }
    glBindTexture(GL_TEXTURE_2D, (GLuint) previous);
    logInfo("GL: texture flush unloaded %u pages kept %u (recent %u)\n", unloaded, kept, (unsigned) lruKept);
    if (getenv("HLD_NO_PAGE_PREFETCH") == NULL) hldPagePrefetchRoom(gl);
    hldUiPagesPreload(gl);
#if PLATFORM_VITA
    { extern void hldBootPagePrefetchDrop(void) __attribute__((weak)); if (hldBootPagePrefetchDrop) hldBootPagePrefetchDrop(); }
#endif
}
"""

# Menu pages (map, dialogue portraits, shops, pause screen) were decoded the first time each was drawn, 0.3-1.7 s
# each, and again after every room change because the flush unloads every page. A page first loaded while the GUI
# layer is drawing is a UI page: it is remembered in ui-pages.txt, kept across flushes (up to HLD_UI_PAGE_BYTES while
# memory is plentiful) and, from the next session on, loaded during the first level load.
UI_PAGES = r"""

// Vita button prompts: the handheld's shoulder buttons are the game's L2/R2 and D-pad left/right are its L1/R1, so the
// prompt icons for those (PlayStation and Xbox sets) are pointed, once, at vita-icons/*.png (made from the game's own
// icon art: the PS d-pad rotated, the L1/R1 badge with only the letter).
void hldVitaButtonIcons(Renderer* renderer) {
    static bool done;
    if (done || renderer == NULL || renderer->vtable->createSpriteFromPixels == NULL || getenv("HLD_NO_VITA_ICONS") != NULL) return;
    done = true;
    DataWin* dw = renderer->dataWin;
    static const char* const map[][2] = {
        {"spr_MEN_Bttn_PS_L1", "dleft"}, {"spr_MEN_Bttn_PS_R1", "dright"}, {"spr_MEN_Bttn_PS_L2", "L"}, {"spr_MEN_Bttn_PS_R2", "R"},
        {"spr_MEN_Bttn_XB_LB", "dleft"}, {"spr_MEN_Bttn_XB_RB", "dright"}, {"spr_MEN_Bttn_XB_LT", "L"}, {"spr_MEN_Bttn_XB_RT", "R"},
    };
    unsigned replaced = 0;
    for (size_t m = 0; m < sizeof(map) / sizeof(*map); ++m) {
        int32_t target = -1;
        for (uint32_t i = 0; i < dw->sprt.count && target < 0; ++i)
            if (dw->sprt.sprites[i].name != NULL && strcmp(dw->sprt.sprites[i].name, map[m][0]) == 0) target = (int32_t) i;
        if (target < 0 || dw->sprt.sprites[target].textureCount < 1 || dw->sprt.sprites[target].tpagIndices == NULL) continue;
        char path[512];
        const char* dir = getenv("HLD_VITA_ICONS_DIR") != NULL ? getenv("HLD_VITA_ICONS_DIR") : "app0:vita-icons";
        snprintf(path, sizeof path, "%s/%s.png", dir, map[m][1]);
        FILE* file = fopen(path, "rb");
        if (file == NULL) continue;
        fseek(file, 0, SEEK_END);
        const long size = ftell(file);
        fseek(file, 0, SEEK_SET);
        uint8_t* bytes = size > 0 && size < (1 << 20) ? (uint8_t*) malloc((size_t) size) : NULL;
        const bool read = bytes != NULL && fread(bytes, 1, (size_t) size, file) == (size_t) size;
        fclose(file);
        int w = 0, h = 0;
        uint8_t* pixels = read ? ImageDecoder_decodeToRgba(bytes, (size_t) size, false, &w, &h) : NULL;
        free(bytes);
        if (pixels == NULL) continue;
        const int32_t made = renderer->vtable->createSpriteFromPixels(renderer, pixels, w, h, false, 0, 0);
        free(pixels);
        if (made < 0) continue;
        Sprite* source = &dw->sprt.sprites[made];
        Sprite* sprite = &dw->sprt.sprites[target];
        sprite->tpagIndices[0] = source->tpagIndices[0];
        sprite->width = source->width;
        sprite->height = source->height;
        sprite->marginLeft = source->marginLeft;
        sprite->marginRight = source->marginRight;
        sprite->marginTop = source->marginTop;
        sprite->marginBottom = source->marginBottom;
        ++replaced;
    }
    logInfo("GL: Vita button icons replaced=%u\n", replaced);
}
#if !PLATFORM_VITA
int hldExternalPage = -1;  // defined by the Vita textures.bin loader
uint32_t hldExternalBytes;
#endif
int hldGuiDrawing;
uint32_t hldPageLastUse[4096];
size_t hldPageBytesSeen[4096];
enum { HLD_UI_PAGE_LIMIT = 4096 };
static const size_t HLD_UI_PAGE_BYTES = (size_t) 48 << 20;
static uint8_t hldUiPage[HLD_UI_PAGE_LIMIT];
static size_t hldUiBytes;
static bool hldUiPagesOff(void) { static int off = -1; if (off < 0) off = getenv("HLD_NO_UI_PAGES") != NULL; return off; }
static bool hldUiMemoryRoomy(void) {
#if PLATFORM_VITA
    return vglMemFree(VGL_MEM_VRAM) + vglMemFree(VGL_MEM_RAM) >= (size_t) 48 << 20;
#else
    return true;
#endif
}
static bool hldUiPageMark(GLRenderer* gl, uint32_t pageId) {
    if (pageId >= HLD_UI_PAGE_LIMIT || hldUiPage[pageId]) return false;
    extern int hldExternalPage __attribute__((weak)); extern uint32_t hldExternalBytes __attribute__((weak));
    const size_t bytes = &hldExternalPage != NULL && hldExternalPage == (int) pageId ? (size_t) hldExternalBytes
                       : (size_t) gl->textureWidths[pageId] * (size_t) gl->textureHeights[pageId] * 4;
    if (bytes == 0 || hldUiBytes + bytes > HLD_UI_PAGE_BYTES) return false;
    hldUiPage[pageId] = 1;
    hldUiBytes += bytes;
    return true;
}
void hldUiPageNote(GLRenderer* gl, uint32_t pageId, bool loaded) {
    if (loaded && pageId < 4096) {
        extern int hldExternalPage __attribute__((weak)); extern uint32_t hldExternalBytes __attribute__((weak));
        hldPageBytesSeen[pageId] = &hldExternalPage != NULL && hldExternalPage == (int) pageId ? (size_t) hldExternalBytes
                                 : (size_t) gl->textureWidths[pageId] * (size_t) gl->textureHeights[pageId] * 4;
    }
    if (!loaded || !hldGuiDrawing || hldUiPagesOff() || pageId >= gl->originalTexturePageCount) return;
    if (!hldUiPageMark(gl, pageId)) return;
    FILE* out = fopen("ux0:data/hld-vm/ui-pages.txt", "a");
    if (out != NULL) { fprintf(out, "%u\n", pageId); fclose(out); }
    logInfo("GL: UI page %u (%dx%d) kept from now on, ui_mb=%.1f\n", pageId, gl->textureWidths[pageId], gl->textureHeights[pageId], hldUiBytes / 1048576.0);
}
static bool hldUiPageKeep(uint32_t pageId) {
    return pageId < HLD_UI_PAGE_LIMIT && hldUiPage[pageId] && !hldUiPagesOff() && hldUiMemoryRoomy();
}
// NPC dialogue bubbles (spr_BBL_*) are drawn as world effects, not in the GUI layer, so they are listed here.
static void hldUiBubblePages(GLRenderer* gl) {
    DataWin* dw = gl->base.dataWin;
    unsigned loaded = 0;
    for (uint32_t i = 0; i < dw->sprt.count; ++i) {
        const Sprite* sprite = &dw->sprt.sprites[i];
        if (sprite->name == NULL || strncmp(sprite->name, "spr_BBL", 7) != 0 || sprite->tpagIndices == NULL) continue;
        for (uint32_t f = 0; f < sprite->textureCount; ++f) {
            const int32_t tpag = sprite->tpagIndices[f];
            if (tpag < 0 || (uint32_t) tpag >= dw->tpag.count) continue;
            const int32_t page = dw->tpag.items[tpag].texturePageId;
            if (page < 0 || (uint32_t) page >= gl->originalTexturePageCount || page >= HLD_UI_PAGE_LIMIT || hldUiPage[page] || !hldUiMemoryRoomy()) continue;
            if (GLRenderer_ensureTextureLoaded(gl, (uint32_t) page) && hldUiPageMark(gl, (uint32_t) page)) ++loaded;
        }
    }
    logInfo("GL: dialogue bubble pages kept=%u ui_mb=%.1f\n", loaded, hldUiBytes / 1048576.0);
}
static void hldUiPagesPreload(GLRenderer* gl) {
    static bool done;
    if (done || hldUiPagesOff()) return;
    done = true;
    hldUiBubblePages(gl);
    FILE* in = fopen("ux0:data/hld-vm/ui-pages.txt", "r");
    if (in == NULL) return;
    unsigned page, loaded = 0;
    uint64_t nowNanos(void); const uint64_t start = nowNanos();
    while (fscanf(in, "%u", &page) == 1) {
        if (page >= gl->originalTexturePageCount || page >= HLD_UI_PAGE_LIMIT || hldUiPage[page] || !hldUiMemoryRoomy()) continue;
        if (!GLRenderer_ensureTextureLoaded(gl, page)) continue;
        if (hldUiPageMark(gl, page)) ++loaded;
    }
    fclose(in);
    logInfo("GL: preloaded %u UI pages ms=%.1f ui_mb=%.1f\n", loaded, (nowNanos() - start) * 1e-6, hldUiBytes / 1048576.0);
}
"""


def with_texture_flush_builtins(code):
    start = code.index("static RValue builtin_draw_texture_flush(")
    end = code.index("\n}\n", start)
    body = replace_once(code[start:end], TEXTURE_FLUSH_CALL,
                        "    if (ctx->runner->renderer) { extern void hldUnloadTexturePages(Renderer*); "
                        "hldUnloadTexturePages(ctx->runner->renderer); }")
    return code[:start] + body + code[end:]


PAGE_PREFETCH = r"""
// ===[ Room page preload ]===
// A page decoded on first draw stalls that frame ~0.3-0.6 s (2048x2048 PNG), often in the middle of a fight when an
// enemy type first appears. Pages loaded in a room are remembered per room (ux0:data/hld-vm/page-learn.txt, first-use
// order); when the room is entered again its pages are loaded during the room change through the normal path, so the
// cost lands in the transition instead of gameplay. Background decoding was tried and stalled unpredictably.
#if PLATFORM_VITA
#include <psp2/kernel/threadmgr.h>
uint64_t nowNanos(void);
typedef struct { int32_t room; uint32_t page; } HldPageUse;
static HldPageUse* hldPageUses;
static uint32_t hldPageUseCount, hldPageUseCapacity, hldPageUseSaved;
static uint8_t* hldPageKnown;  // room * pages bitmap of learned uses
static uint32_t hldPageCount, hldPageRooms;

static void hldPageRemember(int32_t room, uint32_t page) {
    const size_t key = (size_t) room * hldPageCount + page;
    if (hldPageKnown[key]) return;
    hldPageKnown[key] = 1;
    if (hldPageUseCount == hldPageUseCapacity) {
        hldPageUseCapacity = hldPageUseCapacity ? hldPageUseCapacity * 2 : 256;
        hldPageUses = (HldPageUse*) realloc(hldPageUses, hldPageUseCapacity * sizeof(HldPageUse));
    }
    hldPageUses[hldPageUseCount++] = (HldPageUse) {room, page};
}

static bool hldPageInit(GLRenderer* gl) {
    if (hldPageKnown != NULL) return true;
    DataWin* dw = gl->base.dataWin;
    if (dw->gen8.gameID != 742741103 || dw->txtr.count == 0) return false;
    hldPageCount = dw->txtr.count < gl->originalTexturePageCount ? dw->txtr.count : gl->originalTexturePageCount;
    hldPageRooms = dw->room.count;
    hldPageKnown = (uint8_t*) calloc((size_t) hldPageRooms * hldPageCount, 1);
    if (hldPageKnown == NULL) return false;
    FILE* learned = fopen("ux0:data/hld-vm/page-learn.txt", "r");
    char line[160];
    while (learned != NULL && fgets(line, sizeof(line), learned) != NULL) {
        char name[128]; unsigned page;
        if (sscanf(line, "%127s %u", name, &page) != 2 || page >= hldPageCount) continue;
        for (uint32_t r = 0; r < hldPageRooms; ++r)
            if (strcmp(dw->room.rooms[r].name, name) == 0) { hldPageRemember((int32_t) r, page); break; }
    }
    if (learned != NULL) fclose(learned);
    hldPageUseSaved = hldPageUseCount;
    logInfo("GL: page preload learned %u room pages\\n", hldPageUseCount);
    return true;
}

// ---- Background decode (v3) ----
// At a room change the room's learned pages get their texture storage allocated here and are queued; a worker on
// another core reads each PNG with its own file handle and decodes it straight into that storage. The page is only
// handed to the renderer when it is first drawn: done -> used as is; still queued -> taken back and loaded normally on
// this thread; being decoded -> waited for (no longer than loading it here). Pages decoded but not drawn by the next
// room change are released there.
bool ImageDecoder_readRgba8Png(const uint8_t* blob, size_t size, uint8_t* output, size_t stride);
enum { HLD_PG_IDLE, HLD_PG_QUEUED, HLD_PG_RUNNING, HLD_PG_DONE, HLD_PG_FAILED, HLD_PG_TAKEN, HLD_PG_ABANDONED, HLD_PG_ORPHAN };
// ABANDONED: the renderer gave up waiting and moved the page to a fresh texture; the worker still owns the old one
// (orphan) and its storage until it finishes, after which the main thread deletes it.
typedef struct { int state; uint8_t* dst; size_t stride; int w, h; uint32_t offset, size; GLuint texture; } HldPageSlot;
static HldPageSlot* hldPageSlots;
static uint32_t* hldPageQueue;
static uint32_t hldPageQueueHead, hldPageQueueTail;  // tail written by the main thread, head by the worker
static SceUID hldPageSema = -1, hldPageThread = -1;
static FILE* hldPageHeaderFile;
static uint8_t* hldPageBundled;
static unsigned hldPageQueued, hldPagePrefetched, hldPageTaken, hldPageWaited;

static int hldPageWorker(SceSize argSize, void* argp) {
    (void) argSize; (void) argp;
    for (;;) {
        sceKernelWaitSema(hldPageSema, 1, NULL);
        const uint32_t index = hldPageQueueHead;
        if (index == __atomic_load_n(&hldPageQueueTail, __ATOMIC_ACQUIRE)) continue;
        const uint32_t page = hldPageQueue[index % 256];
        __atomic_store_n(&hldPageQueueHead, index + 1, __ATOMIC_RELEASE);
        HldPageSlot* slot = &hldPageSlots[page];
        int expected = HLD_PG_QUEUED;
        if (!__atomic_compare_exchange_n(&slot->state, &expected, HLD_PG_RUNNING, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) continue;
        const uint64_t start = nowNanos();
        bool hldStripDecodeInto(uint32_t page, uint8_t* dst, size_t stride, int w, int h);
        bool ok = hldStripDecodeInto(page, slot->dst, slot->stride, slot->w, slot->h);
        // Opened per page and closed straight away: no file handle is held while idle.
        FILE* archive = ok ? NULL : fopen("ux0:data/hld-vm/data.win", "rb");
        uint8_t* blob = archive != NULL ? (uint8_t*) malloc(slot->size) : NULL;
        if (blob != NULL && fseek(archive, (long) slot->offset, SEEK_SET) == 0 && fread(blob, 1, slot->size, archive) == slot->size) {
            fclose(archive);
            archive = NULL;
            ok = ImageDecoder_readRgba8Png(blob, slot->size, slot->dst, slot->stride);
        }
        if (archive != NULL) fclose(archive);
        free(blob);
        int running = HLD_PG_RUNNING;
        if (!__atomic_compare_exchange_n(&slot->state, &running, ok ? HLD_PG_DONE : HLD_PG_FAILED, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
            __atomic_store_n(&slot->state, HLD_PG_ORPHAN, __ATOMIC_RELEASE);
        logInfo("GL: background page %u ok=%d ms=%.1f\\n", page, (int) ok, (nowNanos() - start) * 1e-6);
    }
    return 0;
}

static bool hldPageReadSize(DataWin* dw, uint32_t page, int* w, int* h) {
    const Texture* tex = &dw->txtr.textures[page];
    if (tex->blobOffset == 0 || tex->blobSize < 33 || tex->externalPath != NULL) return false;
    if (hldPageHeaderFile == NULL) hldPageHeaderFile = fopen("ux0:data/hld-vm/data.win", "rb");
    uint8_t header[33];
    if (hldPageHeaderFile == NULL || fseek(hldPageHeaderFile, (long) tex->blobOffset, SEEK_SET) != 0 ||
        fread(header, 1, sizeof(header), hldPageHeaderFile) != sizeof(header)) return false;
    static const uint8_t signature[16] = {137, 'P', 'N', 'G', 13, 10, 26, 10, 0, 0, 0, 13, 'I', 'H', 'D', 'R'};
    if (memcmp(header, signature, 16) != 0 || header[24] != 8 || header[25] != 6 || header[28] != 0) return false;
    *w = (int) ((uint32_t) header[16] << 24 | (uint32_t) header[17] << 16 | (uint32_t) header[18] << 8 | header[19]);
    *h = (int) ((uint32_t) header[20] << 24 | (uint32_t) header[21] << 16 | (uint32_t) header[22] << 8 | header[23]);
    return *w > 0 && *h > 0 && *w <= 4096 && *h <= 4096;
}

static void hldPageBundleScan(void) {
    hldPageBundled = (uint8_t*) calloc(hldPageCount + 1, 1);
    FILE* bundle = fopen("ux0:data/hld-vm/textures.bin", "rb");
    int32_t records = 0;
    if (bundle != NULL && hldPageBundled != NULL && fread(&records, 4, 1, bundle) == 1) {
        for (int32_t i = 0; i < records; ++i) {
            int32_t bytes = 0;
            if (fread(&bytes, 4, 1, bundle) != 1 || bytes < 0) break;
            if (bytes > 0 && (uint32_t) i < hldPageCount) hldPageBundled[i] = 1;
            if (bytes > 0 && fseek(bundle, bytes, SEEK_CUR) != 0) break;
        }
    }
    if (bundle != NULL) fclose(bundle);
}

// The worker keeps writing into the page's texture: give the page a fresh texture name for the normal load path.
static bool hldPageAbandon(GLRenderer* gl, uint32_t page) {
    HldPageSlot* slot = &hldPageSlots[page];
    int running = HLD_PG_RUNNING;
    if (!__atomic_compare_exchange_n(&slot->state, &running, HLD_PG_ABANDONED, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return false;
    GLuint fresh = 0;
    glGenTextures(1, &fresh);
    gl->glTextures[page] = fresh;
    logInfo("GL: background page %u abandoned to the renderer\\n", page);
    return true;
}

// Orphaned textures whose worker has finished are deleted here (main thread).
static void hldPageReapOrphans(void) {
    for (uint32_t page = 0; page < hldPageCount; ++page) {
        HldPageSlot* slot = &hldPageSlots[page];
        if (__atomic_load_n(&slot->state, __ATOMIC_ACQUIRE) != HLD_PG_ORPHAN) continue;
        glDeleteTextures(1, &slot->texture);
        slot->state = HLD_PG_IDLE;
    }
}

// Called where ensureTextureLoaded is about to load a page. Returns true when a background-decoded page was installed.
bool hldPagePrefetchFinish(GLRenderer* gl, uint32_t pageId) {
    Runner* runner = gl->base.runner;
    HLD_EARLY_PAGE_INIT
    if (hldPageKnown == NULL || runner == NULL || pageId >= hldPageCount) return false;
    if (runner->currentRoomIndex >= 0 && (uint32_t) runner->currentRoomIndex < hldPageRooms)
        hldPageRemember(runner->currentRoomIndex, pageId);
    if (hldPageSlots == NULL) return false;
    HldPageSlot* slot = &hldPageSlots[pageId];
    int expected = HLD_PG_QUEUED;
    if (__atomic_compare_exchange_n(&slot->state, &expected, HLD_PG_TAKEN, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        ++hldPageTaken;
        return false;  // not started: the normal path reallocates and loads it here
    }
    if (expected == HLD_PG_RUNNING) {
        // Wait briefly; a starved worker must never hold up the game, so after 40 ms the page moves to a fresh
        // texture and loads here.
        const uint64_t start = nowNanos();
        while (__atomic_load_n(&slot->state, __ATOMIC_ACQUIRE) == HLD_PG_RUNNING && nowNanos() - start < 40000000ull)
            sceKernelDelayThread(500);
        ++hldPageWaited;
        logInfo("GL: waited %.1f ms for background page %u\\n", (nowNanos() - start) * 1e-6, pageId);
        if (hldPageAbandon(gl, pageId)) return false;
        expected = __atomic_load_n(&slot->state, __ATOMIC_ACQUIRE);
    }
    if (expected == HLD_PG_ABANDONED || expected == HLD_PG_ORPHAN) return false;
    if (expected != HLD_PG_DONE) {
        if (expected == HLD_PG_FAILED) slot->state = HLD_PG_IDLE;
        return false;
    }
    slot->state = HLD_PG_IDLE;
    glBindTexture(GL_TEXTURE_2D, gl->glTextures[pageId]);
    gl->textureLoaded[pageId] = true;
    gl->textureWidths[pageId] = slot->w;
    gl->textureHeights[pageId] = slot->h;
    const bool pot = (slot->w & (slot->w - 1)) == 0 && (slot->h & (slot->h - 1)) == 0;
    GLCommon_applyTexFilter(gl->base.texFilter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, pot ? GL_REPEAT : GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, pot ? GL_REPEAT : GL_CLAMP_TO_EDGE);
    ++hldPagePrefetched;
    logInfo("GL: Loaded TXTR page %u (%dx%d) in background\\n", pageId, slot->w, slot->h);
    return true;
}

// Before a texture flush: stop queued work, finish the running page, release decoded pages nobody drew.
static void hldPagePrefetchSettle(GLRenderer* gl) {
    if (hldPageSlots == NULL) return;
    static const uint8_t placeholder[4] = {0, 0, 0, 0};
    GLint previous = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
    hldPageReapOrphans();
    unsigned released = 0;
    for (uint32_t page = 0; page < hldPageCount; ++page) {
        HldPageSlot* slot = &hldPageSlots[page];
        int expected = HLD_PG_QUEUED;
        if (!__atomic_compare_exchange_n(&slot->state, &expected, HLD_PG_TAKEN, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE) &&
            expected == HLD_PG_RUNNING && !gl->textureLoaded[page]) {
            hldPageAbandon(gl, page);  // never wait for the worker at a room change
        }
        const int state = __atomic_load_n(&slot->state, __ATOMIC_ACQUIRE);
        if (state == HLD_PG_TAKEN || state == HLD_PG_DONE || state == HLD_PG_FAILED) {
            if (!gl->textureLoaded[page]) {
                glBindTexture(GL_TEXTURE_2D, gl->glTextures[page]);
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 1, 1, 0, GL_RGBA, GL_UNSIGNED_BYTE, placeholder);
                ++released;
            }
            slot->state = HLD_PG_IDLE;
        }
    }
    glBindTexture(GL_TEXTURE_2D, (GLuint) previous);
    if (hldPageQueued || released)
        logInfo("GL: background pages queued=%u used=%u taken_back=%u waited=%u released=%u\\n",
                hldPageQueued, hldPagePrefetched, hldPageTaken, hldPageWaited, released);
    hldPageQueued = hldPagePrefetched = hldPageTaken = hldPageWaited = 0;
}

static void hldPagePrefetchRoom(GLRenderer* gl) {
    if (!hldPageInit(gl)) return;
    Runner* runner = gl->base.runner;
    if (hldPageUseCount > hldPageUseSaved) {
        FILE* out = fopen("ux0:data/hld-vm/page-learn.txt", "a");
        for (; out != NULL && hldPageUseSaved < hldPageUseCount; ++hldPageUseSaved)
            fprintf(out, "%s %u\\n", gl->base.dataWin->room.rooms[hldPageUses[hldPageUseSaved].room].name, hldPageUses[hldPageUseSaved].page);
        if (out != NULL) fclose(out);
    }
    if (runner == NULL || runner->currentRoomIndex < 0 || getenv("HLD_NO_PAGE_PREFETCH") != NULL) return;
#ifdef HLD_NO_BACKGROUND_PAGES
    return;
#endif
    if (hldPageSlots == NULL) {
        hldPageSlots = (HldPageSlot*) calloc(hldPageCount, sizeof(HldPageSlot));
        hldPageQueue = (uint32_t*) calloc(256, sizeof(uint32_t));
        hldPageBundleScan();
        hldPageSema = sceKernelCreateSema("HldPageQueue", 0, 0, 256, NULL);
        hldPageThread = sceKernelCreateThread("HldPageDecode", hldPageWorker, 191, 0x10000, 0,
                                              SCE_KERNEL_CPU_MASK_USER_1 | SCE_KERNEL_CPU_MASK_USER_2, NULL);
        SceKernelThreadInfo mainInfo = {.size = sizeof(mainInfo)};
        sceKernelGetThreadInfo(sceKernelGetThreadId(), &mainInfo);
        logInfo("GL: background page decoder thread=%d main_priority=%d main_affinity=0x%x\n", (int) hldPageThread,
                (int) mainInfo.currentPriority, (unsigned) mainInfo.currentCpuAffinityMask);
        if (hldPageSlots == NULL || hldPageQueue == NULL || hldPageSema < 0 || hldPageThread < 0 ||
            sceKernelStartThread(hldPageThread, 0, NULL) < 0) {
            free(hldPageSlots); hldPageSlots = NULL;
            return;
        }
    }
    DataWin* dw = gl->base.dataWin;
    GLint previous = 0;
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous);
    for (uint32_t u = 0; u < hldPageUseCount; ++u) {
        if (hldPageUses[u].room != runner->currentRoomIndex) continue;
        const uint32_t page = hldPageUses[u].page;
        HldPageSlot* slot = &hldPageSlots[page];
        if (gl->textureLoaded[page] || slot->state != HLD_PG_IDLE) continue;
        if (VitaTextures_Active() && hldPageBundled != NULL && hldPageBundled[page]) continue;
        if (hldPageQueueTail - __atomic_load_n(&hldPageQueueHead, __ATOMIC_ACQUIRE) >= 255) break;
        if (vglMemFree(VGL_MEM_VRAM) + vglMemFree(VGL_MEM_RAM) < (size_t) 80 << 20) break;
        int w, h;
        if (!hldPageReadSize(dw, page, &w, &h)) continue;
        glBindTexture(GL_TEXTURE_2D, gl->glTextures[page]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
        uint8_t* dst = (uint8_t*) vglGetTexDataPointer(GL_TEXTURE_2D);
        if (dst == NULL) continue;
        slot->dst = dst;
        slot->texture = gl->glTextures[page];
        slot->stride = (size_t) ((w + 7) & ~7) * 4;
        slot->w = w; slot->h = h;
        slot->offset = dw->txtr.textures[page].blobOffset;
        slot->size = dw->txtr.textures[page].blobSize;
        __atomic_store_n(&slot->state, HLD_PG_QUEUED, __ATOMIC_RELEASE);
        hldPageQueue[hldPageQueueTail % 256] = page;
        __atomic_store_n(&hldPageQueueTail, hldPageQueueTail + 1, __ATOMIC_RELEASE);
        sceKernelSignalSema(hldPageSema, 1);
        ++hldPageQueued;
    }
    glBindTexture(GL_TEXTURE_2D, (GLuint) previous);
    if (hldPageHeaderFile != NULL) { fclose(hldPageHeaderFile); hldPageHeaderFile = NULL; }
    if (hldPageQueued) logInfo("GL: background pages room=%s queued=%u\\n", runner->currentRoom->name, hldPageQueued);
}
#else
bool hldPagePrefetchFinish(GLRenderer* gl, uint32_t pageId) { (void) gl; (void) pageId; return false; }
static void hldPagePrefetchSettle(GLRenderer* gl) { (void) gl; }
static void hldPagePrefetchRoom(GLRenderer* gl) { (void) gl; }
#endif
"""


# Page strips (make-page-strips.py, page_strips.h): a page with ux0:data/hld-vm/pagez/pNNN.hlz is read strip by strip
# and each strip is LZ4-decoded straight into the texture by whichever of the main thread and two helper threads takes
# it first, overlapping the card reads. The bytes equal the PNG decoder's (verify-page-strips). Any failure falls back
# to the PNG path; HLD_NO_PAGE_STRIPS disables it.
PAGE_STRIPS_TEMPLATE = r"""
#if PLATFORM_VITA
/*HLZ_HEADER*/
#include <psp2/kernel/threadmgr.h>
#include <psp2/io/fcntl.h>
uint64_t nowNanos(void);
// Whole reads through sceIoRead: newlib's fread passes large requests to the card in small pieces.
static bool hldStripRead(SceUID fd, void* buffer, size_t bytes) {
    uint8_t* at = (uint8_t*) buffer;
    while (bytes > 0) {
        const int got = sceIoRead(fd, at, bytes > (size_t) 4 << 20 ? (size_t) 4 << 20 : bytes);
        if (got <= 0) return false;
        at += got;
        bytes -= (size_t) got;
    }
    return true;
}
static struct {
    uint8_t* dst;
    uint32_t rowBytes, strips;
    HlzStrip strip[HLZ_MAX_STRIPS];
    const uint8_t* src[HLZ_MAX_STRIPS];
    volatile int ready, next, done, failed;
    uint8_t* scratch[3];
    size_t scratchBytes;
    bool filtered;
    size_t dstStride;          // texture row pitch (vitaGL pads rows to 8 pixels)
    uint8_t* mask;             // optional collision mask (1 bit per pixel, alpha != 0), maskStride bytes a row
    size_t maskStride;
    int32_t bounds[HLZ_MAX_STRIPS][4];  // per strip: left, top, right, bottom of the non-transparent pixels
} hldStripJob;
static SceUID hldStripReady = -1, hldStripFinished = -1;
static int hldStripHelpers;

static void hldStripWork(int slot, bool helper) {
    for (;;) {
        const int index = __atomic_load_n(&hldStripJob.next, __ATOMIC_ACQUIRE);
        if (index >= (int) hldStripJob.strips) return;
        if (index >= __atomic_load_n(&hldStripJob.ready, __ATOMIC_ACQUIRE)) {
            if (!helper) return;  // the main thread goes back to reading
            sceKernelWaitSema(hldStripReady, 1, NULL);
            continue;
        }
        int expected = index;
        if (!__atomic_compare_exchange_n(&hldStripJob.next, &expected, index + 1, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) continue;
        const HlzStrip* strip = &hldStripJob.strip[index];
        // Decode into this thread's cached buffer (LZ4 reads back what it wrote), then copy the rows into the texture.
        const size_t bytes = (size_t) strip->rowCount * hldStripJob.rowBytes;
        if (bytes <= hldStripJob.scratchBytes && hlzDecompress(hldStripJob.src[index], strip->compressedBytes, hldStripJob.scratch[slot], bytes)) {
            if (hldStripJob.filtered || (strip->firstRow & HLZ_FILTERED)) hlzUnfilter(hldStripJob.scratch[slot], strip->rowCount, hldStripJob.rowBytes);
            const uint32_t first = strip->firstRow & ~HLZ_FILTERED;
            const uint8_t* rows = hldStripJob.scratch[slot];
            if (hldStripJob.dstStride == hldStripJob.rowBytes) memcpy(hldStripJob.dst + (size_t) first * hldStripJob.rowBytes, rows, bytes);
            else for (uint32_t r = 0; r < strip->rowCount; ++r)
                memcpy(hldStripJob.dst + (size_t) (first + r) * hldStripJob.dstStride, rows + (size_t) r * hldStripJob.rowBytes, hldStripJob.rowBytes);
            if (hldStripJob.mask != NULL) {
                // glCreateSpriteFromPixels' mask and bounds, for this strip's rows.
                const int32_t w = (int32_t) (hldStripJob.rowBytes / 4);
                int32_t left = w, top = -1, right = -1, bottom = -1;
                for (uint32_t r = 0; r < strip->rowCount; ++r) {
                    uint8_t* rowMask = hldStripJob.mask + (size_t) (first + r) * hldStripJob.maskStride;
                    const uint8_t* rgba = rows + (size_t) r * hldStripJob.rowBytes;
                    for (int32_t col = 0; col < w; ++col) {
                        if (rgba[(size_t) col * 4 + 3] == 0) continue;
                        rowMask[col / 8] |= 0x80u >> (col % 8);
                        if (col < left) left = col;
                        if (col > right) right = col;
                        if (top < 0) top = (int32_t) (first + r);
                        bottom = (int32_t) (first + r);
                    }
                }
                hldStripJob.bounds[index][0] = left; hldStripJob.bounds[index][1] = top;
                hldStripJob.bounds[index][2] = right; hldStripJob.bounds[index][3] = bottom;
            }
        } else __atomic_store_n(&hldStripJob.failed, 1, __ATOMIC_RELEASE);
        if (__atomic_add_fetch(&hldStripJob.done, 1, __ATOMIC_ACQ_REL) == (int) hldStripJob.strips) sceKernelSignalSema(hldStripFinished, 1);
    }
}

static int hldStripHelper(SceSize argSize, void* argp) {
    const int slot = argSize == sizeof(int) ? *(const int*) argp : 0;
    for (;;) {
        sceKernelWaitSema(hldStripReady, 1, NULL);
        hldStripWork(slot, true);
    }
    return 0;
}

static bool hldStripInit(void) {
    static int state;  // 0 untried, 1 ready, -1 off
    if (state != 0) return state > 0;
    state = -1;
    if (getenv("HLD_NO_PAGE_STRIPS") != NULL) return false;
    FILE* probe = fopen("ux0:data/hld-vm/pagez/p000.hlz", "rb");
    if (probe == NULL) return false;
    fclose(probe);
    hldStripReady = sceKernelCreateSema("HldStripReady", 0, 0, 1 << 20, NULL);
    hldStripFinished = sceKernelCreateSema("HldStripDone", 0, 0, 1, NULL);
    if (hldStripReady < 0 || hldStripFinished < 0) return false;
    const int masks[2] = {SCE_KERNEL_CPU_MASK_USER_1, SCE_KERNEL_CPU_MASK_USER_2};
    for (int i = 0; i < 2; ++i) {
        SceUID thread = sceKernelCreateThread("HldStripDecode", hldStripHelper, 150, 0x4000, 0, masks[i], NULL);
        int slot = i;
        if (thread >= 0 && sceKernelStartThread(thread, sizeof slot, &slot) >= 0) ++hldStripHelpers;
    }
    state = 1;
    logInfo("GL: page strips on, helpers=%d\n", hldStripHelpers);
    return true;
}

// The background worker's version: one thread, the whole page into the texture storage the main thread allocated.
bool hldStripDecodeInto(uint32_t page, uint8_t* dst, size_t stride, int w, int h) {
    if (getenv("HLD_NO_PAGE_STRIPS") != NULL || dst == NULL || w <= 0 || h <= 0 || stride < (size_t) w * 4) return false;
    char path[64];
    snprintf(path, sizeof path, "ux0:data/hld-vm/pagez/p%03u.hlz", page);
    const SceUID file = sceIoOpen(path, SCE_O_RDONLY, 0);
    if (file < 0) return false;
    uint32_t head[4];
    HlzStrip strips[HLZ_MAX_STRIPS];
    bool ok = hldStripRead(file, head, sizeof head) && (memcmp(head, "HLZ1", 4) == 0 || memcmp(head, "HLZ2", 4) == 0 ||
              memcmp(head, "HLZ3", 4) == 0) && head[1] == (uint32_t) w && head[2] == (uint32_t) h && head[3] > 0 && head[3] <= HLZ_MAX_STRIPS;
    const uint32_t count = ok ? head[3] : 0;
    if (ok) ok = hldStripRead(file, strips, sizeof(HlzStrip) * count);
    size_t largest = 0, packed = 0;
    uint32_t rows = 0;
    for (uint32_t i = 0; ok && i < count; ++i) {
        const bool filtered = memcmp(head, "HLZ2", 4) == 0 || (memcmp(head, "HLZ3", 4) == 0 && (strips[i].rowCount & HLZ_FILTERED));
        strips[i].rowCount &= ~HLZ_FILTERED;
        if (filtered) strips[i].firstRow |= HLZ_FILTERED;
        ok = (strips[i].firstRow & ~HLZ_FILTERED) == rows && strips[i].rowCount > 0;
        rows += strips[i].rowCount;
        if ((size_t) strips[i].rowCount * w * 4 > largest) largest = (size_t) strips[i].rowCount * w * 4;
        if (strips[i].compressedBytes > packed) packed = strips[i].compressedBytes;
    }
    ok = ok && rows == (uint32_t) h;
    uint8_t* scratch = ok ? (uint8_t*) malloc(largest) : NULL;
    uint8_t* input = ok ? (uint8_t*) malloc(packed) : NULL;
    ok = ok && scratch != NULL && input != NULL;
    for (uint32_t i = 0; ok && i < count; ++i) {
        const size_t bytes = (size_t) strips[i].rowCount * w * 4;
        ok = hldStripRead(file, input, strips[i].compressedBytes) && hlzDecompress(input, strips[i].compressedBytes, scratch, bytes);
        if (!ok) break;
        if (strips[i].firstRow & HLZ_FILTERED) hlzUnfilter(scratch, strips[i].rowCount, (size_t) w * 4);
        const uint32_t first = strips[i].firstRow & ~HLZ_FILTERED;
        if (stride == (size_t) w * 4) memcpy(dst + (size_t) first * stride, scratch, bytes);
        else for (uint32_t r = 0; r < strips[i].rowCount; ++r) memcpy(dst + (size_t) (first + r) * stride, scratch + (size_t) r * w * 4, (size_t) w * 4);
    }
    free(scratch);
    free(input);
    sceIoClose(file);
    return ok;
}

bool hldStripLoad(GLRenderer* gl, uint32_t pageId) {
    if (gl->textureFormat != GL_RGBA || !hldStripInit()) return false;
    char path[64];
    snprintf(path, sizeof path, "ux0:data/hld-vm/pagez/p%03u.hlz", pageId);
    const uint64_t start = nowNanos();
    const SceUID file = sceIoOpen(path, SCE_O_RDONLY, 0);
    if (file < 0) return false;
    uint32_t head[4];
    bool ok = hldStripRead(file, head, sizeof head) && (memcmp(head, "HLZ1", 4) == 0 || memcmp(head, "HLZ2", 4) == 0 || memcmp(head, "HLZ3", 4) == 0) && head[3] > 0 && head[3] <= HLZ_MAX_STRIPS &&
              head[1] > 0 && head[2] > 0 && head[1] <= 4096 && head[2] <= 4096 && (head[1] & 7) == 0;
    const uint32_t width = head[1], height = head[2], strips = head[3];
    size_t total = 0;
    if (ok) ok = hldStripRead(file, hldStripJob.strip, sizeof(HlzStrip) * strips);
    uint32_t rows = 0;
    const bool perStrip = memcmp(head, "HLZ3", 4) == 0;
    for (uint32_t i = 0; ok && perStrip && i < strips; ++i)
        if (hldStripJob.strip[i].rowCount & HLZ_FILTERED) { hldStripJob.strip[i].rowCount &= ~HLZ_FILTERED; hldStripJob.strip[i].firstRow |= HLZ_FILTERED; }
    for (uint32_t i = 0; ok && i < strips; ++i) {
        ok = (hldStripJob.strip[i].firstRow & ~HLZ_FILTERED) == rows && hldStripJob.strip[i].rowCount > 0 && hldStripJob.strip[i].compressedBytes > 0;
        rows += hldStripJob.strip[i].rowCount;
        total += hldStripJob.strip[i].compressedBytes;
    }
    ok = ok && rows == height && total <= (size_t) 64 << 20;
    size_t largest = 0;
    for (uint32_t i = 0; ok && i < strips; ++i)
        if ((size_t) hldStripJob.strip[i].rowCount * width * 4 > largest) largest = (size_t) hldStripJob.strip[i].rowCount * width * 4;
    if (ok && largest > hldStripJob.scratchBytes) {
        for (int i = 0; i < 3; ++i) { free(hldStripJob.scratch[i]); hldStripJob.scratch[i] = (uint8_t*) malloc(largest); ok = ok && hldStripJob.scratch[i] != NULL; }
        hldStripJob.scratchBytes = ok ? largest : 0;
    }
    uint8_t* compressed = ok ? (uint8_t*) malloc(total) : NULL;
    uint8_t* pixels = NULL;
    if (compressed != NULL) {
        glBindTexture(GL_TEXTURE_2D, gl->glTextures[pageId]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, (GLsizei) width, (GLsizei) height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
        pixels = (uint8_t*) vglGetTexDataPointer(GL_TEXTURE_2D);
    }
    if (pixels == NULL) { free(compressed); sceIoClose(file); return false; }
    hldStripJob.dst = pixels;
    hldStripJob.rowBytes = width * 4;
    hldStripJob.dstStride = width * 4;
    hldStripJob.mask = NULL;
    hldStripJob.filtered = memcmp(head, "HLZ2", 4) == 0;
    hldStripJob.strips = strips;
    hldStripJob.ready = hldStripJob.next = hldStripJob.done = hldStripJob.failed = 0;
    size_t at = 0;
    bool read = true;
    for (uint32_t i = 0; i < strips; ++i) {
        hldStripJob.src[i] = compressed + at;
        if (read) read = hldStripRead(file, compressed + at, hldStripJob.strip[i].compressedBytes);
        if (!read) memset(compressed + at, 0, hldStripJob.strip[i].compressedBytes);  // decodes as a failure
        at += hldStripJob.strip[i].compressedBytes;
        __atomic_store_n(&hldStripJob.ready, (int) i + 1, __ATOMIC_RELEASE);
        if (hldStripHelpers > 0) sceKernelSignalSema(hldStripReady, hldStripHelpers);
        else hldStripWork(2, false);
    }
    sceIoClose(file);
    const uint64_t readDone = nowNanos();
    hldStripWork(2, false);
    sceKernelWaitSema(hldStripFinished, 1, NULL);
    free(compressed);
    if (!read || hldStripJob.failed) {
        logWarn("GL: page strips failed for page %u, using the PNG\n", pageId);
        return false;
    }
    gl->textureWidths[pageId] = (int32_t) width;
    gl->textureHeights[pageId] = (int32_t) height;
    const bool pot = (width & (width - 1)) == 0 && (height & (height - 1)) == 0;
    GLCommon_applyTexFilter(gl->base.texFilter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, pot ? GL_REPEAT : GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, pot ? GL_REPEAT : GL_CLAMP_TO_EDGE);
    logInfo("GL: Loaded TXTR page %u (%ux%u) strips ms=%.1f read_ms=%.1f frame=%d\n", pageId, width, height, (nowNanos() - start) * 1e-6,
            (readDone - start) * 1e-6, gl->base.runner != NULL ? gl->base.runner->frameCount : -1);
    return true;
}

// sprite_add of a room layer PNG with a <file>.hlz beside it: glCreateSpriteFromPixels' work, but the strips decode on
// three cores straight into the new texture and each builds its rows of the collision mask. -2: not handled here.
int32_t hldStripSpriteCreate(Renderer* renderer, const char* path, bool smooth, int32_t xorig, int32_t yorig) {
    GLRenderer* gl = (GLRenderer*) renderer;
    DataWin* dw = renderer->dataWin;
    if (gl->textureFormat != GL_RGBA || !hldStripInit()) return -2;
    const uint64_t start = nowNanos();
    const SceUID file = sceIoOpen(path, SCE_O_RDONLY, 0);
    if (file < 0) return -2;
    uint32_t head[4];
    bool ok = hldStripRead(file, head, sizeof head) && memcmp(head, "HLZ3", 4) == 0 && head[3] > 0 && head[3] <= HLZ_MAX_STRIPS &&
              head[1] > 0 && head[2] > 0 && head[1] <= UINT16_MAX && head[2] <= UINT16_MAX;
    GLint maxTextureSize = 0;
    glGetIntegerv(GL_MAX_TEXTURE_SIZE, &maxTextureSize);
    ok = ok && (GLint) head[1] <= maxTextureSize && (GLint) head[2] <= maxTextureSize;
    const uint32_t width = head[1], height = head[2], strips = head[3];
    if (ok) ok = hldStripRead(file, hldStripJob.strip, sizeof(HlzStrip) * strips);
    uint32_t rows = 0;
    size_t total = 0, largest = 0;
    for (uint32_t i = 0; ok && i < strips; ++i) {
        if (hldStripJob.strip[i].rowCount & HLZ_FILTERED) { hldStripJob.strip[i].rowCount &= ~HLZ_FILTERED; hldStripJob.strip[i].firstRow |= HLZ_FILTERED; }
        ok = (hldStripJob.strip[i].firstRow & ~HLZ_FILTERED) == rows && hldStripJob.strip[i].rowCount > 0 && hldStripJob.strip[i].compressedBytes > 0;
        rows += hldStripJob.strip[i].rowCount;
        total += hldStripJob.strip[i].compressedBytes;
        if ((size_t) hldStripJob.strip[i].rowCount * width * 4 > largest) largest = (size_t) hldStripJob.strip[i].rowCount * width * 4;
    }
    ok = ok && rows == height && total <= (size_t) 64 << 20;
    if (ok && largest > hldStripJob.scratchBytes) {
        for (int i = 0; i < 3; ++i) { free(hldStripJob.scratch[i]); hldStripJob.scratch[i] = (uint8_t*) malloc(largest); ok = ok && hldStripJob.scratch[i] != NULL; }
        hldStripJob.scratchBytes = ok ? largest : 0;
    }
    uint8_t* compressed = ok ? (uint8_t*) malloc(total) : NULL;
    const size_t maskStride = ((size_t) width + 7) / 8;
    uint8_t* mask = compressed != NULL ? (uint8_t*) calloc(maskStride, height) : NULL;
    if (mask == NULL) { free(compressed); sceIoClose(file); return -2; }
    flushBatch(gl);
    GLuint newTexId;
    glGenTextures(1, &newTexId);
    glBindTexture(GL_TEXTURE_2D, newTexId);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, (GLsizei) width, (GLsizei) height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    uint8_t* pixels = (uint8_t*) vglGetTexDataPointer(GL_TEXTURE_2D);
    if (pixels == NULL) { glDeleteTextures(1, &newTexId); free(mask); free(compressed); sceIoClose(file); return -2; }
    hldStripJob.dst = pixels;
    hldStripJob.rowBytes = width * 4;
    hldStripJob.dstStride = (((size_t) width + 7) & ~(size_t) 7) * 4;
    hldStripJob.mask = mask;
    hldStripJob.maskStride = maskStride;
    hldStripJob.filtered = false;
    hldStripJob.strips = strips;
    hldStripJob.ready = hldStripJob.next = hldStripJob.done = hldStripJob.failed = 0;
    size_t at = 0;
    bool read = true;
    for (uint32_t i = 0; i < strips; ++i) {
        hldStripJob.src[i] = compressed + at;
        if (read) read = hldStripRead(file, compressed + at, hldStripJob.strip[i].compressedBytes);
        if (!read) memset(compressed + at, 0, hldStripJob.strip[i].compressedBytes);
        at += hldStripJob.strip[i].compressedBytes;
        __atomic_store_n(&hldStripJob.ready, (int) i + 1, __ATOMIC_RELEASE);
        if (hldStripHelpers > 0) sceKernelSignalSema(hldStripReady, hldStripHelpers);
        else hldStripWork(2, false);
    }
    sceIoClose(file);
    hldStripWork(2, false);
    sceKernelWaitSema(hldStripFinished, 1, NULL);
    free(compressed);
    hldStripJob.mask = NULL;
    if (!read || hldStripJob.failed) {
        glDeleteTextures(1, &newTexId);
        free(mask);
        logWarn("GL: layer strips failed for %s, using the PNG\n", path);
        return -2;
    }
    GLCommon_applyTexFilter(smooth);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);
    const int32_t w = (int32_t) width, h = (int32_t) height;
    uint32_t pageId = findOrAllocTexturePageSlot(gl);
    gl->glTextures[pageId] = newTexId;
    gl->textureWidths[pageId] = w;
    gl->textureHeights[pageId] = h;
    gl->textureLoaded[pageId] = true;
    uint32_t tpagIndex = findOrAllocTpagSlot(dw, gl->originalTpagCount);
    TexturePageItem* tpag = &dw->tpag.items[tpagIndex];
    tpag->sourceX = 0;
    tpag->sourceY = 0;
    tpag->sourceWidth = (uint16_t) w;
    tpag->sourceHeight = (uint16_t) h;
    tpag->targetX = 0;
    tpag->targetY = 0;
    tpag->targetWidth = (uint16_t) w;
    tpag->targetHeight = (uint16_t) h;
    tpag->boundingWidth = (uint16_t) w;
    tpag->boundingHeight = (uint16_t) h;
    tpag->texturePageId = (int16_t) pageId;
    uint32_t spriteIndex = DataWin_allocSpriteSlot(dw, gl->originalSpriteCount);
    Sprite* sprite = &dw->sprt.sprites[spriteIndex];
    sprite->width = (uint32_t) w;
    sprite->height = (uint32_t) h;
    sprite->originX = xorig;
    sprite->originY = yorig;
    sprite->textureCount = 1;
    sprite->tpagIndices = (int32_t *)safeMalloc(sizeof(int32_t));
    sprite->tpagIndices[0] = (int32_t) tpagIndex;
    sprite->present = true;
    sprite->smooth = smooth;
    sprite->bboxMode = 0;
    sprite->maskCount = 1;
    sprite->maskWidth = (uint32_t)w;
    sprite->maskHeight = (uint32_t)h;
    sprite->masks = safeMalloc(sizeof(*sprite->masks));
    sprite->masks[0] = mask;
    int32_t left = w, top = h, right = -1, bottom = -1;
    for (uint32_t i = 0; i < strips; ++i) {
        if (hldStripJob.bounds[i][2] < 0) continue;  // no visible pixel in this strip
        if (hldStripJob.bounds[i][0] < left) left = hldStripJob.bounds[i][0];
        if (hldStripJob.bounds[i][2] > right) right = hldStripJob.bounds[i][2];
        if (hldStripJob.bounds[i][1] < top) top = hldStripJob.bounds[i][1];
        if (hldStripJob.bounds[i][3] > bottom) bottom = hldStripJob.bounds[i][3];
    }
    sprite->marginLeft = left;
    sprite->marginRight = right;
    sprite->marginTop = top;
    sprite->marginBottom = bottom;
    logInfo("GL: Created dynamic sprite %u (%dx%d), mask bounds (%d,%d)–(%d,%d) strips ms=%.1f\n",
        spriteIndex, w, h, left, top, right, bottom, (nowNanos() - start) * 1e-6);
    return (int32_t) spriteIndex;
}
#else
int32_t hldStripSpriteCreate(Renderer* renderer, const char* path, bool smooth, int32_t xorig, int32_t yorig) {
    (void) renderer; (void) path; (void) smooth; (void) xorig; (void) yorig; return -2;
}
bool hldStripLoad(GLRenderer* gl, uint32_t pageId) { (void) gl; (void) pageId; return false; }
bool hldStripDecodeInto(uint32_t page, uint8_t* dst, size_t stride, int w, int h) { (void) page; (void) dst; (void) stride; (void) w; (void) h; return false; }
#endif
"""

PAGE_STRIPS = PAGE_STRIPS_TEMPLATE.replace("/*HLZ_HEADER*/", (Path(__file__).resolve().parent / "page_strips.h").read_text().replace("#pragma once", ""))


def with_texture_flush_renderer(code):
    assert "static void flushBatch(GLRenderer* gl) {" in code
    anchor = "    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previousTexture);\n    gl->textureLoaded[pageId] = true;"
    if anchor in code:
        code = replace_once(code, anchor, "    glGetIntegerv(GL_TEXTURE_BINDING_2D, &previousTexture);\n"
                            "    { extern bool hldPagePrefetchFinish(GLRenderer*, uint32_t); if (hldPagePrefetchFinish(gl, pageId)) { "
                            "glBindTexture(GL_TEXTURE_2D, (GLuint) previousTexture); return gl->textureWidths[pageId] != 0; } } "
                            "gl->textureLoaded[pageId] = true;")
    # Time each on-demand page decode (and its frame), so menu/map/portrait stalls show which pages cause them.
    start = "static bool loadTexturePage(GLRenderer* gl, uint32_t pageId) {\n"
    done = '    logInfo("GL: Loaded TXTR page %u (%dx%d)%s\\n", pageId, w, h, directUpload ? " streamed" : "");'
    if start in code and done in code:
        code = replace_once(code, start, start + "    uint64_t nowNanos(void); const uint64_t hldPageStart = nowNanos();\n")
        code = replace_once(code, done, '    logInfo("GL: Loaded TXTR page %u (%dx%d)%s ms=%.1f frame=%d\\n", pageId, w, h, directUpload ? " streamed" : "", '
                            '(nowNanos() - hldPageStart) * 1e-6, gl->base.runner != NULL ? gl->base.runner->frameCount : -1);')
    if os.environ.get("HLDC_DRAWSTATS") == "1":
        # Diagnostic: batches and quads per frame, and how long the GPU still needs after the frame is submitted.
        code = replace_once(code, "    int32_t indexCount = modernGl->batchCount * INDICES_PER_QUAD;\n",
                            "    int32_t indexCount = modernGl->batchCount * INDICES_PER_QUAD; "
                            "{ extern unsigned hldDrawFlushes, hldDrawQuads; ++hldDrawFlushes; hldDrawQuads += modernGl->batchCount; }\n")
        code = replace_once(code, "#ifdef PLATFORM_VITA\n    int32_t frame = renderer->runner->frameCount;\n",
                            "#ifdef PLATFORM_VITA\n    int32_t frame = renderer->runner->frameCount; hldDrawFrameEnd(frame);\n")
        code = replace_once(code, "static void glEndFrameEnd(Renderer* renderer) {",
                            "static void hldDrawFrameEnd(int32_t frame);\nstatic void glEndFrameEnd(Renderer* renderer) {")
        code += DRAW_STATS
    txtr_load = "    DataWin_loadTxtrIfNeeded(dw, pageId);\n"
    if txtr_load in code and "static bool loadTexturePage(GLRenderer* gl, uint32_t pageId) {" in code:
        start = code.index("static bool loadTexturePage(GLRenderer* gl, uint32_t pageId) {")
        end = code.index("\n}\n", start)
        body = replace_once(code[start:end], txtr_load, "    { extern bool hldStripLoad(GLRenderer*, uint32_t); if (hldStripLoad(gl, pageId)) return true; }\n" + txtr_load)
        code = code[:start] + body + code[end:]
    used = "bool GLRenderer_ensureTextureLoaded(GLRenderer* gl, uint32_t pageId) {\n"
    if used in code:
        code = replace_once(code, used, used + "    { extern uint32_t hldPageLastUse[]; if (pageId < 4096 && gl->base.runner != NULL) "
                            "hldPageLastUse[pageId] = (uint32_t) gl->base.runner->frameCount; }\n")
    loaded = "    bool loaded = loadTexturePage(gl, pageId);\n"
    if loaded in code:
        code = replace_once(code, loaded, loaded + "    { extern void hldUiPageNote(GLRenderer*, uint32_t, bool); hldUiPageNote(gl, pageId, loaded); }\n")
    early = "    if (hldPageKnown == NULL && runner != NULL) hldPageInit(gl);" if os.environ.get("HLDC_BOOT_PREFETCH", "1") == "1" else ""
    return code + PAGE_PREFETCH.replace("    HLD_EARLY_PAGE_INIT", early) + PAGE_STRIPS + UI_PAGES + UNLOAD_PAGES


DRAW_STATS = r"""
uint64_t nowNanos(void);
unsigned hldDrawFlushes, hldDrawQuads;
// sceGxm calls timed through -Wl,--wrap: where the CPU blocks on the GPU.
uint64_t hldGxmEndNs; static uint64_t hldGxmBeginNs, hldGxmDrawNs; static unsigned hldGxmBegins;
int __real_sceGxmBeginScene(void*, unsigned, const void*, const void*, void*, void*, const void*, const void*);
// Scene list of one frame (HLDSCENE): render target, draws, CPU time blocked ending it.
int hldSceneTraceFrame = -1; static unsigned hldSceneDraws; static const void* hldSceneTarget; static const void* hldSceneColor;
int __wrap_sceGxmBeginScene(void* c, unsigned f, const void* rt, const void* vr, void* vs, void* fs, const void* cs, const void* ds) {
    const uint64_t t = nowNanos(); const int r = __real_sceGxmBeginScene(c, f, rt, vr, vs, fs, cs, ds);
    hldGxmBeginNs += nowNanos() - t; ++hldGxmBegins; hldSceneDraws = 0; hldSceneTarget = rt; hldSceneColor = cs; return r;
}
void hldSceneEnded(uint64_t ns) {
    if (hldSceneTraceFrame >= 0)
        logInfo("HLDSCENE frame=%d rt=%p color=%p draws=%u end_ms=%.2f\n", hldSceneTraceFrame, hldSceneTarget, hldSceneColor,
            hldSceneDraws, ns * 1e-6);
}
int __real_sceGxmDraw(void*, int, int, const void*, unsigned);
int __wrap_sceGxmDraw(void* c, int p, int i, const void* d, unsigned n) {
    const uint64_t t = nowNanos(); const int r = __real_sceGxmDraw(c, p, i, d, n); hldGxmDrawNs += nowNanos() - t; ++hldSceneDraws; return r;
}
static void hldDrawFrameEnd(int32_t frame) {
    hldSceneTraceFrame = (frame == 299 || frame == 539) ? frame + 1 : -1;
    static unsigned frames, maxFlushes, maxQuads;
    static double gpuWait, maxGpuWait;
    const uint64_t start = nowNanos();
#ifdef HLD_GPU_FINISH
    glFinish();
#endif
    const double wait = (nowNanos() - start) * 1e-6;
    gpuWait += wait; if (wait > maxGpuWait) maxGpuWait = wait;
    if (hldDrawFlushes > maxFlushes) maxFlushes = hldDrawFlushes;
    if (hldDrawQuads > maxQuads) maxQuads = hldDrawQuads;
    static unsigned sumFlushes, sumQuads;
    sumFlushes += hldDrawFlushes; sumQuads += hldDrawQuads; ++frames;
    hldDrawFlushes = hldDrawQuads = 0;
    if (frame % 90 == 0 && frames) {
        logInfo("HLDDRAW frame=%d batches=%.0f max=%u quads=%.0f max=%u gpu_tail_ms=%.2f max=%.2f "
            "begin_ms=%.2f n=%.1f end_ms=%.2f draw_ms=%.2f\n", frame,
            (double) sumFlushes / frames, maxFlushes, (double) sumQuads / frames, maxQuads, gpuWait / frames, maxGpuWait,
            hldGxmBeginNs * 1e-6 / frames, (double) hldGxmBegins / frames, hldGxmEndNs * 1e-6 / frames, hldGxmDrawNs * 1e-6 / frames);
        hldGxmBeginNs = hldGxmEndNs = hldGxmDrawNs = 0; hldGxmBegins = 0;
        frames = sumFlushes = sumQuads = maxFlushes = maxQuads = 0; gpuWait = maxGpuWait = 0;
    }
}
"""


# ds_map_secure_save / ds_map_secure_load (the PC save path HLD uses here): Codex's secure_save_source.py bodies,
# appended at the end and registered on the existing ds_map_write line so no earlier line moves.
SECURE_REGISTER = '    VM_registerBuiltin(ctx, "ds_map_write", builtin_ds_map_write);'


def with_secure_save_appended(code):
    from secure_save_source import with_secure_save
    if '"ds_map_secure_save"' in code:
        return code
    patched = with_secure_save(code)
    start = patched.index('static const char HLD_SECURE_SAVE_HEADER[]')
    end = patched.index("static RValue builtin_ds_grid_write(VMContext* ctx, RValue* args, MAYBE_UNUSED int32_t argCount) {")
    functions = patched[start:end]
    functions = functions.replace("static RValue builtin_ds_map_secure_save(", "RValue hldSecureMapSave(")
    functions = functions.replace("static RValue builtin_ds_map_secure_load(", "RValue hldSecureMapLoad(")
    code = replace_once(code, SECURE_REGISTER, SECURE_REGISTER + " { extern RValue hldSecureMapSave(VMContext*, RValue*, int32_t); "
                        "extern RValue hldSecureMapLoad(VMContext*, RValue*, int32_t); "
                        'VM_registerBuiltin(ctx, "ds_map_secure_save", hldSecureMapSave); '
                        'VM_registerBuiltin(ctx, "ds_map_secure_load", hldSecureMapLoad); }')
    return code + "\n" + functions


# Saving writes, copies, renames and deletes files on the memory card synchronously (~0.2 s hitch). The first
# file_delete / file_rename / file_copy / ds_map_secure_save puts a proxy in front of the runner's file system:
# writes, deletes and renames go to one worker thread in order and return success at once; file_exists answers
# from the pending operations; any read of a path with a pending operation (and every other kind of access)
# first waits for the queue to drain, so nothing ever observes a stale file.
FILE_QUEUE = r"""
#include <pthread.h>
enum { HLD_FILE_WRITE_TEXT, HLD_FILE_WRITE_BINARY, HLD_FILE_DELETE, HLD_FILE_RENAME };
typedef struct HldFileOp { int kind; char* path; char* other; uint8_t* data; int32_t size; struct HldFileOp* next; } HldFileOp;
typedef struct { FileSystem base; FileSystem* inner; FileSystemVtable vtable; } HldFileQueue;
static HldFileQueue hldFileQueue;
static pthread_mutex_t hldFileLock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t hldFileWake = PTHREAD_COND_INITIALIZER, hldFileIdle = PTHREAD_COND_INITIALIZER;
static HldFileOp* hldFileHead;
static HldFileOp* hldFileTail;
static bool hldFileBusy;

static void* hldFileWorker(void* unused) {
    (void) unused;
    FileSystem* inner = hldFileQueue.inner;
    pthread_mutex_lock(&hldFileLock);
    for (;;) {
        while (hldFileHead == NULL) pthread_cond_wait(&hldFileWake, &hldFileLock);
        HldFileOp* op = hldFileHead;
        hldFileBusy = true;
        pthread_mutex_unlock(&hldFileLock);
        switch (op->kind) {
            case HLD_FILE_WRITE_TEXT: inner->vtable->writeFileText(inner, op->path, (const char*) op->data); break;
            case HLD_FILE_WRITE_BINARY: inner->vtable->writeFileBinary(inner, op->path, op->data, op->size); break;
            case HLD_FILE_DELETE: inner->vtable->deleteFile(inner, op->path); break;
            case HLD_FILE_RENAME: inner->vtable->renameFile(inner, op->path, op->other); break;
        }
        pthread_mutex_lock(&hldFileLock);
        hldFileHead = op->next;
        if (hldFileHead == NULL) hldFileTail = NULL;
        hldFileBusy = false;
        free(op->path);
        free(op->other);
        free(op->data);
        free(op);
        if (hldFileHead == NULL) pthread_cond_broadcast(&hldFileIdle);
    }
    return NULL;
}

static void hldFileDrain(void) {
    pthread_mutex_lock(&hldFileLock);
    while (hldFileHead != NULL || hldFileBusy) pthread_cond_wait(&hldFileIdle, &hldFileLock);
    pthread_mutex_unlock(&hldFileLock);
}

// file_exists answers remembered per path (the game only touches its save files through this queue, so the card cannot
// change behind it): each checkpoint save asked the card ~7 times (save, backups 1-3, again before each rename), a
// synchronous lookup each, ~0.1 s per save in all. Queued writes/deletes/renames update the entries. The lock must be held.
enum { HLD_EXIST_CACHE = 64 };
static struct { char* path; bool exists; } hldExistCache[HLD_EXIST_CACHE];
static unsigned hldExistNext;
// Only save files and their backups: those change only through this queue (settings files may be written elsewhere).
static bool hldExistCacheable(const char* path) { return strstr(path, ".sav") != NULL || strstr(path, "HLD_BACKUPS") != NULL; }
static int hldExistLookup(const char* path) {
    if (!hldExistCacheable(path)) return -1;
    for (unsigned i = 0; i < HLD_EXIST_CACHE; ++i)
        if (hldExistCache[i].path != NULL && strcmp(hldExistCache[i].path, path) == 0) return hldExistCache[i].exists;
    return -1;
}
static void hldExistStore(const char* path, bool exists) {
    if (!hldExistCacheable(path)) return;
    for (unsigned i = 0; i < HLD_EXIST_CACHE; ++i)
        if (hldExistCache[i].path != NULL && strcmp(hldExistCache[i].path, path) == 0) { hldExistCache[i].exists = exists; return; }
    const unsigned slot = hldExistNext++ % HLD_EXIST_CACHE;
    free(hldExistCache[slot].path);
    hldExistCache[slot].path = safeStrdup(path);
    hldExistCache[slot].exists = exists;
}

static void hldFileEnqueue(int kind, const char* path, const char* other, const void* data, int32_t size) {
    HldFileOp* op = (HldFileOp*) safeCalloc(1, sizeof(HldFileOp));
    op->kind = kind;
    op->path = safeStrdup(path);
    op->other = other != NULL ? safeStrdup(other) : NULL;
    if (data != NULL) {
        op->data = (uint8_t*) safeMalloc((size_t) size + 1);
        memcpy(op->data, data, (size_t) size);
        op->data[size] = 0;
        op->size = size;
    }
    pthread_mutex_lock(&hldFileLock);
    hldExistStore(path, kind == HLD_FILE_WRITE_TEXT || kind == HLD_FILE_WRITE_BINARY);
    if (kind == HLD_FILE_RENAME) hldExistStore(other, true);
    if (hldFileTail != NULL) hldFileTail->next = op; else hldFileHead = op;
    hldFileTail = op;
    pthread_cond_signal(&hldFileWake);
    pthread_mutex_unlock(&hldFileLock);
}

// The pending effect on a path: 1 exists, 0 missing, -1 untouched. The lock must be held.
static int hldFilePendingState(const char* path) {
    int state = -1;
    for (HldFileOp* op = hldFileHead; op != NULL; op = op->next) {
        if (strcmp(op->path, path) == 0) state = op->kind == HLD_FILE_DELETE || op->kind == HLD_FILE_RENAME ? 0 : 1;
        if (op->kind == HLD_FILE_RENAME && strcmp(op->other, path) == 0) state = 1;
    }
    return state;
}

static bool hldFileTouched(const char* path) {
    pthread_mutex_lock(&hldFileLock);
    const bool touched = hldFilePendingState(path) != -1;
    pthread_mutex_unlock(&hldFileLock);
    return touched;
}

static FileSystem* hldFileInner(void) { return hldFileQueue.inner; }

static char* hldQueueResolve(FileSystem* fs, const char* p) { (void) fs; return hldFileInner()->vtable->resolvePath(hldFileInner(), p); }
static bool hldQueueExists(FileSystem* fs, const char* p) {
    (void) fs;
    pthread_mutex_lock(&hldFileLock);
    int state = hldFilePendingState(p);
    if (state == -1 && getenv("HLD_NO_EXIST_CACHE") == NULL) state = hldExistLookup(p);
    pthread_mutex_unlock(&hldFileLock);
    if (state != -1) return state == 1;
    const bool exists = hldFileInner()->vtable->fileExists(hldFileInner(), p);
    pthread_mutex_lock(&hldFileLock);
    if (hldFilePendingState(p) == -1) hldExistStore(p, exists);
    pthread_mutex_unlock(&hldFileLock);
    return exists;
}
static char* hldQueueReadText(FileSystem* fs, const char* p) { (void) fs; if (hldFileTouched(p)) hldFileDrain(); return hldFileInner()->vtable->readFileText(hldFileInner(), p); }
static bool hldQueueWriteText(FileSystem* fs, const char* p, const char* c) { (void) fs; hldFileEnqueue(HLD_FILE_WRITE_TEXT, p, NULL, c, (int32_t) strlen(c)); return true; }
static bool hldQueueDelete(FileSystem* fs, const char* p) { (void) fs; hldFileEnqueue(HLD_FILE_DELETE, p, NULL, NULL, 0); return true; }
static bool hldQueueReadBinary(FileSystem* fs, const char* p, uint8_t** d, int32_t* n) { (void) fs; if (hldFileTouched(p)) hldFileDrain(); return hldFileInner()->vtable->readFileBinary(hldFileInner(), p, d, n); }
static bool hldQueueWriteBinary(FileSystem* fs, const char* p, const uint8_t* d, int32_t n) { (void) fs; hldFileEnqueue(HLD_FILE_WRITE_BINARY, p, NULL, d, n); return true; }
static bool hldQueueRename(FileSystem* fs, const char* a, const char* b) {
    (void) fs;
    // file_rename reports whether the source existed; answer that from the same view file_exists uses.
    const bool exists = hldQueueExists(fs, a);
    if (exists) hldFileEnqueue(HLD_FILE_RENAME, a, b, NULL, 0);
    return exists;
}
static void* hldQueueOpen(FileSystem* fs, const char* p, int32_t m) { (void) fs; hldFileDrain(); return hldFileInner()->vtable->binaryOpen(hldFileInner(), p, m); }
static bool hldQueueDirExists(FileSystem* fs, const char* p) { (void) fs; hldFileDrain(); return hldFileInner()->vtable->directoryExists(hldFileInner(), p); }
static bool hldQueueMakeDir(FileSystem* fs, const char* p) { (void) fs; hldFileDrain(); return hldFileInner()->vtable->createDirectory(hldFileInner(), p); }
static bool hldQueueRemoveDir(FileSystem* fs, const char* p) { (void) fs; hldFileDrain(); return hldFileInner()->vtable->deleteDirectory(hldFileInner(), p); }
static FileSystemDirEntry* hldQueueList(FileSystem* fs, const char* p) { (void) fs; hldFileDrain(); return hldFileInner()->vtable->listDirectory(hldFileInner(), p); }

void hldInstallFileQueue(Runner* runner) {
    if (hldFileQueue.inner != NULL || runner == NULL || runner->fileSystem == NULL) return;
    hldFileQueue.inner = runner->fileSystem;
    hldFileQueue.vtable = *runner->fileSystem->vtable;
    hldFileQueue.vtable.resolvePath = hldQueueResolve;
    hldFileQueue.vtable.fileExists = hldQueueExists;
    hldFileQueue.vtable.readFileText = hldQueueReadText;
    hldFileQueue.vtable.writeFileText = hldQueueWriteText;
    hldFileQueue.vtable.deleteFile = hldQueueDelete;
    hldFileQueue.vtable.readFileBinary = hldQueueReadBinary;
    hldFileQueue.vtable.writeFileBinary = hldQueueWriteBinary;
    hldFileQueue.vtable.renameFile = hldQueueRename;
    hldFileQueue.vtable.binaryOpen = hldQueueOpen;
    hldFileQueue.vtable.directoryExists = hldQueueDirExists;
    hldFileQueue.vtable.createDirectory = hldQueueMakeDir;
    hldFileQueue.vtable.deleteDirectory = hldQueueRemoveDir;
    hldFileQueue.vtable.listDirectory = hldQueueList;
    hldFileQueue.base.vtable = &hldFileQueue.vtable;
    pthread_t worker;
    pthread_attr_t attributes;
    pthread_attr_init(&attributes);
    pthread_attr_setstacksize(&attributes, 64 * 1024);
    if (pthread_create(&worker, &attributes, hldFileWorker, NULL) != 0) {
        hldFileQueue.inner = NULL;
        return;
    }
    pthread_detach(worker);
    runner->fileSystem = &hldFileQueue.base;
    logInfo("HLD: file writes run on a worker thread\n");
}
"""

FILE_HOOKS = (
    ("static RValue builtin_file_delete(VMContext* ctx, RValue* args, int32_t argCount) {", "    FileSystem* fs = runner->fileSystem;"),
    ("static RValue builtin_file_rename(VMContext* ctx, RValue* args, int32_t argCount) {", "    FileSystem* fs = runner->fileSystem;"),
    ("static RValue builtin_file_copy(VMContext* ctx, RValue* args, int32_t argCount) {", "    FileSystem* fs = ctx->runner->fileSystem;"),
)


def with_file_queue(code):
    for signature, line in FILE_HOOKS:
        start = code.index(signature)
        end = code.index("\n}\n", start)
        body = replace_once(code[start:end], line, "    { extern void hldInstallFileQueue(Runner*); hldInstallFileQueue(ctx->runner); } " + line.strip())
        code = code[:start] + body + code[end:]
    code = replace_once(code, "RValue hldSecureMapSave(VMContext* ctx, RValue* args, int32_t argCount) {",
                        "void hldInstallFileQueue(Runner*); RValue hldSecureMapSave(VMContext* ctx, RValue* args, int32_t argCount) { "
                        "hldInstallFileQueue(ctx->runner);")
    return code + FILE_QUEUE


# Load-time diagnostics: sprite_add (the level layer images) logs how long each call takes.
SPRITE_ADD_REGISTER = '    VM_registerBuiltin(ctx, "sprite_add", builtin_sprite_add);'
SPRITE_DELETE_REGISTER = '    VM_registerBuiltin(ctx, "sprite_delete", builtin_sprite_delete);'
SPRITE_ADD_TIMING = r"""
// Room layer reuse. A respawn reloads the same room: LevelLoad deletes each room-sized layer sprite and immediately
// sprite_adds the same file again, re-decoding the PNG. A deleted layer sprite is kept aside (its slot cleared exactly as
// a delete would) and handed back if the next sprite_add asks for the same file and arguments; any other sprite_add first
// releases everything kept aside, so a normal room change frees the old layers before decoding new ones.
enum { HLD_LAYER_SPRITES = 8 };
typedef struct {
    char* path;
    int32_t removeBack, smooth, xorig, yorig;
    int32_t live;     // sprite index while in use, -1 while kept aside
    int32_t keptFrame;  // frame it was kept aside on; released on a later frame if not reused
    Sprite pristine;  // fields as sprite_add created them (GML may change bbox/margins afterwards)
} HldLayerSprite;
static HldLayerSprite hldLayerSprites[HLD_LAYER_SPRITES];

static bool hldLayerSpriteGame(VMContext* ctx) {
    static int disabled = -1;  // read once: this runs every frame
    if (disabled < 0) disabled = getenv("HLD_NO_LAYER_REUSE") != nullptr;
    return !disabled &&
           ctx->dataWin->gen8.gameID == 742741103 && ctx->dataWin->gen8.wadVersion == 15 && ctx->runner->renderer != nullptr &&
           ctx->runner->renderer->vtable->deleteSprite != nullptr;
}

static void hldLayerSpriteForget(HldLayerSprite* entry) {
    free(entry->path);
    memset(entry, 0, sizeof(*entry));
}

static bool hldLayerSpriteShared(const HldLayerSprite* entry) {
    for (int i = 0; i < HLD_LAYER_SPRITES; ++i)
        if (&hldLayerSprites[i] != entry && hldLayerSprites[i].path != nullptr &&
            hldLayerSprites[i].pristine.tpagIndices == entry->pristine.tpagIndices) return true;
    return false;
}

static void hldLayerSpriteRelease(VMContext* ctx) {
    DataWin* dw = ctx->dataWin;
    for (int i = 0; i < HLD_LAYER_SPRITES; ++i) {
        HldLayerSprite* entry = &hldLayerSprites[i];
        if (entry->path == nullptr || entry->live >= 0) continue;
        // Put the kept sprite into a free slot and delete it there, freeing its texture, page item and masks.
        const uint32_t index = DataWin_allocSpriteSlot(dw, dw->sprt.parsedCount);
        const char* name = dw->sprt.sprites[index].name;
        dw->sprt.sprites[index] = entry->pristine;
        dw->sprt.sprites[index].name = name;
        ctx->runner->renderer->vtable->deleteSprite(ctx->runner->renderer, (int32_t) index);
        logInfo("HLD layer sprite released file=%s\n", entry->path);
        hldLayerSpriteForget(entry);
    }
}

static RValue hldLayerSpriteAdd(VMContext* ctx, RValue* args, int32_t argCount) {
    if (!hldLayerSpriteGame(ctx) || argCount < 6 || args[0].type != RVALUE_STRING || RValue_toInt32(args[1]) != 1)
        return builtin_sprite_add(ctx, args, argCount);
    const int32_t removeBack = RValue_toBool(args[2]), smooth = RValue_toBool(args[3]);
    const int32_t xorig = RValue_toInt32(args[4]), yorig = RValue_toInt32(args[5]);
    DataWin* dw = ctx->dataWin;
    for (int i = 0; i < HLD_LAYER_SPRITES; ++i) {
        HldLayerSprite* entry = &hldLayerSprites[i];
        if (entry->path == nullptr || entry->live >= 0 || strcmp(entry->path, args[0].string) != 0 ||
            entry->removeBack != removeBack || entry->smooth != smooth || entry->xorig != xorig || entry->yorig != yorig) continue;
        const uint32_t index = DataWin_allocSpriteSlot(dw, dw->sprt.parsedCount);
        const char* name = dw->sprt.sprites[index].name;
        dw->sprt.sprites[index] = entry->pristine;
        dw->sprt.sprites[index].name = name;
        entry->live = (int32_t) index;
        logInfo("HLD layer sprite reused index=%u file=%s\n", index, entry->path);
        return RValue_makeInt32((int32_t) index);
    }
    // The same file still in use (CreateInverseFloor adds the new floor before deleting the old one): share it.
    for (int i = 0; i < HLD_LAYER_SPRITES; ++i) {
        HldLayerSprite* entry = &hldLayerSprites[i];
        if (entry->path == nullptr || entry->live < 0 || strcmp(entry->path, args[0].string) != 0 ||
            entry->removeBack != removeBack || entry->smooth != smooth || entry->xorig != xorig || entry->yorig != yorig) continue;
        int slot = -1;
        for (int k = 0; k < HLD_LAYER_SPRITES && slot < 0; ++k) if (hldLayerSprites[k].path == nullptr) slot = k;
        if (slot < 0) break;
        const uint32_t index = DataWin_allocSpriteSlot(dw, dw->sprt.parsedCount);
        const char* name = dw->sprt.sprites[index].name;
        dw->sprt.sprites[index] = entry->pristine;
        dw->sprt.sprites[index].name = name;
        HldLayerSprite* copy = &hldLayerSprites[slot];
        *copy = *entry;
        copy->path = safeStrdup(entry->path);
        copy->live = (int32_t) index;
        logInfo("HLD layer sprite shared index=%u file=%s\n", index, copy->path);
        return RValue_makeInt32((int32_t) index);
    }
    hldLayerSpriteRelease(ctx);
    RValue result = builtin_sprite_add(ctx, args, argCount);
    const int32_t index = RValue_toInt32(result);
    if (index < (int32_t) dw->sprt.parsedCount || (uint32_t) index >= dw->sprt.count) return result;
    const Sprite* sprite = &dw->sprt.sprites[index];
    if ((uint64_t) sprite->width * sprite->height < 960u * 544u || sprite->textureCount != 1) return result;
    int slot = -1;
    for (int i = 0; i < HLD_LAYER_SPRITES && slot < 0; ++i) if (hldLayerSprites[i].path == nullptr) slot = i;
    for (int i = 0; i < HLD_LAYER_SPRITES && slot < 0; ++i)
        if (hldLayerSprites[i].live >= 0 && !hldLayerSpriteShared(&hldLayerSprites[i])) slot = i;  // forget a live one
    if (slot < 0) return result;
    if (hldLayerSprites[slot].path != nullptr) hldLayerSpriteForget(&hldLayerSprites[slot]);
    HldLayerSprite* entry = &hldLayerSprites[slot];
    entry->path = safeStrdup(args[0].string);
    entry->removeBack = removeBack;
    entry->smooth = smooth;
    entry->xorig = xorig;
    entry->yorig = yorig;
    entry->live = index;
    entry->pristine = *sprite;
    return result;
}

static RValue hldLayerSpriteDelete(VMContext* ctx, RValue* args, int32_t argCount) {
    if (hldLayerSpriteGame(ctx) && argCount >= 1) {
        const int32_t index = RValue_toInt32(args[0]);
        DataWin* dw = ctx->dataWin;
        for (int i = 0; i < HLD_LAYER_SPRITES; ++i) {
            HldLayerSprite* entry = &hldLayerSprites[i];
            if (entry->path == nullptr || entry->live != index) continue;
            Sprite* sprite = &dw->sprt.sprites[index];
            if (sprite->textureCount != 1 || sprite->tpagIndices != entry->pristine.tpagIndices ||
                sprite->masks != entry->pristine.masks) {
                logWarn("HLD layer sprite %d changed under the reuse table\n", index);
                hldLayerSpriteForget(entry);
                break;
            }
            // Clear the slot as the renderer's delete does, keeping the texture, page item and masks aside
            // (or leaving them to the other sprite that shares them).
            const char* name = sprite->name;
            memset(sprite, 0, sizeof(*sprite));
            sprite->name = name;
            if (hldLayerSpriteShared(entry)) {
                hldLayerSpriteForget(entry);
            } else {
                entry->live = -1;
                entry->keptFrame = ctx->runner->frameCount;
            }
            return RValue_makeUndefined();
        }
    }
    return builtin_sprite_delete(ctx, args, argCount);
}

// Called once per frame: anything kept aside on an earlier frame was not reused, so free it.
void hldLayerSpriteFrame(Runner* runner) {
    VMContext* ctx = runner->vmContext;
    if (ctx == nullptr || !hldLayerSpriteGame(ctx)) return;
    bool stale = false;
    for (int i = 0; i < HLD_LAYER_SPRITES; ++i)
        if (hldLayerSprites[i].path != nullptr && hldLayerSprites[i].live < 0 && hldLayerSprites[i].keptFrame != runner->frameCount)
            stale = true;
    if (stale) hldLayerSpriteRelease(ctx);
}

RValue hldTimedSpriteDelete(VMContext* ctx, RValue* args, int32_t argCount) { return hldLayerSpriteDelete(ctx, args, argCount); }

uint64_t hldSpriteAddRead, hldSpriteAddDecode, hldSpriteAddCreate;
RValue hldTimedSpriteAdd(VMContext* ctx, RValue* args, int32_t argCount) {
    const uint64_t start = nowNanos();
    hldSpriteAddRead = hldSpriteAddDecode = hldSpriteAddCreate = 0;
    RValue result = hldLayerSpriteAdd(ctx, args, argCount);
    logInfo("HLD sprite_add ms=%.1f read_ms=%.1f decode_ms=%.1f create_ms=%.1f file=%s\n", (nowNanos() - start) * 1e-6,
            hldSpriteAddRead * 1e-6, hldSpriteAddDecode * 1e-6, hldSpriteAddCreate * 1e-6,
            argCount > 0 && args[0].type == RVALUE_STRING ? args[0].string : "?");
    return result;
}
"""


LAYER_FAST = '    if (!RValue_toBool(args[2]) && getenv("HLD_NO_LAYER_STRIPS") == NULL) {\n        extern int32_t hldStripSpriteCreate(Renderer*, const char*, bool, int32_t, int32_t);\n        char hlz[600];\n        snprintf(hlz, sizeof hlz, "%s.hlz", args[0].string);\n        for (char* c = hlz; *c; ++c) if (*c == \'\\\\\') *c = \'/\';\n        const int32_t fast = hldStripSpriteCreate(renderer, hlz, RValue_toBool(args[3]), RValue_toInt32(args[4]), RValue_toInt32(args[5]));\n        if (fast >= 0) return RValue_makeInt32(fast);\n    }\n'


def with_sprite_add_timing(code):
    if SPRITE_ADD_REGISTER not in code:
        return code
    start = code.index("static RValue builtin_sprite_add(VMContext* ctx, RValue* args, int32_t argCount) {")
    end = code.index("\n}\n", start)
    body = code[start:end]
    body = replace_once(body, "    if (!fs->vtable->readFileBinary(fs, args[0].string, &bytes, &size)) return RValue_makeInt32(-1);",
        "    extern uint64_t hldSpriteAddRead, hldSpriteAddDecode, hldSpriteAddCreate; uint64_t hldSaT = nowNanos();\n"
        "    if (!fs->vtable->readFileBinary(fs, args[0].string, &bytes, &size)) return RValue_makeInt32(-1);\n"
        "    hldSpriteAddRead += nowNanos() - hldSaT; hldSaT = nowNanos();")
    body = replace_once(body, "    free(bytes);\n    if (pixels == nullptr) return RValue_makeInt32(-1);",
        "    free(bytes);\n    hldSpriteAddDecode += nowNanos() - hldSaT; hldSaT = nowNanos();\n    if (pixels == nullptr) return RValue_makeInt32(-1);")
    body = replace_once(body, "    free(pixels);\n    return RValue_makeInt32(sprite);",
        "    free(pixels);\n    hldSpriteAddCreate += nowNanos() - hldSaT;\n    return RValue_makeInt32(sprite);")
    body = replace_once(body, "    extern uint64_t hldSpriteAddRead", LAYER_FAST + "    extern uint64_t hldSpriteAddRead")
    code = code[:start] + body + code[end:]
    code = replace_once(code, SPRITE_ADD_REGISTER, "    { extern RValue hldTimedSpriteAdd(VMContext*, RValue*, int32_t); "
                        'VM_registerBuiltin(ctx, "sprite_add", hldTimedSpriteAdd); }')
    code = replace_once(code, SPRITE_DELETE_REGISTER, "    { extern RValue hldTimedSpriteDelete(VMContext*, RValue*, int32_t); "
                        'VM_registerBuiltin(ctx, "sprite_delete", hldTimedSpriteDelete); }')
    return code + SPRITE_ADD_TIMING
