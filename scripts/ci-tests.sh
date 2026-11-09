#!/usr/bin/env bash
# Builds xboxrecomp's runtime tests with the toolchain the launcher installs on
# a handheld (package-launcher.sh sets it up) and runs them: the arm64 device
# register decoder, the Win32 layer, the guest memory and CPU helpers, the
# APU, input and keyboard maps, the NV2A shader, sampler and pacing models,
# the SDL3 GPU presenter, and WMA and media decoding. The renderer tests need
# a display and a Vulkan driver (Xvfb and lavapipe): with DISPLAY set, a test
# that skips counts as failed.
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
# SDL3, FFmpeg and DXC, as the game loads them from lib/: only those, never
# the sysroot's glibc.
mkdir -p "$work/lib"
sysroot_lib="$(ls -d "$data"/toolchain/sysroot-*/usr/lib/aarch64-linux-gnu)"
for name in $(python3 -c 'import json, sys; print(" ".join(json.load(open(sys.argv[1]))["runtime_libraries"]))' \
        "$(dirname "$0")/../toolchain.json"); do
    cp -L "$sysroot_lib/$name" "$work/lib/"
done
export LD_LIBRARY_PATH="$work/lib"

tests=(
    mmio_decode_a64 win32_compat_posix win32_thread_posix platform_memory platform_wait_all
    guest_read stack_ops fist fp_precision mmx_rounding x87_trig
    apu_phys apu_sdl apu_wav wma_decoder
    input_keyboard input_keyboard_map input_map keymap keymap_spec net kernel_crypto
    nv2a_combiner nv2a_hash nv2a_pacing nv2a_sampler_cache nv2a_shader_cache nv2a_vsh
    fb_present flip_hold frame_limit video_pump
)
failed=()
summary=()
for test in "${tests[@]}"; do
    echo "::group::$test"
    build="$work/$test"
    # The game's optimisation, with the tests' asserts kept.
    if "$cmake" -S "$src/tools/xboxrecomp/tests/$test" -B "$build" -G Ninja \
            -DCMAKE_MAKE_PROGRAM="$ninja" -DCMAKE_TOOLCHAIN_FILE="$toolchain" \
            -DXBOXRECOMP_DXC="$data/toolchain/bin/dxc" \
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
