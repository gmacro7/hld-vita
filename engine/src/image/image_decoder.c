#include "image_decoder.h"

#include "stdio_compat.h"
#include <stdlib.h>
#include "string_compat.h"
#include <bzlib.h>
#define MINIZ_NO_ZLIB_COMPATIBLE_NAMES
#include <miniz.h>
#include <limits.h>

#include "log.h"
#include "utils.h"
#ifdef PLATFORM_VITA
#include <vitaGL.h>
#endif

#ifdef USE_WUFFS_IMGDEC
#define WUFFS_IMPLEMENTATION
#define WUFFS_CONFIG__MODULES
#define WUFFS_CONFIG__MODULE__BASE
#define WUFFS_CONFIG__MODULE__CRC32
#define WUFFS_CONFIG__MODULE__ADLER32
#define WUFFS_CONFIG__MODULE__DEFLATE
#define WUFFS_CONFIG__MODULE__ZLIB
#define WUFFS_CONFIG__MODULE__PNG
#define WUFFS_CONFIG__MODULE__BZIP2
#include "wuffs-v0.4.c"
#define WUFFS_STACK_WORKBUF_SIZE 16384
#elif defined(USE_STB_IMGDEC)
#include "stb_image.h"
#endif

#define QOI_HEADER_SIZE 12
#define COMPRESSED_QOI_HEADER_SIZE_OLD 8
#define COMPRESSED_QOI_HEADER_SIZE_NEW 12
#if (defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)
    #define PACK_RGBA(r, g, b, a) (((uint32_t)(r) << 24) | ((uint32_t)(g) << 16) | ((uint32_t)(b) << 8) | (uint32_t)(a))
    #define UNPACK_R(color) (((color) >> 24) & 0xFF)
    #define UNPACK_G(color) (((color) >> 16) & 0xFF)
    #define UNPACK_B(color) (((color) >> 8)  & 0xFF)
    #define UNPACK_A(color) ((color)         & 0xFF)
#else
    #define PACK_RGBA(r, g, b, a) (((uint32_t)(a) << 24) | ((uint32_t)(b) << 16) | ((uint32_t)(g) << 8) | (uint32_t)(r))
    #define UNPACK_R(color) ((color)         & 0xFF)
    #define UNPACK_G(color) (((color) >> 8)  & 0xFF)
    #define UNPACK_B(color) (((color) >> 16) & 0xFF)
    #define UNPACK_A(color) (((color) >> 24) & 0xFF)
#endif

// Sign-extend the low "bits" bits of "val" to an 8-bit two's-complement value.
static inline uint8_t signExtend(uint32_t val, int bits) {
    uint32_t mask = 1U << (bits - 1);
    return (uint8_t)((val ^ mask) - mask);
}

// Decodes GameMaker's custom QOI format (ported from UndertaleModTool's QoiConverter).
static uint8_t* decodeQoi(const uint8_t* restrict data, size_t dataSize, int* restrict outW, int* restrict outH) {
    if (QOI_HEADER_SIZE > dataSize) return nullptr;
    if (data[0] != 'f' || data[1] != 'i' || data[2] != 'o' || data[3] != 'q') return nullptr;

    int width = data[4] | (data[5] << 8);
    int height = data[6] | (data[7] << 8);
    uint32_t length = (uint32_t)data[8] | ((uint32_t)data[9] << 8) | ((uint32_t)data[10] << 16) | ((uint32_t)data[11] << 24);

    if (QOI_HEADER_SIZE + (size_t) length > dataSize) return nullptr;
    if (0 >= width || 0 >= height) return nullptr;

    const uint8_t* restrict pixelData = data + QOI_HEADER_SIZE;
    const uint8_t* restrict pixelDataEnd = pixelData + length;
    size_t rawSize = (size_t)width * (size_t)height;
    uint32_t* restrict raw = (uint32_t*)safeMalloc(rawSize * sizeof(uint32_t));
    if (!raw) return nullptr;

    const uint32_t* rawStart = raw;
    const uint32_t* rawEnd = raw + rawSize;
    uint32_t index[64] = {0};

    uint8_t r = 0, g = 0, b = 0, a = 255;
    uint32_t packedCol = PACK_RGBA(r, g, b, a);

    while (raw < rawEnd && pixelData < pixelDataEnd) {
        uint8_t b1 = *pixelData++;

        if ((b1 & 0xC0) == 0x00) {
            // QOI_INDEX
            packedCol = index[b1 & 0x3F];
            r = UNPACK_R(packedCol);
            g = UNPACK_G(packedCol);
            b = UNPACK_B(packedCol);
            a = UNPACK_A(packedCol);
            *raw++ = packedCol;
        } else if ((b1 & 0xE0) == 0x40) {
            // QOI_RUN_8
            size_t run = (b1 & 0x1F) + 1;
            size_t remaining = rawEnd - raw;
            if (run > remaining) run = remaining;

            const uint32_t* end = raw + run;
            while (raw < end) {
                *raw++ = packedCol;
            }
        } else if ((b1 & 0xE0) == 0x60) {
            // QOI_RUN_16
            if (pixelData >= pixelDataEnd) { free((void*)rawStart); return nullptr; }
            uint8_t b2 = *pixelData++;
            size_t run = (((b1 & 0x1F) << 8) | b2) + 33;
            size_t remaining = rawEnd - raw;
            if (run > remaining) run = remaining;

            const uint32_t* end = raw + run;
            while (raw < end) {
                *raw++ = packedCol;
            }
        } else if ((b1 & 0xC0) == 0x80) {
            // QOI_DIFF_8 (2-2-2 signed deltas on r,g,b)
            r += signExtend((b1 >> 4) & 3, 2);
            g += signExtend((b1 >> 2) & 3, 2);
            b += signExtend(b1 & 3, 2);
            packedCol = PACK_RGBA(r, g, b, a);
            index[(r ^ g ^ b ^ a) & 63] = packedCol;
            *raw++ = packedCol;
        } else if ((b1 & 0xE0) == 0xC0) {
            // QOI_DIFF_16 (5-4-4 signed deltas on r,g,b)
            if (pixelData >= pixelDataEnd) { free((void*)rawStart); return nullptr; }
            uint8_t b2 = *pixelData++;
            uint32_t merged = ((uint32_t)b1 << 8) | b2;
            r += signExtend((merged >> 8) & 0x1F, 5);
            g += signExtend((merged >> 4) & 0x0F, 4);
            b += signExtend(merged & 0x0F, 4);
            packedCol = PACK_RGBA(r, g, b, a);
            index[(r ^ g ^ b ^ a) & 63] = packedCol;
            *raw++ = packedCol;
        } else if ((b1 & 0xF0) == 0xE0) {
            // QOI_DIFF_24 (5-5-5-5 signed deltas on r,g,b,a)
            if (pixelData + 1 >= pixelDataEnd) { free((void*)rawStart); return nullptr; }
            uint8_t b2 = *pixelData++;
            uint8_t b3 = *pixelData++;
            uint32_t merged = ((uint32_t)b1 << 16) | ((uint32_t)b2 << 8) | b3;
            r += signExtend((merged >> 15) & 0x1F, 5);
            g += signExtend((merged >> 10) & 0x1F, 5);
            b += signExtend((merged >> 5) & 0x1F, 5);
            a += signExtend(merged & 0x1F, 5);
            packedCol = PACK_RGBA(r, g, b, a);
            index[(r ^ g ^ b ^ a) & 63] = packedCol;
            *raw++ = packedCol;
        } else if ((b1 & 0xF0) == 0xF0) {
            // QOI_COLOR (per-channel raw bytes, only those with set bit flag)
            if (b1 & 8) { if (pixelData >= pixelDataEnd) { free((void*)rawStart); return nullptr; } r = *pixelData++; }
            if (b1 & 4) { if (pixelData >= pixelDataEnd) { free((void*)rawStart); return nullptr; } g = *pixelData++; }
            if (b1 & 2) { if (pixelData >= pixelDataEnd) { free((void*)rawStart); return nullptr; } b = *pixelData++; }
            if (b1 & 1) { if (pixelData >= pixelDataEnd) { free((void*)rawStart); return nullptr; } a = *pixelData++; }            
            packedCol = PACK_RGBA(r, g, b, a);
            index[(r ^ g ^ b ^ a) & 63] = packedCol;
            *raw++ = packedCol;
        }
    }

    *outW = width;
    *outH = height;
    return (uint8_t*)rawStart;
}

// Decodes BZip2-compressed custom QOI. The blob layout is:
//   bytes 0..3 = "2zoq" magic
//   bytes 4..5 = width (LE uint16)
//   bytes 6..7 = height (LE uint16)
//   bytes 8..11 = uncompressed BZ2 length (LE uint32) -- ONLY when gm2022_5 is true
//   bytes 8.. (or 12.. if gm2022_5) = raw BZip2 stream, which decompresses into a full "fioq" QOI file.
static uint8_t* decodeBz2Qoi(const uint8_t* blob, size_t blobSize, bool gm2022_5, int* outW, int* outH) {
    size_t headerSize = gm2022_5 ? COMPRESSED_QOI_HEADER_SIZE_NEW : COMPRESSED_QOI_HEADER_SIZE_OLD;
    if (headerSize > blobSize) return nullptr;

    int width = blob[4] | (blob[5] << 8);
    int height = blob[6] | (blob[7] << 8);
    if (0 >= width || 0 >= height) return nullptr;

    // Upper bound on decompressed QOI: header size + width*height*5 pixel data.
    size_t uncompressedCapacity = QOI_HEADER_SIZE + (size_t) width * (size_t) height * 5;
    uint8_t* uncompressed = (uint8_t*) malloc(uncompressedCapacity);
    if (!uncompressed) return nullptr;
#ifdef USE_WUFFS_IMGDEC
    // The bzip2 decoder struct is ~4 MB (it embeds the whole 900k block buffer), so it MUST live on the heap:
    // on threads with small stacks (Android's ~1 MB GL executor thread) a stack-allocated decoder overflows.
    size_t decSize = sizeof__wuffs_bzip2__decoder();
    wuffs_bzip2__decoder* dec = (wuffs_bzip2__decoder*) malloc(decSize);
    if (!dec) {
        free(uncompressed);
        return nullptr;
    }
    wuffs_base__status status = wuffs_bzip2__decoder__initialize(
        dec, decSize, WUFFS_VERSION,
        WUFFS_INITIALIZE__LEAVE_INTERNAL_BUFFERS_UNINITIALIZED
    );
    if (!wuffs_base__status__is_ok(&status)) {
        free(dec);
        free(uncompressed);
        return nullptr;
    }

    wuffs_bzip2__decoder__set_quirk(dec, WUFFS_BASE__QUIRK_IGNORE_CHECKSUM, 1);

    wuffs_base__io_buffer dstBuf = wuffs_base__ptr_u8__writer(uncompressed, uncompressedCapacity);
    wuffs_base__io_buffer srcBuf = wuffs_base__ptr_u8__reader((uint8_t*)(blob + headerSize), blobSize - headerSize, true);

    status = wuffs_bzip2__decoder__transform_io(dec, &dstBuf, &srcBuf, wuffs_base__empty_slice_u8());
    free(dec);

    if (wuffs_base__status__is_ok(&status)) {
        uint8_t* result = decodeQoi(uncompressed, dstBuf.meta.wi, outW, outH);
        free(uncompressed);
        return result;
    }

    logWarn("ImageDecoder: wuffs BZ2 decompress failed (%s), trying libbzip2 fallback\n", status.repr ? status.repr : "unknown");
#endif 
    
    int rc = BZ2_bzBuffToBuffDecompress((char*) uncompressed, (unsigned int*)&uncompressedCapacity, (char*)(blob + headerSize), (unsigned int)(blobSize - headerSize), 0, 0);
    if (rc != BZ_OK) {
        logWarn("ImageDecoder: libbzip2 decompress failed (rc=%d)\n", rc);
        free(uncompressed);
        return nullptr;
    }

    uint8_t* result = decodeQoi(uncompressed, uncompressedCapacity, outW, outH);
    free(uncompressed);
    return result;
}

static uint32_t pngReadU32(const uint8_t* p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

bool ImageDecoder_getRgba8PngSize(const uint8_t* blob, size_t size, int* width, int* height) {
    static const uint8_t header[] = {
        137, 'P', 'N', 'G', 13, 10, 26, 10, 0, 0, 0, 13, 'I', 'H', 'D', 'R'
    };
    static const uint8_t rgba8[] = {8, 6, 0, 0, 0};
    if (blob == nullptr || size < 33 || memcmp(blob, header, sizeof(header)) != 0 ||
        memcmp(blob + 24, rgba8, sizeof(rgba8)) != 0) return false;
    uint32_t w = pngReadU32(blob + 16);
    uint32_t h = pngReadU32(blob + 20);
    if (w == 0 || h == 0 || w > INT_MAX || h > INT_MAX) return false;
    *width = (int)w;
    *height = (int)h;
    return true;
}

static uint8_t pngPaeth(int left, int up, int upperLeft) {
    int leftDistance = abs(up - upperLeft);
    int upDistance = abs(left - upperLeft);
    int upperLeftDistance = abs(left + up - 2 * upperLeft);
    if (leftDistance <= upDistance && leftDistance <= upperLeftDistance) return (uint8_t)left;
    return (uint8_t)(upDistance <= upperLeftDistance ? up : upperLeft);
}

static bool pngUnfilterRow(uint8_t* row, const uint8_t* previous, size_t bytes, uint8_t filter) {
    switch (filter) {
        case 0:
            return true;
        case 1:
            for (size_t i = 4; i < bytes; i++) row[i] += row[i - 4];
            return true;
        case 2:
            for (size_t i = 0; i < bytes; i++) row[i] += previous[i];
            return true;
        case 3:
            for (size_t i = 0; i < 4; i++) row[i] += previous[i] / 2;
            for (size_t i = 4; i < bytes; i++) row[i] += (row[i - 4] + previous[i]) / 2;
            return true;
        case 4:
            for (size_t i = 0; i < 4; i++) row[i] += previous[i];
            for (size_t i = 4; i < bytes; i++) row[i] += pngPaeth(row[i - 4], previous[i], previous[i - 4]);
            return true;
        default:
            return false;
    }
}

bool ImageDecoder_readRgba8Png(const uint8_t* blob, size_t size, uint8_t* output, size_t stride) {
    int width, height;
    if (output == nullptr || !ImageDecoder_getRgba8PngSize(blob, size, &width, &height) ||
        (size_t)width > (SIZE_MAX / 2 - 1) / 4 || (unsigned)width > (UINT_MAX - 1) / 4) return false;
    size_t rowBytes = (size_t)width * 4;
    if (stride < rowBytes || (size_t)height > SIZE_MAX / stride) return false;
    unsigned scanBytes = (unsigned)(rowBytes + 1);
    uint8_t* rows = calloc(2, scanBytes);
    if (rows == nullptr) return false;

    mz_stream stream = {0};
    if (mz_inflateInit(&stream) != MZ_OK) {
        free(rows);
        return false;
    }

    uint8_t* scan = rows;
    uint8_t* previous = rows + scanBytes;
    stream.next_out = scan;
    stream.avail_out = scanBytes;
    int line = 0, status = MZ_OK;
    bool complete = false;

    for (size_t pos = 33; size - pos >= 12;) {
        uint32_t chunkBytes = pngReadU32(blob + pos);
        if (chunkBytes > size - pos - 12) break;
        const uint8_t* tag = blob + pos + 4;
        pos += (size_t)chunkBytes + 12;

        if (memcmp(tag, "IEND", 4) == 0) {
            complete = chunkBytes == 0 && status == MZ_STREAM_END && line == height;
            break;
        }
        if (memcmp(tag, "IDAT", 4) != 0 || chunkBytes == 0) continue;
        if (status == MZ_STREAM_END) break;
        stream.next_in = tag + 4;
        stream.avail_in = chunkBytes;

        // Inflate can retain output after consuming an IDAT. Drain full rows
        // before advancing to the next chunk, including the end of the stream.
        for (;;) {
            unsigned beforeIn = stream.avail_in;
            unsigned beforeOut = stream.avail_out;
            status = mz_inflate(&stream, MZ_NO_FLUSH);
            if (status != MZ_OK && status != MZ_STREAM_END && status != MZ_BUF_ERROR) goto cleanup;
            bool full = stream.avail_out == 0;
            if (full) {
                if (line == height || !pngUnfilterRow(scan + 1, previous + 1, rowBytes, scan[0])) goto cleanup;
                memcpy(output + (size_t)line++ * stride, scan + 1, rowBytes);
                uint8_t* swap = previous;
                previous = scan;
                scan = swap;
                stream.next_out = scan;
                stream.avail_out = line == height ? 1 : scanBytes;
            }
            if (status == MZ_STREAM_END) {
                if (stream.avail_in != 0) goto cleanup;
                break;
            }
            if (!full && beforeIn == stream.avail_in && beforeOut == stream.avail_out) {
                if (stream.avail_in != 0) goto cleanup;
                break;
            }
        }
    }

cleanup:
    mz_inflateEnd(&stream);
    free(rows);
    return complete;
}

uint8_t* ImageDecoder_decodeToRgba(const uint8_t* blob, size_t blobSize, bool gm2022_5, int* outW, int* outH) {
    if (4 > blobSize || !blob) return nullptr;

    // GameMaker custom QOI ("fioq")
    if (blob[0] == 'f' && blob[1] == 'i' && blob[2] == 'o' && blob[3] == 'q') {
        return decodeQoi(blob, blobSize, outW, outH);
    }

    // BZip2 + GameMaker custom QOI ("2zoq")
    if (blob[0] == '2' && blob[1] == 'z' && blob[2] == 'o' && blob[3] == 'q') {
        return decodeBz2Qoi(blob, blobSize, gm2022_5, outW, outH);
    }

#ifdef USE_WUFFS_IMGDEC
    wuffs_png__decoder dec;
    wuffs_base__status status = wuffs_png__decoder__initialize(
        &dec, sizeof(dec), WUFFS_VERSION,
        WUFFS_INITIALIZE__LEAVE_INTERNAL_BUFFERS_UNINITIALIZED
    );
    if (!wuffs_base__status__is_ok(&status)) return nullptr;

    wuffs_base__image_decoder* imgDec = wuffs_png__decoder__upcast_as__wuffs_base__image_decoder(&dec);

    wuffs_base__image_decoder__set_quirk(imgDec, WUFFS_BASE__QUIRK_IGNORE_CHECKSUM, 1);

    wuffs_base__io_buffer src = wuffs_base__ptr_u8__reader((uint8_t*)blob, blobSize, true);

    wuffs_base__image_config ic;
    status = wuffs_base__image_decoder__decode_image_config(imgDec, &ic, &src);
    if (!wuffs_base__status__is_ok(&status)) return nullptr;

    uint32_t w = wuffs_base__pixel_config__width(&ic.pixcfg);
    uint32_t h = wuffs_base__pixel_config__height(&ic.pixcfg);

    wuffs_base__pixel_config__set(
        &ic.pixcfg,
        WUFFS_BASE__PIXEL_FORMAT__RGBA_NONPREMUL,
        WUFFS_BASE__PIXEL_SUBSAMPLING__NONE,
        w, h
    );

    if (w == 0 || h == 0 || (size_t)w > SIZE_MAX / h / 4) return nullptr;
    size_t pixBufSize = (size_t)w * (size_t)h * 4;
    uint8_t* pixels = (uint8_t*)malloc(pixBufSize);
    if (!pixels) return nullptr;

    wuffs_base__pixel_buffer pb;
    status = wuffs_base__pixel_buffer__set_from_slice(&pb, &ic.pixcfg, wuffs_base__make_slice_u8(pixels, pixBufSize));
    if (!wuffs_base__status__is_ok(&status)) {
        free(pixels);
        return nullptr;
    }

    wuffs_base__range_ii_u64 workbufLen = wuffs_base__image_decoder__workbuf_len(imgDec);
    wuffs_base__slice_u8 workbuf = wuffs_base__empty_slice_u8();
    uint8_t stackWork[WUFFS_STACK_WORKBUF_SIZE];
    uint8_t* heapWork = nullptr;
#ifdef PLATFORM_VITA
    bool workInVitaPool = false;
#endif

    if (workbufLen.min_incl > 0) {
        if (workbufLen.min_incl <= sizeof(stackWork)) {
            workbuf = wuffs_base__make_slice_u8(stackWork, (size_t)workbufLen.min_incl);
        } else {
            if (workbufLen.min_incl <= SIZE_MAX) heapWork = (uint8_t*)malloc((size_t)workbufLen.min_incl);
#ifdef PLATFORM_VITA
            if (!heapWork && workbufLen.min_incl <= UINT32_MAX) {
                heapWork = (uint8_t*)vglMalloc((uint32_t)workbufLen.min_incl);
                workInVitaPool = heapWork != nullptr;
            }
#endif
            if (!heapWork) {
                free(pixels);
                return nullptr;
            }
            workbuf = wuffs_base__make_slice_u8(heapWork, (size_t)workbufLen.min_incl);
        }
    }

    status = wuffs_base__image_decoder__decode_frame(imgDec, &pb, &src, WUFFS_BASE__PIXEL_BLEND__SRC, workbuf, nullptr);
#ifdef PLATFORM_VITA
    if (workInVitaPool) vglFree(heapWork);
    else
#endif
    free(heapWork);

    if (!wuffs_base__status__is_ok(&status)) {
        free(pixels);
        return nullptr;
    }
#elif defined(USE_STB_IMGDEC)
    int w, h, channels;
    uint8_t* pixels = stbi_load_from_memory(blob, (int) blobSize, &w, &h, &channels, 4);
    if (!pixels) return nullptr;
#endif
    *outW = (int)w;
    *outH = (int)h;
    return pixels;
}
