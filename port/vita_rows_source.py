"""Full-height picture on the Vita: HLD renders a 480x270 view, scaled x2 to 960x540 and centred on the 960x544 screen
(2 black rows top and bottom). When the game sizes the application surface to 480x270 (SCREEN Create, right after
global.vh = 270, before anything reads vh), it gets 480x272 and global.vh = 272, so every view, surface and camera
built from vh is two game rows (four screen rows) taller and the scaled picture fills 960x544.
The distortion lookup sprite (distortionbase, 480x270, every row identical) is drawn a second time two rows lower when
its target is 272 tall, so the distortion surface keeps the identity mapping in the new bottom rows. The final-present
fast path also accepts the 272-row picture."""

from runtime_verification_source import replace_once


def with_vita_rows_builtins(code):
    a = ("static RValue builtin_surface_resize(VMContext* ctx, RValue* args, MAYBE_UNUSED int32_t argCount) {\n"
         "    int32_t surfaceId = (int32_t) RValue_toReal(args[0]);\n"
         "    int32_t w = (int32_t) RValue_toReal(args[1]);\n"
         "    int32_t h = (int32_t) RValue_toReal(args[2]);\n"
         "    Runner* runner = ctx->runner;\n")
    code = replace_once(code, a, a +
        "#ifdef PLATFORM_VITA\n"
        "    if (surfaceId == runner->applicationSurfaceId && w == 480 && h == 270 && ctx->dataWin->gen8.gameID == 742741103 &&\n"
        "        getenv(\"HLD_NATIVE_ROWS\") == NULL) {\n"
        "        h = 272;\n"
        "        variableInstanceSetOn(ctx, ctx->globalScopeInstance, \"vh\", RValue_makeInt32(272), \"vita rows\");\n"
        "        static bool logged;\n"
        "        if (!logged) { logged = true; logInfo(\"HLD: full-height view 480x272 (global.vh = 272)\\n\"); }\n"
        "    }\n"
        "#endif\n")
    a = "    Renderer_drawSprite(runner->renderer, spriteIndex, subimg, x, y);\n    return RValue_makeUndefined();\n}\n"
    start = code.index("static RValue builtin_draw_sprite(VMContext* ctx, RValue* args, MAYBE_UNUSED int32_t argCount) {")
    at = code.index(a, start)
    assert at - start < 1200
    code = code[:at] + (
        "    Renderer_drawSprite(runner->renderer, spriteIndex, subimg, x, y);\n"
        "#ifdef PLATFORM_VITA\n"
        "    if (spriteIndex == 12 && x == 0.0f && y == 0.0f && (uint32_t) spriteIndex < ctx->dataWin->sprt.count &&\n"
        "        ctx->dataWin->sprt.sprites[12].name != NULL && strcmp(ctx->dataWin->sprt.sprites[12].name, \"distortionbase\") == 0) {\n"
        "        const int32_t target = Runner_surfaceGetTarget(runner);\n"
        "        if (target >= 0 && Renderer_getSurfaceHeight(runner->renderer, target) == 272)\n"
        "            Renderer_drawSprite(runner->renderer, spriteIndex, subimg, x, 2.0f);\n"
        "    }\n"
        "#endif\n"
        "    return RValue_makeUndefined();\n}\n") + code[at + len(a):]
    return code


def with_vita_rows_renderer(code):
    a = "texW == 480 && texH == 270 && srcLeft == 0 && srcTop == 0 && srcWidth == texW && srcHeight == texH &&"
    return replace_once(code, a, "texW == 480 && (texH == 270 || texH == 272) && srcLeft == 0 && srcTop == 0 && srcWidth == texW && srcHeight == texH &&")
