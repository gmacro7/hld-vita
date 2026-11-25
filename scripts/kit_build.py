#!/usr/bin/env python3
"""HLD Vita Builder: make the Hyper Light Drifter PS Vita app from your own copy of the game, in one step.

Run it through the launcher for your computer ("HLD Vita Builder.bat" on Windows, "HLD Vita Builder.command" on
macOS), or directly: python3 scripts/kit_build.py [--qr] [--game FOLDER]

What it does, all in this folder:
 1. downloads the pinned tools it needs (VitaSDK compiler, UndertaleModTool, Python packages; checksummed);
 2. finds the August 2016 build of the game on this computer, or downloads it from Steam with your account;
 3. decompiles the game scripts the port translates, from your data.win, and repacks its textures for the Vita;
 4. translates those scripts to C and compiles them with the interpreter (about a minute);
 5. links them with the port's precompiled engine (prebuilt/) into build/HyperLightDrifter-HLDV00001.vpk;
 6. optionally copies everything to your Vita over FTP.

The precompiled engine contains no game code; everything made from the game stays on this computer. Do not share the
VPK or the work/ folder.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "deps"
WORK = ROOT / "work"
OUT = ROOT / "port" / "build-out"
sys.path.insert(0, str(ROOT / "scripts"))
import bootstrap  # noqa: E402
from bootstrap import exe, venv_python, WINDOWS  # noqa: E402

TITLE_ID = os.environ.get("HLD_TITLE_ID", "HLDV00001")


def step(text):
    print(f"\n==> {text}", flush=True)


LOG = OUT / "compile.log"


def quiet(argv, what):
    """Run a compiler/linker step with its (warning-heavy) output in port/build-out/compile.log; show the tail only
    if it fails."""
    with LOG.open("a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n### {what}\n")
        log.flush()
        result = subprocess.run(argv, cwd=OUT, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        tail = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        print("\n".join(tail))
        raise SystemExit(f"{what} failed; the full output is in {LOG}")


def ensure_python_packages():
    """Re-run inside deps/venv (numpy, Pillow, lz4) if this Python is not it."""
    venv = DEPS / "venv"
    python = venv_python(venv)
    if Path(sys.prefix).resolve() == venv.resolve():
        return
    step("Setting up Python packages (first run only)")
    bootstrap.install_venv()
    # Continue inside the virtual environment (a child process, which also works on Windows).
    sys.exit(subprocess.run([str(python), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)


def ensure_tools():
    name = bootstrap.host()
    step("Getting the VitaSDK compiler and UndertaleModTool (first run only, ~170 MB)")
    sdk = bootstrap.install_sdk(name)
    bootstrap.install_utmt(name)
    encoder = DEPS / ("bc3-encoder.dylib" if sys.platform == "darwin" else
                      "bc3-encoder.dll" if WINDOWS else "bc3-encoder.so")
    shipped = DEPS / "bc3" / "prebuilt" / f"bc3-encoder-{name}{encoder.suffix}"
    if not encoder.is_file() and shipped.is_file():
        shutil.copyfile(shipped, encoder)  # stb_dxt, cross-compiled for this system (scripts/make_kit.py)
    if not encoder.is_file() and not WINDOWS:
        try:
            bootstrap.build_bc3_encoder()
        except (SystemExit, subprocess.CalledProcessError):
            pass
    if not encoder.is_file():
        print("No texture encoder for this system: using Pillow's instead (slightly lower quality).")
    return sdk


def generate_sources(recipe):
    """vm-portable.c and the translated parts, from your decompiled scripts (port/portable_vm_source.py)."""
    os.environ.update(recipe["environment"])
    os.environ["HLDC_TRANSLATE"] = str(ROOT / "port" / Path(recipe["environment"]["HLDC_TRANSLATE"]).name)
    os.environ["HLD_DATA_WIN"] = str(WORK / "game" / "data.win")
    sys.path.insert(0, str(ROOT / "port"))
    os.chdir(ROOT / "port")
    from portable_vm_source import portable_vm_source, PART_SOURCES
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vm-portable.c").write_text(portable_vm_source((ROOT / "port" / "vm-base.c").read_text()))
    parts = []
    for number, source in enumerate(PART_SOURCES):
        path = OUT / f"hldc-part-{number}.c"
        path.write_text(source)
        parts.append(path)
    return parts


def substitute(argv, sdk):
    mapping = {"{SDK}": str(sdk), "{KIT}": str(ROOT), "{OUT}": str(OUT)}
    result = []
    for token in argv:
        for key, value in mapping.items():
            token = token.replace(key, value)
        result.append(token)
    return result


def compiler(argv, sdk):
    argv = list(argv)
    argv[0] = str(exe(sdk / "bin" / "arm-vita-eabi-gcc"))
    return argv


def compile_game_code(recipe, sdk, parts):
    step(f"Compiling the interpreter and {len(parts)} translated parts")
    vm = compiler(substitute(recipe["vm_compile"], sdk), sdk)
    LOG.unlink(missing_ok=True)
    result = subprocess.run(vm, cwd=OUT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    LOG.write_text("### interpreter (vm-portable.c)\n" + result.stdout + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        print("\n".join(result.stderr.splitlines()[-25:]))
        raise SystemExit(f"The interpreter failed to compile; the full output is in {LOG}")
    unmatched = result.stderr.count("[-Wmissing-profile]") + result.stderr.count("[-Wcoverage-mismatch]")
    print(f"interpreter compiled ({unmatched} functions without matching profile data)")
    base = compiler(substitute(recipe["part_compile"], sdk), sdk)
    source_index = next(i for i, token in enumerate(base) if token.endswith(".c") and not token.startswith("-"))
    print("(this takes a few minutes and prints nothing until it is done)", flush=True)
    objects, running = [], []
    for part in parts:
        argv = list(base)
        argv[source_index] = part.name
        argv[argv.index("-o") + 1] = part.with_suffix(".obj").name
        argv[argv.index("-MF") + 1] = part.with_suffix(".d").name
        objects.append(part.with_suffix(".obj"))
        log = part.with_suffix(".log").open("w", encoding="utf-8", errors="replace")
        running.append((subprocess.Popen(argv, cwd=OUT, stdout=log, stderr=subprocess.STDOUT), log, part))
    for process, log, part in running:
        code = process.wait()
        log.close()
        text = part.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
        with LOG.open("a", encoding="utf-8", errors="replace") as combined:
            combined.write(f"\n### {part.name}\n{text}")
        if code != 0:
            print("\n".join(text.splitlines()[-25:]))
            raise SystemExit(f"{part.name} failed to compile; the full output is in {LOG}")
    print(f"compiled {len(parts)} translated parts", flush=True)
    return [OUT / "vm.obj", *objects]


def link(recipe, sdk, objects):
    step("Linking the app")
    argv = []
    for token in compiler(substitute(recipe["link"], sdk), sdk):
        if token == "{GAME_OBJECTS}":
            argv += [str(p) for p in objects]
        else:
            argv.append(token)
    quiet(argv, "Linking")
    elf = OUT / "butterscotch"
    quiet([str(exe(sdk / "bin" / "vita-elf-create")), str(elf), str(OUT / "butterscotch.velf")], "vita-elf-create")
    quiet([str(exe(sdk / "bin" / "vita-make-fself")), "-c", str(OUT / "butterscotch.velf"), str(OUT / "eboot.bin")],
          "vita-make-fself")
    sfo = OUT / "param.sfo"
    subprocess.run([str(exe(sdk / "bin" / "vita-mksfoex")), "-s", f"TITLE_ID={TITLE_ID}", "-s",
                    f"APP_VER={recipe['version']}", "-s", f"CONTENT_ID=HB0001-{TITLE_ID}_00-0000000000000000",
                    "-d", "ATTRIBUTE2=12", "Hyper Light Drifter", str(sfo)], check=True)
    package = ROOT / "build" / f"HyperLightDrifter-{TITLE_ID}.vpk"
    package.parent.mkdir(exist_ok=True)
    from make_livearea import livearea_folder
    art = livearea_folder(ROOT)  # from your game's splash.png (work/livearea), or the plain placeholders
    with ZipFile(package, "w", ZIP_DEFLATED) as zipped:
        zipped.write(sfo, "sce_sys/param.sfo")
        zipped.write(OUT / "eboot.bin", "eboot.bin")
        zipped.write(art / "icon0.png", "sce_sys/icon0.png")
        for name in ("bg.png", "startup.png", "template.xml"):
            zipped.write(art / name, f"sce_sys/livearea/contents/{name}")
    return package


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", type=Path, help="the folder with the 2016 data.win (found or downloaded otherwise)")
    parser.add_argument("--qr", action="store_true", help="sign in to Steam with a QR code when downloading")
    parser.add_argument("--vita", help="your Vita's IP address: copy everything to it over FTP when done")
    options = parser.parse_args()
    if sys.version_info < (3, 12):
        raise SystemExit("Python 3.12 or newer is required (the launchers download one if needed).")
    ensure_python_packages()
    recipe = json.loads((ROOT / "kit.json").read_text())
    sdk = ensure_tools()
    step("Finding your 2016 copy of Hyper Light Drifter")
    import prepare_game
    import fetch_game
    folder = options.game.expanduser() if options.game else fetch_game.locate(options.qr)
    data = prepare_game.check_game(folder)
    prepare_game.link_game(folder)
    step("Decompiling the game scripts the port uses")
    prepare_game.decompile(data)
    step("Repacking the textures for the Vita (10-20 minutes the first time)")
    prepare_game.textures(data)
    step("Translating the game scripts to C")
    parts = generate_sources(recipe)
    objects = compile_game_code(recipe, sdk, parts)
    package = link(recipe, sdk, objects)
    print(f"\nDone: {package}")
    vita = options.vita
    if vita is None and sys.stdin.isatty():
        vita = input("Copy it to your Vita now? Start FTP on the Vita (VitaShell: SELECT), then type its IP address, "
                     "or press Enter to skip: ").strip() or None
    if vita:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "install.py"), vita], check=True)
    else:
        print("To copy it later: python3 scripts/install.py <vita-ip>  (see README.txt)")


if __name__ == "__main__":
    main()
