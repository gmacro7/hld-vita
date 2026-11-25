"""Export GCC feedback counters from instrumented Vita translation units."""

from runtime_verification_source import replace_once


ENGINE_PROFILE_UNITS = ("runner", "vm_builtins")


def engine_profile_marker(unit):
    return f"benchmark-0247-{unit}.gcda"


PROFILE_CALLBACKS = r'''
// The Vita SDK supplies the freestanding libgcov implementation but
// does not install gcov.h in the cross compiler's include directory.
struct gcov_info;
extern const struct gcov_info* const __start_hld_gcov[];
extern const struct gcov_info* const __stop_hld_gcov[];
extern void __gcov_info_to_gcda(const struct gcov_info*,
                               void (*filename)(const char*, void*),
                               void (*dump)(const void*, unsigned, void*),
                               void* (*allocate)(unsigned, void*), void*);

typedef struct HldProfileAllocation {
    struct HldProfileAllocation* next;
    void* storage;
} HldProfileAllocation;

typedef struct {
    FILE* file;
    HldProfileAllocation* allocations;
    unsigned bytes;
    bool failed;
    int unit;
} HldVmProfile;

static void hldVmProfileWrite(const void* bytes, unsigned count, void* opaque) {
    HldVmProfile* profile = opaque;
    if (profile->failed) return;
    const unsigned written = (unsigned)fwrite(bytes, 1, count, profile->file);
    profile->failed = written != count;
    profile->bytes += written;
}

static void* hldVmProfileAllocate(unsigned bytes, void* opaque) {
    HldVmProfile* profile = opaque;
    HldProfileAllocation* allocation = safeMalloc(sizeof(*allocation));
    allocation->next = profile->allocations;
    allocation->storage = safeMalloc(bytes);
    profile->allocations = allocation;
    return allocation->storage;
}

static bool hldFinishProfile(HldVmProfile* profile) {
    const bool closed = profile->file != nullptr && fclose(profile->file) == 0;
    while (profile->allocations != nullptr) {
        HldProfileAllocation* allocation = profile->allocations;
        profile->allocations = allocation->next;
        free(allocation->storage);
        free(allocation);
    }
    return closed && !profile->failed && profile->bytes >= 16;
}
'''

VM_PROFILE_EXPORT = r'''
static void hldVmProfileFilename(const char* filename, void* opaque) {
    (void)filename;
    (void)opaque;
    // There is one translation unit. The host expects vm.gcda.
}

static void hldExportVmProfile(void) {
    const char* filename = "ux0:data/hld-vm/benchmark-0239-vm.gcda";
    if ((uintptr_t)__stop_hld_gcov - (uintptr_t)__start_hld_gcov != sizeof(*__start_hld_gcov)) {
        logError("Vita VM profile expected exactly one instrumented unit\n");
        return;
    }
    FILE* file = fopen(filename, "wb");
    if (file == nullptr) {
        logError("Vita VM profile could not open its output file\n");
        return;
    }
    HldVmProfile profile = {.file = file};
    __gcov_info_to_gcda(__start_hld_gcov[0], hldVmProfileFilename,
                        hldVmProfileWrite, hldVmProfileAllocate, &profile);
    if (!hldFinishProfile(&profile)) {
        remove(filename);
        logError("Vita VM profile export failed\n");
        return;
    }
    logInfo("Vita VM profile exported bytes=%u\n", profile.bytes);
}
'''


ENGINE_PROFILE_EXPORT = r'''
static const struct {
    const char* name;
    const char* gcda;
    const char* destination;
} hldEngineProfileUnits[] = {
ENGINE_PROFILE_UNIT_ROWS
};

enum { HLD_ENGINE_PROFILE_COUNT = sizeof(hldEngineProfileUnits) / sizeof(hldEngineProfileUnits[0]) };

static void hldEngineProfileFilename(const char* filename, void* opaque) {
    HldVmProfile* profile = opaque;
    const char* slash = strrchr(filename, '/');
    const char* basename = slash != nullptr ? slash + 1 : filename;
    for (int unit = 0; unit < HLD_ENGINE_PROFILE_COUNT; ++unit) {
        if (strcmp(hldEngineProfileUnits[unit].gcda, basename) != 0) continue;
        profile->unit = unit;
        profile->file = fopen(hldEngineProfileUnits[unit].destination, "wb");
        profile->failed = profile->file == nullptr;
        return;
    }
    profile->failed = true;
    logError("Vita engine profile export failed: unexpected GCC output name %s\n", basename);
}

static void hldExportEngineProfiles(void) {
    const uintptr_t registeredBytes = (uintptr_t)__stop_hld_gcov - (uintptr_t)__start_hld_gcov;
    if (registeredBytes != HLD_ENGINE_PROFILE_COUNT * sizeof(*__start_hld_gcov)) {
        logError("Vita engine profile export failed: incorrect registered unit count\n");
        return;
    }
    bool exported[HLD_ENGINE_PROFILE_COUNT] = {0};
    for (int index = 0; index < HLD_ENGINE_PROFILE_COUNT; ++index) {
        HldVmProfile profile = {.unit = -1};
        __gcov_info_to_gcda(__start_hld_gcov[index], hldEngineProfileFilename,
                            hldVmProfileWrite, hldVmProfileAllocate, &profile);
        const bool written = hldFinishProfile(&profile);
        if (profile.unit < 0 || !written || exported[profile.unit]) {
            if (profile.unit >= 0) remove(hldEngineProfileUnits[profile.unit].destination);
            logError("Vita engine profile export failed: missing or duplicate profile data\n");
            return;
        }
        exported[profile.unit] = true;
        logInfo("Vita engine profile exported unit=%s bytes=%u\n",
                hldEngineProfileUnits[profile.unit].name, profile.bytes);
    }
    logInfo("Vita engine profiles complete\n");
}
'''


def with_profile_export(code, exporter, call):
    entry = "int loop(CommandLineArgs args, const char *argv0) {"
    code = replace_once(code, entry, PROFILE_CALLBACKS + exporter + "\n" + entry)
    frame = "            uint64_t frameStartNow = nowNanos();"
    return replace_once(code, frame, f"            if (runner->frameCount == 720) {call};\n" + frame)


def with_vm_profile_export(code):
    return with_profile_export(code, VM_PROFILE_EXPORT, "hldExportVmProfile()")


def with_engine_profile_export(code, directory="ux0:data/hld-vm"):
    units = "\n".join(f'    {{"{unit}", "{unit}.gcda", "{directory}/{engine_profile_marker(unit)}"}},'
                      for unit in ENGINE_PROFILE_UNITS)
    return with_profile_export(code, ENGINE_PROFILE_EXPORT.replace("ENGINE_PROFILE_UNIT_ROWS", units),
                               "hldExportEngineProfiles()")
