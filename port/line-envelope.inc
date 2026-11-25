typedef struct {
    uint16_t x, y;
} LineSpriteEnvelope;

typedef struct {
    const LineSpriteEnvelope* sprites;
    uint32_t count;
    float left, right, top, bottom;
} LineEnvelopeQuery;

static const DataWin* lineEnvelopeArchive;
static LineSpriteEnvelope* lineEnvelopes;

void hldReleaseLineEnvelopes(const DataWin* data) {
    if (lineEnvelopeArchive != data) return;
    free(lineEnvelopes);
    lineEnvelopes = nullptr;
    lineEnvelopeArchive = nullptr;
}

static LineEnvelopeQuery prepareLineEnvelope(DataWin* data, GMLReal x1, GMLReal y1, GMLReal x2, GMLReal y2) {
    if (!(GMLReal_fabs(x1) < 1048576 && GMLReal_fabs(y1) < 1048576 &&
          GMLReal_fabs(x2) < 1048576 && GMLReal_fabs(y2) < 1048576)) return (LineEnvelopeQuery){0};
    const uint32_t count = data->sprt.parsedCount;
    if (count == 0 || count > data->sprt.count) return (LineEnvelopeQuery){0};
    if (lineEnvelopeArchive != data) {
        if (lineEnvelopeArchive != nullptr) hldReleaseLineEnvelopes(lineEnvelopeArchive);
        lineEnvelopes = (LineSpriteEnvelope*)safeCalloc(count, sizeof(*lineEnvelopes));
        for (uint32_t i = 0; i < count; ++i) {
            const Sprite* sprite = &data->sprt.sprites[i];
            const bool full = sprite->bboxMode == 1;
            const GMLReal left = (full ? 0.0 : (GMLReal)sprite->marginLeft) - sprite->originX;
            const GMLReal right = (full ? (GMLReal)sprite->width : (GMLReal)sprite->marginRight + 1.0) - sprite->originX;
            const GMLReal top = (full ? 0.0 : (GMLReal)sprite->marginTop) - sprite->originY;
            const GMLReal bottom = (full ? (GMLReal)sprite->height : (GMLReal)sprite->marginBottom + 1.0) - sprite->originY;
            const GMLReal x = GMLReal_fmax(GMLReal_fabs(left), GMLReal_fabs(right));
            const GMLReal y = GMLReal_fmax(GMLReal_fabs(top), GMLReal_fabs(bottom));
            lineEnvelopes[i] = x <= 8192 && y <= 8192 ? (LineSpriteEnvelope){(uint16_t)x, (uint16_t)y}
                                                    : (LineSpriteEnvelope){UINT16_MAX, UINT16_MAX};
        }
        lineEnvelopeArchive = data;
    }
    // With finite coordinates and scaled radii below 2^20, two pixels cover
    // both float conversion error and the compatibility-mode rounding.
    return (LineEnvelopeQuery){lineEnvelopes, count,
        (float)GMLReal_fmin(x1, x2) - 2.0f, (float)GMLReal_fmax(x1, x2) + 2.0f,
        (float)GMLReal_fmin(y1, y2) - 2.0f, (float)GMLReal_fmax(y1, y2) + 2.0f};
}

static inline bool missesLineEnvelope(const LineEnvelopeQuery* query, const Instance* inst) {
    if (query->sprites == nullptr) return false;
    const uint32_t sprite = (uint32_t)(inst->maskIndex >= 0 ? inst->maskIndex : inst->spriteIndex);
    if (sprite >= query->count || inst->imageAngle != 0.0 ||
        !(fabsf(inst->x) < 1048576.0f && fabsf(inst->y) < 1048576.0f)) return false;
    const LineSpriteEnvelope radius = query->sprites[sprite];
    if (radius.x == UINT16_MAX) return false;
    const float x = fabsf(inst->imageXscale) * radius.x;
    const float y = fabsf(inst->imageYscale) * radius.y;
    if (!(x < 1048576.0f && y < 1048576.0f)) return false;
    return inst->x + x < query->left || inst->x - x > query->right ||
           inst->y + y < query->top || inst->y - y > query->bottom;
}

#ifdef HLD_COMPARE_LINE_ENVELOPES
static unsigned lineEnvelopeSkips;

static inline bool useLineEnvelopes(int frame) {
    return frame >= 180 && (frame / 180) % 2 != 0;
}

void hldReportLineEnvelopes(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita line envelopes frame=%d enabled=%d skipped=%u\n",
            frame, (int)useLineEnvelopes(frame - 1), lineEnvelopeSkips);
    lineEnvelopeSkips = 0;
}
#else
#ifdef HLD_VERIFY_LINE_ENVELOPES
static unsigned lineEnvelopeVerified;
#endif
static inline bool useLineEnvelopes(int frame) {
    (void)frame;
    return true;
}
#endif
