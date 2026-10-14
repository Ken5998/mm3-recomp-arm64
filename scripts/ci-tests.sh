#!/usr/bin/env bash
# Builds xboxrecomp's runtime tests with the toolchain the launcher installs on
# a handheld (package-launcher.sh sets it up) and runs them: the device
# register traps, the D3D11 renderer on DXVK, the OpenGL D3D8 path, audio
# decoding and the x87 and MMX helpers. The renderer and D3D8 tests need a
# display and drivers (Xvfb, lavapipe, llvmpipe); without them they skip.
#
#   scripts/ci-tests.sh SOURCE   (SOURCE made by prepare-source.sh)
#
# Runs on ARM64 Linux. No game files are involved.
set -euo pipefail

src="$(cd "$1" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}/MidtownMadness3"
toolchain="$data/toolchain/mm3-arm64.cmake"
cmake="$(ls -d "$data"/toolchain/cmake-*/bin/cmake)"
ctest="$(dirname "$cmake")/ctest"
ninja="$(ls -d "$data"/toolchain/ninja-*/ninja)"
work="$(mktemp -d)"
# DXVK, vkd3d and epoxy, as the game loads them from lib/.
export LD_LIBRARY_PATH="$data/lib"

tests=(
    mmio_decode_posix d3d11_dxvk_posix d3d8_smoke adpcm_decode wma_decoder apu_mixdown
    dsp cpu_helpers fist fp_precision mmx_rounding nv2a_combiner nv2a_vsh
)
failed=()
for test in "${tests[@]}"; do
    echo "::group::$test"
    build="$work/$test"
    # The game's optimisation, with the tests' asserts kept.
    if "$cmake" -S "$src/tools/xboxrecomp/tests/$test" -B "$build" -G Ninja \
            -DCMAKE_MAKE_PROGRAM="$ninja" -DCMAKE_TOOLCHAIN_FILE="$toolchain" \
            -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS_RELEASE=-O2 -DCMAKE_CXX_FLAGS_RELEASE=-O2 \
        && "$ninja" -C "$build" \
        && (cd "$build" && "$ctest" --output-on-failure --timeout 600); then
        echo "::endgroup::"
    else
        echo "::endgroup::"
        echo "::error::$test failed"
        failed+=("$test")
    fi
done
rm -rf "$work"
if [ "${#failed[@]}" -gt 0 ]; then
    echo "Failed: ${failed[*]}" >&2
    exit 1
fi
echo "All ${#tests[@]} runtime tests passed"
