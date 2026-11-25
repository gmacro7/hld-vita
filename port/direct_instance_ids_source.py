"""Accelerate the current room's exact-ID lookups with an indexed pointer table."""

import re

from runtime_verification_source import replace_once


DECLARATIONS = r'''
enum { HLD_DIRECT_ID_COUNT = 16384 };
extern const Runner* hldDirectIdOwner;
extern Instance* hldDirectIds[HLD_DIRECT_ID_COUNT];
extern unsigned hldDirectIdHits, hldDirectIdFallbacks;

static inline Instance* hldLookupInstanceId(Runner* runner, uint32_t id) {
    const uint32_t index = id - INSTANCE_ID_BASE;
    if (runner == hldDirectIdOwner && index < HLD_DIRECT_ID_COUNT) {
        Instance* result = hldDirectIds[index];
#ifdef HLD_VERIFY_DIRECT_IDS
        require(result == hmget(runner->instancesById, id));
#endif
        ++hldDirectIdHits;
        return result;
    }
    ++hldDirectIdFallbacks;
    return hmget(runner->instancesById, id);
}

'''


def with_direct_id_lookups(code, include, *, verify=False):
    code, reads = re.subn(r"\bhmget\((runner|ctx->runner)->instancesById,\s*",
                         r"hldLookupInstanceId(\1, ", code)
    assert reads > 0, include
    prefix = "#define HLD_VERIFY_DIRECT_IDS\n" if verify else ""
    return replace_once(code, include, include + "\n\n" + prefix + DECLARATIONS)


def with_direct_id_runner(code, *, launch_flag=None, verify=False):
    include = '#include "stb_ds.h"'
    code = with_direct_id_lookups(code, include, verify=verify)
    gate = f"extern bool {launch_flag};\n" if launch_flag else ""
    enabled = f"{launch_flag} && " if launch_flag else ""
    bound = r'''
const Runner* hldDirectIdOwner;
Instance* hldDirectIds[HLD_DIRECT_ID_COUNT];
unsigned hldDirectIdHits, hldDirectIdFallbacks;

static inline void mirrorInstanceId(Runner* runner, uint32_t id, Instance* value) {
    const uint32_t index = id - INSTANCE_ID_BASE;
    if (runner == hldDirectIdOwner && index < HLD_DIRECT_ID_COUNT) hldDirectIds[index] = value;
}

static inline void storeInstanceId(Runner* runner, uint32_t id, Instance* value) {
    hmput(runner->instancesById, id, value);
    mirrorInstanceId(runner, id, value);
}

static inline void removeInstanceId(Runner* runner, uint32_t id) {
    hmdel(runner->instancesById, id);
    mirrorInstanceId(runner, id, NULL);
}
'''
    report = r'''
void hldReportDirectIds(int frame) {
    if (frame <= 0 || frame % 90 != 0) return;
    logInfo("Vita direct instance IDs frame=%d enabled=%d indexed=%u fallback=%u\n",
            frame, (int)(hldDirectIdOwner != NULL), hldDirectIdHits, hldDirectIdFallbacks);
    hldDirectIdHits = hldDirectIdFallbacks = 0;
}
''' if launch_flag else r'''
void hldReportDirectIdsAtExit(void) {
    if (hldDirectIdOwner != NULL)
        logInfo("HLD: direct instance IDs indexed=%u fallback=%u\n", hldDirectIdHits, hldDirectIdFallbacks);
}
'''

    code, puts = re.subn(r"\bhmput\(runner->instancesById,\s*", "storeInstanceId(runner, ", code)
    code, deletes = re.subn(r"\bhmdel\(runner->instancesById,\s*", "removeInstanceId(runner, ", code)
    assert puts == 2 and deletes == 9, (puts, deletes)
    anchor = "    runner->dataWin = dataWin;"
    code = replace_once(code, anchor, anchor + f'''
    if ({enabled}hldDirectIdOwner == NULL && dataWin->gen8.gameID == 742741103 &&
        dataWin->gen8.wadVersion == 15) {{
        hldDirectIdOwner = runner;
        hldDirectIdHits = hldDirectIdFallbacks = 0;
    }}''')
    anchor = "    hmfree(runner->instancesById);"
    code = replace_once(code, anchor, anchor + r'''
    if (hldDirectIdOwner == runner) memset(hldDirectIds, 0, sizeof(hldDirectIds));''')
    anchor = """void Runner_free(Runner* runner) {
    if (runner == nullptr) return;

    cleanupState(runner);"""
    code = replace_once(code, anchor, anchor + "\n    if (hldDirectIdOwner == runner) hldDirectIdOwner = NULL;")
    return replace_once(code, DECLARATIONS, DECLARATIONS + bound + gate + report)


def with_direct_id_host_exit(code):
    anchor = "            if (args.exitAtFrame >= 0 && runner->frameCount >= args.exitAtFrame) {"
    return replace_once(code, anchor, anchor + """
                extern void hldReportDirectIdsAtExit(void);
                hldReportDirectIdsAtExit();""")
