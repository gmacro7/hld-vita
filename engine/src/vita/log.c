#include <stdio.h>

#include "log.h"

static FILE* vitaLog;

void vitaOpenLog(void) {
    vitaLog = fopen("ux0:data/hld-vm/runtime.log", "w");
}

void vitaCloseLog(void) {
    if (vitaLog != NULL) fclose(vitaLog);
    vitaLog = NULL;
}

void platformLog(const logType type, const char* format, va_list va) {
    if (vitaLog == NULL) return;
    switch (type) {
        case LOG_TYPE_WARNING: fputs("Warning: ", vitaLog); break;
        case LOG_TYPE_ERROR: fputs("Error: ", vitaLog); break;
        case LOG_TYPE_DEBUG: fputs("Debug: ", vitaLog); break;
        case LOG_TYPE_NORMAL: break;
    }
    vfprintf(vitaLog, format, va);
    fflush(vitaLog);
}
