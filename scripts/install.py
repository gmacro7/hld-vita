#!/usr/bin/env python3
"""Step 4: copy your game files and the app to your Vita, over Wi-Fi (FTP) or a USB cable.

Usage: python3 scripts/install.py <vita-ip> [--port 1337]     Wi-Fi: start FTP on the Vita first (VitaShell: SELECT)
       python3 scripts/install.py --usb [card folder]          USB: VitaShell > START > USB device: Memory Card, then
                                                               SELECT; plug the Vita in (the card is found by itself)

This copies, to ux0:data/hld-vm/:
  - your game's files (data.win, music and sounds, level files, text files) from work/game
  - textures.bin and pagez/ from work/vita
  - the empty marker files that switch on the port's speedups
and puts the VPK at ux0:data/HyperLightDrifter.vpk. Install it with VitaShell (open ux0:data, press X on the VPK).

Files already on the Vita with the same size are skipped, so re-running after a rebuild only sends what changed.
Your saves (*.sav, HLD_BACKUPS) are never touched.
"""
import argparse
import os
import shutil
import string
import sys
from ftplib import FTP, error_perm
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = "/ux0:/data/hld-vm"
GAME_FILES = {".win", ".ogg", ".txt", ".lvl", ".png", ".webm", ".ini", ".dat"}
# The port's speedups, each switched on by an empty file of this name in ux0:data/hld-vm (the configuration tested
# on hardware). Delete one on the Vita to fall back to the game's own code for that part.
FEATURES = ("archive-audio-reads", "builtin-mask-overlap", "collision-target-views", "display-clear", "mine-entry",
            "dormant-mine", "eventless-case", "floor-pattern", "idle-scenery", "instance-slot-reads", "line-chunks",
            "mask-tiles", "mantle-sweep", "native-scenery-body", "ordered-line-candidates", "palette-tail",
            "persistent-terrain-bounds", "phase-mine-trigger", "quiet-shadow-log", "reduced-vm-work", "scene-queries",
            "native-movement-helpers", "scenery-cursor", "scenery-guard", "scenery-skip", "terrain-sweep",
            "prefetch-ambience", "native-consolidated-runtime", "native-scenery-draw-lists",
            "native-direct-instance-ids", "defer-loop-length", "native-enemy-shadow", "native-trails")


def mkdirs(ftp, made, path):
    parts = path.strip("/").split("/")
    for i in range(1, len(parts) + 1):
        directory = "/" + "/".join(parts[:i])
        if directory in made:
            continue
        try:
            ftp.mkd(directory)
        except error_perm:
            pass
        made.add(directory)


def upload(ftp, source, destination):
    size = source.stat().st_size
    try:
        if ftp.size(destination) == size:
            return False
    except error_perm:
        pass
    partial = destination + ".part"
    with source.open("rb") as stream:
        ftp.storbinary("STOR " + partial, stream, blocksize=1 << 18)
    if ftp.size(partial) != size:
        raise SystemExit(f"Transfer size mismatch for {destination}")
    try:
        ftp.delete(destination)
    except error_perm:
        pass
    ftp.rename(partial, destination)
    return True


def game_files():
    """(source, path under ux0:data/hld-vm) for everything the game needs, plus the empty speedup markers."""
    game = ROOT / "work" / "game"
    vita = ROOT / "work" / "vita"
    if not (game / "data.win").is_file() or not (vita / "textures.bin").is_file():
        raise SystemExit("Run scripts/prepare_game.py first.")
    files = [(p, p.relative_to(game).as_posix()) for p in sorted(game.rglob("*"))
             if p.is_file() and p.suffix.lower() in GAME_FILES]
    files += [(vita / "textures.bin", "textures.bin")]
    files += [(p, f"pagez/{p.name}") for p in sorted((vita / "pagez").glob("*.hlz"))]
    empty = ROOT / "build" / ".empty"
    empty.parent.mkdir(exist_ok=True)
    empty.write_bytes(b"")
    files += [(empty, name) for name in FEATURES]
    return files


def newest_package():
    packages = sorted((ROOT / "build").glob("HyperLightDrifter-*.vpk"), key=lambda p: p.stat().st_mtime)
    if not packages:
        raise SystemExit("Build the app first.")
    return packages[-1]


def find_memory_card():
    """The Vita's memory card mounted over USB (VitaShell USB mode): a drive or volume with app/ and data/ folders."""
    if sys.platform == "win32":
        roots = [Path(f"{letter}:\\") for letter in string.ascii_uppercase[3:]]
    elif sys.platform == "darwin":
        roots = list(Path("/Volumes").iterdir()) if Path("/Volumes").is_dir() else []
    else:
        roots = [p for base in ("/media", "/run/media", "/mnt") if Path(base).is_dir()
                 for p in Path(base).glob("*/*")] + [p for base in ("/media", "/mnt") if Path(base).is_dir()
                                                     for p in Path(base).glob("*")]
    found = []
    for root in roots:
        try:
            if (root / "app").is_dir() and (root / "data").is_dir():
                found.append(root)
        except OSError:
            continue
    return found


def install_usb(card):
    if card is None:
        cards = find_memory_card()
        if not cards:
            raise SystemExit("No Vita memory card found. On the Vita: VitaShell > START > USB device: Memory Card, "
                             "then press SELECT and connect the cable. Or give the card's folder: --usb E:\\")
        if len(cards) > 1:
            raise SystemExit("More than one possible card: " + ", ".join(map(str, cards)) + ". Pick one: --usb <folder>")
        card = cards[0]
    card = Path(card)
    if not (card / "data").is_dir():
        raise SystemExit(f"{card} does not look like the Vita's memory card (no data folder).")
    print(f"Copying to the Vita's memory card at {card}", flush=True)
    files = game_files()
    package = newest_package()
    target = card / "data" / "hld-vm"
    copied = 0
    for index, (source, relative) in enumerate(files, 1):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size == source.stat().st_size:
            continue
        if source.stat().st_size > 16_000_000:
            print(f"Copying {relative} ({source.stat().st_size / 1e6:.0f} MB)...", flush=True)
        partial = destination.with_name(destination.name + ".part")
        shutil.copyfile(source, partial)
        os.replace(partial, destination)
        copied += 1
        if index % 250 == 0 or index == len(files):
            print(f"{index}/{len(files)} game files checked, {copied} copied", flush=True)
    print(f"Copying {package.name}...", flush=True)
    shutil.copyfile(package, card / "data" / "HyperLightDrifter.vpk")
    print("Done. Eject the card on your computer first (Windows: Safely Remove; Mac: Eject), press O on the Vita to "
          "leave USB mode, then VitaShell > ux0:data > HyperLightDrifter.vpk > X to install, and launch it.")


def install_ftp(host, port):
    files = game_files()
    package = newest_package()
    made = set()
    with FTP() as ftp:
        ftp.connect(host, port, timeout=30)
        ftp.login()
        ftp.voidcmd("TYPE I")
        mkdirs(ftp, made, DESTINATION)
        sent = 0
        for index, (source, relative) in enumerate(files, 1):
            destination = f"{DESTINATION}/{relative}"
            mkdirs(ftp, made, destination.rsplit("/", 1)[0])
            if source.stat().st_size > 16_000_000:
                print(f"Uploading {relative} ({source.stat().st_size / 1e6:.0f} MB)...", flush=True)
            sent += upload(ftp, source, destination)
            if index % 250 == 0 or index == len(files):
                print(f"{index}/{len(files)} game files checked, {sent} sent", flush=True)
        print(f"Uploading {package.name}...", flush=True)
        upload(ftp, package, "/ux0:/data/HyperLightDrifter.vpk")
    print("Done. On the Vita: VitaShell > ux0:data > HyperLightDrifter.vpk > X to install, then launch it.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", nargs="?", help="your Vita's IP address (shown by its FTP server)")
    parser.add_argument("--port", type=int, default=1337)
    parser.add_argument("--usb", nargs="?", const="", metavar="CARD",
                        help="copy onto the memory card over USB instead (found by itself, or give its folder)")
    options = parser.parse_args()
    if options.usb is not None:
        install_usb(options.usb or None)
    elif options.host:
        install_ftp(options.host, options.port)
    else:
        parser.error("give your Vita's IP address (Wi-Fi) or --usb (cable)")


if __name__ == "__main__":
    main()
