#include "int_rvalue_hashmap.h"

#include <stdlib.h>
#include "string_compat.h"

#define INITIAL_CAPACITY 8
#define INITIAL_INDEX_CAPACITY 16

// Parent of every first insertion. It has no keys and no index.
static IntRValueShape rootShape;

static IntRValueShapeIndex* indexCreate(uint32_t capacity) {
    IntRValueShapeIndex* index = (IntRValueShapeIndex*) safeMalloc(sizeof(IntRValueShapeIndex));
    index->keys = (int32_t*) safeMalloc(capacity * sizeof(int32_t));
    memset(index->keys, 0xFF, capacity * sizeof(int32_t));
    index->slots = (uint16_t*) safeMalloc(capacity * sizeof(uint16_t));
    index->mask = capacity - 1;
    index->used = 0;
    return index;
}

static void indexPlace(IntRValueShapeIndex* index, int32_t key, uint16_t slot) {
    uint32_t idx = ((uint32_t) key * 0x9E3779B9u) & index->mask;
    while (index->keys[idx] != INT_RVALUE_HASHMAP_EMPTY_KEY) {
        idx = (idx + 1) & index->mask;
    }
    index->keys[idx] = key;
    index->slots[idx] = slot;
}

static void indexInsert(IntRValueShapeIndex* index, int32_t key, uint16_t slot) {
    // Same 0.75 load factor as the original per-instance table.
    if ((index->used + 1) * 4 > (index->mask + 1) * 3) {
        uint32_t oldCapacity = index->mask + 1;
        int32_t* oldKeys = index->keys;
        uint16_t* oldSlots = index->slots;
        uint32_t capacity = oldCapacity * 2;
        index->keys = (int32_t*) safeMalloc(capacity * sizeof(int32_t));
        memset(index->keys, 0xFF, capacity * sizeof(int32_t));
        index->slots = (uint16_t*) safeMalloc(capacity * sizeof(uint16_t));
        index->mask = capacity - 1;
        repeat(oldCapacity, i) {
            if (oldKeys[i] != INT_RVALUE_HASHMAP_EMPTY_KEY) indexPlace(index, oldKeys[i], oldSlots[i]);
        }
        free(oldKeys);
        free(oldSlots);
    }
    indexPlace(index, key, slot);
    index->used++;
}

// Builds a private index holding exactly the keys of shape.
static IntRValueShapeIndex* indexCopyFor(const IntRValueShape* shape) {
    uint32_t capacity = INITIAL_INDEX_CAPACITY;
    while ((shape->count + 1) * 4 > capacity * 3) capacity *= 2;
    IntRValueShapeIndex* index = indexCreate(capacity);
    for (const IntRValueShape* s = shape; s != nullptr && s->count > 0; s = s->parent) {
        indexPlace(index, s->lastKey, (uint16_t) (s->count - 1));
        index->used++;
    }
    return index;
}

static IntRValueShape* childOf(IntRValueShape* shape, int32_t key) {
    ptrdiff_t childCount = arrlen(shape->children);
    repeat(childCount, i) {
        if (shape->children[i]->lastKey == key) return shape->children[i];
    }
    requireMessage(shape->count < UINT16_MAX, "IntRValueHashMap: shape exceeds 65535 keys");

    IntRValueShape* child = (IntRValueShape*) safeCalloc(1, sizeof(IntRValueShape));
    child->parent = shape;
    child->lastKey = key;
    child->count = shape->count + 1;
    if (shape->index == nullptr) {
        child->index = indexCreate(INITIAL_INDEX_CAPACITY);
        indexInsert(child->index, key, 0);
    } else if (!shape->extendedIndex && shape->index->used == shape->count) {
        // First extension of the tail of this chain: share the index and add the key in place.
        // Ancestors reject the new slot because it is not below their count.
        child->index = shape->index;
        indexInsert(child->index, key, (uint16_t) shape->count);
    } else {
        child->index = indexCopyFor(shape);
        indexInsert(child->index, key, (uint16_t) shape->count);
    }
    shape->extendedIndex = true;
    arrput(shape->children, child);
    return child;
}

void IntRValueHashMap_freeAllValues(IntRValueHashMap* map) {
    if (map->entries != nullptr) {
        repeat(map->count, i) {
            RValue_free(&map->entries[i].value);
        }
        free(map->entries);
        map->entries = nullptr;
    }
    map->capacity = 0;
    map->mask = 0;
    map->count = 0;
    map->shape = nullptr;
}

RValue* IntRValueHashMap_findSlot(IntRValueHashMap* map, int32_t key) {
    int32_t slot = IntRValueShape_slotOf(map->shape, key);
    return slot < 0 ? nullptr : &map->entries[slot].value;
}

RValue* IntRValueHashMap_getOrInsertUndefined(IntRValueHashMap* map, int32_t key) {
    requireMessage(key != INT_RVALUE_HASHMAP_EMPTY_KEY, "IntRValueHashMap_getOrInsertUndefined: key -1 collides with the empty-slot sentinel");

    int32_t slot = IntRValueShape_slotOf(map->shape, key);
    if (slot >= 0) return &map->entries[slot].value;

    IntRValueShape* shape = map->shape != nullptr ? map->shape : &rootShape;
    IntRValueShape* next = childOf(shape, key);

    if (map->count + 1 > map->capacity) {
        uint32_t oldCapacity = map->capacity;
        uint32_t capacity = oldCapacity == 0 ? INITIAL_CAPACITY : oldCapacity * 2;
        map->entries = (IntRValueEntry*) safeRealloc(map->entries, capacity * sizeof(IntRValueEntry));
        memset(map->entries + oldCapacity, 0xFF, (capacity - oldCapacity) * sizeof(IntRValueEntry));
        map->capacity = capacity;
        map->mask = capacity - 1;
    }

    IntRValueEntry* entry = &map->entries[map->count];
    entry->key = key;
    entry->value = RValue_makeUndefined();
    map->count++;
    map->shape = next;
    return &entry->value;
}
