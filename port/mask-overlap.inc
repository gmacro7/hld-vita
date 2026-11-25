typedef struct {
    GMLReal position, scale;
    int32_t origin, first, last, maskOffset;
} CollisionMaskAxis;

static inline CollisionMaskAxis Collision_maskAxis(const CollisionPointQuery* query, bool horizontal) {
    const Sprite* sprite = query->sprite;
    CollisionMaskAxis axis = {
        .position = horizontal ? query->x : query->y,
        .scale = horizontal ? query->scaleX : query->scaleY,
        .origin = horizontal ? sprite->originX : sprite->originY,
        .first = 0,
        .last = (int32_t)(horizontal ? sprite->width : sprite->height) - 1,
    };
    if (sprite->bboxMode != 1) {
        const int32_t first = horizontal ? sprite->marginLeft : sprite->marginTop;
        const int32_t last = horizontal ? sprite->marginRight : sprite->marginBottom;
        if (first > axis.first) axis.first = first;
        if (last < axis.last) axis.last = last;
    }
    if (query->mask != nullptr) {
        axis.maskOffset = horizontal ? sprite->maskOffsetX : sprite->maskOffsetY;
        const int64_t count = (int32_t)(horizontal ? sprite->maskWidth : sprite->maskHeight);
        const int64_t last = (int64_t)axis.maskOffset + count - 1;
        if (axis.maskOffset > axis.first) axis.first = axis.maskOffset;
        if (last < axis.first) axis.last = -1;
        else if (last < axis.last) axis.last = (int32_t)last;
    }
    return axis;
}

static inline int32_t Collision_maskCoordinate(CollisionMaskAxis axis, GMLReal world) {
    const GMLReal delta = world - axis.position;
    const GMLReal local = (axis.scale == 1.0 ? delta : delta / axis.scale) + (GMLReal)axis.origin;
    // Keep truncation toward zero, including the interval (-1, 0).
    if (!(local > -1.0 && (double)local < 2147483648.0)) return -1;
    const int32_t pixel = (int32_t)local;
    return pixel >= axis.first && pixel <= axis.last ? pixel - axis.maskOffset : -1;
}

typedef struct {
    uint32_t byteA, byteB;
    uint8_t bitA, bitB;
} CollisionMaskColumn;

static inline uint8_t Collision_maskByte(const uint8_t* row, uint32_t column) {
    const uint32_t byte = column / 8, shift = column % 8;
    if (shift == 0) return row[byte];
    return (uint8_t)((row[byte] << shift) | (row[byte + 1] >> (8 - shift)));
}

static inline bool Collision_axisMaskOverlap(const CollisionPointQuery* a, const CollisionPointQuery* b,
                                             int32_t startX, int32_t startY, int32_t endX, int32_t endY,
                                             bool compat, GMLReal sampleOffset) {
    const CollisionMaskAxis ax = Collision_maskAxis(a, true), bx = Collision_maskAxis(b, true);
    const CollisionMaskAxis ay = Collision_maskAxis(a, false), by = Collision_maskAxis(b, false);
    const int64_t stopX = (int64_t)endX + compat;
    const int64_t stopY = (int64_t)endY + compat;
    const uint8_t rectangle = 0xff;
    CollisionMaskColumn columns[128];

    for (int64_t first = startX; first < stopX;) {
        const int64_t blockEnd = first + 128 < stopX ? first + 128 : stopX;
        unsigned count = 0;
        uint32_t firstA = 0, firstB = 0;
        bool packed = true;
        for (; first < blockEnd; ++first) {
            const GMLReal x = (GMLReal)first + sampleOffset;
            const int32_t xA = Collision_maskCoordinate(ax, x);
            if (xA < 0) continue;
            const int32_t xB = Collision_maskCoordinate(bx, x);
            if (xB < 0) continue;
            if (count == 0) {
                firstA = (uint32_t)xA;
                firstB = (uint32_t)xB;
            }
            packed &= (a->mask == nullptr || (uint32_t)xA == firstA + count) &&
                      (b->mask == nullptr || (uint32_t)xB == firstB + count);
            columns[count++] = (CollisionMaskColumn){
                .byteA = a->mask != nullptr ? (uint32_t)xA / 8 : 0,
                .byteB = b->mask != nullptr ? (uint32_t)xB / 8 : 0,
                .bitA = 0x80u >> ((uint32_t)xA % 8),
                .bitB = 0x80u >> ((uint32_t)xB % 8),
            };
        }
        if (count == 0) continue;

        for (int64_t row = startY; row < stopY; ++row) {
            const GMLReal y = (GMLReal)row + sampleOffset;
            const int32_t yA = Collision_maskCoordinate(ay, y);
            if (yA < 0) continue;
            const int32_t yB = Collision_maskCoordinate(by, y);
            if (yB < 0) continue;
            const uint8_t* rowA = a->mask != nullptr ? a->mask + (size_t)yA * a->stride : &rectangle;
            const uint8_t* rowB = b->mask != nullptr ? b->mask + (size_t)yB * b->stride : &rectangle;
            unsigned col = 0;
            if (packed) {
                for (; col + 8 <= count; col += 8) {
                    const uint8_t bitsA = a->mask != nullptr ? Collision_maskByte(rowA, firstA + col) : rectangle;
                    const uint8_t bitsB = b->mask != nullptr ? Collision_maskByte(rowB, firstB + col) : rectangle;
                    if (bitsA & bitsB) return true;
                }
            }
            for (; col < count; ++col) {
                const CollisionMaskColumn c = columns[col];
                if ((rowA[c.byteA] & c.bitA) && (rowB[c.byteB] & c.bitB)) return true;
            }
        }
    }
    return false;
}

static inline bool Collision_canSplitMaskAxes(const CollisionPointQuery* query) {
    const Sprite* sprite = query->sprite;
    return !query->rotated && isfinite(query->x) && isfinite(query->y) &&
        isfinite(query->scaleX) && isfinite(query->scaleY) &&
        sprite->width > 0 && sprite->width <= INT32_MAX && sprite->height > 0 && sprite->height <= INT32_MAX &&
        (query->mask == nullptr || (sprite->maskWidth <= INT32_MAX && sprite->maskHeight <= INT32_MAX));
}
