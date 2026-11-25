#!/usr/bin/env python3
"""Step 4: copy your game files and the app to your Vita over FTP.

Usage: python3 scripts/install.py <vita-ip> [--port 1337]

Start an FTP server on the Vita first (VitaShell: press SELECT; or VitaCompanion). This uploads, to
ux0:data/hld-vm/:
  - your game's files (data.win, music and sounds, level files, text files) from work/game
  - textures.bin and pagez/ from work/vita
  - the empty marker files that switch on the port's speedups
and puts the VPK at ux0:data/HyperLightDrifter.vpk. Install it with VitaShell (open ux0:data, press X on the VPK).

Files already on the Vita with the same size are skipped, so re-running after a rebuild only sends what changed.
Your saves (*.sav, HLD_BACKUPS) are never touched.
"""
import argparse
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
            "native-direct-instance-ids", "defer-loop-length", "native-enemy-shadow")


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="your Vita's IP address (shown by its FTP server)")
    parser.add_argument("--port", type=int, default=1337)
    options = parser.parse_args()
    game = ROOT / "work" / "game"
    vita = ROOT / "work" / "vita"
    packages = sorted((ROOT / "build").glob("HyperLightDrifter-*.vpk"))
    if not (game / "data.win").is_file() or not (vita / "textures.bin").is_file():
        raise SystemExit("Run scripts/prepare_game.py first.")
    if not packages:
        raise SystemExit("Run scripts/build.py first.")
    files = [(p, p.relative_to(game).as_posix()) for p in sorted(game.rglob("*"))
             if p.is_file() and p.suffix.lower() in GAME_FILES]
    files += [(vita / "textures.bin", "textures.bin")]
    files += [(p, f"pagez/{p.name}") for p in sorted((vita / "pagez").glob("*.hlz"))]
    made = set()
    with FTP() as ftp:
        ftp.connect(options.host, options.port, timeout=30)
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
        empty = ROOT / "build" / ".empty"
        empty.parent.mkdir(exist_ok=True)
        empty.write_bytes(b"")
        for name in FEATURES:
            upload(ftp, empty, f"{DESTINATION}/{name}")
        print(f"Uploading {packages[-1].name}...", flush=True)
        upload(ftp, packages[-1], "/ux0:/data/HyperLightDrifter.vpk")
    print("Done. On the Vita: VitaShell > ux0:data > HyperLightDrifter.vpk > X to install, then launch it.")


if __name__ == "__main__":
    main()
