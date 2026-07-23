# SPDX-License-Identifier: Apache-2.0
"""Tests for the Careerjet source adapter.

All tests use recorded fixture payloads — no live network calls.
httpx is mocked via unittest.mock so credentials are never required.

See: specs/02-functional-spec.md §Stage 1-2
     IMPLEMENTATION_PLAN.md aggregator-adapter item
"""

from __future__ import annotations

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from jobhunter.adapters.careerjet import (
    CareerjetAdapter,
    _parse_employment,
    _parse_posted_at,
)
from jobhunter.model import JobListing

# ---------------------------------------------------------------------------
# Fixture payloads — representative Careerjet API response shapes
# ---------------------------------------------------------------------------

_SENIOR_LISTING: dict = {
    "url": "https://www.careerjet.com.au/jobad/au1234567890",
    "site": "seek.com.au",
    "title": "Senior Software Engineer",
    "company": "Acme Corp",
    "locations": "Sydney, NSW, Australia",
    "description": "Join our platform team to build scalable distributed systems.",
    "salary": "$130,000 - $170,000 per annum",
    "date": "2026-07-01",
    "contracttype": "permanent",
    "id": "au1234567890",
}

_JUNIOR_LISTING: dict = {
    "url": "https://www.careerjet.com.au/jobad/au2222222222",
    "site": "seek.com.au",
    "title": "Junior Python Developer",
    "company": "Startup Co",
    "locations": "Melbourne, VIC, Australia",
    "description": "Great opportunity for a junior developer to grow their Python skills.",
    "salary": "",
    "date": "2026-07-10",
    "contracttype": "permanent",
    "id": "au2222222222",
}

_REMOTE_CONTRACT_LISTING: dict = {
    "url": "https://www.careerjet.com.au/jobad/au3333333333",
    "site": "remote.com",
    "title": "Remote Data Engineer (Contract)",
    "company": "DataCo",
    "locations": "Remote, Australia",
    "description": "Fully remote contract role. Work from anywhere in Australia.",
    "salary": "$900/day",
    "date": "2026-07-15",
    "contracttype": "contract",
    "id": "au3333333333",
}

_MANAGER_LISTING: dict = {
    "url": "https://www.careerjet.com.au/jobad/au4444444444",
    "site": "linkedin.com",
    "title": "Engineering Manager",
    "company": "BigCo",
    "locations": "Brisbane, QLD, Australia",
    "description": "Lead and manage a team of 8 engineers delivering cloud infrastructure.",
    "salary": "AUD 180000 - AUD 220000 per year",
    "date": "2026-07-05",
    "contracttype": "permanent",
    "id": "au4444444444",
}

_NO_COMPANY_LISTING: dict = {
    "url": "https://www.careerjet.com.au/jobad/au5555555555",
    "site": "seek.com.au",
    "title": "Staff Engineer",
    "company": "",
    "locations": "Remote",
    "description": "Lead the platform architecture.",
    "salary": "",
    "date": "2026-07-20",
    "contracttype": "permanent",
    "id": "au5555555555",
}

_RELATIVE_DATE_LISTING: dict = {
    "url": "https://www.careerjet.com.au/jobad/au6666666666",
    "site": "seek.com.au",
    "title": "DevOps Engineer",
    "company": "CloudCo",
    "locations": "Sydney, NSW",
    "description": "Looking for a DevOps engineer to manage our cloud infrastructure.",
    "salary": "",
    "date": "2 days ago",  # relative date — cannot be resolved
    "contracttype": "permanent",
    "id": "au6666666666",
}

_CAREERJET_PAGE_RESPONSE: dict = {
    "type": "JOBS",
    "hits": 2,
    "pages": 1,
    "page": 1,
    "jobs": [_SENIOR_LISTING, _JUNIOR_LISTING],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> CareerjetAdapter:
    """Return a CareerjetAdapter with a fake affiliate ID."""
    env = {"CAREERJET_AFFILIATE_ID": "test_affiliate_id"}
    with patch.dict(os.environ, env):
        return CareerjetAdapter(locale_code="en_AU")


def _mock_response(payload: dict, status: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


def test_parse_employment_permanent():
    assert _parse_employment({"contracttype": "permanent"}) == "full_time"


def test_parse_employment_contract():
    assert _parse_employment({"contracttype": "contract"}) == "contract"


def test_parse_employment_temp():
    assert _parse_employment({"contracttype": "temp"}) == "temp"


def test_parse_employment_part_time():
    assert _parse_employment({"contracttype": "part-time"}) == "part_time"


def test_parse_employment_internship():
    assert _parse_employment({"contracttype": "internship"}) == "internship"


def test_parse_employment_unknown():
    assert _parse_employment({"contracttype": "something_else"}) is None


def test_parse_employment_missing():
    assert _parse_employment({}) is None


def test_parse_employment_case_insensitive():
    assert _parse_employment({"contracttype": "PERMANENT"}) == "full_time"


def test_parse_posted_at_iso():
    assert _parse_posted_at("2026-07-01") == "2026-07-01"


def test_parse_posted_at_iso_with_time():
    assert _parse_posted_at("2026-07-01T10:00:00Z") == "2026-07-01"


def test_parse_posted_at_none():
    assert _parse_posted_at(None) is None


def test_parse_posted_at_empty():
    assert _parse_posted_at("") is None


def test_parse_posted_at_relative_returns_none():
    """Relative dates ('2 days ago') cannot be resolved — return None (unknown-data policy)."""
    assert _parse_posted_at("2 days ago") is None
    assert _parse_posted_at("Today") is None
    assert _parse_posted_at("Yesterday") is None


# ---------------------------------------------------------------------------
# normalize() tests
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> CareerjetAdapter:
    return _make_adapter()


def test_normalize_returns_job_listing(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert isinstance(listing, JobListing)


def test_normalize_title(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.title == "Senior Software Engineer"


def test_normalize_company(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.company == "Acme Corp"


def test_normalize_source_name(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert len(listing.sources) == 1
    assert listing.sources[0].name == "careerjet"


def test_normalize_source_url(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].url == "https://www.careerjet.com.au/jobad/au1234567890"


def test_normalize_source_id(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].source_id == "au1234567890"


def test_normalize_description_no_html(adapter):
    raw = dict(_SENIOR_LISTING)
    raw["description"] = "<p>Join our <strong>platform team</strong>.</p>"
    listing = adapter.normalize(raw)
    assert "<" not in listing.description
    assert "platform team" in listing.description


def test_normalize_location_city_region_country(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.location.city == "Sydney"
    assert listing.location.region == "NSW"
    assert listing.location.country == "AU"


def test_normalize_location_remote(adapter):
    listing = adapter.normalize(_REMOTE_CONTRACT_LISTING)
    assert listing.location.is_remote is True


def test_normalize_location_bare_remote(adapter):
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    assert listing.location.is_remote is True


def test_normalize_location_not_remote(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.location.is_remote is False


def test_normalize_salary_range(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.salary is not None
    assert listing.salary.min == 130000.0
    assert listing.salary.max == 170000.0
    assert listing.salary.currency == "AUD"
    assert listing.salary.period == "year"


def test_normalize_salary_day_rate(adapter):
    listing = adapter.normalize(_REMOTE_CONTRACT_LISTING)
    assert listing.salary is not None
    assert listing.salary.min == 900.0
    assert listing.salary.period == "day"


def test_normalize_salary_absent_is_none(adapter):
    listing = adapter.normalize(_JUNIOR_LISTING)
    assert listing.salary is None


def test_normalize_salary_aud_explicit(adapter):
    listing = adapter.normalize(_MANAGER_LISTING)
    assert listing.salary is not None
    assert listing.salary.currency == "AUD"


def test_normalize_employment_full_time(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.employment == "full_time"


def test_normalize_employment_contract(adapter):
    listing = adapter.normalize(_REMOTE_CONTRACT_LISTING)
    assert listing.employment == "contract"


def test_normalize_posted_at_iso(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.posted_at == "2026-07-01"


def test_normalize_posted_at_relative_is_none(adapter):
    listing = adapter.normalize(_RELATIVE_DATE_LISTING)
    assert listing.posted_at is None


def test_normalize_first_seen_at_uses_run_date(adapter):
    adapter.run_date = "2026-01-15"
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.first_seen_at == "2026-01-15"


def test_normalize_first_seen_at_defaults_to_today(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.first_seen_at == date.today().isoformat()


def test_normalize_seniority_senior(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.track == "ic"
    assert listing.seniority.level == "senior"


def test_normalize_seniority_junior(adapter):
    listing = adapter.normalize(_JUNIOR_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.track == "ic"
    assert listing.seniority.level == "junior"


def test_normalize_seniority_manager(adapter):
    listing = adapter.normalize(_MANAGER_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.track == "management"
    assert listing.seniority.level == "manager"


def test_normalize_id_stable(adapter):
    id1 = adapter.normalize(_SENIOR_LISTING).id
    id2 = adapter.normalize(_SENIOR_LISTING).id
    assert id1 == id2
    assert len(id1) == 64  # SHA-256 hex


def test_normalize_content_hash_stable(adapter):
    h1 = adapter.normalize(_SENIOR_LISTING).content_hash
    h2 = adapter.normalize(_SENIOR_LISTING).content_hash
    assert h1 == h2


def test_normalize_content_hash_changes_with_salary(adapter):
    import copy

    modified = copy.deepcopy(_SENIOR_LISTING)
    modified["salary"] = "$200,000 per annum"
    h_orig = adapter.normalize(_SENIOR_LISTING).content_hash
    h_new = adapter.normalize(modified).content_hash
    assert h_orig != h_new


def test_normalize_unknown_company_salts_id(adapter):
    """Two unknown-company listings with the same title+location must not share ids."""
    a = adapter.normalize({**_NO_COMPANY_LISTING, "id": "id_a"})
    b = adapter.normalize({**_NO_COMPANY_LISTING, "id": "id_b"})
    assert a.id != b.id


def test_adapter_name():
    adapter = _make_adapter()
    assert adapter.name == "careerjet"


def test_adapter_currency_au():
    adapter = _make_adapter()
    assert adapter._currency == "AUD"


def test_adapter_currency_gb():
    env = {"CAREERJET_AFFILIATE_ID": "x"}
    with patch.dict(os.environ, env):
        a = CareerjetAdapter(locale_code="en_GB")
    assert a._currency == "GBP"


def test_adapter_currency_unknown_locale():
    env = {"CAREERJET_AFFILIATE_ID": "x"}
    with patch.dict(os.environ, env):
        a = CareerjetAdapter(locale_code="zz_ZZ")
    assert a._currency is None


def test_adapter_explicit_credentials_no_env():
    """Explicit affiliate_id works with no env vars set."""
    excluded = {"CAREERJET_AFFILIATE_ID"}
    clean_env = {k: v for k, v in os.environ.items() if k not in excluded}
    with patch.dict(os.environ, clean_env, clear=True):
        a = CareerjetAdapter(affiliate_id="explicit_id")
        assert a.affiliate_id == "explicit_id"


def test_adapter_missing_credentials_raises():
    """Missing env var must raise KeyError at construction time."""
    excluded = {"CAREERJET_AFFILIATE_ID"}
    clean_env = {k: v for k, v in os.environ.items() if k not in excluded}
    with patch.dict(os.environ, clean_env, clear=True):
        with pytest.raises(KeyError):
            CareerjetAdapter()


# ---------------------------------------------------------------------------
# search() tests — httpx is mocked, no live network
# ---------------------------------------------------------------------------


def test_search_returns_raw_listings(adapter):
    mock_resp = _mock_response(_CAREERJET_PAGE_RESPONSE)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        results = adapter.search("python developer", "Sydney", max_results=10)

    assert len(results) == 2
    assert results[0]["id"] == "au1234567890"
    assert results[1]["id"] == "au2222222222"


def test_search_empty_results(adapter):
    mock_resp = _mock_response({"type": "JOBS", "hits": 0, "pages": 0, "page": 1, "jobs": []})
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        results = adapter.search("no-match-keyword", "nowhere", max_results=50)

    assert results == []


def test_search_respects_max_results(adapter):
    """max_results < page full: results sliced correctly."""
    page = {
        "type": "JOBS",
        "hits": 20,
        "pages": 1,
        "page": 1,
        "jobs": [_SENIOR_LISTING] * 10,
    }
    mock_resp = _mock_response(page)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        results = adapter.search("python", "Sydney", max_results=3)

    assert len(results) == 3


def test_search_paginates_when_multiple_pages():
    """When multiple pages exist, search issues multiple requests."""
    page1 = {
        "type": "JOBS",
        "hits": 3,
        "pages": 2,
        "page": 1,
        "jobs": [_SENIOR_LISTING, _JUNIOR_LISTING],
    }
    page2 = {
        "type": "JOBS",
        "hits": 3,
        "pages": 2,
        "page": 2,
        "jobs": [_REMOTE_CONTRACT_LISTING],
    }

    responses = [_mock_response(page1), _mock_response(page2)]
    env = {"CAREERJET_AFFILIATE_ID": "x"}
    with (
        patch("jobhunter.adapters.careerjet._PAGE_SIZE", 2),
        patch("httpx.Client") as mock_client_cls,
        patch.dict(os.environ, env),
    ):
        adapter_local = CareerjetAdapter(page_delay=0.0)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = responses
        mock_client_cls.return_value = mock_client

        results = adapter_local.search("python", "Australia", max_results=10)

    assert len(results) == 3
    assert mock_client.get.call_count == 2


def test_search_stops_on_single_page():
    """When page 1 of 1 is returned, no second request is issued."""
    mock_resp = _mock_response(_CAREERJET_PAGE_RESPONSE)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        adapter = _make_adapter()
        adapter.search("python", "Sydney", max_results=50)

    assert mock_client.get.call_count == 1


def test_search_increments_requests_made(adapter):
    """requests_made is incremented once per HTTP call."""
    mock_resp = _mock_response(_CAREERJET_PAGE_RESPONSE)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        adapter.search("python", "Sydney", max_results=50)

    assert adapter.requests_made == 1


def test_search_raises_on_http_error(adapter):
    """HTTP errors propagate to the caller (pipeline catches them)."""
    import httpx as _httpx

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
        "403", request=MagicMock(), response=MagicMock()
    )
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with pytest.raises(_httpx.HTTPStatusError):
            adapter.search("python", "Sydney", max_results=10)


def test_page_delay_between_pages():
    """time.sleep is called with page_delay between page 1 and page 2."""
    page1 = {
        "type": "JOBS",
        "hits": 3,
        "pages": 2,
        "page": 1,
        "jobs": [_SENIOR_LISTING, _JUNIOR_LISTING],
    }
    page2 = {
        "type": "JOBS",
        "hits": 3,
        "pages": 2,
        "page": 2,
        "jobs": [_REMOTE_CONTRACT_LISTING],
    }

    responses = [_mock_response(page1), _mock_response(page2)]
    env = {"CAREERJET_AFFILIATE_ID": "x"}
    with (
        patch("jobhunter.adapters.careerjet._PAGE_SIZE", 2),
        patch("jobhunter.adapters.careerjet.time.sleep") as mock_sleep,
        patch("httpx.Client") as mock_client_cls,
        patch.dict(os.environ, env),
    ):
        a = CareerjetAdapter(page_delay=0.3)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = responses
        mock_client_cls.return_value = mock_client

        a.search("python", "Australia", max_results=10)

    mock_sleep.assert_called_once_with(0.3)


def test_no_page_delay_on_first_page():
    """time.sleep is NOT called for the first page."""
    mock_resp = _mock_response(_CAREERJET_PAGE_RESPONSE)
    with (
        patch("jobhunter.adapters.careerjet.time.sleep") as mock_sleep,
        patch("httpx.Client") as mock_client_cls,
    ):
        a = _make_adapter()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        a.search("python", "Sydney", max_results=50)

    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# END-TO-END criterion: adapter activates when env var is set
# ---------------------------------------------------------------------------


def test_build_adapters_includes_careerjet_when_configured():
    """END-TO-END: CAREERJET_AFFILIATE_ID set → CareerjetAdapter present in pipeline."""
    from jobhunter.cli import _build_adapters

    profile = {"queries": {"ats_watchlist": []}}
    # Supply Careerjet; suppress Adzuna to keep the test clean
    excluded = {"ADZUNA_APP_ID", "ADZUNA_APP_KEY"}
    clean_env = {k: v for k, v in os.environ.items() if k not in excluded}
    clean_env["CAREERJET_AFFILIATE_ID"] = "test_id"
    with patch.dict(os.environ, clean_env, clear=True):
        adapters = _build_adapters(profile)
    assert any(a.name == "careerjet" for a in adapters)


def test_build_adapters_skips_careerjet_without_env():
    """Disabled source (no env var) → CareerjetAdapter never constructed."""
    from jobhunter.cli import _build_adapters

    excluded = {"CAREERJET_AFFILIATE_ID", "ADZUNA_APP_ID", "ADZUNA_APP_KEY"}
    clean_env = {k: v for k, v in os.environ.items() if k not in excluded}
    with patch.dict(os.environ, clean_env, clear=True):
        adapters = _build_adapters({"queries": {"ats_watchlist": []}})
    assert not any(a.name == "careerjet" for a in adapters)


def test_careerjet_in_sources_used_after_search():
    """END-TO-END pipeline criterion: Careerjet search populates sources_used."""
    from jobhunter.pipeline import run as pipeline_run

    mock_resp = _mock_response(_CAREERJET_PAGE_RESPONSE)
    env = {"CAREERJET_AFFILIATE_ID": "test_id"}
    with (
        patch.dict(os.environ, env),
        patch("httpx.Client") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        from jobhunter.adapters.careerjet import CareerjetAdapter

        adapter = CareerjetAdapter(affiliate_id="test_id")
        profile = {
            "queries": {
                "keywords": ["python"],
                "locations": ["Sydney"],
                "max_results_per_query": 10,
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
                "max_age_days": 365,
                "fx_rates": {},
            },
            "preferences": {"preferred_locations": [], "preferred_companies": []},
            "weights": {"skill_match": 1.0},
            "output": {
                "display_threshold": 0,
                "max_shown": 25,
                "show_previously_seen": True,
                "format": "markdown",
                "data_format": "json",
            },
        }
        _results, report = pipeline_run(profile, [adapter])

    assert "careerjet" in report.sources_used
