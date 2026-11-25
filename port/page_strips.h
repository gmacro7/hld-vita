// Texture page strips: each data.win texture page pre-decoded to RGBA and stored as LZ4 blocks of whole rows
// (pagez/pNNN.hlz), so a page decodes on several cores at once. Built by make-page-strips.py; the bytes equal
// the PNG decoder's output (checked by verify-page-strips).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

// File layout (little endian):
//   "HLZ1" or "HLZ2" u32 width u32 height u32 stripCount
//   ("HLZ2": each row is stored as byte differences from the pixel to its left, ~25% smaller; hlzUnfilter undoes it;
//    "HLZ3": per strip, filtered when HLZ_FILTERED is set in rowCount)
//   stripCount x { u32 firstRow u32 rowCount u32 compressedBytes }
//   the compressed strips, in order
enum { HLZ_MAX_STRIPS = 64 };
#define HLZ_FILTERED 0x80000000u

typedef struct {
    uint32_t firstRow, rowCount, compressedBytes;
} HlzStrip;

// LZ4 block decompression (no frame, no size prefix). True only if the input decodes to exactly dstLen bytes.
static inline bool hlzDecompress(const uint8_t* src, size_t srcLen, uint8_t* dst, size_t dstLen) {
    const uint8_t* ip = src;
    const uint8_t* const iend = src + srcLen;
    uint8_t* op = dst;
    uint8_t* const oend = dst + dstLen;
    while (ip < iend) {
        const unsigned token = *ip++;
        size_t literals = token >> 4;
        if (literals == 15) {
            unsigned byte;
            do {
                if (ip >= iend) return false;
                byte = *ip++;
                literals += byte;
            } while (byte == 255);
        }
        if (literals > (size_t) (iend - ip) || literals > (size_t) (oend - op)) return false;
        for (size_t i = 0; i < literals; ++i) op[i] = ip[i];
        ip += literals;
        op += literals;
        if (ip == iend) break;  // the last sequence has literals only
        if (iend - ip < 2) return false;
        const size_t offset = (size_t) ip[0] | ((size_t) ip[1] << 8);
        ip += 2;
        if (offset == 0 || offset > (size_t) (op - dst)) return false;
        size_t match = (token & 15u) + 4;
        if ((token & 15u) == 15) {
            unsigned byte;
            do {
                if (ip >= iend) return false;
                byte = *ip++;
                match += byte;
            } while (byte == 255);
        }
        if (match > (size_t) (oend - op)) return false;
        const uint8_t* from = op - offset;
        if (offset >= 8 && match >= 8) {
            // Non-overlapping 8-byte chunks (an offset of at least 8 keeps every chunk's source behind it).
            size_t i = 0;
            for (; i + 8 <= match; i += 8) { uint64_t v; __builtin_memcpy(&v, from + i, 8); __builtin_memcpy(op + i, &v, 8); }
            for (; i < match; ++i) op[i] = from[i];
        } else {
            for (size_t i = 0; i < match; ++i) op[i] = from[i];
        }
        op += match;
    }
    return op == oend;
}

// HLZ2 rows: every byte after the first pixel holds (byte - same channel of the previous pixel) mod 256.
// Four channels are added at once (SWAR, no carries between bytes); the running sum makes each row serial.
static inline void hlzUnfilter(uint8_t* rows, size_t rowCount, size_t rowBytes) {
    for (size_t r = 0; r < rowCount; ++r) {
        uint8_t* row = rows + r * rowBytes;
        uint32_t previous;
        __builtin_memcpy(&previous, row, 4);
        for (size_t i = 4; i + 4 <= rowBytes; i += 4) {
            uint32_t delta;
            __builtin_memcpy(&delta, row + i, 4);
            const uint32_t sum = ((previous & 0x7f7f7f7fu) + (delta & 0x7f7f7f7fu)) ^ ((previous ^ delta) & 0x80808080u);
            __builtin_memcpy(row + i, &sum, 4);
            previous = sum;
        }
    }
}
