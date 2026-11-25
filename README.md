# Hyper Light Drifter for PS Vita

An unofficial native PS Vita port of Hyper Light Drifter. It is not an emulator. The game's own GameMaker
bytecode runs on a Vita build of the [Butterscotch](https://github.com/ButterscotchRunner/Butterscotch) runner.
The game's hottest scripts are translated to C at build time. A set of Vita-specific engine changes keeps the game
at its 30 FPS target in most places.

> **You need your own copy of the game on Steam.** This repository and its downloads contain no game files: no
> `data.win`, art, music, levels, text or decompiled scripts. The builder reads your copy and makes everything
> game-specific on your computer. **Do not share the VPK or the `work/` folder you build**: they contain code and
> data made from the game. See [NOTICE.md](NOTICE.md).

> **AI-assisted project.** The port's code, build scripts and documentation were written with AI coding
> assistants (Claude Code by Anthropic, and OpenAI Codex), directed and tested on a real PS Vita by the maintainer.

## Status

Playable through the opening areas, the Central hub and much of the East and West, with saves, sound, the map and
menus working. Later areas have had less testing.

| | |
| --- | --- |
| Frame rate | 30 FPS in most rooms. Some heavy fights still dip (for example ~22 FPS in East Lab with 12+ enemies awake). |
| Boot | About 27 s from launch to the first screen. 14 s of that is compiling shaders, and caching them is planned. |
| Room loads | 1–3 s; large rooms that decode big background layers take longer the first time. |
| Picture | 960×540, the game's 16:9 view scaled ×2, with 2 thin black rows top and bottom on the 544-pixel screen. |
| Clocks | The app sets 444 MHz CPU / 222 MHz GPU itself; no overclock plugin is needed. |

Expect bugs. If you hit one, open an issue with what you did and `ux0:data/hld-vm/runtime.log`.

## Install (Windows or macOS)

You need:

- **Hyper Light Drifter on Steam.**
- **A hacked PS Vita** with VitaShell.
- **A Windows 10/11 PC or a Mac.**

Then:

1. Download **`HLD-Vita-Builder-<version>.zip`** from the [Releases](../../releases) page and unzip it anywhere.
2. Start the builder:
   - **Windows:** double-click **`HLD Vita Builder.bat`**. If SmartScreen says "Windows protected your PC", choose
     **More info → Run anyway**.
   - **macOS:** double-click **`HLD Vita Builder.command`**. The first time, right-click it and choose **Open**,
     because it is not from the App Store.
3. If the builder doesn't find the right version of the game on your computer, it downloads it from Steam (about
   1 GB; see [Getting the right game files](#getting-the-right-game-files)). Type your Steam username when asked,
   then your password and Steam Guard code.
4. Wait. The first run takes 20–40 minutes:
   1. It downloads its tools.
   2. It decompiles and translates the game scripts it needs.
   3. It repacks the textures for the Vita.
   4. It builds the app.

   Later runs reuse all of that.
5. At the end it offers to copy everything to your Vita over Wi-Fi:
   1. On the Vita, start VitaShell's FTP server (press **SELECT**).
   2. Type the IP address VitaShell shows into the builder.
   3. On the Vita, open `ux0:data` in VitaShell, press **X** on `HyperLightDrifter.vpk` to install it, and launch
      **Hyper Light Drifter**.

Your saves are never touched. The LiveArea icon and background are made from your own copy's key art
(`splash.png`), so the downloads ship no game art.

The builder kit holds the port's precompiled engine (no game code) and the tools to finish the build from your
copy. It uses Python 3.12+ if installed and otherwise downloads its own. Every download it makes is pinned and
checksummed.

## Controls

The Vita has two shoulder buttons where a PlayStation controller has four, so the port remaps a few of them. In-game
button prompts show PlayStation buttons, and you can rebind actions in the game's own control settings.

| On the Vita | Acts as (controller) | In game |
| --- | --- | --- |
| Left stick | Left stick | Move |
| Right stick | Right stick | Aim |
| ✕ ○ □ △ | ✕ ○ □ △ | As on a PlayStation controller |
| **L** / **R** shoulder | **L2** / **R2** triggers | As the triggers on a controller |
| **D-pad ←** | **L1** | Heal (on the map and inventory: previous page) |
| **D-pad →** | **R1** | Special (on the map and inventory: next page) |
| Rear touch pad (anywhere) | **L1** | Heal |
| D-pad ↑ / ↓ | D-pad ↑ / ↓ | Menus |
| START / SELECT | Options / Share | Pause / map |

D-pad left and right only act as L1/R1. The game itself uses them for nothing else, apart from developer cheats.

## Getting the right game files

The port needs the **August 2016 build** of the game, the last one shipped with GameMaker bytecode. Later builds,
including the current Steam and GOG releases, compile the game to native code and cannot run on this port. Steam still
serves the 2016 build to everyone who owns the game, so **you need Hyper Light Drifter on Steam**.

The builder fetches it for you:

1. It looks in Steam's usual folders, in case you already have it.
2. If not, it downloads it (about 1 GB) into `work/depot/` with
   [DepotDownloader](https://github.com/SteamRE/DepotDownloader). This is the standard open-source Steam depot tool;
   the version is pinned and checksummed.
3. You sign in with **your own Steam account**. Steam checks that the account owns the game.
4. DepotDownloader asks for your password and Steam Guard code directly; the builder never sees or stores them. Add
   `--qr` to sign in by scanning a QR code with the Steam mobile app instead.

If you would rather use Steam itself:

1. Open the Steam console (`steam://open/console` in a browser or the Run dialog).
2. Enter `download_depot 257850 257851 2557763276340557312`.
3. Run the builder; it finds the download.

Either way, the file is checked to be exactly this build (612,369,518 bytes, SHA-256 `8dcc8b53…f69`) before use.

## Building from source (macOS)

For working on the port itself. You need macOS (Apple silicon or Intel) with Python 3.12+ and a C compiler. Run
`xcode-select --install`, then `brew install cmake ninja pkg-config`. You also need `git` and `make`.

```sh
git clone https://github.com/gmacro7/hld-vita.git && cd hld-vita
python3 scripts/bootstrap.py       # 1. tools, about 400 MB of pinned downloads into deps/
python3 scripts/prepare_game.py   # 2. find/download the 2016 build, decompile, repack (~20 min)
python3 scripts/build.py           # 3. build/HyperLightDrifter-HLDV00001.vpk
python3 scripts/install.py <vita-ip>  # 4. copy to the Vita (VitaShell FTP must be running)
```

What each step does:

1. **`bootstrap.py`** downloads the pinned tools, each checked against `deps/dependencies.json`. It rebuilds vitaGL
   for the softfp stubs and creates a Python environment in `deps/venv`. It installs nothing globally. The tools:
   - the VitaSDK softfp snapshot and its library layer
   - the UndertaleModTool CLI
   - vitaGL
2. **`prepare_game.py`** finds or downloads the 2016 build and links it as `work/game`. It decompiles the 428
   scripts the port translates (`port/hot-entries-v5.txt`) into `work/decompiled/`, working on a temporary copy of
   `data.win`. It repacks the texture pages into `work/vita/textures.bin` and `work/vita/pagez/`.
3. **`build.py`** configures and builds the engine, then runs `port/build-hldc-vita.py`. That applies the port's
   engine changes, translates the decompiled scripts to C and links the app.
4. **`install.py`** uploads your game files, the prepared textures and the speedup marker files to
   `ux0:data/hld-vm/`, and the VPK to `ux0:data/`.

`python3 scripts/make_kit.py` makes the builder kit zip for a release.

The title ID is `HLDV00001`; set `HLD_TITLE_ID` before building to change it.

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
- **The interpreter's compiler profile** (`port/vm-profile/vm.gcda`) was recorded on a PS Vita over four routes, so
  GCC can lay out its hottest paths. It is keyed to relative paths, so it applies to every build, from source or from
  the kit.
- **Runtime switches.** Each speedup has an empty marker file in `ux0:data/hld-vm/` (listed in
  `scripts/install.py`). Delete a marker on the Vita to fall back to the game's own code for that part, which helps
  when tracking down a bug.

Each change was checked against the unmodified runner on a desktop build: game state and pixels matched. Speedups
were measured on hardware before being kept.

## Licence

AGPL-3.0, like Butterscotch; see [LICENSE](LICENSE). Third-party parts and the game's own rights are listed in
[NOTICE.md](NOTICE.md).
