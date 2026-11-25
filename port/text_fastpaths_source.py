"""Avoid redundant allocation and byte-by-byte scans in string builtins."""

import re


TEXT_SCAN = r'''
static inline int32_t fastTextAdvance(const char* text, int32_t length, int32_t remaining) {
    int32_t pos = 0;
    while (pos < length && remaining > 0) {
        int32_t step = 1;
        if (remaining >= 4 && length - pos >= 4) {
            uint32_t word;
            memcpy(&word, text + pos, sizeof(word));
            if (!(word & UINT32_C(0x80808080))) step = 4;
        }
        pos += step;
        // The original indexer groups continuation bytes even in malformed UTF-8.
        while (pos < length && ((uint8_t)text[pos] & 0xc0) == 0x80) ++pos;
        remaining -= step;
    }
    return pos;
}

static inline int32_t fastTextCount(const char* text, int32_t length) {
    if (length <= 0) return 0;
    int32_t continuation = 0;
    int32_t pos = 0;
    for (; length - pos >= 4; pos += 4) {
        uint32_t word;
        memcpy(&word, text + pos, sizeof(word));
        uint32_t lanes = (((word >> 1) & ~word) & UINT32_C(0x40404040)) >> 6;
        continuation += (int32_t)((lanes * UINT32_C(0x01010101)) >> 24);
    }
    for (; pos < length; ++pos)
        continuation += ((uint8_t)text[pos] & 0xc0) == 0x80;
    return length - continuation;
}

'''


def with_text_fastpaths(code, compare=False):
    prefix = TEXT_SCAN
    if compare:
        prefix += r'''
static bool useFastText(void) {
    static bool initialized;
    static bool enabled;
    if (!initialized) {
        FILE* flag = fopen("ux0:data/hld-vm/fast-text", "r");
        enabled = flag != nullptr;
        if (flag != nullptr) fclose(flag);
        initialized = true;
        logInfo("Vita text paths fast=%d\n", (int)enabled);
    }
    return enabled;
}
'''
    bodies = {
        "length": r'''    const int32_t length = (int32_t)strlen(text);
    const int32_t count = fastTextCount(text, length);
#ifdef HLD_VERIFY_TEXT_FASTPATHS
    require(count == TextUtils_utf8CodepointCount(text, length));
#endif
    return RValue_makeInt32(count);''',
        "char_at": r'''    const int32_t index = RValue_toInt32(args[1]);
    const int32_t length = (int32_t)strlen(text);
    const int32_t position = index > 0 ? index - 1 : 0;
    const int32_t start = fastTextAdvance(text, length, position);
#ifdef HLD_VERIFY_TEXT_FASTPATHS
    require(start == TextUtils_utf8AdvanceCodepoints(text, length, position));
#endif
    if (start >= length) return RValue_makeString("");
    const uint8_t byte = (uint8_t)text[start];
    if (byte < 128) return RValue_makeString(singleByteStrings[byte]);
    int32_t end = start;
    TextUtils_decodeUtf8(text, length, &end);
    const size_t bytes = (size_t)(end - start);
    char* result = safeMalloc(bytes + 1);
    memcpy(result, text + start, bytes);
    result[bytes] = '\0';
    return RValue_makeOwnedString(result);''',
        "ord_at": r'''    const int32_t index = RValue_toInt32(args[1]);
    const int32_t length = (int32_t)strlen(text);
    const int32_t position = index > 0 ? index - 1 : 0;
    int32_t start = fastTextAdvance(text, length, position);
#ifdef HLD_VERIFY_TEXT_FASTPATHS
    require(start == TextUtils_utf8AdvanceCodepoints(text, length, position));
#endif
    return start < length ? RValue_makeReal((GMLReal)TextUtils_decodeUtf8(text, length, &start))
                          : RValue_makeReal(-1.0);''',
    }
    for suffix, body in bodies.items():
        name = "builtin_string_" + suffix
        match = re.search(rf"(?m)^(static RValue {name})\(([^\n]+)\) \{{\n[\s\S]*?^\}}", code)
        assert match is not None, name
        reference = match[0].replace(match[1], match[1] + "_reference", 1)
        definitions = prefix if suffix == "length" else ""
        if suffix == "char_at":
            entries = [", ".join(f"{{{value}, 0}}" for value in range(start, start + 8))
                       for start in range(0, 128, 8)]
            definitions = "static const char singleByteStrings[128][2] = {\n    " + ",\n    ".join(entries) + "\n};\n"
        count = 1 if suffix == "length" else 2
        gate = " || !useFastText()" if compare else ""
        replacement = f"""{definitions}{reference}

{match[1]}({match[2]}) {{
    if (argCount < {count} || args[0].type != RVALUE_STRING{gate})
        return {name}_reference(ctx, args, argCount);
    const char* text = args[0].string != nullptr ? args[0].string : "";
{body}
}}"""
        code = code[:match.start()] + replacement + code[match.end():]
    return code
