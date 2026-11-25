"""Provide ds_map_secure_save and ds_map_secure_load for the 2016 save files.

The files hold a fixed header followed by the map's ds_map_write string.
Only this runtime reads them; files without the header load as missing.
"""

from runtime_verification_source import replace_once


def with_secure_save(code):
    anchor = "static RValue builtin_ds_grid_write(VMContext* ctx, RValue* args, MAYBE_UNUSED int32_t argCount) {"
    code = replace_once(code, anchor, r'''static const char HLD_SECURE_SAVE_HEADER[] = "HLDVM-SECURE-MAP-1\n";

static RValue builtin_ds_map_secure_save(VMContext* ctx, RValue* args, int32_t argCount) {
    REQUIRE_ARGC_AT_LEAST("ds_map_secure_save", 2, RValue_makeBool(false));
    if (args[1].type != RVALUE_STRING || args[1].string == nullptr || args[1].string[0] == '\0') {
        return RValue_makeBool(false);
    }
    if (dsMapGet(ctx->runner, RValue_toInt32(args[0])) == nullptr) return RValue_makeBool(false);
    RValue encoded = builtin_ds_map_write(ctx, args, 1);
    const size_t headerLength = sizeof(HLD_SECURE_SAVE_HEADER) - 1;
    const size_t encodedLength = strlen(encoded.string);
    char* contents = (char*)safeMalloc(headerLength + encodedLength + 1);
    memcpy(contents, HLD_SECURE_SAVE_HEADER, headerLength);
    memcpy(contents + headerLength, encoded.string, encodedLength + 1);
    FileSystem* fs = ctx->runner->fileSystem;
    const bool written = fs->vtable->writeFileText(fs, args[1].string, contents);
    free(contents);
    RValue_free(&encoded);
    return RValue_makeBool(written);
}

static RValue builtin_ds_map_secure_load(VMContext* ctx, RValue* args, int32_t argCount) {
    REQUIRE_ARGC_AT_LEAST("ds_map_secure_load", 1, RValue_makeReal(-1));
    if (args[0].type != RVALUE_STRING || args[0].string == nullptr || args[0].string[0] == '\0') {
        return RValue_makeReal(-1);
    }
    FileSystem* fs = ctx->runner->fileSystem;
    if (!fs->vtable->fileExists(fs, args[0].string)) return RValue_makeReal(-1);
    char* contents = fs->vtable->readFileText(fs, args[0].string);
    if (contents == nullptr) return RValue_makeReal(-1);
    const size_t headerLength = sizeof(HLD_SECURE_SAVE_HEADER) - 1;
    if (strncmp(contents, HLD_SECURE_SAVE_HEADER, headerLength) != 0) {
        free(contents);
        return RValue_makeReal(-1);
    }
    const int32_t id = dsMapCreate(ctx->runner);
    RValue readArgs[2] = {RValue_makeReal((GMLReal) id), RValue_makeString(contents + headerLength)};
    const RValue loaded = builtin_ds_map_read(ctx, readArgs, 2);
    free(contents);
    if (!RValue_toBool(loaded)) {
        RValue destroyArgs[1] = {RValue_makeReal((GMLReal) id)};
        builtin_ds_map_destroy(ctx, destroyArgs, 1);
        return RValue_makeReal(-1);
    }
    return RValue_makeReal((GMLReal) id);
}

''' + anchor)
    anchor = '    VM_registerBuiltin(ctx, "ds_map_write", builtin_ds_map_write);'
    return replace_once(code, anchor, anchor + '''
    VM_registerBuiltin(ctx, "ds_map_secure_save", builtin_ds_map_secure_save);
    VM_registerBuiltin(ctx, "ds_map_secure_load", builtin_ds_map_secure_load);''')
