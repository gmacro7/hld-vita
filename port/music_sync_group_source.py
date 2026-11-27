"""GameMaker audio sync groups, which the 2016 game uses for all of its music.

MusicStartSyncGroup (run by the interpreter) creates a sync group, adds every layer of a song to it with
audio_play_in_sync_group, starts it with audio_start_sync_group, and the MUSIC object then fades each layer in with
audio_sound_gain on the sound index. The engine registers none of the audio_*sync_group* builtins, and the
interpreter silently returns undefined for an unregistered builtin, so no music track ever started (sound effects,
which use audio_play_sound, were unaffected).

These builtins are built on the engine's AudioSystem vtable: a track added to a group starts paused (so its stream is
opened and primed but silent), and audio_start_sync_group resumes all of a group's tracks in one call, so the layers
start on the same audio update. A builtin the engine already registers under the same name is left alone.
The code goes at the end of the interpreter source so that no existing line moves (see with_music_sync_groups).
"""

from runtime_verification_source import replace_once

HELPERS = r'''

// ===[ GameMaker audio sync groups (music) ]===
extern void hldAudioFence(void) __attribute__((weak));
enum { HLD_SYNC_GROUP_MAX = 64, HLD_SYNC_GROUP_TRACKS = 16 };
typedef struct {
    bool used, loop, started;
    int32_t count;
    int32_t instances[HLD_SYNC_GROUP_TRACKS];
} HldSyncGroup;
static HldSyncGroup hldSyncGroups[HLD_SYNC_GROUP_MAX];
static unsigned hldSyncGroupStarts;

static AudioSystem* hldSyncAudio(VMContext* ctx) {
    if (ctx->runner == nullptr || ctx->runner->audioSystem == nullptr) return nullptr;
    if (hldAudioFence != nullptr) hldAudioFence();  // the audio worker may be updating; wait for it first
    return ctx->runner->audioSystem;
}

static HldSyncGroup* hldSyncGroupArg(RValue* args, int32_t argCount) {
    if (argCount < 1) return nullptr;
    const int32_t index = RValue_toInt32(args[0]);
    if (index < 0 || index >= HLD_SYNC_GROUP_MAX || !hldSyncGroups[index].used) return nullptr;
    return &hldSyncGroups[index];
}

static void hldSyncGroupStop(AudioSystem* audio, HldSyncGroup* group) {
    for (int32_t i = 0; i < group->count; i++) audio->vtable->stopSound(audio, group->instances[i]);
    group->count = 0;
    group->started = false;
}

static RValue builtin_audio_create_sync_group(VMContext* ctx, RValue* args, int32_t argCount) {
    (void) ctx;
    for (int32_t i = 0; i < HLD_SYNC_GROUP_MAX; i++) {
        if (hldSyncGroups[i].used) continue;
        hldSyncGroups[i] = (HldSyncGroup) {.used = true, .loop = argCount > 0 && RValue_toReal(args[0]) != 0};
        return RValue_makeReal((GMLReal) i);
    }
    logWarn("Audio: no free sync group (%d in use)\n", HLD_SYNC_GROUP_MAX);
    return RValue_makeReal(-1);
}

static RValue builtin_audio_play_in_sync_group(VMContext* ctx, RValue* args, int32_t argCount) {
    HldSyncGroup* group = hldSyncGroupArg(args, argCount);
    AudioSystem* audio = hldSyncAudio(ctx);
    if (group == nullptr || audio == nullptr || argCount < 2) return RValue_makeReal(-1);
    if (group->count >= HLD_SYNC_GROUP_TRACKS) {
        logWarn("Audio: sync group full (%d tracks)\n", HLD_SYNC_GROUP_TRACKS);
        return RValue_makeReal(-1);
    }
    const int32_t sound = RValue_toInt32(args[1]);
    const int32_t instance = audio->vtable->playSound(audio, sound, 0, group->loop);
    if (instance < 0) return RValue_makeReal(-1);
    if (!group->started) audio->vtable->pauseSound(audio, instance);  // held until audio_start_sync_group
    group->instances[group->count++] = instance;
    return RValue_makeReal((GMLReal) instance);
}

static RValue builtin_audio_start_sync_group(VMContext* ctx, RValue* args, int32_t argCount) {
    HldSyncGroup* group = hldSyncGroupArg(args, argCount);
    AudioSystem* audio = hldSyncAudio(ctx);
    if (group == nullptr || audio == nullptr) return RValue_makeUndefined();
    for (int32_t i = 0; i < group->count; i++) audio->vtable->resumeSound(audio, group->instances[i]);
    group->started = true;
    if (hldSyncGroupStarts++ < 8)
        logInfo("Audio: started sync group %d with %d tracks\n", (int) (group - hldSyncGroups), group->count);
    return RValue_makeUndefined();
}

static RValue builtin_audio_stop_sync_group(VMContext* ctx, RValue* args, int32_t argCount) {
    HldSyncGroup* group = hldSyncGroupArg(args, argCount);
    AudioSystem* audio = hldSyncAudio(ctx);
    if (group != nullptr && audio != nullptr) hldSyncGroupStop(audio, group);
    return RValue_makeUndefined();
}

static RValue builtin_audio_destroy_sync_group(VMContext* ctx, RValue* args, int32_t argCount) {
    HldSyncGroup* group = hldSyncGroupArg(args, argCount);
    AudioSystem* audio = hldSyncAudio(ctx);
    if (group == nullptr) return RValue_makeUndefined();
    if (audio != nullptr) hldSyncGroupStop(audio, group);
    group->used = false;
    return RValue_makeUndefined();
}

static RValue builtin_audio_sync_group_is_playing(VMContext* ctx, RValue* args, int32_t argCount) {
    HldSyncGroup* group = hldSyncGroupArg(args, argCount);
    AudioSystem* audio = hldSyncAudio(ctx);
    if (group == nullptr || audio == nullptr || !group->started) return RValue_makeBool(false);
    for (int32_t i = 0; i < group->count; i++)
        if (audio->vtable->isPlaying(audio, group->instances[i])) return RValue_makeBool(true);
    return RValue_makeBool(false);
}

static void hldRegisterSyncGroupBuiltin(VMContext* ctx, const char* name, BuiltinFunc func) {
    if (shgeti(ctx->builtinMap, name) < 0) VM_registerBuiltin(ctx, name, func);
}

void hldRegisterSyncGroups(VMContext* ctx) {
    hldRegisterSyncGroupBuiltin(ctx, "audio_create_sync_group", builtin_audio_create_sync_group);
    hldRegisterSyncGroupBuiltin(ctx, "audio_play_in_sync_group", builtin_audio_play_in_sync_group);
    hldRegisterSyncGroupBuiltin(ctx, "audio_start_sync_group", builtin_audio_start_sync_group);
    hldRegisterSyncGroupBuiltin(ctx, "audio_stop_sync_group", builtin_audio_stop_sync_group);
    hldRegisterSyncGroupBuiltin(ctx, "audio_destroy_sync_group", builtin_audio_destroy_sync_group);
    hldRegisterSyncGroupBuiltin(ctx, "audio_sync_group_is_playing", builtin_audio_sync_group_is_playing);
}
'''

def with_music_sync_groups(code):
    """Append the builtins at the end of the file and register them on the existing registration line, so no line of
    the interpreter moves: its GCC profile (port/vm-profile) is keyed to line numbers, and shifting them would leave
    hundreds of functions without profile data."""
    if "builtin_audio_start_sync_group" in code:
        return code
    register = "    VMBuiltins_registerAll(ctx);\n"
    code = replace_once(code, register, "    VMBuiltins_registerAll(ctx); "
                        "{ extern void hldRegisterSyncGroups(VMContext*); hldRegisterSyncGroups(ctx); }\n")
    return code.rstrip("\n") + "\n" + HELPERS
