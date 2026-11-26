"""Native bullet trails (marker ux0:data/hld-vm/native-trails).

Every frame each bullet calls Effect_ImageFade, which creates an imageFade instance: a copy of the bullet's sprite
that fades out over a few frames. In a boss barrage that is ~40 instances created, stepped, drawn, destroyed and
cleaned up per frame. The bullet discards the returned id and no game code touches imageFade instances afterwards
(no deactivation in the game; the in-play with(all) loops only act on the player type), so the trail can be a plain
record instead of an instance.

Exactness: the record takes the instance id the instance would have had (nextInstanceId), so later ids are unchanged.
It is not stepped on the frame it is created (event loops iterate snapshots), honours ActionIsPaused (globals only),
fades with the same GMLReal/float conversions and epsilon comparison as the translated imageFade step, toggles its blend
white -> fuchsia -> aqua like the flash code, and draws with the same renderer calls as imageFade's draw event
(luminosity uniform when glowing, draw_sprite_ext at floor(x), floor(y - z)). It sits in the depth-sorted draw list
with an instance's sort key (depth, instance kind, id), so it draws exactly where the instance would. Without the
marker hld_native_trail runs the original Effect_ImageFade script.

At build time gml_source_rewrite (called by gml_translate for every script) turns the bullet step's stand-alone
Effect_ImageFade(...) statement into hld_native_trail(alpha, fadeRate, flash, glowing, xscale, z, global.h_Luma), with
xscale chosen as Effect_ImageFade does. HLDC_NATIVE_TRAILS=0 leaves both the source and the engine unchanged.
"""

from runtime_verification_source import replace_once

TRAILS = r'''
// ===[ Native bullet trails ]===
#define HLD_DRAWABLE_TRAIL ((DrawableType) 7)
typedef struct {
    uint32_t id, epoch, blend;
    int32_t sprite, luma;
    float imageIndex, x, y, xscale, yscale, alpha;
    GMLReal angle, depth, z, fade, flash, glowing;
    bool alive;
} HldTrail;
static HldTrail* hldTrails;
static uint32_t hldTrailEpoch;

static bool hldTrailsEnabled(void) {
    static int enabled = -1;
    if (enabled < 0) {
        FILE* marker = fopen("ux0:data/hld-vm/native-trails", "r");
        enabled = marker != nullptr;
        if (marker != nullptr) fclose(marker);
    }
    return enabled == 1;
}

static int32_t hldTrailCodeIndex(Runner* runner, const char* name) {
    ptrdiff_t index = shgeti(runner->vmContext->codeIndexByName, name);
    return index < 0 ? -1 : runner->vmContext->codeIndexByName[index].value;
}

RValue hldTrailSpawnFromBuiltin(VMContext* ctx, RValue* args, int32_t argCount) {
    Runner* runner = ctx->runner;
    Instance* self = ctx->currentCallFrame->self;
    if (!hldTrailsEnabled() || self == nullptr || argCount < 7) {
        static int32_t fallback = -2;
        if (fallback == -2) fallback = hldTrailCodeIndex(runner, "gml_Script_Effect_ImageFade");
        if (fallback < 0) return RValue_makeUndefined();
        return VM_executeCode(ctx, fallback, self, ctx->currentCallFrame->other, CODE_NOT_RELATED_TO_EVENTS,
                              CODE_NOT_RELATED_TO_EVENTS, CODE_NOT_RELATED_TO_EVENTS, args, argCount < 4 ? argCount : 4);
    }
    HldTrail trail;
    ZERO_STRUCT(trail);
    trail.id = runner->nextInstanceId++;
    trail.epoch = hldTrailEpoch;
    trail.x = self->x;
    trail.y = self->y;
    trail.sprite = self->spriteIndex;
    trail.imageIndex = self->imageIndex;
    trail.angle = self->imageAngle;
    trail.depth = self->depth;
    trail.alpha = (float) RValue_toReal(args[0]);
    trail.fade = RValue_toReal(args[1]);
    trail.flash = RValue_toReal(args[2]);
    trail.glowing = RValue_toReal(args[3]);
    trail.xscale = (float) RValue_toReal(args[4]);
    trail.yscale = self->imageYscale;
    trail.z = RValue_toReal(args[5]);
    trail.luma = (int32_t) RValue_toReal(args[6]);
    trail.blend = 0xFFFFFF;
    trail.alive = true;
    int32_t slot = -1;
    for (int32_t i = 0; i < (int32_t) arrlen(hldTrails); ++i) {
        if (!hldTrails[i].alive) { slot = i; break; }
    }
    if (slot < 0) { arrput(hldTrails, trail); } else { hldTrails[slot] = trail; }
    runner->drawableListStructureDirty = true;
    return RValue_makeReal((GMLReal) trail.id);
}

static void hldTrailsStep(Runner* runner) {
    // Trails come only from Normal Step code, so those made this frame (this epoch) are not stepped until the next one.
    const uint32_t epoch = hldTrailEpoch++;
    bool any = false;
    // The runner's animation pass (before Normal Step, paused or not) wraps image_index into the frame range.
    for (int32_t i = 0; i < (int32_t) arrlen(hldTrails); ++i) {
        HldTrail* trail = &hldTrails[i];
        if (!trail->alive || trail->epoch == epoch) continue;
        any = true;
        if (0 > trail->sprite || (uint32_t) trail->sprite >= runner->dataWin->sprt.count) continue;
        const float frameCount = (float) runner->dataWin->sprt.sprites[trail->sprite].textureCount;
        if (trail->imageIndex >= frameCount) trail->imageIndex -= frameCount;
        else if (0.0f > trail->imageIndex) trail->imageIndex += frameCount;
    }
    if (!any) return;
    static int32_t paused = -2;
    if (paused == -2) paused = hldTrailCodeIndex(runner, "gml_Script_ActionIsPaused");
    if (paused >= 0) {
        RValue result = VM_executeCode(runner->vmContext, paused, runner->vmContext->globalScopeInstance, nullptr,
                                       CODE_NOT_RELATED_TO_EVENTS, CODE_NOT_RELATED_TO_EVENTS, CODE_NOT_RELATED_TO_EVENTS,
                                       nullptr, 0);
        const bool isPaused = RValue_toBool(result);
        RValue_free(&result);
        if (isPaused) return;
    }
    const GMLReal epsilon = runner->mathEpsilon;
    for (int32_t i = 0; i < (int32_t) arrlen(hldTrails); ++i) {
        HldTrail* trail = &hldTrails[i];
        if (!trail->alive || trail->epoch == epoch) continue;
        trail->alpha = (float) ((GMLReal) trail->alpha - trail->fade);
        const GMLReal diff = (GMLReal) trail->alpha;
        if (GMLReal_fabs(diff) <= epsilon || diff < 0) {
            trail->alive = false;
            runner->drawableListStructureDirty = true;
        }
        if (GMLReal_fabs(trail->flash - 1) <= epsilon)
            trail->blend = trail->blend == 0xFF00FF ? 0xFFFF00 : 0xFF00FF;
    }
}

static void hldTrailsClear(Runner* runner) {
    // Not persistent: room changes remove them. Slots stay allocated so a stale drawable index stays in bounds.
    bool any = false;
    for (int32_t i = 0; i < (int32_t) arrlen(hldTrails); ++i) {
        any |= hldTrails[i].alive;
        hldTrails[i].alive = false;
    }
    if (any) runner->drawableListStructureDirty = true;
}

static void hldDrawTrail(Runner* runner, int32_t slot) {
    if (!runner->drawEnabled || runner->renderer == nullptr) return;
    if (slot < 0 || slot >= (int32_t) arrlen(hldTrails) || !hldTrails[slot].alive) return;
    const HldTrail* trail = &hldTrails[slot];
    Renderer* renderer = runner->renderer;
    const bool glowing = GMLReal_fabs(trail->glowing - 1) <= runner->mathEpsilon;
    if (glowing) renderer->vtable->shaderSetUniformF(renderer, trail->luma, 1, (float) (GMLReal) 0.9, 0.0, 0.0, 0.0);
    int32_t subimg = RValue_toInt32(RValue_makeReal((GMLReal) trail->imageIndex));
    if (0 > subimg) subimg = (int32_t) trail->imageIndex;
    Renderer_drawSpriteExt(renderer, trail->sprite, subimg, (float) GMLReal_floor((GMLReal) trail->x),
                           (float) GMLReal_floor((GMLReal) trail->y - trail->z), trail->xscale, trail->yscale,
                           (float) trail->angle, trail->blend, trail->alpha);
    if (glowing) renderer->vtable->shaderSetUniformF(renderer, trail->luma, 1, (float) (GMLReal) 0, 0.0, 0.0, 0.0);
}

'''


def with_native_trails_runner(code):
    code = replace_once(code, "// ===[ Draw ]===\n", "// ===[ Draw ]===\n" + TRAILS)
    code = replace_once(code, "        case DRAWABLE_INSTANCE: k.order = (int32_t) d->instance->instanceId;  break;",
                        "        case DRAWABLE_INSTANCE: k.order = (int32_t) d->instance->instanceId;  break;\n"
                        "        case HLD_DRAWABLE_TRAIL: k.type = DRAWABLE_INSTANCE; "
                        "k.order = (int32_t) hldTrails[d->particleSystemId].id; break;")
    anchor = "        if (!DataWin_isVersionAtLeast(runner->dataWin, 2, 0, 0, 0)) {\n            repeat(room->tileCount, i) {"
    code = replace_once(code, anchor, r'''        for (int32_t t = 0; t < (int32_t) arrlen(hldTrails); ++t) {
            if (!hldTrails[t].alive) continue;
            Drawable d;
            ZERO_STRUCT(d);
            d.type = HLD_DRAWABLE_TRAIL;
            d.depth = hldTrails[t].depth;
            d.particleSystemId = t;
            arrput(runner->cachedDrawables, d);
        }

''' + anchor)
    anchor = "        } else if (d->type == DRAWABLE_PARTICLE_SYSTEM) {\n            // Filtered at draw time, like instance visibility"
    code = replace_once(code, anchor, "        } else if (d->type == HLD_DRAWABLE_TRAIL) {\n"
                                      "            hldDrawTrail(runner, d->particleSystemId);\n" + anchor)
    step = "    Runner_executeEventForAll(runner, EVENT_STEP, STEP_NORMAL);\n"
    if step not in code:  # the step-event profile build routes the phases through profileStepDispatch
        step = "    profileStepDispatch(runner, STEP_NORMAL, 1);\n"
    code = replace_once(code, step, step + "    hldTrailsStep(runner);\n")
    room = "static void initRoom(Runner* runner, int32_t roomIndex) {"
    code = replace_once(code, room, room + "\n    hldTrailsClear(runner);")
    return code


def with_native_trails_builtins(code):
    anchor = "static RValue builtin_place_meeting(VMContext* ctx, RValue* args, int32_t argCount) {"
    code = replace_once(code, anchor, "RValue hldTrailSpawnFromBuiltin(VMContext* ctx, RValue* args, int32_t argCount);\n"
                        "static RValue builtin_hld_native_trail(VMContext* ctx, RValue* args, int32_t argCount) {\n"
                        "    return hldTrailSpawnFromBuiltin(ctx, args, argCount);\n}\n\n" + anchor)
    register = '    VM_registerBuiltin(ctx, "place_meeting", builtin_place_meeting);'
    return replace_once(code, register, register + '\n    VM_registerBuiltin(ctx, "hld_native_trail", builtin_hld_native_trail);')


# ---- Build-time source rewrite (gml_translate reads every decompiled script through gml_source_rewrite) ----
import re

_TRAIL_CALL = re.compile(r"^(?P<indent>[ \t]*)Effect_ImageFade\((?P<args>[^;()\n]*)\);[ \t]*$", re.MULTILINE)
_TRAIL_SCRIPTS = {"gml_Object_bullet_Step_0"}


def _native_trail_call(match):
    args = [a.strip() for a in match.group("args").split(",")]
    if len(args) not in (3, 4) or not all(args):
        return match.group(0)
    glowing = args[3] if len(args) == 4 else "0"
    i = match.group("indent")
    # Effect_ImageFade's arguments as it reads them: the caller's xs when IsType(caller, 17), else image_xscale.
    return (f"{i}var _hldTrailXs = image_xscale;\n{i}if (IsType(id, 17))\n{i}{{\n{i}    _hldTrailXs = xs;\n{i}}}\n"
            f"{i}hld_native_trail({args[0]}, {args[1]}, {args[2]}, {glowing}, _hldTrailXs, z, global.h_Luma);")


def gml_source_rewrite(name, text):
    """The bullet step's stand-alone trail call (its result unused) becomes the native trail builtin."""
    if name not in _TRAIL_SCRIPTS or __import__("os").environ.get("HLDC_NATIVE_TRAILS", "1") != "1":
        return text
    return _TRAIL_CALL.sub(_native_trail_call, text)
