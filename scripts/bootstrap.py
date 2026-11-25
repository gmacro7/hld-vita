#!/usr/bin/env python3
"""Step 1: fetch and set up the pinned build tools in deps/ (nothing global is touched).

  - VitaSDK (softfp snapshot) for this host               -> deps/vitasdk
  - UndertaleModTool CLI 0.9.2.0 (to decompile your copy)  -> deps/utmt
  - the VitaSDK softfp library layer (vitasdk-softfp image) -> deps/softfp-target
  - vitaGL at a pinned commit, rebuilt for softfp stubs     -> deps/softfp-target/lib/libvitaGL.a
  - the newlib entropy shim                                  -> deps/vita-newlib-entropy.o
  - a Python virtual environment with numpy and Pillow      -> deps/venv

Every download is checked against the SHA-256 in deps/dependencies.json. Re-running skips finished steps.
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "deps"
CACHE = DEPS / "downloads"
MANIFEST = json.loads((DEPS / "dependencies.json").read_text())


def host():
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    name = f"{sys.platform.replace('linux', 'linux').replace('darwin', 'darwin')}-{machine}"
    if name not in {t["host"] for t in MANIFEST["tools"]}:
        raise SystemExit(f"Unsupported build host {name}: use macOS (Apple silicon or Intel) or Linux x86-64.")
    return name


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, path, expected):
    if path.is_file() and sha256(path) == expected:
        return path
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}", flush=True)
    partial = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(url) as source, partial.open("wb") as target:
        shutil.copyfileobj(source, target, 1 << 20)
    if sha256(partial) != expected:
        partial.unlink()
        raise SystemExit(f"Checksum mismatch for {url}")
    partial.rename(path)
    return path


def tool(kind, name):
    record = next(t for t in MANIFEST["tools"] if t["host"] == name and t["kind"] == kind)
    return download(record["url"], CACHE / record["file"], record["sha256"])


def install_sdk(name):
    target = DEPS / "vitasdk"
    if (target / "bin" / "arm-vita-eabi-gcc").is_file():
        return target
    archive = tool("sdk", name)
    staging = DEPS / "vitasdk.extract"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(staging, filter="data")
    inner = next(p for p in [staging / "vitasdk", staging] if (p / "bin" / "arm-vita-eabi-gcc").is_file())
    inner.rename(target)
    shutil.rmtree(staging, ignore_errors=True)
    return target


def install_utmt(name):
    target = DEPS / "utmt"
    if (target / "UndertaleModCli").is_file():
        return target
    archive = tool("utmt", name)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not (target / item.filename).resolve().is_relative_to(target.resolve()):
                raise SystemExit("Unsafe path in the UndertaleModTool archive")
        z.extractall(target)
        for item in z.infolist():
            mode = (item.external_attr >> 16) & 0o777
            if mode and not item.is_dir():
                (target / item.filename).chmod(mode)
    (target / "UndertaleModCli").chmod(0o755)
    return target


def install_softfp_layer():
    target = DEPS / "softfp-target"
    if (target / "lib").is_dir() and (target / "include").is_dir():
        return target
    layer = MANIFEST["softfp_layer"]
    repository, digest = layer["repository"], layer["digest"]
    archive = CACHE / "softfp-layer.tar.gz"
    if not archive.is_file() or sha256(archive) != digest.split(":")[1]:
        CACHE.mkdir(parents=True, exist_ok=True)
        token = json.load(urllib.request.urlopen(
            f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repository}:pull"))["token"]
        request = urllib.request.Request(f"https://registry-1.docker.io/v2/{repository}/blobs/{digest}",
                                         headers={"Authorization": "Bearer " + token})
        print(f"Downloading the VitaSDK softfp library layer ({layer['size'] / 1e6:.0f} MB)", flush=True)
        with urllib.request.urlopen(request) as source, archive.open("wb") as stream:
            shutil.copyfileobj(source, stream, 1 << 20)
        if sha256(archive) != digest.split(":")[1]:
            archive.unlink()
            raise SystemExit("Checksum mismatch for the softfp layer")
    prefix = "usr/local/vitasdk/arm-vita-eabi/"
    with tarfile.open(archive) as tar:
        for member in tar:
            if member.name.startswith(prefix) and member.isfile():
                relative = Path(member.name[len(prefix):])
                if ".." in relative.parts:
                    raise SystemExit("Unsafe path in the softfp layer")
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, destination.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
    return target


def install_vitagl(sdk):
    marker = DEPS / "softfp-target" / "lib" / ".vitagl-commit"
    commit = MANIFEST["vitaGL"]["commit"]
    if marker.is_file() and marker.read_text().strip() == commit:
        return
    source = DEPS / "vitaGL"
    if not (source / ".git").is_dir():
        subprocess.run(["git", "clone", "--quiet", MANIFEST["vitaGL"]["url"], str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "checkout", "--quiet", commit], check=True)
    subprocess.run([sys.executable, str(DEPS / "fix_vitagl_softfp_sdk.py"), "--source", str(source),
                    "--vitasdk", str(sdk)], check=True)
    marker.write_text(commit + "\n")


def build_entropy_shim(sdk):
    target = DEPS / "vita-newlib-entropy.o"
    if target.is_file():
        return
    subprocess.run([str(sdk / "bin" / "arm-vita-eabi-gcc"), "-c", "-O2",
                    str(ROOT / "port" / "runtime" / "vita-newlib-entropy.c"), "-o", str(target)], check=True)


def build_bc3_encoder():
    """stb_dxt (public domain) behind a one-function wrapper, loaded by port/tools/build_texture_bundle.py."""
    target = DEPS / ("bc3-encoder.dylib" if sys.platform == "darwin" else "bc3-encoder.so")
    if target.is_file():
        return
    compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if compiler is None:
        raise SystemExit("A host C compiler is required (macOS: xcode-select --install).")
    flags = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
    subprocess.run([compiler, "-O2", *flags, str(DEPS / "bc3" / "encode.c"), "-o", str(target)], check=True)


def install_venv():
    venv = DEPS / "venv"
    python = venv / "bin" / "python"
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", "-r", str(ROOT / "scripts" / "requirements.txt")],
                   check=True)


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("Python 3.12 or newer is required.")
    for program in ("git", "cmake", "ninja", "make", "pkg-config"):
        if shutil.which(program) is None:
            raise SystemExit(f"'{program}' is required (macOS: brew install cmake ninja pkg-config; Linux: your package manager).")
    name = host()
    sdk = install_sdk(name)
    install_utmt(name)
    install_softfp_layer()
    install_vitagl(sdk)
    build_entropy_shim(sdk)
    build_bc3_encoder()
    install_venv()
    print("Tools ready in deps/. Next: python3 scripts/prepare_game.py <your Hyper Light Drifter folder>")


if __name__ == "__main__":
    main()
