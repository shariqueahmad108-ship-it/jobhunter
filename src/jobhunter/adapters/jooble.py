# SPDX-License-Identifier: Apache-2.0
"""Jooble source adapter.

Implements the SourceAdapter protocol: search(keyword, location, max_results) -> [RawListing]
and normalize(raw) -> JobListing.

Credentials are read from the environment variable JOOBLE_API_KEY.
Never hardcode credentials — use a .env file or export them in your shell.

API: https://jooble.org/api/about (free key on request)
  POST https://jooble.org/api/{api_key}
  body: {"keywords": ..., "location": ..., "page": N, "ResultOnPage": M}
  response: {"totalCount": N, "jobs": [{title, location, snippet, salary,
             source, type, link, company, updated, id}]}
  403 => invalid key (the pipeline treats 401/403 as a dead adapter).

See: specs/02-functional-spec.md §Stage 1-2
     specs/04-technical-plan.md §Data sources (aggregator-adapter plan item)
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime
from typing import Any

import httpx

from jobhunter.model import (
    JobListing,
    Source,
    derive_content_hash,
    derive_id,
    infer_seniority,
)
from jobhunter.normalize import parse_location, parse_salary, strip_html

RawListing = dict[str, Any]

_BASE_URL = "https://jooble.org/api"
_PAGE_SIZE = 20  # ResultOnPage — polite default page size
_TIMEOUT = 30.0  # seconds
_DEFAULT_PAGE_DELAY = 0.5  # seconds between pages (politeness)

# Jooble `type` values → canonical employment enum. Jooble returns free-form
# strings that vary by source board; map the common ones and leave the rest null.
_EMPLOYMENT_MAP: dict[str, str] = {
    "full-time": "full_time",
    "full time": "full_time",
    "fulltime": "full_time",
    "part-time": "part_time",
    "part time": "part_time",
    "parttime": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "temporary": "temp",
    "temp": "temp",
    "internship": "internship",
    "intern": "internship",
}


def _parse_employment(raw: RawListing) -> str | None:
    """Map a Jooble ``type`` string to the canonical employment enum."""
    jtype = (raw.get("type") or "").lower().strip()
    return _EMPLOYMENT_MAP.get(jtype)


def _parse_posted_at(raw_updated: str | None) -> str | None:
    """Parse Jooble's ``updated`` timestamp to YYYY-MM-DD.

    Jooble returns ISO-8601 timestamps ("2026-07-20T00:00:00.0000000" or
    "2026-07-20"). Anything not ISO-parseable resolves to None (unknown-data
    policy: listing is kept, first_seen_at is used instead).
    """
    if not raw_updated:
        return None
    text = raw_updated.strip()
    # Trim sub-second precision beyond 6 digits which datetime cannot parse.
    if "." in text:
        head, _, tail = text.partition(".")
        frac = "".join(c for c in tail if c.isdigit())[:6]
        text = f"{head}.{frac}" if frac else head
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, AttributeError):
        pass
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


class JoobleAdapter:
    """Source adapter for the Jooble REST API.

    Credentials come from an explicit api_key arg, falling back to the
    JOOBLE_API_KEY environment variable.

    Args:
        api_key:          Jooble API key (falls back to JOOBLE_API_KEY env).
        default_currency: currency assumed for bare "$" salaries. Defaults to
                          "AUD" — the profiles and query locations are AU-centric,
                          so results are AU-located. Pass None to keep bare-symbol
                          salaries as unknown.
        page_delay:       seconds to sleep between pages (politeness).
    """

    name = "jooble"

    def __init__(
        self,
        api_key: str | None = None,
        default_currency: str | None = "AUD",
        page_delay: float = _DEFAULT_PAGE_DELAY,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ["JOOBLE_API_KEY"]
        self.default_currency = default_currency
        self.requests_made = 0
        self.run_date: str | None = None
        self.page_delay = page_delay

    def search(self, keyword: str, location: str, max_results: int = 50) -> list[RawListing]:
        """Fetch raw listings from Jooble for one keyword+location query.

        Pages through results up to max_results, stopping when Jooble returns a
        short/empty page or the reported totalCount is exhausted.

        Raises httpx.HTTPError on network / auth failures (the caller handles it;
        a 403 marks the adapter dead for the rest of the run).
        """
        results: list[RawListing] = []
        page = 1
        url = f"{_BASE_URL}/{self.api_key}"

        with httpx.Client(timeout=_TIMEOUT) as client:
            while len(results) < max_results:
                remaining = max_results - len(results)
                body: dict[str, Any] = {
                    "keywords": keyword,
                    "location": location,
                    "page": page,
                    "ResultOnPage": min(_PAGE_SIZE, remaining),
                }

                if page > 1 and self.page_delay > 0:
                    time.sleep(self.page_delay)

                self.requests_made += 1
                response = client.post(url, json=body)
                response.raise_for_status()
                data = response.json()

                page_results: list[RawListing] = data.get("jobs") or []
                if not page_results:
                    break

                results.extend(page_results)

                total = int(data.get("totalCount") or 0)
                if len(page_results) < _PAGE_SIZE or (total and len(results) >= total):
                    break

                page += 1

        return results[:max_results]

    def normalize(self, raw: RawListing) -> JobListing:
        """Map a raw Jooble listing dict to a canonical JobListing.

        Missing fields are set to null. HTML is stripped from the snippet.
        Salary is parsed via the shared normalize.parse_salary. Seniority is
        inferred from title then description.
        """
        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or "").strip()
        description = strip_html(raw.get("snippet") or "")

        location_raw = (raw.get("location") or "").strip()
        location = parse_location(location_raw)

        salary_str = (raw.get("salary") or "").strip() or None
        salary = (
            parse_salary(salary_str, default_currency=self.default_currency)
            if salary_str
            else None
        )

        employment = _parse_employment(raw)
        posted_at = _parse_posted_at(raw.get("updated"))
        first_seen_at = self.run_date or date.today().isoformat()

        seniority = infer_seniority(title, description)

        url = (raw.get("link") or "").strip()
        source_id = raw.get("id") or url

        source = Source(
            name=self.name,
            url=url,
            source_id=str(source_id),
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
            listing.id = derive_id(f"__unknown_company__{source_id}", title, location)
        listing.content_hash = derive_content_hash(listing)
        return listing
