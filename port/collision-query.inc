// For an unrotated unit-scale sprite at an integer position, each sampled
// world pixel maps to one mask bit. The caller falls back for negative local
// coordinates because the legacy point sampler truncates toward zero.
static bool collisionPackedRect(const CollisionPointQuery* query,
                                int32_t startX, int32_t startY, int32_t endX, int32_t endY,
                                bool* hit) {
    const Sprite* sprite = query->sprite;
    if (sprite == nullptr || query->mask == nullptr || query->rotated ||
        query->scaleX != 1.0 || query->scaleY != 1.0) return false;
    if (!(query->x > -2147483648.0 && query->x < 2147483648.0 &&
          query->y > -2147483648.0 && query->y < 2147483648.0)) return false;
    const int32_t posX = (int32_t)query->x;
    const int32_t posY = (int32_t)query->y;
    if (query->x != posX || query->y != posY ||
        sprite->width > INT32_MAX || sprite->height > INT32_MAX ||
        sprite->maskWidth > INT32_MAX || sprite->maskHeight > INT32_MAX) return false;
    if (sizeof(GMLReal) == sizeof(float) &&
        (posX <= -4194304 || posX >= 4194304 || posY <= -4194304 || posY >= 4194304 ||
         startX <= -4194304 || endX >= 4194304 || startY <= -4194304 || endY >= 4194304 ||
         sprite->originX <= -4194304 || sprite->originX >= 4194304 ||
         sprite->originY <= -4194304 || sprite->originY >= 4194304)) return false;

    const int64_t offsetX = (int64_t)sprite->originX - posX;
    const int64_t offsetY = (int64_t)sprite->originY - posY;
    int64_t left = startX + offsetX, right = endX + offsetX;
    int64_t top = startY + offsetY, bottom = endY + offsetY;
    // Both float and double GML builds represent these half-integers exactly.
    if (left < 0 || top < 0 || right > 8388608 || bottom > 8388608) return false;
    if (right > sprite->width) right = sprite->width;
    if (bottom > sprite->height) bottom = sprite->height;
    if (sprite->bboxMode != 1) {
        if (left < sprite->marginLeft) left = sprite->marginLeft;
        if (top < sprite->marginTop) top = sprite->marginTop;
        if (right > (int64_t)sprite->marginRight + 1) right = (int64_t)sprite->marginRight + 1;
        if (bottom > (int64_t)sprite->marginBottom + 1) bottom = (int64_t)sprite->marginBottom + 1;
    }
    const int64_t maskRight = (int64_t)sprite->maskOffsetX + sprite->maskWidth;
    const int64_t maskBottom = (int64_t)sprite->maskOffsetY + sprite->maskHeight;
    if (left < sprite->maskOffsetX) left = sprite->maskOffsetX;
    if (top < sprite->maskOffsetY) top = sprite->maskOffsetY;
    if (right > maskRight) right = maskRight;
    if (bottom > maskBottom) bottom = maskBottom;

    *hit = false;
    if (left >= right || top >= bottom) return true;
    const uint32_t x = (uint32_t)(left - sprite->maskOffsetX);
    const uint32_t lastX = (uint32_t)(right - 1 - sprite->maskOffsetX);
    const uint32_t firstColumn = x >> 3;
    const uint32_t lastColumn = lastX >> 3;
    const unsigned firstBits = 0xffu >> (x & 7);
    const unsigned lastBits = (0xffu << (7 - (lastX & 7))) & 0xffu;
    for (int64_t y = top; y < bottom; ++y) {
        const uint8_t* row = query->mask + (size_t)(y - sprite->maskOffsetY) * query->stride;
        if (firstColumn == lastColumn) {
            if ((row[firstColumn] & firstBits & lastBits) == 0) continue;
        } else if ((row[firstColumn] & firstBits) == 0 && (row[lastColumn] & lastBits) == 0) {
            uint32_t column = firstColumn + 1;
            while (column < lastColumn && row[column] == 0) ++column;
            if (column == lastColumn) continue;
        }
        *hit = true;
        break;
    }
    return true;
}

static bool collisionRectPoints(const CollisionPointQuery* query,
                                int32_t startX, int32_t startY, int32_t endX, int32_t endY) {
    for (int32_t py = startY; py < endY; ++py) {
        for (int32_t px = startX; px < endX; ++px) {
            if (Collision_testPoint(query, (GMLReal)px + 0.5, (GMLReal)py + 0.5)) return true;
        }
    }
    return false;
}

static unsigned collisionPackedRectangles;

static bool collisionRectPixels(Sprite* sprite, Instance* instance,
                                int32_t startX, int32_t startY, int32_t endX, int32_t endY) {
    if (startX >= endX || startY >= endY) return false;
    const CollisionPointQuery query = Collision_preparePointQuery(sprite, instance);
    bool hit;
    if (!collisionPackedRect(&query, startX, startY, endX, endY, &hit))
        return collisionRectPoints(&query, startX, startY, endX, endY);
    ++collisionPackedRectangles;
#ifdef HLD_VERIFY_COLLISION_QUERIES
    require(hit == collisionRectPoints(&query, startX, startY, endX, endY));
#endif
    return hit;
}
