# SPDX-License-Identifier: Apache-2.0
"""Phase 1 pipeline runner: ingest → normalize → dedupe → filter.

Wires Stage 1–4 together. Scoring and ranking (Stages 5–6) are Phase 2.

See: specs/02-functional-spec.md §Stage 1–4
     specs/04-technical-plan.md §Architecture
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from jobhunter import dedupe, normalize
from jobhunter.filter import run as filter_run
from jobhunter.ingest import SourceAdapter
from jobhunter.model import JobListing, RunReport, SourceFailure


def run(
    profile: dict,
    adapters: list[SourceAdapter],
    dismissed_ids: Optional[set[str]] = None,
    today: Optional[date] = None,
) -> tuple[list[JobListing], dict[str, list[str]], RunReport]:
    """Run stages 1–4: ingest → normalize → dedupe → filter.

    Args:
        profile:       Loaded profile dict (validated by load_profile).
        adapters:      Source adapter instances to query.
        dismissed_ids: Set of listing ids permanently dismissed by the user.
        today:         Reference date for freshness filter (defaults to date.today()).

    Returns:
        (passed_listings, unknown_flags, report)
    """
    run_at = datetime.now(timezone.utc).isoformat()
    queries = profile["queries"]
    keywords: list[str] = queries["keywords"]
    locations: list[str] = queries["locations"]
    max_results: int = int(queries.get("max_results_per_query", 50))
    max_requests: int = int(queries.get("max_requests_per_run", 100))

    sources_used: list[str] = []
    sources_failed: list[SourceFailure] = []
    raw_listings: list[JobListing] = []
    requests_made = 0
    truncated = False
    done = False

    for adapter in adapters:
        if done:
            break
        for keyword in keywords:
            if done:
                break
            for location in locations:
                if requests_made >= max_requests:
                    truncated = True
                    done = True
                    break
                try:
                    results = adapter.search(keyword, location, max_results)
                    for raw in results:
                        raw_listings.append(adapter.normalize(raw))
                    requests_made += 1
                    if adapter.name not in sources_used:
                        sources_used.append(adapter.name)
                except Exception as exc:
                    sources_failed.append(SourceFailure(name=adapter.name, error=str(exc)))

    # Stage 2: post-adapter normalization pass
    normalized = normalize.run(raw_listings)

    # Stage 3: dedupe by identity key and URL
    deduped = dedupe.run(normalized)

    # Stage 4: hard filter
    filter_result = filter_run(deduped, profile, dismissed_ids=dismissed_ids, today=today)
    tally = filter_result.tally

    report = RunReport(
        run_at=run_at,
        sources_used=sources_used,
        sources_failed=sources_failed,
        requests_made=requests_made,
        truncated=truncated,
        ingested_count=len(raw_listings),
        after_dedupe=len(deduped),
        dropped_by_location=tally.by_location,
        dropped_by_seniority=tally.by_seniority,
        dropped_by_salary=tally.by_salary,
        dropped_by_employment=tally.by_employment,
        dropped_by_keyword=tally.by_keyword,
        dropped_by_age=tally.by_age,
        dropped_dismissed=tally.dismissed,
        shown_new=len(filter_result.passed),
    )

    return filter_result.passed, filter_result.unknown_flags, report
