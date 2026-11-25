"""Use shared field layouts in the VM while keeping sparse bucket order visible."""

import re

from runtime_verification_source import replace_once


MAP_ACCESS = r'''
#include "shared_instance_fields.h"

// A sparse power-of-two table cannot have this mask.
#define SHARED_FIELD_MASK UINT32_MAX

static inline bool hasSharedFields(const IntRValueHashMap* map) {
    return map->mask == SHARED_FIELD_MASK;
}

static inline SharedInstanceFields* sharedFields(const IntRValueHashMap* map) {
    return (SharedInstanceFields*)map->entries;
}

static inline int32_t instanceFieldKeyAt(const IntRValueHashMap* map, uint32_t index) {
    return hasSharedFields(map) ? sharedFields(map)->layout->fields[index].key : map->entries[index].key;
}

static inline RValue* instanceFieldValueAt(IntRValueHashMap* map, uint32_t index) {
    return hasSharedFields(map) ? &sharedFields(map)->values[index] : &map->entries[index].value;
}

static inline IntRValueEntry instanceFieldAt(const IntRValueHashMap* map, uint32_t index) {
    const int32_t key = instanceFieldKeyAt(map, index);
    if (key == INT_RVALUE_HASHMAP_EMPTY_KEY) return (IntRValueEntry){key, RValue_makeUndefined()};
    return (IntRValueEntry){key, hasSharedFields(map) ? sharedFields(map)->values[index] : map->entries[index].value};
}
'''


def with_shared_fields_header(code):
    anchor = "} IntRValueHashMap;"
    code = replace_once(code, anchor, anchor + "\n" + MAP_ACCESS)
    anchor = "static inline RValue IntRValueHashMap_get(const IntRValueHashMap* map, int32_t key) {"
    code = replace_once(code, anchor, anchor + r'''
    if (hasSharedFields(map)) {
        const RValue* slot = SharedInstanceFields_find(sharedFields(map), key);
        if (slot == nullptr) return RValue_makeUndefined();
        RValue result = *slot;
        result.ownsReference = false;
        return result;
    }''')
    anchor = "static inline bool IntRValueHashMap_contains(const IntRValueHashMap* map, int32_t key) {"
    return replace_once(code, anchor, anchor + r'''
    if (hasSharedFields(map)) return SharedInstanceFields_find(sharedFields(map), key) != nullptr;''')


def with_shared_fields_hashmap(code):
    anchor = "void IntRValueHashMap_freeAllValues(IntRValueHashMap* map) {"
    code = replace_once(code, anchor, anchor + r'''
    if (hasSharedFields(map)) {
        SharedInstanceFields_free(sharedFields(map));
        *map = (IntRValueHashMap){0};
        return;
    }''')
    anchor = "RValue* IntRValueHashMap_findSlot(IntRValueHashMap* map, int32_t key) {"
    code = replace_once(code, anchor, anchor + r'''
    if (hasSharedFields(map)) return SharedInstanceFields_find(sharedFields(map), key);''')
    anchor = "    if ((map->count + 1) * 4 > map->capacity * 3) {"
    return replace_once(code, anchor, r'''    if (hasSharedFields(map)) {
        SharedInstanceFields* packed = sharedFields(map);
        RValue* slot = SharedInstanceFields_find(packed, key);
        if (slot != nullptr) return slot;
        *map = (IntRValueHashMap){0};
        SharedInstanceFields_unpack(packed, map);
    }
''' + anchor)


PROMOTE = r'''
static void shareSceneryFieldLayout(Runner* runner) {
    if (runner->dataWin->gen8.gameID != 742741103 || runner->dataWin->gen8.wadVersion != 15 ||
        runner->dataWin->objt.count <= 31 || runner->instancesByExactObject == nullptr) return;
    Instance** group = runner->instancesByExactObject[31];
    const size_t size = arrlenu(group);
    if (size == 0) return;
    SharedFieldLayout* layout = SharedFieldLayout_create(&group[0]->selfVars);
    if (layout == nullptr) return;

    size_t packedCount = 0;
    for (size_t index = 0; index < size; ++index) {
        Instance* instance = group[index];
        if (!instance->active) continue;
        IntRValueHashMap* map = &instance->selfVars;
        SharedInstanceFields* fields = SharedInstanceFields_pack(layout, map);
        if (fields == nullptr) continue;
        *map = (IntRValueHashMap){(IntRValueEntry*)fields, layout->count, SHARED_FIELD_MASK, layout->count};
        ++packedCount;
    }
    const size_t perInstance = layout->capacity * sizeof(IntRValueEntry) - sizeof(SharedInstanceFields) -
        layout->count * sizeof(RValue);
    logInfo("HLD: shared field storage frame=%d packed=%u of %u bytes_saved_per_instance=%u\n",
            runner->frameCount, (unsigned)packedCount, (unsigned)size, (unsigned)perInstance);
    SharedFieldLayout_release(layout);
}
'''


def with_shared_fields_runner(code, *, launch_flag=None):
    declare = f"\nextern bool {launch_flag};\n" if launch_flag else ""
    enabled = f"{launch_flag} && " if launch_flag else ""
    code = replace_once(code, "void Runner_step(Runner* runner) {", declare + PROMOTE + f'''
void Runner_step(Runner* runner) {{
    if ({enabled}runner->frameCount == 180) shareSceneryFieldLayout(runner);''')
    old = """            IntRValueEntry* entry = &inst->selfVars.entries[slotIndex];
            if (entry->key == INT_RVALUE_HASHMAP_EMPTY_KEY) continue;
            RValue* value = &entry->value;"""
    code = replace_once(code, old, """            if (instanceFieldKeyAt(&inst->selfVars, slotIndex) == INT_RVALUE_HASHMAP_EMPTY_KEY) continue;
            RValue* value = instanceFieldValueAt(&inst->selfVars, slotIndex);""")
    return with_shared_field_iteration(code)


def with_shared_field_iteration(code):
    # Copy the entry for read-only enumeration, including the original order of keys.
    pattern = r"IntRValueEntry\* entry = &(\w+->selfVars)\.entries\[(\w+)\];(?P<body>.*?)(?=\n    \})"
    code = re.sub(pattern, lambda match: f"IntRValueEntry entry = instanceFieldAt(&{match[1]}, {match[2]});"
                  + match["body"].replace("entry->", "entry."), code, flags=re.S)
    pattern = r"((?:[\w]+(?:\.|->))*selfVars)\.entries\[(\w+)\]"
    code = re.sub(pattern, r"instanceFieldAt(&\1, \2)", code)
    assert ".selfVars.entries" not in code and "->selfVars.entries" not in code
    return code


def with_shared_fields_instance(code):
    code = with_shared_field_iteration(code)
    code = replace_once(code, "    const IntRValueEntry* entries = map->entries;\n    uint32_t mask = map->mask;\n", "")
    assert code.count("idx <= mask") == code.count("entries[idx].key") == code.count("entries[idx].value") == 2
    return (code.replace("idx <= mask", "idx < map->capacity")
            .replace("entries[idx].key", "instanceFieldAt(map, idx).key")
            .replace("entries[idx].value", "instanceFieldAt(map, idx).value"))
