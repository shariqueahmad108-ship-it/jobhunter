# SPDX-License-Identifier: Apache-2.0
"""Full pipeline runner: ingest → normalize → dedupe → filter → score → rank.

Wires all six stages together and returns ranked ScoredResult objects.

See: specs/02-functional-spec.md §Stage 1–6
     specs/04-technical-plan.md §Architecture
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from jobhunter import dedupe, normalize
from jobhunter.filter import run as filter_run
from jobhunter.ingest import SourceAdapter
from jobhunter.model import JobListing, RunReport, ScoredResult, SourceFailure
from jobhunter.rank import run as rank_run
from jobhunter.score import run as score_run


def run(
    profile: dict,
    adapters: list[SourceAdapter],
    dismissed_ids: Optional[set[str]] = None,
    today: Optional[date] = None,
) -> tuple[list[ScoredResult], RunReport]:
    """Run the full pipeline: ingest → normalize → dedupe → filter → score → rank.

    Args:
        profile:       Loaded profile dict (validated by load_profile).
        adapters:      Source adapter instances to query.
        dismissed_ids: Set of listing ids permanently dismissed by the user.
        today:         Reference date for freshness and recency scoring.

    Returns:
        (shortlist, report) — shortlist is ranked, at-or-above-threshold ScoredResults.
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

    def _auth_failure(exc: Exception) -> bool:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status in (401, 403)

    for adapter in adapters:
        if done:
            break
        if today is not None and hasattr(adapter, "run_date"):
            adapter.run_date = today.isoformat()
        counts_requests = hasattr(adapter, "requests_made")
        base_count = adapter.requests_made if counts_requests else 0
        total_before_adapter = requests_made
        queries_attempted = 0
        adapter_dead = False
        for keyword in keywords:
            if done or adapter_dead:
                break
            for location in locations:
                if requests_made >= max_requests:
                    truncated = True
                    done = True
                    break
                try:
                    results = adapter.search(keyword, location, max_results)
                    queries_attempted += 1
                    for raw in results:
                        raw_listings.append(adapter.normalize(raw))
                    if adapter.name not in sources_used:
                        sources_used.append(adapter.name)
                except Exception as exc:
                    queries_attempted += 1
                    sources_failed.append(SourceFailure(name=adapter.name, error=str(exc)))
                    if _auth_failure(exc):
                        adapter_dead = True  # credentials are wrong; stop querying it
                        break
                # Prefer the adapter's real HTTP count (pagination makes one query
                # several requests); fall back to one per attempted query.
                if counts_requests:
                    requests_made = total_before_adapter + (adapter.requests_made - base_count)
                else:
                    requests_made = total_before_adapter + queries_attempted

    # Stage 2: post-adapter normalization pass
    normalized = normalize.run(raw_listings)

    # Stage 3: dedupe by identity key and URL
    deduped = dedupe.run(normalized)

    # Stage 4: hard filter
    filter_result = filter_run(deduped, profile, dismissed_ids=dismissed_ids, today=today)
    tally = filter_result.tally

    # Stage 5: score surviving listings
    scored = score_run(
        filter_result.passed,
        profile,
        unknown_flags=filter_result.unknown_flags,
        today=today,
    )

    # Stage 6: rank and apply display threshold
    shortlist, below_threshold = rank_run(scored, profile)

    # Populate active_weights for the run report header
    weights_cfg: dict = profile.get("weights", {})
    active_weights = {k: float(v) for k, v in weights_cfg.items() if float(v) > 0}

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
        dropped_by_required=tally.by_required,
        dropped_by_age=tally.by_age,
        dropped_dismissed=tally.dismissed,
        below_threshold=below_threshold,
        shown_new=len(shortlist),
        active_weights=active_weights,
        search_mode=profile.get("search_mode"),
    )

    return shortlist, report
