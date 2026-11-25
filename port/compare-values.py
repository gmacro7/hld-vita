"""Compare game state and rendered frames around the ownership-check change."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

BACKGROUND = ["/usr/sbin/taskpolicy", "-b", "-c", "background", "/usr/bin/nice", "-n", "10"]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--baseline", default="value-baseline")
parser.add_argument("--candidate", default="value-candidate")
parser.add_argument("--directory", default="value-check")
parser.add_argument("--room")
parser.add_argument("--position", type=int, nargs=2, metavar=("X", "Y"))
parser.add_argument("--frame", type=int, default=660)
parser.add_argument("--window-size")
parser.add_argument("--surface", type=int, help="compare this internal render target instead of the window")
options = parser.parse_args()
if options.position is not None and options.room is None:
    parser.error("--position requires --room")
proof = Path(__file__).resolve().parent
game = Path(__import__("os").environ.get("HLD_DATA_WIN") or Path(__file__).resolve().parent.parent / "work" / "game" / "data.win")  # your own copy (scripts/prepare_game.py), or $HLD_DATA_WIN

for build in {options.baseline, options.candidate}:
    binary = proof / build
    receipt = binary.with_suffix(".offscreen-sha256")
    if not binary.is_file() or not receipt.is_file():
        parser.error(f"Rebuild {build} with build-sort-mac.py before running this comparison.")
    with binary.open("rb") as executable:
        checksum = hashlib.file_digest(executable, "sha256").hexdigest()
    if checksum != receipt.read_text().strip():
        parser.error(f"{build} changed since its offscreen build. Rebuild it with build-sort-mac.py.")


def run(build, room, position, frame):
    path = proof / options.directory / room / build
    path.mkdir(parents=True, exist_ok=True)
    (path / "test-room.txt").write_text(f"{room} {position}\n")
    window_size = ["--window-size", options.window_size] if options.window_size else []
    if options.surface is None:
        capture = ["--screenshot", str(path / "frame.png"), "--screenshot-at-frame", str(frame)]
    else:
        capture = ["--screenshot-surfaces", str(path / "surface-%d-%d.png"),
                   "--screenshot-surfaces-at-frame", str(frame)]
    with (path / "run.log").open("w") as log:
        subprocess.run([*BACKGROUND, str(proof / build), str(game), "--renderer", "modern-gl",
                        "--lazy-textures", "--load-type", "load-per-chunk", "--seed", "541",
                        "--speed", "30", *window_size, "--save-folder", str(path), "--exit-at-frame", str(frame + 2),
                        *capture,
                        "--dump-frame-json", str(frame), "--dump-frame-json-file", str(path / "state.json")],
                       stdout=log, stderr=subprocess.STDOUT,
                       env=dict(os.environ, ALSOFT_DRIVERS="null", SDL_AUDIO_DRIVER="dummy", SDL_AUDIODRIVER="dummy"),
                       timeout=80, check=True)
    return path


def state(path):
    raw = (path / "state.json").read_text()
    value = json.loads(re.sub(r"(:|,|\[)(-?inf|nan)(?=[,}\]])", r"\1null", raw))
    for instance in value["instances"]:
        fields = instance.get("selfVariables", {})
        if isinstance(fields, dict):
            fields.pop("timeSinceLastSave", None)
            fields.pop("dateTime", None)
    return value


rooms = (
    ("rm_C_Central", "344 320"),
    ("rm_C_Dregs_E", ""),
    ("rm_IN_03_Tut_Combat", "1160 1088"),
    ("rm_IN_HorizonCliff", "384 208"),
)
if options.room and options.room not in {room for room, _ in rooms}:
    rooms = ((options.room, ""),)

for room, position in rooms:
    if options.room is not None and room != options.room:
        continue
    if options.position is not None:
        position = " ".join(map(str, options.position))
    frame = options.frame
    baseline, candidate = [run(build, room, position, frame) for build in (options.baseline, options.candidate)]
    old, new = state(baseline), state(candidate)
    differences = [field for field in sorted(old.keys() | new.keys()) if old.get(field) != new.get(field)]
    image = "frame.png" if options.surface is None else f"surface-{frame}-{options.surface}.png"
    pixels = subprocess.run([*BACKGROUND, "magick", "compare", "-limit", "thread", "1",
                             "-metric", "AE", baseline / image, candidate / image, "null:"],
                            capture_output=True, text=True)
    print(room, frame, "differing state fields", differences, "differing pixels", pixels.stderr.strip(), flush=True)
    assert pixels.returncode == 0 and not differences
