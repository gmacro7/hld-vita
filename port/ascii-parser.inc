typedef struct {
    const VMContext* context;
    const CodeEntry* code;
    int32_t cursor, indent;
    bool enabled;
} AsciiParser;

static AsciiParser asciiParser;

static void bindAsciiParser(VMContext* ctx) {
    const CodeEntry* code = matchHldCode(ctx, "gml_Script_ParseString", 612, 0xacd2e87fu);
    if (code == nullptr) return;
    const uint8_t* bytes = codeBytes(ctx, code);
    const int32_t cursor = resolveVarDef(ctx, resolveVarOperand(bytes + 0x50))->varID;
    const int32_t indent = resolveVarDef(ctx, resolveVarOperand(bytes + 0x3c))->varID;
    if (cursor < 0 || indent < 0 || cursor == indent) return;
    bool enabled = true;
#ifdef HLD_COMPARE_ASCII_PARSER
    FILE* flag = fopen("ux0:data/hld-vm/ascii-parser", "r");
    enabled = flag != nullptr;
    if (flag != nullptr) fclose(flag);
#endif
    asciiParser = (AsciiParser){ctx, code, cursor, indent, enabled};
    logInfo("HLD ASCII parser enabled=%d\n", (int)enabled);
}

#if !defined(ENABLE_VM_TRACING) && !defined(ENABLE_VM_GML_PROFILER) && !defined(ENABLE_VM_OPCODE_PROFILER)
#define HLD_ASCII_PARSER_AVAILABLE

typedef struct {
    const char* token;
    size_t bytes;
    RValue cursor;
    bool markIndented;
} AsciiParse;

static bool scanAsciiToken(VMContext* ctx, Instance* inst, RValue text, AsciiParse* parsed) {
    if (text.type != RVALUE_STRING || text.string == nullptr || inst == nullptr ||
        inst->objectIndex == STRUCT_OBJECT_INDEX || ctx->runner == nullptr) return false;
    const GMLReal epsilon = ctx->runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    RValue cursor = Instance_getSelfVar(inst, asciiParser.cursor);
    const RValue indent = Instance_getSelfVar(inst, asciiParser.indent);
    if ((cursor.type != RVALUE_INT32 && cursor.type != RVALUE_REAL) ||
        (indent.type != RVALUE_INT32 && indent.type != RVALUE_REAL)) return false;
    const GMLReal source = RValue_toReal(cursor);
    const GMLReal indentValue = RValue_toReal(indent);
    if (!(source >= 1.0 && source <= 1048576.0) || (indentValue != 0.0 && indentValue != 1.0)) return false;
    size_t position = (size_t)source;
    if ((GMLReal)position != source) return false;
    size_t length = strlen(text.string);
    if (length > 1048576u) return false;
    size_t i = 0;
    for (; length - i >= 4; i += 4) {
        uint32_t word;
        memcpy(&word, text.string + i, sizeof(word));
        if ((word & UINT32_C(0x80808080)) != 0) return false;
    }
    for (; i < length; ++i) if ((unsigned char)text.string[i] >= 128u) return false;

    if (indentValue == 0.0) {
        // The original leaves one character at the end even on an
        // all-space line. Positions are one-based in the game state.
        while (position < length && text.string[position - 1u] == ' ') ++position;
    }
    const char* token = text.string + (position <= length ? position - 1u : length);
    const size_t bytes = strcspn(token, "/,]|");
    // The original advances by the token length and one delimiter, even
    // when the current position is past the string or at a comment.
    const int32_t next = (int32_t)(position + bytes + 1u);
    RValue nextCursor = cursor.type == RVALUE_INT32 ? RValue_makeInt32(next) : RValue_makeReal(next);
    nextCursor.gmlStackType = GML_TYPE_VARIABLE;
    *parsed = (AsciiParse){token, bytes, nextCursor, indentValue == 0.0};
    return true;
}
#endif

#if defined(HLD_COMPARE_ASCII_PARSER) || defined(HLD_VERIFY_ASCII_PARSER)
static unsigned parsedAsciiTokens;
#endif

#ifdef HLD_ASCII_PARSER_AVAILABLE
static inline void noteAsciiParse(void) {
#ifdef HLD_COMPARE_ASCII_PARSER
    ++parsedAsciiTokens;
#elif defined(HLD_VERIFY_ASCII_PARSER)
    if (++parsedAsciiTokens == 1000) logInfo("HLD: verified 1,000 ASCII tokens and cursor updates\n");
#endif
}
#endif

#ifdef HLD_COMPARE_ASCII_PARSER
void hldReportAsciiParser(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita ASCII parser frame=%d enabled=%d tokens=%u\n", frame, (int)asciiParser.enabled, parsedAsciiTokens);
    parsedAsciiTokens = 0;
}
#endif
