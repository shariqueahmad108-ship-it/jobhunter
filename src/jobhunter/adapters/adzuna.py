# SPDX-License-Identifier: Apache-2.0
"""Adzuna source adapter.

Implements the SourceAdapter protocol: search(keyword, location, max_results) -> [RawListing]
and normalize(raw) -> JobListing.

Credentials are read from environment variables ADZUNA_APP_ID and ADZUNA_APP_KEY.
Never hardcode credentials — use a .env file or export them in your shell.

API docs: https://developer.adzuna.com/
See: specs/02-functional-spec.md §Stage 1-2
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
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
from jobhunter.normalize import strip_html

RawListing = dict[str, Any]

_BASE_URL = "https://api.adzuna.com/v1/api/jobs"
_PAGE_SIZE = 50  # Adzuna max per page
_TIMEOUT = 30.0  # seconds

# Default salary currency by Adzuna country code
_COUNTRY_CURRENCY: dict[str, str] = {
    "au": "AUD",
    "us": "USD",
    "gb": "GBP",
    "ca": "CAD",
    "nz": "NZD",
    "de": "EUR",
    "fr": "EUR",
    "nl": "EUR",
    "sg": "SGD",
    "in": "INR",
    "br": "BRL",
    "mx": "MXN",
    "pl": "PLN",
    "it": "EUR",
    "ru": "RUB",
    "za": "ZAR",
    "at": "EUR",
    "be": "EUR",
}

# Map Adzuna country display names to ISO 3166-1 alpha-2
_COUNTRY_ISO: dict[str, str] = {
    "Australia": "AU",
    "United States": "US",
    "United Kingdom": "GB",
    "Canada": "CA",
    "New Zealand": "NZ",
    "Germany": "DE",
    "France": "FR",
    "Netherlands": "NL",
    "Singapore": "SG",
    "India": "IN",
    "Brazil": "BR",
    "Mexico": "MX",
    "Poland": "PL",
    "Italy": "IT",
    "South Africa": "ZA",
    "Austria": "AT",
    "Belgium": "BE",
}

# Adzuna contract_type + contract_time -> canonical employment type
_EMPLOYMENT_MAP: dict[tuple[str, str], str] = {
    ("permanent", "full_time"): "full_time",
    ("permanent", "part_time"): "part_time",
    ("contract", "full_time"): "contract",
    ("contract", "part_time"): "contract",
    ("temporary", "full_time"): "temp",
    ("temporary", "part_time"): "temp",
}


_REMOTE_WORD_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_REMOTE_NEGATION_RE = re.compile(
    r"\b(?:no|not|isn'?t|without|except|cannot|can'?t)\s+(?:be\s+|fully[\s-])?remote\b"
    r"|\bno\s+remote\s+work\b"
    r"|\bremote\s+work\s+(?:is\s+)?not\b",
    re.IGNORECASE,
)
_REMOTE_POSITIVE_RE = re.compile(
    r"\b(?:fully[\s-]remote|100%\s*remote|remote[\s-]first"
    r"|work\s+from\s+(?:home|anywhere)"
    r"|remote\s+(?:role|position|work|job|team))\b",
    re.IGNORECASE,
)


def _detect_remote(title: str, description: str, location_raw: str) -> bool:
    """Detect remoteness conservatively (see plan item fix-remote-detection).

    Title and location strings are strong signals: a bare "remote" there counts.
    Description text is weak: only explicit positive phrases count, and any
    negated mention ("no remote work") vetoes description-based detection.
    """
    if _REMOTE_WORD_RE.search(title) or _REMOTE_WORD_RE.search(location_raw):
        return True
    head = description[:1000]
    if _REMOTE_NEGATION_RE.search(head):
        return False
    return bool(_REMOTE_POSITIVE_RE.search(head))


def _parse_location(loc: dict[str, Any]) -> Location:
    """Parse an Adzuna location object into a canonical Location."""
    display = (loc.get("display_name") or "").strip()
    area: list[str] = loc.get("area") or []

    # Adzuna area array: [country, region?, city?]
    country_name = area[0] if len(area) >= 1 else None
    region = area[1] if len(area) >= 2 else None
    city = area[2] if len(area) >= 3 else None

    country_iso = _COUNTRY_ISO.get(country_name) if country_name else None

    return Location(
        raw=display,
        city=city,
        region=region,
        country=country_iso,
        is_remote=False,  # set properly in normalize() once we have title/description
    )


def _parse_employment(raw: RawListing) -> str | None:
    """Map Adzuna contract_type + contract_time to canonical employment enum."""
    ctype = (raw.get("contract_type") or "").lower().strip()
    ctime = (raw.get("contract_time") or "").lower().strip()
    return _EMPLOYMENT_MAP.get((ctype, ctime))


def _parse_posted_at(created: str | None) -> str | None:
    """Parse Adzuna's ISO 8601 'created' field to a date string (YYYY-MM-DD)."""
    if not created:
        return None
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except (ValueError, AttributeError):
        return None


class AdzunaAdapter:
    """Source adapter for the Adzuna Jobs API.

    Credentials come from explicit app_id/app_key args, falling back to the
    ADZUNA_APP_ID and ADZUNA_APP_KEY environment variables.

    Args:
        country: Two-letter Adzuna country code (default "au" for Australia).
    """

    name = "adzuna"

    def __init__(
        self,
        app_id: str | None = None,
        app_key: str | None = None,
        country: str = "au",
    ) -> None:
        self.app_id = app_id if app_id is not None else os.environ["ADZUNA_APP_ID"]
        self.app_key = app_key if app_key is not None else os.environ["ADZUNA_APP_KEY"]
        self.country = country.lower()
        self.requests_made = 0  # actual HTTP requests issued (pipeline reads this)
        self.run_date: str | None = None  # injected by the pipeline for first_seen_at
        # Unknown country code => currency unknown (never guess); the salary
        # comparison treats it via the unknown-salary policy.
        self._currency = _COUNTRY_CURRENCY.get(self.country)

    def search(self, keyword: str, location: str, max_results: int = 50) -> list[RawListing]:
        """Fetch raw listings from Adzuna for one keyword+location query.

        Always requests _PAGE_SIZE items per call; stops when Adzuna returns fewer
        than _PAGE_SIZE (last page) or when max_results is reached. Results are
        sliced to max_results before returning.
        Raises httpx.HTTPError on network / auth failures (caller handles gracefully).
        """
        results: list[RawListing] = []
        page = 1

        with httpx.Client(timeout=_TIMEOUT) as client:
            while len(results) < max_results:
                params: dict[str, Any] = {
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "results_per_page": _PAGE_SIZE,
                    "what": keyword,
                    "where": location,
                    "content-type": "application/json",
                }

                url = f"{_BASE_URL}/{self.country}/search/{page}"
                self.requests_made += 1  # count the attempt even if it fails
                response = client.get(url, params=params)
                response.raise_for_status()

                data = response.json()
                page_results: list[RawListing] = data.get("results") or []
                if not page_results:
                    break

                results.extend(page_results)
                if len(page_results) < _PAGE_SIZE:
                    break  # fewer results than page size — this was the last page

                page += 1

        return results[:max_results]

    def normalize(self, raw: RawListing) -> JobListing:
        """Map a raw Adzuna listing dict to a canonical JobListing.

        Missing fields are set to null (never guessed). HTML is stripped from
        description. Seniority is inferred from the title then description.
        Salary period is always 'year' for Adzuna (they always return annual figures).
        """
        title = (raw.get("title") or "").strip()

        company_obj = raw.get("company") or {}
        # Never guess: a missing company stays empty and is flagged downstream;
        # its id is salted with the source id so unknown-company roles never merge.
        company = (company_obj.get("display_name") or "").strip()

        description = strip_html(raw.get("description") or "")

        loc_obj = raw.get("location") or {}
        location = _parse_location(loc_obj)

        # Remote detection needs title + description + location string
        is_remote = _detect_remote(title, description, location.raw)
        location = Location(
            raw=location.raw,
            city=location.city,
            region=location.region,
            country=location.country,
            is_remote=is_remote,
        )

        # Salary — Adzuna always returns annual figures
        sal_min = raw.get("salary_min")
        sal_max = raw.get("salary_max")
        if sal_min is not None or sal_max is not None:
            predicted = bool(raw.get("salary_is_predicted", 0))
            salary_raw: str | None = None
            if predicted:
                salary_raw = f"predicted: {sal_min}–{sal_max} {self._currency}/year"
            salary: Salary | None = Salary(
                min=float(sal_min) if sal_min is not None else None,
                max=float(sal_max) if sal_max is not None else None,
                currency=self._currency,
                period="year",
                raw=salary_raw,
            )
        else:
            salary = None

        seniority: Seniority | None = infer_seniority(title, description)
        employment = _parse_employment(raw)
        posted_at = _parse_posted_at(raw.get("created"))
        # The pipeline injects the run date; date.today() is only a fallback for
        # direct adapter use. The dedupe/seen merge owns "earliest first_seen_at".
        first_seen_at = self.run_date or date.today().isoformat()

        source = Source(
            name=self.name,
            url=(raw.get("redirect_url") or raw.get("url") or "").strip(),
            source_id=str(raw.get("id") or ""),
        )

        # Build listing with placeholder hashes, then fill them in
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
            # Salt with the source's own id: two unknown-company roles with the
            # same title+location must not collapse into one listing.
            listing.id = derive_id(f"__unknown_company__{source.source_id}", title, location)
        listing.content_hash = derive_content_hash(listing)
        return listing
