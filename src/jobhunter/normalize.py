# SPDX-License-Identifier: Apache-2.0
"""Stage 2 — Normalize.

Shared utilities for mapping raw source payloads to the canonical JobListing schema
and a post-adapter normalization pass for cross-adapter consistency.

Public surface:
  strip_html(text)                     — strip HTML tags and unescape entities
  parse_salary(raw, default_currency)  — parse free-text salary strings
  parse_location(raw)                  — parse free-text location strings
  infer_seniority(title, description)  — re-exported from jobhunter.model
  run(listings)                        — Stage-2 pipeline function

See: specs/02-functional-spec.md §Stage 2
     specs/03-data-model.md §JobListing
"""

from __future__ import annotations

import html as _html_mod
import re
from typing import Optional

from jobhunter.model import (
    JobListing,
    Location,
    Salary,
    derive_content_hash,
    infer_seniority,  # noqa: F401  re-exported for callers
)

__all__ = [
    "IC_LEVELS",
    "MANAGEMENT_LEVELS",
    "strip_html",
    "parse_salary",
    "parse_location",
    "infer_seniority",
    "run",
]

# ---------------------------------------------------------------------------
# Seniority track level lists — exported for dedupe stage title normalization
# ---------------------------------------------------------------------------

IC_LEVELS = ["intern", "junior", "mid", "senior", "staff", "principal"]
MANAGEMENT_LEVELS = ["manager", "senior_manager", "director", "vp"]

# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------


def strip_html(text: str) -> str:
    """Strip HTML tags and unescape HTML entities, collapsing whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Salary parsing
# ---------------------------------------------------------------------------

# Ordered (regex_pattern, ISO_4217_code).  More-specific patterns come first so
# "AU $" is recognized before the bare "$" fallback.
_CURRENCY_PATTERNS: list[tuple[str, str]] = [
    (r"AU\s*\$|(?<!\w)AUD(?!\w)", "AUD"),
    (r"NZ\s*\$|(?<!\w)NZD(?!\w)", "NZD"),
    (r"CA\s*\$|C\s*\$|(?<!\w)CAD(?!\w)", "CAD"),
    (r"US\s*\$|(?<!\w)USD(?!\w)", "USD"),
    (r"£|(?<!\w)GBP(?!\w)", "GBP"),
    (r"€|(?<!\w)EUR(?!\w)", "EUR"),
    (r"(?<!\w)SGD(?!\w)|S\$", "SGD"),
    (r"\$", "USD"),  # bare $ last — catches whatever the above missed
]

# Ordered (regex_pattern, canonical_period).  Checked case-insensitively.
_PERIOD_PATTERNS: list[tuple[str, str]] = [
    (r"per\s+(?:annum|year)|p\.a\.|/(?:year|yr|annum)\b|annual(?:ly)?", "year"),
    (r"per\s+month(?:ly)?|/(?:month|mo|mth)\b|monthly", "month"),
    (r"per\s+day|/(?:day|d)\b|daily", "day"),
    (r"per\s+hour(?:ly)?|/(?:hour|hr|h)\b|hourly", "hour"),
]

# Matches a numeric token with an optional k/m multiplier suffix.
_AMOUNT_RE = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*([kKmM]?)\b")


def _parse_amount(digits: str, multiplier: str) -> float:
    val = float(digits.replace(",", ""))
    m = multiplier.upper()
    if m == "K":
        val *= 1_000
    elif m == "M":
        val *= 1_000_000
    return val


def parse_salary(
    raw: Optional[str],
    default_currency: Optional[str] = None,
) -> Optional[Salary]:
    """Parse a free-text salary string into a canonical Salary.

    Returns None when raw is None or empty.
    Returns Salary(raw=text) with null numeric fields when no numbers are found —
    the caller should treat this as unknown salary (keep per spec policy).

    Args:
        raw: Free-text salary string, e.g. "$120k-$150k", "$900/day", "competitive".
        default_currency: ISO 4217 code to use when the string carries no symbol.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    # Detect currency (first match wins)
    currency: Optional[str] = default_currency
    for pattern, code in _CURRENCY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            currency = code
            break

    # Detect period (first match wins)
    period: Optional[str] = None
    for pattern, p in _PERIOD_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            period = p
            break

    # Strip currency and period markers so only numeric tokens remain
    clean = text
    for pattern, _ in _CURRENCY_PATTERNS:
        clean = re.sub(pattern, " ", clean, flags=re.IGNORECASE)
    for pattern, _ in _PERIOD_PATTERNS:
        clean = re.sub(pattern, " ", clean, flags=re.IGNORECASE)

    # Extract all amounts in left-to-right order
    amounts: list[float] = []
    for m in _AMOUNT_RE.finditer(clean):
        try:
            amounts.append(_parse_amount(m.group(1), m.group(2)))
        except ValueError:
            pass

    if not amounts:
        return Salary(raw=text)

    if len(amounts) == 1:
        v = amounts[0]
        return Salary(min=v, max=v, currency=currency, period=period, raw=text)

    return Salary(min=amounts[0], max=amounts[1], currency=currency, period=period, raw=text)


# ---------------------------------------------------------------------------
# Location parsing
# ---------------------------------------------------------------------------

# Lowercased country name → ISO 3166-1 alpha-2 (None for non-geographic terms)
_COUNTRY_NAMES: dict[str, Optional[str]] = {
    "australia": "AU",
    "australian": "AU",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "england": "GB",
    "canada": "CA",
    "new zealand": "NZ",
    "nz": "NZ",
    "germany": "DE",
    "france": "FR",
    "netherlands": "NL",
    "holland": "NL",
    "singapore": "SG",
    "india": "IN",
    "ireland": "IE",
    "spain": "ES",
    "italy": "IT",
    "poland": "PL",
    "portugal": "PT",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "switzerland": "CH",
    "austria": "AT",
    "belgium": "BE",
    "brazil": "BR",
    "mexico": "MX",
    "japan": "JP",
    "south africa": "ZA",
    "worldwide": None,
    "global": None,
    "anywhere": None,
}

# ISO 3166-1 alpha-2 codes we recognize (for bare 2-letter code detection)
_ISO2_CODES: frozenset[str] = frozenset(
    code for code in _COUNTRY_NAMES.values() if code is not None
)

# US state / territory 2-letter abbreviations
_US_STATES: frozenset[str] = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    }
)

# Australian state / territory abbreviations
_AU_STATES: frozenset[str] = frozenset({"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"})

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)


def _strip_remote_prefix(text: str) -> str:
    """Remove 'remote' keyword and surrounding punctuation, returning the geographic part."""
    text = re.sub(r"(?i)\bremote\b\s*[,;—–\-]?\s*", "", text)
    text = re.sub(r"[()]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ,;—–-")


def _strip_based_suffix(seg: str) -> str:
    """'Sydney-based' → 'Sydney', 'Based in Sydney' → 'Sydney'."""
    seg = re.sub(r"(?i)^based\s+in\s+", "", seg).strip()
    seg = re.sub(r"(?i)[- ]based$", "", seg).strip()
    return seg


def parse_location(raw: str) -> Location:
    """Parse a free-text location string into a canonical Location.

    Best-effort: fields that cannot be confidently identified are left null.
    The raw string is always preserved verbatim.
    """
    if not raw or not raw.strip():
        return Location(raw=raw or "")

    is_remote = bool(_REMOTE_RE.search(raw))

    # Strip 'remote' keyword to isolate the geographic portion
    geo = _strip_remote_prefix(raw) if is_remote else raw

    if not geo:
        return Location(raw=raw, is_remote=is_remote)

    # Normalize dashes and pipes to commas for uniform comma-splitting
    geo = re.sub(r"[—–|]+", ",", geo)
    segments = [s.strip() for s in geo.split(",") if s.strip()]

    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    remaining: list[str] = []

    for seg in segments:
        cleaned = _strip_based_suffix(seg)
        if not cleaned:
            continue
        lower = cleaned.lower()
        upper = cleaned.upper()

        if lower in _COUNTRY_NAMES:
            c = _COUNTRY_NAMES[lower]
            if c is not None:
                country = c
            continue

        # Bare 2-letter ISO country code
        if len(upper) == 2 and upper.isalpha() and upper in _ISO2_CODES:
            country = upper
            continue

        if upper in _US_STATES:
            region = upper
            country = country or "US"
            continue

        if upper in _AU_STATES:
            region = upper
            country = country or "AU"
            continue

        remaining.append(cleaned)

    # First remaining segment is the city; it may carry a trailing state abbreviation
    if remaining:
        city_seg = remaining[0]
        words = city_seg.rsplit(None, 1)
        if len(words) == 2:
            last = words[1].upper()
            if last in _US_STATES:
                city = words[0].strip() or None
                region = region or last
                country = country or "US"
            elif last in _AU_STATES:
                city = words[0].strip() or None
                region = region or last
                country = country or "AU"
            else:
                city = city_seg
        else:
            city = city_seg

    return Location(
        raw=raw,
        city=city or None,
        region=region,
        country=country,
        is_remote=is_remote,
    )


# ---------------------------------------------------------------------------
# Stage 2 pipeline entry point
# ---------------------------------------------------------------------------


def run(listings: list[JobListing]) -> list[JobListing]:
    """Stage 2 — post-adapter normalization pass.

    Adapters handle the primary raw-to-JobListing conversion. This stage adds
    defensive cross-adapter consistency: strips residual HTML from descriptions,
    canonicalizes empty strings to None, and re-derives content_hash when the
    description was cleaned.
    """
    result: list[JobListing] = []
    for listing in listings:
        changed = False

        desc = strip_html(listing.description)
        if desc != listing.description:
            listing.description = desc
            changed = True

        if listing.employment == "":
            listing.employment = None
            changed = True
        if listing.posted_at == "":
            listing.posted_at = None
            changed = True

        if changed:
            listing.content_hash = derive_content_hash(listing)

        result.append(listing)
    return result
