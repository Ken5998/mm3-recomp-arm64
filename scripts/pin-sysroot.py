#!/usr/bin/env python3
"""Pins an ARM64 sysroot made by build-sysroot.sh for the launcher's setup.

  scripts/pin-sysroot.py ARCHIVE                 toolchain.json's sysroot entry,
                                                 for the release sysroot-VERSION
  scripts/pin-sysroot.py ARCHIVE --local FILE    an override (MM3_TOOLCHAIN_OVERRIDE)
                                                 that takes ARCHIVE itself (CI)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "Ken5998/mm3-recomp-arm64")
NAME = re.compile(r"^mm3-arm64-sysroot-(?P<version>[0-9][0-9A-Za-z.]*)\.tar\.xz$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--local", type=Path, metavar="FILE",
                        help="write an override for this archive instead of toolchain.json")
    args = parser.parse_args()
    archive = args.archive.resolve()
    match = NAME.match(archive.name)
    if not match:
        raise SystemExit(f"error: {archive.name} is not a build-sysroot.sh archive")
    version = match["version"]
    url = (archive.as_uri() if args.local else
           f"https://github.com/{REPOSITORY}/releases/download/sysroot-{version}/{archive.name}")
    pin = {
        "label": "the ARM64 sysroot",
        "version": version,
        "url": url,
        "sha256": sha256(archive),
        "size": archive.stat().st_size,
        "strip": 1,
        "check": ["usr/lib/ld-linux-aarch64.so.1"],
    }
    if args.local:
        args.local.write_text(json.dumps({"sysroot": pin}, indent=2) + "\n")
        print(f"Override for {archive.name} in {args.local}")
        return 0
    path = HERE / "toolchain.json"
    toolchain = json.loads(path.read_text())
    toolchain["sysroot"] = pin
    # The sysroot before the runtime library list, beside the other tools.
    libraries = toolchain.pop("runtime_libraries")
    toolchain["runtime_libraries"] = libraries
    path.write_text(json.dumps(toolchain, indent=2) + "\n")
    print(f"Pinned ARM64 sysroot {version} ({pin['sha256'][:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
