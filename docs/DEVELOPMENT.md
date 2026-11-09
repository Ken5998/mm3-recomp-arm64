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
| `patches/xboxrecomp/` | What the runtime still lacks on Linux arm64: device register (MMIO) traps through SIGSEGV for the NIC, USB, video overlay, GPU and audio registers with the A64 decoder (including SVE register writes), the AC'97 registers emulated on store, a build of the SDL3 renderer and its Direct3D 8 layer outside Windows, case-insensitive game paths, process exit and file reads and writes at an offset as on Windows, and a 10 MHz performance counter. The upstream runtime already has the POSIX memory model, partition images, handles and heap reclaim. Some of the runtime's sources have CRLF line ends, so they are applied with `git am --keep-cr`. |
| `patches/mm3-recomp/` | The game on Linux arm64 (CMake, the C flags Clang needs, the x86 intrinsics and Windows-only profiler kept out), a generation key that does not need git, and the launcher: `mm3-launcher` built without the game (`MM3_LAUNCHER_ONLY`) on SDL3 and RmlUi, with a first-run setup (`src/mm3_setup.cpp`) that drives `launcher/mm3_setup.py`, and handheld defaults (full screen, supersampling off, 2x MSAA). |
| `upstream.json` | The mm3-recomp release and the xboxrecomp commit the patches apply to. |
| `launcher/mm3_setup.py` | The launcher's setup, run with the handheld's own Python: finds disc images, checks and copies the game files (`scripts/extract_disc.py`), downloads and unpacks the pinned build tools, translates (`scripts/generate_mm3.py`) and compiles the game, runs it once without a window to capture its audio DSP programs and compiles it again with them built in, installs it with the libraries it loads, adds it to Steam and draws the Steam artwork from the disc's title image. |
| `toolchain.json` | The build tools it downloads: LLVM 20.1.8 (only clang, lld and llvm-ar are unpacked), CMake, Ninja, Python 3.13 with the Capstone and Pillow wheels, and the ARM64 sysroot; and the sysroot libraries the game ships with. |
| `scripts/prepare-source.sh DEST` | Checks out mm3-recomp and xboxrecomp at the pinned commits and applies the patches. |
| `scripts/build-sysroot.sh OUTDIR` | Builds the ARM64 sysroot from Ubuntu 24.04 arm64 packages, with SDL3 3.4.18, SDL_shadercross with its own DXC (the library and the `dxc` compiler the build uses for the renderer's shaders) and FFmpeg 7.1 (LGPL, for videos and WMA) built from source, in an `ubuntu:24.04` arm64 container. |
| `scripts/pin-sysroot.py ARCHIVE` | Pins a published sysroot in `toolchain.json`; with `--local` writes an override for one built locally (`MM3_TOOLCHAIN_OVERRIDE`). |
| `scripts/package-launcher.sh SOURCE OUTDIR` | On ARM64 Linux: sets up the build tools as the launcher does, builds `mm3-launcher` with them and packages it with `setup/` (the setup, the pins, the icon and the patched source, with RmlUi, FreeType, Dear ImGui and SDL3_image so the handheld builds offline). |
| `scripts/ci-tests.sh SOURCE` | Builds the runtime's tests with the launcher's build tools and runs them. |
| `.github/workflows/ci.yml` | On every push: builds the sysroot, then on GitHub's `ubuntu-24.04-arm` runner in a bare Fedora 44 (the base of ArmadaOS) packages the launcher with that sysroot, builds the runtime's tests with the launcher's build tools and runs those that need no GPU or Xbox device. |
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
| `bin/mm3_recomp`, `lib/` | The game and the libraries it loads that a handheld may not have (SDL3, DXC, FFmpeg), found through its `$ORIGIN/../lib` RPATH. |
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
Clang, SDL3, SDL_shadercross, DXC and FFmpeg, after
`python3 scripts/extract_disc.py DISC.iso --out game_files` and
`python3 scripts/generate_mm3.py` (Python with `capstone` 5) in the patched
source. With `qemu-aarch64` and an arm64 build, the same runs on emulated
ARM64.

## When the game hangs

A hang leaves a log that just ends. A switch of the runtime helps, set in
the environment of `midtown-madness-3.sh` (from a terminal, or as Steam launch
options before `%command%`):

- `RECOMP_GUEST_LOCK=1` lets one game thread run at a time, as on the Xbox's
  single CPU. The game's threads assume that order between their plain loads
  and stores, which x86 nearly gives and arm64 does not; if a hang goes away
  with it, it is a race between them. A thread that spins on another one
  without a kernel call deadlocks under it.

## How a release is made

1. `ci.yml` is green on the branch, which is then merged to `main`.
2. When `build-sysroot.sh` changes, run `sysroot.yml`, then pin the release:
   download its archive and run `scripts/pin-sysroot.py ARCHIVE`, and commit
   `toolchain.json`.
3. Bump `VERSION` and run `release.yml`, which packages the launcher with the
   pinned sysroot and publishes it as release `vVERSION`.
