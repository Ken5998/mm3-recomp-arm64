#!/usr/bin/env bash
# Builds the ARM64 sysroot the launcher downloads to build the game on a
# handheld: an Ubuntu 24.04 arm64 root with glibc, GCC 14's libstdc++ and the
# SDL2, epoxy, OpenSSL and Vulkan development files, plus DXVK-native (D3D11
# on Vulkan) and vkd3d (D3DCompile) built from source, and the pkgconf the
# build runs. It lets an image-based system such as ArmadaOS build the game
# without installing anything.
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

dxvk_tag=v2.7.1
dxvk_commit=c3dd74be6baec53786d4e064a572185b70347a17
vkd3d_url=http://archive.ubuntu.com/ubuntu/pool/universe/v/vkd3d/vkd3d_2.0+ds.orig.tar.xz
vkd3d_sha256=7a5d631ca7794373a00049b86d55cc4764b9fe185f5fed25e41735fd31dc6419

if [ "$(dpkg --print-architecture)" != arm64 ]; then
    echo "error: run this in an arm64 Ubuntu 24.04 container" >&2
    exit 1
fi

packages=(
    libc6-dev linux-libc-dev libgcc-14-dev libstdc++-14-dev
    libsdl2-dev libepoxy-dev libssl-dev libvulkan-dev
    pkgconf-bin libxml2 zlib1g libzstd1
)
# Only for building DXVK and vkd3d; not copied into the sysroot.
tools=(
    g++-14 meson ninja-build glslang-tools git ca-certificates curl xz-utils python3
    autoconf automake libtool make bison flex mingw-w64-tools spirv-headers
)
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends "${packages[@]}" "${tools[@]}"

work="$(mktemp -d)"
export CC=gcc-14 CXX=g++-14

# DXVK-native: d3d11 and dxgi on Vulkan, presenting into SDL2 windows.
git clone -q --depth 1 --branch "$dxvk_tag" --recurse-submodules --shallow-submodules \
    https://github.com/doitsujin/dxvk "$work/dxvk"
if [ "$(git -C "$work/dxvk" rev-parse HEAD)" != "$dxvk_commit" ]; then
    echo "error: DXVK $dxvk_tag is not commit $dxvk_commit" >&2
    exit 1
fi
meson setup "$work/dxvk-build" "$work/dxvk" --buildtype release --prefix /usr \
    --libdir lib/aarch64-linux-gnu -Denable_d3d8=false -Denable_d3d9=false \
    -Denable_d3d10=false -Dnative_sdl2=enabled -Dnative_sdl3=disabled -Dnative_glfw=disabled
ninja -C "$work/dxvk-build" install

# vkd3d: vkd3d-utils' D3DCompile compiles the renderer's HLSL to SPIR-V.
curl -sSfL -o "$work/vkd3d.tar.xz" "$vkd3d_url"
echo "$vkd3d_sha256  $work/vkd3d.tar.xz" | sha256sum -c -
mkdir "$work/vkd3d"
tar -C "$work/vkd3d" --strip-components 1 -xf "$work/vkd3d.tar.xz"
(
    cd "$work/vkd3d"
    autoreconf -fi
    ./configure --prefix=/usr --libdir=/usr/lib/aarch64-linux-gnu --disable-static \
        --disable-tests --disable-demos WIDL=/usr/bin/x86_64-w64-mingw32-widl
    make -j"$(nproc)"
    make install
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
ln -s usr/lib "$root/lib"
ln -s usr/bin "$root/bin"

# Only what a build links or runs: no drivers, interpreters, compilers' own
# libraries or the build tools' dependencies.
lib="$root/usr/lib/aarch64-linux-gnu"
rm -rf "$lib"/dri "$lib"/gallium-pipe "$lib"/gconv "$lib"/perl* "$lib"/perl-base \
    "$lib"/python3* "$lib"/libpython3* "$lib"/libLLVM* "$lib"/libgallium* "$lib"/libvulkan_* \
    "$lib"/libz3* "$lib"/libglslang* "$lib"/libSPIRV* "$lib"/libSPVRemapper* \
    "$lib"/libMachineIndependent* "$lib"/libGenericCodeGen* "$lib"/libOSDependent* \
    "$lib"/libisl* "$lib"/libmpc* "$lib"/libmpfr* "$lib"/libgmp* "$lib"/libbfd* \
    "$lib"/libopcodes* "$lib"/libctf* "$lib"/libsframe* "$lib"/libgprofng*
rm -rf "$root/usr/include/c++/13" "$root/usr/include/aarch64-linux-gnu/c++/13" \
    "$root/usr/include/glslang" "$root/usr/include/spirv"

# The licences of everything inside, and what it is.
{
    dpkg-query -W -f '${binary:Package}\t${Version}\n' | sort
    printf 'dxvk-native\t%s (%s)\n' "${dxvk_tag#v}" "$dxvk_commit"
    printf 'vkd3d\t2.0 (%s)\n' "$vkd3d_sha256"
} > "$out/$name.packages.txt"
for doc in /usr/share/doc/*/copyright; do
    package="$(basename "$(dirname "$doc")")"
    mkdir -p "$root/usr/share/doc/$package"
    cp -L "$doc" "$root/usr/share/doc/$package/"
done
mkdir -p "$root/usr/share/doc/dxvk-native" "$root/usr/share/doc/vkd3d"
cp "$work/dxvk/LICENSE" "$root/usr/share/doc/dxvk-native/copyright"
cp "$work/vkd3d/COPYING" "$root/usr/share/doc/vkd3d/copyright"
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
for file in libdxvk_d3d11.so.0 libdxvk_dxgi.so.0 libvkd3d-utils.so.1 libvkd3d.so.1 \
        libvkd3d-shader.so.1 libepoxy.so.0 libSDL2.so libcrypto.so libvulkan.so \
        pkgconfig/dxvk-d3d11.pc pkgconfig/libvkd3d-utils.pc pkgconfig/epoxy.pc; do
    if [ ! -e "$lib/$file" ]; then
        echo "error: the sysroot lacks $file" >&2
        exit 1
    fi
done

tar -C "$root" --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 -cf - . \
    | xz -T0 -6 > "$out/$name.tar.xz"
du -sh "$root" "$out/$name.tar.xz"
sha256sum "$out/$name.tar.xz"
