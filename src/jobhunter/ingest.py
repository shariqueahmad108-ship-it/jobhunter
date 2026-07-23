# SPDX-License-Identifier: Apache-2.0
"""Stage 1 — Ingest.

Pull raw listings from pluggable source adapters for the queries in the profile.
Each adapter implements search(query) -> [RawListing].
Source failures are logged and skipped; other sources continue.
The runner enforces max_requests_per_run from the profile as a hard cap.

See: specs/02-functional-spec.md §Stage 1
"""

from __future__ import annotations

from typing import Any, Protocol

RawListing = dict[str, Any]


class SourceAdapter(Protocol):
    """Interface every source adapter must satisfy."""

    name: str

    def search(self, keyword: str, location: str, max_results: int) -> list[RawListing]:
        """Return raw listing dicts or raise on unrecoverable error."""
        ...

    def normalize(self, raw: RawListing) -> "JobListing":  # type: ignore[name-defined]  # noqa: F821
        """Map a raw payload to a canonical JobListing."""
        ...
