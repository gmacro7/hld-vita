"""Compare offset reads with the original lazy audio path on the Vita."""

from runtime_verification_source import replace_once


def with_archive_audio_reads(code):
    code = replace_once(code, '#include "data_win.h"', '#include "data_win.h"\n#include <limits.h>')
    anchor = "void DataWin_loadAudoIfNeeded(DataWin* dw, uint32_t audioEntryId) {"
    code = replace_once(code, anchor, """extern bool hldArchiveAudioReads;

static bool readAudioAtOffset(DataWin* dw, AudioEntry* entry) {
    const uint64_t end = (uint64_t)entry->dataOffset + entry->dataSize;
    if (end > dw->fileSize || end > LONG_MAX) return false;
    const int fd = fileno(dw->lazyLoadFile);
    if (fd < 0) return false;

    size_t done = 0;
    while (done < entry->dataSize) {
        const ssize_t count = pread(fd, entry->data + done, entry->dataSize - done,
                                    (off_t)entry->dataOffset + (off_t)done);
        if (count <= 0) return false;
        done += (size_t)count;
    }
    return true;
}

""" + anchor)
    allocation = "    entry->data = (uint8_t *)safeMalloc(entry->dataSize);\n"
    start = code.index(anchor)
    end = code.index("\n}", start)
    body = replace_once(code[start:end], allocation, allocation + """
    if (hldArchiveAudioReads) {
        if (readAudioAtOffset(dw, entry)) return;
        logWarn("Vita archive audio offset read fallback entry=%u\\n", audioEntryId);
    }
""")
    return code[:start] + body + code[end:]


def with_footstep_start_timings(code):
    start = code.index("static int32_t maPlaySound(")
    end = code.index("\nstatic void maSetSoundSpatial(", start)
    body = code[start:end]
    anchor = "    SoundInstance* slot = findFreeSlot(ma);"
    body = replace_once(body, anchor, """    const bool profileFootstep = !loop && inAudo && sound != nullptr && sound->name != nullptr &&
        strncmp(sound->name, "snd_FootstepEast", sizeof("snd_FootstepEast") - 1) == 0;
    uint64_t allocateFrom = 0, loadFrom = 0, loadUntil = 0, uploadFrom = 0, uploadUntil = 0;
    bool cold = false;
    uint32_t effectBytes = 0;
""" + anchor)
    anchor = "        (void)alGetError(); // band-aid for the undertale 'thud'"
    body = replace_once(body, anchor, "        if (profileFootstep) allocateFrom = nowNanos();\n" + anchor)
    original = """            DataWin_loadAudoIfNeeded(ma->base.audioGroups[sound->audioGroup], (uint32_t)sound->audioFile);
            AudioEntry* entry = &ma->base.audioGroups[sound->audioGroup]->audo.entries[sound->audioFile];"""
    body = replace_once(body, original, """            DataWin* group = ma->base.audioGroups[sound->audioGroup];
            AudioEntry* entry = &group->audo.entries[sound->audioFile];
            if (profileFootstep) {
                cold = entry->data == nullptr;
                effectBytes = entry->dataSize;
                loadFrom = nowNanos();
            }
            DataWin_loadAudoIfNeeded(group, (uint32_t)sound->audioFile);
            if (profileFootstep) loadUntil = nowNanos();""")
    anchor = "#if defined(IS_BIG_ENDIAN)"
    body = replace_once(body, anchor, "            if (profileFootstep) uploadFrom = nowNanos();\n" + anchor)
    anchor = """            alSourcei(slot->alSource, AL_BUFFER, slot->alBuffer);
            free(decodedData);"""
    body = replace_once(body, anchor, """            alSourcei(slot->alSource, AL_BUFFER, slot->alBuffer);
            if (profileFootstep) uploadUntil = nowNanos();
            free(decodedData);""")
    anchor = "    return slot->instanceId;"
    body = replace_once(body, anchor, r'''    if (uploadUntil != 0) {
        logInfo("Vita footstep stages name=%s cold=%d bytes=%u allocate_ms=%.2f read_ms=%.2f "
                "prepare_ms=%.2f upload_ms=%.2f play_ms=%.2f\n",
                sound->name, (int)cold, effectBytes, (loadFrom - allocateFrom) * 1e-6,
                (loadUntil - loadFrom) * 1e-6, (uploadFrom - loadUntil) * 1e-6,
                (uploadUntil - uploadFrom) * 1e-6, (nowNanos() - uploadUntil) * 1e-6);
    }
''' + anchor)
    return code[:start] + body + code[end:]
