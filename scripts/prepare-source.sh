#!/usr/bin/env bash
# Checks out mm3-recomp at the commit upstream.json pins, with its xboxrecomp
# submodule, and applies this repository's patches to both.
#
#   scripts/prepare-source.sh DEST
#
# DEST must not exist. Needs git and python3. The xboxrecomp checkout also
# holds the generator commit generate_mm3.py extracts its translator from.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
dest="$1"
if [ -e "$dest" ]; then
    echo "error: $dest already exists" >&2
    exit 1
fi

read_pin() {
    python3 -c 'import json, sys
pin = json.load(open(sys.argv[1]))
for key in sys.argv[2].split("."):
    pin = pin[key]
print(pin)' "$here/upstream.json" "$1"
}
repository="$(read_pin repository)"
commit="$(read_pin commit)"
xbox_repository="$(read_pin xboxrecomp.repository)"
xbox_commit="$(read_pin xboxrecomp.commit)"
generator_commit="$(read_pin xboxrecomp.generator_commit)"

retry() {
    local attempt
    for attempt in 1 2 3 4; do
        "$@" && return 0
        sleep $((attempt * 4))
    done
    "$@"
}

# The patches are committed as their authors wrote them; git am needs an
# identity for the committer only.
identity=(-c user.name=mm3-recomp-arm64 -c user.email=mm3-recomp-arm64@users.noreply.github.com)

git init -q "$dest"
git -C "$dest" remote add origin "$repository"
retry git -C "$dest" fetch -q --depth 1 origin "$commit"
git -C "$dest" -c advice.detachedHead=false checkout -q FETCH_HEAD

pinned="$(git -C "$dest" ls-tree "$commit" tools/xboxrecomp | awk '{print $3}')"
if [ "$pinned" != "$xbox_commit" ]; then
    echo "error: mm3-recomp $commit pins xboxrecomp $pinned, upstream.json says $xbox_commit" >&2
    exit 1
fi
xbox="$dest/tools/xboxrecomp"
rm -rf "$xbox"
git init -q "$xbox"
git -C "$xbox" remote add origin "$xbox_repository"
retry git -C "$xbox" fetch -q --depth 1 origin "$xbox_commit" "$generator_commit"
git -C "$xbox" -c advice.detachedHead=false checkout -q "$xbox_commit"

# Some xboxrecomp sources have CRLF line ends, which their patches carry.
git -C "$xbox" "${identity[@]}" am -q --keep-cr --3way "$here"/patches/xboxrecomp/*.patch
git -C "$dest" "${identity[@]}" am -q --3way "$here"/patches/mm3-recomp/*.patch

echo "mm3-recomp $(git -C "$dest" rev-parse --short HEAD) on ${commit:0:12}," \
     "xboxrecomp $(git -C "$xbox" rev-parse --short HEAD) on ${xbox_commit:0:12}"
