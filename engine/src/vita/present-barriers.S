.syntax unified
.arm
.text
.p2align 2

.macro VITA_GL_BARRIER name
    .global __wrap_\name
    .type __wrap_\name, %function
__wrap_\name:
    ldr r12, =vitaPresentPending
    ldr r12, [r12]
    cmp r12, #0
    beq __real_\name
    push {r0-r3,r12,lr}
    bl vitaWaitForPresent
    pop {r0-r3,r12,lr}
    b __real_\name
    .size __wrap_\name, . - __wrap_\name
    .ltorg
.endm

#define VITA_GL_ENTRY(name) VITA_GL_BARRIER name
#include "present-entrypoints.def"
#undef VITA_GL_ENTRY

.section .note.GNU-stack,"",%progbits
