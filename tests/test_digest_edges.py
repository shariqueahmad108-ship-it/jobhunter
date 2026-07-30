# SPDX-License-Identifier: Apache-2.0
"""Digest formatting edge cases.

tests/test_digest.py covers the ordinary Markdown and HTML renders. This module
covers the branches that only appear with awkward data — partial salary ranges,
a location with nothing but a raw string, a source with no URL, an empty run —
because those are exactly the shapes real job boards emit, and the digest is
the one artefact the user actually reads.

See: specs/02-functional-spec.md §Stage 7
     specs/03-data-model.md §Unknown-data policy
"""

from __future__ import annotations

from jobhunter.digest import _format_location, _format_salary, render_html, render_markdown
from jobhunter.model import (
    JobListing,
    Location,
    RunReport,
    Salary,
    ScoredResult,
    Source,
    SourceStat,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(
    *,
    location: Location | None = None,
    salary: Salary | None = None,
    sources: list[Source] | None = None,
    summary_reason: str = "exact match",
    unknown_flags: list[str] | None = None,
) -> ScoredResult:
    listing = JobListing(
        id="f" * 64,
        content_hash="c" * 64,
        title="Staff Engineer",
        company="Acme & Co",  # ampersand: HTML escaping must handle it
        location=location or Location(raw="Remote", is_remote=True),
        description="Build things.",
        sources=sources or [Source(name="stub", url="https://example.com/1", source_id="1")],
        first_seen_at="2026-07-01",
        salary=salary,
        posted_at=None,  # forces the first_seen_at fallback
    )
    return ScoredResult(
        listing=listing,
        score=71.4,
        components=[],
        summary_reason=summary_reason,
        rank=1,
        unknown_flags=unknown_flags or [],
    )


# ---------------------------------------------------------------------------
# _format_salary
# ---------------------------------------------------------------------------


def test_salary_none_reads_as_not_listed():
    assert _format_salary(None) == "not listed"


def test_salary_single_point_is_not_rendered_as_a_range():
    out = _format_salary(Salary(min=200000, max=200000, currency="AUD", period="year"))
    assert out == "AUD 200,000 /year"


def test_salary_range_uses_an_en_dash():
    out = _format_salary(Salary(min=180000, max=200000, currency="AUD"))
    assert out == "AUD 180,000–200,000"


def test_salary_max_only_reads_as_up_to():
    assert _format_salary(Salary(max=150000, currency="USD")) == "USD up to 150,000"


def test_salary_min_only_reads_as_from():
    assert _format_salary(Salary(min=150000, currency="USD")) == "USD from 150,000"


def test_salary_with_no_figures_falls_back_to_the_raw_string():
    """Unknown data is kept and shown, never silently dropped."""
    assert _format_salary(Salary(raw="competitive")) == "competitive"


def test_salary_empty_everywhere_reads_as_not_listed():
    assert _format_salary(Salary()) == "not listed"


# ---------------------------------------------------------------------------
# _format_location
# ---------------------------------------------------------------------------


def test_remote_with_geography_is_qualified():
    loc = Location(raw="Remote, Sydney", city="Sydney", country="AU", is_remote=True)
    assert _format_location(_result(location=loc)) == "Remote (Sydney, AU)"


def test_remote_without_geography_is_bare():
    assert _format_location(_result(location=Location(raw="Remote", is_remote=True))) == "Remote"


def test_onsite_uses_the_parsed_parts():
    loc = Location(raw="Sydney, NSW, AU", city="Sydney", region="NSW", country="AU")
    assert _format_location(_result(location=loc)) == "Sydney, NSW, AU"


def test_onsite_with_nothing_parsed_falls_back_to_raw():
    assert _format_location(_result(location=Location(raw="Somewhere odd"))) == "Somewhere odd"


def test_location_with_no_data_at_all_is_marked_unclear():
    assert _format_location(_result(location=Location(raw=""))) == "location unclear"


# ---------------------------------------------------------------------------
# Renders with awkward reports
# ---------------------------------------------------------------------------


def _sparse_report() -> RunReport:
    """Every optional header branch switched on at once."""
    return RunReport(
        run_at="2026-07-30",
        sources_used=[],  # no source produced anything
        requests_made=0,
        truncated=True,
        below_threshold=7,
        fx_rates_stale_days=120,
        source_stats=[
            SourceStat(name="stub", fetched=3, passed_filter=1, shown=1, sole_source=1),
            SourceStat(name="broken", failed=True, error="401 unauthorized"),
        ],
    )


def test_html_reports_no_sources_loudly():
    html = render_html([], _sparse_report())
    assert "(none — check adapter configuration)" in html


def test_html_includes_truncation_threshold_and_fx_warnings():
    html = render_html([], _sparse_report())
    assert "Run truncated" in html
    assert "Below threshold:</strong> 7" in html
    assert "120 days old" in html


def test_html_includes_per_source_stat_lines():
    html = render_html([], _sparse_report())
    assert "Source stats:" in html
    assert "stub: 3 fetched" in html
    assert "broken: FAILED (401 unauthorized)" in html


def test_html_source_without_a_url_is_plain_text():
    result = _result(sources=[Source(name="nolink", url="", source_id="1")])
    html = render_html([result], _sparse_report())
    assert "nolink" in html
    assert '<a href="">' not in html


def test_html_escapes_company_names():
    html = render_html([_result()], _sparse_report())
    assert "Acme &amp; Co" in html
    assert "Acme & Co" not in html


def test_html_omits_the_reason_block_when_there_is_no_reason():
    html = render_html([_result(summary_reason="")], _sparse_report())
    assert 'class="reason"' not in html


def test_html_shows_unknown_flags():
    html = render_html([_result(unknown_flags=["salary unknown"])], _sparse_report())
    assert "salary unknown" in html


def test_markdown_falls_back_to_first_seen_when_posted_at_is_absent():
    md = render_markdown([_result()], _sparse_report())
    assert "Posted: 2026-07-01" in md


def test_markdown_and_html_agree_on_the_headline_counts():
    """The two renderers read the same report; they must not drift."""
    report = _sparse_report()
    md = render_markdown([_result()], report)
    html = render_html([_result()], report)
    for fragment in ("2026-07-30", "Below threshold", "Run truncated"):
        assert fragment in md
        assert fragment in html
