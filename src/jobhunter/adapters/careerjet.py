# SPDX-License-Identifier: Apache-2.0
"""Careerjet source adapter.

Implements the SourceAdapter protocol: search(keyword, location, max_results) -> [RawListing]
and normalize(raw) -> JobListing.

Credentials are read from the environment variable CAREERJET_AFFILIATE_ID.
Never hardcode credentials — use a .env file or export them in your shell.

API: https://www.careerjet.com/affiliate.html (free affiliate programme)
See: specs/02-functional-spec.md §Stage 1-2
     specs/04-technical-plan.md §Data sources (aggregator-adapter plan item)
"""

from __future__ import annotations

import os
import re
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

_BASE_URL = "http://public.api.careerjet.net/search"
_PAGE_SIZE = 20  # Careerjet affiliate API maximum per page
_TIMEOUT = 30.0  # seconds
_DEFAULT_PAGE_DELAY = 0.5  # seconds between pages (politeness)

# Default salary currency inferred from locale code
_LOCALE_CURRENCY: dict[str, str] = {
    "en_AU": "AUD",
    "en_US": "USD",
    "en_GB": "GBP",
    "en_CA": "CAD",
    "en_NZ": "NZD",
    "en_SG": "SGD",
    "en_IE": "EUR",
    "en_IN": "INR",
    "en_ZA": "ZAR",
}

# Careerjet contracttype values → canonical employment enum
_EMPLOYMENT_MAP: dict[str, str] = {
    "permanent": "full_time",
    "contract": "contract",
    "temp": "temp",
    "temporary": "temp",
    "part-time": "part_time",
    "part_time": "part_time",
    "internship": "internship",
    "intern": "internship",
}

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)


def _parse_employment(raw: RawListing) -> str | None:
    """Map Careerjet contracttype to canonical employment enum."""
    ctype = (raw.get("contracttype") or "").lower().strip()
    return _EMPLOYMENT_MAP.get(ctype)


def _parse_posted_at(raw_date: str | None) -> str | None:
    """Parse Careerjet's date field to YYYY-MM-DD.

    Careerjet returns ISO dates ("2026-07-20") or relative strings ("2 days ago",
    "Today", "Yesterday"). Only ISO-parseable dates are returned; relative strings
    resolve to None (unknown-data policy: listing is kept, first_seen_at is used).
    """
    if not raw_date:
        return None
    text = raw_date.strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except (ValueError, AttributeError):
        pass
    return None


class CareerjetAdapter:
    """Source adapter for the Careerjet Affiliates API.

    Credentials come from an explicit affiliate_id arg, falling back to
    CAREERJET_AFFILIATE_ID environment variable.

    Args:
        locale_code: Careerjet locale code (default "en_AU" for Australia).
        page_delay:  Seconds to sleep between pages (politeness).
    """

    name = "careerjet"

    def __init__(
        self,
        affiliate_id: str | None = None,
        locale_code: str = "en_AU",
        page_delay: float = _DEFAULT_PAGE_DELAY,
    ) -> None:
        self.affiliate_id = (
            affiliate_id if affiliate_id is not None else os.environ["CAREERJET_AFFILIATE_ID"]
        )
        self.locale_code = locale_code
        self.requests_made = 0
        self.run_date: str | None = None
        self.page_delay = page_delay
        self._currency = _LOCALE_CURRENCY.get(locale_code)

    def search(self, keyword: str, location: str, max_results: int = 50) -> list[RawListing]:
        """Fetch raw listings from Careerjet for one keyword+location query.

        Pages through results up to max_results, stopping early when Careerjet
        returns fewer results than pagesize (last page) or when all pages are
        exhausted.

        Raises httpx.HTTPError on network / auth failures (caller handles gracefully).
        """
        results: list[RawListing] = []
        page = 1

        with httpx.Client(timeout=_TIMEOUT) as client:
            while len(results) < max_results:
                remaining = max_results - len(results)
                params: dict[str, Any] = {
                    "keywords": keyword,
                    "location": location,
                    "affid": self.affiliate_id,
                    "locale_code": self.locale_code,
                    "start": page,
                    "pagesize": min(_PAGE_SIZE, remaining),
                    "sort": "date",
                }

                if page > 1 and self.page_delay > 0:
                    time.sleep(self.page_delay)

                self.requests_made += 1
                response = client.get(_BASE_URL, params=params)
                response.raise_for_status()
                data = response.json()

                page_results: list[RawListing] = data.get("jobs") or []
                if not page_results:
                    break

                results.extend(page_results)

                total_pages = int(data.get("pages") or 1)
                if page >= total_pages or len(page_results) < _PAGE_SIZE:
                    break

                page += 1

        return results[:max_results]

    def normalize(self, raw: RawListing) -> JobListing:
        """Map a raw Careerjet listing dict to a canonical JobListing.

        Missing fields are set to null. HTML is stripped from description.
        Salary is parsed via the shared normalize.parse_salary. Seniority is
        inferred from title then description.
        """
        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or "").strip()
        description = strip_html(raw.get("description") or "")

        location_raw = (raw.get("locations") or "").strip()
        location = parse_location(location_raw)

        salary_str = (raw.get("salary") or "").strip() or None
        salary = parse_salary(salary_str, default_currency=self._currency) if salary_str else None

        employment = _parse_employment(raw)
        posted_at = _parse_posted_at(raw.get("date"))
        first_seen_at = self.run_date or date.today().isoformat()

        seniority = infer_seniority(title, description)

        url = (raw.get("url") or "").strip()
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
