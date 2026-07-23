# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for the full pipeline: ingest → normalize → dedupe → filter → score → rank.

Uses a FixtureAdapter that returns pre-built JobListing objects so no live network
calls are made. Tests verify that the pipeline produces the expected filtered and ranked
set, handles source failures gracefully, enforces max_requests_per_run, and that the
Markdown digest contains the expected content.

See: specs/02-functional-spec.md §Stage 1–7
"""

from __future__ import annotations

from datetime import date

from jobhunter.digest import render_markdown
from jobhunter.model import JobListing, Location, Salary, Seniority, Source
from jobhunter.pipeline import run as pipeline_run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = date(2026, 7, 23)

BASE_PROFILE = {
    "identity": {
        "target_skills": ["Python", "AWS"],
        "target": [{"track": "ic", "level": "senior"}],
    },
    "queries": {
        "keywords": ["senior engineer"],
        "locations": ["Remote"],
        "max_results_per_query": 50,
        "max_requests_per_run": 100,
    },
    "hard_requirements": {
        "remote_policy": "remote_only",
        "exclude_locations": ["Sydney"],
        "locations_allowed": [],
        "seniority": {"ic": {"min": "mid", "max": None}},
        "salary_floor": 160000,
        "salary_currency": "AUD",
        "fx_rates": {"USD": 1.5},
        "keep_unknown_salary": True,
        "exclude_employment": ["internship"],
        "exclude_keywords": [{"term": "PHP", "scope": "title"}],
        "max_age_days": 30,
    },
    "preferences": {"preferred_locations": [], "preferred_companies": []},
    "weights": {
        "skill_match": 30,
        "seniority_fit": 20,
        "compensation": 20,
        "location_fit": 15,
        "company_signal": 10,
        "recency": 5,
    },
    "output": {
        "display_threshold": 0,
        "max_shown": 25,
        "show_previously_seen": True,
        "format": "markdown",
    },
}


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
    employment: str | None = "full_time",
    description: str = "We build scalable distributed systems.",
    posted_at: str | None = "2026-07-15",
    first_seen_at: str = "2026-07-15",
    content_hash: str = "cafebabe",
    source_id: str = "1",
) -> JobListing:
    # Use source_id in the URL so distinct listings are not merged by the URL-based dedupe pass.
    url = f"https://example.com/job/{source_id}"
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
        sources=[Source(name="fixture", url=url, source_id=source_id)],
    )


class _FixtureAdapter:
    """Adapter that returns pre-built JobListing objects; no network calls."""

    name = "fixture"

    def __init__(
        self,
        listings: list[JobListing],
        fail: bool = False,
    ) -> None:
        self._listings = listings
        self._fail = fail
        self.search_calls: list[tuple[str, str, int]] = []

    def search(self, keyword: str, location: str, max_results: int) -> list[dict]:
        self.search_calls.append((keyword, location, max_results))
        if self._fail:
            raise RuntimeError("adapter down")
        return [{"_listing": listing} for listing in self._listings[:max_results]]

    def normalize(self, raw: dict) -> JobListing:
        return raw["_listing"]


# ---------------------------------------------------------------------------
# Core pipeline correctness
# ---------------------------------------------------------------------------


class TestPipelineFiltering:
    def test_passing_listing_survives(self):
        """A listing meeting all hard requirements appears in the output."""
        listing = _listing(
            id="pass-1",
            is_remote=True,
            seniority=Seniority(track="ic", level="senior"),
            salary=Salary(min=170000, max=200000, currency="AUD", period="year"),
        )
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert len(passed) == 1
        assert passed[0].listing.id == "pass-1"

    def test_sydney_based_remote_role_is_dropped(self):
        """A listing labelled remote but with Sydney in the parsed location is dropped."""
        listing = _listing(
            id="sydney-remote",
            is_remote=True,
            city="Sydney",
            country="AU",
            location_raw="Remote — Sydney-based",
        )
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert passed == []
        assert report.dropped_by_location == 1

    def test_genuine_remote_role_is_kept(self):
        """A fully-remote listing with no excluded location is kept."""
        listing = _listing(id="remote-ok", is_remote=True, city=None, country="AU")
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert len(passed) == 1

    def test_junior_dropped_by_seniority(self):
        """A junior listing is dropped when the profile floor is mid."""
        listing = _listing(
            id="junior-1",
            seniority=Seniority(track="ic", level="junior"),
            is_remote=True,
        )
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert passed == []
        assert report.dropped_by_seniority == 1

    def test_low_salary_dropped(self):
        """A listing with an annualized salary below the floor is dropped."""
        listing = _listing(
            id="low-sal",
            is_remote=True,
            salary=Salary(min=120000, max=140000, currency="AUD", period="year"),
        )
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert passed == []
        assert report.dropped_by_salary == 1

    def test_old_listing_dropped(self):
        """A listing older than max_age_days is dropped."""
        listing = _listing(id="stale-1", is_remote=True, posted_at="2026-05-01")
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert passed == []
        assert report.dropped_by_age == 1

    def test_keyword_title_scope_drops_listing(self):
        """A listing with PHP in the title is dropped (title scope)."""
        listing = _listing(id="php-dev", title="PHP Developer", is_remote=True)
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert passed == []
        assert report.dropped_by_keyword == 1

    def test_keyword_in_description_only_does_not_drop_title_scoped(self):
        """PHP in description only doesn't drop a listing when scope is title."""
        listing = _listing(
            id="desc-php",
            title="Senior Engineer",
            description="We use Python and occasionally PHP.",
            is_remote=True,
        )
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert len(passed) == 1

    def test_unknown_salary_kept_by_default(self):
        """A listing with no salary is kept when keep_unknown_salary is True."""
        listing = _listing(id="no-sal", is_remote=True, salary=None)
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert len(passed) == 1

    def test_unknown_seniority_kept(self):
        """A listing with no inferred seniority is kept (unknown-data policy)."""
        listing = _listing(id="no-sen", is_remote=True, seniority=None)
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)
        assert len(passed) == 1

    def test_mixed_listings_produce_expected_filtered_set(self):
        """End-to-end: only the two passing listings survive from six input listings."""
        listings = [
            _listing(id="ok-1", is_remote=True, source_id="1"),
            _listing(id="ok-2", is_remote=True, source_id="2", company="Canva"),
            _listing(id="sydney", is_remote=True, city="Sydney", source_id="3"),
            _listing(
                id="junior", seniority=Seniority("ic", "junior"), is_remote=True, source_id="4"
            ),
            _listing(id="php-title", title="PHP Developer", is_remote=True, source_id="5"),
            _listing(id="stale", is_remote=True, posted_at="2026-04-01", source_id="6"),
        ]
        adapter = _FixtureAdapter(listings)
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)

        surviving_ids = {result.listing.id for result in passed}
        assert surviving_ids == {"ok-1", "ok-2"}
        assert report.dropped_by_location == 1
        assert report.dropped_by_seniority == 1
        assert report.dropped_by_keyword == 1
        assert report.dropped_by_age == 1


# ---------------------------------------------------------------------------
# Dedupe integration
# ---------------------------------------------------------------------------


class TestPipelineDedupe:
    def test_same_id_deduped_into_one(self):
        """Two listings with the same id are merged into one with both source links."""
        listing_a = _listing(id="dup-1", source_id="a1")
        listing_b = _listing(id="dup-1", source_id="b99")
        adapter = _FixtureAdapter([listing_a, listing_b])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)

        assert len(passed) == 1
        assert report.after_dedupe == 1
        assert report.ingested_count == 2
        assert len(passed[0].listing.sources) == 2

    def test_distinct_listings_stay_separate(self):
        """Two different roles remain separate after dedupe."""
        listing_a = _listing(id="role-a", company="Alpha", source_id="1")
        listing_b = _listing(id="role-b", company="Beta", source_id="2")
        adapter = _FixtureAdapter([listing_a, listing_b])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)

        assert report.after_dedupe == 2


# ---------------------------------------------------------------------------
# Source failures and run report
# ---------------------------------------------------------------------------


class TestPipelineSourceHandling:
    def test_adapter_failure_skipped_run_continues(self):
        """When an adapter raises, the run continues (other adapters still queried)."""
        failing = _FixtureAdapter([], fail=True)
        ok_listing = _listing(id="ok", is_remote=True)
        ok_adapter = _FixtureAdapter([ok_listing])

        passed, report = pipeline_run(BASE_PROFILE, [failing, ok_adapter], today=TODAY)

        assert len(report.sources_failed) == 1
        assert report.sources_failed[0].name == "fixture"
        assert "ok" in {result.listing.id for result in passed}

    def test_run_report_counts_correct(self):
        """RunReport counters reflect actual pipeline execution."""
        listings = [
            _listing(id="ok", is_remote=True, source_id="1"),
            _listing(id="dropped", is_remote=True, posted_at="2025-01-01", source_id="2"),
        ]
        adapter = _FixtureAdapter(listings)
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)

        assert report.ingested_count == 2
        assert report.after_dedupe == 2
        assert report.shown_new == 1
        assert report.dropped_by_age == 1
        assert report.requests_made == 1
        assert "fixture" in report.sources_used

    def test_no_adapters_produces_empty_run(self):
        """With no adapters, the run completes with zero listings."""
        passed, report = pipeline_run(BASE_PROFILE, [], today=TODAY)

        assert passed == []
        assert report.ingested_count == 0
        assert report.shown_new == 0
        assert report.sources_used == []


# ---------------------------------------------------------------------------
# max_requests_per_run cap
# ---------------------------------------------------------------------------


class TestPipelineRequestCap:
    def test_truncation_at_max_requests(self):
        """Pipeline stops ingesting once max_requests_per_run is reached."""
        profile = {
            **BASE_PROFILE,
            "queries": {
                "keywords": ["engineer", "developer", "manager"],
                "locations": ["Remote", "Australia"],
                "max_results_per_query": 50,
                "max_requests_per_run": 2,
            },
        }
        adapter = _FixtureAdapter([])
        passed, report = pipeline_run(profile, [adapter], today=TODAY)

        assert report.requests_made == 2
        assert report.truncated is True

    def test_no_truncation_when_under_cap(self):
        """When requests_made < cap, truncated is False."""
        profile = {
            **BASE_PROFILE,
            "queries": {
                "keywords": ["engineer"],
                "locations": ["Remote"],
                "max_results_per_query": 50,
                "max_requests_per_run": 10,
            },
        }
        adapter = _FixtureAdapter([])
        passed, report = pipeline_run(profile, [adapter], today=TODAY)

        assert report.requests_made == 1
        assert report.truncated is False


# ---------------------------------------------------------------------------
# Markdown digest rendering
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_header_shows_run_metadata(self):
        """Markdown digest includes run-at, source, and tally information."""
        listing = _listing(id="abc" + "0" * 61, is_remote=True)  # deterministic id
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)

        md = render_markdown(passed, report)
        assert "# JobHunter" in md
        assert "Run at:" in md
        assert "fixture" in md
        assert "Filter tally:" in md

    def test_listing_row_contains_required_fields(self):
        """Each listing row has rank, id, title, company, location, salary, score, sources."""
        listing = _listing(
            id="deadbeef" + "a" * 56,
            title="Senior Python Engineer",
            company="Atlassian",
            is_remote=True,
            salary=Salary(min=180000, max=220000, currency="AUD", period="year"),
            source_id="42",
        )
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)

        md = render_markdown(passed, report)
        assert "`deadbeef`" in md
        assert "Senior Python Engineer" in md
        assert "Atlassian" in md
        assert "Remote" in md
        assert "AUD" in md
        assert "180,000" in md
        assert "example.com/job/42" in md
        assert "Score:" in md
        assert "#1" in md

    def test_empty_result_shows_no_roles_message(self):
        """When no listings survive, the digest says so."""
        listing = _listing(id="stale", posted_at="2020-01-01", is_remote=True)
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)

        md = render_markdown(passed, report)
        assert "No roles matched" in md

    def test_unknown_flags_appear_in_digest(self):
        """A listing with an ambiguous location carries its unknown flags in the digest."""
        # Fully remote with no geographic info → "remote scope unclear"
        listing = _listing(id="ambig" + "0" * 59, is_remote=True, city=None, country=None)
        adapter = _FixtureAdapter([listing])
        passed, report = pipeline_run(BASE_PROFILE, [adapter], today=TODAY)

        md = render_markdown(passed, report)
        assert "remote scope unclear" in md

    def test_truncation_noted_in_digest(self):
        """When the run is truncated, the digest says so."""
        profile = {
            **BASE_PROFILE,
            "queries": {
                "keywords": ["a", "b", "c"],
                "locations": ["Remote"],
                "max_results_per_query": 50,
                "max_requests_per_run": 1,
            },
        }
        adapter = _FixtureAdapter([])
        passed, report = pipeline_run(profile, [adapter], today=TODAY)

        md = render_markdown(passed, report)
        assert "truncated" in md.lower()

    def test_source_failure_noted_in_digest(self):
        """When a source fails, the digest names it."""
        failing = _FixtureAdapter([], fail=True)
        passed, report = pipeline_run(BASE_PROFILE, [failing], today=TODAY)

        md = render_markdown(passed, report)
        assert "Sources failed:" in md
        assert "fixture" in md
