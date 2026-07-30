#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Refuse to commit personal criteria or credentials.

.gitignore already covers these; this is the backstop for the day someone
runs `git add -f` or renames a file. Called by pre-commit with staged paths.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BLOCKED = re.compile(r"^(profile\.yaml|profile-.*\.yaml|\.env(\..*)?)$")


def main(argv: list[str]) -> int:
    bad = [a for a in argv if BLOCKED.match(Path(a).name)]
    for path in bad:
        print(f"refusing to commit personal file: {path}", file=sys.stderr)
    if bad:
        print(
            "These hold real job criteria or API keys. Use "
            "specs/profile.example.yaml for anything shareable.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
