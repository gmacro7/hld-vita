#include <errno.h>
#include <reent.h>
#include <stddef.h>
#include <psp2/kernel/rng.h>

/* Newlib's getentropy wrapper expects the target to provide this hook. */
int _getentropy_r(struct _reent *reent, void *output, size_t length) {
    if (length > 256) {
        reent->_errno = EIO;
        return -1;
    }

    unsigned char *cursor = output;
    while (length > 0) {
        SceSize chunk = length > 64 ? 64 : (SceSize)length;
        if (sceKernelGetRandomNumber(cursor, chunk) < 0) {
            reent->_errno = EIO;
            return -1;
        }
        cursor += chunk;
        length -= chunk;
    }
    return 0;
}
