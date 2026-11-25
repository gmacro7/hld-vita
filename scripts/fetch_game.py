#!/usr/bin/env python3
"""Find, or download from Steam, the August 2016 build of Hyper Light Drifter that the port needs.

Usage: python3 scripts/fetch_game.py [--qr]

The port runs the game's GameMaker bytecode, and only the August 2016 build still ships it (later builds, the current
Steam and GOG releases included, compile the game to native code). Steam still serves that build to everyone who owns
the game, as depot 257851 at manifest 2557763276340557312.

1. Look for it where Steam's own console command (download_depot 257850 257851 2557763276340557312) puts it, and in
   work/depot from an earlier run.
2. Otherwise download it into work/depot (about 1 GB) with DepotDownloader (github.com/SteamRE/DepotDownloader,
   pinned and checksummed in deps/dependencies.json), signed in with YOUR Steam account. Steam checks that the account
   owns Hyper Light Drifter. DepotDownloader asks for the password and Steam Guard code itself; this script never sees
   or stores them. With --qr you sign in by scanning a QR code with the Steam mobile app instead.

Prints the folder that holds the verified data.win.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "deps" / "dependencies.json").read_text())
GAME = MANIFEST["game"]
DEPOT_DIR = ROOT / "work" / "depot"


def steam_content_folders():
    home = Path.home()
    roots = [
        home / "Library/Application Support/Steam/Steam.AppBundle/Steam/Contents/MacOS",  # macOS (console download)
        home / "Library/Application Support/Steam",
        Path("C:/Program Files (x86)/Steam"), Path("C:/Program Files/Steam"),
    ]
    if os.environ.get("STEAM_DIR"):
        roots.insert(0, Path(os.environ["STEAM_DIR"]))
    depot = Path("steamapps/content") / f"app_{GAME['app']}" / f"depot_{GAME['depot']}"
    return [DEPOT_DIR] + [root / depot for root in roots]


def verified(folder):
    data = folder / "data.win"
    if not data.is_file() or data.stat().st_size != GAME["data_win_size"]:
        return False
    with data.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == GAME["data_win_sha256"]


def find_game():
    for folder in steam_content_folders():
        if verified(folder):
            return folder
    return None


def depotdownloader():
    sys.path.insert(0, str(ROOT / "scripts"))
    import bootstrap
    name = bootstrap.host()
    target = ROOT / "deps" / "depotdownloader"
    program = bootstrap.exe(target / "DepotDownloader")
    if program.is_file():
        return program
    record = next(t for t in MANIFEST["tools"] if t["host"] == name and t["kind"] == "depotdownloader")
    archive = bootstrap.download(record["url"], bootstrap.CACHE / record["file"], record["sha256"])
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(target)
    if not bootstrap.WINDOWS:
        program.chmod(0o755)
    return program


def fetch(qr):
    program = depotdownloader()
    command = [str(program), "-app", str(GAME["app"]), "-depot", str(GAME["depot"]), "-manifest", GAME["manifest"],
               "-dir", str(DEPOT_DIR)]
    if qr:
        command.append("-qr")
    else:
        username = input("Your Steam username (the account that owns Hyper Light Drifter): ").strip()
        if not username:
            raise SystemExit("No username given.")
        command += ["-username", username]
    print("Downloading the August 2016 build from Steam into work/depot (about 1 GB)...", flush=True)
    # Interactive: DepotDownloader prompts on this terminal for the password and Steam Guard code.
    result = subprocess.run(command)
    if result.returncode != 0:
        raise SystemExit("The download did not finish. Check that this Steam account owns Hyper Light Drifter.")
    if not verified(DEPOT_DIR):
        raise SystemExit("Downloaded, but data.win is not the expected 2016 build.")
    return DEPOT_DIR


def locate(qr=False, download=True):
    folder = find_game()
    if folder is not None:
        print(f"Found the 2016 build in {folder}", flush=True)
        return folder
    if not download:
        raise SystemExit("The 2016 build was not found; run python3 scripts/fetch_game.py to download it.")
    print("The 2016 build of Hyper Light Drifter was not found on this computer.")
    return fetch(qr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qr", action="store_true", help="sign in with a QR code from the Steam mobile app")
    options = parser.parse_args()
    print(locate(options.qr))


if __name__ == "__main__":
    main()
