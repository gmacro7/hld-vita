// For bounded unit-scale coordinates on an integer or half-integer grid,
// each intermediate in the original transform is exact even in binary32.
static inline bool Collision_clipGridMaskAxis(CollisionMaskAxis axis, GMLReal sampleOffset,
                                              int32_t* first, int32_t* stop, int64_t* bias) {
    const int32_t limit = 1 << 19;
    if (axis.scale != 1.0 || !(axis.position >= -limit && axis.position <= limit) ||
        axis.origin < -limit || axis.origin > limit) return false;

    const GMLReal doubled = 2 * axis.position;
    if ((GMLReal)(int32_t)doubled != doubled) return false;
    const int32_t shift = (int32_t)(2 * sampleOffset) - (int32_t)doubled + 2 * axis.origin;
    const int32_t floorShift = (shift - (shift % 2 != 0)) / 2;
    const int64_t lower = (int64_t)axis.first - floorShift;
    const int64_t upper = (int64_t)axis.last - floorShift + 1;

    // Truncation also maps -0.5 to zero. That row or column would repeat
    // the first bit, so let the general scan handle it if it is in range.
    if (axis.first == 0 && shift % 2 != 0 && *first < lower && *stop >= lower)
        return false;

    if (lower > *first) *first = lower < *stop ? (int32_t)lower : *stop;
    if (upper < *stop) *stop = (int32_t)upper;
    *bias = (int64_t)floorShift - axis.maskOffset;
    return true;
}

static inline uint32_t Collision_maskWord(const uint8_t* row, uint32_t column) {
    const uint32_t shift = column % 8;
    const uint8_t* bytes = row + column / 8;
    uint32_t word;
    __builtin_memcpy(&word, bytes, sizeof(word));
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    word = __builtin_bswap32(word);
#endif
    return shift == 0 ? word : (word << shift) | (bytes[4] >> (8 - shift));
}

static inline bool Collision_tryGridMaskOverlap(const CollisionPointQuery* a, const CollisionPointQuery* b,
                                                int32_t startX, int32_t startY, int32_t endX, int32_t endY,
                                                bool compat, GMLReal sampleOffset, bool* result) {
    const int32_t limit = 1 << 19;
    if (startX < -limit || startY < -limit || endX > limit || endY > limit ||
        startX > endX || startY > endY || !(sampleOffset == 0.0 || sampleOffset == 0.5))
        return false;

    int32_t x = startX, y = startY, stopX = endX + compat, stopY = endY + compat;
    int64_t xA, xB, yA, yB;
    if (!Collision_clipGridMaskAxis(Collision_maskAxis(a, true), sampleOffset, &x, &stopX, &xA) ||
        !Collision_clipGridMaskAxis(Collision_maskAxis(b, true), sampleOffset, &x, &stopX, &xB) ||
        !Collision_clipGridMaskAxis(Collision_maskAxis(a, false), sampleOffset, &y, &stopY, &yA) ||
        !Collision_clipGridMaskAxis(Collision_maskAxis(b, false), sampleOffset, &y, &stopY, &yB))
        return false;

    *result = false;
    if (x >= stopX || y >= stopY) return true;
    if (a->mask == nullptr && b->mask == nullptr) {
        *result = true;
        return true;
    }
    const uint32_t width = (uint32_t)(stopX - x);
    const uint32_t firstA = a->mask != nullptr ? (uint32_t)((int64_t)x + xA) : 0;
    const uint32_t firstB = b->mask != nullptr ? (uint32_t)((int64_t)x + xB) : 0;
    for (; y < stopY; ++y) {
        const uint8_t* rowA = a->mask != nullptr ? a->mask + (size_t)((int64_t)y + yA) * a->stride : nullptr;
        const uint8_t* rowB = b->mask != nullptr ? b->mask + (size_t)((int64_t)y + yB) * b->stride : nullptr;
        uint32_t col = 0;
        for (; col + 32 <= width; col += 32) {
            const uint32_t bitsA = rowA != nullptr ? Collision_maskWord(rowA, firstA + col) : UINT32_MAX;
            const uint32_t bitsB = rowB != nullptr ? Collision_maskWord(rowB, firstB + col) : UINT32_MAX;
            if (bitsA & bitsB) {
                *result = true;
                return true;
            }
        }
        for (; col + 8 <= width; col += 8) {
            const uint8_t bitsA = rowA != nullptr ? Collision_maskByte(rowA, firstA + col) : UINT8_MAX;
            const uint8_t bitsB = rowB != nullptr ? Collision_maskByte(rowB, firstB + col) : UINT8_MAX;
            if (bitsA & bitsB) {
                *result = true;
                return true;
            }
        }
        for (; col < width; ++col) {
            const uint32_t bitA = firstA + col, bitB = firstB + col;
            if ((rowA == nullptr || (rowA[bitA / 8] & (0x80u >> (bitA % 8)))) &&
                (rowB == nullptr || (rowB[bitB / 8] & (0x80u >> (bitB % 8))))) {
                *result = true;
                return true;
            }
        }
    }
    return true;
}
