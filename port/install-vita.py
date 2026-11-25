"""Install the isolated HLD 2016 runtime and verify the Vita executable."""
from argparse import ArgumentParser
import re
from contextlib import closing
from ftplib import FTP, error_perm, error_temp
from hashlib import sha256
from io import BytesIO
from os import getpid
from pathlib import Path
from socket import create_connection
from subprocess import run
from sys import platform
from time import sleep
from zipfile import ZipFile

TITLE = "HLDVM2016"


def allow_transfer_scheduling():
    if platform == "darwin":
        # FTP sockets inherit Darwin background throttling on open.
        # Clear it for this transfer process while keeping its nice priority.
        run(["/usr/sbin/taskpolicy", "-B", "-p", str(getpid())], check=False)


def command(host, text):
    with create_connection((host, 1338), timeout=15) as sock:
        sock.sendall((text + "\n").encode())
        sock.shutdown(1)
        return sock.recv(4096).decode().strip()


def download(ftp, path):
    stream = BytesIO()
    try:
        ftp.retrbinary("RETR " + path, stream.write)
    except error_perm as error:
        if str(error).startswith("550"):
            return None
        raise
    return stream.getvalue()


def readback(host, path):
    """Reconnect and resume a byte-for-byte readback after a transfer interruption."""
    stream = BytesIO()
    max_attempts, stalled_attempts = 12, 0
    for attempt in range(max_attempts):
        offset = stream.tell()
        try:
            with closing(FTP()) as ftp:
                ftp.connect(host, 1337, timeout=45)
                ftp.login()
                ftp.retrbinary("RETR " + path, stream.write, rest=offset or None)
            return stream.getvalue()
        except error_perm as error:
            reply = str(error)[:3]
            if reply == "550":
                return None
            if not offset or reply not in {"500", "501", "502", "504"} or attempt == max_attempts - 1:
                raise
            stream.seek(0)
            stream.truncate()
            print(f"FTP restart unsupported for {path}. Retrying the full readback.", flush=True)
        except (OSError, EOFError, error_temp) as error:
            stalled_attempts = 0 if stream.tell() > offset else stalled_attempts + 1
            if stalled_attempts >= 4 or attempt == max_attempts - 1:
                raise
            print(f"Readback interrupted at {stream.tell()} bytes for {path}. Reconnecting ({error}).", flush=True)
        sleep(1)


def mkdir(ftp, path):
    try:
        ftp.mkd(path)
    except error_perm:
        ftp.cwd(path)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("package", type=Path)
    parser.add_argument("--title", default="HLDVM2016", type=lambda t: t if re.fullmatch(r"[A-Z0-9]{9}", t) else parser.error("--title must be 9 capital letters or digits"))
    args = parser.parse_args()
    allow_transfer_scheduling()
    with ZipFile(args.package) as package:
        contents = {name: package.read(name) for name in ("eboot.bin", "sce_sys/param.sfo")}
        # Everything else the package carries too (bubble icon, LiveArea art, bundled data such as vita-icons/).
        extras = {info.filename: package.read(info.filename) for info in package.infolist()
                  if not info.is_dir() and info.filename not in contents}
    if args.title.encode() not in contents["sce_sys/param.sfo"]:
        raise SystemExit("Unexpected package title ID")
    digest = sha256(contents["eboot.bin"]).hexdigest()
    package_digest = sha256(contents["eboot.bin"] + contents["sce_sys/param.sfo"] + b"".join(
        name.encode() + data for name, data in sorted(extras.items()))).hexdigest()
    staged = f"/ux0:/data/hld-vm-install-{package_digest[:10]}"
    installed_app = f"/ux0:/app/{args.title}"
    installed = installed_app + "/eboot.bin"
    metadata_matches = readback(args.host, installed_app + "/sce_sys/param.sfo") == contents["sce_sys/param.sfo"]
    extras_match = all(readback(args.host, installed_app + "/" + name) == data for name, data in extras.items())
    if not metadata_matches or not extras_match or readback(args.host, installed) != contents["eboot.bin"]:
        with closing(FTP()) as ftp:
            ftp.connect(args.host, 1337, timeout=45)
            ftp.login()
            mkdir(ftp, staged)
            mkdir(ftp, staged + "/sce_sys")
            made = set()
            for name in extras:
                parts = name.split("/")[:-1]
                for depth in range(1, len(parts) + 1):
                    folder = "/".join(parts[:depth])
                    if folder not in made and folder != "sce_sys":
                        mkdir(ftp, staged + "/" + folder)
                        made.add(folder)
            for name, data in {**contents, **extras}.items():
                target = staged + "/" + name
                ftp.storbinary("STOR " + target, BytesIO(data), 65536)
        for name, data in {**contents, **extras}.items():
            if readback(args.host, staged + "/" + name) != data:
                raise RuntimeError(f"Failed readback of {name}")
        promotion = command(args.host, f"promote {staged.lstrip('/')}")
        print(promotion, flush=True)
        attempts = 1 if promotion.startswith("Error:") else 20
        for _ in range(attempts):
            if readback(args.host, installed) == contents["eboot.bin"]:
                break
            sleep(0.5)
        else:
            raise RuntimeError("Installed executable failed readback")
    print(f"{args.title} installed, executable SHA-256 {digest}", flush=True)


if __name__ == "__main__":
    main()
