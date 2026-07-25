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
from jobhunter.model import JobListing, RunReport, ScoredResult, SourceFailure, SourceStat
from jobhunter.rank import run as rank_run
from jobhunter.score import run as score_run


def run_with_snapshot(
    profile: dict,
    adapters: list[SourceAdapter],
    dismissed_ids: Optional[set[str]] = None,
    today: Optional[date] = None,
) -> tuple[list[ScoredResult], RunReport, list[JobListing]]:
    """Like run() but also returns the pre-filter listing set for snapshot writing.

    Returns:
        (shortlist, report, pre_filter) — pre_filter is post-dedupe, pre-Stage-4.
    """
    shortlist, report, pre_filter = _run_internal(profile, adapters, dismissed_ids, today)
    return shortlist, report, pre_filter


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
    shortlist, report, _ = _run_internal(profile, adapters, dismissed_ids, today)
    return shortlist, report


def _run_internal(
    profile: dict,
    adapters: list[SourceAdapter],
    dismissed_ids: Optional[set[str]] = None,
    today: Optional[date] = None,
) -> tuple[list[ScoredResult], RunReport, list[JobListing]]:
    """Core pipeline implementation; returns (shortlist, report, pre_filter)."""
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

    # Per-source tracking for source contribution stats.
    fetched_by_source: dict[str, int] = {}
    requests_by_source: dict[str, int] = {}
    failed_by_source: dict[str, Optional[str]] = {}  # name -> error string

    def _auth_failure(exc: Exception) -> bool:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status in (401, 403)

    for adapter in adapters:
        if done:
            break
        if today is not None and hasattr(adapter, "run_date"):
            adapter.run_date = today.isoformat()

        if getattr(adapter, "query_independent", False):
            # ATS-style adapters: fetch all companies once, not per keyword×location.
            # Each company's failures are caught inside search() and stored on the
            # adapter; we surface them here so they appear in the run report.
            if requests_made >= max_requests:
                truncated = True
                done = True
                break
            adapter_fetched = 0
            try:
                results = adapter.search("", "", max_results)
                for raw in results:
                    raw_listings.append(adapter.normalize(raw))
                adapter_fetched = len(results)
                if adapter.name not in sources_used:
                    sources_used.append(adapter.name)
            except Exception as exc:
                sources_failed.append(SourceFailure(name=adapter.name, error=str(exc)))
                failed_by_source[adapter.name] = str(exc)
            if hasattr(adapter, "company_failures"):
                for msg in adapter.company_failures:
                    sources_failed.append(SourceFailure(name=adapter.name, error=msg))
            requests_made += 1
            fetched_by_source[adapter.name] = (
                fetched_by_source.get(adapter.name, 0) + adapter_fetched
            )
            requests_by_source[adapter.name] = requests_by_source.get(adapter.name, 0) + 1
            continue

        counts_requests = hasattr(adapter, "requests_made")
        base_count = adapter.requests_made if counts_requests else 0
        total_before_adapter = requests_made
        queries_attempted = 0
        adapter_dead = False
        adapter_fetched = 0
        adapter_error: Optional[str] = None
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
                    adapter_fetched += len(results)
                    if adapter.name not in sources_used:
                        sources_used.append(adapter.name)
                except Exception as exc:
                    queries_attempted += 1
                    adapter_error = str(exc)
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

        fetched_by_source[adapter.name] = fetched_by_source.get(adapter.name, 0) + adapter_fetched
        requests_by_source[adapter.name] = requests_by_source.get(adapter.name, 0) + (
            requests_made - total_before_adapter
        )
        if adapter_error is not None and adapter.name not in failed_by_source:
            failed_by_source[adapter.name] = adapter_error

    # Stage 2: post-adapter normalization pass
    normalized = normalize.run(raw_listings)

    # Stage 3: dedupe by identity key and URL
    deduped = dedupe.run(normalized)

    # Compute contributed and sole_source from the merged sources[] on deduped listings.
    contributed_by_source: dict[str, int] = {}
    sole_by_source: dict[str, int] = {}
    for listing in deduped:
        source_names = {s.name for s in listing.sources}
        for name in source_names:
            contributed_by_source[name] = contributed_by_source.get(name, 0) + 1
        if len(source_names) == 1:
            name = next(iter(source_names))
            sole_by_source[name] = sole_by_source.get(name, 0) + 1

    # Stage 4: hard filter
    filter_result = filter_run(deduped, profile, dismissed_ids=dismissed_ids, today=today)
    tally = filter_result.tally

    # Compute passed_filter from Stage 4 survivors.
    passed_filter_by_source: dict[str, int] = {}
    for listing in filter_result.passed:
        for s in listing.sources:
            passed_filter_by_source[s.name] = passed_filter_by_source.get(s.name, 0) + 1

    # Stage 5: score surviving listings
    scored = score_run(
        filter_result.passed,
        profile,
        unknown_flags=filter_result.unknown_flags,
        today=today,
    )

    # Stage 6: rank and apply display threshold
    shortlist, below_threshold = rank_run(scored, profile)

    # Build per-source stats (shown=0 — updated by cli after rendering).
    all_source_names = sorted(
        set(fetched_by_source) | set(failed_by_source) | set(contributed_by_source)
    )
    source_stats: list[SourceStat] = []
    for name in all_source_names:
        is_failed = name in failed_by_source
        source_stats.append(
            SourceStat(
                name=name,
                fetched=fetched_by_source.get(name, 0),
                contributed=contributed_by_source.get(name, 0),
                sole_source=sole_by_source.get(name, 0),
                passed_filter=passed_filter_by_source.get(name, 0),
                shown=0,  # filled in by cli after partition + render
                dismissed=0,
                requests=requests_by_source.get(name, 0),
                failed=is_failed,
                error=failed_by_source.get(name) if is_failed else None,
            )
        )

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
        source_stats=source_stats,
    )

    return shortlist, report, deduped
