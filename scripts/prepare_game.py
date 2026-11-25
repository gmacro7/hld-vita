#!/usr/bin/env python3
"""Step 2: prepare YOUR copy of Hyper Light Drifter for the build and for the Vita.

Usage: python3 scripts/prepare_game.py [--qr] [/path/to/the/2016/game/folder]

Without a folder, it finds the August 2016 build in Steam's usual places, or downloads it from Steam with your account
(scripts/fetch_game.py: ownership is checked by Steam; your password goes only to Steam).

Everything made here comes from your own game files and stays in work/ (git-ignored, never commit it):
  work/game          -> a link to your game folder (nothing is copied)
  work/decompiled/   -> the game scripts the port translates to C, decompiled from your data.win
  work/vita/textures.bin, work/vita/pagez/  -> the texture pages repacked for the Vita

Your game folder and data.win are only read. The decompiler needs one header field changed to open this 2016
data.win, so it works on a temporary copy that is deleted afterwards.
"""
import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "deps"
WORK = ROOT / "work"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap import exe, venv_python, WINDOWS  # noqa: E402
PYTHON = venv_python(DEPS / "venv")
# The August 2016 build (Steam depot 257851, manifest 2557763276340557312): the last one shipped with GameMaker
# bytecode. Later builds compile the game to native code and cannot be used.
EXPECTED_SHA256 = "8dcc8b53bd0da874410c919accf514221bf63055b95e3a3293f1e391c373af69"
EXPECTED_SIZE = 612_369_518


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_game(folder):
    data = folder / "data.win"
    if not data.is_file():
        raise SystemExit(f"No data.win in {folder}. Point this at the folder that holds the game's data.win.")
    if data.stat().st_size != EXPECTED_SIZE or sha256(data) != EXPECTED_SHA256:
        raise SystemExit("This data.win is not the supported 2016 build. See README.md, 'Getting the right game "
                         "files' (Steam console: download_depot 257850 257851 2557763276340557312).")
    return data


def link_game(folder):
    WORK.mkdir(exist_ok=True)
    link = WORK / "game"
    if link.is_symlink() or link.exists():
        if link.resolve() == folder.resolve():
            return
        link.unlink()
    if WINDOWS:
        # A directory junction needs no administrator rights, unlike a symbolic link.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(folder.resolve())], check=True,
                       stdout=subprocess.DEVNULL)
    else:
        link.symlink_to(folder.resolve(), target_is_directory=True)


def decompile(data):
    out = WORK / "decompiled"
    names = [n.strip() for n in (ROOT / "port" / "hot-entries-v5.txt").read_text().splitlines() if n.strip()]
    if out.is_dir() and all((out / f"{n}.gml").is_file() for n in names):
        print(f"Decompiled scripts already present ({len(names)}).")
        return
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hld-decompile-") as scratch:
        copy = Path(scratch) / "data.win"
        shutil.copyfile(data, copy)
        # UndertaleModTool 0.9.2 reads the two EXTN product IDs only for GameMaker builds >= 1773; this file stores
        # them but reports build 1250 (GEN8 build field, file offset 72). Raise it in the temporary copy only.
        with copy.open("r+b") as stream:
            stream.seek(72)
            assert struct.unpack("<I", stream.read(4))[0] == 1250
            stream.seek(72)
            stream.write(struct.pack("<I", 1773))
        environment = dict(os.environ, HLD_DECOMPILE_LIST=str(ROOT / "port" / "hot-entries-v5.txt"),
                           HLD_DECOMPILE_OUT=str(out), DOTNET_CLI_TELEMETRY_OPTOUT="1")
        from progress import Bar
        bar = Bar(len(names), "scripts decompiled")
        done = 0
        with (WORK / "decompile.log").open("w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen([str(exe(DEPS / "utmt" / "UndertaleModCli")), "load", str(copy), "-s",
                                        str(ROOT / "port" / "tools" / "decompile_list.csx")], env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       encoding="utf-8", errors="replace")
            for line in process.stdout:
                log.write(line)
                if line.startswith(("OK ", "FAILED ", "TIMEOUT ", "MISSING ")):
                    done += 1
                    bar.update(done)
            process.wait()
        bar.finish(False)
        result = process
    missing = [n for n in names if not (out / f"{n}.gml").is_file()]
    if result.returncode != 0 or missing:
        raise SystemExit(f"Decompiling failed ({len(missing)} scripts missing); see work/decompile.log.")
    print(f"Decompiled {len(names)} scripts into work/decompiled/.")


def textures(data):
    vita = WORK / "vita"
    vita.mkdir(parents=True, exist_ok=True)
    bundle = vita / "textures.bin"
    if not bundle.is_file():
        print("Packing texture pages into textures.bin (10-20 minutes)...", flush=True)
        subprocess.run([str(PYTHON), str(ROOT / "port" / "tools" / "build_texture_bundle.py"), str(data), str(bundle)],
                       check=True)
    pages = vita / "pagez"
    if not pages.is_dir() or len(list(pages.glob("*.hlz"))) < 150:
        print("Writing the multi-core page strips (pagez/)...", flush=True)
        subprocess.run([str(PYTHON), str(ROOT / "port" / "tools" / "make_page_strips.py"), str(data), str(pages)],
                       check=True)


def vita_icons(data):
    """The Vita button prompt icons (L/R badges, D-pad left/right), cut from your data.win's own prompt sprites."""
    icons = WORK / "vita-icons"
    if all((icons / f"{name}.png").is_file() for name in ("L", "R", "dleft", "dright")):
        return
    subprocess.run([str(PYTHON), str(ROOT / "port" / "tools" / "make_vita_icons.py"), str(data), str(icons)],
                   check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", type=Path, nargs="?", help="the folder with the 2016 data.win (found or downloaded "
                        "automatically when left out)")
    parser.add_argument("--qr", action="store_true", help="when downloading, sign in to Steam with a QR code")
    options = parser.parse_args()
    if not exe(DEPS / "utmt" / "UndertaleModCli").is_file() or not PYTHON.is_file():
        raise SystemExit("Run python3 scripts/bootstrap.py first.")
    if options.game is None:
        sys.path.insert(0, str(ROOT / "scripts"))
        from fetch_game import locate
        folder = locate(options.qr)
    else:
        folder = options.game.expanduser()
    data = check_game(folder)
    link_game(folder)
    decompile(data)
    textures(data)
    vita_icons(data)
    print("Game files prepared. Next: python3 scripts/build.py")


if __name__ == "__main__":
    main()
