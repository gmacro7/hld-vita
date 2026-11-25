#ifndef _BS_INT_RVALUE_HASHMAP_H_
#define _BS_INT_RVALUE_HASHMAP_H_

#include "common.h"
#include "utils.h"
#include "rvalue.h"
#include <stdint.h>
#include <stdbool.h>

// Map from int32_t to RValue with shared shapes.
//
// Every map that inserts the same keys in the same order shares one IntRValueShape, which owns the key -> slot
// index. A map only stores its values, densely in insertion order, so instances of one object keep their fields
// together instead of scattered through a sparse hash table, and the shared index stays warm in the cache.
//
// The entries array keeps the original layout: entries[slot].key is the key in that slot and unused slots hold
// INT_RVALUE_HASHMAP_EMPTY_KEY. Iterators may still scan [0, capacity) and skip empty keys. mask is capacity - 1.

#define INT_RVALUE_HASHMAP_EMPTY_KEY ((int32_t) -1)

typedef struct {
    int32_t key;
    RValue value;
} IntRValueEntry;

// Open-addressed key -> slot index, shared along a chain of shapes that extend one another.
typedef struct {
    int32_t* keys;
    uint16_t* slots;
    uint32_t mask;
    uint32_t used;
} IntRValueShapeIndex;

typedef struct IntRValueShape IntRValueShape;
struct IntRValueShape {
    IntRValueShapeIndex* index; // A key belongs to this shape when its indexed slot is below count.
    uint32_t count;
    IntRValueShape* parent;
    int32_t lastKey;            // Key added by this shape (slot count - 1).
    IntRValueShape** children;  // stb_ds array of shapes that add one key to this shape.
    bool extendedIndex;         // A child already extended this shape's index in place.
};

typedef struct {
    IntRValueEntry* entries;
    uint32_t capacity;
    uint32_t mask;
    uint32_t count;
    IntRValueShape* shape;      // nullptr until the first insertion.
} IntRValueHashMap;

// RValue_free's every occupied entry, then releases the entries buffer. The shape is shared and kept.
void IntRValueHashMap_freeAllValues(IntRValueHashMap* map);

// Returns a pointer to the value slot for key, or nullptr if absent. The pointer is valid until the next mutation of the map.
RValue* IntRValueHashMap_findSlot(IntRValueHashMap* map, int32_t key);

// Returns a pointer to the existing value slot for key. If the key is absent, inserts an RVALUE_UNDEFINED entry first and returns that slot.
// The pointer is valid until the next mutation of the map.
RValue* IntRValueHashMap_getOrInsertUndefined(IntRValueHashMap* map, int32_t key);

static inline uint32_t IntRValueHashMap_count(const IntRValueHashMap* map) {
    return map->count;
}

// Returns the slot of key in shape, or -1.
static inline int32_t IntRValueShape_slotOf(const IntRValueShape* shape, int32_t key) {
    if (shape == nullptr) return -1;
    const IntRValueShapeIndex* index = shape->index;
    uint32_t idx = ((uint32_t) key * 0x9E3779B9u) & index->mask;
    while (true) {
        int32_t slotKey = index->keys[idx];
        if (slotKey == key) {
            uint32_t slot = index->slots[idx];
            return slot < shape->count ? (int32_t) slot : -1;
        }
        if (slotKey == INT_RVALUE_HASHMAP_EMPTY_KEY) return -1;
        idx = (idx + 1) & index->mask;
    }
}

// Fast inline get. Returns a non-owning (weak) view of the value, or RVALUE_UNDEFINED if absent. Caller must NOT RValue_free the result without first strengthening it (strdup / incRef).
static inline RValue IntRValueHashMap_get(const IntRValueHashMap* map, int32_t key) {
    int32_t slot = IntRValueShape_slotOf(map->shape, key);
    if (slot < 0) return RValue_makeUndefined();
    RValue result = map->entries[slot].value;
    result.ownsReference = false;
    return result;
}

// Returns true if the map contains the given key. Used for variable_instance_exists() and similar existence checks.
static inline bool IntRValueHashMap_contains(const IntRValueHashMap* map, int32_t key) {
    return IntRValueShape_slotOf(map->shape, key) >= 0;
}

#endif /* _BS_INT_RVALUE_HASHMAP_H_ */
