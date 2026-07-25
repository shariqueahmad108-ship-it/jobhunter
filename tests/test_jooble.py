# SPDX-License-Identifier: Apache-2.0
"""Tests for the Jooble source adapter.

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

from jobhunter.adapters.jooble import (
    JoobleAdapter,
    _parse_employment,
    _parse_posted_at,
)
from jobhunter.model import JobListing

# ---------------------------------------------------------------------------
# Fixture payloads — representative Jooble API response shapes
# ---------------------------------------------------------------------------

_SENIOR_LISTING: dict = {
    "title": "Senior Software Engineer",
    "location": "Sydney, NSW, Australia",
    "snippet": "Join our platform team to build <b>scalable</b> distributed systems.",
    "salary": "$130,000 - $170,000 per annum",
    "source": "seek.com.au",
    "type": "Full-time",
    "link": "https://jooble.org/desc/1111111111",
    "company": "Acme Corp",
    "updated": "2026-07-01T00:00:00.0000000",
    "id": 1111111111,
}

_JUNIOR_LISTING: dict = {
    "title": "Junior Python Developer",
    "location": "Melbourne, VIC, Australia",
    "snippet": "Great opportunity for a junior developer to grow their Python skills.",
    "salary": "",
    "source": "seek.com.au",
    "type": "Full-time",
    "link": "https://jooble.org/desc/2222222222",
    "company": "Startup Co",
    "updated": "2026-07-10T00:00:00.0000000",
    "id": 2222222222,
}

_REMOTE_CONTRACT_LISTING: dict = {
    "title": "Remote Data Engineer (Contract)",
    "location": "Remote, Australia",
    "snippet": "Fully remote contract role. Work from anywhere in Australia.",
    "salary": "$900/day",
    "source": "remote.com",
    "type": "Contract",
    "link": "https://jooble.org/desc/3333333333",
    "company": "DataCo",
    "updated": "2026-07-15T00:00:00.0000000",
    "id": 3333333333,
}

_NO_COMPANY_LISTING: dict = {
    "title": "Staff Engineer",
    "location": "Remote",
    "snippet": "Lead the platform architecture.",
    "salary": "",
    "source": "seek.com.au",
    "type": "",
    "link": "https://jooble.org/desc/5555555555",
    "company": "",
    "updated": "2026-07-20T00:00:00.0000000",
    "id": 5555555555,
}

_BARE_DATE_LISTING: dict = {
    "title": "DevOps Engineer",
    "location": "Sydney, NSW",
    "snippet": "Manage our cloud infrastructure.",
    "salary": "",
    "source": "seek.com.au",
    "type": "Part-time",
    "link": "https://jooble.org/desc/6666666666",
    "company": "CloudCo",
    "updated": "2026-07-18",  # bare date, no time component
    "id": 6666666666,
}

_JOOBLE_PAGE_RESPONSE: dict = {
    "totalCount": 2,
    "jobs": [_SENIOR_LISTING, _JUNIOR_LISTING],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> JoobleAdapter:
    """Return a JoobleAdapter with a fake API key."""
    env = {"JOOBLE_API_KEY": "test_api_key"}
    with patch.dict(os.environ, env):
        return JoobleAdapter()


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


def test_parse_employment_full_time():
    assert _parse_employment({"type": "Full-time"}) == "full_time"


def test_parse_employment_full_time_spaced():
    assert _parse_employment({"type": "Full time"}) == "full_time"


def test_parse_employment_part_time():
    assert _parse_employment({"type": "Part-time"}) == "part_time"


def test_parse_employment_contract():
    assert _parse_employment({"type": "Contract"}) == "contract"


def test_parse_employment_temp():
    assert _parse_employment({"type": "Temporary"}) == "temp"


def test_parse_employment_internship():
    assert _parse_employment({"type": "Internship"}) == "internship"


def test_parse_employment_unknown():
    assert _parse_employment({"type": "something_else"}) is None


def test_parse_employment_missing():
    assert _parse_employment({}) is None


def test_parse_employment_case_insensitive():
    assert _parse_employment({"type": "FULL-TIME"}) == "full_time"


def test_parse_posted_at_iso_datetime_with_subseconds():
    """Jooble's 7-digit sub-second timestamps must parse (Python caps at 6)."""
    assert _parse_posted_at("2026-07-01T00:00:00.0000000") == "2026-07-01"


def test_parse_posted_at_bare_date():
    assert _parse_posted_at("2026-07-18") == "2026-07-18"


def test_parse_posted_at_iso_with_z():
    assert _parse_posted_at("2026-07-01T10:00:00Z") == "2026-07-01"


def test_parse_posted_at_none():
    assert _parse_posted_at(None) is None


def test_parse_posted_at_empty():
    assert _parse_posted_at("") is None


def test_parse_posted_at_unparseable_returns_none():
    assert _parse_posted_at("just now") is None


# ---------------------------------------------------------------------------
# normalize() tests
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> JoobleAdapter:
    return _make_adapter()


def test_normalize_returns_job_listing(adapter):
    assert isinstance(adapter.normalize(_SENIOR_LISTING), JobListing)


def test_normalize_title(adapter):
    assert adapter.normalize(_SENIOR_LISTING).title == "Senior Software Engineer"


def test_normalize_company(adapter):
    assert adapter.normalize(_SENIOR_LISTING).company == "Acme Corp"


def test_normalize_source_name(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert len(listing.sources) == 1
    assert listing.sources[0].name == "jooble"


def test_normalize_source_url_from_link(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].url == "https://jooble.org/desc/1111111111"


def test_normalize_source_id_from_id(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].source_id == "1111111111"


def test_normalize_strips_html_from_snippet(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert "<b>" not in listing.description
    assert "scalable" in listing.description


def test_normalize_employment_from_type(adapter):
    assert adapter.normalize(_REMOTE_CONTRACT_LISTING).employment == "contract"


def test_normalize_posted_at(adapter):
    assert adapter.normalize(_SENIOR_LISTING).posted_at == "2026-07-01"


def test_normalize_salary_parsed(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.salary is not None


def test_normalize_missing_salary_is_none(adapter):
    assert adapter.normalize(_JUNIOR_LISTING).salary is None


def test_normalize_first_seen_uses_run_date():
    env = {"JOOBLE_API_KEY": "x"}
    with patch.dict(os.environ, env):
        a = JoobleAdapter()
    a.run_date = "2026-07-22"
    assert a.normalize(_SENIOR_LISTING).first_seen_at == "2026-07-22"


def test_normalize_first_seen_defaults_to_today(adapter):
    assert adapter.normalize(_SENIOR_LISTING).first_seen_at == date.today().isoformat()


def test_normalize_no_company_still_gets_stable_id(adapter):
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    assert listing.id  # non-empty deterministic id even without a company
    assert listing.company == ""


def test_normalize_is_deterministic(adapter):
    a = adapter.normalize(_SENIOR_LISTING)
    b = adapter.normalize(_SENIOR_LISTING)
    assert a.id == b.id
    assert a.content_hash == b.content_hash


def test_default_currency_threads_through_for_symbolless_salary():
    """default_currency applies to a bare-number salary (no currency symbol).

    A leading "$" is always read as USD by the shared parser, so the constructor
    default only governs symbol-less amounts — the AU market often quotes those.
    """
    listing_raw = {**_SENIOR_LISTING, "salary": "130000 - 170000 per year"}
    env = {"JOOBLE_API_KEY": "x"}
    with patch.dict(os.environ, env):
        a_aud = JoobleAdapter(default_currency="AUD")
        a_none = JoobleAdapter(default_currency=None)
    assert a_aud.normalize(listing_raw).salary.currency == "AUD"
    # With no default, a symbol-less amount carries no assumed currency.
    none_salary = a_none.normalize(listing_raw).salary
    assert none_salary is None or none_salary.currency in (None, "")


# ---------------------------------------------------------------------------
# search() tests
# ---------------------------------------------------------------------------


def _client_mock(get_or_post_return=None, side_effect=None):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    if side_effect is not None:
        mock_client.post.side_effect = side_effect
    else:
        mock_client.post.return_value = get_or_post_return
    return mock_client


def test_search_returns_raw_listings(adapter):
    mock_resp = _mock_response(_JOOBLE_PAGE_RESPONSE)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_mock(mock_resp)
        mock_client_cls.return_value = mock_client
        results = adapter.search("platform engineer", "Remote", max_results=10)

    assert len(results) == 2
    assert results[0]["id"] == 1111111111
    assert results[1]["id"] == 2222222222


def test_search_posts_to_keyed_url_with_json_body(adapter):
    """The API key goes in the URL path and the query goes in the JSON body."""
    mock_resp = _mock_response(_JOOBLE_PAGE_RESPONSE)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_mock(mock_resp)
        mock_client_cls.return_value = mock_client
        adapter.search("platform engineer", "Remote", max_results=5)

    args, kwargs = mock_client.post.call_args
    assert args[0].endswith("/test_api_key")
    assert kwargs["json"]["keywords"] == "platform engineer"
    assert kwargs["json"]["location"] == "Remote"


def test_search_empty_results(adapter):
    mock_resp = _mock_response({"totalCount": 0, "jobs": []})
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_mock(mock_resp)
        mock_client_cls.return_value = mock_client
        results = adapter.search("no-match", "nowhere", max_results=50)

    assert results == []


def test_search_respects_max_results(adapter):
    page = {"totalCount": 20, "jobs": [_SENIOR_LISTING] * 10}
    mock_resp = _mock_response(page)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_mock(mock_resp)
        mock_client_cls.return_value = mock_client
        results = adapter.search("platform engineer", "Remote", max_results=3)

    assert len(results) == 3


def test_search_paginates_when_multiple_pages():
    page1 = {"totalCount": 3, "jobs": [_SENIOR_LISTING, _JUNIOR_LISTING]}
    page2 = {"totalCount": 3, "jobs": [_REMOTE_CONTRACT_LISTING]}
    responses = [_mock_response(page1), _mock_response(page2)]
    env = {"JOOBLE_API_KEY": "x"}
    with (
        patch("jobhunter.adapters.jooble._PAGE_SIZE", 2),
        patch("httpx.Client") as mock_client_cls,
        patch.dict(os.environ, env),
    ):
        adapter_local = JoobleAdapter(page_delay=0.0)
        mock_client = _client_mock(side_effect=responses)
        mock_client_cls.return_value = mock_client
        results = adapter_local.search("platform engineer", "Australia", max_results=10)

    assert len(results) == 3
    assert mock_client.post.call_count == 2


def test_search_stops_when_total_exhausted():
    """A full first page that already covers totalCount issues no second request."""
    page = {"totalCount": 2, "jobs": [_SENIOR_LISTING, _JUNIOR_LISTING]}
    env = {"JOOBLE_API_KEY": "x"}
    with (
        patch("jobhunter.adapters.jooble._PAGE_SIZE", 2),
        patch("httpx.Client") as mock_client_cls,
        patch.dict(os.environ, env),
    ):
        adapter_local = JoobleAdapter(page_delay=0.0)
        mock_client = _client_mock(_mock_response(page))
        mock_client_cls.return_value = mock_client
        results = adapter_local.search("platform engineer", "Australia", max_results=10)

    assert len(results) == 2
    assert mock_client.post.call_count == 1


def test_search_increments_requests_made(adapter):
    mock_resp = _mock_response(_JOOBLE_PAGE_RESPONSE)
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_mock(mock_resp)
        mock_client_cls.return_value = mock_client
        adapter.search("platform engineer", "Remote", max_results=5)

    assert adapter.requests_made == 1


def test_search_raises_on_http_error(adapter):
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.raise_for_status.side_effect = __import__("httpx").HTTPStatusError(
        "403", request=MagicMock(), response=MagicMock(status_code=403)
    )
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_mock(mock_resp)
        mock_client_cls.return_value = mock_client
        with pytest.raises(Exception):
            adapter.search("platform engineer", "Remote", max_results=5)


def test_missing_api_key_raises_keyerror():
    """No api_key arg and no env var → KeyError at construction (fail loud)."""
    env = {k: v for k, v in os.environ.items() if k != "JOOBLE_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(KeyError):
            JoobleAdapter()
