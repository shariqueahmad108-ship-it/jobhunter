# SPDX-License-Identifier: Apache-2.0
"""Tests for digest.py: HTML rendering and JSON data-file output.

Covers the acceptance criteria from specs/02-functional-spec.md §Stage 7:
- Every digest row contains all required fields (id, score, reason, location,
  salary, sources, date).
- The data file round-trips: reloading JSON loses no scored field.
- render_html() produces valid HTML with the same structural sections as render_markdown().
- render_json_data() includes both new-this-run and previously-seen results.

See: specs/02-functional-spec.md §Stage 7
     specs/03-data-model.md §ScoredResult, §Run report
"""

from __future__ import annotations

import csv as _csv
import io as _io
import json
from datetime import date

from jobhunter.digest import (
    render_csv_data,
    render_html,
    render_json_data,
    render_markdown,
    result_to_dict,
)
from jobhunter.model import (
    JobListing,
    Location,
    RunReport,
    Salary,
    ScoreComponent,
    ScoredResult,
    Seniority,
    Source,
    SourceFailure,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TODAY = date(2026, 7, 23).isoformat()


def _make_listing(
    *,
    idx: int = 1,
    title: str = "Senior Engineer",
    company: str = "Acme Corp",
    city: str | None = None,
    is_remote: bool = True,
    salary_min: float | None = 150_000,
    salary_max: float | None = 200_000,
    currency: str = "AUD",
    period: str = "year",
    posted: str | None = TODAY,
    source_url: str = "https://example.com/job/1",
) -> JobListing:
    from jobhunter.model import derive_content_hash, derive_id

    loc = Location(
        raw=f"Remote ({city})" if city else "Remote",
        city=city,
        is_remote=is_remote,
        country="AU" if is_remote else None,
    )
    listing = JobListing(
        id=derive_id(company, title + str(idx), loc),
        content_hash="",
        title=title,
        company=company,
        location=loc,
        description=f"A {title} role at {company}. Python, AWS skills required.",
        sources=[Source(name="adzuna", url=source_url, source_id=f"az{idx}")],
        first_seen_at=TODAY,
        salary=Salary(min=salary_min, max=salary_max, currency=currency, period=period),
        seniority=Seniority(track="ic", level="senior"),
        employment="full_time",
        posted_at=posted,
    )
    listing.content_hash = derive_content_hash(listing)
    return listing


def _make_result(listing: JobListing, *, rank: int = 1, score: float = 75.0) -> ScoredResult:
    return ScoredResult(
        listing=listing,
        score=score,
        rank=rank,
        summary_reason="5/8 target skills present; salary above floor",
        unknown_flags=[],
        components=[
            ScoreComponent(name="skill_match", sub=0.8, weight=30, reason="5/8 skills"),
            ScoreComponent(name="compensation", sub=0.7, weight=20, reason="above floor"),
        ],
    )


def _make_report(*, new: int = 1, prev: int = 0) -> RunReport:
    return RunReport(
        run_at=TODAY,
        sources_used=["adzuna"],
        sources_failed=[],
        requests_made=10,
        truncated=False,
        ingested_count=20,
        after_dedupe=18,
        dropped_by_location=2,
        dropped_by_seniority=1,
        dropped_by_salary=0,
        dropped_by_employment=0,
        dropped_by_keyword=0,
        dropped_by_age=0,
        dropped_dismissed=0,
        below_threshold=0,
        shown_new=new,
        shown_previous=prev,
        active_weights={"skill_match": 30, "compensation": 20},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _two_results() -> tuple[list[ScoredResult], list[ScoredResult]]:
    """One new result and one previously-seen result."""
    l1 = _make_listing(idx=1, title="Senior Engineer")
    l2 = _make_listing(idx=2, title="Staff Engineer")
    r1 = _make_result(l1, rank=1, score=80.0)
    r2 = _make_result(l2, rank=2, score=65.0)
    return [r1], [r2]


# ---------------------------------------------------------------------------
# render_markdown — baseline sanity (already tested in test_pipeline.py,
# kept here to verify the two-argument interface used by test_digest)
# ---------------------------------------------------------------------------


def test_render_markdown_contains_required_fields() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)
    report = _make_report(new=1)

    md = render_markdown([result], report)

    short_id = listing.id[:8]
    assert short_id in md, "digest must contain the short listing id"
    assert f"{result.score:.0f}/100" in md, "digest must contain the score"
    assert result.summary_reason in md, "digest must contain the reason"
    assert listing.title in md
    assert listing.company in md
    assert "Remote" in md
    assert "AUD" in md
    assert "adzuna" in md


def test_render_markdown_previously_shown_section() -> None:
    new_results, prev_results = _two_results()
    report = _make_report(new=1, prev=1)

    md = render_markdown(new_results, report, previously_seen=prev_results)

    assert "New This Run" in md
    assert "Previously Shown" in md
    assert new_results[0].listing.title in md
    assert prev_results[0].listing.title in md


def test_render_markdown_no_previously_shown_when_disabled() -> None:
    new_results, prev_results = _two_results()
    report = _make_report(new=1)

    md = render_markdown(
        new_results, report, previously_seen=prev_results, show_previously_seen=False
    )

    assert "Previously Shown" not in md
    assert prev_results[0].listing.title not in md


def test_render_markdown_max_shown_cap() -> None:
    listings = [_make_listing(idx=i, title=f"Engineer {i}") for i in range(5)]
    results = [_make_result(lst, rank=i + 1) for i, lst in enumerate(listings)]
    report = _make_report(new=5)

    md = render_markdown(results, report, max_shown=2)

    # Only first 2 shown by title
    assert "Engineer 0" in md
    assert "Engineer 1" in md
    assert "Engineer 4" not in md
    assert "showing 2 of 5" in md


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


def test_render_html_is_valid_html_structure() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)
    report = _make_report(new=1)

    html_out = render_html([result], report)

    assert html_out.startswith("<!DOCTYPE html>")
    assert "<html" in html_out
    assert "</html>" in html_out
    assert "<head>" in html_out
    assert "<body>" in html_out


def test_render_html_contains_required_fields() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)
    report = _make_report(new=1)

    html_out = render_html([result], report)

    short_id = listing.id[:8]
    assert short_id in html_out, "HTML digest must contain the short listing id"
    assert f"{result.score:.0f}/100" in html_out, "HTML digest must contain the score"
    assert result.summary_reason in html_out, "HTML digest must contain the reason"
    assert listing.title in html_out
    assert listing.company in html_out
    assert "Remote" in html_out
    assert "AUD" in html_out
    # Source link rendered as anchor
    assert "adzuna" in html_out
    assert "https://example.com/job/1" in html_out


def test_render_html_run_report_header() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)
    report = _make_report(new=1)
    report.sources_failed = [SourceFailure(name="usajobs", error="timeout")]

    html_out = render_html([result], report)

    assert TODAY in html_out
    assert "adzuna" in html_out
    assert "usajobs" in html_out
    assert "timeout" in html_out
    assert "Ingested" in html_out
    assert "Filter tally" in html_out or "location:" in html_out


def test_render_html_two_sections() -> None:
    new_results, prev_results = _two_results()
    report = _make_report(new=1, prev=1)

    html_out = render_html(new_results, report, previously_seen=prev_results)

    assert "New This Run" in html_out
    assert "Previously Shown" in html_out
    assert new_results[0].listing.title in html_out
    assert prev_results[0].listing.title in html_out


def test_render_html_no_previously_shown_when_disabled() -> None:
    new_results, prev_results = _two_results()
    report = _make_report(new=1)

    html_out = render_html(
        new_results, report, previously_seen=prev_results, show_previously_seen=False
    )

    assert "Previously Shown" not in html_out
    assert prev_results[0].listing.title not in html_out


def test_render_html_max_shown_cap() -> None:
    listings = [_make_listing(idx=i, title=f"Eng {i}") for i in range(5)]
    results = [_make_result(lst, rank=i + 1) for i, lst in enumerate(listings)]
    report = _make_report(new=5)

    html_out = render_html(results, report, max_shown=2)

    assert "Eng 0" in html_out
    assert "Eng 1" in html_out
    assert "Eng 4" not in html_out
    assert "showing 2 of 5" in html_out


def test_render_html_escapes_special_characters() -> None:
    listing = _make_listing(
        idx=99,
        title='Engineer <script>alert("xss")</script>',
        company="A&B Corp",
    )
    result = _make_result(listing)
    report = _make_report(new=1)

    html_out = render_html([result], report)

    assert "<script>" not in html_out, "Raw <script> must be escaped in HTML output"
    assert "&amp;" in html_out or "A&amp;B" in html_out


def test_render_html_active_weights_shown() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)
    report = _make_report(new=1)
    report.active_weights = {"skill_match": 30, "compensation": 20, "recency": 5}

    html_out = render_html([result], report)

    assert "skill_match" in html_out
    assert "30" in html_out


def test_render_html_empty_new_results() -> None:
    report = _make_report(new=0)
    html_out = render_html([], report)

    assert "No new roles matched your criteria" in html_out


def test_render_html_unknown_flags() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)
    result.unknown_flags = ["level unclear", "remote scope unclear"]
    report = _make_report(new=1)

    html_out = render_html([result], report)

    assert "level unclear" in html_out
    assert "remote scope unclear" in html_out


def test_render_html_salary_not_listed() -> None:
    listing = _make_listing(idx=1, salary_min=None, salary_max=None)
    listing.salary = None
    result = _make_result(listing)
    report = _make_report(new=1)

    html_out = render_html([result], report)

    assert "not listed" in html_out


# ---------------------------------------------------------------------------
# result_to_dict / round-trip
# ---------------------------------------------------------------------------


def test_result_to_dict_contains_all_scored_fields() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)

    d = result_to_dict(result)

    # Top-level scored fields
    assert d["id"] == listing.id
    assert d["content_hash"] == listing.content_hash
    assert d["rank"] == result.rank
    assert d["score"] == result.score
    assert d["summary_reason"] == result.summary_reason
    assert d["unknown_flags"] == result.unknown_flags

    # Component breakdown
    assert len(d["components"]) == len(result.components)
    c = d["components"][0]
    assert "name" in c and "sub" in c and "weight" in c and "reason" in c

    # Listing fields
    assert d["title"] == listing.title
    assert d["company"] == listing.company
    assert d["description"] == listing.description
    assert d["posted_at"] == listing.posted_at
    assert d["first_seen_at"] == listing.first_seen_at
    assert d["employment"] == listing.employment

    # Nested structs
    loc = d["location"]
    assert loc["raw"] == listing.location.raw
    assert loc["is_remote"] == listing.location.is_remote

    sal = d["salary"]
    assert sal["min"] == listing.salary.min
    assert sal["max"] == listing.salary.max
    assert sal["currency"] == listing.salary.currency
    assert sal["period"] == listing.salary.period

    sen = d["seniority"]
    assert sen["track"] == listing.seniority.track
    assert sen["level"] == listing.seniority.level

    # Sources
    assert len(d["sources"]) == 1
    assert d["sources"][0]["name"] == "adzuna"
    assert d["sources"][0]["url"] == "https://example.com/job/1"


def test_result_to_dict_null_salary() -> None:
    listing = _make_listing(idx=2)
    listing.salary = None
    result = _make_result(listing)

    d = result_to_dict(result)
    assert d["salary"] is None


def test_result_to_dict_null_seniority() -> None:
    listing = _make_listing(idx=3)
    listing.seniority = None
    result = _make_result(listing)

    d = result_to_dict(result)
    assert d["seniority"] is None


def test_result_to_dict_json_serializable() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)

    d = result_to_dict(result)
    # Must not raise
    serialized = json.dumps(d)
    assert len(serialized) > 0


# ---------------------------------------------------------------------------
# render_json_data
# ---------------------------------------------------------------------------


def test_render_json_data_includes_all_results() -> None:
    new_results, prev_results = _two_results()

    json_str = render_json_data(new_results, prev_results)
    data = json.loads(json_str)

    assert len(data) == 2
    ids = {d["id"] for d in data}
    assert new_results[0].listing.id in ids
    assert prev_results[0].listing.id in ids


def test_render_json_data_no_previously_seen() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)

    json_str = render_json_data([result])
    data = json.loads(json_str)

    assert len(data) == 1
    assert data[0]["id"] == listing.id


def test_render_json_data_round_trip_preserves_score() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing, score=87.5)

    json_str = render_json_data([result])
    data = json.loads(json_str)

    assert data[0]["score"] == 87.5


def test_render_json_data_round_trip_preserves_components() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)

    json_str = render_json_data([result])
    data = json.loads(json_str)

    assert len(data[0]["components"]) == 2
    names = {c["name"] for c in data[0]["components"]}
    assert "skill_match" in names
    assert "compensation" in names


def test_render_json_data_empty() -> None:
    json_str = render_json_data([])
    data = json.loads(json_str)
    assert data == []


def test_digest_header_names_search_mode():
    report = _make_report()
    report.search_mode = "active_unemployed"
    md = render_markdown([], report)
    assert "Mode: active_unemployed" in md


def test_digest_header_mode_none_when_unset():
    md = render_markdown([], _make_report())
    assert "Mode: none" in md


# ---------------------------------------------------------------------------
# render_csv_data
# ---------------------------------------------------------------------------


def test_render_csv_data_header_row() -> None:
    csv_str = render_csv_data([])
    lines = csv_str.strip().splitlines()
    assert len(lines) == 1
    header = lines[0]
    for col in ("id", "rank", "score", "title", "company", "salary_min", "is_remote"):
        assert col in header, f"expected column {col!r} in CSV header"


def test_render_csv_data_single_result() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing, rank=1, score=75.0)

    csv_str = render_csv_data([result])
    lines = csv_str.strip().splitlines()
    assert len(lines) == 2  # header + 1 data row

    data_row = lines[1]
    assert listing.id[:8] in data_row or listing.id in data_row
    assert "75.0" in data_row
    assert "Senior Engineer" in data_row
    assert "Acme Corp" in data_row
    assert "True" in data_row  # is_remote


def test_render_csv_data_includes_previously_seen() -> None:
    new_results, prev_results = _two_results()

    csv_str = render_csv_data(new_results, prev_results)
    lines = csv_str.strip().splitlines()
    assert len(lines) == 3  # header + 2 data rows

    body = "\n".join(lines[1:])
    assert new_results[0].listing.title in body
    assert prev_results[0].listing.title in body


def test_render_csv_data_no_previously_seen() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)

    csv_str = render_csv_data([result])
    lines = csv_str.strip().splitlines()
    assert len(lines) == 2


def test_render_csv_data_empty() -> None:
    csv_str = render_csv_data([])
    lines = csv_str.strip().splitlines()
    assert len(lines) == 1  # header only


def test_render_csv_data_salary_fields() -> None:
    listing = _make_listing(
        idx=1, salary_min=150_000, salary_max=200_000, currency="AUD", period="year"
    )
    result = _make_result(listing)

    csv_str = render_csv_data([result])
    reader = _csv.DictReader(_io.StringIO(csv_str))
    row = next(reader)
    assert float(row["salary_min"]) == 150_000
    assert float(row["salary_max"]) == 200_000
    assert row["salary_currency"] == "AUD"
    assert row["salary_period"] == "year"


def test_render_csv_data_null_salary() -> None:
    listing = _make_listing(idx=2)
    listing.salary = None
    result = _make_result(listing)

    csv_str = render_csv_data([result])
    reader = _csv.DictReader(_io.StringIO(csv_str))
    row = next(reader)
    assert row["salary_min"] == ""
    assert row["salary_max"] == ""
    assert row["salary_currency"] == ""


def test_render_csv_data_unknown_flags_pipe_joined() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)
    result.unknown_flags = ["level unclear", "remote scope unclear"]

    csv_str = render_csv_data([result])
    assert "level unclear|remote scope unclear" in csv_str


def test_render_csv_data_components_json() -> None:
    listing = _make_listing(idx=1)
    result = _make_result(listing)

    csv_str = render_csv_data([result])
    reader = _csv.DictReader(_io.StringIO(csv_str))
    row = next(reader)
    components = json.loads(row["components"])
    assert len(components) == 2
    assert components[0]["name"] == "skill_match"
    assert components[0]["sub"] == 0.8


def test_render_csv_data_sources_pipe_joined() -> None:
    listing = _make_listing(idx=1, source_url="https://example.com/job/1")
    result = _make_result(listing)

    csv_str = render_csv_data([result])
    assert "adzuna" in csv_str
    assert "https://example.com/job/1" in csv_str


# ---------------------------------------------------------------------------
# fx-staleness-warning: digest header note
# ---------------------------------------------------------------------------


def test_render_markdown_no_staleness_note_when_fresh() -> None:
    """No fx staleness note when fx_rates_stale_days is None."""
    report = _make_report()
    assert report.fx_rates_stale_days is None
    md = render_markdown([], report)
    assert "fx_rates.yaml" not in md


def test_render_markdown_staleness_note_when_stale() -> None:
    """Markdown header includes staleness note when fx_rates_stale_days is set."""
    report = _make_report()
    report.fx_rates_stale_days = 95
    md = render_markdown([], report)
    assert "fx_rates.yaml is 95 days old" in md
    assert "consider refreshing exchange rates" in md


def test_render_html_no_staleness_note_when_fresh() -> None:
    """No fx staleness note in HTML when fx_rates_stale_days is None."""
    report = _make_report()
    html_out = render_html([], report)
    assert "fx_rates.yaml" not in html_out


def test_render_html_staleness_note_when_stale() -> None:
    """HTML header includes staleness note when fx_rates_stale_days is set."""
    report = _make_report()
    report.fx_rates_stale_days = 120
    html_out = render_html([], report)
    assert "fx_rates.yaml is 120 days old" in html_out
    assert "consider refreshing exchange rates" in html_out
