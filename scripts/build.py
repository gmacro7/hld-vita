#!/usr/bin/env python3
"""Step 3: build the Vita app (build/HyperLightDrifter-<TITLE_ID>.vpk).

Usage: python3 scripts/build.py [--clean]

Configures the Butterscotch engine (engine/) for the Vita, builds it, then runs the port's build
(port/build-hldc-vita.py), which applies the port's engine changes, translates your decompiled game scripts
(work/decompiled) to C and links the app. The VPK contains code made from your copy of the game: do not share it.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "deps"
SDK = DEPS / "vitasdk"
TARGET = DEPS / "softfp-target"
# The engine build the port's build reads its compile commands from (port/build-hldc-vita.py: build/engine-vita,
# with the sprite-decode cache variant these flags select).
ENGINE_BUILD = ROOT / "build" / "engine-vita-sprite-cache"
# The released configuration. Each flag enables a group of the port's engine changes; see docs/BUILDING.md.
PORT_FLAGS = ["--runtime-refinements", "--consolidated-runtime", "--integrated-optimizations", "--verified-runtime",
              "--persistent-terrain-bounds", "--stream-slot-obstacles",
              "--vm-feedback", "vm-feedback-profiles/20261121-162657/merged-3"]
# --vm-feedback selects the instance-slot VM build; HLDC_NO_COMPILER_FEEDBACK (below) compiles it without the GCC
# profile data recorded on hardware, which GCC ties to the absolute path it was recorded at.


def configure_engine():
    if (ENGINE_BUILD / "build.ninja").is_file():
        return
    ENGINE_BUILD.mkdir(parents=True, exist_ok=True)
    stdlibs = " ".join([str(DEPS / "vita-newlib-entropy.o"), "-lstdc++", "-lSceKernelDmacMgr_stub",
                        "-lSceShaccCg_stub", "-lSceAudio_stub", "-lSceLibKernel_stub"])
    subprocess.run([
        "cmake", "-G", "Ninja", "-S", str(ROOT / "engine"), "-B", str(ENGINE_BUILD),
        f"-DCMAKE_TOOLCHAIN_FILE={SDK / 'share' / 'vita.toolchain.cmake'}", f"-DVITASDK={SDK}",
        f"-DPKG_CONFIG_EXECUTABLE={DEPS / 'vita-pkg-config'}",
        "-DPLATFORM=vita", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON",
        f"-DCMAKE_C_FLAGS=-I{TARGET / 'include'} -mtune=cortex-a9 -DHLD_COMPACT_RVALUES",
        f"-DCMAKE_C_STANDARD_LIBRARIES={stdlibs}",
        f"-DCMAKE_EXE_LINKER_FLAGS=-L{SDK / 'arm-vita-eabi' / 'lib'} -L{TARGET / 'lib'} "
        "-Wl,-z,nocopyreloc,--defsym=__sce_headroom=0x10000",
        "-DENABLE_SPRITE_DECODE_CACHE=ON", "-DENABLE_VITA_ASYNC_PRESENT=OFF",
        "-DENABLE_WAD14=OFF", "-DENABLE_WAD16=ON", "-DENABLE_WAD17=OFF",
        "-DVITA_MKSFOEX_FLAGS=-d ATTRIBUTE2=12",
    ], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clean", action="store_true", help="reconfigure and rebuild the engine from scratch")
    options = parser.parse_args()
    if not (SDK / "bin" / "arm-vita-eabi-gcc").is_file():
        raise SystemExit("Run python3 scripts/bootstrap.py first.")
    if not (ROOT / "work" / "decompiled").is_dir():
        raise SystemExit("Run python3 scripts/prepare_game.py <your game folder> first.")
    if not (ROOT / "assets" / "livearea" / "icon0.png").is_file():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_livearea.py")], check=True)
    if options.clean:
        shutil.rmtree(ENGINE_BUILD, ignore_errors=True)
    configure_engine()
    environment = dict(os.environ, PATH=f"{SDK / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}", VITASDK=str(SDK))
    subprocess.run(["ninja", "-C", str(ENGINE_BUILD)], check=True, env=environment)
    environment.update(HLDC_DIR="build-out", HLDC_TRANSLATE="hot-entries-v5.txt", HLDC_NATIVE_SCRIPTS="1",
                       HLDC_NO_COMPILER_FEEDBACK="1",
                       HLDC_VITA_ROWS="0", HLDC_BOOT_PREFETCH="0")
    subprocess.run([str(DEPS / "venv" / "bin" / "python"), "build-hldc-vita.py", *PORT_FLAGS],
                   cwd=ROOT / "port", check=True, env=environment)
    vpk = sorted((ROOT / "build").glob("HyperLightDrifter-*.vpk"))
    print(f"Built {vpk[-1] if vpk else 'the VPK'}. Next: python3 scripts/install.py <vita-ip>  (see README.md)")


if __name__ == "__main__":
    main()
