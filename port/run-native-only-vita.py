"""Run the native-only build on the Vita test title in one room and collect its log.

Installs arm-hldc/hld-async-test.vpk as HLDVMTEST, writes ux0:data/hld-vm/native-only with the room name,
launches, waits, quits, saves the runtime log and removes the marker again.
"""
from argparse import ArgumentParser
from ftplib import FTP, error_perm
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import time

proof = Path(__file__).resolve().parent
root = proof.parent


def command(host, text):
    from socket import create_connection
    with create_connection((host, 1338), timeout=15) as sock:
        sock.sendall((text + "\n").encode())
        sock.shutdown(1)
        return sock.recv(4096).decode().strip()

parser = ArgumentParser(description=__doc__)
parser.add_argument("host")
parser.add_argument("room")
parser.add_argument("--seconds", type=float, default=45)
parser.add_argument("--title", default="HLDVMTEST", help="title ID of the app to install and launch")
parser.add_argument("--log", type=Path, default=proof / "native-only-vita.log")
parser.add_argument("--package", default="arm-hldc")
parser.add_argument("--capture", type=Path, help="save frame 720 as this PNG")
parser.add_argument("--test-room", help="launch the normal game into ROOM X Y (test-room.txt) instead of native-only mode")
parser.add_argument("--route", action="store_true", help="drive the player with the verifier's route for the room (test-route.txt)")
parser.add_argument("--marker", action="append", default=[], help="create ux0:data/hld-vm/NAME for the run, removed after")
parser.add_argument("--no-install", action="store_true", help="run the package already installed")
parser.add_argument("--route-legs", help="custom route legs 'first,end,x,y[,cross|square];...' (implies --route)")
parser.add_argument("--fight", help="'<frame> <object:count,...>': spawn enemies around the player, keep hp up (fight.txt, removed after)")
parser.add_argument("--room-tour", help="comma-separated rooms to visit in turn (room-tour.txt, removed after)")
parser.add_argument("--quiet-boot", type=int, default=0, help="seconds to wait before the first boot check (no log polling)")
parser.add_argument("--codex-features", action="store_true",
                    help="enable the Codex 02.78 feature markers its East Lab hardware runs used (removed after)")
options = parser.parse_args()
if options.route_legs:
    options.route = True

MARKER = "/ux0:/data/hld-vm/native-only"


def ftp():
    connection = FTP()
    connection.connect(options.host, 1337, timeout=30)
    connection.login()
    return connection


command(options.host, "quit " + options.title)
time.sleep(10)  # a launch while the previous instance is still shutting down gets killed during boot
if not options.no_install:
    subprocess.run([sys.executable, str(proof / "install-vita.py"), options.host,
                    str(proof / options.package / "hld-async-test.vpk"), "--title", options.title], check=True)
    time.sleep(15)  # let the system finish promoting the package before launching it
# The feature markers enabled in Codex's 02.78-stack hardware runs (compact-stack-rm_EL_MegaHugeLAB@1206,629.log).
CODEX_FEATURES = ("archive-audio-reads", "builtin-mask-overlap", "collision-target-views", "display-clear", "mine-entry",
                  "dormant-mine", "eventless-case", "floor-pattern", "idle-scenery", "instance-slot-reads", "line-chunks",
                  "mask-tiles", "mantle-sweep", "native-scenery-body", "ordered-line-candidates", "palette-tail",
                  "persistent-terrain-bounds", "phase-mine-trigger", "quiet-shadow-log", "reduced-vm-work", "scene-queries",
                  "native-movement-helpers", "scenery-cursor", "scenery-guard", "scenery-skip", "terrain-sweep",
                  "prefetch-ambience", "native-consolidated-runtime", "native-scenery-draw-lists",
                  "native-direct-instance-ids", "defer-loop-length", "native-enemy-shadow")
names = list(options.marker) + (list(CODEX_FEATURES) if options.codex_features else [])
MARKERS = ["/ux0:/data/hld-vm/" + name for name in dict.fromkeys(names)]
BUFFERED = "/ux0:/data/hld-vm/buffered-frame-reports"
if options.test_room:
    MARKER = "/ux0:/data/hld-vm/test-room.txt"
    marker_text = options.test_room
else:
    marker_text = options.room
with ftp() as connection:
    connection.storbinary("STOR " + MARKER, BytesIO(marker_text.encode() + b"\n"))
    # Synchronous report writes stall a frame per window; the verifier runs with buffered reports.
    try:
        connection.size(BUFFERED)
        created_buffered = False
    except error_perm:
        connection.storbinary("STOR " + BUFFERED, BytesIO(b""))
        created_buffered = True
    if options.route:
        # The verifier's moving route: (first frame, last frame, stick x, stick y) legs, 128 = centred.
        room = (options.test_room or options.room).split()[0]
        legs = ((540, 560, 255, 128), (572, 594, 128, 24), (610, 637, 23, 128), (650, 674, 128, 225), (692, 707, 209, 187))
        if options.route_legs:
            legs = tuple(tuple(part.split(",")) for part in options.route_legs.split(";"))
        route = "\n".join([f"{room} {len(legs)}", *(" ".join(map(str, leg)) for leg in legs)]) + "\n"
        connection.storbinary("STOR /ux0:/data/hld-vm/test-route.txt", BytesIO(route.encode()))
    # Only markers this run creates are removed afterwards; ones already present (e.g. set up for play) stay.
    present = set(connection.nlst("/ux0:/data/hld-vm/"))
    present = {name.split("/")[-1] for name in present}
    created_markers = [path for path in MARKERS if path.split("/")[-1] not in present]
    for path in created_markers:
        connection.storbinary("STOR " + path, BytesIO(b""))
    if options.room_tour:
        connection.storbinary("STOR /ux0:/data/hld-vm/room-tour.txt", BytesIO("\n".join(options.room_tour.split(",")).encode() + b"\n"))
        created_markers.append("/ux0:/data/hld-vm/room-tour.txt")
    if options.fight:
        connection.storbinary("STOR /ux0:/data/hld-vm/fight.txt", BytesIO(options.fight.encode() + b"\n"))
        created_markers.append("/ux0:/data/hld-vm/fight.txt")
    if options.capture:
        try:
            connection.delete("/ux0:/data/hld-vm/frame.rgba")  # a capture left by an earlier run
        except error_perm:
            pass
        connection.storbinary("STOR /ux0:/data/hld-vm/capture-exact-720", BytesIO(b""))
        created_markers.append("/ux0:/data/hld-vm/capture-exact-720")
with ftp() as connection:
    try:
        connection.delete("/ux0:/data/hld-vm/runtime.log")  # so the boot check never reads the previous run's log
    except error_perm:
        pass
command(options.host, "nosleep on")
command(options.host, "screen on")
time.sleep(5)  # let the display wake before the game's first vblank wait
print(command(options.host, "launch " + options.title), flush=True)
# A launch occasionally stalls during boot; wait for the room, relaunch once if it never arrives.
def room_loaded():
    data = BytesIO()
    try:
        with ftp() as connection:
            connection.retrbinary("RETR /ux0:/data/hld-vm/runtime.log", data.write)
    except Exception:
        return False
    return b"Runner: Room loaded: " + (options.test_room or options.room).split()[0].encode() in data.getvalue()
started = time.time()
if options.quiet_boot:
    time.sleep(options.quiet_boot)  # no FTP reads of runtime.log while the game boots
while time.time() - started < 100 and not room_loaded():
    time.sleep(10)
if not room_loaded():
    print("boot stalled; relaunching", flush=True)
    stalled = BytesIO()
    try:
        with ftp() as connection:
            connection.retrbinary("RETR /ux0:/data/hld-vm/runtime.log", stalled.write)
        options.log.with_suffix(".stall.log").write_bytes(stalled.getvalue())  # kept for diagnosing the stall
    except Exception:
        pass
    command(options.host, "quit " + options.title)
    time.sleep(10)
    with ftp() as connection:
        try:
            connection.delete("/ux0:/data/hld-vm/runtime.log")
        except error_perm:
            pass
        # The stalled boot may already have consumed the launch marker (test-room.txt is removed when read).
        connection.storbinary("STOR " + MARKER, BytesIO(marker_text.encode() + b"\n"))
    print(command(options.host, "launch " + options.title), flush=True)
    started = time.time()
    while time.time() - started < 100 and not room_loaded():
        time.sleep(10)
time.sleep(options.seconds)
command(options.host, "quit " + options.title)
time.sleep(2)
log = BytesIO()
with ftp() as connection:
    connection.retrbinary("RETR /ux0:/data/hld-vm/runtime.log", log.write)
    for path in [MARKER] + created_markers + ([BUFFERED] if created_buffered else []) + (["/ux0:/data/hld-vm/test-route.txt"] if options.route else []):
        try:
            connection.delete(path)
        except error_perm:
            pass
options.log.write_bytes(log.getvalue())
if options.capture:
    from capture_transport import decode_capture
    frame = BytesIO()
    with ftp() as connection:
        connection.retrbinary("RETR /ux0:/data/hld-vm/frame.rgba", frame.write)
    pixels = decode_capture(frame.getvalue())
    if pixels is None:
        raise SystemExit("Invalid Vita capture")
    subprocess.run(["magick", "-size", "960x544", "-depth", "8", "rgba:-", "-alpha", "off", str(options.capture)],
                   input=pixels, check=True)
    print(f"saved {options.capture}")
print(f"saved {options.log}")
