# SPDX-License-Identifier: Apache-2.0
"""Stage 7 — Present (digest).

Produce a human-readable Markdown digest of ranked, scored listings.

Each row shows: rank, short listing id, title, company, location/remote, salary (or
"not listed"), score, one-line reason, source link(s), posted date, and unknown-field markers.
The header summarizes: new count, below-threshold count, sources, filter tally, active weights.

Seen-state (new vs. previously-shown sections) is added in the seen-state work item.

See: specs/02-functional-spec.md §Stage 7
     specs/03-data-model.md §Run report, §ScoredResult
"""

from __future__ import annotations

from jobhunter.model import RunReport, Salary, ScoredResult


def _short_id(listing_id: str) -> str:
    """Return the first 8 hex characters of a listing id for display."""
    return listing_id[:8]


def _format_salary(salary: Salary | None) -> str:
    if salary is None:
        return "not listed"
    parts: list[str] = []
    if salary.currency:
        parts.append(salary.currency)
    if salary.min is not None and salary.max is not None:
        if salary.min == salary.max:
            parts.append(f"{salary.min:,.0f}")
        else:
            parts.append(f"{salary.min:,.0f}–{salary.max:,.0f}")
    elif salary.max is not None:
        parts.append(f"up to {salary.max:,.0f}")
    elif salary.min is not None:
        parts.append(f"from {salary.min:,.0f}")
    if salary.period:
        parts.append(f"/{salary.period}")
    return " ".join(parts) if parts else (salary.raw or "not listed")


def _format_location(result: ScoredResult) -> str:
    loc = result.listing.location
    if loc.is_remote:
        if loc.city or loc.region or loc.country:
            geo = ", ".join(p for p in [loc.city, loc.region, loc.country] if p)
            return f"Remote ({geo})"
        return "Remote"
    parts = [p for p in [loc.city, loc.region, loc.country] if p]
    return ", ".join(parts) if parts else (loc.raw or "location unclear")


def _render_result_block(result: ScoredResult, lines: list[str]) -> None:
    listing = result.listing
    sid = _short_id(listing.id)
    location_str = _format_location(result)
    salary_str = _format_salary(listing.salary)
    posted = listing.posted_at or listing.first_seen_at or "unknown"

    lines.append(f"### #{result.rank} `{sid}` — {listing.title} at {listing.company}")
    lines.append(
        f"Score: {result.score:.0f}/100 | Location: {location_str} | "
        f"Salary: {salary_str} | Posted: {posted}"
    )
    if result.summary_reason:
        lines.append(f"Reason: {result.summary_reason}")

    source_links = " | ".join(
        f"[{src.name}]({src.url})" if src.url else src.name for src in listing.sources
    )
    lines.append(f"Sources: {source_links}")

    if result.unknown_flags:
        lines.append(f"Flags: {', '.join(result.unknown_flags)}")

    lines.append("")


def render_markdown(
    results: list[ScoredResult],
    report: RunReport,
    previously_seen: list[ScoredResult] | None = None,
    max_shown: int = 25,
    show_previously_seen: bool = True,
) -> str:
    """Render a Markdown digest: run report header + ranked scored listings.

    Args:
        results:            "New this run" listings (ranked, at/above threshold).
        report:             Per-run metadata for the header.
        previously_seen:    Still-passing listings shown in a prior run; rendered
                            in a secondary section when show_previously_seen is True.
        max_shown:          Cap on the "New this run" section (default 25).
        show_previously_seen: Whether to render the "Previously shown" section.

    See: specs/02-functional-spec.md §Stage 7
    """
    lines: list[str] = []

    # --- Header ---
    lines.append("# JobHunter — Run Report")
    lines.append("")
    lines.append(f"Run at: {report.run_at}")

    if report.sources_used:
        lines.append(f"Sources: {', '.join(report.sources_used)}")
    else:
        lines.append("Sources: (none — check adapter configuration)")

    if report.sources_failed:
        failed_str = ", ".join(f"{sf.name} ({sf.error})" for sf in report.sources_failed)
        lines.append(f"Sources failed: {failed_str}")

    lines.append(f"Requests made: {report.requests_made}")
    if report.truncated:
        lines.append("**Run truncated** — max_requests_per_run cap reached; some results omitted.")

    lines.append("")
    total_dropped = (
        report.dropped_by_location
        + report.dropped_by_seniority
        + report.dropped_by_salary
        + report.dropped_by_employment
        + report.dropped_by_keyword
        + report.dropped_by_age
        + report.dropped_dismissed
    )
    prev_count = len(previously_seen) if previously_seen else report.shown_previous
    lines.append(
        f"Ingested: {report.ingested_count} | "
        f"After dedupe: {report.after_dedupe} | "
        f"Dropped by filters: {total_dropped} | "
        f"New: {report.shown_new} | "
        f"Previously shown: {prev_count}"
        + (f" | Below threshold: {report.below_threshold}" if report.below_threshold else "")
    )
    lines.append("")
    lines.append("Filter tally:")
    lines.append(f"- by location:      {report.dropped_by_location}")
    lines.append(f"- by seniority:     {report.dropped_by_seniority}")
    lines.append(f"- by salary:        {report.dropped_by_salary}")
    lines.append(f"- by employment:    {report.dropped_by_employment}")
    lines.append(f"- by keyword:       {report.dropped_by_keyword}")
    lines.append(f"- too old:          {report.dropped_by_age}")
    lines.append(f"- dismissed:        {report.dropped_dismissed}")

    if report.active_weights:
        weights_str = ", ".join(f"{k}: {v:g}" for k, v in report.active_weights.items())
        lines.append(f"\nActive weights: {weights_str}")

    lines.append("")
    lines.append("---")
    lines.append("")

    # --- New this run ---
    capped = results[:max_shown]
    overflow = len(results) - len(capped)
    overflow_note = f" (showing {len(capped)} of {len(results)})" if overflow > 0 else ""
    lines.append(f"## New This Run ({report.shown_new}){overflow_note}")
    lines.append("")

    if not capped:
        lines.append("_No new roles matched your criteria._")
    else:
        for result in capped:
            _render_result_block(result, lines)

    # --- Previously shown ---
    if show_previously_seen and previously_seen:
        lines.append("---")
        lines.append("")
        lines.append(f"## Previously Shown ({len(previously_seen)})")
        lines.append("")
        for result in previously_seen:
            _render_result_block(result, lines)

    return "\n".join(lines)
