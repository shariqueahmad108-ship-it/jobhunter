#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail if a tracked Python or shell source file lacks an SPDX licence header.

CONTRIBUTING.md requires new source files to carry an Apache-2.0 SPDX header.
This makes that requirement mechanical instead of a review comment.

Usage:
    python3 tools/check_spdx.py             # check every tracked .py/.sh
    python3 tools/check_spdx.py path [...]  # check specific files (pre-commit)

Markdown is deliberately not checked: the docs in specs/ predate the
convention and adding headers there is a separate, cosmetic change.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MARKER = "SPDX-License-Identifier: Apache-2.0"
SUFFIXES = {".py", ".sh"}
# Lines to skip before the header: shebang, encoding cookie, blank.
SCAN_LINES = 5


def tracked_sources() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py", "*.sh"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(line) for line in out.splitlines() if line]


def has_header(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            for _, line in zip(range(SCAN_LINES), handle):
                if MARKER in line:
                    return True
    except OSError:
        return False
    return False


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] if argv else tracked_sources()
    missing = [
        p for p in paths if p.suffix in SUFFIXES and p.exists() and not has_header(p)
    ]
    for path in missing:
        print(f"missing SPDX header: {path}", file=sys.stderr)
    if missing:
        print(
            f"\n{len(missing)} file(s) need a '# {MARKER}' line in the first "
            f"{SCAN_LINES} lines.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
