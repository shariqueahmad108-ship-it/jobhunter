# SPDX-License-Identifier: Apache-2.0
"""ATS company job-board adapter.

Fetches all open roles from a watchlist of target companies' ATS job boards.
Supports Greenhouse, Lever, and Ashby — all publicly accessible JSON APIs
that require no authentication for reading public job postings.

The watchlist is configured in profile queries.ats_watchlist.
Unlike keyword-based adapters, keyword and location are ignored; the hard
filter stage applies location/keyword filtering downstream.

Sources used:
  Greenhouse: GET  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  Lever:      GET  https://api.lever.co/v0/postings/{slug}?mode=json
  Ashby:      POST https://jobs.ashbyhq.com/api/non-authed/job-board/jobs

See: specs/04-technical-plan.md §Data sources
     specs/02-functional-spec.md §Stage 1–2
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

import httpx

from jobhunter.model import (
    JobListing,
    Location,
    Salary,
    Seniority,
    Source,
    derive_content_hash,
    derive_id,
    infer_seniority,
)
from jobhunter.normalize import parse_location, strip_html

RawListing = dict[str, Any]

_TIMEOUT = 30.0  # seconds

# ---------------------------------------------------------------------------
# ATS type → source name prefix
# ---------------------------------------------------------------------------

_ATS_SOURCE_PREFIX = {
    "greenhouse": "ats_greenhouse",
    "lever": "ats_lever",
    "ashby": "ats_ashby",
}

# ---------------------------------------------------------------------------
# Employment type mappings
# ---------------------------------------------------------------------------

_LEVER_COMMITMENT_MAP: dict[str, str] = {
    "full-time": "full_time",
    "fulltime": "full_time",
    "part-time": "part_time",
    "parttime": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "internship": "internship",
    "intern": "internship",
    "temporary": "temp",
    "temp": "temp",
}

_ASHBY_EMPLOYMENT_MAP: dict[str, str] = {
    "fulltime": "full_time",
    "full_time": "full_time",
    "parttime": "part_time",
    "part_time": "part_time",
    "contract": "contract",
    "intern": "internship",
    "internship": "internship",
    "temporary": "temp",
}


# ---------------------------------------------------------------------------
# Per-ATS fetch helpers
# ---------------------------------------------------------------------------


def _fetch_greenhouse(slug: str) -> list[RawListing]:
    """Fetch all open jobs from a Greenhouse job board."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(url, params={"content": "true"})
        resp.raise_for_status()
        data = resp.json()
    return data.get("jobs") or []


def _fetch_lever(slug: str) -> list[RawListing]:
    """Fetch all open postings from a Lever job board."""
    url = f"https://api.lever.co/v0/postings/{slug}"
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(url, params={"mode": "json"})
        resp.raise_for_status()
        data = resp.json()
    # Lever returns a list directly
    return data if isinstance(data, list) else []


def _fetch_ashby(slug: str) -> list[RawListing]:
    """Fetch all open job postings from an Ashby job board."""
    url = "https://jobs.ashbyhq.com/api/non-authed/job-board/jobs"
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, json={"organizationHostedJobsPageName": slug})
        resp.raise_for_status()
        data = resp.json()
    return data.get("jobPostings") or []


# ---------------------------------------------------------------------------
# Per-ATS normalize helpers
# ---------------------------------------------------------------------------


def _parse_iso_date(raw: str | None) -> str | None:
    """Parse an ISO 8601 datetime or date string to a YYYY-MM-DD string."""
    if not raw:
        return None
    # Strip trailing Z and parse
    try:
        cleaned = raw.replace("Z", "+00:00").replace(".000+00:00", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt.date().isoformat()
    except (ValueError, AttributeError):
        # Try bare date
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
        return m.group(1) if m else None


def _lever_employment(raw: RawListing) -> str | None:
    """Map Lever categories.commitment to canonical employment type."""
    cats = raw.get("categories") or {}
    commitment = (cats.get("commitment") or "").lower().replace(" ", "")
    return _LEVER_COMMITMENT_MAP.get(commitment)


def _ashby_employment(raw: RawListing) -> str | None:
    """Map Ashby employmentType to canonical employment type."""
    emp = (raw.get("employmentType") or "").lower().replace(" ", "_").rstrip("s")
    # Normalize CamelCase Ashby values: "FullTime" → "fulltime"
    emp_lower = (raw.get("employmentType") or "").lower().replace(" ", "")
    return _ASHBY_EMPLOYMENT_MAP.get(emp_lower)


def _build_listing(
    title: str,
    company: str,
    description_html: str,
    location_raw: str,
    posted_at: str | None,
    employment: str | None,
    source_name: str,
    source_url: str,
    source_id: str,
) -> JobListing:
    """Shared listing construction logic for all three ATS formats."""
    description = strip_html(description_html)
    location = parse_location(location_raw)

    seniority: Seniority | None = infer_seniority(title, description)
    first_seen_at = date.today().isoformat()

    source = Source(
        name=source_name,
        url=source_url,
        source_id=source_id,
    )

    listing = JobListing(
        id="",
        content_hash="",
        title=title,
        company=company,
        location=location,
        description=description,
        sources=[source],
        first_seen_at=first_seen_at,
        salary=None,  # ATS public feeds do not expose salary
        seniority=seniority,
        employment=employment,
        posted_at=posted_at,
    )
    listing.id = derive_id(company, title, location)
    listing.content_hash = derive_content_hash(listing)
    return listing


def _normalize_greenhouse(raw: RawListing) -> JobListing:
    """Normalize a Greenhouse job listing to a canonical JobListing."""
    company = raw.get("_company_name") or "Unknown"
    slug = raw.get("_ats_slug") or ""
    title = (raw.get("title") or "").strip()
    description_html = raw.get("content") or ""
    loc_obj = raw.get("location") or {}
    location_raw = (loc_obj.get("name") or "").strip() if isinstance(loc_obj, dict) else ""
    posted_at = _parse_iso_date(raw.get("updated_at"))
    source_url = (raw.get("absolute_url") or "").strip()
    source_id = str(raw.get("id") or "")
    source_name = f"{_ATS_SOURCE_PREFIX['greenhouse']}:{slug}"

    return _build_listing(
        title=title,
        company=company,
        description_html=description_html,
        location_raw=location_raw,
        posted_at=posted_at,
        employment=None,  # Greenhouse public API doesn't expose employment type
        source_name=source_name,
        source_url=source_url,
        source_id=source_id,
    )


def _normalize_lever(raw: RawListing) -> JobListing:
    """Normalize a Lever job posting to a canonical JobListing."""
    company = raw.get("_company_name") or "Unknown"
    slug = raw.get("_ats_slug") or ""
    title = (raw.get("text") or "").strip()
    description_html = raw.get("description") or raw.get("descriptionPlain") or ""
    cats = raw.get("categories") or {}
    location_raw = (cats.get("location") or "").strip()

    # Lever createdAt is milliseconds since epoch
    created_ms = raw.get("createdAt")
    posted_at: str | None = None
    if created_ms is not None:
        try:
            dt = datetime.fromtimestamp(int(created_ms) / 1000, tz=timezone.utc)
            posted_at = dt.date().isoformat()
        except (ValueError, TypeError, OSError):
            posted_at = None

    source_url = (raw.get("hostedUrl") or raw.get("applyUrl") or "").strip()
    source_id = str(raw.get("id") or "")
    source_name = f"{_ATS_SOURCE_PREFIX['lever']}:{slug}"
    employment = _lever_employment(raw)

    return _build_listing(
        title=title,
        company=company,
        description_html=description_html,
        location_raw=location_raw,
        posted_at=posted_at,
        employment=employment,
        source_name=source_name,
        source_url=source_url,
        source_id=source_id,
    )


def _normalize_ashby(raw: RawListing) -> JobListing:
    """Normalize an Ashby job posting to a canonical JobListing."""
    company = raw.get("_company_name") or "Unknown"
    slug = raw.get("_ats_slug") or ""
    title = (raw.get("title") or "").strip()
    description_html = raw.get("descriptionSocial") or ""
    location_raw = (raw.get("locationName") or "").strip()
    posted_at = _parse_iso_date(raw.get("publishedDate"))
    source_url = (raw.get("jobUrl") or "").strip()
    source_id = str(raw.get("id") or "")
    source_name = f"{_ATS_SOURCE_PREFIX['ashby']}:{slug}"
    employment = _ashby_employment(raw)

    return _build_listing(
        title=title,
        company=company,
        description_html=description_html,
        location_raw=location_raw,
        posted_at=posted_at,
        employment=employment,
        source_name=source_name,
        source_url=source_url,
        source_id=source_id,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_NORMALIZERS = {
    "greenhouse": _normalize_greenhouse,
    "lever": _normalize_lever,
    "ashby": _normalize_ashby,
}

_FETCHERS = {
    "greenhouse": _fetch_greenhouse,
    "lever": _fetch_lever,
    "ashby": _fetch_ashby,
}

_SUPPORTED_ATS = frozenset(_FETCHERS)


# ---------------------------------------------------------------------------
# Public adapter class
# ---------------------------------------------------------------------------


class AtsAdapter:
    """Source adapter for ATS company job boards (Greenhouse, Lever, Ashby).

    Accepts a watchlist of target companies and fetches all their open roles.
    Keyword and location arguments are ignored — the hard filter stage applies
    location policy and keyword filtering downstream.

    Each entry in watchlist must have:
        ats:  "greenhouse" | "lever" | "ashby"
        slug: the company's slug in their ATS (from the job board URL)
        name: (optional) display name; defaults to slug

    Credentials: none required — these are public job board APIs.
    """

    name = "ats"

    def __init__(self, watchlist: list[dict]) -> None:
        self._watchlist = [
            e for e in watchlist if e.get("ats", "").lower() in _SUPPORTED_ATS and e.get("slug")
        ]

    def search(self, keyword: str, location: str, max_results: int = 50) -> list[RawListing]:
        """Fetch all open jobs from all configured companies.

        keyword and location are intentionally ignored; the pipeline's hard filter
        handles location policy and deal-breaker keywords after normalization.

        Raises httpx.HTTPError on unrecoverable network / auth failures for a
        company; the pipeline's exception handler skips and records the failure.
        Per-company errors propagate so the pipeline's source-failure tally
        catches them — but only after all companies for the current entry are tried.
        """
        results: list[RawListing] = []
        for entry in self._watchlist:
            ats_type = entry["ats"].lower()
            slug = entry["slug"]
            company_name = (entry.get("name") or slug).strip()

            raws = _FETCHERS[ats_type](slug)
            for raw in raws:
                raw["_ats_type"] = ats_type
                raw["_ats_slug"] = slug
                raw["_company_name"] = company_name
            results.extend(raws)

        return results[:max_results]

    def normalize(self, raw: RawListing) -> JobListing:
        """Map an ATS raw listing dict to a canonical JobListing.

        The raw dict must have a ``_ats_type`` key injected by ``search()``.
        """
        ats_type = raw.get("_ats_type", "")
        normalizer = _NORMALIZERS.get(ats_type)
        if normalizer is None:
            raise ValueError(f"Unknown ATS type: {ats_type!r}")
        return normalizer(raw)
