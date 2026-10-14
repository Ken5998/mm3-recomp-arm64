# Developing mm3-recomp-arm64

This repository is an overlay: patches on a pinned
[mm3-recomp](https://github.com/MattSG/mm3-recomp) commit and its
[xboxrecomp](https://github.com/MattSG/xboxrecomp) runtime, the launcher's
setup, the scripts that apply and package them, and CI. The game is never
built here or on GitHub: the disc, the translated code and the binary must
not be published ([LEGAL.md](../LEGAL.md)).

## What is here

| Path | What it does |
| --- | --- |
| `patches/xboxrecomp/` | The runtime on Linux, arm64 and x86-64: device register (MMIO) traps through SIGSEGV with A64 and x86-64 decoders, the D3D11 renderer on DXVK-native with vkd3d's D3DCompile and an SDL2 window, XInput over SDL2 game controllers, audio over SDL2, case-insensitive game paths, the 4 GB guest address space, hard disk partitions as image files. Some of the runtime's sources have CRLF line ends, so they are applied with `git am --keep-cr`. |
| `patches/mm3-recomp/` | The game on Linux (CMake, the crash handler, the POSIX hooks), the launcher on SDL2 and OpenGL with a first-run setup (`src/mm3_setup.cpp`) that drives `launcher/mm3_setup.py`, full screen by default on arm64, and C flags Clang needs for the translated code. |
| `upstream.json` | The mm3-recomp and xboxrecomp commits the patches apply to, and the xboxrecomp commit the translator is taken from. |
| `launcher/mm3_setup.py` | The launcher's setup, run with the handheld's own Python: finds disc images, checks and copies the game files (`scripts/extract_disc.py`), downloads and unpacks the pinned build tools, translates (`scripts/generate_mm3.py`) and compiles the game, installs it with the libraries it loads, adds it to Steam and draws the Steam artwork from the disc's title image. |
| `toolchain.json` | The build tools it downloads: LLVM 20.1.8 (only clang, lld and llvm-ar are unpacked), CMake, Ninja, Python 3.13 with the Capstone and Pillow wheels, and the ARM64 sysroot; and the sysroot libraries the game ships with. |
| `scripts/prepare-source.sh DEST` | Checks out mm3-recomp and xboxrecomp at the pinned commits and applies the patches. |
| `scripts/build-sysroot.sh OUTDIR` | Builds the ARM64 sysroot from Ubuntu 24.04 arm64 packages, with DXVK-native 2.7.1 and vkd3d 2.0 built from source, in an `ubuntu:24.04` arm64 container. |
| `scripts/pin-sysroot.py ARCHIVE` | Pins a published sysroot in `toolchain.json`; with `--local` writes an override for one built locally (`MM3_TOOLCHAIN_OVERRIDE`). |
| `scripts/package-launcher.sh SOURCE OUTDIR` | On ARM64 Linux: sets up the build tools as the launcher does, builds `mm3-launcher` with them and packages it with `setup/` (the setup, the pins, the icon and the patched source, with RmlUi, FreeType and the pinned translator so the handheld builds offline). |
| `scripts/ci-tests.sh SOURCE` | Builds the runtime's tests with the launcher's build tools and runs them. |
| `.github/workflows/ci.yml` | On every push: builds the sysroot, then on GitHub's `ubuntu-24.04-arm` runner in a bare Fedora 44 (the base of ArmadaOS) packages the launcher with that sysroot, builds the runtime with the launcher's build tools and runs its tests, the DXVK renderer's on Xvfb with Mesa's lavapipe. |
| `.github/workflows/sysroot.yml` | On demand: publishes the sysroot as release `sysroot-VERSION`. |
| `.github/workflows/release.yml` | On demand: publishes the launcher as release `vVERSION` (the `VERSION` file). |

## The launcher on a handheld

`mm3-launcher` finds `setup/mm3_setup.py` beside itself and runs it with the
system's `python3`, reading one event a line (`@disc`, `@step`, `@progress`,
`@done`, `@error`). In `~/.local/share/MidtownMadness3`:

| Path | What it holds |
| --- | --- |
| `game_files/` | The files copied from the disc. |
| `source/` | The patched source the launcher carries, unpacked; `game_files` links to the folder above. |
| `toolchain/` | The build tools, one folder per pinned version, the `ld.lld` and `pkg-config` wrappers (they run on the sysroot's glibc, as the handheld may lack their libraries) and the CMake toolchain file. |
| `bin/mm3_recomp`, `lib/` | The game and the libraries it loads that a handheld may not have (DXVK, vkd3d, epoxy), found through its `$ORIGIN/../lib` RPATH. |
| `midtown-madness-3.sh` | Starts the game with the launcher's saved settings; Steam and the desktop menu run it. |
| `saves/`, `logs/`, `artwork/` | Saves, the build and game logs, the Steam artwork. |

The game starts with `MM3_LAUNCHER_SKIP=1`, so it reads the settings the
launcher saved instead of opening its own settings window.

## Changing the patches

```bash
scripts/prepare-source.sh ~/mm3-recomp
cd ~/mm3-recomp                     # commit runtime changes in tools/xboxrecomp
git format-patch "$(jq -r .commit ~/mm3-recomp-arm64/upstream.json)"..HEAD \
    --no-signature -o ~/mm3-recomp-arm64/patches/mm3-recomp
git -C tools/xboxrecomp format-patch "$(jq -r .xboxrecomp.commit ~/mm3-recomp-arm64/upstream.json)"..HEAD \
    --no-signature -o ~/mm3-recomp-arm64/patches/xboxrecomp
```

Remove the old patch files first, and never commit the `tools/xboxrecomp`
pointer in mm3-recomp. Keep the CRLF line ends of the runtime files that have
them: edit them with an editor that preserves them, not with tools that
rewrite line ends.

To try the game on a PC, build it for x86-64 Linux with the system's GCC or
Clang, SDL2, epoxy, OpenSSL, DXVK-native and vkd3d, after
`python3 scripts/extract_disc.py DISC.iso --out game_files` and
`python3 scripts/generate_mm3.py` (Python with `capstone` 5) in the patched
source. With `qemu-aarch64` and an arm64 build, the same runs on emulated
ARM64.

## How a release is made

1. `ci.yml` is green on the branch, which is then merged to `main`.
2. When `build-sysroot.sh` changes, run `sysroot.yml`, then pin the release:
   download its archive and run `scripts/pin-sysroot.py ARCHIVE`, and commit
   `toolchain.json`.
3. Bump `VERSION` and run `release.yml`, which packages the launcher with the
   pinned sysroot and publishes it as release `vVERSION`.
