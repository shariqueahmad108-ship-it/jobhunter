# SPDX-License-Identifier: Apache-2.0
"""Stage 7 — Present (digest).

Produce a human-readable Markdown/HTML digest and a machine-readable JSON data file.

Two sections:
  1. New this run  — ranked listings not shown in any prior run (or materially changed).
  2. Previously shown (optional) — still-live listings from prior runs.

Each row shows: listing id (short form), title, company, location/remote, salary (or
"not listed"), score, one-line reason, source link(s), posted date, and unknown-field markers.

Phase 1 (this implementation): plain Markdown list without scores or seen-state (Phase 2/3).

See: specs/02-functional-spec.md §Stage 7
     specs/03-data-model.md §Run state, §Run report
"""

from __future__ import annotations

from jobhunter.model import JobListing, RunReport, Salary


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


def _format_location(listing: JobListing) -> str:
    loc = listing.location
    if loc.is_remote:
        if loc.city or loc.region or loc.country:
            geo = ", ".join(p for p in [loc.city, loc.region, loc.country] if p)
            return f"Remote ({geo})"
        return "Remote"
    parts = [p for p in [loc.city, loc.region, loc.country] if p]
    return ", ".join(parts) if parts else (loc.raw or "location unclear")


def render_markdown(
    listings: list[JobListing],
    report: RunReport,
    unknown_flags: dict[str, list[str]] | None = None,
) -> str:
    """Render a Phase 1 plain-Markdown digest: run report tally + surviving roles.

    Scores and seen-state sections are added in Phase 2 and 3 respectively.

    See: specs/02-functional-spec.md §Stage 7
    """
    flags = unknown_flags or {}
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
    lines.append(
        f"Ingested: {report.ingested_count} | "
        f"After dedupe: {report.after_dedupe} | "
        f"Dropped by filters: {total_dropped} | "
        f"Survived: {report.shown_new}"
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

    lines.append("")
    lines.append("> Scoring and ranking not yet implemented (Phase 2). Listings in pipeline order.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Listings ---
    count = len(listings)
    lines.append(f"## Surviving Roles ({count})")
    lines.append("")

    if not listings:
        lines.append("_No roles matched your criteria._")
        return "\n".join(lines)

    for listing in listings:
        sid = _short_id(listing.id)
        location_str = _format_location(listing)
        salary_str = _format_salary(listing.salary)
        posted = listing.posted_at or listing.first_seen_at or "unknown"

        lines.append(f"### `{sid}` — {listing.title} at {listing.company}")
        lines.append(f"Location: {location_str} | Salary: {salary_str} | Posted: {posted}")

        source_links = " | ".join(
            f"[{src.name}]({src.url})" if src.url else src.name for src in listing.sources
        )
        lines.append(f"Sources: {source_links}")

        listing_flags = flags.get(listing.id, [])
        if listing_flags:
            lines.append(f"Flags: {', '.join(listing_flags)}")

        lines.append("")

    return "\n".join(lines)
