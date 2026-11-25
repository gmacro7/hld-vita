"""Time the exact dynamic sprite readback, upload and collision-mask work."""
from pathlib import Path


def with_mask_profile(code):
    lo = code.index("static int32_t glCreateSpriteFromPixels(")
    hi = code.index("static void glDeleteSprite(", lo)
    region = code[lo:hi]

    def substitute(before, after):
        nonlocal region
        assert region.count(before) == 1, before
        region = region.replace(before, after)

    substitute("    // Create a new GL texture from the captured pixels",
               "    const uint64_t uploadStart = nowNanos();\n    // Create a new GL texture from the captured pixels")
    substitute("    // Find or allocate slots for texture page, TPAG, and sprite",
               "    const uint64_t uploadElapsed = nowNanos() - uploadStart;\n\n"
               "    // Find or allocate slots for texture page, TPAG, and sprite")
    substitute("    int32_t left = w, top = h, right = -1, bottom = -1;",
               "    const uint64_t scalarStart = nowNanos();\n    int32_t left = w, top = h, right = -1, bottom = -1;")
    substitute("    sprite->marginLeft = left;", """    const uint64_t scalarElapsed = nowNanos() - scalarStart;
    uint8_t* vectorMask = safeCalloc(stride, (size_t)h);
    const uint64_t vectorStart = nowNanos();
    MaskBounds vector = fillVectorMask(vectorMask, pixels, w, h, stride);
    const uint64_t vectorElapsed = nowNanos() - vectorStart;
    bool equal = vector.left == left && vector.right == right && vector.top == top && vector.bottom == bottom &&
        memcmp(mask, vectorMask, stride * (size_t)h) == 0;
    free(vectorMask);
    logInfo("Vita mask profile frame=%d size=%dx%d upload_ms=%.3f scalar_ms=%.3f vector_ms=%.3f match=%d\\n",
            renderer->runner->frameCount, w, h, uploadElapsed * 1e-6, scalarElapsed * 1e-6, vectorElapsed * 1e-6, equal);
    sprite->marginLeft = left;""")
    substitute("    glReadPixels(x, y, w, h, GL_RGBA, GL_UNSIGNED_BYTE, pixels);",
               "    const uint64_t readStart = nowNanos();\n"
               "    glReadPixels(x, y, w, h, GL_RGBA, GL_UNSIGNED_BYTE, pixels);\n"
               "    logInfo(\"Vita sprite readback frame=%d size=%dx%d read_ms=%.3f\\n\",\n"
               "            renderer->runner->frameCount, w, h, (nowNanos() - readStart) * 1e-6);")
    return code[:lo] + Path(__file__).with_name("mask-profile.inc").read_text() + "\n" + region + code[hi:]
