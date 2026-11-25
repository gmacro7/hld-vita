#ifndef _BS_SPRITE_DECODE_CACHE_H_
#define _BS_SPRITE_DECODE_CACHE_H_

#include <stddef.h>
#include <stdint.h>

typedef struct SpriteDecodeCache SpriteDecodeCache;

// Returns a caller-owned RGBA copy, with the same layout as the image decoder.
uint8_t* SpriteDecodeCache_decode(SpriteDecodeCache** cache, const uint8_t* file, size_t fileBytes,
                                  int* w, int* h);
void SpriteDecodeCache_free(SpriteDecodeCache* cache);

#endif
