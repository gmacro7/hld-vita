"""textures.bin (Codex's external DXT5 pages, 1-4 MB each) was read with fread, which reaches the card at ~3 MB/s:
a 4 MB page took ~1.3 s (map, dialogue portraits, room loads). Pre-swizzled DXT5 records are now read with sceIoPread
straight into the texture's memory (no staging buffer or copy) and set up exactly as before; any other record, or any
failure, takes the original path."""

from runtime_verification_source import replace_once

FAST = r"""
#include <psp2/io/fcntl.h>
#include "log.h"
uint64_t nowNanos(void);
// The UI-page budget counts these pages at their real (compressed) size.
int hldExternalPage = -1;
uint32_t hldExternalBytes;
static SceUID hldTexBinFd = -1;
// 1: loaded, 0: not handled here (use the original path).
static int hldTexBinFastLoad(long offset, uint32_t recordSize, int pageIdx, int* outWidth, int* outHeight) {
    enum { pvrHeaderSize = 0x34 };
    if (hldTexBinFd == -1) {
        hldTexBinFd = sceIoOpen("ux0:data/hld-vm/textures.bin", SCE_O_RDONLY, 0);
        if (hldTexBinFd < 0) hldTexBinFd = -2;
    }
    if (hldTexBinFd < 0 || recordSize <= pvrHeaderSize + 4) return 0;
    const uint64_t start = nowNanos();
    uint32_t header[pvrHeaderSize / sizeof(uint32_t)];
    if (sceIoPread(hldTexBinFd, header, pvrHeaderSize, offset) != pvrHeaderSize || header[0] != 0x03525650u) return 0;
    const uint64_t format = (uint64_t) header[2] | ((uint64_t) header[3] << 32);
    const int height = (int) header[6], width = (int) header[7];
    const uint32_t metadata = header[12];
    if (format != 0x0B || (metadata != 4 && metadata != 0) || width <= 0 || height <= 0) return 0;
    const uint32_t size = recordSize - pvrHeaderSize - metadata;
    if (metadata == 0) {
        // Linear DXT5: the original's glCompressedTexImage2D upload, fed by one fast read.
        void* ext_data = vglMalloc(size);
        if (!ext_data) return 0;
        uint8_t* at = (uint8_t*) ext_data;
        uint32_t left = size;
        long position = offset + pvrHeaderSize;
        while (left > 0) {
            const int got = sceIoPread(hldTexBinFd, at, left > (4u << 20) ? (4u << 20) : left, position);
            if (got <= 0) { vglFree(ext_data); return 0; }
            at += got; position += got; left -= (uint32_t) got;
        }
        glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_S3TC_DXT5_EXT, width, height, 0, size, ext_data);
        vglFree(ext_data);
    } else {
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 8, 8, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    SceGxmTexture* gxm_tex = vglGetGxmTexture(GL_TEXTURE_2D);
    void* tex_data = vglForceAlloc(size);
    if (!tex_data) return 0;
    uint8_t* at = (uint8_t*) tex_data;
    uint32_t left = size;
    long position = offset + pvrHeaderSize + metadata;
    while (left > 0) {
        const int got = sceIoPread(hldTexBinFd, at, left > (4u << 20) ? (4u << 20) : left, position);
        if (got <= 0) { vglFree(tex_data); return 0; }
        at += got; position += got; left -= (uint32_t) got;
    }
    vglFree(vglGetTexDataPointer(GL_TEXTURE_2D));
    sceGxmTextureInitSwizzledArbitrary(gxm_tex, tex_data, SCE_GXM_TEXTURE_FORMAT_UBC3_ABGR, width, height, 0);
    vglOverloadTexDataPointer(GL_TEXTURE_2D, tex_data);
    }
    const bool isPOT = (width & (width - 1)) == 0 && (height & (height - 1)) == 0;
    const GLint wrapMode = isPOT ? GL_REPEAT : GL_CLAMP_TO_EDGE;
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, wrapMode);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, wrapMode);
    *outWidth = width;
    *outHeight = height;
    hldExternalPage = pageIdx;
    hldExternalBytes = size;
    logInfo("GL: external page %d (%dx%d) direct ms=%.1f\n", pageIdx, width, height, (nowNanos() - start) * 1e-6);
    return 1;
}
"""


def with_fast_texbin(code):
    tail = "    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);\n    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);\n"
    assert tail in code, "unexpected texture tail"
    anchor = "bool VitaTextures_LoadPage(int pageIdx, int* outWidth, int* outHeight) {"
    code = replace_once(code, anchor, FAST + anchor)
    call = "        return loadTextureRecord(vitaTexBinF, pageOffsets[pageIdx], (uint32_t)pageSizes[pageIdx], outWidth, outHeight);"
    code = replace_once(code, call, "        if (hldTexBinFastLoad(pageOffsets[pageIdx], (uint32_t)pageSizes[pageIdx], pageIdx, outWidth, outHeight)) return true;\n" + call)
    import os
    if os.environ.get("HLDC_BOOT_PREFETCH", "1") == "1":
        code = with_boot_prefetch_texbin(code)
    return code


# Boot prefetch: the opening room's pages, the dialogue-bubble pages and the UI pages (ui-pages.txt) were read one
# after another on the main thread during "Loading the opening room" (~10 MB/s card, 0.1-0.4 s each, ~2.8 s in all).
# Right after data.win is parsed they are now queued to a reader thread on another core, which reads each whole
# textures.bin record into a heap buffer while the VM starts and the shaders compile. The page is uploaded exactly as
# before when it is first loaded: a finished read is used as is, one being read is waited for, one not started yet is
# read on the spot as before. Buffers nobody took are freed after the first texture flush's UI-page preload.
PREFETCH = r"""
#include <psp2/kernel/threadmgr.h>
#include "data_win.h"
enum { HLD_TB_IDLE, HLD_TB_QUEUED, HLD_TB_RUNNING, HLD_TB_DONE, HLD_TB_DROPPED };
typedef struct { int state; uint8_t* data; } HldTbJob;
static HldTbJob* hldTbJobs;
static int hldTbQueue[512];
static uint32_t hldTbHead, hldTbTail;
static SceUID hldTbSema = -1;
static unsigned hldTbQueued, hldTbUsed, hldTbWaited, hldTbDropped;
static int hldTbWorker(SceSize argSize, void* argp) {
    (void) argSize; (void) argp;
    const SceUID fd = sceIoOpen("ux0:data/hld-vm/textures.bin", SCE_O_RDONLY, 0);
    for (;;) {
        sceKernelWaitSema(hldTbSema, 1, NULL);
        const uint32_t index = hldTbHead;
        if (index == __atomic_load_n(&hldTbTail, __ATOMIC_ACQUIRE)) continue;
        const int page = hldTbQueue[index % 512];
        __atomic_store_n(&hldTbHead, index + 1, __ATOMIC_RELEASE);
        HldTbJob* job = &hldTbJobs[page];
        int expected = HLD_TB_QUEUED;
        if (!__atomic_compare_exchange_n(&job->state, &expected, HLD_TB_RUNNING, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) continue;
        const uint64_t start = nowNanos();
        const uint32_t size = (uint32_t) pageSizes[page];
        uint8_t* data = fd >= 0 ? (uint8_t*) malloc(size) : NULL;
        uint32_t done = 0;
        while (data != NULL && done < size) {
            // Dropped after boot: stop between chunks, so no read competes with the game's own room loads.
            if (__atomic_load_n(&job->state, __ATOMIC_ACQUIRE) == HLD_TB_DROPPED) { free(data); data = NULL; break; }
            const uint32_t want = size - done > (1u << 20) ? (1u << 20) : size - done;
            const int got = sceIoPread(fd, data + done, want, (SceOff) pageOffsets[page] + done);
            if (got <= 0) { free(data); data = NULL; break; }
            done += (uint32_t) got;
        }
        job->data = data;
        int running = HLD_TB_RUNNING;
        if (!__atomic_compare_exchange_n(&job->state, &running, data != NULL ? HLD_TB_DONE : HLD_TB_IDLE, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            free(data);  // dropped while it was being read
            job->data = NULL;
            __atomic_store_n(&job->state, HLD_TB_IDLE, __ATOMIC_RELEASE);
        }
        logInfo("GL: boot prefetch page %d ok=%d ms=%.1f\n", page, data != NULL, (nowNanos() - start) * 1e-6);
    }
    return 0;
}
static uint32_t hldTbQueuedBytes;
static void hldTbQueuePage(int page) {
    enum { HLD_TB_BUDGET = 32u << 20 };
    if (page < 0 || page >= pageCount || pageSizes[page] < (64 << 10)) return;
    if (hldTbJobs[page].state != HLD_TB_IDLE || hldTbQueuedBytes + (uint32_t) pageSizes[page] > HLD_TB_BUDGET) return;
    if (hldTbTail - __atomic_load_n(&hldTbHead, __ATOMIC_ACQUIRE) >= 511) return;
    hldTbQueuedBytes += (uint32_t) pageSizes[page];
    __atomic_store_n(&hldTbJobs[page].state, HLD_TB_QUEUED, __ATOMIC_RELEASE);
    hldTbQueue[hldTbTail % 512] = page;
    __atomic_store_n(&hldTbTail, hldTbTail + 1, __ATOMIC_RELEASE);
    sceKernelSignalSema(hldTbSema, 1);
    ++hldTbQueued;
}
void hldBootPagePrefetch(DataWin* dw) {
    if (!vitaUsingTexBin || pageCount <= 0 || dw == NULL || dw->gen8.gameID != 742741103 || getenv("HLD_NO_BOOT_PREFETCH") != NULL) return;
    FILE* off = fopen("ux0:data/hld-vm/no-boot-prefetch", "r");
    if (off != NULL) { fclose(off); return; }
    hldTbJobs = (HldTbJob*) calloc((size_t) pageCount, sizeof(HldTbJob));
    hldTbSema = sceKernelCreateSema("HldBootPages", 0, 0, 511, NULL);
    const SceUID thread = sceKernelCreateThread("HldBootPages", hldTbWorker, 191, 0x4000, 0,
                                                SCE_KERNEL_CPU_MASK_USER_1 | SCE_KERNEL_CPU_MASK_USER_2, NULL);
    if (hldTbJobs == NULL || hldTbSema < 0 || thread < 0 || sceKernelStartThread(thread, 0, NULL) < 0) {
        free(hldTbJobs); hldTbJobs = NULL;
        return;
    }
    // 1. The first room's pages, as learned on earlier boots (page-learn.txt).
    const int32_t first = dw->gen8.roomOrderCount > 0 ? dw->gen8.roomOrder[0] : 0;
    const char* firstName = first >= 0 && (uint32_t) first < dw->room.count ? dw->room.rooms[first].name : NULL;
    // Entries are "<room> <page>" pairs; older builds separated them with a literal backslash-n, so both count.
    FILE* learned = firstName != NULL ? fopen("ux0:data/hld-vm/page-learn.txt", "r") : NULL;
    if (learned != NULL) {
        char* text = NULL;
        long length = fseek(learned, 0, SEEK_END) == 0 ? ftell(learned) : -1;
        if (length > 0 && length < (4 << 20) && fseek(learned, 0, SEEK_SET) == 0 && (text = (char*) malloc((size_t) length + 1)) != NULL) {
            length = (long) fread(text, 1, (size_t) length, learned);
            text[length] = '\0';
            for (long i = 0; i + 1 < length; ++i)
                if (text[i] == '\\' && text[i + 1] == 'n') text[i] = text[i + 1] = ' ';
            char* save = NULL;
            for (char* name = strtok_r(text, " \t\r\n", &save); name != NULL; name = strtok_r(NULL, " \t\r\n", &save)) {
                char* number = strtok_r(NULL, " \t\r\n", &save);
                if (number == NULL) break;
                if (strcmp(name, firstName) == 0) hldTbQueuePage(atoi(number));
            }
            free(text);
        }
        fclose(learned);
    }
    // 2. Dialogue-bubble pages (spr_BBL_*), loaded by the first flush's UI-page preload.
    for (uint32_t i = 0; i < dw->sprt.count; ++i) {
        const Sprite* sprite = &dw->sprt.sprites[i];
        if (sprite->name == NULL || strncmp(sprite->name, "spr_BBL", 7) != 0 || sprite->tpagIndices == NULL) continue;
        for (uint32_t f = 0; f < sprite->textureCount; ++f) {
            const int32_t tpag = sprite->tpagIndices[f];
            if (tpag >= 0 && (uint32_t) tpag < dw->tpag.count) hldTbQueuePage(dw->tpag.items[tpag].texturePageId);
        }
    }
    // 3. UI pages (ui-pages.txt), same preload.
    FILE* ui = fopen("ux0:data/hld-vm/ui-pages.txt", "r");
    unsigned page;
    while (ui != NULL && fscanf(ui, "%u", &page) == 1) hldTbQueuePage((int) page);
    if (ui != NULL) fclose(ui);
    logInfo("GL: boot prefetch queued %u pages (%.1f MB)\n", hldTbQueued, hldTbQueuedBytes / 1048576.0);
}
// The page's prefetched record, or NULL: then it is read here as before.
static uint8_t* hldTbTake(int page) {
    if (hldTbJobs == NULL || page < 0 || page >= pageCount) return NULL;
    HldTbJob* job = &hldTbJobs[page];
    int expected = HLD_TB_QUEUED;
    if (__atomic_compare_exchange_n(&job->state, &expected, HLD_TB_IDLE, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return NULL;
    if (expected == HLD_TB_RUNNING) {
        const uint64_t start = nowNanos();
        while (__atomic_load_n(&job->state, __ATOMIC_ACQUIRE) == HLD_TB_RUNNING) sceKernelDelayThread(200);
        ++hldTbWaited;
        logInfo("GL: waited %.1f ms for boot prefetch page %d\n", (nowNanos() - start) * 1e-6, page);
        expected = __atomic_load_n(&job->state, __ATOMIC_ACQUIRE);
    }
    if (expected != HLD_TB_DONE) return NULL;
    uint8_t* data = job->data;
    job->data = NULL;
    __atomic_store_n(&job->state, HLD_TB_IDLE, __ATOMIC_RELEASE);
    ++hldTbUsed;
    return data;
}
// After the first flush's UI-page preload: free what nobody took (and stop what is still queued).
void hldBootPagePrefetchDrop(void) {
    if (hldTbJobs == NULL) return;
    static bool done;
    if (done) return;
    done = true;
    for (int page = 0; page < pageCount; ++page) {
        HldTbJob* job = &hldTbJobs[page];
        int expected = HLD_TB_QUEUED;
        if (__atomic_compare_exchange_n(&job->state, &expected, HLD_TB_IDLE, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) { ++hldTbDropped; continue; }
        if (expected == HLD_TB_DONE) {
            free(job->data);
            job->data = NULL;
            __atomic_store_n(&job->state, HLD_TB_IDLE, __ATOMIC_RELEASE);
            ++hldTbDropped;
        } else if (expected == HLD_TB_RUNNING &&
                   __atomic_compare_exchange_n(&job->state, &expected, HLD_TB_DROPPED, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            ++hldTbDropped;
        }
    }
    logInfo("GL: boot prefetch queued=%u used=%u waited=%u dropped=%u\n", hldTbQueued, hldTbUsed, hldTbWaited, hldTbDropped);
}
"""


def with_boot_prefetch_texbin(code):
    anchor = "static int hldTexBinFastLoad(long offset, uint32_t recordSize, int pageIdx, int* outWidth, int* outHeight) {"
    code = replace_once(code, anchor, PREFETCH + anchor)
    start = code.index(anchor)
    end = code.index("\n}\n", start)
    body = code[start:end]
    edits = [
        ("    const uint64_t start = nowNanos();\n",
         "    const uint64_t start = nowNanos();\n    uint8_t* pre = hldTbTake(pageIdx);\n"),
        ("    if (sceIoPread(hldTexBinFd, header, pvrHeaderSize, offset) != pvrHeaderSize || header[0] != 0x03525650u) return 0;",
         "    if (pre != NULL) memcpy(header, pre, pvrHeaderSize);\n"
         "    if ((pre == NULL && sceIoPread(hldTexBinFd, header, pvrHeaderSize, offset) != pvrHeaderSize) || header[0] != 0x03525650u) { free(pre); return 0; }"),
        ("    if (format != 0x0B || (metadata != 4 && metadata != 0) || width <= 0 || height <= 0) return 0;",
         "    if (format != 0x0B || (metadata != 4 && metadata != 0) || width <= 0 || height <= 0) { free(pre); return 0; }"),
        ("    if (metadata == 0) {\n",
         "    if (metadata == 0 && pre != NULL) {\n"
         "        glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_S3TC_DXT5_EXT, width, height, 0, size, pre + pvrHeaderSize);\n"
         "        free(pre);\n"
         "    } else if (metadata == 0) {\n"),
        ("    if (!tex_data) return 0;\n",
         "    if (!tex_data) { free(pre); return 0; }\n"
         "    const bool fromPre = pre != NULL;\n"
         "    if (fromPre) { memcpy(tex_data, pre + pvrHeaderSize + metadata, size); free(pre); }\n"),
        ("    uint32_t left = size;\n    long position = offset + pvrHeaderSize + metadata;\n",
         "    uint32_t left = fromPre ? 0 : size;\n    long position = offset + pvrHeaderSize + metadata;\n"),
    ]
    for old, new in edits:
        assert body.count(old) == 1, old
        body = body.replace(old, new)
    return code[:start] + body + code[end:]
