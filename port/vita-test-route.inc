#include "stb_ds.h"

enum { MAX_TEST_ROUTE_LEGS = 160 };

static struct {
    char room[64];
    int count;
    struct {
        int first, end, x, y;
        uint32_t buttons;
    } legs[MAX_TEST_ROUTE_LEGS];
    int lastSample;
    unsigned movingFrames;
    bool recordActions;
    int32_t playerObject, dashVar, swordVar;
    unsigned dashFrames, swordFrames;
} testRoute;

static void loadTestRoute(void) {
    testRoute.count = 0;
    FILE* input = fopen("ux0:data/hld-vm/test-route.txt", "r");
    if (input == NULL) return;

    char line[96], extra;
    int count = 0;
    bool valid = fgets(line, sizeof line, input) != NULL &&
                 sscanf(line, "%63s %3d %c", testRoute.room, &count, &extra) == 2 &&
                 count > 0 && count <= MAX_TEST_ROUTE_LEGS;
    int previousEnd = 180;
    bool recordActions = false;
    for (int i = 0; valid && i < count; ++i) {
        int first, end, x, y;
        char button[8];
        const int fields = fgets(line, sizeof line, input) != NULL
            ? sscanf(line, "%4d %4d %3d %3d %7s %c", &first, &end, &x, &y, button, &extra) : 0;
        valid = (fields == 4 || fields == 5) &&
                first >= previousEnd && end > first && end <= 1260 &&
                x >= 0 && x <= 255 && y >= 0 && y <= 255;
        if (!valid) break;
        uint32_t buttons = 0;
        if (fields == 5) {
            if (strcmp(button, "cross") == 0) buttons = SCE_CTRL_CROSS;
            else if (strcmp(button, "square") == 0) buttons = SCE_CTRL_SQUARE;
            else if (strcmp(button, "select") == 0) buttons = SCE_CTRL_SELECT;
            else if (strcmp(button, "start") == 0) buttons = SCE_CTRL_START;
            else if (strcmp(button, "triangl") == 0) buttons = SCE_CTRL_TRIANGLE;
            else if (strcmp(button, "circle") == 0) buttons = SCE_CTRL_CIRCLE;
            else if (strcmp(button, "dleft") == 0) buttons = SCE_CTRL_LEFT;
            else if (strcmp(button, "dright") == 0) buttons = SCE_CTRL_RIGHT;
            else {
                valid = false;
                break;
            }
            recordActions = true;
        }
        testRoute.legs[i].first = first;
        testRoute.legs[i].end = end;
        testRoute.legs[i].x = x;
        testRoute.legs[i].y = y;
        testRoute.legs[i].buttons = buttons;
        previousEnd = end;
    }
    valid = valid && fscanf(input, " %c", &extra) == EOF;
    fclose(input);

    if (!valid) {
        logWarn("Vita test route was invalid\n");
        return;
    }
    testRoute.count = count;
    testRoute.lastSample = -1;
    testRoute.recordActions = recordActions;
    testRoute.movingFrames = testRoute.dashFrames = testRoute.swordFrames = 0;
    if (recordActions) {
        testRoute.playerObject = -1;
        testRoute.dashVar = -1;
        testRoute.swordVar = -1;
        for (uint32_t i = 0; i < activeRunner->dataWin->objt.count; ++i) {
            if (strcmp(activeRunner->dataWin->objt.objects[i].name, "char") != 0) continue;
            testRoute.playerObject = (int32_t)i;
            break;
        }
        VMContext* vm = activeRunner->vmContext;
        if (shgeti(vm->varNameMap, "dashing") >= 0) testRoute.dashVar = shget(vm->varNameMap, "dashing");
        if (shgeti(vm->varNameMap, "meleeAttacking") >= 0) testRoute.swordVar = shget(vm->varNameMap, "meleeAttacking");
    }
    logInfo("Vita test route room=%s legs=%d\n", testRoute.room, count);
}

static void reportTestRoute(int frame) {
    const char* actor = "absent";
    float x = 0, y = 0;
    for (ptrdiff_t index = 0; index < arrlen(activeRunner->instances); ++index) {
        const Instance* instance = activeRunner->instances[index];
        if (!instance->active || instance->objectIndex < 0 ||
            (uint32_t)instance->objectIndex >= activeRunner->dataWin->objt.count) continue;
        const char* name = activeRunner->dataWin->objt.objects[instance->objectIndex].name;
        if (strcmp(name, "char") != 0) continue;
        actor = "present";
        x = instance->x;
        y = instance->y;
        break;
    }
    GMLCamera* camera = Runner_getCameraForView(activeRunner, 0);
    if (camera == NULL) {
        logInfo("Vita route frame=%d moved=%u actor=%s x=%f y=%f camera=absent\n",
                frame, testRoute.movingFrames, actor, (double)x, (double)y);
    } else {
        logInfo("Vita route frame=%d moved=%u actor=%s x=%f y=%f camera=%f,%f\n",
                frame, testRoute.movingFrames, actor, (double)x, (double)y,
                (double)camera->viewX, (double)camera->viewY);
    }
    testRoute.movingFrames = 0;
    if (testRoute.recordActions) {
        logInfo("Vita route effects frame=%d dash_frames=%u sword_frames=%u\n",
                frame, testRoute.dashFrames, testRoute.swordFrames);
        testRoute.dashFrames = testRoute.swordFrames = 0;
    }
}

static void countTestRouteEffects(void) {
    if (testRoute.playerObject < 0 || activeRunner->instancesByExactObject == NULL) return;
    Instance** players = activeRunner->instancesByExactObject[testRoute.playerObject];
    for (ptrdiff_t index = 0; index < arrlen(players); ++index) {
        Instance* player = players[index];
        if (!player->active) continue;
        if (testRoute.dashVar >= 0) testRoute.dashFrames += RValue_toBool(Instance_getSelfVar(player, testRoute.dashVar));
        if (testRoute.swordVar >= 0) testRoute.swordFrames += RValue_toBool(Instance_getSelfVar(player, testRoute.swordVar));
        return;
    }
}

static bool applyTestRoute(SceCtrlData* pad) {
    if (testRoute.count == 0 || activeRunner->currentRoom == NULL ||
        strcmp(activeRunner->currentRoom->name, testRoute.room) != 0) return false;

    const int frame = activeRunner->frameCount;
    pad->buttons = 0;
    pad->lx = pad->ly = pad->rx = pad->ry = 128;
    for (int i = 0; i < testRoute.count; ++i) {
        if (frame < testRoute.legs[i].first) break;
        if (frame >= testRoute.legs[i].end) continue;
        pad->lx = (uint8_t)testRoute.legs[i].x;
        pad->ly = (uint8_t)testRoute.legs[i].y;
        pad->buttons = testRoute.legs[i].buttons;
        break;
    }

    if (frame != testRoute.lastSample) {
        if (frame > 0 && frame % 90 == 0) reportTestRoute(frame);
        testRoute.lastSample = frame;
        testRoute.movingFrames += pad->lx != 128 || pad->ly != 128;
        if (testRoute.recordActions) countTestRouteEffects();
    }
    return true;
}
