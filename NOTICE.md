# Notices

## Hyper Light Drifter

Hyper Light Drifter is © Heart Machine. This is an unofficial fan project, not affiliated with or endorsed by
Heart Machine, Abylight or any publisher of the game.

This repository contains **none of the game's files**: no `data.win`, graphics, music, sounds, level files, text,
decompiled scripts or LiveArea art. To build and play you need your own copy of the game, and every file made from
it (`work/`, `build/`, the VPK) stays on your computer and your Vita. **Do not share a built VPK or anything in
`work/`**: they contain code and data made from the game.

Some of the port's C code (for example the enemy-shadow, flame-vent, movement and collision fast paths under
`port/`) re-implements specific parts of the game's own scripts in C so they run fast enough on the Vita, and the
port's sources name the game's scripts, objects, sprites and rooms where they hook in. That code was written for
this port and is published under the licence below, but it is derived from the game's behaviour; treat it as you
would a decompilation project.

## This project

The port is built on a fork of [Butterscotch](https://github.com/ButterscotchRunner/Butterscotch) (upstream commit
`dc9731e`), a GameMaker runner licensed under the **GNU Affero General Public License v3.0**. The engine changes in
`engine/` and the port in `port/`, `scripts/` and `deps/*.py` are distributed under the same licence (`LICENSE`).
If you distribute a build, the AGPL requires you to offer its corresponding source; pointing to this repository at
the commit you built satisfies that for the parts in it.

## Third-party components

| Component | Where | Licence |
| --- | --- | --- |
| Butterscotch (and its vendored libraries: miniaudio, miniz, stb, wuffs, Box2D, …) | `engine/` | AGPL-3.0; vendored libraries keep their own licences, see the files in `engine/vendor/` |
| stb_dxt (BC3 texture encoder) | `deps/bc3/stb_dxt.h` | Public domain / MIT |
| vitaGL | fetched to `deps/vitaGL` | LGPL-2.1 |
| VitaSDK and its softfp library layer | fetched to `deps/` | Their own licences (mostly GPL/LGPL/BSD/MIT toolchain and libraries) |
| UndertaleModTool CLI (used only on your computer to decompile your copy) | fetched to `deps/utmt` | GPL-3.0 |

Nothing under `deps/` except the scripts, the dependency manifest and `deps/bc3/` is part of this repository; the
rest is downloaded at the pinned versions listed in `deps/dependencies.json`.
