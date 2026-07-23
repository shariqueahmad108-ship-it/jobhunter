# SPDX-License-Identifier: Apache-2.0
"""Tests for Stage 2 — Normalize.

Covers strip_html, parse_salary, parse_location, infer_seniority (re-export),
and the run() pipeline stage.

Validation: python -m pytest tests/test_normalize.py -q
"""

from __future__ import annotations

from datetime import date

from jobhunter.model import (
    JobListing,
    Location,
    Source,
    derive_content_hash,
    derive_id,
)
from jobhunter.normalize import (
    IC_LEVELS,
    MANAGEMENT_LEVELS,
    infer_seniority,
    parse_location,
    parse_salary,
    run,
    strip_html,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date.today().isoformat()


def _make_listing(
    title: str = "Engineer",
    company: str = "Acme",
    description: str = "A great role.",
    location_raw: str = "Remote",
    is_remote: bool = True,
    employment: str | None = None,
    posted_at: str | None = None,
) -> JobListing:
    loc = Location(raw=location_raw, is_remote=is_remote)
    src = Source(name="test", url="http://example.com/1", source_id="1")
    listing = JobListing(
        id="",
        content_hash="",
        title=title,
        company=company,
        location=loc,
        description=description,
        sources=[src],
        first_seen_at=_TODAY,
        employment=employment,
        posted_at=posted_at,
    )
    listing.id = derive_id(company, title, loc)
    listing.content_hash = derive_content_hash(listing)
    return listing


# ---------------------------------------------------------------------------
# strip_html
# ---------------------------------------------------------------------------


def test_strip_html_removes_tags():
    assert strip_html("<b>hello</b>") == "hello"


def test_strip_html_removes_nested_tags():
    assert strip_html("<div><p>hello <strong>world</strong></p></div>") == "hello world"


def test_strip_html_unescapes_entities():
    assert strip_html("Foo &amp; Bar") == "Foo & Bar"
    assert strip_html("&lt;tag&gt;") == "<tag>"
    assert strip_html("&#39;quoted&#39;") == "'quoted'"


def test_strip_html_collapses_whitespace():
    result = strip_html("<p>  lots   of   space  </p>")
    assert result == "lots of space"


def test_strip_html_no_html_unchanged():
    assert strip_html("plain text") == "plain text"


def test_strip_html_empty_string():
    assert strip_html("") == ""


def test_strip_html_mixed():
    result = strip_html("<strong>Bold</strong> &amp; <em>italic</em>")
    assert result == "Bold & italic"


def test_strip_html_replaces_br_with_space():
    result = strip_html("Line one<br>Line two")
    assert result == "Line one Line two"


# ---------------------------------------------------------------------------
# parse_salary — None / empty
# ---------------------------------------------------------------------------


def test_parse_salary_none_input():
    assert parse_salary(None) is None


def test_parse_salary_empty_string():
    assert parse_salary("") is None


def test_parse_salary_whitespace_only():
    assert parse_salary("   ") is None


# ---------------------------------------------------------------------------
# parse_salary — currency detection
# ---------------------------------------------------------------------------


def test_parse_salary_bare_dollar_uses_default_currency():
    """A bare $ carries no country info — resolve to default_currency, not USD."""
    s = parse_salary("$120k-$150k", default_currency="AUD")
    assert s.currency == "AUD"
    assert s.min == 120_000 and s.max == 150_000


def test_parse_salary_bare_dollar_no_default_is_unknown():
    s = parse_salary("$120k-$150k")
    assert s.currency is None
    assert s.min == 120_000 and s.max == 150_000


def test_parse_salary_explicit_code_overrides_default():
    s = parse_salary("USD 150,000", default_currency="AUD")
    assert s.currency == "USD"


def test_parse_salary_percent_tokens_ignored():
    """'$120,000 + 10% super' must not read 10 as the max."""
    s = parse_salary("circa $120,000 + 10% super", default_currency="AUD")
    assert s.min == 120_000 and s.max == 120_000


def test_parse_salary_inverted_range_swapped():
    s = parse_salary("150k - 120k AUD")
    assert s.min == 120_000 and s.max == 150_000

def test_parse_salary_aud_prefix():
    s = parse_salary("AUD 120,000")
    assert s is not None
    assert s.currency == "AUD"


def test_parse_salary_au_dollar_sign():
    s = parse_salary("AU $80,000")
    assert s is not None
    assert s.currency == "AUD"


def test_parse_salary_gbp_symbol():
    s = parse_salary("£60,000")
    assert s is not None
    assert s.currency == "GBP"


def test_parse_salary_eur_symbol():
    s = parse_salary("€50,000")
    assert s is not None
    assert s.currency == "EUR"


def test_parse_salary_cad_prefix():
    s = parse_salary("CAD 100,000")
    assert s is not None
    assert s.currency == "CAD"


def test_parse_salary_default_currency_applied_when_no_symbol():
    s = parse_salary("120,000", default_currency="AUD")
    assert s is not None
    assert s.currency == "AUD"


def test_parse_salary_explicit_symbol_overrides_default():
    s = parse_salary("£80,000", default_currency="AUD")
    assert s is not None
    assert s.currency == "GBP"


# ---------------------------------------------------------------------------
# parse_salary — period detection
# ---------------------------------------------------------------------------


def test_parse_salary_per_day():
    s = parse_salary("$900/day")
    assert s is not None
    assert s.period == "day"
    assert s.min == 900.0
    assert s.max == 900.0


def test_parse_salary_per_hour():
    s = parse_salary("$75/hour")
    assert s is not None
    assert s.period == "hour"


def test_parse_salary_per_hr():
    s = parse_salary("$75/hr")
    assert s is not None
    assert s.period == "hour"


def test_parse_salary_per_month():
    s = parse_salary("€4,000/month")
    assert s is not None
    assert s.period == "month"
    assert s.currency == "EUR"


def test_parse_salary_per_annum():
    s = parse_salary("£60,000 per annum")
    assert s is not None
    assert s.period == "year"


def test_parse_salary_per_year():
    s = parse_salary("$120,000 per year")
    assert s is not None
    assert s.period == "year"


def test_parse_salary_annually():
    s = parse_salary("AUD 130,000 annually")
    assert s is not None
    assert s.period == "year"
    assert s.currency == "AUD"


def test_parse_salary_no_period_is_null():
    s = parse_salary("$120,000")
    assert s is not None
    assert s.period is None


# ---------------------------------------------------------------------------
# parse_salary — amount parsing
# ---------------------------------------------------------------------------


def test_parse_salary_single_amount():
    s = parse_salary("120000")
    assert s is not None
    assert s.min == 120000.0
    assert s.max == 120000.0


def test_parse_salary_range_with_en_dash():
    s = parse_salary("$120k–$150k")  # en dash
    assert s is not None
    assert s.min == 120000.0
    assert s.max == 150000.0


def test_parse_salary_range_with_hyphen():
    s = parse_salary("$120k-$150k")
    assert s is not None
    assert s.min == 120000.0
    assert s.max == 150000.0


def test_parse_salary_range_with_to():
    s = parse_salary("$120k to $150k")
    assert s is not None
    assert s.min == 120000.0
    assert s.max == 150000.0


def test_parse_salary_k_multiplier():
    s = parse_salary("$80k")
    assert s is not None
    assert s.min == 80000.0
    assert s.max == 80000.0


def test_parse_salary_K_uppercase_multiplier():
    s = parse_salary("$80K")
    assert s is not None
    assert s.min == 80000.0


def test_parse_salary_m_multiplier():
    s = parse_salary("$1.5m")
    assert s is not None
    assert s.min == 1_500_000.0
    assert s.max == 1_500_000.0


def test_parse_salary_commas_in_amount():
    s = parse_salary("$120,000")
    assert s is not None
    assert s.min == 120000.0


def test_parse_salary_raw_always_preserved():
    text = "$120k–$150k"
    s = parse_salary(text)
    assert s is not None
    assert s.raw == text


# ---------------------------------------------------------------------------
# parse_salary — spec acceptance criteria
# ---------------------------------------------------------------------------


def test_parse_salary_day_rate_not_stored_as_annual():
    s = parse_salary("$900/day")
    assert s is not None
    assert s.period == "day"
    assert s.min == 900.0
    assert s.max == 900.0


def test_parse_salary_hourly_rate_has_hour_period():
    s = parse_salary("$75/hour")
    assert s is not None
    assert s.period == "hour"


def test_parse_salary_full_range_with_currency_and_period():
    s = parse_salary("£60,000 - £80,000 per annum")
    assert s is not None
    assert s.min == 60000.0
    assert s.max == 80000.0
    assert s.currency == "GBP"
    assert s.period == "year"


def test_parse_salary_au_dollar_with_range_and_period():
    s = parse_salary("AU $80,000 - $120,000 per year")
    assert s is not None
    assert s.min == 80000.0
    assert s.max == 120000.0
    assert s.currency == "AUD"
    assert s.period == "year"


def test_parse_salary_eur_monthly():
    s = parse_salary("€50k/month")
    assert s is not None
    assert s.min == 50000.0
    assert s.currency == "EUR"
    assert s.period == "month"


# ---------------------------------------------------------------------------
# parse_salary — unparseable → Salary(raw=...) with null numerics
# ---------------------------------------------------------------------------


def test_parse_salary_unparseable_returns_salary_with_raw():
    s = parse_salary("competitive")
    assert s is not None
    assert s.raw == "competitive"
    assert s.min is None
    assert s.max is None
    assert s.currency is None
    assert s.period is None


def test_parse_salary_negotiable_returns_salary_with_raw():
    s = parse_salary("Negotiable")
    assert s is not None
    assert s.raw == "Negotiable"
    assert s.min is None


# ---------------------------------------------------------------------------
# parse_location — basic cases
# ---------------------------------------------------------------------------


def test_parse_location_empty_string():
    loc = parse_location("")
    assert loc.raw == ""
    assert loc.city is None
    assert loc.is_remote is False


def test_parse_location_remote_only():
    loc = parse_location("Remote")
    assert loc.raw == "Remote"
    assert loc.is_remote is True
    assert loc.city is None
    assert loc.region is None
    assert loc.country is None


def test_parse_location_remote_case_insensitive():
    loc = parse_location("REMOTE")
    assert loc.is_remote is True


def test_parse_location_country_australia():
    loc = parse_location("Australia")
    assert loc.country == "AU"
    assert loc.is_remote is False


def test_parse_location_city_and_country():
    loc = parse_location("London, UK")
    assert loc.city == "London"
    assert loc.country == "GB"
    assert loc.is_remote is False


def test_parse_location_city_state_country_au():
    loc = parse_location("Sydney NSW, Australia")
    assert loc.city == "Sydney"
    assert loc.region == "NSW"
    assert loc.country == "AU"
    assert loc.is_remote is False


def test_parse_location_city_state_au():
    loc = parse_location("Melbourne, VIC")
    assert loc.city == "Melbourne"
    assert loc.region == "VIC"
    assert loc.country == "AU"


def test_parse_location_city_state_us():
    loc = parse_location("New York, NY")
    assert loc.city == "New York"
    assert loc.region == "NY"
    assert loc.country == "US"


def test_parse_location_city_with_embedded_state():
    loc = parse_location("Sydney NSW")
    assert loc.city == "Sydney"
    assert loc.region == "NSW"
    assert loc.country == "AU"


# ---------------------------------------------------------------------------
# parse_location — remote + geographic combos
# ---------------------------------------------------------------------------


def test_parse_location_remote_dash_city():
    loc = parse_location("Remote — Sydney")  # em dash
    assert loc.is_remote is True
    assert loc.city == "Sydney"


def test_parse_location_remote_parens_country():
    loc = parse_location("Remote (Australia)")
    assert loc.is_remote is True
    assert loc.country == "AU"


def test_parse_location_remote_sydney_based():
    loc = parse_location("Remote — Sydney-based")
    assert loc.is_remote is True
    assert loc.city == "Sydney"


def test_parse_location_raw_always_preserved():
    raw = "Remote — Sydney NSW, Australia"
    loc = parse_location(raw)
    assert loc.raw == raw


def test_parse_location_remote_with_country_code():
    loc = parse_location("Remote, AU")
    assert loc.is_remote is True
    assert loc.country == "AU"


# ---------------------------------------------------------------------------
# parse_location — unknown locations → nulls
# ---------------------------------------------------------------------------


def test_parse_location_unknown_preserves_raw():
    raw = "Somewhere Over the Rainbow"
    loc = parse_location(raw)
    assert loc.raw == raw
    assert loc.is_remote is False


# ---------------------------------------------------------------------------
# infer_seniority — re-exported from model
# ---------------------------------------------------------------------------


def test_infer_seniority_senior_ic():
    s = infer_seniority("Senior Software Engineer")
    assert s is not None
    assert s.track == "ic"
    assert s.level == "senior"


def test_infer_seniority_manager():
    s = infer_seniority("Engineering Manager")
    assert s is not None
    assert s.track == "management"
    assert s.level == "manager"


def test_infer_seniority_unknown_returns_none():
    s = infer_seniority("Software Engineer")
    assert s is None


def test_infer_seniority_description_fallback():
    s = infer_seniority("Software Engineer", "Looking for a senior developer with 5+ years")
    assert s is not None
    assert s.track == "ic"
    assert s.level == "senior"


def test_infer_seniority_lead_maps_to_staff():
    s = infer_seniority("Lead Engineer")
    assert s is not None
    assert s.track == "ic"
    assert s.level == "staff"


# ---------------------------------------------------------------------------
# Level list exports
# ---------------------------------------------------------------------------


def test_ic_levels_ordered():
    assert IC_LEVELS == ["intern", "junior", "mid", "senior", "staff", "principal"]


def test_management_levels_ordered():
    assert MANAGEMENT_LEVELS == ["manager", "senior_manager", "director", "vp"]


# ---------------------------------------------------------------------------
# run() — Stage 2 pipeline function
# ---------------------------------------------------------------------------


def test_run_empty_list():
    assert run([]) == []


def test_run_passes_through_clean_listing():
    listing = _make_listing(description="A clean description.")
    original_hash = listing.content_hash
    result = run([listing])
    assert len(result) == 1
    assert result[0].description == "A clean description."
    assert result[0].content_hash == original_hash


def test_run_strips_html_from_description():
    listing = _make_listing(description="<b>Great role</b> &amp; benefits")
    original_hash = listing.content_hash
    result = run([listing])
    assert result[0].description == "Great role & benefits"
    assert result[0].content_hash != original_hash


def test_run_content_hash_changes_when_description_cleaned():
    dirty = _make_listing(description="<p>text</p>")
    clean_listing = _make_listing(description="text")
    results = run([dirty])
    assert results[0].content_hash == clean_listing.content_hash


def test_run_canonicalizes_empty_employment_to_none():
    listing = _make_listing(employment="")
    result = run([listing])
    assert result[0].employment is None


def test_run_canonicalizes_empty_posted_at_to_none():
    listing = _make_listing(posted_at="")
    result = run([listing])
    assert result[0].posted_at is None


def test_run_preserves_non_empty_employment():
    listing = _make_listing(employment="full_time")
    result = run([listing])
    assert result[0].employment == "full_time"


def test_run_processes_multiple_listings():
    listings = [
        _make_listing(title="Engineer A", description="<b>A</b>"),
        _make_listing(title="Engineer B", description="plain B"),
    ]
    results = run(listings)
    assert len(results) == 2
    assert results[0].description == "A"
    assert results[1].description == "plain B"


def test_run_null_unknown_data_not_dropped():
    listing = _make_listing(description="Role with no salary info.")
    listing.salary = None
    listing.seniority = None
    result = run([listing])
    assert len(result) == 1
    assert result[0].salary is None
    assert result[0].seniority is None
