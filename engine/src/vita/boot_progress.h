#ifndef BS_VITA_BOOT_PROGRESS_H
#define BS_VITA_BOOT_PROGRESS_H

typedef enum {
    VITA_BOOT_ARCHIVE,
    VITA_BOOT_VM,
    VITA_BOOT_GRAPHICS,
    VITA_BOOT_ROOM
} VitaBootPhase;

void VitaBootProgress_begin(void);
void VitaBootProgress_set(VitaBootPhase phase, unsigned completed, unsigned total);
void VitaBootProgress_end(void);
void VitaBootProgress_gamePresented(void);

#endif
