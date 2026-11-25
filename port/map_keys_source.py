"""ds_map keys keep their number type (GameMaker semantics).

Maps store keys as text, so ds_map_find_first/find_next/ds_map_keys_to_array returned "48" for a key added as 48.
HLD's map screen walks DATA.roomMapModuleStates that way and passes the key to GetMapRoomCoordinates, whose
`switch (_room) { case 15: ...}` never matches a string, so every module marker fell back to its region's default
position. Entries now record that their key was a number; readers return that number (only when the stored text
still matches it, so entries inserted elsewhere without the flag stay strings)."""

from runtime_verification_source import replace_once

HELPERS = r"""
static inline bool hldMapKeyIsNumber(RValue key) {
    return key.type == RVALUE_REAL || key.type == RVALUE_INT32 || key.type == RVALUE_INT64 || key.type == RVALUE_BOOL;
}
static inline void hldMapKeyMark(DsMapEntry* map, ptrdiff_t index, RValue key) {
    if (index < 0) return;
    map[index].numericKey = hldMapKeyIsNumber(key);
    map[index].numericKeyValue = map[index].numericKey ? (double) RValue_toReal(key) : 0.0;
}
static inline RValue hldMapKeyValue(const DsMapEntry* entry, DataWin* dataWin) {
    if (entry->numericKey) {
        char* text = RValue_toString(RValue_makeReal((GMLReal) entry->numericKeyValue), dataWin);
        const bool same = text != nullptr && strcmp(text, entry->key) == 0;
        free(text);
        if (same) return RValue_makeReal((GMLReal) entry->numericKeyValue);
    }
    return RValue_makeOwnedString(safeStrdup(entry->key));
}
"""


def with_numeric_map_keys(code):
    import os
    if os.environ.get("HLDC_MAP_KEYS", "1") != "1":
        return code
    if "bool numericKey;" in code or "static inline ptrdiff_t getValueIndexInMap(" not in code:
        return code
    # Helpers go right before the first map helper (same line, so later line numbers are unchanged).
    anchor = "static inline ptrdiff_t getValueIndexInMap(DsMapEntry** mapPtr, RValue keyRvalue, DataWin* dataWin) {"
    code = replace_once(code, anchor, HELPERS.replace("\n", " ").strip() + " " + anchor)
    # ds_map_add (and add_map/add_list): new key
    code = replace_once(code, "        shput(*mapPtr, key, valueToStore);\n    }\n\n    return RValue_makeUndefined();",
                        "        shput(*mapPtr, key, valueToStore); hldMapKeyMark(*mapPtr, shgeti(*mapPtr, key), args[1]);\n    }\n\n    return RValue_makeUndefined();")
    # ds_map_replace / ds_map_set
    code = replace_once(code, "    shput(*mapPtr, key, RValue_makeIndependent(args[2]));\n",
                        "    shput(*mapPtr, key, RValue_makeIndependent(args[2])); hldMapKeyMark(*mapPtr, shgeti(*mapPtr, key), args[1]);\n")
    # ds_map_copy
    code = replace_once(code, "        shput(*destPtr, safeStrdup((*srcPtr)[i].key),\n            RValue_makeIndependent((*srcPtr)[i].value));",
                        "        shput(*destPtr, safeStrdup((*srcPtr)[i].key),\n            RValue_makeIndependent((*srcPtr)[i].value)); "
                        "{ const ptrdiff_t hldAt = shgeti(*destPtr, (*srcPtr)[i].key); if (hldAt >= 0) { (*destPtr)[hldAt].numericKey = (*srcPtr)[i].numericKey; "
                        "(*destPtr)[hldAt].numericKeyValue = (*srcPtr)[i].numericKeyValue; } }")
    # iteration
    code = replace_once(code, "    return RValue_makeOwnedString(safeStrdup((*mapPtr)[0].key));",
                        "    return hldMapKeyValue(&(*mapPtr)[0], ctx->runner->dataWin);")
    code = replace_once(code, "    return RValue_makeOwnedString(safeStrdup((*mapPtr)[idx + 1].key));",
                        "    return hldMapKeyValue(&(*mapPtr)[idx + 1], ctx->runner->dataWin);")
    code = replace_once(code, "        GMLArray_add(arr, RValue_makeOwnedString(safeStrdup((*map)[i].key)));",
                        "        GMLArray_add(arr, hldMapKeyValue(&(*map)[i], ctx->runner->dataWin));")
    # json-decoded maps: string keys
    code = replace_once(code, "            shput(*mapPtr, safeStrdup(key), storedVal);\n        }\n    }\n\n    JsonReader_free(parsed);",
                        "            shput(*mapPtr, safeStrdup(key), storedVal); { const ptrdiff_t hldAt = shgeti(*mapPtr, key); if (hldAt >= 0) (*mapPtr)[hldAt].numericKey = false; }\n        }\n    }\n\n    JsonReader_free(parsed);")
    return code
