#!/usr/bin/env bash
# Builds xboxrecomp's runtime tests with the toolchain the launcher installs on
# a handheld (package-launcher.sh sets it up) and runs them: the device
# register traps, the Win32 handle layer, the thread dump, the D3D11 renderer
# on DXVK, audio decoding and DSP, and the x87 and MMX helpers; d3d8_smoke,
# whose checks are Windows only, builds the whole runtime. The renderer test needs a display
# and a Vulkan driver (Xvfb and lavapipe): with DISPLAY set, a test that skips
# counts as failed.
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
    mmio_decode_posix win32_handles_posix thread_dump_posix d3d11_dxvk_posix d3d8_smoke adpcm_decode
    wma_decoder apu_mixdown dsp cpu_helpers fist fp_precision mmx_rounding nv2a_combiner nv2a_vsh
)
failed=()
summary=()
for test in "${tests[@]}"; do
    echo "::group::$test"
    build="$work/$test"
    # The game's optimisation, with the tests' asserts kept.
    if "$cmake" -S "$src/tools/xboxrecomp/tests/$test" -B "$build" -G Ninja \
            -DCMAKE_MAKE_PROGRAM="$ninja" -DCMAKE_TOOLCHAIN_FILE="$toolchain" \
            -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS_RELEASE=-O2 -DCMAKE_CXX_FLAGS_RELEASE=-O2 \
        && "$ninja" -C "$build" \
        && (cd "$build" && "$ctest" --output-on-failure --timeout 600 | tee "$build/ctest.log") \
        && { [ -z "${DISPLAY:-}" ] || ! grep -q "Skipped\|Not Run" "$build/ctest.log"; }; then
        echo "::endgroup::"
        summary+=("passed  $test: $(grep -c ' Passed ' "$build/ctest.log" || true) tests")
    else
        echo "::endgroup::"
        echo "::error::$test failed"
        failed+=("$test")
        summary+=("FAILED  $test")
    fi
done
printf '%s\n' "${summary[@]}"
rm -rf "$work"
if [ "${#failed[@]}" -gt 0 ]; then
    echo "Failed: ${failed[*]}" >&2
    exit 1
fi
echo "All ${#tests[@]} runtime tests passed"
