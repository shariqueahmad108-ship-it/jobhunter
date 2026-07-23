# SPDX-License-Identifier: Apache-2.0
"""Tests for the Adzuna source adapter.

All tests use recorded fixture payloads — no live network calls.
httpx is mocked via unittest.mock so credentials are never required.

See: specs/02-functional-spec.md §Stage 1-2
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from jobhunter.adapters.adzuna import (
    AdzunaAdapter,
    _detect_remote,
    _parse_employment,
    _parse_location,
    _parse_posted_at,
)
from jobhunter.model import JobListing
from jobhunter.normalize import strip_html as _strip_html

# ---------------------------------------------------------------------------
# Fixture payloads — representative Adzuna API response shapes
# ---------------------------------------------------------------------------

_SENIOR_LISTING: dict = {
    "id": "1111111111",
    "title": "Senior Software Engineer",
    "description": "<p>Join our <strong>platform team</strong> to build scalable systems.</p>",
    "redirect_url": "https://www.adzuna.com.au/jobs/details/1111111111",
    "created": "2026-07-01T10:00:00Z",
    "salary_min": 130000.0,
    "salary_max": 170000.0,
    "salary_is_predicted": 0,
    "company": {"display_name": "Acme Corp"},
    "location": {
        "display_name": "Sydney, New South Wales",
        "area": ["Australia", "New South Wales", "Sydney"],
    },
    "contract_type": "permanent",
    "contract_time": "full_time",
    "category": {"label": "IT Jobs", "tag": "it-jobs"},
}

_JUNIOR_LISTING: dict = {
    "id": "2222222222",
    "title": "Junior Python Developer",
    "description": "Great opportunity for a junior developer to grow their skills.",
    "redirect_url": "https://www.adzuna.com.au/jobs/details/2222222222",
    "created": "2026-07-10T08:30:00Z",
    "salary_min": 65000.0,
    "salary_max": 80000.0,
    "salary_is_predicted": 0,
    "company": {"display_name": "Startup Co"},
    "location": {
        "display_name": "Melbourne, Victoria",
        "area": ["Australia", "Victoria", "Melbourne"],
    },
    "contract_type": "permanent",
    "contract_time": "full_time",
    "category": {"label": "IT Jobs", "tag": "it-jobs"},
}

_REMOTE_CONTRACT_LISTING: dict = {
    "id": "3333333333",
    "title": "Remote Data Engineer (Contract)",
    "description": "Fully remote contract role. Work from anywhere in Australia.",
    "redirect_url": "https://www.adzuna.com.au/jobs/details/3333333333",
    "created": "2026-07-15T14:00:00Z",
    "salary_min": None,
    "salary_max": None,
    "salary_is_predicted": 0,
    "company": {"display_name": "DataCo"},
    "location": {
        "display_name": "Australia",
        "area": ["Australia"],
    },
    "contract_type": "contract",
    "contract_time": "full_time",
    "category": {"label": "IT Jobs", "tag": "it-jobs"},
}

_NO_COMPANY_LISTING: dict = {
    "id": "4444444444",
    "title": "Staff Engineer",
    "description": "Lead the platform architecture.",
    "redirect_url": "https://www.adzuna.com.au/jobs/details/4444444444",
    "created": "2026-07-20T09:00:00Z",
    "salary_min": 200000.0,
    "salary_max": 250000.0,
    "salary_is_predicted": 1,
    "company": {},
    "location": {
        "display_name": "Remote",
        "area": [],
    },
    "contract_type": "permanent",
    "contract_time": "full_time",
    "category": {"label": "IT Jobs", "tag": "it-jobs"},
}

_MANAGER_LISTING: dict = {
    "id": "5555555555",
    "title": "Engineering Manager",
    "description": "Manage a team of 8 engineers.",
    "redirect_url": "https://www.adzuna.com.au/jobs/details/5555555555",
    "created": "2026-07-05T11:00:00Z",
    "salary_min": 180000.0,
    "salary_max": 220000.0,
    "salary_is_predicted": 0,
    "company": {"display_name": "BigCo"},
    "location": {
        "display_name": "Sydney, New South Wales",
        "area": ["Australia", "New South Wales", "Sydney"],
    },
    "contract_type": "permanent",
    "contract_time": "full_time",
    "category": {"label": "IT Jobs", "tag": "it-jobs"},
}

_ADZUNA_SEARCH_RESPONSE: dict = {
    "__CLASS__": "Adzuna::API::Response::Jobs",
    "count": 2,
    "results": [_SENIOR_LISTING, _JUNIOR_LISTING],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> AdzunaAdapter:
    """Return an AdzunaAdapter with fake credentials injected via env."""
    env = {"ADZUNA_APP_ID": "test_id", "ADZUNA_APP_KEY": "test_key"}
    with patch.dict(os.environ, env):
        return AdzunaAdapter(country="au")


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


def test_strip_html_removes_tags():
    assert _strip_html("<p>Hello <strong>world</strong></p>") == "Hello world"


def test_strip_html_unescape_entities():
    assert "&amp;" not in _strip_html("Foo &amp; Bar")
    assert "Foo & Bar" == _strip_html("Foo &amp; Bar")


def test_strip_html_collapses_whitespace():
    result = _strip_html("<p>  lots   of   space  </p>")
    assert "  " not in result


def test_detect_remote_from_title():
    assert _detect_remote("Remote Software Engineer", "", "Sydney") is True


def test_detect_remote_from_location():
    assert _detect_remote("Software Engineer", "", "Remote") is True


def test_detect_remote_from_description():
    assert _detect_remote("Engineer", "This is a fully remote role.", "Sydney") is True


def test_detect_remote_description_negation_rejected():
    assert _detect_remote("Engineer", "This role has no remote work available.", "Sydney") is False
    assert _detect_remote("Engineer", "This position cannot be remote.", "Sydney") is False


def test_detect_remote_full_description_bare_mention_not_enough():
    """In a FULL description, a bare 'remote' is too weak a signal."""
    long_desc = ("Our team spans several remote offices. " + "We build software. " * 40)
    assert len(long_desc) >= 500
    assert _detect_remote("Engineer", long_desc, "Sydney") is False


def test_detect_remote_truncated_snippet_bare_mention_counts():
    """In a TRUNCATED snippet (Adzuna), a bare 'remote' counts — recall over precision."""
    assert _detect_remote("Engineer", "This role can be worked remote from anywhere in AU...", "Melbourne") is True


def test_detect_remote_truncated_snippet_hybrid_vetoes():
    assert _detect_remote("Engineer", "Hybrid role, 2 days remote per week.", "Melbourne") is False


def test_detect_remote_truncated_snippet_negation_vetoes():
    assert _detect_remote("Engineer", "No remote work available for this role.", "Melbourne") is False


def test_detect_remote_description_positive_phrases():
    assert _detect_remote("Engineer", "This is a 100% remote position.", "Sydney") is True
    assert _detect_remote("Engineer", "We are a remote-first company.", "Sydney") is True
    assert _detect_remote("Engineer", "You can work from home.", "Sydney") is True


def test_detect_remote_false_when_not_present():
    assert _detect_remote("Software Engineer", "Office-based role.", "Sydney") is False


def test_parse_location_full():
    loc = _parse_location(
        {
            "display_name": "Sydney, New South Wales",
            "area": ["Australia", "New South Wales", "Sydney"],
        }
    )
    assert loc.city == "Sydney"
    assert loc.region == "New South Wales"
    assert loc.country == "AU"
    assert loc.raw == "Sydney, New South Wales"


def test_parse_location_country_only():
    loc = _parse_location({"display_name": "Australia", "area": ["Australia"]})
    assert loc.city is None
    assert loc.region is None
    assert loc.country == "AU"


def test_parse_location_empty():
    loc = _parse_location({})
    assert loc.city is None
    assert loc.region is None
    assert loc.country is None
    assert loc.raw == ""


def test_parse_employment_fulltime():
    raw = {"contract_type": "permanent", "contract_time": "full_time"}
    assert _parse_employment(raw) == "full_time"


def test_parse_employment_parttime():
    raw = {"contract_type": "permanent", "contract_time": "part_time"}
    assert _parse_employment(raw) == "part_time"


def test_parse_employment_contract():
    raw = {"contract_type": "contract", "contract_time": "full_time"}
    assert _parse_employment(raw) == "contract"


def test_parse_employment_temp():
    raw = {"contract_type": "temporary", "contract_time": "full_time"}
    assert _parse_employment(raw) == "temp"


def test_parse_employment_unknown():
    raw = {"contract_type": "unknown_type", "contract_time": "full_time"}
    assert _parse_employment(raw) is None


def test_parse_employment_missing_fields():
    assert _parse_employment({}) is None


def test_parse_posted_at_valid():
    assert _parse_posted_at("2026-07-01T10:00:00Z") == "2026-07-01"


def test_parse_posted_at_none():
    assert _parse_posted_at(None) is None


def test_parse_posted_at_invalid():
    assert _parse_posted_at("not-a-date") is None


# ---------------------------------------------------------------------------
# normalize() tests
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> AdzunaAdapter:
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


def test_normalize_company_missing_stays_empty(adapter):
    """Never guess: a missing company is empty, not "Unknown"."""
    raw = {**_SENIOR_LISTING, "company": {}}
    listing = adapter.normalize(raw)
    assert listing.company == ""


def test_normalize_unknown_companies_never_share_ids(adapter):
    """Two unknown-company roles with the same title+location get distinct ids."""
    a = adapter.normalize({**_SENIOR_LISTING, "company": {}, "id": 111})
    b = adapter.normalize({**_SENIOR_LISTING, "company": {}, "id": 222})
    assert a.id != b.id


def test_normalize_injected_run_date_used_for_first_seen(adapter):
    adapter.run_date = "2026-01-15"
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.first_seen_at == "2026-01-15"

def test_normalize_description_html_stripped(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert "<" not in listing.description
    assert "platform team" in listing.description


def test_normalize_location_city(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.location.city == "Sydney"
    assert listing.location.region == "New South Wales"
    assert listing.location.country == "AU"


def test_normalize_location_country_only(adapter):
    listing = adapter.normalize(_REMOTE_CONTRACT_LISTING)
    assert listing.location.city is None
    assert listing.location.country == "AU"


def test_normalize_salary_annual(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.salary is not None
    assert listing.salary.min == 130000.0
    assert listing.salary.max == 170000.0
    assert listing.salary.currency == "AUD"
    assert listing.salary.period == "year"


def test_normalize_salary_none_when_absent(adapter):
    listing = adapter.normalize(_REMOTE_CONTRACT_LISTING)
    assert listing.salary is None


def test_normalize_predicted_salary_sets_raw(adapter):
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    assert listing.salary is not None
    assert listing.salary.raw is not None
    assert "predicted" in listing.salary.raw


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


def test_normalize_seniority_staff(adapter):
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.track == "ic"
    assert listing.seniority.level == "staff"


def test_normalize_seniority_manager(adapter):
    listing = adapter.normalize(_MANAGER_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.track == "management"
    assert listing.seniority.level == "manager"


def test_normalize_employment_fulltime(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.employment == "full_time"


def test_normalize_employment_contract(adapter):
    listing = adapter.normalize(_REMOTE_CONTRACT_LISTING)
    assert listing.employment == "contract"


def test_normalize_remote_from_title(adapter):
    listing = adapter.normalize(_REMOTE_CONTRACT_LISTING)
    assert listing.location.is_remote is True


def test_normalize_remote_from_location_string():
    adapter = _make_adapter()
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    # location.raw == "Remote" -> should detect is_remote=True
    assert listing.location.is_remote is True


def test_normalize_not_remote_for_office_role(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    # "Sydney, New South Wales" with no "remote" keyword
    assert listing.location.is_remote is False


def test_normalize_posted_at(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.posted_at == "2026-07-01"


def test_normalize_first_seen_at_today(adapter):
    from datetime import date

    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.first_seen_at == date.today().isoformat()


def test_normalize_source_name(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert len(listing.sources) == 1
    assert listing.sources[0].name == "adzuna"


def test_normalize_source_url(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].url == "https://www.adzuna.com.au/jobs/details/1111111111"


def test_normalize_source_id(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].source_id == "1111111111"


def test_normalize_id_is_stable(adapter):
    """Same input must produce the same id every time."""
    id1 = adapter.normalize(_SENIOR_LISTING).id
    id2 = adapter.normalize(_SENIOR_LISTING).id
    assert id1 == id2
    assert len(id1) == 64  # SHA-256 hex


def test_normalize_content_hash_is_stable(adapter):
    h1 = adapter.normalize(_SENIOR_LISTING).content_hash
    h2 = adapter.normalize(_SENIOR_LISTING).content_hash
    assert h1 == h2


def test_normalize_content_hash_changes_with_salary(adapter):
    import copy

    modified = copy.deepcopy(_SENIOR_LISTING)
    modified["salary_max"] = 200000.0
    h_orig = adapter.normalize(_SENIOR_LISTING).content_hash
    h_new = adapter.normalize(modified).content_hash
    assert h_orig != h_new


def test_normalize_content_hash_stable_across_source_change(adapter):
    """Changing the source URL must NOT change content_hash (volatile field)."""
    import copy

    modified = copy.deepcopy(_SENIOR_LISTING)
    modified["redirect_url"] = "https://www.adzuna.com.au/jobs/details/different-url"
    h_orig = adapter.normalize(_SENIOR_LISTING).content_hash
    h_new = adapter.normalize(modified).content_hash
    assert h_orig == h_new


def test_normalize_id_unaffected_by_posted_date(adapter):
    """Changing created date must NOT change id (volatile field)."""
    import copy

    modified = copy.deepcopy(_SENIOR_LISTING)
    modified["created"] = "2020-01-01T00:00:00Z"
    id_orig = adapter.normalize(_SENIOR_LISTING).id
    id_new = adapter.normalize(modified).id
    assert id_orig == id_new


# ---------------------------------------------------------------------------
# search() tests — httpx is mocked, no live network
# ---------------------------------------------------------------------------


def test_search_returns_raw_listings(adapter):
    mock_resp = _mock_response(_ADZUNA_SEARCH_RESPONSE)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        results = adapter.search("python developer", "Australia", max_results=10)

    assert len(results) == 2
    assert results[0]["id"] == "1111111111"
    assert results[1]["id"] == "2222222222"


def test_search_respects_max_results(adapter):
    """max_results < page_size: only that many results returned."""
    response = {
        "count": 50,
        "results": [_SENIOR_LISTING] * 5,
    }
    mock_resp = _mock_response(response)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        results = adapter.search("python", "Sydney", max_results=3)

    assert len(results) == 3


def test_search_empty_results(adapter):
    mock_resp = _mock_response({"count": 0, "results": []})
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        results = adapter.search("obscure-keyword", "nowhere", max_results=50)

    assert results == []


def test_search_paginates_when_needed():
    """When one page isn't enough, search issues a second request.

    _PAGE_SIZE is patched to 2 so that page1 returning 2 items looks like a
    full page (triggers pagination) and page2 returning 1 item looks like the
    last page (stops).
    """
    page1 = {"count": 3, "results": [_SENIOR_LISTING, _JUNIOR_LISTING]}
    page2 = {"count": 3, "results": [_REMOTE_CONTRACT_LISTING]}

    responses = [_mock_response(page1), _mock_response(page2)]
    with (
        patch("jobhunter.adapters.adzuna._PAGE_SIZE", 2),
        patch("httpx.Client") as mock_client_cls,
        patch.dict(os.environ, {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}),
    ):
        adapter_local = AdzunaAdapter(country="au")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = responses
        mock_client_cls.return_value = mock_client

        results = adapter_local.search("python", "Australia", max_results=10)

    assert len(results) == 3
    assert mock_client.get.call_count == 2


def test_search_raises_on_http_error(adapter):
    """HTTP errors (auth, rate limit) propagate to the caller."""
    import httpx as _httpx

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock()
    )
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with pytest.raises(_httpx.HTTPStatusError):
            adapter.search("python", "Sydney", max_results=10)


def test_adapter_name():
    adapter = _make_adapter()
    assert adapter.name == "adzuna"


def test_adapter_currency_au():
    adapter = _make_adapter()
    assert adapter._currency == "AUD"


def test_adapter_currency_us():
    with patch.dict(os.environ, {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}):
        adapter_us = AdzunaAdapter(country="us")
    assert adapter_us._currency == "USD"


def test_adapter_explicit_credentials_no_env():
    """Explicit app_id/app_key args work with NO env vars set (cli.py's call path)."""
    excluded = {"ADZUNA_APP_ID", "ADZUNA_APP_KEY"}
    clean_env = {k: v for k, v in os.environ.items() if k not in excluded}
    with patch.dict(os.environ, clean_env, clear=True):
        a = AdzunaAdapter(app_id="explicit_id", app_key="explicit_key")
        assert a.app_id == "explicit_id" and a.app_key == "explicit_key"


def test_adapter_unknown_country_currency_none():
    """Unknown country code => currency None (unknown-salary policy), never a guess."""
    env = {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}
    with patch.dict(os.environ, env):
        a = AdzunaAdapter(country="zz")
        assert a._currency is None


def test_adapter_missing_credentials_raises():
    """Missing env vars must raise KeyError at construction time."""
    excluded = {"ADZUNA_APP_ID", "ADZUNA_APP_KEY"}
    clean_env = {k: v for k, v in os.environ.items() if k not in excluded}
    with patch.dict(os.environ, clean_env, clear=True):
        with pytest.raises(KeyError):
            AdzunaAdapter()
