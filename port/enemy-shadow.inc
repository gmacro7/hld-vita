enum { OP_PACKED_ENEMY_SHADOW = 0xce, HLD_ENEMY_SHADOW_START = 0x24a0 };

static const VMContext* enemyShadowContext;
static int32_t enemyShadowPlatforms;

#if defined(HLD_COMPARE_ENEMY_SHADOW) || defined(HLD_VERIFY_ENEMY_SHADOW)
static unsigned enemyShadowSkips;
#endif

#ifdef HLD_COMPARE_ENEMY_SHADOW
static bool enemyShadowEnabled;

void hldReportEnemyShadow(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita enemy shadow frame=%d enabled=%d skipped=%u\n",
            frame, (int)enemyShadowEnabled, enemyShadowSkips);
    enemyShadowSkips = 0;
}
#endif

bool hldBindShadowProbes(VMContext* ctx, uint32_t x, uint32_t y, uint32_t meeting);
bool hldShadowProbesMiss(Runner* runner, Instance* instance, const RValue* platforms);

static void bindEnemyShadow(VMContext* ctx) {
    if (ctx->dataWin->gen8.gameID != 742741103 || ctx->dataWin->gen8.wadVersion != 15) return;
    // The measured runtime installs the pure pause-call opcode before this check.
    const CodeEntry* code = matchHldCode(ctx, "gml_Object_enemy_Step_0", 9764, 0x04ef6847u);
    if (code == nullptr || code->localsCount != 12 || code->argumentsCount != 0) return;
    uint8_t* bytes = codeBytes(ctx, code);
    const Variable* platforms = resolveVarDef(ctx, resolveVarOperand(bytes + 0x2500));
    if (platforms->varID < 0 || strcmp(platforms->name, "nonFloorPlatforms") != 0) return;
    if (!hldBindShadowProbes(ctx, resolveFuncOperand(bytes + 0x2544), resolveFuncOperand(bytes + 0x2520),
                             resolveFuncOperand(bytes + 0x2550))) return;

    enemyShadowContext = ctx;
    enemyShadowPlatforms = platforms->varID;
#ifdef HLD_COMPARE_ENEMY_SHADOW
    FILE* marker = fopen("ux0:data/hld-vm/native-enemy-shadow", "r");
    enemyShadowEnabled = marker != nullptr;
    if (marker != nullptr) fclose(marker);
#endif
    const uint32_t word = BinaryUtils_readUint32Aligned(bytes + HLD_ENEMY_SHADOW_START);
    BinaryUtils_writeUint32(bytes + HLD_ENEMY_SHADOW_START,
                           (word & 0x00ffffffu) | ((uint32_t)OP_PACKED_ENEMY_SHADOW << 24));
    logInfo("HLD: bound the enemy shadow probe guard\n");
}

static bool canSkipEnemyShadow(VMContext* ctx) {
    Runner* runner = ctx->runner;
    if (ctx != enemyShadowContext || runner == nullptr || runner->spatialGrid == nullptr ||
        ctx->globalScopeInstance == nullptr || ctx->currentCallFrame->self == nullptr) return false;
#ifdef HLD_COMPARE_ENEMY_SHADOW
    if (!enemyShadowEnabled) return false;
#endif
    const GMLReal epsilon = runner->mathEpsilon;
    if (!(epsilon >= 0.0 && epsilon < 0.5)) return false;
    const RValue platforms = Instance_getSelfVar(ctx->globalScopeInstance, enemyShadowPlatforms);
    return hldShadowProbesMiss(runner, ctx->currentCallFrame->self, &platforms);
}
