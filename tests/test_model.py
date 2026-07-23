# SPDX-License-Identifier: Apache-2.0
"""Tests for the canonical JobListing model and id/content_hash derivation.

See: specs/03-data-model.md
Validation: python -m pytest tests/test_model.py -q
"""

from jobhunter.model import (
    JobListing,
    Location,
    RunReport,
    RunState,
    Salary,
    ScoreComponent,
    ScoredResult,
    SeenEntry,
    Seniority,
    Source,
    SourceFailure,
    derive_content_hash,
    derive_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_location(
    raw: str = "Sydney NSW",
    city: str | None = "Sydney",
    region: str | None = "NSW",
    country: str | None = "AU",
    is_remote: bool = False,
) -> Location:
    return Location(raw=raw, city=city, region=region, country=country, is_remote=is_remote)


def make_listing(
    title: str = "Senior Software Engineer",
    company: str = "Acme Corp",
    location: Location | None = None,
    description: str = "Build great software.",
    salary: Salary | None = None,
    **kwargs,
) -> JobListing:
    loc = location or make_location()
    listing = JobListing(
        id="placeholder",
        content_hash="placeholder",
        title=title,
        company=company,
        location=loc,
        description=description,
        sources=[Source(name="adzuna", url="https://example.com/job/1", source_id="abc123")],
        first_seen_at="2026-07-23",
        salary=salary,
        **kwargs,
    )
    listing.id = derive_id(listing.company, listing.title, listing.location)
    listing.content_hash = derive_content_hash(listing)
    return listing


# ---------------------------------------------------------------------------
# Type instantiation smoke tests
# ---------------------------------------------------------------------------


class TestTypeInstantiation:
    def test_location_minimal(self):
        loc = Location(raw="Remote")
        assert loc.raw == "Remote"
        assert loc.city is None
        assert loc.country is None
        assert loc.is_remote is False

    def test_location_full(self):
        loc = Location(raw="Sydney AU", city="Sydney", region="NSW", country="AU", is_remote=False)
        assert loc.city == "Sydney"
        assert loc.country == "AU"

    def test_salary_all_optional(self):
        s = Salary()
        assert s.min is None
        assert s.max is None
        assert s.currency is None
        assert s.period is None
        assert s.raw is None

    def test_salary_full(self):
        s = Salary(min=120000, max=150000, currency="AUD", period="year", raw="$120k-$150k")
        assert s.min == 120000
        assert s.period == "year"

    def test_seniority(self):
        sn = Seniority(track="ic", level="senior")
        assert sn.track == "ic"
        assert sn.level == "senior"

    def test_source(self):
        src = Source(name="adzuna", url="https://example.com/1", source_id="xyz")
        assert src.name == "adzuna"

    def test_joblisting_minimal(self):
        loc = Location(raw="Remote", is_remote=True)
        listing = JobListing(
            id="abc",
            content_hash="def",
            title="Engineer",
            company="Corp",
            location=loc,
            description="desc",
            sources=[Source(name="adzuna", url="https://x.com", source_id="1")],
            first_seen_at="2026-07-23",
        )
        assert listing.salary is None
        assert listing.seniority is None
        assert listing.employment is None
        assert listing.posted_at is None

    def test_score_component(self):
        sc = ScoreComponent(name="skill_match", sub=0.75, weight=3.0, reason="6/8 skills")
        assert sc.sub == 0.75

    def test_scored_result_defaults(self):
        listing = make_listing()
        result = ScoredResult(listing=listing, score=72.5)
        assert result.components == []
        assert result.summary_reason == ""
        assert result.rank == 0
        assert result.unknown_flags == []

    def test_seen_entry(self):
        entry = SeenEntry(id="abc", content_hash="xyz", last_shown_at="2026-07-23")
        assert entry.id == "abc"

    def test_run_state_defaults(self):
        state = RunState(schema_version=1)
        assert state.seen == []
        assert state.dismissed_ids == []
        assert state.last_run_at is None

    def test_run_report_defaults(self):
        report = RunReport(run_at="2026-07-23")
        assert report.sources_used == []
        assert report.sources_failed == []
        assert report.requests_made == 0
        assert report.truncated is False

    def test_source_failure(self):
        sf = SourceFailure(name="adzuna", error="timeout")
        assert sf.name == "adzuna"


# ---------------------------------------------------------------------------
# derive_id: stable identity key
# ---------------------------------------------------------------------------


class TestDeriveId:
    def test_returns_hex_string(self):
        loc = make_location()
        id_ = derive_id("Acme", "Senior Engineer", loc)
        assert isinstance(id_, str)
        assert len(id_) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in id_)

    def test_same_inputs_same_id(self):
        loc = make_location()
        id1 = derive_id("Acme", "Senior Engineer", loc)
        id2 = derive_id("Acme", "Senior Engineer", loc)
        assert id1 == id2

    def test_different_company_different_id(self):
        loc = make_location()
        id1 = derive_id("Acme", "Senior Engineer", loc)
        id2 = derive_id("Globex", "Senior Engineer", loc)
        assert id1 != id2

    def test_different_title_different_id(self):
        loc = make_location()
        id1 = derive_id("Acme", "Senior Engineer", loc)
        id2 = derive_id("Acme", "Junior Engineer", loc)
        assert id1 != id2

    def test_different_location_different_id(self):
        loc_sydney = make_location(city="Sydney")
        loc_melbourne = make_location(city="Melbourne")
        id1 = derive_id("Acme", "Engineer", loc_sydney)
        id2 = derive_id("Acme", "Engineer", loc_melbourne)
        assert id1 != id2

    # Title decoration normalization

    def test_sr_dot_normalizes_to_senior(self):
        loc = make_location()
        id_sr = derive_id("Acme", "Sr. Software Engineer", loc)
        id_senior = derive_id("Acme", "Senior Software Engineer", loc)
        assert id_sr == id_senior

    def test_sr_no_dot_normalizes_to_senior(self):
        loc = make_location()
        id_sr = derive_id("Acme", "Sr Software Engineer", loc)
        id_senior = derive_id("Acme", "Senior Software Engineer", loc)
        assert id_sr == id_senior

    def test_snr_normalizes_to_senior(self):
        loc = make_location()
        id_snr = derive_id("Acme", "Snr Engineer", loc)
        id_senior = derive_id("Acme", "Senior Engineer", loc)
        assert id_snr == id_senior

    def test_jr_normalizes_to_junior(self):
        loc = make_location()
        id_jr = derive_id("Acme", "Jr. Developer", loc)
        id_junior = derive_id("Acme", "Junior Developer", loc)
        assert id_jr == id_junior

    def test_trailing_parens_removed(self):
        loc = make_location()
        id_with = derive_id("Acme", "Senior Engineer (Backend Team)", loc)
        id_without = derive_id("Acme", "Senior Engineer", loc)
        assert id_with == id_without

    def test_trailing_parens_multi_word(self):
        loc = make_location()
        id_with = derive_id("Acme", "Software Engineer (Fintech Division)", loc)
        id_without = derive_id("Acme", "Software Engineer", loc)
        assert id_with == id_without

    # Case insensitivity

    def test_case_insensitive_company(self):
        loc = make_location()
        id1 = derive_id("ACME Corp", "Engineer", loc)
        id2 = derive_id("acme corp", "Engineer", loc)
        assert id1 == id2

    def test_case_insensitive_title(self):
        loc = make_location()
        id1 = derive_id("Acme", "SENIOR ENGINEER", loc)
        id2 = derive_id("Acme", "senior engineer", loc)
        assert id1 == id2

    # Whitespace collapsing

    def test_extra_whitespace_collapsed(self):
        loc = make_location()
        id1 = derive_id("Acme  Corp", "Senior   Engineer", loc)
        id2 = derive_id("Acme Corp", "Senior Engineer", loc)
        assert id1 == id2

    # Punctuation stripping

    def test_punctuation_stripped(self):
        loc = make_location()
        id1 = derive_id("Acme, Corp.", "Senior Engineer", loc)
        id2 = derive_id("Acme Corp", "Senior Engineer", loc)
        assert id1 == id2

    # Location fallback logic: city > country > "remote"

    def test_uses_city_over_country(self):
        loc_city = Location(raw="Sydney AU", city="Sydney", country="AU", is_remote=False)
        loc_country_only = Location(raw="AU", city=None, country="AU", is_remote=False)
        # These should differ because city vs country is used
        id_city = derive_id("Acme", "Engineer", loc_city)
        id_country = derive_id("Acme", "Engineer", loc_country_only)
        # city "Sydney" != country "AU" → different ids
        assert id_city != id_country

    def test_no_city_uses_country(self):
        loc = Location(raw="Australia", city=None, country="AU", is_remote=False)
        id_ = derive_id("Acme", "Engineer", loc)
        assert isinstance(id_, str)
        assert len(id_) == 64

    def test_fully_remote_no_city_uses_remote(self):
        loc_remote_au = Location(raw="Remote AU", city=None, country="AU", is_remote=True)
        loc_remote_no_country = Location(raw="Remote", city=None, country=None, is_remote=True)
        # Uses "AU" for first, "remote" for second
        id_au = derive_id("Acme", "Engineer", loc_remote_au)
        id_remote = derive_id("Acme", "Engineer", loc_remote_no_country)
        assert id_au != id_remote

    def test_fully_remote_no_country_uses_remote_keyword(self):
        """Two remote-only listings with no country/city share the 'remote' location key."""
        loc1 = Location(raw="Remote", city=None, country=None, is_remote=True)
        loc2 = Location(raw="Anywhere", city=None, country=None, is_remote=True)
        id1 = derive_id("Acme", "Engineer", loc1)
        id2 = derive_id("Acme", "Engineer", loc2)
        # Both use "remote" as location — raw string differs but id is the same
        assert id1 == id2

    def test_no_city_no_country_not_remote(self):
        """Listing with no city, no country, not remote — location part is absent."""
        loc = Location(raw="Unknown", city=None, country=None, is_remote=False)
        id_ = derive_id("Acme", "Engineer", loc)
        assert isinstance(id_, str)
        assert len(id_) == 64


# ---------------------------------------------------------------------------
# derive_content_hash: change detection
# ---------------------------------------------------------------------------


class TestDeriveContentHash:
    def test_returns_hex_string(self):
        listing = make_listing()
        assert isinstance(listing.content_hash, str)
        assert len(listing.content_hash) == 64

    def test_same_listing_same_hash(self):
        listing = make_listing()
        h1 = derive_content_hash(listing)
        h2 = derive_content_hash(listing)
        assert h1 == h2

    def test_different_title_different_hash(self):
        l1 = make_listing(title="Senior Engineer")
        l2 = make_listing(title="Principal Engineer")
        assert derive_content_hash(l1) != derive_content_hash(l2)

    def test_different_description_different_hash(self):
        l1 = make_listing(description="Build great software.")
        l2 = make_listing(description="Build products at scale.")
        assert derive_content_hash(l1) != derive_content_hash(l2)

    def test_different_salary_min_different_hash(self):
        l1 = make_listing(salary=Salary(min=100000, max=150000, currency="AUD", period="year"))
        l2 = make_listing(salary=Salary(min=120000, max=150000, currency="AUD", period="year"))
        assert derive_content_hash(l1) != derive_content_hash(l2)

    def test_different_salary_max_different_hash(self):
        l1 = make_listing(salary=Salary(min=100000, max=130000, currency="AUD", period="year"))
        l2 = make_listing(salary=Salary(min=100000, max=150000, currency="AUD", period="year"))
        assert derive_content_hash(l1) != derive_content_hash(l2)

    def test_different_salary_currency_different_hash(self):
        l1 = make_listing(salary=Salary(min=100000, currency="AUD"))
        l2 = make_listing(salary=Salary(min=100000, currency="USD"))
        assert derive_content_hash(l1) != derive_content_hash(l2)

    def test_different_salary_period_different_hash(self):
        l1 = make_listing(salary=Salary(min=900, period="day"))
        l2 = make_listing(salary=Salary(min=900, period="hour"))
        assert derive_content_hash(l1) != derive_content_hash(l2)

    def test_different_location_raw_different_hash(self):
        loc1 = make_location(raw="Sydney NSW")
        loc2 = make_location(raw="Melbourne VIC")
        l1 = make_listing(location=loc1)
        l2 = make_listing(location=loc2)
        assert derive_content_hash(l1) != derive_content_hash(l2)

    def test_volatile_fields_do_not_affect_hash(self):
        """Changing sources, posted_at, first_seen_at must NOT change the content hash."""
        loc = make_location()
        base_kwargs = dict(
            title="Engineer",
            company="Acme",
            location=loc,
            description="desc",
        )

        l1 = make_listing(
            **base_kwargs,
            posted_at="2026-07-01",
        )
        l1.sources = [Source(name="adzuna", url="https://a.com/1", source_id="s1")]

        l2 = make_listing(
            **base_kwargs,
            posted_at="2026-07-20",
        )
        l2.sources = [
            Source(name="adzuna", url="https://a.com/1", source_id="s1"),
            Source(name="seek", url="https://b.com/2", source_id="s2"),
        ]
        l2.first_seen_at = "2026-07-10"

        assert derive_content_hash(l1) == derive_content_hash(l2)

    def test_null_salary_stable(self):
        """A listing with no salary has a stable, consistent hash."""
        l1 = make_listing(salary=None)
        l2 = make_listing(salary=None)
        assert derive_content_hash(l1) == derive_content_hash(l2)

    def test_salary_raw_field_excluded_from_hash(self):
        """Salary.raw is cosmetic and must not influence the content hash."""
        base = dict(min=100000, max=150000, currency="AUD", period="year")
        l1 = make_listing(salary=Salary(**base, raw="$100k-$150k AUD"))
        l2 = make_listing(salary=Salary(**base, raw="100000–150000 AUD per year"))
        assert derive_content_hash(l1) == derive_content_hash(l2)


# ---------------------------------------------------------------------------
# Integration: id + content_hash together
# ---------------------------------------------------------------------------


class TestIdAndHashTogether:
    def test_same_role_different_sources_same_id_and_hash(self):
        """The same role cross-posted on two boards shares id and content_hash."""
        loc = make_location(raw="Remote", city=None, country="AU", is_remote=True)
        common = dict(
            title="Staff Engineer", company="Initech", location=loc, description="Do stuff."
        )
        l1 = make_listing(**common)
        l1.sources = [Source(name="adzuna", url="https://adzuna.com/1", source_id="a1")]
        l2 = make_listing(**common)
        l2.sources = [Source(name="seek", url="https://seek.com/2", source_id="s2")]

        assert l1.id == l2.id
        assert derive_content_hash(l1) == derive_content_hash(l2)

    def test_updated_salary_same_id_different_hash(self):
        """A material field change keeps the same id but produces a different content_hash."""
        l1 = make_listing(salary=Salary(min=120000, max=150000, currency="AUD", period="year"))
        l2 = make_listing(salary=Salary(min=140000, max=170000, currency="AUD", period="year"))

        assert l1.id == l2.id  # same role
        assert derive_content_hash(l1) != derive_content_hash(l2)  # salary changed

    def test_different_titles_same_company_different_id(self):
        """Two distinct roles at the same company must not share an id."""
        loc = make_location()
        l1 = make_listing(title="Backend Engineer", company="Acme", location=loc)
        l2 = make_listing(title="Frontend Engineer", company="Acme", location=loc)
        assert l1.id != l2.id


# ---------------------------------------------------------------------------
# term_pattern: symbol-edged terms
# ---------------------------------------------------------------------------

from jobhunter.model import term_pattern


class TestTermPattern:
    def test_plain_word_boundaries(self):
        assert term_pattern("PHP").search("Senior PHP Developer")
        assert not term_pattern("PHP").search("PHPUnit expert")

    def test_cpp_matches(self):
        assert term_pattern("C++").search("C++ developer wanted")
        assert term_pattern("C++").search("Knowledge of C++ required")

    def test_no_substring_matches(self):
        assert not term_pattern("java").search("javascript required")

    def test_dotnet_matches(self):
        assert term_pattern(".NET").search("Senior .NET Engineer")
        assert not term_pattern(".NET").search("wideNETwork tooling")

    def test_case_insensitive(self):
        assert term_pattern("php").search("Senior PHP Developer")

    def test_multiword(self):
        assert term_pattern("system design").search("strong System Design skills")
