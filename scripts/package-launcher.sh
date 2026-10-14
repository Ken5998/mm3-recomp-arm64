#!/usr/bin/env bash
# Packages the ARM64 launcher: mm3-launcher, built with the pinned toolchain
# the device uses, beside setup/ with mm3_setup.py, the toolchain pins and the
# patched source the device builds the game from. No game files: the game is
# only ever built on the player's handheld, from their own disc.
#
#   scripts/package-launcher.sh SOURCE OUTDIR   (SOURCE made by prepare-source.sh)
#
# Runs on ARM64 Linux with Python 3.11 or newer, git, curl, tar and xz, such as
# a fedora:44 container on GitHub's ARM64 runner. The build tools go where the
# launcher keeps them, under $XDG_DATA_HOME/MidtownMadness3 (worth caching);
# MM3_TOOLCHAIN_OVERRIDE swaps the sysroot (pin-sysroot.py --local).
# Writes OUTDIR/MidtownMadness3-Launcher-linux-arm64.tar.gz.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
src="$(cd "$1" && pwd)"
out="$(mkdir -p "$2" && cd "$2" && pwd)"
work="$(mktemp -d)"
stage="$work/MidtownMadness3"
mkdir -p "$stage/setup"

# The sources CMake would fetch (RmlUi, FreeType), in third_party/: the
# device builds offline.
python3 - "$src" <<'PY'
import hashlib, re, subprocess, sys, time, urllib.request
from pathlib import Path
src = Path(sys.argv[1])
declared = re.findall(r"FetchContent_Declare\((\w+)\s+URL (\S+)\s+URL_HASH SHA256=(\w+)",
                      (src / "CMakeLists.txt").read_text())
assert {name for name, _, _ in declared} == {"freetype", "rmlui"}, declared
for name, url, digest in declared:
    dest = src / "third_party" / name
    if dest.is_dir():
        continue
    for attempt in range(4):
        try:
            data = urllib.request.urlopen(url, timeout=120).read()
            break
        except OSError as error:
            print(f"{url}: {error}; retrying", flush=True)
            time.sleep(4 * (attempt + 1))
    else:
        data = urllib.request.urlopen(url, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != digest.lower():
        sys.exit(f"{url}: checksum mismatch")
    dest.mkdir(parents=True)
    subprocess.run(["tar", "-xzf", "-", "--strip-components", "1", "-C", dest], input=data, check=True)
    print(f"{name}: {url}")
PY

# The translator generate_mm3.py extracts from the pinned xboxrecomp commit
# (with its fld tbyte fix), so the device needs no git history.
(cd "$src" && python3 - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("generate_mm3", "scripts/generate_mm3.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("translator:", module.TOOLKIT)
PY
)
ls "$src"/tools/.generator/*/tools/recomp/__main__.py > /dev/null

top="$(basename "$src")"
tar -C "$(dirname "$src")" --exclude-vcs --exclude='__pycache__' \
    --exclude="$top/game_files" --exclude="$top/src/recomp/gen" --exclude="$top/build" \
    --exclude="$top/out" --exclude="$top/logs" \
    --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 -cf - "$top" \
    | xz -T0 -6 > "$stage/setup/source.tar.xz"

cp "$here/launcher/mm3_setup.py" "$here/toolchain.json" "$here/VERSION" "$stage/setup/"
# The icon is the 256 px picture in the project's own mm3.ico.
python3 - "$src/src/mm3.ico" "$stage/setup/icon.png" <<'PY'
import struct, sys
data = open(sys.argv[1], "rb").read()
count = struct.unpack_from("<H", data, 4)[0]
entries = [struct.unpack_from("<BBBBHHII", data, 6 + 16 * i) for i in range(count)]
w, h, _, _, _, _, size, offset = max(entries, key=lambda e: (e[0] or 256) * (e[1] or 256))
picture = data[offset:offset + size]
assert picture[:8] == b"\x89PNG\r\n\x1a\n", "the largest icon is not a PNG"
open(sys.argv[2], "wb").write(picture)
PY
cp "$here/LICENSE" "$here/NOTICE" "$here/LEGAL.md" "$stage/"

# The launcher, with the toolchain the device builds the game with.
data="${XDG_DATA_HOME:-$HOME/.local/share}/MidtownMadness3"
toolchain="$(python3 "$stage/setup/mm3_setup.py" toolchain | sed -n 's/^@done\t//p')"
if [ -z "$toolchain" ]; then
    tail -n 30 "$data/logs/build.log" >&2 || true
    echo "error: the build tools did not install" >&2
    exit 1
fi
cmake="$(ls -d "$data"/toolchain/cmake-*/bin/cmake)"
ninja="$(ls -d "$data"/toolchain/ninja-*/ninja)"
build="$work/build"
"$cmake" -S "$src" -B "$build" -G Ninja -DCMAKE_MAKE_PROGRAM="$ninja" \
    -DCMAKE_TOOLCHAIN_FILE="$toolchain" -DCMAKE_BUILD_TYPE=Release -DMM3_LAUNCHER_ONLY=ON \
    -DCMAKE_EXE_LINKER_FLAGS_RELEASE=-s \
    -DFETCHCONTENT_SOURCE_DIR_RMLUI="$src/third_party/rmlui" \
    -DFETCHCONTENT_SOURCE_DIR_FREETYPE="$src/third_party/freetype" \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON
"$ninja" -C "$build" mm3-launcher
cp "$build/mm3-launcher" "$stage/"

tar -C "$work" --sort=name --owner=0 --group=0 --numeric-owner -czf \
    "$out/MidtownMadness3-Launcher-linux-arm64.tar.gz" MidtownMadness3
tar -tzvf "$out/MidtownMadness3-Launcher-linux-arm64.tar.gz"
rm -rf "$work"
