# SPDX-License-Identifier: Apache-2.0
"""Tests for the ATS company job-board adapter.

All tests use mocked HTTP — no live network calls are made.
httpx is mocked via unittest.mock.patch.

See: specs/02-functional-spec.md §Stage 1-2
     specs/04-technical-plan.md §Data sources
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobhunter.adapters.ats import (
    _FETCHERS,
    SUPPORTED_ATS_TYPES,
    AtsAdapter,
    _ashby_employment,
    _fetch_ashby,
    _fetch_greenhouse,
    _fetch_lever,
    _lever_employment,
    _normalize_ashby,
    _normalize_greenhouse,
    _normalize_lever,
    _parse_iso_date,
)
from jobhunter.model import JobListing

# ---------------------------------------------------------------------------
# Fixture payloads
# ---------------------------------------------------------------------------

_GH_RAW: dict = {
    "id": 1234567,
    "title": "Senior Open Source Community Manager",
    "content": "<p>Join our <strong>OSPO team</strong> to build community programs.</p>",
    "absolute_url": "https://boards.greenhouse.io/github/jobs/1234567",
    "updated_at": "2026-07-01T10:00:00Z",
    "location": {"name": "Remote"},
    "_ats_type": "greenhouse",
    "_ats_slug": "github",
    "_company_name": "GitHub",
    "_run_date": "2026-07-23",
}

_LEVER_RAW: dict = {
    "id": "abc-123",
    "text": "Head of Developer Relations",
    "description": "<p>Lead our global DevRel team.</p>",
    "hostedUrl": "https://jobs.lever.co/hashicorp/abc-123",
    "createdAt": 1751500000000,  # ms since epoch → 2025-07-03 ish
    "categories": {
        "commitment": "Full-time",
        "location": "Remote",
    },
    "_ats_type": "lever",
    "_ats_slug": "hashicorp",
    "_company_name": "HashiCorp",
    "_run_date": "2026-07-23",
}

_ASHBY_RAW: dict = {
    "id": "xyz-789",
    "title": "Director of Community",
    "descriptionSocial": (
        "We are looking for a Director of Community to lead our open source initiatives."
    ),
    "locationName": "Remote, Worldwide",
    "publishedDate": "2026-06-15T00:00:00.000Z",
    "jobUrl": "https://jobs.ashbyhq.com/elastic/xyz-789",
    "employmentType": "FullTime",
    "_ats_type": "ashby",
    "_ats_slug": "elastic",
    "_company_name": "Elastic",
    "_run_date": "2026-07-23",
}


# ---------------------------------------------------------------------------
# _parse_iso_date
# ---------------------------------------------------------------------------


class TestParseIsoDate:
    def test_utc_z_suffix(self):
        assert _parse_iso_date("2026-07-01T10:00:00Z") == "2026-07-01"

    def test_timezone_offset(self):
        assert _parse_iso_date("2026-07-01T10:00:00+05:30") == "2026-07-01"

    def test_bare_date(self):
        assert _parse_iso_date("2026-07-23") == "2026-07-23"

    def test_none_returns_none(self):
        assert _parse_iso_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_iso_date("") is None

    def test_invalid_returns_none(self):
        assert _parse_iso_date("not-a-date") is None


# ---------------------------------------------------------------------------
# Employment type helpers
# ---------------------------------------------------------------------------


class TestLeverEmployment:
    def test_full_time(self):
        raw = {"categories": {"commitment": "Full-time"}}
        assert _lever_employment(raw) == "full_time"

    def test_part_time(self):
        raw = {"categories": {"commitment": "Part-time"}}
        assert _lever_employment(raw) == "part_time"

    def test_contract(self):
        raw = {"categories": {"commitment": "Contract"}}
        assert _lever_employment(raw) == "contract"

    def test_internship(self):
        raw = {"categories": {"commitment": "Internship"}}
        assert _lever_employment(raw) == "internship"

    def test_unknown_returns_none(self):
        raw = {"categories": {"commitment": "Seasonal"}}
        assert _lever_employment(raw) is None

    def test_missing_categories(self):
        assert _lever_employment({}) is None


class TestAshbyEmployment:
    def test_full_time(self):
        assert _ashby_employment({"employmentType": "FullTime"}) == "full_time"

    def test_part_time(self):
        assert _ashby_employment({"employmentType": "PartTime"}) == "part_time"

    def test_contract(self):
        assert _ashby_employment({"employmentType": "Contract"}) == "contract"

    def test_internship(self):
        assert _ashby_employment({"employmentType": "Internship"}) == "internship"

    def test_unknown_returns_none(self):
        assert _ashby_employment({"employmentType": "Volunteer"}) is None

    def test_missing_field(self):
        assert _ashby_employment({}) is None

    def test_no_dead_code(self):
        """Employment mapping uses emp_lower, not an unused intermediate variable."""
        # This test exists to confirm the fix: the original draft had a dead 'emp'
        # variable that was computed but never used. The function should work correctly.
        result = _ashby_employment({"employmentType": "FullTime"})
        assert result == "full_time"


# ---------------------------------------------------------------------------
# Per-ATS normalize helpers
# ---------------------------------------------------------------------------


class TestNormalizeGreenhouse:
    def test_basic_fields(self):
        listing = _normalize_greenhouse(_GH_RAW)
        assert listing.title == "Senior Open Source Community Manager"
        assert listing.company == "GitHub"
        assert listing.posted_at == "2026-07-01"
        assert listing.first_seen_at == "2026-07-23"
        assert listing.salary is None
        assert listing.employment is None  # Greenhouse public API doesn't expose it

    def test_description_html_stripped(self):
        listing = _normalize_greenhouse(_GH_RAW)
        assert "<p>" not in listing.description
        assert "OSPO team" in listing.description

    def test_source_name_includes_slug(self):
        listing = _normalize_greenhouse(_GH_RAW)
        assert listing.sources[0].name == "ats_greenhouse:github"

    def test_source_url(self):
        listing = _normalize_greenhouse(_GH_RAW)
        assert "greenhouse.io" in listing.sources[0].url

    def test_id_derived(self):
        listing = _normalize_greenhouse(_GH_RAW)
        assert listing.id != ""

    def test_content_hash_derived(self):
        listing = _normalize_greenhouse(_GH_RAW)
        assert listing.content_hash != ""

    def test_missing_company_uses_slug(self):
        raw = {**_GH_RAW, "_company_name": "", "_ats_slug": "myslug"}
        listing = _normalize_greenhouse(raw)
        assert listing.company == "myslug"

    def test_run_date_used_for_first_seen(self):
        raw = {**_GH_RAW, "_run_date": "2026-01-15"}
        listing = _normalize_greenhouse(raw)
        assert listing.first_seen_at == "2026-01-15"


class TestNormalizeLever:
    def test_basic_fields(self):
        listing = _normalize_lever(_LEVER_RAW)
        assert listing.title == "Head of Developer Relations"
        assert listing.company == "HashiCorp"
        assert listing.employment == "full_time"
        assert listing.first_seen_at == "2026-07-23"
        assert listing.salary is None

    def test_description_html_stripped(self):
        listing = _normalize_lever(_LEVER_RAW)
        assert "<p>" not in listing.description
        assert "DevRel" in listing.description

    def test_source_name_includes_slug(self):
        listing = _normalize_lever(_LEVER_RAW)
        assert listing.sources[0].name == "ats_lever:hashicorp"

    def test_created_at_ms_parsed(self):
        listing = _normalize_lever(_LEVER_RAW)
        # createdAt 1751500000000 ms → 2025-07-03 (UTC)
        assert listing.posted_at is not None
        assert listing.posted_at.startswith("202")

    def test_missing_created_at(self):
        raw = {**_LEVER_RAW, "createdAt": None}
        listing = _normalize_lever(raw)
        assert listing.posted_at is None

    def test_run_date_used_for_first_seen(self):
        raw = {**_LEVER_RAW, "_run_date": "2026-03-10"}
        listing = _normalize_lever(raw)
        assert listing.first_seen_at == "2026-03-10"

    def test_missing_company_uses_slug(self):
        raw = {**_LEVER_RAW, "_company_name": "", "_ats_slug": "lever-co"}
        listing = _normalize_lever(raw)
        assert listing.company == "lever-co"


class TestNormalizeAshby:
    def test_basic_fields(self):
        listing = _normalize_ashby(_ASHBY_RAW)
        assert listing.title == "Director of Community"
        assert listing.company == "Elastic"
        assert listing.employment == "full_time"
        assert listing.first_seen_at == "2026-07-23"
        assert listing.salary is None

    def test_posted_at_parsed(self):
        listing = _normalize_ashby(_ASHBY_RAW)
        assert listing.posted_at == "2026-06-15"

    def test_source_name_includes_slug(self):
        listing = _normalize_ashby(_ASHBY_RAW)
        assert listing.sources[0].name == "ats_ashby:elastic"

    def test_run_date_used_for_first_seen(self):
        raw = {**_ASHBY_RAW, "_run_date": "2026-02-28"}
        listing = _normalize_ashby(raw)
        assert listing.first_seen_at == "2026-02-28"

    def test_missing_company_uses_slug(self):
        raw = {**_ASHBY_RAW, "_company_name": "", "_ats_slug": "ashby-co"}
        listing = _normalize_ashby(raw)
        assert listing.company == "ashby-co"


# ---------------------------------------------------------------------------
# AtsAdapter class
# ---------------------------------------------------------------------------


class TestAtsAdapterInit:
    def test_filters_unsupported_ats(self):
        watchlist = [
            {"ats": "greenhouse", "slug": "github"},
            {"ats": "workday", "slug": "microsoft"},  # unsupported
        ]
        adapter = AtsAdapter(watchlist)
        assert len(adapter._watchlist) == 1

    def test_filters_missing_slug(self):
        watchlist = [
            {"ats": "lever"},  # no slug
            {"ats": "lever", "slug": "hashicorp"},
        ]
        adapter = AtsAdapter(watchlist)
        assert len(adapter._watchlist) == 1

    def test_empty_watchlist(self):
        adapter = AtsAdapter([])
        assert adapter._watchlist == []

    def test_query_independent_flag(self):
        adapter = AtsAdapter([])
        assert adapter.query_independent is True

    def test_supported_ats_types(self):
        assert SUPPORTED_ATS_TYPES == {"greenhouse", "lever", "ashby"}


class TestAtsAdapterSearch:
    def _make_adapter(self, entries=None):
        if entries is None:
            entries = [
                {"ats": "greenhouse", "slug": "github", "name": "GitHub"},
            ]
        return AtsAdapter(entries)

    def test_injects_metadata_into_raw(self):
        adapter = self._make_adapter()
        fake_raw = [{"id": 99, "title": "Staff Engineer"}]
        with patch.dict(_FETCHERS, {"greenhouse": lambda slug: fake_raw}):
            results = adapter.search("", "", 50)
        assert len(results) == 1
        assert results[0]["_ats_type"] == "greenhouse"
        assert results[0]["_ats_slug"] == "github"
        assert results[0]["_company_name"] == "GitHub"

    def test_run_date_injected_into_raw(self):
        adapter = self._make_adapter()
        adapter.run_date = "2026-07-23"
        fake_raw = [{"id": 1}]
        with patch.dict(_FETCHERS, {"greenhouse": lambda slug: fake_raw}):
            results = adapter.search("", "", 50)
        assert results[0]["_run_date"] == "2026-07-23"

    def test_name_defaults_to_slug_when_absent(self):
        adapter = AtsAdapter([{"ats": "greenhouse", "slug": "canonical"}])
        fake_raw = [{"id": 1}]
        with patch.dict(_FETCHERS, {"greenhouse": lambda slug: fake_raw}):
            results = adapter.search("", "", 50)
        assert results[0]["_company_name"] == "canonical"

    def test_keyword_and_location_ignored(self):
        """search() accepts keyword/location for protocol compatibility but ignores them."""
        adapter = self._make_adapter()
        calls = []

        def fake_fetch(slug):
            calls.append(slug)
            return []

        with patch.dict(_FETCHERS, {"greenhouse": fake_fetch}):
            adapter.search("whatever keyword", "Sydney CBD", 50)
        # Called once with just the slug (keyword/location not forwarded)
        assert calls == ["github"]

    def test_no_max_results_truncation(self):
        """All results are returned without [:max_results] truncation."""
        adapter = self._make_adapter()
        fake_raw = [{"id": i} for i in range(200)]
        with patch.dict(_FETCHERS, {"greenhouse": lambda slug: fake_raw}):
            results = adapter.search("", "", 10)  # max_results=10 but should not truncate
        assert len(results) == 200

    def test_per_company_error_continues(self):
        """A failure for one company does not abort the remaining watchlist."""
        adapter = AtsAdapter([
            {"ats": "greenhouse", "slug": "github", "name": "GitHub"},
            {"ats": "lever", "slug": "hashicorp", "name": "HashiCorp"},
        ])
        gh_raw = [{"id": 1, "title": "Community Manager"}]

        def fail_lever(slug):
            raise RuntimeError("network timeout")

        with patch.dict(_FETCHERS, {"greenhouse": lambda slug: gh_raw, "lever": fail_lever}):
            results = adapter.search("", "", 50)

        # GitHub results still returned
        assert len(results) == 1
        # HashiCorp failure recorded
        assert len(adapter.company_failures) == 1
        assert "HashiCorp" in adapter.company_failures[0]
        assert "network timeout" in adapter.company_failures[0]

    def test_company_failures_reset_each_call(self):
        """company_failures is cleared at the start of each search() call."""
        adapter = AtsAdapter([{"ats": "lever", "slug": "bad-co", "name": "BadCo"}])
        def _raise(slug):
            raise RuntimeError("err")

        with patch.dict(_FETCHERS, {"lever": _raise}):
            adapter.search("", "", 50)
        assert len(adapter.company_failures) == 1

        # Second call: failures from previous run are gone
        with patch.dict(_FETCHERS, {"lever": lambda slug: []}):
            adapter.search("", "", 50)
        assert adapter.company_failures == []

    def test_aggregates_multiple_companies(self):
        adapter = AtsAdapter([
            {"ats": "greenhouse", "slug": "github", "name": "GitHub"},
            {"ats": "lever", "slug": "hashicorp", "name": "HashiCorp"},
            {"ats": "ashby", "slug": "elastic", "name": "Elastic"},
        ])
        with patch.dict(_FETCHERS, {
            "greenhouse": lambda slug: [{"id": 1}],
            "lever": lambda slug: [{"id": 2}],
            "ashby": lambda slug: [{"id": 3}],
        }):
            results = adapter.search("", "", 50)
        assert len(results) == 3
        ats_types = {r["_ats_type"] for r in results}
        assert ats_types == {"greenhouse", "lever", "ashby"}


class TestAtsAdapterNormalize:
    def test_dispatches_by_ats_type(self):
        adapter = AtsAdapter([])
        listing = adapter.normalize(_GH_RAW)
        assert isinstance(listing, JobListing)
        assert listing.company == "GitHub"

    def test_unknown_ats_type_raises(self):
        adapter = AtsAdapter([])
        with pytest.raises(ValueError, match="Unknown ATS type"):
            adapter.normalize({"_ats_type": "workday"})

    def test_missing_ats_type_raises(self):
        adapter = AtsAdapter([])
        with pytest.raises(ValueError, match="Unknown ATS type"):
            adapter.normalize({})


# ---------------------------------------------------------------------------
# HTTP fetch helpers (light smoke tests via mocked httpx)
# ---------------------------------------------------------------------------


class TestFetchGreenhouse:
    def test_returns_jobs_list(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": [{"id": 1, "title": "Head of OSPO"}]}
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        with patch("jobhunter.adapters.ats.httpx.Client", return_value=mock_client):
            jobs = _fetch_greenhouse("github")
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Head of OSPO"

    def test_empty_board_returns_empty_list(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": []}
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        with patch("jobhunter.adapters.ats.httpx.Client", return_value=mock_client):
            jobs = _fetch_greenhouse("nobody")
        assert jobs == []


class TestFetchLever:
    def test_returns_list_directly(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": "abc", "text": "Community Lead"}]
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        with patch("jobhunter.adapters.ats.httpx.Client", return_value=mock_client):
            jobs = _fetch_lever("hashicorp")
        assert jobs == [{"id": "abc", "text": "Community Lead"}]

    def test_non_list_response_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "not found"}
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        with patch("jobhunter.adapters.ats.httpx.Client", return_value=mock_client):
            jobs = _fetch_lever("nobody")
        assert jobs == []


class TestFetchAshby:
    def test_returns_job_postings(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobPostings": [{"id": "xyz", "title": "DevRel Lead"}]}
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        with patch("jobhunter.adapters.ats.httpx.Client", return_value=mock_client):
            jobs = _fetch_ashby("elastic")
        assert len(jobs) == 1
        assert jobs[0]["title"] == "DevRel Lead"


# ---------------------------------------------------------------------------
# Pipeline integration (query-independent adapter path)
# ---------------------------------------------------------------------------


def _make_profile():
    return {
        "identity": {
            "target_skills": ["Python"],
            "target": [{"track": "ic", "level": "senior"}],
        },
        "queries": {
            "keywords": ["open source"],
            "locations": ["Remote"],
            "max_results_per_query": 50,
            "max_requests_per_run": 10,
            "ats_watchlist": [],
        },
        "hard_requirements": {
            "remote_policy": "any",
            "exclude_locations": [],
            "locations_allowed": [],
            "keep_unknown_salary": True,
            "exclude_employment": [],
            "exclude_keywords": [],
            "require_keywords": [],
            "max_age_days": 90,
        },
        "preferences": {},
        "weights": {"skill_match": 1},
        "output": {
            "display_threshold": 0,
            "max_shown": 25,
            "show_previously_seen": True,
            "format": "markdown",
        },
    }


class TestAtsPipelineIntegration:
    def test_ats_adapter_called_once_not_per_keyword_location(self):
        """AtsAdapter.search is called once per run, not per keyword×location combo."""
        from datetime import date

        from jobhunter.pipeline import run as pipeline_run

        call_count = []

        class FakeAtsAdapter:
            name = "ats"
            query_independent = True
            company_failures: list = []
            run_date = ""

            def search(self, keyword, location, max_results):
                call_count.append((keyword, location))
                return []

            def normalize(self, raw):
                raise AssertionError("normalize should not be called on empty results")

        profile = {
            **_make_profile(),
            "queries": {
                "keywords": ["ospo manager", "devrel lead", "community director"],
                "locations": ["Remote AU", "Remote US", "Remote"],
                "max_results_per_query": 50,
                "max_requests_per_run": 100,
                "ats_watchlist": [],
            },
        }

        _results, _report = pipeline_run(profile, [FakeAtsAdapter()], today=date(2026, 7, 23))
        assert len(call_count) == 1, (
            f"AtsAdapter.search should be called once per run; called {len(call_count)} times"
        )

    def test_company_failures_surfaced_in_report(self):
        """Per-company failures appear in run report sources_failed."""
        from datetime import date

        from jobhunter.pipeline import run as pipeline_run

        class FailingAtsAdapter:
            name = "ats"
            query_independent = True
            run_date = ""

            def search(self, keyword, location, max_results):
                self.company_failures = ["GitHub (greenhouse:github): timeout"]
                return []

            def normalize(self, raw):
                raise AssertionError

        _results, report = pipeline_run(
            _make_profile(), [FailingAtsAdapter()], today=date(2026, 7, 23)
        )
        failure_errors = [f.error for f in report.sources_failed]
        assert any("GitHub" in e and "timeout" in e for e in failure_errors)

    def test_ats_adapter_counts_one_request(self):
        """An ATS adapter counts as one request toward max_requests_per_run."""
        from datetime import date

        from jobhunter.pipeline import run as pipeline_run

        class FakeAtsAdapter:
            name = "ats"
            query_independent = True
            company_failures: list = []
            run_date = ""

            def search(self, keyword, location, max_results):
                return []

            def normalize(self, raw):
                raise AssertionError

        _results, report = pipeline_run(
            _make_profile(), [FakeAtsAdapter()], today=date(2026, 7, 23)
        )
        assert report.requests_made == 1
