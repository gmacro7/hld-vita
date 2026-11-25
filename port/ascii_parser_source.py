"""Use one byte scan for ASCII inputs to the fingerprinted parser."""

from pathlib import Path


def with_ascii_parser(code, compare=False, verify=False):
    def replace(before, after):
        nonlocal code
        assert code.count(before) == 1, before
        code = code.replace(before, after)

    helper = Path(__file__).with_name("ascii-parser.inc").read_text()
    if compare:
        helper = "#define HLD_COMPARE_ASCII_PARSER\n" + helper
    if verify:
        helper = "#define HLD_VERIFY_ASCII_PARSER\n" + helper
    anchor = "VMContext* VM_create(DataWin* dataWin) {"
    replace(anchor, helper + "\n" + anchor)
    anchor = "        ctx->hldSceneryVisibilityCode = matchHldCode("
    replace(anchor, "        bindAsciiParser(ctx);\n" + anchor)
    replace("void VM_free(VMContext* ctx) {", "void VM_free(VMContext* ctx) {\n"
            "    if (asciiParser.context == ctx) asciiParser = (AsciiParser){0};")
    anchor = "    // Create a CallFrame for this script\n"
    replace(anchor, r'''#ifdef HLD_ASCII_PARSER_AVAILABLE
    AsciiParse parsed;
    const bool parsedAscii = code == asciiParser.code && ctx == asciiParser.context &&
        argCount == 1 && args != nullptr && asciiParser.enabled &&
        scanAsciiToken(ctx, selfInstance, args[0], &parsed);
    if (parsedAscii) {
        noteAsciiParse();
#ifndef HLD_VERIFY_ASCII_PARSER
        char* token = (char*)safeMalloc(parsed.bytes + 1u);
        memcpy(token, parsed.token, parsed.bytes);
        token[parsed.bytes] = '\0';
        if (parsed.markIndented) Instance_setSelfVar(selfInstance, asciiParser.indent, RValue_makeReal(1.0));
        Instance_setSelfVar(selfInstance, asciiParser.cursor, parsed.cursor);
        ctx->savearefBalance = 0;
        ctx->bytecodeBase = codeBytes(ctx, code);
        RValue result = RValue_makeOwnedString(token);
        result.gmlStackType = GML_TYPE_VARIABLE;
        return result;
#endif
    }
#endif

''' + anchor)
    anchor = "    RValue result = executeBytecodeInstructions(ctx, code);"
    replace(anchor, anchor + r'''
#if defined(HLD_ASCII_PARSER_AVAILABLE) && defined(HLD_VERIFY_ASCII_PARSER)
    if (parsedAscii) {
        require(result.type == RVALUE_STRING && result.string != nullptr && result.gmlStackType == GML_TYPE_VARIABLE);
        require(strlen(result.string) == parsed.bytes && memcmp(result.string, parsed.token, parsed.bytes) == 0);
        const RValue cursor = Instance_getSelfVar(selfInstance, asciiParser.cursor);
        require(cursor.type == parsed.cursor.type && cursor.gmlStackType == parsed.cursor.gmlStackType &&
                RValue_toReal(cursor) == RValue_toReal(parsed.cursor));
        const RValue indent = Instance_getSelfVar(selfInstance, asciiParser.indent);
        require(RValue_toReal(indent) == 1.0);
        if (parsed.markIndented) require(indent.type == RVALUE_REAL && indent.gmlStackType == GML_TYPE_DOUBLE);
    }
#endif''')
    return code
