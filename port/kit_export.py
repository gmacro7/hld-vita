"""Export the player kit from a full build (HLDC_EXPORT_KIT=<dir>, set by scripts/make_kit.py).

The kit holds everything game-free that the final steps need, so a player only compiles what is made from their own
copy of the game (the interpreter with the translated scripts, and the translated parts) and links:

  prebuilt/          every object and library the link used, except the SDK's own files and the game-derived objects
  engine/, deps/     the headers the interpreter and parts include, at the same paths relative to port/build-out
                     (the interpreter's GCC profile is keyed to these relative names)
  port/vm-base.c     the transformed interpreter before the scripts are translated into it (no game code)
  kit.json           the compile and link commands, with {SDK} (the player's VitaSDK) and {KIT} (the kit folder)

The link is run once with --trace to list exactly which inputs it uses.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def export_kit(kit, recorded, link, build, out, sdk_bin, root, part_objects, version):
    kit = kit.resolve()
    if kit.exists():
        shutil.rmtree(kit)
    (kit / "prebuilt" / "lib").mkdir(parents=True)
    sdk = sdk_bin.parent.resolve()
    root = root.resolve()
    if "vm_argv" not in recorded or "part_argv" not in recorded:
        raise SystemExit("HLDC_EXPORT_KIT needs the portable profile build (HLDC_PORTABLE_PROFILE=1, --vm-feedback)")
    game_objects = {Path(recorded["vm_object"]).resolve(), *(Path(p).resolve() for p in part_objects)}

    # Which files does the link really use?
    trace_out = out / "kit-trace.elf"
    traced = list(link)
    traced[traced.index("-o") + 1] = str(trace_out)
    result = subprocess.run(traced + ["-Wl,--trace"], cwd=build, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit("kit export: trace link failed\n" + result.stderr[-4000:])
    trace_out.unlink(missing_ok=True)
    used = set()
    for match in re.finditer(r"(/[^\s()]+\.(?:a|o|obj|ld|x))", result.stdout + result.stderr):
        path = Path(match.group(1))
        if path.is_file():
            used.add(path.resolve())

    copied = {}

    def placeholder(path):
        path = Path(path)
        if not path.is_absolute():
            path = build / path
        path = path.resolve()
        if path in game_objects:
            return None
        if path.is_relative_to(sdk):
            return "{SDK}/" + path.relative_to(sdk).as_posix()
        if path not in copied:
            name = f"{len(copied):03d}-{path.name}"
            shutil.copy2(path, kit / "prebuilt" / name)
            copied[path] = "{KIT}/prebuilt/" + name
        return copied[path]

    # Archives found through -L directories outside the SDK go to prebuilt/lib under their own names.
    library_dirs = []
    for i, token in enumerate(link):
        if token == "-L":
            library_dirs.append(Path(link[i + 1]))
        elif token.startswith("-L"):
            library_dirs.append(Path(token[2:]))
    library_dirs = [d if d.is_absolute() else (build / d) for d in library_dirs]
    library_dirs = [d.resolve() for d in library_dirs]
    for path in used:
        if not path.is_relative_to(sdk) and path.suffix == ".a" and path.parent in library_dirs:
            shutil.copy2(path, kit / "prebuilt" / "lib" / path.name)

    template = []
    inserted = False
    skip = False
    for i, token in enumerate(link):
        if skip:
            skip = False
            continue
        if i == 0:
            template.append(placeholder(token))
            continue
        if token == "-o":
            template += ["-o", "{OUT}/butterscotch"]
            skip = True
            continue
        if token.startswith("-L"):
            directory = Path(token[2:] if len(token) > 2 else link[i + 1])
            directory = (directory if directory.is_absolute() else build / directory).resolve()
            mapped = ("{SDK}/" + directory.relative_to(sdk).as_posix()) if directory.is_relative_to(sdk) \
                else "{KIT}/prebuilt/lib"
            template.append("-L" + mapped)
            skip = len(token) == 2
            continue
        candidate = Path(token) if not token.startswith("-") else None
        if candidate is not None and (candidate if candidate.is_absolute() else build / candidate).is_file():
            mapped = placeholder(token)
            if mapped is None:
                if not inserted:
                    template.append("{GAME_OBJECTS}")
                    inserted = True
                continue
            template.append(mapped)
            continue
        if token.startswith("-") and (str(root) in token or str(sdk) in token):
            # Compile flags the CMake link line carries (-I...): harmless at link time; keep them pointing at the kit.
            template.append(token.replace(str(sdk), "{SDK}").replace(str(root), "{KIT}"))
            continue
        if str(root) in token or str(sdk) in token:
            raise SystemExit(f"kit export: unhandled path in link argument {token}")
        template.append(token)
    if not inserted:
        raise SystemExit("kit export: the link has no game-derived objects")

    def relative_command(argv):
        result = []
        for token in argv:
            token = token.replace(str(sdk), "{SDK}").replace(str(root), "{KIT}")
            result.append(token)
        return result

    # Headers: everything the interpreter and the parts include from this checkout, at the same relative paths.
    headers = set()
    for dep in [out / "vm.d", *(Path(p).with_suffix(".d") for p in part_objects)]:
        if not dep.is_file():
            continue
        text = dep.read_text().replace("\\\n", " ")
        for name in text.split(":", 1)[1].split():
            path = (out / name).resolve() if not os.path.isabs(name) else Path(name).resolve()
            if path.is_relative_to(root) and path.suffix in (".h", ".inc", ".def"):
                headers.add(path)
    for path in headers:
        destination = kit / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

    (kit / "port").mkdir(exist_ok=True)
    (kit / "port" / "vm-base.c").write_text(recorded["vm_base"])
    (kit / "port" / "build-out").mkdir(exist_ok=True)
    recipe = {
        "version": version,
        "vm_compile": relative_command(recorded["vm_argv"]),
        "part_compile": relative_command(recorded["part_argv"]),
        "link": template,
        "cwd": "port/build-out",
        "environment": {key: os.environ[key] for key in ("HLDC_TRANSLATE", "HLDC_NATIVE_SCRIPTS") if key in os.environ},
    }
    (kit / "kit.json").write_text(json.dumps(recipe, indent=1) + "\n")
    print(f"kit exported to {kit}: {len(copied)} objects, {len(list((kit / 'prebuilt' / 'lib').iterdir()))} "
          f"libraries, {len(headers)} headers", flush=True)
