# SPDX-License-Identifier: Apache-2.0
"""Seen-state persistence and dismiss workflow.

Manages the run-state file that persists (id, content_hash, last_shown_at)
for every listing shown, plus the set of permanently dismissed ids.

On first run (no state file), returns an empty RunState and writes one after.
On subsequent runs, partitions ranked results into "new" (not previously shown
or materially changed since last shown) vs "previously seen" (same id and
same content_hash as stored).

See: specs/02-functional-spec.md §Stage 7 (Seen-state, Dismissals)
     specs/03-data-model.md §Run state
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import yaml

from jobhunter.model import RunState, ScoredResult, SeenEntry

_SCHEMA_VERSION = 1

# The digest renders ids truncated to 8 characters (digest._short_id), so the
# only id a user can see is a prefix. Accept prefixes here or `dismiss` is a
# no-op for every id a human could actually type.
MIN_ID_PREFIX = 6
_FULL_ID = re.compile(r"^[0-9a-f]{64}$")


class IdError(ValueError):
    """A dismiss/undismiss id that is too short, unknown, or ambiguous."""


def resolve_listing_id(candidate: str, known_ids: Iterable[str]) -> str:
    """Resolve a full id or a unique short prefix to a full listing id.

    Args:
        candidate: What the user typed — a full 64-char hash or the truncated
                   form the digest shows.
        known_ids: The ids the candidate may refer to (seen + dismissed).

    Raises:
        IdError: prefix shorter than MIN_ID_PREFIX, no match, or ambiguous.

    A full 64-hex-char id is accepted even when absent from known_ids: scripts
    read full ids from the .json digest, which may name a listing this state
    file has never recorded.
    """
    cand = candidate.strip().lower()
    known = list(known_ids)

    if cand in known:
        return cand
    if _FULL_ID.match(cand):
        return cand
    if len(cand) < MIN_ID_PREFIX:
        raise IdError(
            f"id {candidate!r} is too short — give at least {MIN_ID_PREFIX} characters "
            "(the digest shows 8)"
        )

    matches = sorted({k for k in known if k.startswith(cand)})
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise IdError(f"id {candidate!r} matches no listing in the state file")
    shown = ", ".join(m[:12] for m in matches)
    raise IdError(f"id {candidate!r} is ambiguous — matches {len(matches)}: {shown}")


def known_ids(state: RunState) -> list[str]:
    """Every id this state file knows: shown before, or already dismissed."""
    return [e.id for e in state.seen] + list(state.dismissed_ids)


def load_state(path: str | Path) -> RunState:
    """Load run state from a YAML file, returning an empty RunState if absent.

    Raises ValueError if the file exists but carries an unrecognised schema_version.
    """
    path = Path(path)
    if not path.exists():
        return RunState(schema_version=_SCHEMA_VERSION)

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"State file {path} is not a YAML mapping")

    version = raw.get("schema_version", _SCHEMA_VERSION)
    if version != _SCHEMA_VERSION:
        raise ValueError(
            f"State file {path}: schema_version {version} not supported "
            f"(expected {_SCHEMA_VERSION}). Delete the file to reset."
        )

    seen = [
        SeenEntry(
            id=entry["id"],
            content_hash=entry["content_hash"],
            last_shown_at=entry["last_shown_at"],
        )
        for entry in raw.get("seen", [])
    ]

    return RunState(
        schema_version=_SCHEMA_VERSION,
        seen=seen,
        dismissed_ids=list(raw.get("dismissed_ids", [])),
        last_run_at=raw.get("last_run_at"),
    )


def save_state(state: RunState, path: str | Path) -> None:
    """Persist run state to a YAML file, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    raw = {
        "schema_version": state.schema_version,
        "last_run_at": state.last_run_at,
        "dismissed_ids": state.dismissed_ids,
        "seen": [
            {
                "id": entry.id,
                "content_hash": entry.content_hash,
                "last_shown_at": entry.last_shown_at,
            }
            for entry in state.seen
        ],
    }
    with open(path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True, sort_keys=True)


def partition_results(
    results: list[ScoredResult],
    state: RunState,
) -> tuple[list[ScoredResult], list[ScoredResult]]:
    """Split ranked results into (new_results, previously_seen_results).

    A result is "new" when its id has never been shown or its content_hash
    differs from the stored one (material change: title, salary, location, or
    description changed). A result is "previously seen" when its id and
    content_hash both match the stored entry.

    Dismissed ids are not present in `results` — they are dropped at Stage 4.
    """
    seen_map: dict[str, str] = {e.id: e.content_hash for e in state.seen}
    new_results: list[ScoredResult] = []
    prev_results: list[ScoredResult] = []

    for result in results:
        lid = result.listing.id
        if lid not in seen_map or seen_map[lid] != result.listing.content_hash:
            new_results.append(result)
        else:
            prev_results.append(result)

    return new_results, prev_results


def update_state(
    state: RunState,
    shown: list[ScoredResult],
    today: str,
) -> RunState:
    """Record every shown result in state and set last_run_at.

    If a listing re-surfaces due to a material change, its stored content_hash
    is updated to the current one so it won't re-surface on the next run unless
    it changes again.
    """
    seen_map: dict[str, SeenEntry] = {e.id: e for e in state.seen}
    for result in shown:
        lid = result.listing.id
        seen_map[lid] = SeenEntry(
            id=lid,
            content_hash=result.listing.content_hash,
            last_shown_at=today,
        )
    state.seen = list(seen_map.values())
    state.last_run_at = today
    return state


def dismiss_ids(state: RunState, ids: list[str]) -> RunState:
    """Append ids to the dismissed set; idempotent."""
    existing = set(state.dismissed_ids)
    for lid in ids:
        existing.add(lid)
    state.dismissed_ids = sorted(existing)
    return state


def undismiss_id(state: RunState, lid: str) -> RunState:
    """Remove an id from the dismissed set."""
    state.dismissed_ids = [x for x in state.dismissed_ids if x != lid]
    return state
