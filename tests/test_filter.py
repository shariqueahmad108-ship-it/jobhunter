# SPDX-License-Identifier: Apache-2.0
"""Tests for Stage 4 — Hard filter.

See: specs/02-functional-spec.md §Stage 4
"""

from __future__ import annotations

from datetime import date

from jobhunter.filter import FilterResult, FilterTally, run
from jobhunter.model import JobListing, Location, Salary, Seniority, Source

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

TODAY = date(2026, 7, 23)


def _src(n: int = 1) -> list[Source]:
    return [Source(name="test", url=f"https://example.com/job/{n}", source_id=str(n))]


def _listing(
    *,
    id: str = "listing-1",
    title: str = "Senior Software Engineer",
    company: str = "Acme",
    location_raw: str = "Remote",
    city: str | None = None,
    region: str | None = None,
    country: str | None = None,
    is_remote: bool = True,
    salary: Salary | None = None,
    seniority: Seniority | None = None,
    employment: str | None = None,
    description: str = "We are building scalable cloud systems.",
    posted_at: str | None = None,
    first_seen_at: str = "2026-07-10",
    content_hash: str = "deadbeef",
) -> JobListing:
    return JobListing(
        id=id,
        content_hash=content_hash,
        title=title,
        company=company,
        location=Location(
            raw=location_raw,
            city=city,
            region=region,
            country=country,
            is_remote=is_remote,
        ),
        salary=salary,
        seniority=seniority,
        employment=employment,
        description=description,
        posted_at=posted_at,
        first_seen_at=first_seen_at,
        sources=_src(),
    )


# Profile matching profile.example.yaml semantics.
BASE_HR = {
    "remote_policy": "remote_only",
    "exclude_locations": ["Sydney"],
    "locations_allowed": [],
    "seniority": {
        "ic": {"min": "mid", "max": None},
        "management": {"min": "manager", "max": "director"},
    },
    "salary_floor": 160000,
    "salary_currency": "AUD",
    "fx_rates": {"USD": 1.5, "NZD": 0.93},
    "keep_unknown_salary": True,
    "exclude_employment": ["internship"],
    "exclude_keywords": [
        {"term": "PHP", "scope": "title"},
        {"term": "unpaid", "scope": "requirements"},
        {"term": "security clearance", "scope": "requirements"},
    ],
    "max_age_days": 21,
}

BASE_PROFILE = {"hard_requirements": BASE_HR}


def _profile(**overrides) -> dict:
    """Return a profile with the given hard_requirements overrides."""
    return {"hard_requirements": {**BASE_HR, **overrides}}


# ---------------------------------------------------------------------------
# Location / remote tests
# ---------------------------------------------------------------------------


class TestLocationFilter:
    def test_remote_sydney_tagged_is_dropped(self):
        """Core spec case: remote role tagged 'Sydney' → dropped by exclude_locations."""
        listing = _listing(
            id="sydney-remote",
            is_remote=True,
            location_raw="Remote — Sydney based",
            city="Sydney",
            country="AU",
        )
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_location == 1

    def test_genuine_remote_is_kept(self):
        """Core spec case: genuine remote role (no Sydney) → kept."""
        listing = _listing(is_remote=True, location_raw="Remote")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1
        assert result.tally.by_location == 0

    def test_remote_no_geo_gets_scope_unclear_flag(self):
        """Remote listing with no city/region/country → kept + 'remote scope unclear' flag."""
        listing = _listing(id="r1", is_remote=True, location_raw="Remote")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1
        assert "remote scope unclear" in result.unknown_flags.get("r1", [])

    def test_nonremote_known_location_dropped_for_remote_only(self):
        """Non-remote listing with a known city → dropped by remote_only."""
        listing = _listing(
            id="mel",
            is_remote=False,
            location_raw="Melbourne VIC, Australia",
            city="Melbourne",
            region="VIC",
            country="AU",
        )
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_location == 1

    def test_nonremote_unknown_location_kept_with_flag(self):
        """Non-remote listing with no parseable location → kept + 'location unclear' flag."""
        listing = _listing(id="unk", is_remote=False, location_raw="", city=None, country=None)
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1
        assert "location unclear" in result.unknown_flags.get("unk", [])

    def test_exclude_location_region_match(self):
        """exclude_locations matches against the region field."""
        listing = _listing(
            id="nsw",
            is_remote=True,
            location_raw="Remote — NSW based",
            region="NSW",
            country="AU",
        )
        result = run([listing], _profile(exclude_locations=["NSW"]), today=TODAY)
        assert result.passed == []
        assert result.tally.by_location == 1

    def test_exclude_location_country_match(self):
        """exclude_locations matches against the country field."""
        listing = _listing(id="au", is_remote=True, city=None, region=None, country="AU")
        result = run([listing], _profile(exclude_locations=["AU"]), today=TODAY)
        assert result.passed == []

    def test_exclude_location_case_insensitive(self):
        """exclude_locations matching is case-insensitive."""
        listing = _listing(id="syd", is_remote=True, city="Sydney")
        result = run([listing], _profile(exclude_locations=["sydney"]), today=TODAY)
        assert result.passed == []

    def test_locations_allowed_empty_never_drops(self):
        """locations_allowed: [] → no listing dropped by location alone."""
        listing = _listing(
            id="mel", is_remote=False, city="Melbourne", country="AU", location_raw="Melbourne"
        )
        result = run(
            [listing],
            _profile(remote_policy="any", locations_allowed=[]),
            today=TODAY,
        )
        assert len(result.passed) == 1
        assert result.tally.by_location == 0

    def test_locations_allowed_restricts_non_remote(self):
        """Non-empty locations_allowed restricts non-remote listings by location."""
        nz = _listing(id="nz", is_remote=False, city="Auckland", country="NZ")
        au = _listing(id="au", is_remote=False, city="Melbourne", country="AU")
        result = run(
            [nz, au],
            _profile(remote_policy="any", locations_allowed=["AU"]),
            today=TODAY,
        )
        assert len(result.passed) == 1
        assert result.passed[0].id == "au"
        assert result.tally.by_location == 1

    def test_remote_exempt_from_locations_allowed_hybrid_ok(self):
        """Remote listings bypass locations_allowed for hybrid_ok policy."""
        remote_us = _listing(id="rus", is_remote=True, country="US")
        result = run(
            [remote_us],
            _profile(remote_policy="hybrid_ok", locations_allowed=["AU"]),
            today=TODAY,
        )
        assert len(result.passed) == 1

    def test_remote_exempt_from_locations_allowed_onsite_ok(self):
        """Remote listings bypass locations_allowed for onsite_ok policy."""
        remote_us = _listing(id="rus", is_remote=True, country="US")
        result = run(
            [remote_us],
            _profile(remote_policy="onsite_ok", locations_allowed=["AU"]),
            today=TODAY,
        )
        assert len(result.passed) == 1

    def test_any_policy_does_not_drop_for_remoteness(self):
        """remote_policy=any never drops a listing for its remote status."""
        onsite_sydney = _listing(id="s", is_remote=False, city="Sydney", country="AU")
        result = run([onsite_sydney], _profile(remote_policy="any"), today=TODAY)
        # Sydney is in exclude_locations → still dropped
        assert result.passed == []
        assert result.tally.by_location == 1

    def test_any_policy_non_excluded_city_kept(self):
        """remote_policy=any, non-excluded city → kept."""
        onsite_mel = _listing(
            id="m", is_remote=False, city="Melbourne", country="AU", location_raw="Melbourne"
        )
        result = run([onsite_mel], _profile(remote_policy="any"), today=TODAY)
        assert len(result.passed) == 1

    def test_exclude_location_not_substring(self):
        """exclude_locations does not match as a substring; must match a full field."""
        # "South Sydney" as city — "Sydney" should NOT match "South Sydney" as a prefix,
        # but since city IS "South Sydney", "Sydney" (as exact field check) does NOT match.
        listing = _listing(id="ss", is_remote=True, city="South Sydney")
        result = run([listing], BASE_PROFILE, today=TODAY)
        # "Sydney" ≠ "South Sydney" — not a match
        assert len(result.passed) == 1


# ---------------------------------------------------------------------------
# Seniority tests
# ---------------------------------------------------------------------------


class TestSeniorityFilter:
    def test_null_seniority_kept_with_flag(self):
        """Unknown seniority (None) → kept with 'level unclear' flag."""
        listing = _listing(id="lv", seniority=None)
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1
        assert "level unclear" in result.unknown_flags.get("lv", [])

    def test_within_ic_band_kept(self):
        """IC senior within [mid, principal] → kept."""
        listing = _listing(seniority=Seniority(track="ic", level="senior"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_ic_mid_at_min_kept(self):
        """IC mid at the exact min bound → kept."""
        listing = _listing(seniority=Seniority(track="ic", level="mid"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_ic_principal_at_open_max_kept(self):
        """IC principal with max=None (unbounded) → kept."""
        listing = _listing(seniority=Seniority(track="ic", level="principal"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_below_ic_min_dropped(self):
        """IC junior below min=mid → dropped."""
        listing = _listing(seniority=Seniority(track="ic", level="junior"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_seniority == 1

    def test_ic_intern_below_min_dropped(self):
        """IC intern → dropped (below min=mid)."""
        listing = _listing(seniority=Seniority(track="ic", level="intern"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_seniority == 1

    def test_management_director_at_max_kept(self):
        """Management director at the exact max bound → kept."""
        listing = _listing(seniority=Seniority(track="management", level="director"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_management_above_max_dropped(self):
        """Management VP above max=director → dropped."""
        listing = _listing(seniority=Seniority(track="management", level="vp"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_seniority == 1

    def test_disallowed_management_track_dropped(self):
        """Management listing when only IC is configured → dropped."""
        listing = _listing(seniority=Seniority(track="management", level="manager"))
        result = run(
            [listing],
            _profile(seniority={"ic": {"min": "mid", "max": None}}),
            today=TODAY,
        )
        assert result.passed == []
        assert result.tally.by_seniority == 1

    def test_disallowed_ic_track_dropped(self):
        """IC listing when only management is configured → dropped."""
        listing = _listing(seniority=Seniority(track="ic", level="senior"))
        result = run(
            [listing],
            _profile(seniority={"management": {"min": "manager", "max": None}}),
            today=TODAY,
        )
        assert result.passed == []

    def test_no_seniority_config_keeps_all(self):
        """seniority=None in profile → no seniority filtering at all."""
        listing = _listing(seniority=Seniority(track="ic", level="intern"))
        result = run([listing], _profile(seniority=None), today=TODAY)
        assert len(result.passed) == 1


# ---------------------------------------------------------------------------
# Salary tests
# ---------------------------------------------------------------------------


class TestSalaryFilter:
    def test_null_salary_kept_keep_unknown_true(self):
        """No salary (None) → kept when keep_unknown_salary=true."""
        listing = _listing(salary=None)
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_null_salary_dropped_keep_unknown_false(self):
        """No salary (None) → dropped when keep_unknown_salary=false."""
        listing = _listing(salary=None)
        result = run([listing], _profile(keep_unknown_salary=False), today=TODAY)
        assert result.passed == []
        assert result.tally.by_salary == 1

    def test_aud_above_floor_kept(self):
        """AUD salary above floor → kept."""
        listing = _listing(salary=Salary(min=170000, max=200000, currency="AUD", period="year"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_aud_at_floor_kept(self):
        """AUD salary exactly at floor → kept (floor is a minimum, not exclusive)."""
        listing = _listing(salary=Salary(min=160000, max=160000, currency="AUD", period="year"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_aud_below_floor_dropped(self):
        """AUD salary below floor → dropped."""
        listing = _listing(salary=Salary(min=120000, max=150000, currency="AUD", period="year"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_salary == 1

    def test_day_rate_annualizes_correctly(self):
        """$900/day AUD → $900 × 260 = $234k → above floor → kept."""
        listing = _listing(salary=Salary(min=900, max=900, currency="AUD", period="day"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_low_day_rate_dropped(self):
        """$500/day AUD → $130k → below floor → dropped."""
        listing = _listing(salary=Salary(min=500, max=500, currency="AUD", period="day"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []

    def test_hourly_rate_annualizes(self):
        """$100/hour AUD → $100 × 2080 = $208k → above floor → kept."""
        listing = _listing(salary=Salary(min=100, max=100, currency="AUD", period="hour"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_monthly_salary_annualizes(self):
        """$15000/month AUD → $180k → above floor → kept."""
        listing = _listing(salary=Salary(min=15000, max=15000, currency="AUD", period="month"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_usd_with_rate_above_floor_kept(self):
        """USD salary × fx_rate=1.5 → AUD → above floor → kept."""
        # USD 120000 × 1.5 = AUD 180000 > 160000
        listing = _listing(salary=Salary(min=100000, max=120000, currency="USD", period="year"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_usd_with_rate_below_floor_dropped(self):
        """USD salary × fx_rate that puts result below floor → dropped."""
        # USD 90000 × 1.5 = AUD 135000 < 160000
        listing = _listing(salary=Salary(min=80000, max=90000, currency="USD", period="year"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_salary == 1

    def test_unknown_currency_kept_by_default(self):
        """Currency with no pinned rate → treated as unknown → kept (keep_unknown_salary=true)."""
        listing = _listing(salary=Salary(min=50000, max=60000, currency="GBP", period="year"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_unknown_currency_dropped_when_not_keep_unknown(self):
        """Unknown currency + keep_unknown_salary=false → dropped."""
        listing = _listing(salary=Salary(min=50000, max=60000, currency="GBP", period="year"))
        result = run([listing], _profile(keep_unknown_salary=False), today=TODAY)
        assert result.passed == []
        assert result.tally.by_salary == 1

    def test_no_salary_floor_skips_filter(self):
        """salary_floor=None → salary filter entirely skipped."""
        listing = _listing(salary=Salary(min=50000, max=60000, currency="AUD", period="year"))
        result = run([listing], _profile(salary_floor=None), today=TODAY)
        assert len(result.passed) == 1

    def test_no_salary_max_treated_as_unknown(self):
        """Salary with max=None → incomparable → treated as unknown."""
        listing = _listing(salary=Salary(min=None, max=None, currency="AUD", period="year"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1  # keep_unknown_salary=True

    def test_nzd_with_rate_above_floor_kept(self):
        """NZD salary with pinned rate converts and compares correctly."""
        # NZD 200000 × 0.93 = AUD 186000 > 160000
        listing = _listing(salary=Salary(min=190000, max=200000, currency="NZD", period="year"))
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1


# ---------------------------------------------------------------------------
# Employment tests
# ---------------------------------------------------------------------------


class TestEmploymentFilter:
    def test_excluded_employment_dropped(self):
        """Employment type in exclude_employment → dropped."""
        listing = _listing(employment="internship")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_employment == 1

    def test_null_employment_kept(self):
        """employment=None → kept (unknown-data policy)."""
        listing = _listing(employment=None)
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_allowed_employment_kept(self):
        """Allowed employment type → kept."""
        listing = _listing(employment="full_time")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_contract_not_excluded_by_default(self):
        """contract employment not in exclude list → kept."""
        listing = _listing(employment="contract")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1


# ---------------------------------------------------------------------------
# Keyword tests
# ---------------------------------------------------------------------------


class TestKeywordFilter:
    def test_title_scope_term_in_title_dropped(self):
        """Title-scope term found in title → dropped."""
        listing = _listing(title="PHP Developer", description="Python and Ruby work")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_keyword == 1

    def test_title_scope_term_in_description_only_kept(self):
        """Title-scope term in description only → NOT dropped (scope is title)."""
        listing = _listing(
            title="Full Stack Developer",
            description="Experience with PHP required",
        )
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_requirements_scope_matches_description(self):
        """Requirements-scope term in description → dropped."""
        listing = _listing(description="This is an unpaid position.")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_keyword == 1

    def test_requirements_scope_matches_title(self):
        """Requirements-scope term in title → also dropped."""
        listing = _listing(title="Unpaid Marketing Intern")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []

    def test_word_boundary_prevents_partial_match(self):
        """Word-boundary: 'PHP' should NOT match inside 'PHPBB'."""
        listing = _listing(title="PHPBB Community Developer")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_case_insensitive_match(self):
        """Keyword matching is case-insensitive."""
        listing = _listing(description="This is an UNPAID trial period.")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []

    def test_multiword_term_matches(self):
        """Multi-word deal-breaker term matches in description."""
        listing = _listing(description="Requires active security clearance.")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []

    def test_multiword_term_word_boundary(self):
        """Multi-word term with word boundaries: 'clearance' at word boundaries."""
        # "security clearance" should match "security clearance level"
        listing = _listing(description="Must have security clearance level 3.")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []

    def test_no_keywords_keeps_all(self):
        """Empty exclude_keywords list → no keyword filtering."""
        listing = _listing(title="PHP Developer", description="PHP everywhere")
        result = run([listing], _profile(exclude_keywords=[]), today=TODAY)
        assert len(result.passed) == 1


# ---------------------------------------------------------------------------
# Freshness tests
# ---------------------------------------------------------------------------


class TestFreshnessFilter:
    def test_old_posting_dropped(self):
        """Posting older than max_age_days (21) → dropped."""
        # 38 days before TODAY
        listing = _listing(posted_at="2026-06-15")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_age == 1

    def test_recent_posting_kept(self):
        """Posting within max_age_days → kept."""
        # 13 days before TODAY
        listing = _listing(posted_at="2026-07-10")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_exactly_at_cutoff_kept(self):
        """Posting exactly at the cutoff date → kept (boundary is inclusive)."""
        # Exactly 21 days before TODAY = 2026-07-02
        listing = _listing(posted_at="2026-07-02")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1

    def test_one_day_past_cutoff_dropped(self):
        """Posting one day past cutoff → dropped."""
        # 22 days before TODAY = 2026-07-01
        listing = _listing(posted_at="2026-07-01")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []

    def test_fallback_to_first_seen_at(self):
        """No posted_at → fall back to first_seen_at for age check."""
        # first_seen_at is too old (38 days)
        listing = _listing(posted_at=None, first_seen_at="2026-06-15")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_age == 1

    def test_fallback_first_seen_at_recent_kept(self):
        """No posted_at, recent first_seen_at → kept."""
        listing = _listing(posted_at=None, first_seen_at="2026-07-10")
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1


# ---------------------------------------------------------------------------
# Dismissed tests
# ---------------------------------------------------------------------------


class TestDismissedFilter:
    def test_dismissed_id_always_dropped(self):
        """Listing id in dismissed_ids → dropped regardless of all other criteria."""
        listing = _listing(id="bye-bye")
        result = run([listing], BASE_PROFILE, dismissed_ids={"bye-bye"}, today=TODAY)
        assert result.passed == []
        assert result.tally.dismissed == 1

    def test_non_dismissed_listing_kept(self):
        """Listing NOT in dismissed_ids → not affected."""
        listing = _listing(id="keeper")
        result = run([listing], BASE_PROFILE, dismissed_ids={"other-id"}, today=TODAY)
        assert len(result.passed) == 1

    def test_empty_dismissed_set_keeps_all(self):
        """Empty dismissed_ids → no listing dropped by dismissal."""
        listing = _listing()
        result = run([listing], BASE_PROFILE, dismissed_ids=set(), today=TODAY)
        assert len(result.passed) == 1

    def test_dismissed_counted_separately(self):
        """Dismissed tally is distinct from other filter tallies."""
        listing = _listing(id="d1")
        result = run([listing], BASE_PROFILE, dismissed_ids={"d1"}, today=TODAY)
        assert result.tally.dismissed == 1
        assert result.tally.by_location == 0


# ---------------------------------------------------------------------------
# Filter tally and FilterResult interface
# ---------------------------------------------------------------------------


class TestFilterTallyAndInterface:
    def test_tally_counts_all_categories(self):
        """Each filter category is independently tallied."""
        listings = [
            _listing(id="loc", is_remote=True, city="Sydney"),
            _listing(id="sen", seniority=Seniority(track="ic", level="junior")),
            _listing(id="sal", salary=Salary(min=50000, max=80000, currency="AUD", period="year")),
            _listing(id="emp", employment="internship"),
            _listing(id="kw", title="PHP Developer"),
            _listing(id="age", posted_at="2026-05-01"),  # way too old
            _listing(id="ok"),  # passes all filters
        ]
        result = run(listings, BASE_PROFILE, today=TODAY)

        assert result.tally.by_location == 1
        assert result.tally.by_seniority == 1
        assert result.tally.by_salary == 1
        assert result.tally.by_employment == 1
        assert result.tally.by_keyword == 1
        assert result.tally.by_age == 1
        assert len(result.passed) == 1
        assert result.passed[0].id == "ok"

    def test_result_has_correct_types(self):
        """FilterResult attributes have the correct types."""
        listing = _listing()
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert isinstance(result, FilterResult)
        assert isinstance(result.passed, list)
        assert isinstance(result.tally, FilterTally)
        assert isinstance(result.unknown_flags, dict)

    def test_empty_input_returns_empty_result(self):
        """No listings in → no listings out."""
        result = run([], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.dismissed == 0

    def test_passing_listing_not_in_flags(self):
        """A listing that passes with no ambiguity has no unknown_flags entry."""
        listing = _listing(
            id="clean",
            is_remote=True,
            location_raw="Remote — Australia",
            country="AU",
            seniority=Seniority(track="ic", level="senior"),
            salary=Salary(min=170000, max=190000, currency="AUD", period="year"),
            employment="full_time",
            posted_at="2026-07-10",
        )
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert len(result.passed) == 1
        assert "clean" not in result.unknown_flags


# ---------------------------------------------------------------------------
# Multi-filter interaction
# ---------------------------------------------------------------------------


class TestMultiFilterInteraction:
    def test_first_failing_filter_stops_evaluation(self):
        """A listing failing multiple filters is counted only under the first failure."""
        # Sydney (location fail) AND junior (seniority fail)
        listing = _listing(
            id="multi",
            is_remote=True,
            city="Sydney",
            seniority=Seniority(track="ic", level="junior"),
        )
        result = run([listing], BASE_PROFILE, today=TODAY)
        assert result.passed == []
        assert result.tally.by_location == 1
        assert result.tally.by_seniority == 0  # never reached

    def test_profile_change_changes_outcome(self):
        """Changing a single profile filter changes the surviving set."""
        listing = _listing(salary=Salary(min=150000, max=155000, currency="AUD", period="year"))

        # Current floor drops this listing
        result_high = run([listing], BASE_PROFILE, today=TODAY)
        assert result_high.passed == []

        # Lowered floor keeps it
        result_low = run([listing], _profile(salary_floor=140000), today=TODAY)
        assert len(result_low.passed) == 1
