# SPDX-License-Identifier: Apache-2.0
"""RemoteOK source adapter.

Implements the SourceAdapter protocol: search(keyword, location, max_results) -> [RawListing]
and normalize(raw) -> JobListing.

RemoteOK provides a free public JSON API for remote jobs. No credentials required.
Attribution: job data sourced from remoteok.com.

API: GET https://remoteok.com/api  — returns a JSON array; first element is a legal notice.
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
    Salary,
    Source,
    derive_content_hash,
    derive_id,
    infer_seniority,
)
from jobhunter.normalize import parse_location, strip_html

RawListing = dict[str, Any]

_API_URL = "https://remoteok.com/api"
_TIMEOUT = 30.0

# RemoteOK locations often include emoji flags — strip before parsing
_EMOJI_RE = re.compile(
    "[\U0001f1e0-\U0001f1ff"  # regional indicator symbols (country flags)
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U00002600-\U000027bf"
    "\U0001fa70-\U0001faff"
    "✀-➿"
    "]+",
    re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    """Remove emoji characters and collapse whitespace."""
    return re.sub(r"\s+", " ", _EMOJI_RE.sub("", text)).strip()


def _parse_posted_at(date_str: str | None) -> str | None:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except (ValueError, AttributeError):
        return None


def _parse_salary_remoteok(raw: RawListing) -> Salary | None:
    """Parse RemoteOK's salary_min / salary_max fields (USD/year when present)."""
    sal_min = raw.get("salary_min")
    sal_max = raw.get("salary_max")
    if sal_min is None and sal_max is None:
        return None
    try:
        min_v = float(sal_min) if sal_min is not None else None
        max_v = float(sal_max) if sal_max is not None else None
    except (TypeError, ValueError):
        return None
    if min_v is None and max_v is None:
        return None
    return Salary(min=min_v, max=max_v, currency="USD", period="year")


class RemoteOKAdapter:
    """Source adapter for RemoteOK remote job board.

    Fetches all remote jobs from remoteok.com's public JSON API.
    Query-independent: search() is called once per run.
    No credentials required.

    RemoteOK salaries are expressed as annual USD figures when present.
    The first element of the API response is a legal notice and is filtered out.
    """

    name = "remoteok"
    query_independent = True  # pipeline calls search() once per run

    def __init__(self) -> None:
        self.run_date: str = ""

    def search(self, keyword: str, location: str, max_results: int = 50) -> list[RawListing]:
        """Fetch all jobs from RemoteOK. keyword and location are ignored (query-independent)."""
        headers = {"User-Agent": "jobhunter/1.0 (personal job-search pipeline)"}
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(_API_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        results: list[RawListing] = []
        if isinstance(data, list):
            for item in data:
                # Filter the legal-notice first element and any non-job objects
                if isinstance(item, dict) and item.get("id") and item.get("url"):
                    item["_run_date"] = self.run_date
                    results.append(item)
        return results

    def normalize(self, raw: RawListing) -> JobListing:
        """Map a raw RemoteOK listing dict to a canonical JobListing."""
        # RemoteOK uses 'position' for the job title
        title = (raw.get("position") or raw.get("title") or "").strip()
        company = (raw.get("company") or "").strip()
        description = strip_html(raw.get("description") or "")
        location_raw = _strip_emoji((raw.get("location") or ""))
        base_loc = parse_location(location_raw)
        # RemoteOK only lists remote jobs; force is_remote regardless of location string
        location = Location(
            raw=base_loc.raw,
            city=base_loc.city,
            region=base_loc.region,
            country=base_loc.country,
            is_remote=True,
        )
        salary = _parse_salary_remoteok(raw)
        posted_at = _parse_posted_at(raw.get("date"))
        seniority = infer_seniority(title, description)
        first_seen_at = raw.get("_run_date") or date.today().isoformat()

        source_id = str(raw.get("id") or "")
        source = Source(
            name=self.name,
            url=(raw.get("url") or "").strip(),
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
            salary=salary,
            seniority=seniority,
            employment=None,  # RemoteOK doesn't consistently expose employment type
            posted_at=posted_at,
        )
        if company:
            listing.id = derive_id(company, title, location)
        else:
            listing.id = derive_id(f"__unknown_company__{source_id}", title, location)
        listing.content_hash = derive_content_hash(listing)
        return listing
