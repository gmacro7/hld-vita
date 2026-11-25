#include "vita_textures.h"
#include "image/image_decoder.h"
#include "stb_image.h"
#include "utils.h"

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <psp2/kernel/clib.h>

static FILE* vitaTexBinF = NULL;
static int pageCount = 0;
static int* pageOffsets = NULL;
static int* pageSizes = NULL;

static const uint8_t PNG_SIGNATURE[8] = {0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A};  
static bool vitaUsingTexBin = false;

bool isPNG(FILE *f) {
    if (!f) return false;
    uint8_t header[8];
    if (fread(header, 1, 8, f) != 8) return false;
    fseek(f, -8, SEEK_CUR);
    return memcmp(header, PNG_SIGNATURE, 8) == 0;
}

bool VitaTextures_Active() { return vitaUsingTexBin; }
uint32_t VitaTextures_GetPageCount() { return (uint32_t)pageCount; }

bool VitaTextures_Init(FILE *binFile) {
    if (vitaUsingTexBin) return true;
    vitaUsingTexBin = true;
    vitaTexBinF = binFile;

    if (!fread(&pageCount, sizeof(int), 1, vitaTexBinF)) goto fail;
    pageOffsets = (int*)safeMalloc(pageCount * sizeof(int));
    pageSizes = (int*)safeMalloc(pageCount * sizeof(int));

    for (int i = 0; i < pageCount; i++) {
        pageOffsets[i] = (int)ftell(vitaTexBinF) + 4;
        if (!fread(&pageSizes[i], sizeof(int), 1, vitaTexBinF)) goto fail;
        fseek(vitaTexBinF, pageSizes[i], SEEK_CUR);
    }
    
    sceClibPrintf("Succesfully parsed textures.bin\n");
    return true;
fail:
    if (vitaTexBinF) fclose(vitaTexBinF);
    sceClibPrintf("textures.bin failed to load, bailin tf outta here\n");
    vitaUsingTexBin = false;
    vitaTexBinF = NULL;
    return false;
}

bool VitaTextures_UploadRgbaPng(const uint8_t* blob, size_t size, int* outWidth, int* outHeight) {
    int width, height;
    if (!ImageDecoder_getRgba8PngSize(blob, size, &width, &height) || width > 4096 || height > 4096) return false;
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    uint8_t* textureData = vglGetTexDataPointer(GL_TEXTURE_2D);
    if (textureData == NULL) return false;

    // VitaGL's linear RGBA8888 textures pad each row to eight pixels.
    size_t stride = ((width + 7) & ~7) * 4;
    if (!ImageDecoder_readRgba8Png(blob, size, textureData, stride)) return false;
    *outWidth = width;
    *outHeight = height;
    return true;
}

static bool loadTextureRecord(FILE* stream, long offset, uint32_t recordSize, int* outWidth, int* outHeight) {
    if (recordSize == 0 || fseek(stream, offset, SEEK_SET) != 0) return false;
    int width, height;
    if (isPNG(stream)) {
        uint8_t* blob = malloc(recordSize);
        bool uploaded = blob != NULL && fread(blob, 1, recordSize, stream) == recordSize &&
            VitaTextures_UploadRgbaPng(blob, recordSize, &width, &height);
        free(blob);
        if (!uploaded) {
            if (fseek(stream, offset, SEEK_SET) != 0) return false;
            uint8_t* pixels = stbi_load_from_file(stream, &width, &height, NULL, 4);
            if (!pixels) return false;
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
            stbi_image_free(pixels);
        }
        *outWidth = width;
        *outHeight = height;
        return true;
    }
    enum { pvrHeaderSize = 0x34 };
    uint32_t header[pvrHeaderSize / sizeof(uint32_t)];
    if (recordSize <= pvrHeaderSize || fread(header, pvrHeaderSize, 1, stream) != 1 ||
        header[0] != 0x03525650u) return false;
    uint64_t format = (uint64_t)header[2] | ((uint64_t)header[3] << 32);
    height = (int)header[6];
    width = (int)header[7];
    uint32_t metadata_size = header[12];
    if (width <= 0 || height <= 0 || metadata_size >= recordSize - pvrHeaderSize ||
        fseek(stream, metadata_size, SEEK_CUR) != 0) return false;
    uint32_t size = recordSize - pvrHeaderSize - metadata_size;
    void* ext_data = vglMalloc(size);
    if (!ext_data) return false;
    if (fread(ext_data, 1, size, stream) != size) {
        vglFree(ext_data);
        return false;
    }
    
    switch (format) {
        case 0x00:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGB_PVRTC_2BPPV1_IMG, width, height, 0, size, ext_data);
            break;
        case 0x01:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_PVRTC_2BPPV1_IMG, width, height, 0, size, ext_data);
            break;
        case 0x02:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGB_PVRTC_4BPPV1_IMG, width, height, 0, size, ext_data);
            break;
        case 0x03:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_PVRTC_4BPPV1_IMG, width, height, 0, size, ext_data);
            break;
        case 0x04:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_PVRTC_2BPPV2_IMG, width, height, 0, size, ext_data);
            break;
        case 0x05:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_PVRTC_4BPPV2_IMG, width, height, 0, size, ext_data);
            break;
        case 0x06:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_ETC1_RGB8_OES, width, height, 0, size, ext_data);
            break;
        case 0x07:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_S3TC_DXT1_EXT, width, height, 0, size, ext_data);
            break;
        case 0x09:
            glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_S3TC_DXT3_EXT, width, height, 0, size, ext_data);
            break;
        case 0x0B:
            if (metadata_size == 4) { // Load DXT5 as pre-swizzled
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, 8, 8, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
                SceGxmTexture *gxm_tex = vglGetGxmTexture(GL_TEXTURE_2D);
                void *tex_data = vglForceAlloc(size);
                if (!tex_data) {
                    vglFree(ext_data);
                    return false;
                }
                vglFree(vglGetTexDataPointer(GL_TEXTURE_2D));
                sceClibMemcpy(tex_data, ext_data, size);
                sceGxmTextureInitSwizzledArbitrary(gxm_tex, tex_data, SCE_GXM_TEXTURE_FORMAT_UBC3_ABGR, width, height, 0);
                vglOverloadTexDataPointer(GL_TEXTURE_2D, tex_data);
            } else
                glCompressedTexImage2D(GL_TEXTURE_2D, 0, GL_COMPRESSED_RGBA_S3TC_DXT5_EXT, width, height, 0, size, ext_data);
            break;
        default:
            sceClibPrintf("Unsupported externalized texture format (0x%llX).\n", format);
            vglFree(ext_data);
            return false;
    }
    vglFree(ext_data);

    bool isPOT = (width & (width - 1)) == 0 && (height & (height - 1)) == 0;
    GLint wrapMode = isPOT ? GL_REPEAT : GL_CLAMP_TO_EDGE;

    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, wrapMode);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, wrapMode);

    *outWidth = width;
    *outHeight = height;

    return true;
}

bool VitaTextures_LoadPage(int pageIdx, int* outWidth, int* outHeight) {
    if (pageIdx < 0 || pageIdx >= pageCount) return false;
    if (vitaTexBinF && pageSizes[pageIdx] > 0) {
        return loadTextureRecord(vitaTexBinF, pageOffsets[pageIdx], (uint32_t)pageSizes[pageIdx], outWidth, outHeight);
    }

    // Small room-specific additions can be staged without replacing the base bundle.
    char path[96];
    snprintf(path, sizeof(path), "ux0:data/hld-vm/atlas/page%03d.pvr", pageIdx);
    FILE* stream = fopen(path, "rb");
    if (!stream) return false;
    long size = fseek(stream, 0, SEEK_END) == 0 ? ftell(stream) : -1;
    bool loaded = size > 0 && loadTextureRecord(stream, 0, (uint32_t)size, outWidth, outHeight);
    fclose(stream);
    return loaded;
}

void VitaTextures_Free() {
    if (!VitaTextures_Active()) return;
    if (vitaTexBinF) fclose(vitaTexBinF);
    free(pageSizes);
    free(pageOffsets);
    vitaTexBinF = NULL;
    pageSizes = pageOffsets = NULL;
    pageCount = 0;
    vitaUsingTexBin = false;
}
