"""The interpreter source as the port builds it: native serialization / native-only / translated GML / native
scripts / compiled entries applied to the transformed vm.c, in the same order as the feedback build. Shared by
build-hldc-vita.py and the player kit (kit/build_vpk.py), so both compile the identical vm-portable.c."""
import json
import os
from pathlib import Path

HLDC_ENTRIES = json.loads(os.environ.get("HLDC_ENTRIES", "[]"))
HLDC_NATIVE_SERIALIZE = os.environ.get("HLDC_NATIVE_SERIALIZE") == "1"
HLDC_NATIVE_SCRIPTS = os.environ.get("HLDC_NATIVE_SCRIPTS") == "1"
HLDC_NATIVE_ONLY = os.environ.get("HLDC_NATIVE_ONLY") == "1"
HLDC_TRANSLATE = os.environ.get("HLDC_TRANSLATE")
PART_SOURCES = []


def portable_vm_source(generated):
    """vm.c as the port builds it: native serialization / native-only / translated GML / native scripts / compiled
    entries applied in the same order as the feedback build."""
    if HLDC_NATIVE_SERIALIZE:
        from native_serialize_source import with_native_serialize
        generated = with_native_serialize(generated)
    if HLDC_NATIVE_ONLY:
        from native_only_source import with_native_only_vm
        generated = with_native_only_vm(generated)
    if HLDC_TRANSLATE:
        from translated_gml_source import all_entries, with_translated_gml
        names = all_entries() if HLDC_TRANSLATE == "all" else \
            [line.strip() for line in Path(HLDC_TRANSLATE).read_text().splitlines() if line.strip()]
        generated, parts = with_translated_gml(generated, names, stats=os.environ.get("HLDC_STATS") == "1")
        PART_SOURCES[:] = parts
    if HLDC_NATIVE_SCRIPTS:
        from native_scripts_source import with_native_scripts
        generated = with_native_scripts(generated)
    if HLDC_ENTRIES:
        from gml_compiler_source import with_compiled_gml
        generated = with_compiled_gml(generated, HLDC_ENTRIES)
    return generated
