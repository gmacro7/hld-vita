#!/usr/bin/env python3
"""Rebuild VitaGL for VitaSDK softfp SceGxm stubs without a duplicate viewport ABI shim."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DEFAULT = ROOT / "deps/vitaGL"
DESTINATION_DEFAULT = ROOT / "deps/softfp-target/lib/libvitaGL.a"
VITASDK_DEFAULT = ROOT / "deps/vitasdk"


def patch_makefile_for_vitasdk_softfp(path: Path) -> None:
    """Apply the adapter guard in an isolated build copy.

    Keeping this transformation here lets the helper work from an unmodified
    VitaGL checkout as well as from this repository's patched dependency.
    """
    text = path.read_text()
    text = text.replace("CFLAGS  = -g -Wl,-q", "CFLAGS ?= -g -Wl,-q")
    legacy = """ifeq ($(SOFTFP_ABI),1)
CFLAGS += -mfloat-abi=softfp -DHAVE_SOFTFP_ABI
endif"""
    guarded = """ifeq ($(SOFTFP_ABI),1)
CFLAGS += -mfloat-abi=softfp
# VitaSDK's SceGxm stub already adapts softfp arguments to the import ABI.
# Do not add VitaGL's legacy viewport adapter when linking against those stubs,
# or sceGxmSetViewport converts the six float arguments twice.
ifneq ($(VITASDK_SOFTFP_STUBS),1)
CFLAGS += -DHAVE_SOFTFP_ABI
endif
endif"""
    if legacy in text:
        text = text.replace(legacy, guarded)
    if guarded not in text or "CFLAGS ?=" not in text:
        raise SystemExit("could not apply or verify the VitaSDK softfp Makefile guard")
    path.write_text(text)


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DEFAULT)
    parser.add_argument("--destination", type=Path, default=DESTINATION_DEFAULT)
    parser.add_argument("--vitasdk", type=Path, default=VITASDK_DEFAULT)
    parser.add_argument(
        "--draw-speedhack",
        choices=("0", "1", "2"),
        default="0",
        help="VitaGL draw path: 0 copies client arrays, 1 uses them directly, 2 bypasses copies only for large draws",
    )
    parser.add_argument("--textures-speedhack", action="store_true")
    parser.add_argument("--samplers-speedhack", action="store_true")
    parser.add_argument("--check", action="store_true", help="Validate the installed archive without rebuilding")
    args = parser.parse_args()

    source = args.source.resolve()
    destination = args.destination.resolve()
    vitasdk = args.vitasdk.resolve()
    toolchain = vitasdk / "bin"
    include_dir = ROOT / "deps/softfp-target/include"
    ar = toolchain / "arm-vita-eabi-ar"
    nm = toolchain / "arm-vita-eabi-nm"
    gxm_stub = vitasdk / "arm-vita-eabi/lib/libSceGxm_stub.a"

    for path in (source / "Makefile", toolchain / "arm-vita-eabi-gcc", ar, nm, include_dir, gxm_stub):
        if not path.exists():
            raise SystemExit(f"required path is missing: {path}")

    stub_symbols = subprocess.check_output([str(nm), "-A", str(gxm_stub)], text=True)
    if "__vita_softfp_target_sceGxmSetViewport" not in stub_symbols:
        raise SystemExit("selected SceGxm stub does not provide the required softfp viewport target")

    if args.check:
        if not destination.exists():
            raise SystemExit("VitaGL archive is missing; run this helper without --check")
        symbols = subprocess.check_output([str(nm), "-A", str(destination)], text=True)
        if "sceGxmSetViewport_sfp" in symbols or " U sceGxmSetViewport" not in symbols:
            raise SystemExit("VitaGL viewport ABI requires rebuilding; run this helper without --check")
        print("VitaGL uses the VitaSDK viewport adapter once")
        return

    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "--short", "HEAD"], text=True).strip()
    cflags = " ".join((
        "-std=gnu11",
        "-Wno-error=implicit-function-declaration",
        "-Wno-error=int-conversion",
        f"-I{include_dir}",
        "-g", "-Wl,-q", "-O3", "-ffast-math", "-mtune=cortex-a9", "-mfpu=neon",
        "-Wno-incompatible-pointer-types", "-Wno-stringop-overflow", "-mfp16-format=ieee",
        f"-DVGL_GIT_HASH='\"{commit}\"'", "-Isource",
    ))

    with tempfile.TemporaryDirectory(prefix="hld-vitagl-softfp-") as tmp:
        build = Path(tmp) / "vitaGL"
        shutil.copytree(source, build, ignore=shutil.ignore_patterns(".git", "*.o", "libvitaGL.a"))
        patch_makefile_for_vitasdk_softfp(build / "Makefile")
        environment = dict(os.environ)
        environment["PATH"] = f"{toolchain}:{environment.get('PATH', '')}"
        environment["VITASDK"] = str(vitasdk)
        environment["CFLAGS"] = cflags
        make_args = [
            "make", "-j4", "SOFTFP_ABI=1", "VITASDK_SOFTFP_STUBS=1",
            "NO_DEBUG=1", "NO_SPLASHSCREEN=1", "SHARED_RENDERTARGETS=1",
            "DEPTH_STENCIL_HACK=1", "ENABLE_LEGACY_PIPELINE=1",
            f"DRAW_SPEEDHACK={args.draw_speedhack}",
            f"TEXTURES_SPEEDHACK={int(args.textures_speedhack)}",
            f"SAMPLERS_SPEEDHACK={int(args.samplers_speedhack)}",
        ]
        run(make_args, cwd=build, env=environment)
        archive = build / "libvitaGL.a"
        if not archive.exists():
            raise SystemExit("VitaGL build did not produce libvitaGL.a")

        members = subprocess.check_output([str(ar), "t", str(archive)], text=True)
        if "vgl.o" not in members.splitlines():
            raise SystemExit("rebuilt archive has no vgl.o")
        symbols = subprocess.check_output([str(nm), "-A", str(archive)], text=True)
        if "sceGxmSetViewport_sfp" in symbols:
            raise SystemExit("rebuilt archive still exports the duplicate viewport adapter")

        destination.parent.mkdir(parents=True, exist_ok=True)
        baseline = destination.with_name(destination.stem + ".pre-softfp-sdk.a")
        if not baseline.exists() and destination.exists():
            shutil.copy2(destination, baseline)
        shutil.copy2(archive, destination)

    print(f"installed {destination}")


if __name__ == "__main__":
    main()
