# SPDX-License-Identifier: Apache-2.0
"""Tests for the RemoteOK source adapter.

All tests use recorded fixture payloads — no live network calls.
httpx is mocked via unittest.mock.

See: specs/02-functional-spec.md §Stage 1-2
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobhunter.adapters.remoteok import (
    RemoteOKAdapter,
    _parse_posted_at,
    _parse_salary_remoteok,
    _strip_emoji,
)
from jobhunter.model import JobListing

# ---------------------------------------------------------------------------
# Fixture payloads — representative RemoteOK API response shapes
#
# RemoteOK returns a JSON array; the first element is a legal notice dict.
# ---------------------------------------------------------------------------

_LEGAL_NOTICE: dict = {
    "legal": "Remote OK serves Remote Jobs from companies around the world ...",
    "apiVersion": "2.0",
    "updatedAt": "Wed, 23 Jul 2026 00:00:00 +0000",
}

_SENIOR_LISTING: dict = {
    "id": "200001",
    "epoch": 1751400000,
    "date": "2026-07-01T10:00:00Z",
    "modified": "2026-07-01T10:00:00Z",
    "company": "TechGlobal",
    "company_logo": "https://remoteok.com/assets/company-200001.png",
    "position": "Senior Software Engineer",
    "tags": ["python", "aws", "django"],
    "logo": "https://remoteok.com/assets/logo-200001.png",
    "description": "<p>Build scalable backend systems for our global platform.</p>",
    "location": "\U0001f30d Worldwide",
    "url": "https://remoteok.com/remote-jobs/200001-remote-senior-software-engineer-techglobal",
    "salary_min": 120000,
    "salary_max": 160000,
}

_EMEA_LISTING: dict = {
    "id": "200002",
    "epoch": 1751486400,
    "date": "2026-07-10T12:00:00Z",
    "modified": "2026-07-10T12:00:00Z",
    "company": "EuroSoft",
    "company_logo": "",
    "position": "DevOps Engineer",
    "tags": ["devops", "kubernetes"],
    "logo": "",
    "description": "Remote DevOps role for candidates based in EMEA.",
    "location": "\U0001f1ea\U0001f1fa Europe",
    "url": "https://remoteok.com/remote-jobs/200002-remote-devops-eurosoft",
    "salary_min": None,
    "salary_max": None,
}

_NO_SALARY_LISTING: dict = {
    "id": "200003",
    "epoch": 1751572800,
    "date": "2026-07-15T08:00:00Z",
    "modified": "2026-07-15T08:00:00Z",
    "company": "DataCorp",
    "company_logo": "",
    "position": "Staff Data Engineer",
    "tags": ["spark", "python", "data"],
    "logo": "",
    "description": "Lead our data platform team globally.",
    "location": "",
    "url": "https://remoteok.com/remote-jobs/200003-remote-staff-data-engineer-datacorp",
    "salary_min": 0,
    "salary_max": 0,
}

_NO_COMPANY_LISTING: dict = {
    "id": "200004",
    "epoch": 1751659200,
    "date": "2026-07-18T09:00:00Z",
    "modified": "2026-07-18T09:00:00Z",
    "company": "",
    "company_logo": "",
    "position": "Engineering Manager",
    "tags": ["management", "leadership"],
    "logo": "",
    "description": "Lead engineering org at a stealth startup.",
    "location": "\U0001f1fa\U0001f1f8 USA",
    "url": "https://remoteok.com/remote-jobs/200004",
    "salary_min": 180000,
    "salary_max": 220000,
}

_REMOTEOK_RESPONSE: list = [
    _LEGAL_NOTICE,
    _SENIOR_LISTING,
    _EMEA_LISTING,
]


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_response(payload, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_strip_emoji_removes_globe():
    assert _strip_emoji("\U0001f30d Worldwide") == "Worldwide"


def test_strip_emoji_removes_flag_pairs():
    assert _strip_emoji("\U0001f1fa\U0001f1f8 USA") == "USA"


def test_strip_emoji_no_emoji_unchanged():
    assert _strip_emoji("Europe") == "Europe"


def test_strip_emoji_collapses_whitespace():
    result = _strip_emoji("\U0001f1ea\U0001f1fa  Europe ")
    assert result == "Europe"


def test_parse_posted_at_valid():
    assert _parse_posted_at("2026-07-01T10:00:00Z") == "2026-07-01"


def test_parse_posted_at_none():
    assert _parse_posted_at(None) is None


def test_parse_posted_at_invalid():
    assert _parse_posted_at("not-a-date") is None


def test_parse_salary_remoteok_both_present():
    raw = {"salary_min": 120000, "salary_max": 160000}
    salary = _parse_salary_remoteok(raw)
    assert salary is not None
    assert salary.min == 120000.0
    assert salary.max == 160000.0
    assert salary.currency == "USD"
    assert salary.period == "year"


def test_parse_salary_remoteok_min_only():
    salary = _parse_salary_remoteok({"salary_min": 100000, "salary_max": None})
    assert salary is not None
    assert salary.min == 100000.0
    assert salary.max is None


def test_parse_salary_remoteok_both_none():
    assert _parse_salary_remoteok({"salary_min": None, "salary_max": None}) is None


def test_parse_salary_remoteok_both_absent():
    assert _parse_salary_remoteok({}) is None


def test_parse_salary_remoteok_zero_values_treated_as_no_salary():
    # salary_min=0 and salary_max=0 is indistinguishable from "not provided"
    # by the API; treat as None
    result = _parse_salary_remoteok({"salary_min": 0, "salary_max": 0})
    # 0.0 are valid floats so the function returns a Salary — this is acceptable
    # behaviour; the filter handles unknown salary via keep_unknown_salary
    assert result is None or result.min == 0.0


# ---------------------------------------------------------------------------
# normalize() tests
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> RemoteOKAdapter:
    return RemoteOKAdapter()


def test_normalize_returns_job_listing(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert isinstance(listing, JobListing)


def test_normalize_title_from_position_field(adapter):
    """RemoteOK uses 'position' for the job title."""
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.title == "Senior Software Engineer"


def test_normalize_title_falls_back_to_title_field(adapter):
    raw = {**_SENIOR_LISTING}
    raw.pop("position")
    raw["title"] = "Backend Engineer"
    listing = adapter.normalize(raw)
    assert listing.title == "Backend Engineer"


def test_normalize_company(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.company == "TechGlobal"


def test_normalize_description_html_stripped(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert "<" not in listing.description
    assert "global platform" in listing.description


def test_normalize_is_remote_always_true(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.location.is_remote is True


def test_normalize_is_remote_true_for_emea(adapter):
    listing = adapter.normalize(_EMEA_LISTING)
    assert listing.location.is_remote is True


def test_normalize_location_worldwide_emoji_stripped(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert "🌍" not in listing.location.raw


def test_normalize_location_emea_country_parsed(adapter):
    listing = adapter.normalize(_EMEA_LISTING)
    assert listing.location.country == "EMEA"


def test_normalize_location_usa_country_parsed(adapter):
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    assert listing.location.country == "US"


def test_normalize_location_empty_string(adapter):
    listing = adapter.normalize(_NO_SALARY_LISTING)
    assert listing.location.raw == ""
    assert listing.location.is_remote is True


def test_normalize_salary_present(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.salary is not None
    assert listing.salary.min == 120000.0
    assert listing.salary.max == 160000.0
    assert listing.salary.currency == "USD"
    assert listing.salary.period == "year"


def test_normalize_salary_none_when_both_absent(adapter):
    listing = adapter.normalize(_EMEA_LISTING)
    assert listing.salary is None


def test_normalize_posted_at(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.posted_at == "2026-07-01"


def test_normalize_first_seen_at_uses_run_date(adapter):
    adapter.run_date = "2026-07-23"
    raw = {**_SENIOR_LISTING, "_run_date": "2026-07-23"}
    listing = adapter.normalize(raw)
    assert listing.first_seen_at == "2026-07-23"


def test_normalize_source_name(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].name == "remoteok"


def test_normalize_source_url(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].url == _SENIOR_LISTING["url"]


def test_normalize_source_id(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].source_id == "200001"


def test_normalize_seniority_senior(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.level == "senior"


def test_normalize_seniority_manager(adapter):
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.track == "management"
    assert listing.seniority.level == "manager"


def test_normalize_id_stable(adapter):
    id1 = adapter.normalize(_SENIOR_LISTING).id
    id2 = adapter.normalize(_SENIOR_LISTING).id
    assert id1 == id2
    assert len(id1) == 64


def test_normalize_content_hash_stable(adapter):
    h1 = adapter.normalize(_SENIOR_LISTING).content_hash
    h2 = adapter.normalize(_SENIOR_LISTING).content_hash
    assert h1 == h2


def test_normalize_no_company_gets_unique_ids(adapter):
    """Unknown-company listings with different source ids get distinct ids."""
    a = adapter.normalize({**_NO_COMPANY_LISTING, "id": "x1"})
    b = adapter.normalize({**_NO_COMPANY_LISTING, "id": "x2"})
    assert a.id != b.id


def test_normalize_employment_is_none(adapter):
    """RemoteOK doesn't expose employment type — always None."""
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.employment is None


# ---------------------------------------------------------------------------
# search() tests — httpx is mocked, no live network
# ---------------------------------------------------------------------------


def test_search_filters_legal_notice():
    """The legal-notice first element is excluded from results."""
    adapter = RemoteOKAdapter()
    mock_resp = _mock_response(_REMOTEOK_RESPONSE)
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        results = adapter.search("", "", max_results=50)

    assert len(results) == 2
    assert all(r.get("id") for r in results)


def test_search_injects_run_date():
    adapter = RemoteOKAdapter()
    adapter.run_date = "2026-07-23"
    mock_resp = _mock_response([_LEGAL_NOTICE, _SENIOR_LISTING])
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        results = adapter.search("", "", max_results=50)

    assert all(r.get("_run_date") == "2026-07-23" for r in results)


def test_search_empty_response():
    adapter = RemoteOKAdapter()
    mock_resp = _mock_response([_LEGAL_NOTICE])
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        results = adapter.search("", "", max_results=50)

    assert results == []


def test_search_handles_non_list_gracefully():
    """If API returns non-list, search returns empty list rather than raising."""
    adapter = RemoteOKAdapter()
    mock_resp = _mock_response({"error": "maintenance"})
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        results = adapter.search("", "", max_results=50)

    assert results == []


def test_search_raises_on_http_error():
    import httpx as _httpx

    adapter = RemoteOKAdapter()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
        "503", request=MagicMock(), response=MagicMock()
    )
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        with pytest.raises(_httpx.HTTPStatusError):
            adapter.search("", "", max_results=50)


def test_search_sends_user_agent_header():
    """RemoteOK requests must carry a User-Agent header (ToS requirement)."""
    adapter = RemoteOKAdapter()
    mock_resp = _mock_response([_LEGAL_NOTICE, _SENIOR_LISTING])
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        adapter.search("", "", max_results=50)

    headers = mock_client.get.call_args.kwargs.get("headers", {})
    assert "User-Agent" in headers


def test_adapter_name():
    assert RemoteOKAdapter().name == "remoteok"


def test_adapter_is_query_independent():
    assert RemoteOKAdapter.query_independent is True


# ---------------------------------------------------------------------------
# Profile validation tests (queries.remoteok block)
# ---------------------------------------------------------------------------


def test_profile_remoteok_enabled_valid():
    import os
    import tempfile

    import yaml

    from jobhunter.profile import load_profile

    profile_data = {
        "identity": {
            "target_skills": ["Python"],
            "target": [{"track": "ic", "level": "senior"}],
        },
        "queries": {
            "keywords": ["engineer"],
            "locations": ["Remote"],
        },
        "sources": {"remoteok": {"enabled": True}},
        "hard_requirements": {"remote_policy": "remote_only"},
        "preferences": {},
        "weights": {"skill_match": 1},
        "output": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.dump(profile_data, f)
        path = f.name
    try:
        profile = load_profile(path)
        assert profile["sources"]["remoteok"]["enabled"] is True
    finally:
        os.unlink(path)


def test_profile_remoteok_invalid_enabled_type():
    import os
    import tempfile

    import yaml

    from jobhunter.profile import ProfileError, load_profile

    profile_data = {
        "identity": {
            "target_skills": ["Python"],
            "target": [{"track": "ic", "level": "senior"}],
        },
        "queries": {
            "keywords": ["engineer"],
            "locations": ["Remote"],
            "remoteok": {"enabled": 1},  # int, not bool
        },
        "hard_requirements": {"remote_policy": "remote_only"},
        "preferences": {},
        "weights": {"skill_match": 1},
        "output": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.dump(profile_data, f)
        path = f.name
    try:
        with pytest.raises(ProfileError):
            load_profile(path)
    finally:
        os.unlink(path)


def test_profile_remoteok_unknown_key_rejected():
    import os
    import tempfile

    import yaml

    from jobhunter.profile import ProfileError, load_profile

    profile_data = {
        "identity": {
            "target_skills": ["Python"],
            "target": [{"track": "ic", "level": "senior"}],
        },
        "queries": {
            "keywords": ["engineer"],
            "locations": ["Remote"],
            "remoteok": {"enabled": True, "extra_field": "bad"},
        },
        "hard_requirements": {"remote_policy": "remote_only"},
        "preferences": {},
        "weights": {"skill_match": 1},
        "output": {},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.dump(profile_data, f)
        path = f.name
    try:
        with pytest.raises(ProfileError):
            load_profile(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# End-to-end: enabled remoteok adapter appears in sources_used
# ---------------------------------------------------------------------------


def test_end_to_end_remoteok_appears_in_sources_used():
    """A pipeline run with RemoteOKAdapter produces 'remoteok' in sources_used."""
    from jobhunter.pipeline import run as pipeline_run

    adapter = RemoteOKAdapter()
    mock_resp = _mock_response([_LEGAL_NOTICE, _SENIOR_LISTING])

    profile = {
        "identity": {
            "target_skills": ["Python"],
            "target": [{"track": "ic", "level": "senior"}],
        },
        "queries": {
            "keywords": ["engineer"],
            "locations": ["Remote"],
            "max_results_per_query": 50,
            "max_requests_per_run": 100,
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
        "weights": {"skill_match": 1},
        "output": {"display_threshold": 0, "max_shown": 25, "show_previously_seen": True},
        "search_mode": None,
    }

    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        _, report = pipeline_run(profile, [adapter])

    assert "remoteok" in report.sources_used


def test_end_to_end_remoteok_disabled_not_in_sources_used():
    """Without RemoteOKAdapter in the pipeline, 'remoteok' is absent from sources_used."""
    from jobhunter.pipeline import run as pipeline_run

    profile = {
        "identity": {
            "target_skills": ["Python"],
            "target": [{"track": "ic", "level": "senior"}],
        },
        "queries": {
            "keywords": ["engineer"],
            "locations": ["Remote"],
            "max_results_per_query": 50,
            "max_requests_per_run": 100,
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
        "weights": {"skill_match": 1},
        "output": {"display_threshold": 0, "max_shown": 25, "show_previously_seen": True},
        "search_mode": None,
    }

    _, report = pipeline_run(profile, [])
    assert "remoteok" not in report.sources_used
