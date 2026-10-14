#!/usr/bin/env python3
"""Builds Midtown Madness 3 on this handheld from the player's own disc.

The launcher runs this with the system's Python (standard library only) and
reads its standard output, one event per line, tab separated:

  @disc      PATH  BYTES          a disc image `scan` found
  @step      N  TOTAL  TITLE      a build step starts
  @progress  FRACTION  DETAIL     -1 when the step cannot tell how far it is
  @done      MESSAGE
  @error     MESSAGE

Everything the tools print goes to logs/build.log in the data folder.

  mm3_setup.py scan             list the disc images in the usual places
  mm3_setup.py build [ISO]      verify, extract, translate and compile; without
                                ISO, rebuild from the game files already copied
  mm3_setup.py steam            add the game to Steam with its artwork
  mm3_setup.py status           @done if the game is built for this launcher
  mm3_setup.py toolchain        fetch the build tools; @done with the CMake
                                toolchain file (for CI and packaging)
  mm3_setup.py artwork DIR      (internal) draw the Steam artwork into DIR

Nothing is installed on the system: the build tools, the source, the game and
its saves all live in ~/.local/share/MidtownMadness3.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path

NAME = "Midtown Madness 3"
APP = Path(__file__).resolve().parent
DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "MidtownMadness3"
LOGS = DATA / "logs"
TOOLS = DATA / "toolchain"
SOURCE = DATA / "source"
GAME_FILES = DATA / "game_files"
INSTALLED = DATA / "installed.json"
START_SCRIPT = DATA / "midtown-madness-3.sh"
ARTWORK = DATA / "artwork"

# Free space a build needs on top of the game files: the build tools
# (about 1.5 GB unpacked), the translated code and the objects.
BUILD_SPACE = 6 << 30


def version() -> str:
    return (APP / "VERSION").read_text().strip()


def pins() -> dict:
    """toolchain.json, with MM3_TOOLCHAIN_OVERRIDE's entries over it (CI tries
    a sysroot it has just built this way)."""
    result = json.loads((APP / "toolchain.json").read_text())
    override = os.environ.get("MM3_TOOLCHAIN_OVERRIDE")
    if override:
        result.update(json.loads(Path(override).read_text()))
    return result


# --- Output --------------------------------------------------------------------

_log = None


def log(text: str) -> None:
    global _log
    if _log is None:
        LOGS.mkdir(parents=True, exist_ok=True)
        _log = open(LOGS / "build.log", "a", encoding="utf-8", errors="replace")
    _log.write(text.rstrip("\n") + "\n")
    _log.flush()


def emit(kind: str, *fields) -> None:
    line = "@" + kind + "".join("\t" + str(f).replace("\t", " ").replace("\n", " ") for f in fields)
    print(line, flush=True)
    log(line)


class Failure(Exception):
    pass


def run(cmd, *, cwd=None, env=None, on_line=None) -> None:
    """Runs cmd with its output in the log, passing each line to on_line."""
    log("$ " + " ".join(str(c) for c in cmd))
    process = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
    last = ""
    for line in process.stdout:
        log(line)
        if line.strip():
            last = line.strip()
        if on_line:
            on_line(line.rstrip("\n"))
    if process.wait() != 0:
        raise Failure(f"{Path(str(cmd[0])).name} failed: {last[:200]}")


# --- Disc images ---------------------------------------------------------------

def scan() -> int:
    home = Path.home()
    roots = [home, home / "Downloads", home / "Desktop", home / "Documents", home / "Games",
             home / "ROMs", home / "roms", home / "Emulation/roms/xbox", home / "Emulation"]
    media = Path("/run/media") / os.environ.get("USER", "")
    if media.is_dir():
        roots += [card for card in media.iterdir() if card.is_dir()]
    found: dict[Path, os.stat_result] = {}

    def walk(folder: Path, depth: int) -> None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False) and depth > 0:
                    walk(Path(entry.path), depth - 1)
                elif entry.is_file() and entry.name.lower().endswith((".iso", ".xiso")):
                    stat = entry.stat()
                    if stat.st_size > 500 << 20:
                        found[Path(entry.path).resolve()] = stat
            except OSError:
                continue

    for root in roots:
        walk(root, 3 if root != home else 1)
    # Midtown Madness 3 first, then the newest.
    for path, stat in sorted(found.items(), key=lambda item: (
            "midtown" not in item[0].name.lower(), -item[1].st_mtime)):
        emit("disc", path, stat.st_size)
    return 0


# --- Build tools ---------------------------------------------------------------

def download(url: str, sha256: str, dest: Path, label: str, done: int, total: int) -> None:
    """Fetches url to dest, checking it against sha256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and file_sha256(dest) == sha256.lower():
        return
    log(f"download {url}")
    partial = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "mm3-recomp-arm64"})
    last = 0.0
    with urllib.request.urlopen(request, timeout=60) as response, open(partial, "wb") as out:
        received = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            now = time.monotonic()
            if now - last > 0.5:
                last = now
                emit("progress", f"{min(done + received, total) / total:.3f}",
                     f"{label}: {(done + received) / (1 << 20):.0f} of {total / (1 << 20):.0f} MB")
    if digest.hexdigest() != sha256.lower():
        partial.unlink()
        raise Failure(f"{dest.name} is not the file this launcher expects (checksum mismatch)")
    partial.replace(dest)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            digest.update(chunk)
    return digest.hexdigest()


def wanted(name: str, members: list[str] | None) -> bool:
    """With members, only those paths (after stripping) and, for the ones
    ending in /, everything below them."""
    if not name:
        return False
    return not members or any(name == m or (m.endswith("/") and name.startswith(m))
                              for m in members)


def unpack(archive: Path, dest: Path, strip: int, members: list[str] | None) -> None:
    """Unpacks archive into dest, dropping `strip` leading path parts and, with
    members, keeping only those (see wanted)."""
    temporary = dest.with_name(dest.name + ".new")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    if archive.name.endswith(".zip") or archive.name.endswith(".whl"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = "/".join(info.filename.split("/")[strip:])
                if not wanted(name, members):
                    continue
                target = temporary / name
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                mode = info.external_attr >> 16
                if mode & 0o111:
                    target.chmod(0o755)
    else:
        with tarfile.open(archive) as t:
            for member in t:
                name = "/".join(member.name.split("/")[strip:])
                if not wanted(name, members):
                    continue
                member.name = name
                if member.islnk():
                    member.linkname = "/".join(member.linkname.split("/")[strip:])
                t.extract(member, temporary, filter="tar")
    shutil.rmtree(dest, ignore_errors=True)
    temporary.replace(dest)


def tool_dir(key: str, pin: dict) -> Path:
    return TOOLS / f"{key}-{pin['version']}"


def tool_ready(key: str, pin: dict) -> bool:
    folder = tool_dir(key, pin)
    try:
        marker = (folder / ".complete").read_text().strip()
    except OSError:
        return False
    return marker == pin["sha256"].lower() and all((folder / p).exists() for p in pin.get("check", []))


def borrow(key: str, pin: dict) -> bool:
    """Links the members of another launcher's unpacked copy of the same
    release instead of downloading it again: Pinyon Shift's ARM64 launcher
    unpacks the same LLVM."""
    for pattern in pin.get("borrow", []):
        for found in sorted(Path.home().glob(pattern), reverse=True):
            if not all((found / p).is_file() for p in pin.get("check", [])):
                continue
            try:
                about = subprocess.run([found / "bin/clang", "--version"], capture_output=True,
                                       text=True, timeout=60).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            if f"version {pin['version']}" not in about:
                continue
            folder = tool_dir(key, pin)
            shutil.rmtree(folder, ignore_errors=True)
            for member in pin["members"]:
                target = folder / member.rstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(found / member.rstrip("/"))
            (folder / ".complete").write_text(pin["sha256"].lower())
            log(f"{key}: using {found}")
            return True
    return False


def fetch_tools() -> dict[str, Path]:
    """Downloads and unpacks the pinned build tools that are not there yet."""
    all_pins = pins()
    wanted = {key: pin for key, pin in all_pins.items() if isinstance(pin, dict) and "url" in pin}
    missing = {key: pin for key, pin in wanted.items()
               if not tool_ready(key, pin) and not borrow(key, pin)}
    total = sum(pin["size"] for pin in missing.values()) or 1
    done = 0
    for key, pin in missing.items():
        archive = TOOLS / "downloads" / pin["url"].rsplit("/", 1)[1].replace("%2B", "+")
        label = pin.get("label", key)
        download(pin["url"], pin["sha256"], archive, label, done, total)
        done += pin["size"]
        emit("progress", f"{done / total:.3f}", f"Unpacking {label}")
        unpack(archive, tool_dir(key, pin), pin.get("strip", 0), pin.get("members"))
        (tool_dir(key, pin) / ".complete").write_text(pin["sha256"].lower())
        archive.unlink()
    # Older versions of the tools: a new pin replaces them.
    keep = {tool_dir(key, pin).name for key, pin in wanted.items()} | {"downloads", "bin"}
    for old in TOOLS.iterdir():
        if old.is_dir() and old.name not in keep and not old.name.endswith(".new"):
            shutil.rmtree(old, ignore_errors=True)
    return {key: tool_dir(key, pin) for key, pin in wanted.items()}


def write_wrappers(tools: dict[str, Path]) -> Path:
    """The linker and pkg-config the build runs, and the CMake toolchain file.

    The LLVM release's ld.lld and the sysroot's pkgconf link libraries an
    image-based system may not have (libxml2.so.2, libpkgconf.so.3), so they
    run on the sysroot's own glibc through its dynamic loader."""
    sysroot = tools["sysroot"]
    libs = sysroot / "usr/lib/aarch64-linux-gnu"
    loader = sysroot / "usr/lib/ld-linux-aarch64.so.1"
    bin_dir = TOOLS / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, program in (("ld.lld", tools["llvm"] / "bin/lld"),
                          ("pkg-config", sysroot / "usr/bin/pkgconf")):
        wrapper = bin_dir / name
        wrapper.write_text(
            "#!/bin/sh\n"
            f'exec "{loader}" --library-path "{libs}" --argv0 {name} "{program}" "$@"\n')
        wrapper.chmod(0o755)
    toolchain = TOOLS / "mm3-arm64.cmake"
    llvm = tools["llvm"] / "bin"
    toolchain.write_text(f"""# Written by mm3_setup.py: the pinned LLVM against the pinned sysroot.
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_SYSROOT "{sysroot}")
set(CMAKE_C_COMPILER "{llvm}/clang")
set(CMAKE_CXX_COMPILER "{llvm}/clang++")
set(CMAKE_C_COMPILER_TARGET aarch64-linux-gnu)
set(CMAKE_CXX_COMPILER_TARGET aarch64-linux-gnu)
# An RPATH rather than a RUNPATH: it also serves the libraries the game
# loads from lib/ (vkd3d-utils loads vkd3d, d3d11 loads dxgi).
set(_mm3_link "-fuse-ld=lld --ld-path={bin_dir}/ld.lld -static-libstdc++ -static-libgcc -Wl,--disable-new-dtags")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${{_mm3_link}}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${{_mm3_link}}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
set(PKG_CONFIG_EXECUTABLE "{bin_dir}/pkg-config" CACHE FILEPATH "")
set(ENV{{PKG_CONFIG_SYSROOT_DIR}} "{sysroot}")
set(ENV{{PKG_CONFIG_LIBDIR}} "{libs}/pkgconfig:{sysroot}/usr/share/pkgconfig")
set(ENV{{PKG_CONFIG_PATH}} "")
""")
    return toolchain


def pinned_python(tools: dict[str, Path]) -> tuple[Path, dict]:
    """The pinned Python, with the generator's capstone and the artwork's
    Pillow on its path."""
    env = dict(os.environ)
    paths = [str(tools[key]) for key in ("capstone", "pillow") if key in tools]
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return tools["python"] / "bin/python3", env


# --- Build ---------------------------------------------------------------------

STEPS = ["Checking your disc and copying the game files", "Downloading the build tools",
         "Translating the game code", "Compiling the game", "Finishing"]


def step(index: int) -> None:
    emit("step", index + 1, len(STEPS), STEPS[index])


def prepare_source() -> None:
    """Unpacks this launcher's source unless it is already there."""
    stamp = SOURCE / ".launcher-version"
    if stamp.is_file() and stamp.read_text().strip() == version():
        return
    emit("progress", "-1", "Unpacking the source")
    unpack(APP / "source.tar.xz", SOURCE, 1, None)
    link = SOURCE / "game_files"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(GAME_FILES)
    stamp.write_text(version())


def free_space(path: Path) -> int:
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def extract(iso: Path) -> None:
    """Checks the disc's code and copies its files to game_files/."""
    if not iso.is_file():
        raise Failure(f"{iso} is not there any more")
    need = iso.stat().st_size + BUILD_SPACE
    if free_space(DATA) < need:
        raise Failure(f"Not enough free space: the build needs about {need / (1 << 30):.0f} GB "
                      f"in {DATA.parent}")
    shutil.rmtree(GAME_FILES, ignore_errors=True)

    def on_line(line: str) -> None:
        if line.startswith("  extracted "):
            done, total = line.split()[1].split("/")
            emit("progress", f"{int(done) / int(total):.3f}", f"Copied {done} of {total} files")
        elif line.startswith("default.xbe code matches"):
            emit("progress", "0", "Your disc checks out; copying its files")

    emit("progress", "-1", "Checking the game code on the disc")
    try:
        run([sys.executable, "-I", SOURCE / "scripts/extract_disc.py", iso, "--out", GAME_FILES,
             "--skip-iso-hash"], cwd=SOURCE, on_line=on_line)
    except Failure:
        shutil.rmtree(GAME_FILES, ignore_errors=True)
        last = (LOGS / "build.log").read_text(errors="replace").strip().splitlines()[-12:]
        if any("code hash" in line for line in last):
            raise Failure("This disc image is not a Midtown Madness 3 release this port "
                          "supports (USA or Europe)")
        if any("not a readable Xbox disc image" in line for line in last):
            raise Failure("This file is not an Xbox disc image")
        raise
    (GAME_FILES / ".complete").write_text(str(iso))


def generate(tools: dict[str, Path]) -> None:
    python, env = pinned_python(tools)
    start = time.monotonic()
    count = [0]

    def on_line(line: str) -> None:
        count[0] += 1
        if count[0] % 20 == 0:
            minutes = (time.monotonic() - start) / 60
            emit("progress", "-1", f"{minutes:.0f} min so far; this takes 10 to 25 minutes")

    emit("progress", "-1", "This takes 10 to 25 minutes")
    run([python, SOURCE / "scripts/generate_mm3.py"], cwd=SOURCE, env=env, on_line=on_line)


def jobs() -> int:
    """Parallel compiles: one per core, and no more than one per 1.5 GB of RAM,
    since the translated sources are large."""
    memory = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    memory = int(line.split()[1]) << 10
    except OSError:
        pass
    by_memory = max(1, int(memory / (1.5 * (1 << 30)))) if memory else 2
    return max(1, min(os.cpu_count() or 1, by_memory))


def compile_game(tools: dict[str, Path], toolchain: Path) -> Path:
    build = SOURCE / "build"
    cmake = tools["cmake"] / "bin/cmake"
    ninja = tools["ninja"] / "ninja"
    emit("progress", "-1", "Configuring")
    run([cmake, "-S", SOURCE, "-B", build, "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={ninja}",
         f"-DCMAKE_TOOLCHAIN_FILE={toolchain}", "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_C_FLAGS_RELEASE=-O2 -DNDEBUG", "-DCMAKE_CXX_FLAGS_RELEASE=-O2 -DNDEBUG",
         "-DCMAKE_BUILD_RPATH=$ORIGIN/../lib",
         f"-DFETCHCONTENT_SOURCE_DIR_RMLUI={SOURCE / 'third_party/rmlui'}",
         f"-DFETCHCONTENT_SOURCE_DIR_FREETYPE={SOURCE / 'third_party/freetype'}",
         "-DFETCHCONTENT_FULLY_DISCONNECTED=ON"], cwd=SOURCE)

    def on_line(line: str) -> None:
        if line.startswith("[") and "/" in line.split("]")[0]:
            done, total = line[1:].split("]")[0].split("/")
            emit("progress", f"{int(done) / int(total):.3f}",
                 f"Compiled {done} of {total} files")

    count = jobs()
    log(f"compiling with {count} jobs")
    run([ninja, "-C", build, f"-j{count}", "mm3_recomp"], cwd=SOURCE, on_line=on_line)
    return build / "mm3_recomp"


def install_libraries(tools: dict[str, Path]) -> None:
    """The sysroot's libraries the game loads that a handheld may not have
    (DXVK, vkd3d, epoxy) into lib/, which the game's rpath names."""
    libs = tools["sysroot"] / "usr/lib/aarch64-linux-gnu"
    lib = DATA / "lib"
    shutil.rmtree(lib, ignore_errors=True)
    lib.mkdir(parents=True)
    for name in pins()["runtime_libraries"]:
        if not (libs / name).exists():
            raise Failure(f"the build tools lack {name}")
        shutil.copy2((libs / name).resolve(), lib / name)


def install(binary: Path, tools: dict[str, Path]) -> None:
    """The game in bin/, the libraries it loads in lib/, the start script."""
    install_libraries(tools)
    (DATA / "bin").mkdir(exist_ok=True)
    temporary = DATA / "bin/mm3_recomp.new"
    shutil.copy2(binary, temporary)
    temporary.replace(DATA / "bin/mm3_recomp")
    (DATA / "saves").mkdir(exist_ok=True)
    START_SCRIPT.write_text(
        "#!/bin/sh\n"
        "# Starts Midtown Madness 3 with the launcher's saved settings.\n"
        "# Written by the Midtown Madness 3 launcher (mm3_setup.py).\n"
        f'cd "{DATA}" || exit 1\n'
        f'mkdir -p "{LOGS}"\n'
        f'[ -f "{LOGS}/game.log" ] && mv -f "{LOGS}/game.log" "{LOGS}/game.previous.log"\n'
        "export MM3_LAUNCHER_SKIP=1\n"
        f'export MM3_SAVE_DIR="{DATA}/saves"\n'
        f'exec "{DATA}/bin/mm3_recomp" "$@" >"{LOGS}/game.log" 2>&1\n')
    START_SCRIPT.chmod(0o755)


def build(iso: Path | None) -> int:
    if DATA.exists() and not os.access(DATA, os.W_OK):
        raise Failure(f"{DATA} is not writable")
    DATA.mkdir(parents=True, exist_ok=True)
    log(f"=== build {time.strftime('%Y-%m-%d %H:%M:%S')}, launcher {version()}, iso {iso}")
    INSTALLED.unlink(missing_ok=True)

    step(0)
    prepare_source()
    if iso:
        extract(iso)
    elif not (GAME_FILES / ".complete").is_file():
        raise Failure("Choose your disc image first")

    step(1)
    if free_space(DATA) < BUILD_SPACE:
        raise Failure(f"Not enough free space: the build needs about "
                      f"{BUILD_SPACE / (1 << 30):.0f} GB in {DATA.parent}")
    tools = fetch_tools()
    toolchain = write_wrappers(tools)

    step(2)
    generate(tools)

    step(3)
    binary = compile_game(tools, toolchain)

    step(4)
    emit("progress", "-1", "Installing")
    install(binary, tools)
    try:
        draw_artwork_with(tools)
    except Failure as error:
        log(f"artwork: {error}")
    write_desktop_entry()
    INSTALLED.write_text(json.dumps({"version": version(), "built": int(time.time())}))
    shutil.rmtree(SOURCE / "build/CMakeFiles", ignore_errors=True)
    emit("done", "Ready to play")
    return 0


def toolchain() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    tools = fetch_tools()
    path = write_wrappers(tools)
    install_libraries(tools)
    emit("done", path)
    return 0


def status() -> int:
    try:
        installed = json.loads(INSTALLED.read_text())
    except (OSError, ValueError):
        installed = {}
    if installed.get("version") == version() and (DATA / "bin/mm3_recomp").is_file():
        emit("done", "built")
        return 0
    emit("error", "not built" if not installed else "built by another launcher version")
    return 1


# --- Desktop menu and Steam ----------------------------------------------------

def icon_path() -> Path:
    return ARTWORK / "icon.png" if (ARTWORK / "icon.png").is_file() else APP / "icon.png"


def write_desktop_entry() -> None:
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    entry = data / "applications/midtown-madness-3.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAME}\n"
        "Comment=Recompiled from your own Xbox disc\n"
        f'Exec="{START_SCRIPT}"\n'
        f"Icon={icon_path()}\n"
        "Categories=Game;\n"
        "Terminal=false\n")


MAP, STRING, INT32, END = 0x00, 0x01, 0x02, 0x08


def _cstring(data: bytes, position: int) -> tuple[str, int]:
    end = data.index(b"\0", position)
    return data[position:end].decode("utf-8", errors="surrogateescape"), end + 1


def parse_vdf(data: bytes, position: int = 0) -> tuple[dict, int]:
    """Steam's binary KeyValues, as shortcuts.vdf stores them."""
    result: dict = {}
    while position < len(data):
        kind = data[position]
        position += 1
        if kind == END:
            return result, position
        key, position = _cstring(data, position)
        if kind == MAP:
            result[key], position = parse_vdf(data, position)
        elif kind == STRING:
            result[key], position = _cstring(data, position)
        elif kind == INT32:
            result[key] = struct.unpack_from("<i", data, position)[0]
            position += 4
        else:
            raise ValueError(f"unsupported VDF field type {kind}")
    return result, position


def dump_vdf(value: dict) -> bytes:
    out = bytearray()
    for key, item in value.items():
        name = key.encode("utf-8", errors="surrogateescape") + b"\0"
        if isinstance(item, dict):
            out += bytes([MAP]) + name + dump_vdf(item)
        elif isinstance(item, int):
            out += bytes([INT32]) + name + struct.pack("<i", item)
        else:
            out += bytes([STRING]) + name + str(item).encode("utf-8", errors="surrogateescape") + b"\0"
    return bytes(out) + bytes([END])


def steam_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []
    for candidate in (home / ".local/share/Steam", home / ".steam/steam",
                      home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"):
        if (candidate / "userdata").is_dir() and candidate.resolve() not in [r.resolve() for r in roots]:
            roots.append(candidate)
    return roots


def steam_running() -> bool:
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        # Steam runs under FEX on ARM64: its own path is an argument then.
        if any(part.endswith(b"/steam") or part == b"steam" for part in cmdline[:3] if part) and \
                any(b"ubuntu12_32" in part or part.endswith(b"steam") for part in cmdline[:3]):
            return True
    return False


def stop_steam() -> bool:
    if not steam_running():
        return False
    emit("progress", "-1", "Closing Steam so it keeps the new entry")
    subprocess.run(["steam", "-shutdown"], capture_output=True)
    deadline = time.monotonic() + 60
    while steam_running():
        if time.monotonic() > deadline:
            raise Failure("Steam did not close; quit it and try again")
        time.sleep(1)
    return True


def steam() -> int:
    if not (DATA / "bin/mm3_recomp").is_file():
        raise Failure("Build the game first")
    roots = steam_roots()
    if not roots:
        raise Failure("Steam is not set up for this account")
    if not (ARTWORK / "capsule.png").is_file():
        emit("progress", "-1", "Drawing the artwork")
        try:
            draw_artwork_with(fetch_tools_quiet())
        except Failure as error:
            log(f"artwork: {error}")
    exe = f'"{START_SCRIPT}"'
    app_id = zlib.crc32((exe + NAME).encode("utf-8")) | 0x80000000
    signed = struct.unpack("<i", struct.pack("<I", app_id))[0]
    restart = stop_steam()
    users = 0
    try:
        for root in roots:
            for user in (root / "userdata").iterdir():
                if not (user.is_dir() and user.name.isdigit() and user.name != "0"):
                    continue
                config = user / "config"
                config.mkdir(parents=True, exist_ok=True)
                path = config / "shortcuts.vdf"
                shortcuts: dict = {}
                if path.is_file() and path.stat().st_size:
                    shortcuts = parse_vdf(path.read_bytes())[0].get("shortcuts", {})
                    backup = path.with_suffix(".vdf.mm3-backup")
                    if not backup.exists():
                        shutil.copyfile(path, backup)
                entries = [e for e in shortcuts.values() if e.get("AppName", e.get("appname")) != NAME]
                entries.append({
                    "appid": signed, "AppName": NAME, "Exe": exe, "StartDir": f'"{DATA}"',
                    "icon": str(icon_path()), "ShortcutPath": "", "LaunchOptions": "",
                    "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1, "OpenVR": 0,
                    "Devkit": 0, "DevkitGameID": "", "DevkitOverrideAppID": 0, "LastPlayTime": 0,
                    "FlatpakAppID": "", "tags": {},
                })
                temporary = path.with_suffix(".vdf.tmp")
                temporary.write_bytes(dump_vdf({"shortcuts": {str(i): e for i, e in enumerate(entries)}}))
                temporary.replace(path)
                grid = config / "grid"
                grid.mkdir(exist_ok=True)
                for source, name in (("capsule.png", f"{app_id}p.png"), ("wide.png", f"{app_id}.png"),
                                     ("hero.png", f"{app_id}_hero.png"), ("logo.png", f"{app_id}_logo.png"),
                                     ("icon.png", f"{app_id}_icon.png")):
                    if (ARTWORK / source).is_file():
                        shutil.copyfile(ARTWORK / source, grid / name)
                users += 1
    finally:
        if restart and shutil.which("steam"):
            subprocess.Popen(["steam"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    if not users:
        raise Failure("No Steam user has signed in on this handheld yet")
    emit("done", "Added to Steam" + (", which is starting again" if restart else ""))
    return 0


def fetch_tools_quiet() -> dict[str, Path]:
    return {key: tool_dir(key, pin) for key, pin in pins().items()
            if isinstance(pin, dict) and "url" in pin and (tool_dir(key, pin) / ".complete").is_file()}


# --- Artwork -------------------------------------------------------------------

def draw_artwork_with(tools: dict[str, Path]) -> None:
    """Draws the Steam artwork with the pinned Python, which has Pillow."""
    if "python" not in tools or "pillow" not in tools:
        raise Failure("the build tools are not there")
    python, env = pinned_python(tools)
    run([python, Path(__file__), "artwork", ARTWORK], env=env)


def title_image(xbe: bytes):
    """The XBE's $$XTIMAGE: the 128x128 picture the dashboard shows for the
    game, a DXT1 texture in an XPR0 resource. Returns RGBA bytes and size."""
    base = struct.unpack_from("<I", xbe, 0x104)[0]
    count, table = struct.unpack_from("<II", xbe, 0x11C)
    data = None
    for i in range(count):
        _, _, _, raw, size, name_at = struct.unpack_from("<6I", xbe, table - base + i * 0x38)
        if xbe[name_at - base:name_at - base + 16].split(b"\0")[0] == b"$$XTIMAGE":
            data = xbe[raw:raw + size]
    if not data or data[:4] != b"XPR0":
        raise Failure("no title image in default.xbe")
    header = struct.unpack_from("<I", data, 8)[0]
    fmt = struct.unpack_from("<I", data, 0x18)[0]
    if (fmt >> 8) & 0xFF != 0x0C:
        raise Failure("the title image is not DXT1")
    w, h = 1 << ((fmt >> 20) & 15), 1 << ((fmt >> 24) & 15)
    pixels = bytearray(w * h * 4)
    blocks = data[header:]

    def rgb(c: int) -> tuple[int, int, int]:
        return (c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31

    offset = 0
    for by in range(h // 4):
        for bx in range(w // 4):
            c0, c1, bits = struct.unpack_from("<HHI", blocks, offset)
            offset += 8
            a, b = rgb(c0), rgb(c1)
            if c0 > c1:
                colors = [a + (255,), b + (255,), tuple((2 * a[k] + b[k]) // 3 for k in range(3)) + (255,),
                          tuple((a[k] + 2 * b[k]) // 3 for k in range(3)) + (255,)]
            else:
                colors = [a + (255,), b + (255,), tuple((a[k] + b[k]) // 2 for k in range(3)) + (255,),
                          (0, 0, 0, 0)]
            for py in range(4):
                for px in range(4):
                    at = ((by * 4 + py) * w + bx * 4 + px) * 4
                    pixels[at:at + 4] = bytes(colors[(bits >> (2 * (py * 4 + px))) & 3])
    return bytes(pixels), (w, h)


def font_file(bold: bool) -> str | None:
    style = "Bold" if bold else "Regular"
    try:
        found = subprocess.run(["fc-match", "-f", "%{file}", f"sans-serif:style={style}"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        if found:
            return found
    except (OSError, subprocess.SubprocessError):
        pass
    for folder in ("/usr/share/fonts", "/usr/local/share/fonts"):
        for path in sorted(Path(folder).rglob("*.ttf")) if Path(folder).is_dir() else []:
            if ("Bold" in path.name) == bold and "Sans" in path.name:
                return str(path)
    return None


def artwork(out: Path) -> int:
    """Steam's five images, from the game's own title image on the player's disc
    and the project's icon. Run with the pinned Python (Pillow)."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    pixels, size = title_image((GAME_FILES / "default.xbe").read_bytes())
    shot = Image.frombytes("RGBA", size, pixels).convert("RGB")
    amber, ink, dark = (232, 163, 61), (255, 255, 255), (18, 20, 24)

    def font(px: int, bold: bool = True):
        path = font_file(bold)
        if path:
            f = ImageFont.truetype(path, px)
            try:
                f.set_variation_by_name("Bold" if bold else "Regular")
            except (OSError, ValueError, AttributeError):
                pass
            return f
        return ImageFont.load_default(px)

    def background(w: int, h: int, blur: float) -> "Image.Image":
        side = max(w, h)
        image = shot.resize((side, side), Image.LANCZOS)
        image = image.crop(((side - w) // 2, (side - h) // 2, (side - w) // 2 + w, (side - h) // 2 + h))
        image = image.filter(ImageFilter.GaussianBlur(blur))
        image = Image.blend(image, Image.new("RGB", (w, h), dark), 0.45)
        shade = Image.linear_gradient("L").resize((w, h))
        return Image.composite(Image.new("RGB", (w, h), dark), image, shade.point(lambda v: v * 0.85))

    def framed(side: int) -> "Image.Image":
        image = shot.resize((side, side), Image.LANCZOS).convert("RGBA")
        mask = Image.new("L", (side, side), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), radius=side // 14, fill=255)
        image.putalpha(mask)
        return image

    def paste_with_shadow(canvas, image, xy) -> None:
        shadow = Image.new("RGBA", (image.width + 80, image.height + 80), (0, 0, 0, 0))
        shadow.paste((0, 0, 0, 170), (40, 48), image)
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        canvas.paste(shadow, (xy[0] - 40, xy[1] - 40), shadow)
        canvas.paste(image, xy, image)

    def title(draw, xy, px: int, anchor: str, lines: tuple[str, str]) -> None:
        """MIDTOWN / MADNESS 3, the 3 in amber."""
        x, y = xy
        f = font(px)
        first, second = lines
        draw.text((x, y), first, font=f, fill=ink, anchor=anchor + "s")
        spacing = int(px * 1.02)
        width = draw.textlength(second + " 3", font=f)
        if anchor == "m":
            start = x - width / 2
        elif anchor == "r":
            start = x - width
        else:
            start = x
        draw.text((start, y + spacing), second + " ", font=f, fill=ink, anchor="ls")
        draw.text((start + draw.textlength(second + " ", font=f), y + spacing), "3", font=f,
                  fill=amber, anchor="ls")

    out.mkdir(parents=True, exist_ok=True)

    capsule = background(600, 900, 28).convert("RGBA")
    paste_with_shadow(capsule, framed(380), (110, 120))
    title(ImageDraw.Draw(capsule), (300, 650), 78, "m", ("MIDTOWN", "MADNESS"))
    capsule.convert("RGB").save(out / "capsule.png")

    wide = background(920, 430, 22).convert("RGBA")
    paste_with_shadow(wide, framed(300), (60, 65))
    title(ImageDraw.Draw(wide), (410, 205), 72, "l", ("MIDTOWN", "MADNESS"))
    wide.convert("RGB").save(out / "wide.png")

    # Steam lays the logo over the hero's left side: the picture fades in on
    # the right.
    hero = background(1920, 620, 36)
    picture = shot.resize((620, 620), Image.LANCZOS)
    ramp = [min(255, x * 255 // 220, (619 - x) * 255 // 90) for x in range(620)]
    fade = Image.frombytes("L", (620, 1), bytes(ramp)).resize((620, 620))
    hero.paste(picture, (1920 - 620 - 120, 0), fade)
    hero.save(out / "hero.png")

    logo = Image.new("RGBA", (1000, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo)
    title(draw, (20, 120), 104, "l", ("MIDTOWN", "MADNESS"))
    shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 160), (4, 6), logo)
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    Image.alpha_composite(shadow, logo).crop(logo.getbbox()).save(out / "logo.png")

    shutil.copyfile(APP / "icon.png", out / "icon.png")
    print(f"artwork in {out}")
    return 0


# --- Main ----------------------------------------------------------------------

def main(argv: list[str]) -> int:
    # The launcher's Cancel ends the whole process group.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(130))
    command = argv[1] if len(argv) > 1 else ""
    try:
        if command == "scan":
            return scan()
        if command == "build":
            return build(Path(argv[2]).expanduser() if len(argv) > 2 and argv[2] else None)
        if command == "steam":
            return steam()
        if command == "status":
            return status()
        if command == "toolchain":
            return toolchain()
        if command == "artwork":
            return artwork(Path(argv[2]))
    except Failure as error:
        emit("error", str(error))
        return 1
    except Exception as error:  # an unexpected failure still reaches the launcher
        log(f"{type(error).__name__}: {error}")
        import traceback
        log(traceback.format_exc())
        emit("error", f"Unexpected error: {error}")
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
