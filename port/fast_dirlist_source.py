"""file_find_first lists a directory through the overlay file system, which stat()ed every entry to learn whether it is
a directory. On the Vita's card a stat costs ~47 ms, and HLD's startup (MapLvlFilePaths) lists the nine level folders,
1558 entries: ~73 s on the splash screen's "Loading the opening room" step. The Vita's readdir already returns each
entry's SceIoStat (d_stat), so its mode is used directly: the same answer without the extra card round trip."""

from runtime_verification_source import replace_once


def with_fast_dirlist(code):
    old = "        struct stat st;\n        if (stat(full, &st) == 0) isDir = S_ISDIR(st.st_mode);\n"
    new = ("#ifdef PLATFORM_VITA\n"
           "        isDir = SCE_S_ISDIR(ent->d_stat.st_mode);\n"
           "#else\n" + old + "#endif\n")
    code = replace_once(code, old, new)
    return "#ifdef PLATFORM_VITA\n#include <psp2/io/stat.h>\n#endif\n" + code
