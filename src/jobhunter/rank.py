# SPDX-License-Identifier: Apache-2.0
"""Stage 6 — Rank & threshold.

Sort scored results best-first. Ties break by recency (newer first), then by listing id
(for full determinism). Apply the display_threshold from the profile: listings below it
are excluded from the shortlist but counted as below_threshold in the run report.

See: specs/02-functional-spec.md §Stage 6
     specs/03-data-model.md §ScoredResult
"""

from __future__ import annotations

from datetime import date

from jobhunter.model import ScoredResult


def _recency_sort_key(result: ScoredResult) -> tuple:
    """Secondary sort key: newer posts sort earlier (ascending negative ordinal)."""
    date_str = result.listing.posted_at or result.listing.first_seen_at
    if date_str:
        try:
            return (0, -date.fromisoformat(str(date_str)[:10]).toordinal())
        except ValueError:
            pass
    return (1, 0)  # unknown date sorts after known dates


def run(
    scored: list[ScoredResult],
    profile: dict,
) -> tuple[list[ScoredResult], int]:
    """Sort scored results and apply the display threshold.

    Assigns a 1-based rank to every result (including below-threshold ones), then
    returns the shortlist (results whose score >= display_threshold) and the count
    of results hidden below it.

    Sort order: score descending → recency descending (newer first) → id ascending.

    Args:
        scored:  Unranked ScoredResult list from Stage 5 (score.run()).
        profile: Loaded profile dict (reads output.display_threshold).

    Returns:
        (shortlist, below_threshold_count)
    """
    output_cfg = profile.get("output", {})
    threshold = float(output_cfg.get("display_threshold", 0))

    sorted_results = sorted(
        scored,
        key=lambda r: (-r.score, *_recency_sort_key(r), r.listing.id),
    )

    for i, result in enumerate(sorted_results):
        result.rank = i + 1

    shortlist = [r for r in sorted_results if r.score >= threshold]
    below_count = len(sorted_results) - len(shortlist)

    return shortlist, below_count
