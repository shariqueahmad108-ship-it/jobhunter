# SPDX-License-Identifier: Apache-2.0
"""Stage 7 — Present (digest).

Produce a human-readable Markdown or HTML digest of ranked, scored listings, plus a
machine-readable JSON data file.

Each row shows: rank, short listing id, title, company, location/remote, salary (or
"not listed"), score, one-line reason, source link(s), posted date, and unknown-field markers.
The header summarizes: new count, below-threshold count, sources, filter tally, active weights.

Public functions:
  render_markdown()  — Markdown digest (two-section: New / Previously Shown)
  render_html()      — Equivalent HTML digest with inline CSS
  result_to_dict()   — Serialize a ScoredResult to a JSON-safe dict
  render_json_data() — JSON array of all scored survivors (for the data file)

See: specs/02-functional-spec.md §Stage 7
     specs/03-data-model.md §Run report, §ScoredResult
"""

from __future__ import annotations

import html
import json

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


# ---------------------------------------------------------------------------
# HTML digest
# ---------------------------------------------------------------------------

_HTML_CSS = """\
body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}
h1{border-bottom:2px solid #0066cc;padding-bottom:.5rem}
h2{margin-top:2rem;border-bottom:1px solid #ccc;padding-bottom:.25rem}
.meta{background:#f5f5f5;border-radius:6px;padding:1rem;margin:1rem 0;font-size:.9rem}
.tally{list-style:none;margin:.5rem 0;padding:0}
.tally li{display:inline;margin-right:1.5rem}
.card{border:1px solid #ddd;border-radius:6px;padding:1rem;margin:.75rem 0}
.card h3{margin:0 0 .4rem;font-size:1rem}
.card .meta-row{color:#555;font-size:.85rem;margin:.2rem 0}
.score{font-weight:700;color:#0066cc}
.reason{font-style:italic;margin:.3rem 0;font-size:.9rem}
.sources a{color:#0066cc}
.flags{color:#cc6600;font-size:.85rem}
.warn{color:#cc0000;font-weight:600}
hr{border:none;border-top:1px solid #ddd;margin:1.5rem 0}
.prev{opacity:.75}
"""


def _h(text: object) -> str:
    """HTML-escape a value for safe insertion into element content."""
    return html.escape(str(text))


def _html_result_card(result: ScoredResult, extra_class: str = "") -> str:
    listing = result.listing
    sid = _short_id(listing.id)
    location_str = _format_location(result)
    salary_str = _format_salary(listing.salary)
    posted = listing.posted_at or listing.first_seen_at or "unknown"

    source_parts = []
    for src in listing.sources:
        if src.url:
            source_parts.append(f'<a href="{_h(src.url)}">{_h(src.name)}</a>')
        else:
            source_parts.append(_h(src.name))
    sources_html = " &middot; ".join(source_parts)

    flags_html = ""
    if result.unknown_flags:
        flags_html = f'<div class="flags">⚠ {_h(", ".join(result.unknown_flags))}</div>'

    reason_html = ""
    if result.summary_reason:
        reason_html = f'<div class="reason">{_h(result.summary_reason)}</div>'

    class_attr = f"card {extra_class}".strip()
    heading = (
        f"<h3>#{result.rank} <code>{_h(sid)}</code>"
        f" — {_h(listing.title)} at {_h(listing.company)}</h3>"
    )
    return (
        f'<div class="{class_attr}">'
        f"{heading}"
        f'<div class="meta-row">'
        f'<span class="score">Score: {result.score:.0f}/100</span> &nbsp;|&nbsp; '
        f"Location: {_h(location_str)} &nbsp;|&nbsp; "
        f"Salary: {_h(salary_str)} &nbsp;|&nbsp; "
        f"Posted: {_h(posted)}"
        f"</div>"
        f"{reason_html}"
        f'<div class="meta-row sources">Sources: {sources_html}</div>'
        f"{flags_html}"
        f"</div>"
    )


def render_html(
    results: list[ScoredResult],
    report: RunReport,
    previously_seen: list[ScoredResult] | None = None,
    max_shown: int = 25,
    show_previously_seen: bool = True,
) -> str:
    """Render an HTML digest equivalent to render_markdown().

    Args:
        results:            "New this run" listings (ranked, at/above threshold).
        report:             Per-run metadata for the header.
        previously_seen:    Still-passing listings shown in a prior run.
        max_shown:          Cap on the "New this run" section (default 25).
        show_previously_seen: Whether to render the "Previously shown" section.

    See: specs/02-functional-spec.md §Stage 7
    """
    parts: list[str] = []
    parts.append(
        f"<!DOCTYPE html><html lang='en'><head>"
        f"<meta charset='UTF-8'>"
        f"<title>JobHunter — Run Report {_h(report.run_at)}</title>"
        f"<style>{_HTML_CSS}</style>"
        f"</head><body>"
    )

    parts.append("<h1>JobHunter — Run Report</h1>")
    parts.append('<div class="meta">')
    parts.append(f"<strong>Run at:</strong> {_h(report.run_at)}<br>")

    if report.sources_used:
        parts.append(f"<strong>Sources:</strong> {_h(', '.join(report.sources_used))}<br>")
    else:
        parts.append("<strong>Sources:</strong> <em>(none — check adapter configuration)</em><br>")

    if report.sources_failed:
        failed_str = ", ".join(f"{sf.name} ({sf.error})" for sf in report.sources_failed)
        parts.append(f'<strong class="warn">Sources failed:</strong> {_h(failed_str)}<br>')

    parts.append(f"<strong>Requests made:</strong> {report.requests_made}<br>")
    if report.truncated:
        parts.append(
            '<strong class="warn">Run truncated</strong> — '
            "max_requests_per_run cap reached; some results omitted.<br>"
        )

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
    parts.append(
        f"<strong>Ingested:</strong> {report.ingested_count} &nbsp;|&nbsp; "
        f"<strong>After dedupe:</strong> {report.after_dedupe} &nbsp;|&nbsp; "
        f"<strong>Dropped:</strong> {total_dropped} &nbsp;|&nbsp; "
        f"<strong>New:</strong> {report.shown_new} &nbsp;|&nbsp; "
        f"<strong>Previously shown:</strong> {prev_count}"
    )
    if report.below_threshold:
        parts.append(f" &nbsp;|&nbsp; <strong>Below threshold:</strong> {report.below_threshold}")
    parts.append("<br>")

    parts.append("<strong>Filter tally:</strong> ")
    tally_items = [
        f"location: {report.dropped_by_location}",
        f"seniority: {report.dropped_by_seniority}",
        f"salary: {report.dropped_by_salary}",
        f"employment: {report.dropped_by_employment}",
        f"keyword: {report.dropped_by_keyword}",
        f"too old: {report.dropped_by_age}",
        f"dismissed: {report.dropped_dismissed}",
    ]
    parts.append(", ".join(tally_items) + "<br>")

    if report.active_weights:
        weights_str = ", ".join(f"{k}: {v:g}" for k, v in report.active_weights.items())
        parts.append(f"<strong>Active weights:</strong> {_h(weights_str)}<br>")

    parts.append("</div>")  # .meta

    # --- New this run ---
    capped = results[:max_shown]
    overflow = len(results) - len(capped)
    overflow_note = f" (showing {len(capped)} of {len(results)})" if overflow > 0 else ""
    parts.append(f"<h2>New This Run ({report.shown_new}){_h(overflow_note)}</h2>")

    if not capped:
        parts.append("<p><em>No new roles matched your criteria.</em></p>")
    else:
        for result in capped:
            parts.append(_html_result_card(result))

    # --- Previously shown ---
    if show_previously_seen and previously_seen:
        parts.append("<hr>")
        parts.append(f"<h2>Previously Shown ({len(previously_seen)})</h2>")
        for result in previously_seen:
            parts.append(_html_result_card(result, extra_class="prev"))

    parts.append("</body></html>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON data file
# ---------------------------------------------------------------------------


def result_to_dict(result: ScoredResult) -> dict:
    """Serialize a ScoredResult to a JSON-safe dict with all scored fields.

    The output round-trips: every scored field is present and no data is lost.

    See: specs/02-functional-spec.md §Stage 7 (Data file)
    """
    listing = result.listing
    loc = listing.location
    sal = listing.salary
    sen = listing.seniority

    return {
        "id": listing.id,
        "content_hash": listing.content_hash,
        "rank": result.rank,
        "score": result.score,
        "summary_reason": result.summary_reason,
        "unknown_flags": list(result.unknown_flags),
        "components": [
            {
                "name": c.name,
                "sub": c.sub,
                "weight": c.weight,
                "reason": c.reason,
            }
            for c in result.components
        ],
        "title": listing.title,
        "company": listing.company,
        "location": {
            "raw": loc.raw,
            "city": loc.city,
            "region": loc.region,
            "country": loc.country,
            "is_remote": loc.is_remote,
        },
        "salary": (
            {
                "min": sal.min,
                "max": sal.max,
                "currency": sal.currency,
                "period": sal.period,
                "raw": sal.raw,
            }
            if sal is not None
            else None
        ),
        "seniority": ({"track": sen.track, "level": sen.level} if sen is not None else None),
        "employment": listing.employment,
        "description": listing.description,
        "posted_at": listing.posted_at,
        "first_seen_at": listing.first_seen_at,
        "sources": [
            {"name": src.name, "url": src.url, "source_id": src.source_id}
            for src in listing.sources
        ],
    }


def render_json_data(
    results: list[ScoredResult],
    previously_seen: list[ScoredResult] | None = None,
) -> str:
    """Serialize all scored survivors to a JSON array.

    Includes both new-this-run and previously-seen results so the data file
    is a complete record of all live, passing listings.

    See: specs/02-functional-spec.md §Stage 7 (Data file)
    """
    all_results = list(results)
    if previously_seen:
        all_results = all_results + list(previously_seen)
    return json.dumps([result_to_dict(r) for r in all_results], indent=2, ensure_ascii=False)
