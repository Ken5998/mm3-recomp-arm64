#!/usr/bin/env bash
# Builds the ARM64 sysroot the launcher downloads to build the game on a
# handheld: an Ubuntu 24.04 arm64 root with glibc, GCC 14's libstdc++ and the
# OpenSSL and Vulkan development files, plus, built from source, SDL3 (window,
# input, audio and the SDL GPU renderer), SDL_shadercross with its DirectX
# Shader Compiler and SPIRV-Cross (the renderer compiles the game's shaders
# with them while it runs; dxc also builds the renderer's own shaders) and a
# shared FFmpeg with only its own decoders (videos and WMA audio). It lets an
# image-based system such as ArmadaOS build the game without installing
# anything, and without compiling DXC, an LLVM fork, on a handheld.
#
#   scripts/build-sysroot.sh OUTDIR [VERSION]
#
# Runs as root in an ubuntu:24.04 arm64 container. VERSION defaults to
# today's date (YYYY.MM.DD). Writes OUTDIR/mm3-arm64-sysroot-VERSION.tar.xz
# and OUTDIR/mm3-arm64-sysroot-VERSION.packages.txt (the packages and versions
# inside; their sources are on launchpad.net/ubuntu).
set -euo pipefail

out="$(mkdir -p "$1" && cd "$1" && pwd)"
version="${2:-$(date -u +%Y.%m.%d)}"
name="mm3-arm64-sysroot-$version"

# What upstream's CMake fetches (tools/xboxrecomp/cmake/xbox_sdl3.cmake).
sdl_tag=release-3.4.18
shadercross_commit=1ff05bec573988a98ef9e0260b4da44f512b8367
ffmpeg_tag=n7.1.2
ffmpeg_commit=f893221c8d89cb798b829bebe71d55e1a3f242fd

if [ "$(dpkg --print-architecture)" != arm64 ]; then
    echo "error: run this in an arm64 Ubuntu 24.04 container" >&2
    exit 1
fi

packages=(
    libc6-dev linux-libc-dev libgcc-14-dev libstdc++-14-dev
    libssl-dev libvulkan-dev pkgconf-bin zlib1g libzstd1
)
# Only for building SDL3, SDL_shadercross and FFmpeg. SDL3 opens the display,
# audio and input libraries at run time, so these headers are all it needs.
tools=(
    gcc-14 g++-14 cmake ninja-build git ca-certificates xz-utils python3 make nasm
    libx11-dev libxext-dev libxrandr-dev libxcursor-dev libxi-dev libxfixes-dev libxss-dev
    libxtst-dev libxkbcommon-dev libwayland-dev wayland-protocols libdecor-0-dev libegl-dev
    libgles-dev libdrm-dev libgbm-dev libpipewire-0.3-dev libpulse-dev libasound2-dev
    libudev-dev libdbus-1-dev
)
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends "${packages[@]}" "${tools[@]}"

work="$(mktemp -d)"
stage="$work/stage"   # what the builds below install; copied into the sysroot
export CC=gcc-14 CXX=g++-14
jobs="$(nproc)"

# SDL3, shared: the game and its launcher load it from beside the executable.
git clone -q --depth 1 --branch "$sdl_tag" https://github.com/libsdl-org/SDL "$work/sdl"
cmake -S "$work/sdl" -B "$work/sdl-build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib/aarch64-linux-gnu \
    -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TEST_LIBRARY=OFF -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF
cmake --build "$work/sdl-build"
DESTDIR="$stage" cmake --install "$work/sdl-build"
DESTDIR=/ cmake --install "$work/sdl-build"   # for SDL_shadercross below

# SDL_shadercross with its vendored DXC, SPIRV-Cross and SPIRV-Tools; and the
# dxc command line from the same DXC.
git init -q "$work/shadercross"
git -C "$work/shadercross" remote add origin https://github.com/libsdl-org/SDL_shadercross
git -C "$work/shadercross" fetch -q --depth 1 origin "$shadercross_commit"
git -C "$work/shadercross" -c advice.detachedHead=false checkout -q FETCH_HEAD
git -C "$work/shadercross" submodule update -q --init --recursive --depth 1
cmake -S "$work/shadercross" -B "$work/shadercross-build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib/aarch64-linux-gnu \
    -DSDLSHADERCROSS_VENDORED=ON -DSDLSHADERCROSS_DXC=ON -DENABLE_SPIRV_CODEGEN=ON \
    -DSDLSHADERCROSS_SHARED=OFF -DSDLSHADERCROSS_STATIC=ON -DSDLSHADERCROSS_SPIRVCROSS_SHARED=OFF \
    -DSDLSHADERCROSS_CLI=OFF -DSDLSHADERCROSS_INSTALL=ON -DSDLSHADERCROSS_TESTS=OFF
cmake --build "$work/shadercross-build" -j "$jobs"
cmake --build "$work/shadercross-build" -j "$jobs" --target dxc
DESTDIR="$stage" cmake --install "$work/shadercross-build"
dxc_bin="$(find "$work/shadercross-build" -type f -name dxc -perm -u+x | head -n 1)"
install -Dm755 "$dxc_bin" "$stage/usr/bin/dxc"

# FFmpeg, shared, LGPL, only its built-in components: no external codec
# libraries to carry, and nothing Fedora's own FFmpeg could clash with.
git clone -q --depth 1 --branch "$ffmpeg_tag" https://github.com/FFmpeg/FFmpeg "$work/ffmpeg"
if [ "$(git -C "$work/ffmpeg" rev-parse HEAD)" != "$ffmpeg_commit" ]; then
    echo "error: FFmpeg $ffmpeg_tag is not commit $ffmpeg_commit" >&2
    exit 1
fi
(
    cd "$work/ffmpeg"
    ./configure --prefix=/usr --libdir=/usr/lib/aarch64-linux-gnu --cc=gcc-14 --cxx=g++-14 \
        --enable-shared --disable-static --disable-programs --disable-doc --disable-network \
        --disable-autodetect --disable-avdevice --disable-avfilter --enable-pic
    make -j"$jobs"
    make install DESTDIR="$stage"
)

root="$work/sysroot"
mkdir -p "$root/usr/lib" "$root/usr/bin" "$root/usr/share/doc"
cp -a /usr/include "$root/usr/"
cp -a /usr/lib/aarch64-linux-gnu "$root/usr/lib/"
mkdir -p "$root/usr/lib/gcc/aarch64-linux-gnu"
cp -a /usr/lib/gcc/aarch64-linux-gnu/14 "$root/usr/lib/gcc/aarch64-linux-gnu/"
cp -a /usr/lib/ld-linux-aarch64.so.1 "$root/usr/lib/"
cp -a /usr/share/pkgconfig "$root/usr/share/"
cp -a /usr/bin/pkgconf "$root/usr/bin/"
cp -a "$stage/usr/." "$root/usr/"
ln -s usr/lib "$root/lib"
ln -s usr/bin "$root/bin"

# Only what a build links or runs: no drivers, interpreters, compilers' own
# libraries or the build tools' dependencies.
lib="$root/usr/lib/aarch64-linux-gnu"
rm -rf "$lib"/dri "$lib"/gallium-pipe "$lib"/gconv "$lib"/perl* "$lib"/perl-base \
    "$lib"/python3* "$lib"/libpython3* "$lib"/libLLVM* "$lib"/libgallium* "$lib"/libvulkan_* \
    "$lib"/libz3* "$lib"/libisl* "$lib"/libmpc* "$lib"/libmpfr* "$lib"/libgmp* "$lib"/libbfd* \
    "$lib"/libopcodes* "$lib"/libctf* "$lib"/libsframe* "$lib"/libgprofng* \
    "$lib"/libjsoncpp* "$lib"/libcurl* "$lib"/libarchive*
rm -rf "$root/usr/include/c++/13" "$root/usr/include/aarch64-linux-gnu/c++/13"

# The licences of everything inside, and what it is.
{
    dpkg-query -W -f '${binary:Package}\t${Version}\n' | sort
    printf 'SDL3\t%s\n' "${sdl_tag#release-}"
    printf 'SDL_shadercross\t%s (with its DirectXShaderCompiler, SPIRV-Cross, SPIRV-Tools submodules)\n' "$shadercross_commit"
    printf 'ffmpeg\t%s (%s)\n' "${ffmpeg_tag#n}" "$ffmpeg_commit"
} > "$out/$name.packages.txt"
for doc in /usr/share/doc/*/copyright; do
    package="$(basename "$(dirname "$doc")")"
    mkdir -p "$root/usr/share/doc/$package"
    cp -L "$doc" "$root/usr/share/doc/$package/"
done
for project in sdl:LICENSE.txt shadercross:LICENSE.txt ffmpeg:COPYING.LGPLv2.1 \
        shadercross/external/DirectXShaderCompiler:LICENSE.TXT \
        shadercross/external/SPIRV-Cross:LICENSE shadercross/external/SPIRV-Tools:LICENSE; do
    folder="${project%%:*}"
    mkdir -p "$root/usr/share/doc/$(basename "$folder")"
    cp "$work/$folder/${project#*:}" "$root/usr/share/doc/$(basename "$folder")/copyright"
done
cp "$out/$name.packages.txt" "$root/usr/share/doc/PACKAGES.txt"

# Absolute links point at this container's /; point them inside the sysroot.
python3 - "$root" <<'EOF'
import os, sys
root = sys.argv[1]
for directory, folders, files in os.walk(root):
    for name in folders + files:
        path = os.path.join(directory, name)
        if os.path.islink(path) and os.readlink(path).startswith("/"):
            target = os.path.relpath(root + os.readlink(path), directory)
            os.unlink(path)
            os.symlink(target, path)
EOF

# What the game needs at run time and the build must find.
for file in libSDL3.so.0 libdxcompiler.so libavcodec.so libavformat.so libavutil.so \
        libswscale.so libswresample.so libcrypto.so libvulkan.so \
        cmake/SDL3/SDL3Config.cmake cmake/SDL3_shadercross/SDL3_shadercrossConfig.cmake \
        pkgconfig/libavcodec.pc; do
    if [ ! -e "$lib/$file" ]; then
        echo "error: the sysroot lacks $file" >&2
        find "$lib" -maxdepth 2 -name 'libdxc*' -o -maxdepth 2 -name 'SDL3*' >&2 || true
        exit 1
    fi
done
LD_LIBRARY_PATH="$lib" "$root/usr/bin/dxc" --version

tar -C "$root" --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 -cf - . \
    | xz -T0 -6 > "$out/$name.tar.xz"
du -sh "$root" "$out/$name.tar.xz"
sha256sum "$out/$name.tar.xz"
