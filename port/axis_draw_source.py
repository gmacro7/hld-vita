"""Draw unrotated sprites directly using their two distinct X and Y coordinates."""


def with_axis_sprite_draw(code, compare=False):
    anchor = "    // Build 2D transform: T(x,y) * R(-angleDeg) * S(xscale, yscale)\n"
    assert code.count(anchor) == 1
    fast = """    if (angleDeg == 0.0f && isfinite(xscale) && isfinite(yscale) &&
        isfinite(localX0) && isfinite(localX1) && isfinite(localY0) && isfinite(localY1)) {
        const float x0 = localX0 * xscale + x;
        const float x1 = localX1 * xscale + x;
        const float y0 = localY0 * yscale + y;
        const float y1 = localY1 * yscale + y;
        const uint8_t r = (uint8_t) BGR_R(color);
        const uint8_t g = (uint8_t) BGR_G(color);
        const uint8_t b = (uint8_t) BGR_B(color);
        emitTexturedQuad(renderer, textureId, x0, y0, x1, y0, x1, y1, x0, y1,
                        u0, v0, u1, v1, r, g, b, r, g, b, r, g, b, r, g, b, alpha);
        return;
    }

"""
    if compare:
        fast = fast.replace("    if (angleDeg == 0.0f &&", """    const Runner* runner = renderer->base.runner;
    if (angleDeg == 0.0f && runner != nullptr && runner->frameCount > 180 &&
        ((runner->frameCount - 1) / 180) % 2 != 0 &&""")
        fast = fast.replace("        return;\n", """        ++axisSpriteCount;
        return;
""")
        phase = r"""
static unsigned axisSpriteCount;

void hldReportAxisSprites(int frame) {
    if (frame > 0 && frame % 90 == 0) {
        logInfo("Vita axis sprite frame=%d quads=%u\n", frame, axisSpriteCount);
        axisSpriteCount = 0;
    }
}

"""
        signature = "static void drawTexture(\n"
        assert code.count(signature) == 1
        code = code.replace(signature, phase + signature)
    return code.replace(anchor, fast + anchor)
