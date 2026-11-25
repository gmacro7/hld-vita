"""Native-only mode: the engine runs, but no GML bytecode does.

VM_executeCode looks the code entry up in a table of native C handlers. Entries without a handler return
undefined without running, and are counted, so a native-only run executes only C. The loop enters the
requested room directly with room_goto's mechanism instead of the GML launch scripts.

The mode is switched on by HLD_NATIVE_ONLY=<room> on the host or ux0:data/hld-vm/native-only (holding the
room name) on the Vita. Without it the runtime is unchanged.
"""

from runtime_verification_source import replace_once

SIGNATURE = ("RValue VM_executeCode(VMContext* ctx, int32_t codeIndex, Instance* selfInstance, Instance* otherInstance, "
             "int32_t eventObjectIndex, int32_t eventType, int32_t eventSubtype, RValue* args, int32_t argCount) {")

VM_NATIVE = r'''
typedef RValue (*HldNativeHandler)(VMContext* ctx, Instance* self, Instance* other, RValue* args, int32_t argCount);
typedef struct { const char* name; HldNativeHandler handler; } HldNativeEntry;

static int32_t hldSpriteIndex(VMContext* ctx, const char* name) {
    repeat(ctx->dataWin->sprt.count, i) {
        if (strcmp(ctx->dataWin->sprt.sprites[i].name, name) == 0) return (int32_t) i;
    }
    return -1;
}

static void hldDrawSprite(VMContext* ctx, int32_t sprite, GMLReal frame, GMLReal x, GMLReal y, GMLReal scale) {
    if (ctx->runner == nullptr || ctx->runner->renderer == nullptr) return;
    Renderer_drawSpriteExt(ctx->runner->renderer, sprite, (int32_t) frame, (float) x, (float) y, (float) scale,
                           (float) scale, 0, 0xffffff, 1);
}

// Rung 1: one animated sprite, drawn in SCREEN's GUI event.
MAYBE_UNUSED static RValue hldNativeScreenDrawGui(VMContext* ctx, Instance* self, Instance* other, RValue* args, int32_t argCount) {
    (void) self; (void) other; (void) args; (void) argCount;
    static int32_t sprite = -2;
    if (sprite == -2) sprite = hldSpriteIndex(ctx, "spr_DirkIdle");
    if (sprite < 0) return RValue_makeUndefined();
    const int32_t frame = ctx->runner != nullptr ? ctx->runner->frameCount : 0;
    hldDrawSprite(ctx, sprite, frame / 4, 240, 150, 4);
    return RValue_makeUndefined();
}

// ===[ Rung 2: the static level ]===
// LevelLoad reads <region>/<room>.lvl. Its "lb" line holds the room settings as name,value pairs; the
// floor image is <region>/<room>_layer1_sketch_baked.png.
// ===[ Rung 3: scenery as plain structs ]===
// Scenery User Event 15 reads 0=sprite 1=image_index 2=imgspd 3=animated k=sticker p=stickerParent fp=flip
// 4=bg 5=fg f=flr l=lvl. SCREEN End Step culls against the view grown by 80 and files visible flr/bg/fg
// scenery into per-level lists; the rest draws with the other instances, ordered by depth = -y / roomHei.
enum { HLD_SC_FLR0, HLD_SC_FLR05, HLD_SC_FLR1, HLD_SC_BG0, HLD_SC_BG1, HLD_SC_DEPTH, HLD_SC_FG0, HLD_SC_FG1,
       HLD_SC_NONE, HLD_SC_LISTS = HLD_SC_NONE };
typedef struct {
    float x, y, x1, y1, x2, y2;   // position and culling bounds
    float frame, speed;
    int16_t sprite;
    int8_t xscale;
    uint8_t list, animated, frames, collides;
    int16_t recess;               // index into hldRecess, or -1
} HldScenery;

// ===[ Rung 5: the case system and RecessingScenery ]===
// Level objects with "caseScript,<case>,<check>,<arg>,<delay>" before "++" take part in CaseCheck. Links are
// "<count>,<cid>..." after the layer field ("-999999" for none).
enum { HLD_CASE_NULL, HLD_CASE_NOOBJECTS, HLD_CASE_TOUCHOBJECT, HLD_CASE_LINKCASEREACT, HLD_CASE_LINKBROKEN };
typedef struct {
    int32_t cid;
    char object[32];
    float x, y, wid, hei;
    int32_t* links;               // stb_ds array of case node indices (-1 for links to unknown cids)
    int32_t* linkCids;            // stb_ds array, resolved into links after loading
    int caseScript;
    GMLReal caseCheck, caseArg, caseDelay, caseTime;
    char caseArgName[32];         // object name argument (TOUCHOBJECT/NOOBJECTS), or ""
    int caseState, caseDelayDir;
    int32_t recess;               // HldRecess index for RecessingScenery, or -1
    bool lockedTrue;              // OneTimeTrue latch
} HldCaseNode;
typedef struct { int32_t key; int32_t value; } HldCidSlot;
static HldCaseNode* hldCases;
static HldCidSlot* hldCaseByCid;
static int hldRoomT;

typedef struct {
    int32_t caseNode;
    Instance* proxy;
    GMLReal height, maxHeight, hSpeed, speedMod, hideT;
    int caseCheckStore;
    float wid, yOffset;
    bool masked;
} HldRecess;
static HldRecess* hldRecess;

typedef struct {
    HldScenery* items;            // stb_ds array, sorted by list then draw order
    uint32_t start[HLD_SC_LISTS + 1];
    uint32_t visible, drawn;
} HldSceneryTable;
static HldSceneryTable hldScenery;

typedef struct { char* key; int32_t value; } HldSpriteName;
static HldSpriteName* hldSpriteNames;
static const VMContext* hldSpriteNamesContext;

static int32_t hldSpriteByName(VMContext* ctx, const char* name) {
    if (hldSpriteNamesContext != ctx) {
        shfree(hldSpriteNames);
        hldSpriteNames = nullptr;
        sh_new_arena(hldSpriteNames);
        repeat(ctx->dataWin->sprt.count, i) shput(hldSpriteNames, (char*) ctx->dataWin->sprt.sprites[i].name, (int32_t) i);
        hldSpriteNamesContext = ctx;
    }
    ptrdiff_t slot = shgeti(hldSpriteNames, (char*) name);
    return slot < 0 ? -1 : hldSpriteNames[slot].value;
}

// Sprites that Scenery's User Event 0 replaces with another object; those objects are later rungs.
static const char* const hldSceneryReplaced[] = {
    "spr_G_HyperLightPlant", "spr_ScnGauntlet_grass", "spr_WCrystalClean", "spr_WCrystalRoughSm",
    "spr_WCrystalRoughTall", "spr_WGeodeBig", "spr_WGeodeBig1", "spr_WGeodeBig2", "spr_WCrystalTrap",
    "spr_WCrystalTrap2", "spr_WCrystalTrap3", "spr_WCrystalTrap4", "spr_N_Soc_block32", "spr_N_Soc_block32b",
    "spr_N_Soc_block32c", "spr_N_Soc_block48", "spr_N_Soc_block64", "spr_WLabBlock16", "spr_WLabBlock16b",
    "spr_WLabBlock16c", "spr_WLabBlock48Fat", "spr_WLabBlock64", "spr_EBlock16", "spr_EBlock16b", "spr_EBlock16c",
    "spr_EBlock48Fat", "spr_EBlock48Thin", "spr_EBlock64", "spr_ScnLabLightsFloor", "spr_ScnGauntlet_FloorLightsTwo",
};
static const char* const hldScenerySnow[] = {
    "spr_N_LedgeSnow", "spr_N_SnowOverhang", "spr_N_SnowPatchSm", "spr_N_SnowPatchMed", "spr_N_SnowPatchMed_flat",
    "spr_N_SnowPatchLrg",
};
static const char* const hldSceneryFloor[] = {
    "spr_E_DocksHoriz_Front", "spr_E_DocksVert_Front", "spr_E_DocksVert", "spr_E_DocksHoriz",
    "spr_ScnGauntlet_CeilingLight", "spr_ScnGauntlet_mush",
};

static bool hldNameIn(const char* name, const char* const* list, size_t count) {
    repeat(count, i) if (strcmp(name, list[i]) == 0) return true;
    return false;
}

// Reads "key=value," pairs after "++," in one level object line.
static bool hldObjectField(const char* line, const char* key, char* out, size_t outSize) {
    const char* fields = strstr(line, ",++,");
    if (fields == nullptr) return false;
    const size_t keyLength = strlen(key);
    for (const char* field = fields + 4; *field != 0 && *field != '\n' && *field != '\r'; ) {
        const char* end = field;
        while (*end != ',' && *end != 0 && *end != '\n' && *end != '\r') ++end;
        if ((size_t)(end - field) > keyLength && field[keyLength] == '=' && memcmp(field, key, keyLength) == 0) {
            size_t length = (size_t)(end - field) - keyLength - 1;
            if (length >= outSize) length = outSize - 1;
            memcpy(out, field + keyLength + 1, length);
            out[length] = 0;
            return true;
        }
        if (*end != ',') break;
        field = end + 1;
    }
    return false;
}

static double hldObjectNumber(const char* line, const char* key, double fallback) {
    char value[64];
    return hldObjectField(line, key, value, sizeof(value)) ? atof(value) : fallback;
}

static int hldSceneryOrder(const void* a, const void* b) {
    const HldScenery* left = a;
    const HldScenery* right = b;
    if (left->list != right->list) return left->list < right->list ? -1 : 1;
    // Depth-sorted scenery: larger depth (smaller y) draws first.
    if (left->list == HLD_SC_DEPTH && left->y != right->y) return left->y < right->y ? -1 : 1;
    return 0;
}

// Reads one level object line's id, position, links and case header. Returns false for malformed lines.
static bool hldParseCaseNode(const char* line, HldCaseNode* node) {
    *node = (HldCaseNode){.recess = -1};
    char object[32];
    int consumed = 0;
    if (sscanf(line, "obj,%31[^,],%d,%f,%f,%*[^,],%n", object, &node->cid, &node->x, &node->y, &consumed) != 4 ||
        consumed == 0) return false;
    snprintf(node->object, sizeof(node->object), "%s", object);
    const char* field = line + consumed;
    int links = (int) strtol(field, nullptr, 10);
    field = strchr(field, ',');
    if (field == nullptr) return false;
    ++field;
    if (links > 0 && links < 1000) {
        repeat(links, i) {
            arrput(node->linkCids, (int32_t) strtol(field, nullptr, 10));
            field = strchr(field, ',');
            if (field == nullptr) return false;
            ++field;
        }
    }
    if (strncmp(field, "caseScript,", 11) == 0) {
        field += 11;
        char arg[32] = {0};
        double script = 0, check = 0, delay = 0;
        if (sscanf(field, "%lf,%lf,%31[^,],%lf", &script, &check, arg, &delay) == 4) {
            node->caseScript = (int) script;
            node->caseCheck = check;
            node->caseDelay = delay;
            char* end;
            node->caseArg = strtod(arg, &end);
            if (*end != 0) {
                snprintf(node->caseArgName, sizeof(node->caseArgName), "%s", arg);
                node->caseArg = 0;
            }
        }
    }
    node->wid = (float) hldObjectNumber(line, "0", 0);
    node->hei = (float) hldObjectNumber(line, "1", 0);
    return true;
}

static void hldLoadCases(const char* text) {
    repeat(arrlen(hldCases), i) {
        arrfree(hldCases[i].links);
        arrfree(hldCases[i].linkCids);
    }
    arrfree(hldCases);
    hmfree(hldCaseByCid);
    hldCases = nullptr;
    hldCaseByCid = nullptr;
    for (const char* line = strstr(text, "obj,"); line != nullptr; line = strstr(line + 1, "\n     obj,")) {
        if (*line == '\n') line += 6;
        HldCaseNode node;
        if (!hldParseCaseNode(line, &node)) continue;
        hmput(hldCaseByCid, node.cid, (int32_t) arrlen(hldCases));
        arrput(hldCases, node);
    }
    repeat(arrlen(hldCases), i) {
        HldCaseNode* node = &hldCases[i];
        repeat(arrlen(node->linkCids), j) {
            const ptrdiff_t slot = hmgeti(hldCaseByCid, node->linkCids[j]);
            arrput(node->links, slot < 0 ? -1 : hldCaseByCid[slot].value);
        }
    }
}

static int32_t hldCaseNodeFor(int32_t cid) {
    const ptrdiff_t slot = hmgeti(hldCaseByCid, cid);
    return slot < 0 ? -1 : hldCaseByCid[slot].value;
}

static void hldLoadScenery(VMContext* ctx, const char* text, float roomHeight) {
    arrfree(hldScenery.items);
    hldScenery = (HldSceneryTable){0};
    arrfree(hldRecess);
    hldRecess = nullptr;
    hldLoadCases(text);
    unsigned skipped = 0;
    for (const char* line = strstr(text, "obj,Scenery,"); line != nullptr; line = strstr(line + 1, "obj,Scenery,")) {
        int id;
        float x, y;
        if (sscanf(line, "obj,Scenery,%d,%f,%f,", &id, &x, &y) != 3) continue;
        char name[96];
        if (!hldObjectField(line, "0", name, sizeof(name))) continue;
        const int32_t sprite = hldSpriteByName(ctx, name);
        if (sprite < 0) continue;
        bool flr = hldObjectNumber(line, "f", 0) != 0;
        const bool bg = hldObjectNumber(line, "4", 0) != 0, fg = hldObjectNumber(line, "5", 0) != 0;
        double lvl = hldObjectNumber(line, "l", 0);
        if (!fg && lvl > 1) lvl = 1;
        if (hldNameIn(name, hldSceneryReplaced, sizeof(hldSceneryReplaced) / sizeof(*hldSceneryReplaced)) ||
            (flr && hldNameIn(name, hldScenerySnow, sizeof(hldScenerySnow) / sizeof(*hldScenerySnow)))) {
            ++skipped;
            continue;
        }
        if (hldNameIn(name, hldSceneryFloor, sizeof(hldSceneryFloor) / sizeof(*hldSceneryFloor))) flr = true;

        const Sprite* info = &ctx->dataWin->sprt.sprites[sprite];
        HldScenery item = {.x = x, .y = y, .sprite = (int16_t) sprite,
                           .xscale = hldObjectNumber(line, "fp", 0) != 0 ? -1 : 1,
                           .frame = (float) hldObjectNumber(line, "1", 0), .speed = (float) hldObjectNumber(line, "2", 0),
                           .animated = hldObjectNumber(line, "3", 0) != 0,
                           .frames = (uint8_t) (info->textureCount > 255 ? 255 : info->textureCount),
                           .recess = -1};
        const float left = x - info->originX * item.xscale, right = left + info->width * item.xscale;
        item.x1 = left < right ? left : right;
        item.x2 = left < right ? right : left;
        item.y1 = y - info->originY;
        item.y2 = item.y1 + info->height;
        if (flr) item.list = lvl == 0 ? HLD_SC_FLR0 : lvl == 1 ? HLD_SC_FLR1 : lvl == 0.5 ? HLD_SC_FLR05 : HLD_SC_NONE;
        else if (bg) item.list = lvl == 0 ? HLD_SC_BG0 : lvl == 1 ? HLD_SC_BG1 : HLD_SC_NONE;
        else if (fg) item.list = lvl == 0 ? HLD_SC_FG0 : lvl == 1 ? HLD_SC_FG1 : HLD_SC_NONE;
        else item.list = HLD_SC_DEPTH;
        // Scenery User Event 1: only plain (depth-sorted, non-sticker) scenery keeps its sprite as a block mask.
        item.collides = item.list == HLD_SC_DEPTH && hldObjectNumber(line, "k", 0) == 0;
        if (item.list == HLD_SC_NONE) {
            ++skipped;
            continue;
        }
        arrput(hldScenery.items, item);
    }
    (void) roomHeight;
    // RecessingScenery: Create sets wid = sprite width and maxHeight = sprite_height - wid; it draws with plain
    // scenery by depth and collides through its sprite while raised.
    for (const char* line = strstr(text, "obj,RecessingScenery,"); line != nullptr;
         line = strstr(line + 1, "obj,RecessingScenery,")) {
        HldCaseNode node;
        char name[96];
        if (!hldParseCaseNode(line, &node) || !hldObjectField(line, "0", name, sizeof(name))) continue;
        arrfree(node.linkCids);
        const int32_t sprite = hldSpriteByName(ctx, name);
        if (sprite < 0) continue;
        const Sprite* info = &ctx->dataWin->sprt.sprites[sprite];
        HldRecess recess = {.caseNode = hldCaseNodeFor(node.cid), .wid = (float) info->width,
                            .maxHeight = (GMLReal) info->height - info->width, .hideT = -1, .yOffset = (float) info->originY,
                            // Create draws speedMod = 0.25 + random(0.75); the RNG is not native yet.
                            .speedMod = 0.625};
        recess.height = recess.maxHeight;
        HldScenery item = {.x = node.x, .y = node.y, .sprite = (int16_t) sprite, .xscale = 1,
                           .frame = (float) hldObjectNumber(line, "1", 0), .list = HLD_SC_DEPTH, .collides = 0,
                           .frames = (uint8_t) info->textureCount, .recess = (int16_t) arrlen(hldRecess)};
        item.x1 = node.x - info->originX;
        item.x2 = item.x1 + info->width;
        item.y1 = node.y - info->originY;
        item.y2 = item.y1 + info->height;
        if (recess.caseNode >= 0) hldCases[recess.caseNode].recess = (int32_t) arrlen(hldRecess);
        arrput(hldRecess, recess);
        arrput(hldScenery.items, item);
    }
    const uint32_t count = (uint32_t) arrlen(hldScenery.items);
    qsort(hldScenery.items, count, sizeof(HldScenery), hldSceneryOrder);
    uint32_t list = 0;
    for (uint32_t i = 0; i <= count; ++i) {
        const uint32_t current = i < count ? hldScenery.items[i].list : HLD_SC_LISTS;
        while (list <= current && list <= HLD_SC_LISTS) hldScenery.start[list++] = i;
    }
    logInfo("HLD native: %u scenery structs (%u bytes each), %u recessing blocks, %u case nodes, %u left to later rungs\n",
            count, (unsigned) sizeof(HldScenery), (unsigned) arrlen(hldRecess), (unsigned) arrlen(hldCases), skipped);
}

static void hldDrawPlayer(VMContext* ctx);
static void hldDrawRecess(VMContext* ctx, const HldScenery* item, const HldRecess* recess);

static void hldDrawSceneryList(VMContext* ctx, unsigned list, float vx1, float vy1, float vx2, float vy2,
                               float playerY, bool* playerDrawn) {
    Renderer* renderer = ctx->runner->renderer;
    for (uint32_t i = hldScenery.start[list]; i < hldScenery.start[list + 1]; ++i) {
        HldScenery* item = &hldScenery.items[i];
        // The player sorts with plain scenery by depth = -y / roomHei.
        if (list == HLD_SC_DEPTH && !*playerDrawn && item->y > playerY) {
            hldDrawPlayer(ctx);
            *playerDrawn = true;
        }
        if (!(item->x2 > vx1 && item->x1 < vx2 && item->y2 > vy1 && item->y1 < vy2)) continue;
        ++hldScenery.visible;
        if (item->recess >= 0) {
            hldDrawRecess(ctx, item, &hldRecess[item->recess]);
            ++hldScenery.drawn;
            continue;
        }
        Renderer_drawSpriteExt(renderer, item->sprite, (int32_t) item->frame, item->x, item->y, item->xscale, 1, 0,
                               0xffffff, 1);
        ++hldScenery.drawn;
    }
}

static void hldStepScenery(void) {
    for (uint32_t i = 0; i < hldScenery.start[HLD_SC_LISTS]; ++i) {
        HldScenery* item = &hldScenery.items[i];
        if (!item->animated || item->frames == 0) continue;
        item->frame += item->speed;
        if (item->frame >= item->frames) item->frame -= item->frames;
    }
}

typedef struct {
    bool loaded;
    int32_t floorSprite, wallSprite, overlaySprite, hardLightSprite, dodgeSprite, patternSprite;
    bool wallsOnTop;
    int32_t width, height;
    GMLReal viewX, viewY, brightness;
} HldLevel;
static HldLevel hldLevel;

static const char* const hldRegions[] = {"Intro", "Central", "East", "North", "West", "South", "Abyss", "Challenges"};

static bool hldReadLevelFile(VMContext* ctx, const char* room, char* region, size_t regionSize, char** text) {
    FileSystem* fs = ctx->runner->fileSystem;
    repeat(sizeof(hldRegions) / sizeof(*hldRegions), i) {
        char path[256];
        snprintf(path, sizeof(path), "%s/%s.lvl", hldRegions[i], room);
        uint8_t* bytes = nullptr;
        int32_t size = 0;
        if (fs->vtable->readFileBinary(fs, path, &bytes, &size) && bytes != nullptr) {
            *text = safeMalloc((size_t) size + 1);
            memcpy(*text, bytes, (size_t) size);
            (*text)[size] = 0;
            free(bytes);
            snprintf(region, regionSize, "%s", hldRegions[i]);
            return true;
        }
    }
    return false;
}

// Returns the value after "<key>," in the comma-separated settings line, copied into out.
static bool hldSetting(const char* line, const char* key, char* out, size_t outSize) {
    const size_t keyLength = strlen(key);
    for (const char* field = line; field != nullptr && *field != 0; ) {
        const char* comma = strchr(field, ',');
        if (comma == nullptr) return false;
        if ((size_t)(comma - field) == keyLength && memcmp(field, key, keyLength) == 0) {
            const char* value = comma + 1;
            const char* end = strchr(value, ',');
            size_t length = end != nullptr ? (size_t)(end - value) : strlen(value);
            if (length >= outSize) length = outSize - 1;
            memcpy(out, value, length);
            out[length] = 0;
            return true;
        }
        field = comma + 1;
    }
    return false;
}

static RValue hldCallBuiltin(VMContext* ctx, const char* name, RValue* args, int32_t argCount) {
    BuiltinFunc builtin = VM_findBuiltin(ctx, name);
    if (builtin == nullptr) {
        logWarn("HLD native: missing builtin %s\n", name);
        return RValue_makeUndefined();
    }
    return builtin(ctx, args, argCount);
}

static void hldSetBuiltin(VMContext* ctx, int16_t id, const char* name, GMLReal value, int32_t index) {
    VMBuiltins_setVariable(ctx, nullptr, id, name, RValue_makeReal(value), index);
}

static void hldPlaceView(VMContext* ctx, GMLReal centerX, GMLReal centerY) {
    const GMLReal width = 480, height = 270;
    GMLReal x = centerX - width / 2, y = centerY - height / 2;
    if (x > hldLevel.width - width) x = hldLevel.width - width;
    if (y > hldLevel.height - height) y = hldLevel.height - height;
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    hldLevel.viewX = x;
    hldLevel.viewY = y;
    hldSetBuiltin(ctx, BUILTIN_VAR_VIEW_XVIEW, "view_xview", x, 0);
    hldSetBuiltin(ctx, BUILTIN_VAR_VIEW_YVIEW, "view_yview", y, 0);
}

static GMLReal hldNumberOf(RValue value) {
    const GMLReal number = RValue_toReal(value);
    RValue_free(&value);
    return number;
}

// ===[ Presentation: shd_ScreenOverlays ]===
// SetOverlaysFromOverlay: the hard light and dodge images share the overlay's base name.
static int32_t hldOverlayPart(VMContext* ctx, const char* base, const char* const* suffixes, size_t count) {
    repeat(count, i) {
        char name[160];
        snprintf(name, sizeof(name), "%s%s", base, suffixes[i]);
        const int32_t sprite = hldSpriteByName(ctx, name);
        if (sprite >= 0) return sprite;
    }
    return -1;
}

static void hldFindOverlays(VMContext* ctx) {
    hldLevel.hardLightSprite = hldLevel.dodgeSprite = -1;
    if (hldLevel.overlaySprite < 0) return;
    char base[160];
    snprintf(base, sizeof(base), "%s", ctx->dataWin->sprt.sprites[hldLevel.overlaySprite].name);
    for (char* c = base; *c != 0; ++c) {
        if (strncasecmp(c, "_overlay", 8) == 0) {
            *c = 0;
            break;
        }
    }
    static const char* const hard[] = {"_HardLight", "_Hardlight", "_hardlight"};
    static const char* const dodge[] = {"_Dodge", "_dodge"};
    hldLevel.hardLightSprite = hldOverlayPart(ctx, base, hard, 3);
    hldLevel.dodgeSprite = hldOverlayPart(ctx, base, dodge, 2);
}

typedef struct {
    const VMContext* context;
    int32_t shader;
    GMLReal viewPos, sizeRatio, brightness, overlayUvs, hardUvs, dodgeUvs, carve, bloom, pulse;
    GMLReal overlaySampler, hardSampler, dodgeSampler, charSampler;
} HldOverlayShader;
static HldOverlayShader hldOverlay;

static GMLReal hldShaderHandle(VMContext* ctx, const char* function, const char* name) {
    RValue args[] = {RValue_makeReal(hldOverlay.shader), RValue_makeString(name)};
    return hldNumberOf(hldCallBuiltin(ctx, function, args, 2));
}

static void hldBindOverlayShader(VMContext* ctx) {
    if (hldOverlay.context == ctx) return;
    hldOverlay = (HldOverlayShader){.context = ctx, .shader = -1};
    repeat(ctx->dataWin->shdr.count, i) {
        if (strcmp(ctx->dataWin->shdr.shaders[i].name, "shd_ScreenOverlays") == 0) hldOverlay.shader = (int32_t) i;
    }
    if (hldOverlay.shader < 0) return;
    hldOverlay.overlaySampler = hldShaderHandle(ctx, "shader_get_sampler_index", "in_Overlay");
    hldOverlay.hardSampler = hldShaderHandle(ctx, "shader_get_sampler_index", "in_HardLight");
    hldOverlay.dodgeSampler = hldShaderHandle(ctx, "shader_get_sampler_index", "in_Dodge");
    hldOverlay.charSampler = hldShaderHandle(ctx, "shader_get_sampler_index", "in_Char");
    hldOverlay.carve = hldShaderHandle(ctx, "shader_get_uniform", "in_carveChars");
    hldOverlay.viewPos = hldShaderHandle(ctx, "shader_get_uniform", "in_viewPos");
    hldOverlay.sizeRatio = hldShaderHandle(ctx, "shader_get_uniform", "in_viewSizeRatio");
    hldOverlay.overlayUvs = hldShaderHandle(ctx, "shader_get_uniform", "in_overlayTextureStartEnd");
    hldOverlay.hardUvs = hldShaderHandle(ctx, "shader_get_uniform", "in_hlTextureStartEnd");
    hldOverlay.dodgeUvs = hldShaderHandle(ctx, "shader_get_uniform", "in_dodgeTextureStartEnd");
    hldOverlay.bloom = hldShaderHandle(ctx, "shader_get_uniform", "in_bloom");
    hldOverlay.pulse = hldShaderHandle(ctx, "shader_get_uniform", "in_pulse");
    hldOverlay.brightness = hldShaderHandle(ctx, "shader_get_uniform", "in_overlaysBrightness");
}

static void hldUniform(VMContext* ctx, GMLReal handle, int count, GMLReal a, GMLReal b, GMLReal c, GMLReal d) {
    RValue args[] = {RValue_makeReal(handle), RValue_makeReal(a), RValue_makeReal(b), RValue_makeReal(c), RValue_makeReal(d)};
    RValue_free((RValue[]){hldCallBuiltin(ctx, "shader_set_uniform_f", args, count + 1)});
}

// Binds one overlay image to its sampler and texture-rectangle uniform, or clears both (OverlaySetup).
static void hldOverlayTexture(VMContext* ctx, GMLReal sampler, GMLReal uvs, int32_t sprite) {
    RValue stage[2] = {RValue_makeReal(sampler), RValue_makeReal(-1)};
    if (sprite < 0) {
        RValue_free((RValue[]){hldCallBuiltin(ctx, "texture_set_stage", stage, 2)});
        hldUniform(ctx, uvs, 4, 0, 0, 0, 0);
        return;
    }
    RValue spriteArgs[] = {RValue_makeReal(sprite), RValue_makeReal(0)};
    RValue rect = hldCallBuiltin(ctx, "sprite_get_uvs", spriteArgs, 2);
    GMLReal r[4] = {0};
    if (rect.type == RVALUE_ARRAY) repeat(4, i) r[i] = RValue_toReal(GMLArray_get(rect.array, (int32_t) i));
    RValue_free(&rect);
    stage[1] = hldCallBuiltin(ctx, "sprite_get_texture", spriteArgs, 2);
    RValue_free((RValue[]){hldCallBuiltin(ctx, "texture_set_stage", stage, 2)});
    RValue_free(&stage[1]);
    hldUniform(ctx, uvs, 4, r[0], r[1], r[2], r[3]);
}

// SCREEN Post Draw (ScreenSurfDraw): the application surface, drawn through shd_ScreenOverlays.
static RValue hldNativeScreenPostDraw(VMContext* ctx, Instance* self, Instance* other, RValue* args, int32_t argCount) {
    (void) self; (void) other; (void) args; (void) argCount;
    if (!hldLevel.loaded) return RValue_makeUndefined();
    hldBindOverlayShader(ctx);
    RValue surface = VMBuiltins_getVariable(ctx, nullptr, BUILTIN_VAR_APPLICATION_SURFACE, "application_surface", -1);
    const GMLReal guiWidth = hldNumberOf(hldCallBuiltin(ctx, "window_get_width", nullptr, 0));
    RValue widthArgs[] = {surface};
    const GMLReal surfaceWidth = hldNumberOf(hldCallBuiltin(ctx, "surface_get_width", widthArgs, 1));
    const bool shade = hldOverlay.shader >= 0 && (hldLevel.overlaySprite >= 0 || hldLevel.hardLightSprite >= 0 ||
                                                  hldLevel.dodgeSprite >= 0 || hldLevel.brightness < 1);
    if (shade) {
        const Sprite* bg = &ctx->dataWin->sprt.sprites[hldLevel.overlaySprite >= 0 ? hldLevel.overlaySprite : 0];
        RValue shader = RValue_makeReal(hldOverlay.shader);
        RValue_free((RValue[]){hldCallBuiltin(ctx, "shader_set", &shader, 1)});
        hldUniform(ctx, hldOverlay.viewPos, 2, hldLevel.viewX / bg->width, hldLevel.viewY / bg->height, 0, 0);
        hldUniform(ctx, hldOverlay.sizeRatio, 2, 480.0 / bg->width, 270.0 / bg->height, 0, 0);
        hldUniform(ctx, hldOverlay.brightness, 1, hldLevel.brightness, 0, 0, 0);
        hldOverlayTexture(ctx, hldOverlay.overlaySampler, hldOverlay.overlayUvs, hldLevel.overlaySprite);
        hldOverlayTexture(ctx, hldOverlay.hardSampler, hldOverlay.hardUvs, hldLevel.hardLightSprite);
        hldOverlayTexture(ctx, hldOverlay.dodgeSampler, hldOverlay.dodgeUvs, hldLevel.dodgeSprite);
        RValue stage[] = {RValue_makeReal(hldOverlay.charSampler), RValue_makeReal(-1)};
        RValue_free((RValue[]){hldCallBuiltin(ctx, "texture_set_stage", stage, 2)});
        hldUniform(ctx, hldOverlay.carve, 1, 0, 0, 0, 0);
        hldUniform(ctx, hldOverlay.bloom, 1, 0, 0, 0, 0);
        hldUniform(ctx, hldOverlay.pulse, 1, 0, 0, 0, 0);
    }
    // bm_one, bm_inv_src_alpha
    RValue blend[] = {RValue_makeReal(2), RValue_makeReal(6)};
    RValue_free((RValue[]){hldCallBuiltin(ctx, "draw_set_blend_mode_ext", blend, 2)});
    const GMLReal scale = surfaceWidth > 0 ? guiWidth / surfaceWidth : 1;
    static bool reported;
    if (!reported) {
        reported = true;
        logInfo("HLD native overlay shader=%d samplers %g %g %g uniforms viewPos=%g ratio=%g bright=%g gui=%g surface=%g\n",
                hldOverlay.shader, (double) hldOverlay.overlaySampler, (double) hldOverlay.hardSampler,
                (double) hldOverlay.dodgeSampler, (double) hldOverlay.viewPos, (double) hldOverlay.sizeRatio,
                (double) hldOverlay.brightness, (double) guiWidth, (double) surfaceWidth);
    }
    RValue draw[] = {surface, RValue_makeReal(0), RValue_makeReal(0), RValue_makeReal(scale), RValue_makeReal(scale),
                     RValue_makeReal(0), RValue_makeReal(0xffffff), RValue_makeReal(1)};
    RValue_free((RValue[]){hldCallBuiltin(ctx, "draw_surface_ext", draw, 8)});
    RValue_free((RValue[]){hldCallBuiltin(ctx, "shader_reset", nullptr, 0)});
    RValue normal = RValue_makeReal(0);
    RValue_free((RValue[]){hldCallBuiltin(ctx, "draw_set_blend_mode", &normal, 1)});
    return RValue_makeUndefined();
}

// ===[ Rung 4: the player (walking) ]===
// char Step -> Char_Control -> Char_GetInput (keyboard), Char_BasicMovement, MantleMovementReturn and the
// walk/stand sprite choice. Exact GML math goes through the engine's own builtins; comparisons use the
// GML epsilon like handleCmp.
typedef struct {
    Instance* inst;
    GMLReal xc, yc, xspd, yspd, dir, inputDir, freeze, imgspd, image, xs, hpos, vpos, jdis;
    int32_t sprite;
} HldPlayer;
static HldPlayer hldPlayer;
static int32_t hldBlockObject = -1, hldPlatformObject = -1, hldCharObject = -1;

static int32_t hldObjectIndex(VMContext* ctx, const char* name) {
    repeat(ctx->dataWin->objt.count, i) if (strcmp(ctx->dataWin->objt.objects[i].name, name) == 0) return (int32_t) i;
    return -1;
}

static GMLReal hldBuiltinReal(VMContext* ctx, const char* name, int32_t count, GMLReal a, GMLReal b, GMLReal c, GMLReal d) {
    RValue args[] = {RValue_makeReal(a), RValue_makeReal(b), RValue_makeReal(c), RValue_makeReal(d)};
    return hldNumberOf(hldCallBuiltin(ctx, name, args, count));
}
#define hldPointDirection(ctx, x1, y1, x2, y2) hldBuiltinReal(ctx, "point_direction", 4, x1, y1, x2, y2)
#define hldPointDistance(ctx, x1, y1, x2, y2) hldBuiltinReal(ctx, "point_distance", 4, x1, y1, x2, y2)
#define hldLengthdirX(ctx, len, dir) hldBuiltinReal(ctx, "lengthdir_x", 2, len, dir, 0, 0)
#define hldLengthdirY(ctx, len, dir) hldBuiltinReal(ctx, "lengthdir_y", 2, len, dir, 0, 0)
#define hldRound(ctx, value) hldBuiltinReal(ctx, "round", 1, value, 0, 0, 0)

static int hldCmp(VMContext* ctx, GMLReal a, GMLReal b) {
    const GMLReal diff = a - b;
    const GMLReal epsilon = ctx->runner != nullptr ? ctx->runner->mathEpsilon : GML_MATH_EPSILON;
    return GMLReal_fabs(diff) <= epsilon ? 0 : diff < 0 ? -1 : 1;
}

static GMLReal hldApproachValue(VMContext* ctx, GMLReal a, GMLReal b, GMLReal step) {
    if (hldCmp(ctx, a, b) < 0) return a + step < b ? a + step : b;
    return a - step > b ? a - step : b;
}

static GMLReal hldAngleBetween(VMContext* ctx, GMLReal from, GMLReal to) {
    GMLReal angle = to - from;
    while (hldCmp(ctx, angle, 180) >= 0) angle -= 360;
    while (hldCmp(ctx, angle, -180) < 0) angle += 360;
    return angle;
}

static Instance* hldCreateInstance(VMContext* ctx, GMLReal x, GMLReal y, int32_t object) {
    const int32_t id = (int32_t) hldBuiltinReal(ctx, "instance_create", 3, x, y, object, 0);
    return id >= 0 ? VM_findInstanceByTarget(ctx, id) : nullptr;
}

static bool hldPlaceMeeting(VMContext* ctx, GMLReal x, GMLReal y, int32_t object) {
    return hldBuiltinReal(ctx, "place_meeting", 3, x, y, object, 0) != 0;
}

static bool hldPositionMeeting(VMContext* ctx, GMLReal x, GMLReal y, int32_t object) {
    return hldBuiltinReal(ctx, "position_meeting", 3, x, y, object, 0) != 0;
}

static GMLReal hldSign(GMLReal value) { return value > 0 ? 1 : value < 0 ? -1 : 0; }

// MantleMovementReturn for the walking player: pixel steps against block, small slope steps onto SafePlatform.
static void hldMantleMovement(VMContext* ctx, HldPlayer* player, GMLReal dx, GMLReal dy) {
    Instance* inst = player->inst;
    player->xc += dx;
    player->yc += dy;
    const GMLReal h = hldRound(ctx, player->xc), v = hldRound(ctx, player->yc);
    player->xc -= h;
    player->yc -= v;
    for (int axis = 0; axis < 2; ++axis) {
        const GMLReal step = hldSign(axis == 0 ? dx : dy);
        const int repeats = (int) GMLReal_fabs(axis == 0 ? h : v);
        for (int i = 0; i < repeats; ++i) {
            const GMLReal sx = axis == 0 ? step : 0, sy = axis == 0 ? 0 : step;
            bool pass = !hldPlaceMeeting(ctx, inst->x + sx, inst->y + sy, hldBlockObject);
            bool progress = true;
            if (!pass) {
                // Slope offsets 1, -1, 2, -2 across the movement axis.
                for (int k = 1; k != 3; ) {
                    const GMLReal ox = axis == 0 ? sx : k, oy = axis == 0 ? k : sy;
                    if (!hldPlaceMeeting(ctx, inst->x + ox, inst->y + oy, hldBlockObject) &&
                        hldPositionMeeting(ctx, inst->x + ox, inst->y + oy, hldPlatformObject)) {
                        if (axis == 0) inst->y += (float) hldSign(k); else inst->x += (float) hldSign(k);
                        if (abs(k) > 1) progress = false;
                        break;
                    }
                    if (k < 0) k--;
                    k = -k;
                }
            }
            if (!pass && hldPlaceMeeting(ctx, inst->x, inst->y, hldBlockObject)) pass = true;
            if (pass && progress) {
                if (axis == 0) inst->x += (float) step; else inst->y += (float) step;
            } else if (!progress) {
                break;
            }
        }
    }
    inst->x = (float) hldRound(ctx, inst->x);
    inst->y = (float) hldRound(ctx, inst->y);
}

// sprite_angle: the facing quadrant picks the side, down or up sprite and the horizontal flip.
static void hldSpriteAngle(VMContext* ctx, HldPlayer* player, int32_t down, int32_t side, int32_t up) {
    GMLReal angle = GMLReal_floor(player->dir);
    while (angle < 0) angle += 360;
    while (angle >= 360) angle -= 360;
    angle = hldRound(ctx, angle / 90) * 90;
    player->xs = 1;
    if (angle == 0 || angle == 360) player->sprite = side;
    else if (angle == 180) { player->xs = -1; player->sprite = side; }
    else if (angle == 270) player->sprite = down;
    else if (angle == 90) player->sprite = up;
    else player->sprite = down;
}

static void hldPlayerStep(VMContext* ctx, HldPlayer* player) {
    Instance* inst = player->inst;
    const GMLReal maxspd = 3.5, accel = 0.7;
    // Animate(0), before the state code.
    player->image += player->imgspd;
    const int32_t frames = ctx->dataWin->sprt.sprites[player->sprite].textureCount;
    if (player->image > frames) player->image -= frames;

    // Char_GetInput. The joystick branch reads the left stick (the d-pad acts as a full stick on the Vita);
    // otherwise the keyboard branch (activate_controls reads W/A/S/D through kbmControlSet).
    const GMLReal deadzone = 0.3;
    RunnerGamepadState* pad = ctx->runner->gamepads;
    GMLReal hpos = 0, vpos = 0, jdis;
    bool joystick = false;
    if (pad != nullptr && RunnerGamepad_isConnected(pad, 0)) {
        hpos = RunnerGamepad_axisValue(pad, 0, GP_AXIS_LH);
        vpos = RunnerGamepad_axisValue(pad, 0, GP_AXIS_LV);
        if (hldPointDistance(ctx, 0, 0, hpos, vpos) < deadzone) {
            hpos = RunnerGamepad_buttonCheck(pad, 0, GP_PADR) ? 1 : RunnerGamepad_buttonCheck(pad, 0, GP_PADL) ? -1 : 0;
            vpos = RunnerGamepad_buttonCheck(pad, 0, GP_PADD) ? 1 : RunnerGamepad_buttonCheck(pad, 0, GP_PADU) ? -1 : 0;
        }
        joystick = hldPointDistance(ctx, 0, 0, hpos, vpos) >= deadzone;
    }
    if (joystick) {
        player->inputDir = hldPointDirection(ctx, 0, 0, hpos, vpos);
        player->dir = player->inputDir;
        hpos *= 1 + GMLReal_fabs(hpos);
        vpos *= 1 + GMLReal_fabs(vpos);
    } else {
        const bool* keys = ctx->runner->keyboard->keyDown;
        hpos = keys['D'] ? 1 : keys['A'] ? -1 : 0;
        vpos = keys['W'] ? -1 : keys['S'] ? 1 : 0;
        jdis = hldPointDistance(ctx, 0, 0, hpos, vpos);
        if (jdis > 0) {
            player->inputDir = hldPointDirection(ctx, 0, 0, hpos, vpos);
            player->dir = player->dir + hldAngleBetween(ctx, player->dir, player->inputDir) * 0.5;
        }
        if (player->xspd != 0) player->xs = hldSign(player->xspd);
    }
    hpos = hpos < -1 ? -1 : hpos > 1 ? 1 : hpos;
    vpos = vpos < -1 ? -1 : vpos > 1 ? 1 : vpos;
    hpos = hldLengthdirX(ctx, GMLReal_fabs(hpos), player->inputDir);
    vpos = hldLengthdirY(ctx, GMLReal_fabs(vpos), player->inputDir);
    jdis = hldPointDistance(ctx, 0, 0, hpos, vpos);

    player->freeze = hldApproachValue(ctx, player->freeze, 1, 0.5);

    // Char_BasicMovement (no ledge catch yet).
    player->xspd = hldApproachValue(ctx, player->xspd, maxspd * hpos, accel);
    player->yspd = hldApproachValue(ctx, player->yspd, maxspd * vpos, accel);
    const GMLReal friction = 1 + (1 - jdis);
    player->xspd /= friction;
    player->yspd /= friction;
    if (hldCmp(ctx, hldPointDistance(ctx, 0, 0, player->xspd, player->yspd), 0.05) < 0) player->xspd = player->yspd = 0;

    hldMantleMovement(ctx, player, player->xspd / player->freeze, player->yspd / player->freeze);
    const GMLReal movdis = hldPointDistance(ctx, 0, 0, player->xspd, player->yspd);

    if (movdis > 0) {
        if (hldCmp(ctx, movdis, 2.3333333333333335) > 0) {
            player->imgspd = movdis / 3.5 / 2;
            hldSpriteAngle(ctx, player, 824, 826, 825);
        } else {
            player->imgspd = movdis / 2.3333333333333335;
            hldSpriteAngle(ctx, player, 822, 823, 821);
        }
    } else {
        player->imgspd = 1;
        hldSpriteAngle(ctx, player, 827, 829, 830);
    }
    // Room border.
    if (inst->x < 5) inst->x = 5;
    if (inst->x > hldLevel.width - 5) inst->x = (float) (hldLevel.width - 5);
    if (inst->y < 5) inst->y = 5;
    if (inst->y > hldLevel.height - 5) inst->y = (float) (hldLevel.height - 5);

    // End of char Step: a finished animation (animdone, or floor(image_index) <= 0 when reversing) resets imgspd.
    const GMLReal imageNumber = ctx->dataWin->sprt.sprites[player->sprite].textureCount;
    if ((hldCmp(ctx, player->image, imageNumber - 1) >= 0 && hldCmp(ctx, player->imgspd, 0) > 0) ||
        (GMLReal_floor(player->image) <= 0 && hldCmp(ctx, player->imgspd, 0) < 0)) {
        if (player->image < 0) player->image = 0;
        player->imgspd = 1;
    }
    inst->spriteIndex = player->sprite;
    inst->imageIndex = (float) player->image;
}

static RValue hldNativeCharStep(VMContext* ctx, Instance* self, Instance* other, RValue* args, int32_t argCount) {
    (void) other; (void) args; (void) argCount;
    if (self == hldPlayer.inst && self != nullptr) hldPlayerStep(ctx, &hldPlayer);
    return RValue_makeUndefined();
}

static void hldDrawPlayer(VMContext* ctx) {
    if (hldPlayer.inst == nullptr) return;
    Renderer_drawSpriteExt(ctx->runner->renderer, hldPlayer.sprite, (int32_t) hldPlayer.image, hldPlayer.inst->x,
                           hldPlayer.inst->y, (float) hldPlayer.xs, 1, 0, 0xffffff, 1);
}

// CaseCheck, for case nodes whose linked objects are native or inert. Event user 10 (state 2) and editor
// link cleanup have no native targets yet.
static bool hldCaseTouches(VMContext* ctx, const HldCaseNode* node) {
    // CASE_TOUCHOBJECT on a Region: an instance of the argument object inside the region rectangle.
    if (strcmp(node->object, "Region") != 0 || strcmp(node->caseArgName, "char") != 0 || hldPlayer.inst == nullptr) return false;
    const GMLReal px = hldPlayer.inst->x, py = hldPlayer.inst->y;
    (void) ctx;
    return px >= node->x && px <= node->x + node->wid && py >= node->y && py <= node->y + node->hei;
}

static int hldCaseCheck(VMContext* ctx, HldCaseNode* node) {
    if (node->caseScript == HLD_CASE_NULL) return -1;
    if (node->caseState == 3) return 0;
    if (node->caseState == 1) {
        node->caseTime += strcmp(node->object, "BoomBox") == 0 ? 1.0 / 30 : 1;
        if (hldCmp(ctx, node->caseTime, node->caseDelay) >= 0) {
            node->caseState = node->caseDelayDir == 1 ? 2 : 0;
            return node->caseDelayDir == 1;
        }
        return node->caseDelayDir == 1 ? 0 : 1;
    }
    int ret = 0;
    const int links = (int) arrlen(node->links);
    switch (node->caseScript) {
        case HLD_CASE_NOOBJECTS:
            // Objects that are not native yet count as present.
            break;
        case HLD_CASE_TOUCHOBJECT:
            if (node->caseArgName[0] != 0 && hldCaseTouches(ctx, node)) ret = 1;
            break;
        case HLD_CASE_LINKCASEREACT:
            for (int i = 0; i < links; ++i) {
                const int32_t link = node->links[i];
                if (link < 0) continue;
                const int linkState = hldCases[link].caseState;
                if (node->caseArg >= 0) {
                    if (i == (int) node->caseArg && linkState == 2) { ret = 1; break; }
                } else if (node->caseArg == -1) {
                    if (linkState == 2) { ret = 1; break; }
                } else if (linkState == 2) {
                    ret = 1;
                } else {
                    ret = 0;
                    break;
                }
            }
            break;
        case HLD_CASE_LINKBROKEN:
            if (links > 0) {
                ret = 1;
                for (int i = 0; i < links; ++i) {
                    const bool exists = node->links[i] >= 0;
                    if (exists && (node->caseArg == -999999 || (node->caseArg >= 0 && i == (int) node->caseArg))) {
                        ret = 0;
                        break;
                    } else if (node->caseArg == -1) {
                        if (!exists) { ret = 1; break; }
                        ret = 0;
                    }
                }
            }
            break;
    }
    if (node->caseState == 0) {
        if (hldCmp(ctx, ret, node->caseCheck) == 0) {
            node->caseState = 1;
            node->caseDelayDir = 1;
            node->caseTime = 0;
            if (hldRoomT < 5 || hldCmp(ctx, node->caseDelay, 0) <= 0) {
                node->caseState = 2;
                return 1;
            }
        }
        return 0;
    }
    if (hldCmp(ctx, ret, node->caseCheck) != 0) {
        node->caseState = 1;
        node->caseDelayDir = -1;
        node->caseTime = 0;
        if (hldRoomT < 5 || hldCmp(ctx, node->caseDelay, 0) <= 0) {
            node->caseState = 0;
            return 0;
        }
    }
    return 1;
}

static int32_t hldNoneSprite = -1;

// RecessingScenery_Step: raise while the case holds, sink otherwise; the mask follows.
static void hldStepRecess(VMContext* ctx, HldRecess* recess) {
    const GMLReal brake = 5;
    HldCaseNode* node = recess->caseNode >= 0 ? &hldCases[recess->caseNode] : nullptr;
    recess->caseCheckStore = node != nullptr ? hldCaseCheck(ctx, node) : 0;
    if (hldRoomT <= 5) {
        if (recess->caseCheckStore) { recess->height = recess->maxHeight; recess->hideT = -4; }
        else { recess->height = 0; recess->hideT = 100; }
    }
    const bool raised = recess->caseCheckStore != 0;
    if (!(recess->height == 0 && !raised && !recess->masked && recess->hideT > 50) &&
        !(recess->height == recess->maxHeight && raised && recess->masked)) {
        if (raised && recess->hideT < 0 && recess->height != recess->maxHeight) {
            recess->hSpeed = hldApproachValue(ctx, recess->hSpeed, 5 * recess->speedMod, 0.4 * recess->speedMod * recess->speedMod);
            if (recess->height > recess->maxHeight - brake) {
                const GMLReal slow = hldApproachValue(ctx, recess->hSpeed, 1 * recess->speedMod, 4 * recess->speedMod);
                if (slow < recess->hSpeed) recess->hSpeed = slow;
            }
        } else if (recess->height != 0) {
            recess->hSpeed = hldApproachValue(ctx, recess->hSpeed, -4 * recess->speedMod, 0.3 * recess->speedMod);
            if (recess->height < brake) {
                const GMLReal slow = hldApproachValue(ctx, recess->hSpeed, -1 * recess->speedMod, 4 * recess->speedMod);
                if (slow > recess->hSpeed) recess->hSpeed = slow;
            }
        }
        GMLReal height = recess->height + recess->hSpeed;
        recess->height = height < 0 ? 0 : height > recess->maxHeight ? recess->maxHeight : height;
        if (raised && !recess->masked) recess->masked = true;
        else if (!raised && recess->height < 2 && recess->masked) recess->masked = false;
    }
    if (recess->proxy != nullptr) recess->proxy->maskIndex = recess->masked ? -1 : hldNoneSprite;
}

// DrawFloorPattern: the level's floor pattern tiled over a region, sampled from the same map position.
static void hldDrawFloorPattern(VMContext* ctx, float srcX, float srcY, float width, float height, float drawX, float drawY) {
    if (hldLevel.patternSprite < 0) return;
    Renderer* renderer = ctx->runner->renderer;
    const Sprite* pattern = &ctx->dataWin->sprt.sprites[hldLevel.patternSprite];
    const float spriteWidth = (float) pattern->width, spriteHeight = (float) pattern->height;
    const float startX = fmodf(srcX, spriteWidth), startY = fmodf(srcY, spriteHeight);
    float sx = startX, sy = startY, dx = drawX, dy = drawY, widthLeft = width, heightLeft = height;
    while (heightLeft > 0) {
        float w = spriteWidth - sx < widthLeft ? spriteWidth - sx : widthLeft;
        float h = spriteHeight - sy < heightLeft ? spriteHeight - sy : heightLeft;
        if (w < 1) w = 1;
        if (h < 1) h = 1;
        Renderer_drawSpritePart(renderer, hldLevel.patternSprite, 0, sx, sy, w, h, dx, dy);
        dx += w;
        sx = fmodf(sx + w, spriteWidth);
        widthLeft -= w;
        if (widthLeft <= 0) {
            dx = drawX;
            widthLeft = width;
            sx = startX;
            dy += h;
            sy = fmodf(sy + h, spriteHeight);
            heightLeft -= h;
        }
    }
}

typedef struct { const VMContext* context; int32_t shader; GMLReal luma; } HldBaseShader;
static HldBaseShader hldBase = {.shader = -1};

// shader_reset_HLD and draw_set_blend_mode_HLD: shd_PreMultiplyAlpha, (bm_one, bm_inv_src_alpha), luminosity 0.
static void hldBeginHldDraw(VMContext* ctx) {
    if (hldBase.context != ctx) {
        hldBase = (HldBaseShader){.context = ctx, .shader = -1};
        repeat(ctx->dataWin->shdr.count, i) {
            if (strcmp(ctx->dataWin->shdr.shaders[i].name, "shd_PreMultiplyAlpha") == 0) hldBase.shader = (int32_t) i;
        }
        if (hldBase.shader >= 0) {
            RValue args[] = {RValue_makeReal(hldBase.shader), RValue_makeString("luminosity")};
            hldBase.luma = hldNumberOf(hldCallBuiltin(ctx, "shader_get_uniform", args, 2));
        }
    }
    if (hldBase.shader < 0) return;
    RValue_free((RValue[]){hldCallBuiltin(ctx, "shader_reset", nullptr, 0)});
    RValue shader = RValue_makeReal(hldBase.shader);
    RValue_free((RValue[]){hldCallBuiltin(ctx, "shader_set", &shader, 1)});
    RValue blend[] = {RValue_makeReal(2), RValue_makeReal(6)};
    RValue_free((RValue[]){hldCallBuiltin(ctx, "draw_set_blend_mode_ext", blend, 2)});
    hldUniform(ctx, hldBase.luma, 1, 0, 0, 0, 0);
}

static void hldLuminosity(VMContext* ctx, GMLReal value) {
    if (hldBase.shader < 0) return;
    // Uniforms apply to the whole pending batch, so draw what is queued first.
    Renderer* renderer = ctx->runner->renderer;
    if (renderer->vtable->flush != nullptr) renderer->vtable->flush(renderer);
    hldUniform(ctx, hldBase.luma, 1, value < 0.9 ? value : 0.9, 0, 0, 0);
}

// RecessingScenery Draw: the raised block, or its sides and top while it moves.
static void hldDrawRecess(VMContext* ctx, const HldScenery* item, const HldRecess* recess) {
    Renderer* renderer = ctx->runner->renderer;
    const GMLReal brightness = hldLevel.patternSprite == 240 ? 0.15 : 0.51;
    const float wid = recess->wid;
    if (recess->height == recess->maxHeight) {
        Renderer_drawSpriteExt(renderer, item->sprite, (int32_t) item->frame, item->x, item->y, 1, 1, 0, 0xffffff, 1);
        hldDrawFloorPattern(ctx, item->x, item->y - wid, wid, wid, item->x, item->y - recess->yOffset);
        hldLuminosity(ctx, brightness);
        hldDrawFloorPattern(ctx, item->x, item->y - wid, wid, wid, item->x, item->y - recess->yOffset);
        hldLuminosity(ctx, 0);
    } else if (recess->hideT < 0) {
        const GMLReal lerp = recess->height / recess->maxHeight;
        const Sprite* info = &ctx->dataWin->sprt.sprites[item->sprite];
        const float sideH = (float) info->height - wid;
        const float h = wid + (float) (sideH * lerp);
        const float top = item->y - recess->yOffset + (float) ((1 - lerp) * sideH);
        const uint8_t level = (uint8_t) (255 * (0.7 + 0.3 * lerp));
        const uint32_t shade = (uint32_t) level | (uint32_t) level << 8 | (uint32_t) level << 16;
        Renderer_drawSpritePartExt(renderer, item->sprite, (int32_t) item->frame, 0, 0, wid, h, item->x, top, 1, 1, 0, 0, 0,
                                   shade, 1);
        hldDrawFloorPattern(ctx, item->x, item->y - wid, wid, wid, item->x, top);
        hldLuminosity(ctx, brightness * lerp);
        hldDrawFloorPattern(ctx, item->x, item->y - wid, wid, wid, item->x, top);
        hldLuminosity(ctx, 0);
    }
}

// Invisible engine instances that give the walls layer, the floor and plain scenery their collision masks.
static void hldCreateCollision(VMContext* ctx) {
    hldBlockObject = hldObjectIndex(ctx, "block");
    hldPlatformObject = hldObjectIndex(ctx, "ProtectionPlatform");
    hldCharObject = hldObjectIndex(ctx, "char");
    unsigned proxies = 0;
    for (uint32_t i = 0; i < hldScenery.start[HLD_SC_LISTS]; ++i) {
        const HldScenery* item = &hldScenery.items[i];
        if (!item->collides) continue;
        Instance* proxy = hldCreateInstance(ctx, item->x, item->y, hldBlockObject);
        if (proxy == nullptr) continue;
        proxy->spriteIndex = item->sprite;
        proxy->imageIndex = item->frame;
        proxy->imageXscale = item->xscale;
        proxy->visible = false;
        ++proxies;
    }
    hldNoneSprite = hldSpriteByName(ctx, "spr_none");
    for (uint32_t i = 0; i < hldScenery.start[HLD_SC_LISTS]; ++i) {
        const HldScenery* item = &hldScenery.items[i];
        if (item->recess < 0) continue;
        Instance* proxy = hldCreateInstance(ctx, item->x, item->y, hldBlockObject);
        if (proxy == nullptr) continue;
        proxy->spriteIndex = item->sprite;
        proxy->imageIndex = item->frame;
        proxy->maskIndex = hldNoneSprite;
        proxy->visible = false;
        hldRecess[item->recess].proxy = proxy;
    }
    // MaskLoad: the walls block (16 lower when walls draw on top) and the floor platform, precise masks.
    if (hldLevel.wallSprite >= 0) {
        RValue maskArgs[] = {RValue_makeReal(hldLevel.wallSprite), RValue_makeBool(false), RValue_makeReal(0),
                             RValue_makeReal(0), RValue_makeReal(0), RValue_makeReal(0), RValue_makeReal(0),
                             RValue_makeReal(0), RValue_makeReal(0.9)};
        RValue_free((RValue[]){hldCallBuiltin(ctx, "sprite_collision_mask", maskArgs, 9)});
        Instance* walls = hldCreateInstance(ctx, 0, hldLevel.wallsOnTop ? 16 : 0, hldBlockObject);
        if (walls != nullptr) {
            walls->spriteIndex = hldLevel.wallSprite;
            walls->visible = false;
        }
    }
    if (hldLevel.floorSprite >= 0) {
        RValue maskArgs[] = {RValue_makeReal(hldLevel.floorSprite), RValue_makeBool(false), RValue_makeReal(0),
                             RValue_makeReal(0), RValue_makeReal(0), RValue_makeReal(0), RValue_makeReal(0),
                             RValue_makeReal(0), RValue_makeReal(0.9)};
        RValue_free((RValue[]){hldCallBuiltin(ctx, "sprite_collision_mask", maskArgs, 9)});
        Instance* floor = hldCreateInstance(ctx, 0, 0, hldPlatformObject);
        if (floor != nullptr) {
            floor->spriteIndex = hldLevel.floorSprite;
            floor->visible = false;
        }
    }
    // The test position used by the reference runs.
    // The reference spawn faces 180 degrees (its first right step picks the up-walk sprite through MidAngle).
    hldPlayer = (HldPlayer){.freeze = 2, .imgspd = 1, .xs = 1, .dir = 180, .inputDir = 180, .sprite = 829};
    hldPlayer.inst = hldCreateInstance(ctx, 1206, 629, hldCharObject);
    if (hldPlayer.inst != nullptr) {
        hldPlayer.inst->visible = false;
        hldPlayer.inst->spriteIndex = hldPlayer.sprite;
    }
    logInfo("HLD native: %u scenery collision proxies, walls block, floor platform, player=%d\n", proxies,
            hldPlayer.inst != nullptr);
}

static void hldLoadLevel(VMContext* ctx) {
    hldRoomT = 0;
    hldLevel = (HldLevel){.floorSprite = -1, .wallSprite = -1, .overlaySprite = -1, .patternSprite = -1, .hardLightSprite = -1, .dodgeSprite = -1,
                          .brightness = 1};
    const char* room = ctx->runner->currentRoom->name;
    char region[32], *text = nullptr;
    if (!hldReadLevelFile(ctx, room, region, sizeof(region), &text)) {
        logWarn("HLD native: no level file for %s\n", room);
        return;
    }
    const char* settings = strstr(text, "lb,");
    char value[128];
    hldLevel.width = settings != nullptr && hldSetting(settings, "w", value, sizeof(value)) ? atoi(value) : 0;
    hldLevel.height = settings != nullptr && hldSetting(settings, "h", value, sizeof(value)) ? atoi(value) : 0;
    if (settings != nullptr && hldSetting(settings, "over", value, sizeof(value))) {
        hldLevel.overlaySprite = hldSpriteIndex(ctx, value);
    }
    hldFindOverlays(ctx);
    // LevelLoad: floorSpr is global.floorBakedBackground, the pattern DrawFloorPattern tiles.
    if (settings != nullptr && hldSetting(settings, "floorSpr", value, sizeof(value))) {
        hldLevel.patternSprite = hldSpriteByName(ctx, value);
    }
    hldLevel.wallsOnTop = settings != nullptr && hldSetting(settings, "wallsTop", value, sizeof(value)) && atof(value) != 0;
    // LevelLoad: "bri" is overlayBrightMode (fractions mean 2); GetOverlayBrightness maps it.
    if (settings != nullptr && hldSetting(settings, "bri", value, sizeof(value))) {
        double mode = atof(value);
        if (mode > 0 && mode < 1) mode = 2;
        hldLevel.brightness = mode == 2 ? 0.84 : mode == 3 ? 0.14 : mode == 4 ? 0 : 1;
    }
    hldLoadScenery(ctx, text, (float) hldLevel.height);
    free(text);

    char path[256];
    snprintf(path, sizeof(path), "%s/%s_layer1_sketch_baked.png", region, room);
    RValue args[] = {RValue_makeString(path), RValue_makeReal(1), RValue_makeBool(false), RValue_makeBool(false),
                     RValue_makeReal(0), RValue_makeReal(0)};
    RValue sprite = hldCallBuiltin(ctx, "sprite_add", args, 6);
    hldLevel.floorSprite = RValue_toInt32(sprite);
    // MaskLoad: the walls layer. (Outside developer mode it is also outlined through a surface; not yet native.)
    snprintf(path, sizeof(path), "%s/%s_layer2_sketch.png", region, room);
    args[0] = RValue_makeString(path);
    hldLevel.wallSprite = RValue_toInt32(hldCallBuiltin(ctx, "sprite_add", args, 6));

    hldSetBuiltin(ctx, BUILTIN_VAR_ROOM_WIDTH, "room_width", hldLevel.width, -1);
    hldSetBuiltin(ctx, BUILTIN_VAR_ROOM_HEIGHT, "room_height", hldLevel.height, -1);
    hldSetBuiltin(ctx, BUILTIN_VAR_VIEW_ENABLED, "view_enabled", 1, -1);
    hldSetBuiltin(ctx, BUILTIN_VAR_VIEW_VISIBLE, "view_visible", 1, 0);
    hldSetBuiltin(ctx, BUILTIN_VAR_VIEW_WVIEW, "view_wview", 480, 0);
    hldSetBuiltin(ctx, BUILTIN_VAR_VIEW_HVIEW, "view_hview", 270, 0);
    hldSetBuiltin(ctx, BUILTIN_VAR_VIEW_WPORT, "view_wport", 480, 0);
    hldSetBuiltin(ctx, BUILTIN_VAR_VIEW_HPORT, "view_hport", 270, 0);
    // The reference captures stand at the East Lab test position.
    // The reference capture camera (rm_EL_MegaHugeLAB at 1206,629): view 934,489.
    hldPlaceView(ctx, 934 + 240, 489 + 135);
    // ScreenSurfDraw presents the application surface itself.
    RValue off = RValue_makeBool(false);
    RValue_free((RValue[]){hldCallBuiltin(ctx, "application_surface_draw_enable", &off, 1)});
    hldLevel.loaded = true;
    hldCreateCollision(ctx);
    logInfo("HLD native: level %s/%s %dx%d floor=%d overlay=%d hardlight=%d dodge=%d brightness=%.2f\n", region, room,
            hldLevel.width, hldLevel.height, hldLevel.floorSprite, hldLevel.overlaySprite, hldLevel.hardLightSprite,
            hldLevel.dodgeSprite, (double) hldLevel.brightness);
}

static RValue hldNativeScreenRoomStart(VMContext* ctx, Instance* self, Instance* other, RValue* args, int32_t argCount) {
    (void) self; (void) other; (void) args; (void) argCount;
    if (ctx->runner != nullptr && ctx->runner->currentRoom != nullptr) hldLoadLevel(ctx);
    return RValue_makeUndefined();
}

static RValue hldNativeScreenDraw(VMContext* ctx, Instance* self, Instance* other, RValue* args, int32_t argCount) {
    (void) self; (void) other; (void) args; (void) argCount;
    if (!hldLevel.loaded) return RValue_makeUndefined();
    hldBeginHldDraw(ctx);
    if (hldLevel.floorSprite >= 0) hldDrawSprite(ctx, hldLevel.floorSprite, 0, 0, 0, 1);
    // SCREEN End Step's cull rectangle: the view grown by 80 on every side.
    const float vx1 = (float) hldLevel.viewX - 80, vy1 = (float) hldLevel.viewY - 80;
    const float vx2 = (float) hldLevel.viewX + 480 + 80, vy2 = (float) hldLevel.viewY + 270 + 80;
    hldScenery.visible = hldScenery.drawn = 0;
    // BG draws the walls after the floor lists, or SCREEN draws them between fg0 and fg1 (drawWallsOnTop).
    const unsigned wallsAfter = hldLevel.wallsOnTop ? HLD_SC_FG0 : HLD_SC_FLR1;
    bool playerDrawn = hldPlayer.inst == nullptr;
    const float playerY = hldPlayer.inst != nullptr ? hldPlayer.inst->y : 0;
    for (unsigned list = 0; list < HLD_SC_LISTS; ++list) {
        hldDrawSceneryList(ctx, list, vx1, vy1, vx2, vy2, playerY, &playerDrawn);
        if (list == HLD_SC_DEPTH && !playerDrawn) {
            hldDrawPlayer(ctx);
            playerDrawn = true;
        }
        if (list == wallsAfter && hldLevel.wallSprite >= 0) hldDrawSprite(ctx, hldLevel.wallSprite, 0, 0, 0, 1);
    }
    return RValue_makeUndefined();
}

static RValue hldNativeScreenStep(VMContext* ctx, Instance* self, Instance* other, RValue* args, int32_t argCount) {
    (void) self; (void) other; (void) args; (void) argCount;
    hldStepScenery();
    ++hldRoomT;
    // Follow camera (the game's eased camx/camy is a later rung). The offset reproduces the reference capture's
    // view (934, 489) for the spawn at (1206, 629).
    if (hldPlayer.inst != nullptr) hldPlaceView(ctx, hldPlayer.inst->x - 32, hldPlayer.inst->y - 5);
    // Step events run in instance order, which follows the level file: relays evaluate CaseCheck, OneTimeTrue
    // latches once true, and RecessingScenery steps its block.
    repeat(arrlen(hldCases), i) {
        HldCaseNode* node = &hldCases[i];
        if (node->recess >= 0) {
            hldStepRecess(ctx, &hldRecess[node->recess]);
        } else if (strcmp(node->object, "OneTimeTrue") == 0) {
            if (node->lockedTrue) node->caseState = 2;
            else {
                hldCaseCheck(ctx, node);
                if (node->caseState == 2) node->lockedTrue = true;
            }
        } else if (strcmp(node->object, "EmptyObject") == 0 || strcmp(node->object, "Region") == 0) {
            hldCaseCheck(ctx, node);
        }
    }
    return RValue_makeUndefined();
}

// Native game code, by code entry name. Grows one piece at a time.
static const HldNativeEntry hldNativeEntries[] = {
    {"gml_Object_SCREEN_Other_4", hldNativeScreenRoomStart},
    {"gml_Object_SCREEN_Draw_0", hldNativeScreenDraw},
    {"gml_Object_SCREEN_Step_0", hldNativeScreenStep},
    {"gml_Object_SCREEN_Draw_77", hldNativeScreenPostDraw},
    {"gml_Object_char_Step_0", hldNativeCharStep},
    {nullptr, nullptr},
};

bool hldNativeOnly;
static const VMContext* hldNativeOnlyContext;
static HldNativeHandler* hldNativeHandlers;
static uint32_t* hldNativeSkips;
static uint32_t hldNativeCodeCount;
static uint64_t hldNativeCalls, hldNativeSkipped;

static void hldNativeOnlyBind(VMContext* ctx) {
    free(hldNativeHandlers);
    free(hldNativeSkips);
    hldNativeOnlyContext = ctx;
    hldNativeCodeCount = ctx->dataWin->code.count;
    hldNativeHandlers = safeCalloc(hldNativeCodeCount, sizeof(*hldNativeHandlers));
    hldNativeSkips = safeCalloc(hldNativeCodeCount, sizeof(*hldNativeSkips));
    unsigned bound = 0;
    for (const HldNativeEntry* entry = hldNativeEntries; entry->name != nullptr; ++entry) {
        ptrdiff_t slot = shgeti(ctx->codeIndexByName, (char*) entry->name);
        if (slot < 0) {
            logWarn("HLD native: no code entry %s\n", entry->name);
            continue;
        }
        hldNativeHandlers[ctx->codeIndexByName[slot].value] = entry->handler;
        ++bound;
    }
    logInfo("HLD native-only: %u native handlers, no bytecode will run\n", bound);
}

static RValue hldNativeOnlyDispatch(VMContext* ctx, int32_t codeIndex, Instance* self, Instance* other,
                                    RValue* args, int32_t argCount) {
    if (hldNativeOnlyContext != ctx) hldNativeOnlyBind(ctx);
    ctx->savearefBalance = 0;
    if ((uint32_t) codeIndex < hldNativeCodeCount && hldNativeHandlers[codeIndex] != nullptr) {
        ++hldNativeCalls;
        // Builtins read self and other from the current frame.
        CallFrame frame = {0};
        frame.parent = ctx->currentCallFrame;
        frame.codeIndex = codeIndex;
        frame.initialSelf = frame.self = self;
        frame.initialOther = frame.other = other;
        ctx->currentCallFrame = &frame;
        RValue result = hldNativeHandlers[codeIndex](ctx, self, other, args, argCount);
        ctx->currentCallFrame = frame.parent;
        return result;
    }
    ++hldNativeSkipped;
    if ((uint32_t) codeIndex < hldNativeCodeCount) ++hldNativeSkips[codeIndex];
    return RValue_makeUndefined();
}

void hldReportNativeOnly(VMContext* ctx, int frame) {
    if (!hldNativeOnly || frame <= 0 || frame % 90 != 0) return;
    logInfo("HLD native-only frame=%d native_calls=%llu skipped_bytecode=%llu\n", frame,
            (unsigned long long) hldNativeCalls, (unsigned long long) hldNativeSkipped);
    if (frame == 90 && ctx->runner != nullptr && ctx->runner->currentRoom != nullptr) {
        const Room* room = ctx->runner->currentRoom;
        const RoomView* view = room->views;
        logInfo("HLD native-only room %s %ux%u tiles=%u objects=%u view0 enabled=%d %d,%d %dx%d port %dx%d\n",
                room->name, room->width, room->height, room->tileCount, room->gameObjectCount,
                view != nullptr && view->enabled, view ? view->viewX : 0, view ? view->viewY : 0,
                view ? view->viewWidth : 0, view ? view->viewHeight : 0, view ? view->portWidth : 0, view ? view->portHeight : 0);
    }
    logInfo("HLD native scenery frame=%d structs=%u drawn=%u\n", frame, hldScenery.start[HLD_SC_LISTS], hldScenery.drawn);
    if (frame == 90 && hldNativeSkips != nullptr) {
        // The most frequently skipped code: the next candidates for native ports.
        for (int rank = 0; rank < 12; ++rank) {
            uint32_t best = 0, bestCount = 0;
            for (uint32_t i = 0; i < hldNativeCodeCount; ++i) {
                if (hldNativeSkips[i] > bestCount) { bestCount = hldNativeSkips[i]; best = i; }
            }
            if (bestCount == 0) break;
            logInfo("HLD native-only skipped %u %s\n", bestCount, ctx->dataWin->code.entries[best].name);
            hldNativeSkips[best] = 0;
        }
    }
    hldNativeCalls = hldNativeSkipped = 0;
}
'''

LOOP_DECLARATIONS = r'''
extern bool hldNativeOnly;
void hldReportNativeOnly(VMContext* ctx, int frame);

// The room named by HLD_NATIVE_ONLY or ux0:data/hld-vm/native-only, or -1.
static int32_t hldNativeOnlyRoom(DataWin* dataWin) {
    char room[64] = {0};
#ifdef PLATFORM_VITA
    FILE* file = fopen("ux0:data/hld-vm/native-only", "rb");
    if (file == nullptr) return -1;
    if (fscanf(file, "%63s", room) != 1) room[0] = 0;
    fclose(file);
#else
    const char* name = getenv("HLD_NATIVE_ONLY");
    if (name == nullptr) return -1;
    snprintf(room, sizeof(room), "%s", name);
#endif
    repeat(dataWin->room.count, i) {
        if (strcmp(dataWin->room.rooms[i].name, room) == 0) return (int32_t) i;
    }
    logWarn("HLD native-only: unknown room %s\n", room);
    return -1;
}
'''


def with_native_only_vm(code):
    code = replace_once(code, SIGNATURE, "static RValue hldNativeOnlyDispatch(VMContext*, int32_t, Instance*, Instance*, "
                        "RValue*, int32_t); extern bool hldNativeOnly; " + SIGNATURE)
    start = code.index(SIGNATURE)
    anchor = "    CodeEntry* code = &ctx->dataWin->code.entries[codeIndex];"
    position = code.index(anchor, start)
    end = code.index("\n", position)
    code = code[:end] + (" if (hldNativeOnly) return hldNativeOnlyDispatch(ctx, codeIndex, selfInstance, "
                         "otherInstance, args, argCount);") + code[end:]
    return code + "\n" + VM_NATIVE


def with_native_only_loop(code):
    code = replace_once(code, "// ===[ MAIN ]===\n", LOOP_DECLARATIONS + "\n// ===[ MAIN ]===\n")
    # Decide before the VM starts the first room, so even global init scripts are skipped.
    code = replace_once(code, "        HldTestRoom hldTestRoom = readHldTestRoom(dataWin, savePath);",
                        "        HldTestRoom hldTestRoom = readHldTestRoom(dataWin, savePath);\n"
                        "        const int32_t nativeOnlyRoom = hldNativeOnlyRoom(dataWin);\n"
                        "        hldNativeOnly = nativeOnlyRoom >= 0;\n"
                        "        if (hldNativeOnly) hldTestRoom.room = nullptr;")
    code = replace_once(code, "            if (shouldStep) {\n                Runner_step(runner);",
                        "            if (shouldStep) {\n"
                        "                if (hldNativeOnly && runner->frameCount == 1) runner->pendingRoom = nativeOnlyRoom;\n"
                        "                Runner_step(runner);\n"
                        "                hldReportNativeOnly(runner->vmContext, runner->frameCount);")
    return code
