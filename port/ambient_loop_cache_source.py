"""Play short ambient loops from the decoded-buffer cache instead of streaming them.

The 2016 game attaches a looping MUS_AMB_* sound to each vat, organ tube, flame bottle, torch and so on; it starts
when the player comes within range and stops when they leave. On the Vita every MUS_ loop was streamed: each start
opened a Vorbis decoder and decoded four buffers (~30 ms of main-thread time), and every playing loop decoded more
on the audio worker each frame, which the main thread then waits for. In a room full of vats that meant 150+ restarts
and frames of 90-180 ms.

Most of these files are tiny (14-160 KB of Vorbis). The first time one starts it still streams, and a low-priority
worker on its own core decodes the whole file to PCM. Once that is done, the next start uploads it into the effect
buffer cache (effect_buffer_cache_source) and plays it through the regular external-sound path: the cached buffer is
attached to a new source with AL_LOOPING, so later starts cost no decoding at all and nothing streams. Only loops the
cache has room for are decoded; long music keeps streaming.
"""

from runtime_verification_source import replace_once

CONDITION = '''    if (sound != nullptr && !inAudo && loop && ma->base.dw->gen8.gameID == 742741103 &&
        ma->base.dw->gen8.wadVersion == 15 && sound->name != nullptr &&
        strncmp(sound->name, "MUS_", 4) == 0) {'''

# Large external effects (the boss door music, wind and snow beds, jingles: 100-450 KB of Vorbis, up to 8 MB of PCM)
# were decoded whole on the main thread at every play (too big for the cache): 2.4 s for the door music. They stream.
NEW_CONDITION = '''    if (sound != nullptr && !inAudo && ma->base.dw->gen8.gameID == 742741103 &&
        ma->base.dw->gen8.wadVersion == 15 && sound->name != nullptr &&
        ((loop && strncmp(sound->name, "MUS_", 4) == 0 && !hldLoopUseCache(ma, soundIndex, sound)) ||
         hldStreamLargeEffect(ma, soundIndex, sound))) {'''

HELPER = r'''
// ===[ Ambient loop cache ]===
enum { HLD_LOOP_CACHE_FILE_LIMIT = 160 * 1024, HLD_LOOP_IDLE = 0, HLD_LOOP_QUEUED = 1, HLD_LOOP_READY = 2, HLD_LOOP_DONE = 3 };
typedef struct { int state; char* path; short* pcm; int samples, channels, rate; size_t reserve; bool uploaded; } HldLoopJob;
static HldLoopJob* hldLoopJobs;
static int32_t* hldLoopFileBytes; // per sound: -1 unknown, else the .ogg size
static uint32_t hldLoopCount;
static size_t hldLoopPending;     // PCM bytes reserved by queued and ready jobs
static SceUID hldLoopThread = -1;
static unsigned hldLoopUploads;

static int hldLoopWorker(SceSize args, void* argp) {
    (void) args; (void) argp;
    for (;;) {
        bool worked = false;
        for (uint32_t i = 0; i < hldLoopCount; ++i) {
            HldLoopJob* job = &hldLoopJobs[i];
            if (__atomic_load_n(&job->state, __ATOMIC_ACQUIRE) != HLD_LOOP_QUEUED) continue;
            int channels = 0, rate = 0;
            short* pcm = NULL;
            const int samples = stb_vorbis_decode_filename(job->path, &channels, &rate, &pcm);
            free(job->path);
            job->path = NULL;
            if (samples <= 0 || pcm == NULL || (channels != 1 && channels != 2) || rate <= 0) {
                free(pcm);
                __atomic_store_n(&job->state, HLD_LOOP_DONE, __ATOMIC_RELEASE);  // stays streamed
                continue;
            }
            job->pcm = pcm; job->samples = samples; job->channels = channels; job->rate = rate;
            __atomic_store_n(&job->state, HLD_LOOP_READY, __ATOMIC_RELEASE);
            worked = true;
        }
        sceKernelDelayThread(worked ? 1000 : 20000);
    }
    return 0;
}

bool hldLoopUseCache(AlAudioSystem* ma, int32_t soundIndex, Sound* sound) {
    if (soundIndex < 0) return false;
    const DataWin* dw = ma->base.audioGroups[0];
    if (dw == NULL || (uint32_t) soundIndex >= dw->sond.count) return false;
    if (hldSfxBuffers != NULL && (uint32_t) soundIndex < hldSfxCount && hldSfxBuffers[soundIndex] != 0) return true;
    if (hldLoopJobs == NULL) {
        hldLoopCount = dw->sond.count;
        hldLoopJobs = (HldLoopJob*) calloc(hldLoopCount, sizeof(HldLoopJob));
        hldLoopFileBytes = (int32_t*) malloc(hldLoopCount * sizeof(int32_t));
        if (hldLoopJobs == NULL || hldLoopFileBytes == NULL) { hldLoopCount = 0; return false; }
        for (uint32_t i = 0; i < hldLoopCount; ++i) hldLoopFileBytes[i] = -1;
        hldLoopThread = sceKernelCreateThread("HldLoopDecode", hldLoopWorker, 160, 0x10000, 0, SCE_KERNEL_CPU_MASK_USER_2, NULL);
        if (hldLoopThread >= 0 && sceKernelStartThread(hldLoopThread, 0, NULL) < 0) {
            sceKernelDeleteThread(hldLoopThread);
            hldLoopThread = -1;
        }
    }
    if (hldLoopThread < 0 || (uint32_t) soundIndex >= hldLoopCount) return false;
    HldLoopJob* job = &hldLoopJobs[soundIndex];
    const int state = __atomic_load_n(&job->state, __ATOMIC_ACQUIRE);
    if (state == HLD_LOOP_READY) {
        const size_t bytes = (size_t) job->samples * (size_t) job->channels * sizeof(int16_t);
        hldLoopPending -= job->reserve;
        ALuint buffer = 0;
        alGetError();
        alGenBuffers(1, &buffer);
        if (alGetError() == AL_NO_ERROR) {
            alBufferData(buffer, job->channels == 2 ? AL_FORMAT_STEREO16 : AL_FORMAT_MONO16, job->pcm, (ALsizei) bytes, job->rate);
            if (alGetError() != AL_NO_ERROR) { alDeleteBuffers(1, &buffer); buffer = 0; }
        } else buffer = 0;
        free(job->pcm);
        job->pcm = NULL;
        job->state = HLD_LOOP_DONE;
        if (buffer == 0) return false;
        if (!hldSfxEnsureTables(dw) || !hldSfxMakeRoom(ma, bytes)) { alDeleteBuffers(1, &buffer); return false; }
        hldSfxBuffers[soundIndex] = buffer;
        hldSfxBytesOf[soundIndex] = (uint32_t) bytes;
        hldSfxLastUse[soundIndex] = ++hldSfxClock;
        hldSfxBytes += bytes;
        ++hldSfxStores;
        job->uploaded = true;
        if (++hldLoopUploads % 10 == 1) logInfo("Audio: cached ambient loop %s bytes=%u loops=%u cache_bytes=%u\n",
                                                sound->name, (unsigned) bytes, hldLoopUploads, (unsigned) hldSfxBytes);
        return true;
    }
    if (state == HLD_LOOP_DONE && job->uploaded) job->state = HLD_LOOP_IDLE;  // evicted from the cache: decode it again
    else if (state != HLD_LOOP_IDLE) return false;
    if (hldLoopFileBytes[soundIndex] < 0) {
        char* path = resolveExternalPath(ma, sound);
        int32_t fileBytes = INT32_MAX;
        if (path != NULL) {
            FILE* file = fopen(path, "rb");
            if (file != NULL) {
                if (fseek(file, 0, SEEK_END) == 0) { const long end = ftell(file); if (end > 0) fileBytes = (int32_t) end; }
                fclose(file);
            }
            free(path);
        }
        hldLoopFileBytes[soundIndex] = fileBytes;
    }
    job->uploaded = false;
    const int32_t fileBytes = hldLoopFileBytes[soundIndex];
    // Vorbis at these rates decodes to well under 16x its size; keep that much headroom in the cache.
    const size_t reserve = (size_t) fileBytes * 16;
    if (fileBytes > HLD_LOOP_CACHE_FILE_LIMIT || hldLoopPending + reserve > HLD_SFX_CACHE_LIMIT / 2) {
        job->state = HLD_LOOP_DONE;  // streams, as before
        return false;
    }
    job->path = resolveExternalPath(ma, sound);
    if (job->path == NULL) { job->state = HLD_LOOP_DONE; return false; }
    job->reserve = reserve;
    hldLoopPending += reserve;
    __atomic_store_n(&job->state, HLD_LOOP_QUEUED, __ATOMIC_RELEASE);
    return false;  // this start streams; the decoded copy is used from the next start on
}

enum { HLD_STREAM_EFFECT_FILE_BYTES = 96 * 1024 };
static int32_t* hldExternalBytes; // per sound: -1 unknown, else the external file's size
bool hldStreamLargeEffect(AlAudioSystem* ma, int32_t soundIndex, Sound* sound) {
    if (strncmp(sound->name, "MUS_", 4) == 0 || soundIndex < 0) return false;
    const DataWin* dw = ma->base.audioGroups[0];
    if (dw == NULL || (uint32_t) soundIndex >= dw->sond.count) return false;
    if (hldExternalBytes == NULL) {
        hldExternalBytes = (int32_t*) malloc(dw->sond.count * sizeof(int32_t));
        if (hldExternalBytes == NULL) return false;
        for (uint32_t i = 0; i < dw->sond.count; ++i) hldExternalBytes[i] = -1;
    }
    if (hldExternalBytes[soundIndex] < 0) {
        char* path = resolveExternalPath(ma, sound);
        int32_t bytes = 0;
        if (path != NULL) {
            FILE* file = fopen(path, "rb");
            if (file != NULL) {
                // Only Vorbis files stream here; anything else keeps the regular path.
                char magic[4] = {0};
                if (fread(magic, 1, 4, file) == 4 && memcmp(magic, "OggS", 4) == 0 && fseek(file, 0, SEEK_END) == 0) {
                    const long end = ftell(file);
                    if (end > 0) bytes = (int32_t) end;
                }
                fclose(file);
            }
            free(path);
        }
        hldExternalBytes[soundIndex] = bytes;
        if (bytes > HLD_STREAM_EFFECT_FILE_BYTES) logInfo("Audio: streaming large effect %s (%d KB)\n", sound->name, bytes / 1024);
    }
    return hldExternalBytes[soundIndex] > HLD_STREAM_EFFECT_FILE_BYTES;
}
'''


def with_ambient_loop_cache(code):
    assert "static ALuint* hldSfxBuffers;" in code, "needs effect_buffer_cache_source first"
    code = replace_once(code, CONDITION, NEW_CONDITION)
    code = replace_once(code, "static int32_t maPlaySound(AudioSystem* audio, int32_t soundIndex, int32_t priority, bool loop) {",
                        "bool hldLoopUseCache(AlAudioSystem* ma, int32_t soundIndex, Sound* sound);\n"
                        "bool hldStreamLargeEffect(AlAudioSystem* ma, int32_t soundIndex, Sound* sound);\n"
                        "static int32_t maPlaySound(AudioSystem* audio, int32_t soundIndex, int32_t priority, bool loop) {")
    return code + HELPER


def with_room_sound_hook(code):
    """runner.c: tell the effect cache's warm worker which room started (effect_buffer_cache_source prefetches)."""
    anchor = "    Room* room = &dataWin->room.rooms[roomIndex];\n"
    return replace_once(code, anchor, anchor + "    { extern void hldSfxRoomStarted(const char*) __attribute__((weak)); "
                                               "if (hldSfxRoomStarted) hldSfxRoomStarted(room->name); }\n")
