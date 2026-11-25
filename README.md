# Hyper Light Drifter for PS Vita

An unofficial native PS Vita port of Hyper Light Drifter. It is not an emulator. The game's own GameMaker
bytecode runs on a Vita build of the [Butterscotch](https://github.com/ButterscotchRunner/Butterscotch) runner.
The game's hottest scripts are translated to C at build time. A set of Vita-specific engine changes keeps the game
at its 30 FPS target in most places.

> **You need your own copy of the game.** This repository contains no game files: no `data.win`, art, music,
> levels, text or decompiled scripts. The build reads your copy and makes everything game-specific on your computer.
> **Do not share the VPK or the `work/` folder you build**: they contain code and data made from the game. See
> [NOTICE.md](NOTICE.md).

> **AI-assisted project.** The port's code, build scripts and documentation were written with AI coding
> assistants (Claude Code by Anthropic, and OpenAI Codex), directed and tested on a real PS Vita by the maintainer.

## Status

Playable through the opening areas, the Central hub and much of the East and West, with saves, sound, the map and
menus working. Later areas have had less testing.

| | |
| --- | --- |
| Frame rate | 30 FPS in most rooms. Some heavy fights still dip (for example ~23 FPS in East Lab with 12+ enemies awake). |
| Boot | About 27 s from launch to the first screen. 14 s of that is compiling shaders, and caching them is planned. |
| Room loads | 1–3 s; large rooms that decode big background layers take longer the first time. |
| Picture | 960×540, the game's 16:9 view scaled ×2, with 2 thin black rows top and bottom on the 544-pixel screen. |
| Clocks | The app sets 444 MHz CPU / 222 MHz GPU itself; no overclock plugin is needed. |

Expect bugs. If you hit one, open an issue with what you did and `ux0:data/hld-vm/runtime.log`.

## Getting the right game files

The port needs the **August 2016 build** of the game, the last one shipped with GameMaker bytecode. Later builds,
including the current Steam and GOG releases, compile the game to native code and cannot run on this port.

If you own the game on Steam:

1. Open the Steam console: run `steam://open/console` in a browser or the Run dialog.
2. Enter `download_depot 257850 257851 2557763276340557312`.
3. Steam saves it under `steamapps/content/app_257850/depot_257851/` and prints the full path when it is done.

`scripts/prepare_game.py` checks that your `data.win` is exactly this build (612,369,518 bytes, SHA-256
`8dcc8b53…f69`) and refuses anything else.

## Building

You need macOS (Apple silicon or Intel) or Linux x86-64, with Python 3.12+, `git`, `cmake`, `ninja`, `make`,
`pkg-config` and a C compiler (macOS: `xcode-select --install`, `brew install cmake ninja pkg-config`).

```sh
git clone <this repository> hld-vita && cd hld-vita
python3 scripts/bootstrap.py                         # 1. tools, about 400 MB of pinned downloads into deps/
python3 scripts/prepare_game.py /path/to/depot_257851 # 2. decompile + repack textures from your copy (~20 min)
python3 scripts/build.py                             # 3. build/HyperLightDrifter-HLDV00001.vpk
python3 scripts/install.py 192.168.1.50              # 4. copy to the Vita (your Vita's IP; FTP must be running)
```

Then on the Vita, open VitaShell, go to `ux0:data`, press X on `HyperLightDrifter.vpk` to install it, and launch it.

What each step does:

1. **`bootstrap.py`** downloads the VitaSDK softfp snapshot, the UndertaleModTool CLI, the VitaSDK softfp library
   layer and vitaGL. Each is pinned and checked against `deps/dependencies.json`. It rebuilds vitaGL for the softfp
   stubs, builds a small BC3 texture encoder and makes a Python environment in `deps/venv`. It installs nothing
   globally.
2. **`prepare_game.py`** links your game folder as `work/game`. It decompiles the 428 scripts the port translates
   (`port/hot-entries-v5.txt`) into `work/decompiled/`, using a temporary copy of `data.win` so your file is never
   changed. It repacks the texture pages into `work/vita/textures.bin` and the multi-core strips in
   `work/vita/pagez/`.
3. **`build.py`** configures and builds the engine, then runs `port/build-hldc-vita.py`. That script applies the
   port's engine changes, translates the decompiled scripts to C and links the app.
4. **`install.py`** uploads your game files, the prepared textures and the speedup marker files to
   `ux0:data/hld-vm/`, and the VPK to `ux0:data/`. Files already on the Vita are skipped, and saves are never touched.

The title ID is `HLDV00001`; set `HLD_TITLE_ID` before `build.py` to change it. To use your own LiveArea art, put
8-bit indexed PNGs named `icon0.png` (128×128), `bg.png` (840×500) and `startup.png` (280×158) in
`assets/livearea/`. The defaults are plain placeholders made by `scripts/make_livearea.py`.

## How it works

- **`engine/`** is a fork of Butterscotch with Vita platform code:
  - vitaGL rendering, with asynchronous presentation and a GXM command stream
  - multi-core texture decoding and a texture page cache
  - a threaded audio mixer
  - a loading screen
  - fixes for the game's 2016 bytecode format
- **`port/build-hldc-vita.py`** and its `*_source.py` modules are source transforms applied at build time:
  - collision and placement caches
  - a scenery fast path
  - fast paths for movement and terrain sweeps
  - draw batching
  - save, file and directory I/O tuned for the Vita's memory card
  - native C versions of a few of the game's hottest scripts
- **`port/gml_translate.py`** translates GameMaker bytecode, through its decompiled form, into C that calls the
  runner directly, for the scripts listed in `port/hot-entries-v5.txt`. Everything else runs on the interpreter.
- **Runtime switches.** Each speedup has an empty marker file in `ux0:data/hld-vm/` (listed in
  `scripts/install.py`). Delete a marker on the Vita to fall back to the game's own code for that part, which helps
  when tracking down a bug.

Each change was checked against the unmodified runner on a desktop build: game state and pixels matched. Speedups
were measured on hardware before being kept.

## Licence

AGPL-3.0, like Butterscotch; see [LICENSE](LICENSE). Third-party parts and the game's own rights are listed in
[NOTICE.md](NOTICE.md).
