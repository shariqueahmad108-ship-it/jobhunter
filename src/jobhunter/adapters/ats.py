# SPDX-License-Identifier: Apache-2.0
"""ATS company job-board adapter.

Fetches all open roles from a watchlist of target companies' ATS job boards.
Supports Greenhouse, Lever, Ashby, and Workday — all publicly accessible JSON
APIs that require no authentication for reading public job postings.

The watchlist is configured in profile queries.ats_watchlist.
Unlike keyword-based adapters, keyword and location are ignored; the hard
filter stage applies location/keyword filtering downstream.

Sources used:
  Greenhouse: GET  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  Lever:      GET  https://api.lever.co/v0/postings/{slug}?mode=json
  Ashby:      POST https://jobs.ashbyhq.com/api/non-authed/job-board/jobs
  Workday:    POST https://{slug}.wd{N}.myworkdayjobs.com/wday/cxs/{workday_path}/jobs

Note: Ashby's public job-board API only exposes descriptionSocial (a short
teaser), not the full job description. The full text lives behind the detail
endpoint (GET /api/non-authed/job-posting/{id}), which requires per-listing
requests and is not fetched here to stay within the single-fetch-per-company
design. Downstream keyword filtering therefore only sees the teaser text for
Ashby listings.

Note: Workday's public job-board list endpoint does not expose job descriptions.
Downstream keyword filtering will only match on title and location for Workday
listings. Full descriptions are available only via per-listing detail requests,
which are not made here to stay within the single-fetch-per-company design.

Workday watchlist entry fields:
  ats:              "workday"
  slug:             URL subdomain, e.g. "redhat" from redhat.wd5.myworkdayjobs.com (required)
  name:             display name, defaults to slug (optional)
  workday_path:     CXS path after /wday/cxs/, e.g. "RedHat/Jobs" (optional; defaults to
                    "{slug}/{slug}" which works for some tenants — check the company's Workday
                    board URL and override this field when the default does not match)
  workday_instance: the wd instance number, e.g. 5 for .wd5. (optional; defaults to 5)

See: specs/04-technical-plan.md §Data sources
     specs/02-functional-spec.md §Stage 1–2
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from jobhunter.model import (
    JobListing,
    Seniority,
    Source,
    derive_content_hash,
    derive_id,
    infer_seniority,
)
from jobhunter.normalize import parse_location, strip_html

RawListing = dict[str, Any]

_TIMEOUT = 30.0  # seconds

_ATS_SOURCE_PREFIX = {
    "greenhouse": "ats_greenhouse",
    "lever": "ats_lever",
    "ashby": "ats_ashby",
    "workday": "ats_workday",
}

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

_WORKDAY_TIME_TYPE_MAP: dict[str, str] = {
    "full time": "full_time",
    "full-time": "full_time",
    "fulltime": "full_time",
    "part time": "part_time",
    "part-time": "part_time",
    "parttime": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "intern": "internship",
    "internship": "internship",
    "temporary": "temp",
    "temp": "temp",
}

SUPPORTED_ATS_TYPES: frozenset[str] = frozenset({"greenhouse", "lever", "ashby", "workday"})

_WORKDAY_FETCH_LIMIT = 20  # items per page (conservative Workday default)
_WORKDAY_MAX_ITEMS = 500  # safety cap per company


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
    return data if isinstance(data, list) else []


def _fetch_ashby(slug: str) -> list[RawListing]:
    """Fetch all open job postings from an Ashby job board."""
    url = "https://jobs.ashbyhq.com/api/non-authed/job-board/jobs"
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, json={"organizationHostedJobsPageName": slug})
        resp.raise_for_status()
        data = resp.json()
    return data.get("jobPostings") or []


def _fetch_workday(slug: str, entry: dict) -> list[RawListing]:
    """Fetch all open jobs from a Workday public job board, paginating to completion.

    Workday endpoint: POST https://{slug}.wd{N}.myworkdayjobs.com/wday/cxs/{path}/jobs
    Body: {"appliedFacets": {}, "limit": N, "offset": M, "searchText": ""}

    The ``entry`` dict may contain ``workday_path`` (defaults to "{slug}/{slug}")
    and ``workday_instance`` (defaults to 5). Inject ``_workday_host`` into each
    returned raw dict so the normalizer can construct a full source URL.
    """
    instance = int(entry.get("workday_instance") or 5)
    workday_path = (entry.get("workday_path") or f"{slug}/{slug}").strip("/")
    host = f"{slug}.wd{instance}.myworkdayjobs.com"
    url = f"https://{host}/wday/cxs/{workday_path}/jobs"

    all_postings: list[RawListing] = []
    offset = 0

    with httpx.Client(timeout=_TIMEOUT) as client:
        while offset < _WORKDAY_MAX_ITEMS:
            resp = client.post(
                url,
                json={
                    "appliedFacets": {},
                    "limit": _WORKDAY_FETCH_LIMIT,
                    "offset": offset,
                    "searchText": "",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("jobPostings") or []
            total = int(data.get("total", 0))

            for posting in batch:
                posting["_workday_host"] = host

            all_postings.extend(batch)
            offset += len(batch)

            if not batch or offset >= total:
                break

    return all_postings


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _parse_iso_date(raw: str | None) -> str | None:
    """Parse an ISO 8601 datetime or date string to a YYYY-MM-DD string."""
    if not raw:
        return None
    try:
        cleaned = raw.replace("Z", "+00:00").replace(".000+00:00", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt.date().isoformat()
    except (ValueError, AttributeError):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
        return m.group(1) if m else None


def _workday_employment(raw_type: str | None) -> str | None:
    """Map Workday timeType to canonical employment type."""
    if not raw_type:
        return None
    return _WORKDAY_TIME_TYPE_MAP.get(raw_type.lower().strip())


def _parse_workday_date(raw: str | None) -> str | None:
    """Parse a Workday human-readable date string to YYYY-MM-DD.

    Workday expresses post date as "Posted N Days Ago", "Posted 30+ Days Ago",
    or "Posted Today". Returns None for any unrecognised format.
    """
    if not raw:
        return None
    lower = raw.lower()
    if "today" in lower:
        return date.today().isoformat()
    m = re.search(r"(\d+)\+?\s*day", lower)
    if m:
        n = int(m.group(1))
        return (date.today() - timedelta(days=n)).isoformat()
    return None


def _lever_employment(raw: RawListing) -> str | None:
    """Map Lever categories.commitment to canonical employment type."""
    cats = raw.get("categories") or {}
    commitment = (cats.get("commitment") or "").lower().replace(" ", "")
    return _LEVER_COMMITMENT_MAP.get(commitment)


def _ashby_employment(raw: RawListing) -> str | None:
    """Map Ashby employmentType to canonical employment type."""
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
    first_seen_at: str,
) -> JobListing:
    """Shared listing construction logic for all three ATS formats."""
    description = strip_html(description_html)
    location = parse_location(location_raw)
    seniority: Seniority | None = infer_seniority(title, description)

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
    if company:
        listing.id = derive_id(company, title, location)
    else:
        # Salt with source_id so unknown-company roles with the same title+location
        # don't collapse into one listing.
        listing.id = derive_id(f"__unknown_company__{source_id}", title, location)
    listing.content_hash = derive_content_hash(listing)
    return listing


# ---------------------------------------------------------------------------
# Per-ATS normalize helpers
# ---------------------------------------------------------------------------


def _normalize_greenhouse(raw: RawListing) -> JobListing:
    """Normalize a Greenhouse job listing to a canonical JobListing."""
    company = (raw.get("_company_name") or raw.get("_ats_slug") or "").strip()
    slug = raw.get("_ats_slug") or ""
    title = (raw.get("title") or "").strip()
    description_html = raw.get("content") or ""
    loc_obj = raw.get("location") or {}
    location_raw = (loc_obj.get("name") or "").strip() if isinstance(loc_obj, dict) else ""
    posted_at = _parse_iso_date(raw.get("updated_at"))
    source_url = (raw.get("absolute_url") or "").strip()
    source_id = str(raw.get("id") or "")
    source_name = f"{_ATS_SOURCE_PREFIX['greenhouse']}:{slug}"
    first_seen_at = raw.get("_run_date") or date.today().isoformat()

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
        first_seen_at=first_seen_at,
    )


def _normalize_lever(raw: RawListing) -> JobListing:
    """Normalize a Lever job posting to a canonical JobListing."""
    company = (raw.get("_company_name") or raw.get("_ats_slug") or "").strip()
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
    first_seen_at = raw.get("_run_date") or date.today().isoformat()

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
        first_seen_at=first_seen_at,
    )


def _normalize_ashby(raw: RawListing) -> JobListing:
    """Normalize an Ashby job posting to a canonical JobListing."""
    company = (raw.get("_company_name") or raw.get("_ats_slug") or "").strip()
    slug = raw.get("_ats_slug") or ""
    title = (raw.get("title") or "").strip()
    # descriptionSocial is a teaser only; full description requires the detail endpoint
    description_html = raw.get("descriptionSocial") or ""
    location_raw = (raw.get("locationName") or "").strip()
    posted_at = _parse_iso_date(raw.get("publishedDate"))
    source_url = (raw.get("jobUrl") or "").strip()
    source_id = str(raw.get("id") or "")
    source_name = f"{_ATS_SOURCE_PREFIX['ashby']}:{slug}"
    employment = _ashby_employment(raw)
    first_seen_at = raw.get("_run_date") or date.today().isoformat()

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
        first_seen_at=first_seen_at,
    )


def _normalize_workday(raw: RawListing) -> JobListing:
    """Normalize a Workday job posting to a canonical JobListing.

    Workday's public list API does not expose job descriptions; downstream
    keyword filtering will only match on title and location for these listings.
    """
    company = (raw.get("_company_name") or raw.get("_ats_slug") or "").strip()
    slug = raw.get("_ats_slug") or ""
    title = (raw.get("title") or "").strip()
    location_raw = (raw.get("locationsText") or "").strip()
    posted_at = _parse_workday_date(raw.get("postedOn"))
    employment = _workday_employment(raw.get("timeType"))
    host = raw.get("_workday_host") or f"{slug}.wd5.myworkdayjobs.com"
    external_path = (raw.get("externalPath") or "").strip()
    source_url = f"https://{host}{external_path}" if external_path else f"https://{host}/"
    source_id = str(raw.get("jobReqId") or raw.get("externalPath") or "")
    source_name = f"{_ATS_SOURCE_PREFIX['workday']}:{slug}"
    first_seen_at = raw.get("_run_date") or date.today().isoformat()

    return _build_listing(
        title=title,
        company=company,
        description_html="",  # Workday list API does not expose descriptions
        location_raw=location_raw,
        posted_at=posted_at,
        employment=employment,
        source_name=source_name,
        source_url=source_url,
        source_id=source_id,
        first_seen_at=first_seen_at,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_NORMALIZERS = {
    "greenhouse": _normalize_greenhouse,
    "lever": _normalize_lever,
    "ashby": _normalize_ashby,
    "workday": _normalize_workday,
}

_FETCHERS = {
    "greenhouse": _fetch_greenhouse,
    "lever": _fetch_lever,
    "ashby": _fetch_ashby,
    # workday uses _fetch_workday(slug, entry) — not in this dict because it
    # requires the full entry dict, not just the slug.
}


# ---------------------------------------------------------------------------
# Public adapter class
# ---------------------------------------------------------------------------


class AtsAdapter:
    """Source adapter for ATS company job boards (Greenhouse, Lever, Ashby).

    Accepts a watchlist of target companies and fetches all their open roles.
    Keyword and location arguments are ignored — the hard filter stage applies
    location policy and keyword filtering downstream.

    This adapter is query-independent: it fetches all watchlist companies once
    per run, not once per keyword×location combination. The pipeline recognises
    the ``query_independent = True`` attribute and calls ``search`` exactly once.

    Each entry in watchlist must have:
        ats:  "greenhouse" | "lever" | "ashby"
        slug: the company's slug in their ATS (from the job board URL)
        name: (optional) display name; defaults to slug

    Credentials: none required — these are public job board APIs.
    """

    name = "ats"
    query_independent = True  # pipeline calls search() once per run, not per keyword×location

    def __init__(self, watchlist: list[dict]) -> None:
        self._watchlist = [
            e
            for e in watchlist
            if e.get("ats", "").lower() in SUPPORTED_ATS_TYPES and e.get("slug")
        ]
        self.run_date: str = ""  # injected by pipeline before each run
        self.company_failures: list[str] = []

    def search(self, keyword: str, location: str, max_results: int = 50) -> list[RawListing]:
        """Fetch all open jobs from all configured companies.

        keyword and location are intentionally ignored; the pipeline's hard filter
        handles location policy and keyword filtering after normalization.

        Per-company errors are caught and recorded in ``self.company_failures``
        so the pipeline can surface them individually without aborting the run.
        The pipeline reads ``company_failures`` after each call.

        Returns all successful results with no truncation — ``max_results`` is
        accepted for protocol compatibility but not applied. The pipeline's
        max_requests_per_run cap governs overall volume; the hard filter trims
        by relevance.
        """
        self.company_failures = []
        results: list[RawListing] = []

        for entry in self._watchlist:
            ats_type = entry["ats"].lower()
            slug = entry["slug"]
            company_name = (entry.get("name") or slug).strip()
            try:
                if ats_type == "workday":
                    raws = _fetch_workday(slug, entry)
                else:
                    raws = _FETCHERS[ats_type](slug)
                for raw in raws:
                    raw["_ats_type"] = ats_type
                    raw["_ats_slug"] = slug
                    raw["_company_name"] = company_name
                    raw["_run_date"] = self.run_date
                results.extend(raws)
            except Exception as exc:
                self.company_failures.append(f"{company_name} ({ats_type}:{slug}): {exc}")

        return results

    def normalize(self, raw: RawListing) -> JobListing:
        """Map an ATS raw listing dict to a canonical JobListing.

        The raw dict must have a ``_ats_type`` key injected by ``search()``.
        """
        ats_type = raw.get("_ats_type", "")
        normalizer = _NORMALIZERS.get(ats_type)
        if normalizer is None:
            raise ValueError(f"Unknown ATS type: {ats_type!r}")
        return normalizer(raw)
