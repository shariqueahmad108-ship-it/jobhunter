# SPDX-License-Identifier: Apache-2.0
"""Tests for the Remotive source adapter.

All tests use recorded fixture payloads — no live network calls.
httpx is mocked via unittest.mock.

See: specs/02-functional-spec.md §Stage 1-2
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobhunter.adapters.remotive import (
    RemotiveAdapter,
    _parse_location_remotive,
    _parse_posted_at,
)
from jobhunter.model import JobListing

# ---------------------------------------------------------------------------
# Fixture payloads — representative Remotive API response shapes
# ---------------------------------------------------------------------------

_SENIOR_LISTING: dict = {
    "id": 100001,
    "url": "https://remotive.com/remote-jobs/software-dev/senior-engineer-100001",
    "title": "Senior Software Engineer",
    "company_name": "RemoteCo",
    "company_logo": "https://remotive.com/company-logo.png",
    "category": "Software Development",
    "job_type": "full_time",
    "publication_date": "2026-07-01T10:00:00Z",
    "candidate_required_location": "Worldwide",
    "salary": "$130,000 - $170,000",
    "description": "<p>Join our <strong>distributed team</strong> to build scalable systems.</p>",
    "tags": ["python", "aws", "remote"],
}

_CONTRACT_LISTING: dict = {
    "id": 100002,
    "url": "https://remotive.com/remote-jobs/devops/cloud-engineer-100002",
    "title": "Cloud Infrastructure Engineer (Contract)",
    "company_name": "CloudFirm",
    "company_logo": "",
    "category": "DevOps / Sysadmin",
    "job_type": "contract",
    "publication_date": "2026-07-10T08:30:00Z",
    "candidate_required_location": "USA Only",
    "salary": "",
    "description": "Contract cloud role. AWS experience required.",
    "tags": ["aws", "terraform"],
}

_NO_COMPANY_LISTING: dict = {
    "id": 100003,
    "url": "https://remotive.com/remote-jobs/engineering/staff-100003",
    "title": "Staff Engineer",
    "company_name": "",
    "company_logo": "",
    "category": "Software Development",
    "job_type": "full_time",
    "publication_date": "2026-07-15T14:00:00Z",
    "candidate_required_location": "Europe",
    "salary": "€120,000/year",
    "description": "Lead architecture across multiple teams.",
    "tags": ["architecture", "leadership"],
}

_INTERN_LISTING: dict = {
    "id": 100004,
    "url": "https://remotive.com/remote-jobs/engineering/intern-100004",
    "title": "Junior Developer Intern",
    "company_name": "StartupXYZ",
    "company_logo": "",
    "category": "Software Development",
    "job_type": "internship",
    "publication_date": "2026-07-18T09:00:00Z",
    "candidate_required_location": "",
    "salary": None,
    "description": "Internship opportunity for recent graduates.",
    "tags": ["python", "junior"],
}

_REMOTIVE_RESPONSE: dict = {
    "job-count": 2,
    "jobs": [_SENIOR_LISTING, _CONTRACT_LISTING],
}


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def _make_adapter(categories: list[str] | None = None) -> RemotiveAdapter:
    return RemotiveAdapter(categories=categories)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


def test_parse_posted_at_valid():
    assert _parse_posted_at("2026-07-01T10:00:00Z") == "2026-07-01"


def test_parse_posted_at_none():
    assert _parse_posted_at(None) is None


def test_parse_posted_at_invalid():
    assert _parse_posted_at("not-a-date") is None


def test_parse_location_remotive_worldwide():
    loc = _parse_location_remotive("Worldwide")
    assert loc.is_remote is True
    assert loc.raw == "Worldwide"


def test_parse_location_remotive_usa_only():
    loc = _parse_location_remotive("USA Only")
    assert loc.is_remote is True
    assert loc.country == "US"
    assert loc.raw == "USA Only"  # raw is preserved verbatim


def test_parse_location_remotive_europe():
    loc = _parse_location_remotive("Europe")
    assert loc.is_remote is True
    assert loc.country == "EMEA"


def test_parse_location_remotive_empty():
    loc = _parse_location_remotive("")
    assert loc.is_remote is True
    assert loc.raw == ""


def test_parse_location_remotive_australia():
    loc = _parse_location_remotive("Australia")
    assert loc.is_remote is True
    assert loc.country == "AU"


# ---------------------------------------------------------------------------
# normalize() tests
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter() -> RemotiveAdapter:
    return _make_adapter()


def test_normalize_returns_job_listing(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert isinstance(listing, JobListing)


def test_normalize_title(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.title == "Senior Software Engineer"


def test_normalize_company(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.company == "RemoteCo"


def test_normalize_description_html_stripped(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert "<" not in listing.description
    assert "distributed team" in listing.description


def test_normalize_is_remote_always_true(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.location.is_remote is True


def test_normalize_location_worldwide(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.location.raw == "Worldwide"
    assert listing.location.country is None


def test_normalize_location_usa_only(adapter):
    listing = adapter.normalize(_CONTRACT_LISTING)
    assert listing.location.is_remote is True
    assert listing.location.country == "US"


def test_normalize_location_europe(adapter):
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    assert listing.location.is_remote is True
    assert listing.location.country == "EMEA"


def test_normalize_salary_parsed(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.salary is not None
    assert listing.salary.min == 130000.0
    assert listing.salary.max == 170000.0


def test_normalize_salary_empty_string_gives_none(adapter):
    listing = adapter.normalize(_CONTRACT_LISTING)
    assert listing.salary is None


def test_normalize_salary_none_field_gives_none(adapter):
    listing = adapter.normalize(_INTERN_LISTING)
    assert listing.salary is None


def test_normalize_employment_full_time(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.employment == "full_time"


def test_normalize_employment_contract(adapter):
    listing = adapter.normalize(_CONTRACT_LISTING)
    assert listing.employment == "contract"


def test_normalize_employment_internship(adapter):
    listing = adapter.normalize(_INTERN_LISTING)
    assert listing.employment == "internship"


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
    assert listing.sources[0].name == "remotive"


def test_normalize_source_url(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].url == _SENIOR_LISTING["url"]


def test_normalize_source_id(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.sources[0].source_id == "100001"


def test_normalize_seniority_senior(adapter):
    listing = adapter.normalize(_SENIOR_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.level == "senior"


def test_normalize_seniority_staff(adapter):
    listing = adapter.normalize(_NO_COMPANY_LISTING)
    assert listing.seniority is not None
    assert listing.seniority.level == "staff"


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
    """Unknown-company listings with different source_ids get distinct ids."""
    a = adapter.normalize({**_NO_COMPANY_LISTING, "id": 9001})
    b = adapter.normalize({**_NO_COMPANY_LISTING, "id": 9002})
    assert a.id != b.id


def test_normalize_falls_back_to_location_when_no_candidate_location(adapter):
    """Falls back to 'location' field when 'candidate_required_location' is absent."""
    raw = {**_SENIOR_LISTING}
    raw.pop("candidate_required_location", None)
    raw["location"] = "Worldwide"
    listing = adapter.normalize(raw)
    assert listing.location.raw == "Worldwide"
    assert listing.location.is_remote is True


# ---------------------------------------------------------------------------
# search() tests — httpx is mocked, no live network
# ---------------------------------------------------------------------------


def test_search_returns_raw_listings():
    adapter = _make_adapter()
    mock_resp = _mock_response(_REMOTIVE_RESPONSE)
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        results = adapter.search("", "", max_results=50)

    assert len(results) == 2
    assert results[0]["id"] == 100001
    assert results[1]["id"] == 100002


def test_search_injects_run_date():
    adapter = _make_adapter()
    adapter.run_date = "2026-07-23"
    mock_resp = _mock_response(_REMOTIVE_RESPONSE)
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        results = adapter.search("", "", max_results=50)

    assert all(r.get("_run_date") == "2026-07-23" for r in results)


def test_search_single_category_passes_param():
    adapter = _make_adapter(categories=["software-dev"])
    mock_resp = _mock_response({"job-count": 1, "jobs": [_SENIOR_LISTING]})
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        mock_cls.return_value = mock_client

        results = adapter.search("", "", max_results=50)

    assert len(results) == 1
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[1]["params"]["category"] == "software-dev"


def test_search_multiple_categories_issues_one_request_each():
    adapter = _make_adapter(categories=["software-dev", "devops-sysadmin"])
    resp1 = _mock_response({"job-count": 1, "jobs": [_SENIOR_LISTING]})
    resp2 = _mock_response({"job-count": 1, "jobs": [_CONTRACT_LISTING]})
    with patch("httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [resp1, resp2]
        mock_cls.return_value = mock_client

        results = adapter.search("", "", max_results=50)

    assert len(results) == 2
    assert mock_client.get.call_count == 2


def test_search_empty_response():
    adapter = _make_adapter()
    mock_resp = _mock_response({"job-count": 0, "jobs": []})
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

    adapter = _make_adapter()
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


def test_adapter_name():
    assert RemotiveAdapter().name == "remotive"


def test_adapter_is_query_independent():
    assert RemotiveAdapter.query_independent is True


def test_adapter_no_categories_default():
    assert RemotiveAdapter().categories == []


def test_adapter_categories_stored():
    a = RemotiveAdapter(categories=["software-dev", "devops-sysadmin"])
    assert a.categories == ["software-dev", "devops-sysadmin"]


# ---------------------------------------------------------------------------
# Profile validation tests (queries.remotive block)
# ---------------------------------------------------------------------------


def test_profile_remotive_enabled_valid():
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
            "remotive": {"enabled": True, "categories": ["software-dev"]},
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
        profile = load_profile(path)
        assert profile["queries"]["remotive"]["enabled"] is True
        assert profile["queries"]["remotive"]["categories"] == ["software-dev"]
    finally:
        os.unlink(path)


def test_profile_remotive_disabled_valid():
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
            "remotive": {"enabled": False},
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
        profile = load_profile(path)
        assert profile["queries"]["remotive"]["enabled"] is False
    finally:
        os.unlink(path)


def test_profile_remotive_invalid_enabled_type():
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
            "remotive": {"enabled": "yes"},  # string, not bool
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


def test_profile_remotive_unknown_key_rejected():
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
            "remotive": {"enabled": True, "api_key": "secret"},  # unknown key
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
# End-to-end: enabled remotive adapter appears in sources_used
# ---------------------------------------------------------------------------


def test_end_to_end_remotive_appears_in_sources_used():
    """A pipeline run with RemotiveAdapter produces 'remotive' in sources_used."""
    from jobhunter.pipeline import run as pipeline_run

    adapter = RemotiveAdapter()
    mock_resp = _mock_response({"job-count": 1, "jobs": [_SENIOR_LISTING]})

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

    assert "remotive" in report.sources_used


def test_end_to_end_remotive_disabled_not_in_sources_used():
    """When RemotiveAdapter is not in the adapter list, 'remotive' is absent from sources_used."""
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
    assert "remotive" not in report.sources_used
