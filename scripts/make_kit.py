#!/usr/bin/env python3
"""Maintainers: make the player kit (build/HLD-Vita-Builder-<version>.zip) for a GitHub release.

Usage: python3 scripts/make_kit.py      (after bootstrap.py and prepare_game.py)

Runs the full build with HLDC_EXPORT_KIT so the port's build records the game-free parts (port/kit_export.py), then
adds the player scripts, the launchers, the interpreter profile, the placeholder LiveArea art and the licences. The kit
holds no game code or data: a check below fails the kit if any text from the translated scripts reached it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "build-out", "build-train"))
    else:
        shutil.copy2(source, destination)


def check_no_game_text(kit):
    """Every string literal of the translated scripts that reached the generated C must be absent from the kit."""
    out = ROOT / "port" / "build-out"
    generated = [p.read_bytes() for p in out.glob("hldc-part-*.c")] + [(out / "vm-portable.c").read_bytes()]
    names = [n.strip() for n in (ROOT / "port" / "hot-entries-v5.txt").read_text().splitlines() if n.strip()]
    literals = set()
    for name in names:
        text = (ROOT / "work" / "decompiled" / f"{name}.gml").read_text(errors="ignore")
        # Messages and names: at least two words (code fragments such as ":\n    case " also occur in the engine).
        literals.update(s for s in re.findall(r'"([^"\\]{12,60})"', text) if len(re.findall(r"[A-Za-z]{3,}", s)) >= 2)
    present = [s.encode() for s in literals if any(s.encode() in g for g in generated)]
    kit_files = [p.read_bytes() for p in kit.rglob("*") if p.is_file()]
    leaked = [s for s in present if any(s in k for k in kit_files)]
    if leaked:
        raise SystemExit(f"The kit contains text from the game's scripts: {leaked[:5]}")
    print(f"Checked: none of {len(present)} translated script literals are in the kit.")


def main():
    export = BUILD / "kit-export"
    environment = dict(os.environ, HLDC_EXPORT_KIT=str(export))
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build.py")], check=True, env=environment)
    recipe = json.loads((export / "kit.json").read_text())
    name = f"HLD-Vita-Builder-{recipe['version']}"
    kit = BUILD / "kit" / name
    shutil.rmtree(kit, ignore_errors=True)
    copy(export, kit)
    for path in ["port/hot-entries-v5.txt", "port/page_strips.h", "port/tools", "port/vm-profile",
                 "deps/dependencies.json", "deps/bc3", "assets/livearea", "LICENSE", "NOTICE.md",
                 "scripts/kit_build.py", "scripts/bootstrap.py", "scripts/fetch_game.py", "scripts/prepare_game.py",
                 "scripts/install.py", "scripts/requirements.txt", "scripts/make_livearea.py"]:
        copy(ROOT / path, kit / path)
    for module in (ROOT / "port").glob("*.py"):
        copy(module, kit / "port" / module.name)
    for include in (ROOT / "port").glob("*.inc"):
        copy(include, kit / "port" / include.name)
    for launcher in (ROOT / "kit").iterdir():
        copy(launcher, kit / launcher.name)
    # The stb_dxt texture encoder for each player system, cross-compiled from deps/bc3/encode.c, e.g. with
    #   zig cc -O2 -shared -target x86_64-windows-gnu deps/bc3/encode.c -o bc3-encoder-windows-x86_64.dll
    # (also x86_64-linux-gnu .so, x86_64-macos and aarch64-macos .dylib). Without them players compile it or use Pillow.
    encoders = Path(os.environ.get("HLD_BC3_PREBUILT", ROOT / "build" / "bc3-prebuilt"))
    for library in ("bc3-encoder-windows-x86_64.dll", "bc3-encoder-linux-x86_64.so",
                    "bc3-encoder-darwin-x86_64.dylib", "bc3-encoder-darwin-arm64.dylib"):
        if (encoders / library).is_file():
            copy(encoders / library, kit / "deps" / "bc3" / "prebuilt" / library)
        else:
            print(f"warning: no prebuilt {library}; those players compile the encoder or fall back to Pillow")
    check_no_game_text(kit)
    archive = BUILD / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(kit.rglob("*")):
            info = zipfile.ZipInfo.from_file(path, f"{name}/{path.relative_to(kit).as_posix()}")
            if path.is_dir():
                zipped.writestr(info, "")
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                zipped.writestr(info, path.read_bytes())
    print(f"Kit: {archive} ({archive.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
