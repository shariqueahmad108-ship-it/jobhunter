# SPDX-License-Identifier: Apache-2.0
"""Remotive source adapter.

Implements the SourceAdapter protocol: search(keyword, location, max_results) -> [RawListing]
and normalize(raw) -> JobListing.

Remotive provides a free public API for remote jobs. No credentials required.
Attribution: job data sourced from remotive.com.

API docs: https://remotive.com/api/remote-jobs
See: specs/02-functional-spec.md §Stage 1-2
     specs/04-technical-plan.md §Data sources
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import httpx

from jobhunter.model import (
    JobListing,
    Location,
    Source,
    derive_content_hash,
    derive_id,
    infer_seniority,
)
from jobhunter.normalize import parse_location, parse_salary, strip_html

RawListing = dict[str, Any]

_BASE_URL = "https://remotive.com/api/remote-jobs"
_TIMEOUT = 30.0

_JOB_TYPE_MAP: dict[str, str] = {
    "full_time": "full_time",
    "contract": "contract",
    "part_time": "part_time",
    "internship": "internship",
    "temporary": "temp",
}

# Remotive location strings often carry eligibility suffixes like "USA Only",
# "Europe Only Employees", "APAC Residents" — strip these before location parsing
# so the geographic token is recognised correctly.
_LOCATION_RESTRICTION_RE = re.compile(
    r"\s*\b(?:only(?:\s+(?:employees|residents|candidates|applicants))?"
    r"|residents|employees|citizens|based)\b.*$",
    re.IGNORECASE,
)


def _strip_location_restriction(text: str) -> str:
    """Remove eligibility suffixes ('Only', 'Only Employees', 'Residents', etc.)."""
    return _LOCATION_RESTRICTION_RE.sub("", text).strip()


def _parse_posted_at(pub_date: str | None) -> str | None:
    if not pub_date:
        return None
    try:
        dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except (ValueError, AttributeError):
        return None


def _parse_location_remotive(location_raw: str) -> Location:
    """Parse Remotive's candidate_required_location; all listings are remote.

    Strips common eligibility suffixes ("Only", "Only Employees", etc.) so the
    underlying geographic token can be matched by parse_location.
    """
    geo = _strip_location_restriction(location_raw)
    base = parse_location(geo) if geo else parse_location(location_raw)
    return Location(
        raw=location_raw,  # preserve the original verbatim
        city=base.city,
        region=base.region,
        country=base.country,
        is_remote=True,  # Remotive only lists remote jobs
    )


class RemotiveAdapter:
    """Source adapter for Remotive remote job board.

    Fetches remote jobs (optionally filtered by Remotive category strings).
    Query-independent: search() is called once per run, not per keyword×location.
    No credentials required.

    Profile config (queries.remotive):
        enabled: true
        categories: ["software-dev", "devops-sysadmin"]  # optional; all categories if omitted
    """

    name = "remotive"
    query_independent = True  # pipeline calls search() once per run

    def __init__(self, categories: list[str] | None = None) -> None:
        self.categories = list(categories) if categories else []
        self.run_date: str = ""

    def search(self, keyword: str, location: str, max_results: int = 50) -> list[RawListing]:
        """Fetch jobs from Remotive. keyword and location are ignored (query-independent).

        Issues one request per category, or one uncategorised request if no categories
        are configured.
        """
        if self.categories:
            results: list[RawListing] = []
            for category in self.categories:
                results.extend(self._fetch(category))
            return results
        return self._fetch(None)

    def _fetch(self, category: str | None) -> list[RawListing]:
        params: dict[str, Any] = {}
        if category:
            params["category"] = category
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(_BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        jobs: list[RawListing] = data.get("jobs") or []
        for job in jobs:
            job["_run_date"] = self.run_date
        return jobs

    def normalize(self, raw: RawListing) -> JobListing:
        """Map a raw Remotive listing dict to a canonical JobListing."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company_name") or "").strip()
        description = strip_html(raw.get("description") or "")
        # candidate_required_location is the geographic scope of eligibility
        location_raw = (raw.get("candidate_required_location") or raw.get("location") or "").strip()
        location = _parse_location_remotive(location_raw)
        salary_text = raw.get("salary") or None
        salary = parse_salary(salary_text) if salary_text else None
        employment_raw = (raw.get("job_type") or "").lower().replace("-", "_").replace(" ", "_")
        employment = _JOB_TYPE_MAP.get(employment_raw)
        posted_at = _parse_posted_at(raw.get("publication_date"))
        seniority = infer_seniority(title, description)
        first_seen_at = raw.get("_run_date") or date.today().isoformat()

        source = Source(
            name=self.name,
            url=(raw.get("url") or "").strip(),
            source_id=str(raw.get("id") or ""),
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
            salary=salary,
            seniority=seniority,
            employment=employment,
            posted_at=posted_at,
        )
        if company:
            listing.id = derive_id(company, title, location)
        else:
            listing.id = derive_id(f"__unknown_company__{source.source_id}", title, location)
        listing.content_hash = derive_content_hash(listing)
        return listing
