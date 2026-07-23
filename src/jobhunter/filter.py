# SPDX-License-Identifier: Apache-2.0
"""Stage 4 — Hard filter (disqualifiers).

Remove listings that fail any hard requirement from the profile (pass/fail, not scored).
Unknown-data policy: when the field a filter needs is null/unparseable, the listing is
KEPT (never dropped for missing data) and the unknown_flags entry notes the ambiguity
(e.g. "location unclear", "level unclear").

Filters applied in order:
  1. dismissed          — never re-surface
  2. exclude_locations  — drop even when labelled remote
  3. remote_policy      — remote_only | hybrid_ok | onsite_ok | any
  4. locations_allowed  — positive restriction (empty = any)
  5. seniority          — per-track min/max bounds; disallowed track = drop
  6. salary_floor       — comparable annualized max vs floor; unknown = keep
  7. exclude_employment — drop if in list; unknown = keep
  8. exclude_keywords   — word-boundary, case-insensitive, scoped
  9. max_age_days       — by posted_at, fallback first_seen_at

See: specs/02-functional-spec.md §Stage 4
     specs/03-data-model.md §Profile hard_requirements
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from jobhunter.model import IC_LEVELS as _IC_LEVELS
from jobhunter.model import MANAGEMENT_LEVELS as _MGMT_LEVELS
from jobhunter.model import PERIOD_MULTIPLIERS as _PERIOD_MULTIPLIERS
from jobhunter.model import JobListing, Location, term_pattern

# ---------------------------------------------------------------------------
# Title geo-hint detection
# ---------------------------------------------------------------------------

# Region acronyms that are unambiguous geographic scope markers in job titles.
_REGION_CODE_RE = re.compile(
    r"\b(EMEA|APAC|LATAM|AMER(?:ICAS?)?|ANZ|DACH|MENA|NOAM)\b",
    re.IGNORECASE,
)

# Country names that commonly appear in titles to indicate a geo restriction.
_GEO_COUNTRIES = [
    "Germany",
    "France",
    "United Kingdom",
    "UK",
    "Netherlands",
    "Spain",
    "Italy",
    "Canada",
    "Australia",
    "India",
    "Singapore",
    "Sweden",
    "Denmark",
    "Norway",
    "Finland",
    "Poland",
    "Portugal",
    "Brazil",
    "Mexico",
    "Japan",
]

_COUNTRIES_RE = "|".join(re.escape(c) for c in _GEO_COUNTRIES)

# Country after a separator: "Software Engineer - Germany", "Account Exec / UK"
_COUNTRY_AFTER_SEP_RE = re.compile(
    rf"[-/|:]\s*({_COUNTRIES_RE})\s*$",
    re.IGNORECASE,
)

# Country in parentheses at end: "Sales Manager (Germany)"
_COUNTRY_IN_PARENS_RE = re.compile(
    rf"\(\s*({_COUNTRIES_RE})\s*\)\s*$",
    re.IGNORECASE,
)

# Country as the final word: "Renewals Manager Germany"
_COUNTRY_AT_END_RE = re.compile(
    rf"\b({_COUNTRIES_RE})\s*$",
    re.IGNORECASE,
)


def _title_geo_hint(title: str) -> str | None:
    """Return a geo-region string found in the title, or None.

    Only returns a value when the title contains an unambiguous geographic
    scope marker — region acronyms (EMEA, APAC, …) or a country name in a
    position that signals scope restriction (after a separator, in parens, or
    as the final word).  Conservative by design: false negatives are fine;
    false positives (flagging a non-restricted role) are the real cost.
    """
    m = _REGION_CODE_RE.search(title)
    if m:
        return m.group(1).upper()
    for pat in (_COUNTRY_AFTER_SEP_RE, _COUNTRY_IN_PARENS_RE, _COUNTRY_AT_END_RE):
        m = pat.search(title)
        if m:
            return m.group(1).title()
    return None


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass
class FilterTally:
    """Counts of listings dropped per filter category — the run's filter tally."""

    by_location: int = 0
    by_seniority: int = 0
    by_salary: int = 0
    by_employment: int = 0
    by_keyword: int = 0
    by_required: int = 0
    by_age: int = 0
    dismissed: int = 0


@dataclass
class FilterResult:
    """Output of the hard-filter stage."""

    passed: list[JobListing]
    tally: FilterTally
    unknown_flags: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Location helpers
# ---------------------------------------------------------------------------


def _location_matches_any(loc: Location, entries: list[str]) -> bool:
    """Return True if any parsed field (city, region, country) matches an entry.

    Word-boundary matching per parsed field (never against the raw string):
    "Sydney" matches a parsed city of "Sydney Region"; "syd" matches nothing.
    Sources like Adzuna parse cities as region-qualified names, so exact
    field equality is too brittle.
    """
    fields = [p for p in [loc.city, loc.region, loc.country] if p]
    return any(term_pattern(e).search(f) for e in entries for f in fields)


def _has_geographic_info(loc: Location) -> bool:
    """True when at least one geographic field is parsed (city/region/country)."""
    return bool(loc.city or loc.region or loc.country)


def _passes_location(listing: JobListing, hr: dict) -> tuple[bool, list[str]]:
    """Apply exclude_locations + remote_policy + locations_allowed.

    Returns (passes, flags_for_unknown_data).  A listing with an ambiguous
    location is always kept; the returned flags are recorded in unknown_flags.
    """
    loc = listing.location
    flags: list[str] = []

    # 0. Region-restricted remote: a listing that is remote but restricted to a
    # parsed country outside remote_countries_allowed is not remote FOR THIS
    # USER — its remoteness no longer helps it pass. Unknown country = kept
    # (unknown-data policy): "Remote" with no country may be work-from-anywhere.
    allowed_rc = hr.get("remote_countries_allowed")
    effective_remote = loc.is_remote and (
        allowed_rc is None or loc.country is None or loc.country in [c.upper() for c in allowed_rc]
    )

    # 1. exclude_locations — drop even if labelled remote.
    excl_locs: list[str] = hr.get("exclude_locations", [])
    if excl_locs and _location_matches_any(loc, excl_locs):
        return False, []

    # 2. remote_policy
    remote_policy: str = hr.get("remote_policy", "any")
    if remote_policy == "remote_only":
        if effective_remote:
            pass  # Passes — definitively remote (and in an allowed remote country).
        elif loc.is_remote:
            return False, []  # Remote, but restricted to a disallowed country.
        elif _has_geographic_info(loc):
            return False, []  # Known non-remote location → drop.
        else:
            # No parsed location and is_remote=False: ambiguous → keep, mark.
            if "location unclear" not in flags:
                flags.append("location unclear")
    elif remote_policy in ("hybrid_ok", "onsite_ok"):
        # A listing that claims to be remote but is restricted to a country the
        # user can't work from is neither effectively remote nor an onsite option
        # for this user — it must be dropped regardless of locations_allowed.
        if loc.is_remote and not effective_remote:
            return False, []

    # 3. locations_allowed (positive restriction, empty = anywhere).
    locs_allowed: list[str] = hr.get("locations_allowed", [])
    if locs_allowed:
        # Remote listings are exempt from the allowlist for non-"any" policies —
        # but only when their remoteness counts for this user (region check above).
        exempt = effective_remote and remote_policy in ("remote_only", "hybrid_ok", "onsite_ok")
        if not exempt:
            if not _location_matches_any(loc, locs_allowed):
                if _has_geographic_info(loc):
                    return False, []  # Known location, not in allowlist → drop.
                elif "location unclear" not in flags:
                    flags.append("location unclear")

    # Remote with no geographic context: may be "remote — must be in excluded city".
    # If the title itself names a region or country, emit a more specific flag
    # so the digest reader can see the likely scope without false-positive drops.
    if loc.is_remote and not _has_geographic_info(loc):
        hint = _title_geo_hint(listing.title)
        flag = f"remote scope: title hints {hint}" if hint else "remote scope unclear"
        if flag not in flags:
            flags.append(flag)

    return True, flags


# ---------------------------------------------------------------------------
# Seniority helper
# ---------------------------------------------------------------------------


def _passes_seniority(listing: JobListing, hr: dict) -> tuple[bool, list[str]]:
    """Check seniority band per track.

    Returns (passes, flags_for_unknown_data).
    """
    seniority_bounds = hr.get("seniority")
    if seniority_bounds is None:
        return True, []  # No seniority filter configured.

    seniority = listing.seniority
    if seniority is None or seniority.track is None or seniority.level is None:
        return True, ["level unclear"]  # Unknown → keep, mark.

    track = seniority.track
    level = seniority.level

    # Track not in profile → disallowed.
    track_bounds = seniority_bounds.get(track)
    if track_bounds is None:
        return False, []

    ordered = _IC_LEVELS if track == "ic" else _MGMT_LEVELS
    if level not in ordered:
        return True, ["level unclear"]

    level_idx = ordered.index(level)
    min_level: Optional[str] = track_bounds.get("min")
    max_level: Optional[str] = track_bounds.get("max")

    if not min_level or min_level not in ordered:
        return True, ["level unclear"]

    min_idx = ordered.index(min_level)
    max_idx = ordered.index(max_level) if max_level and max_level in ordered else len(ordered) - 1

    if level_idx < min_idx or level_idx > max_idx:
        return False, []
    return True, []


# ---------------------------------------------------------------------------
# Salary helper
# ---------------------------------------------------------------------------


def _passes_salary(listing: JobListing, hr: dict) -> bool:
    """Apply salary_floor filter.

    Returns True (passes) or False (drop).  Unknown/incomparable salary is
    governed by keep_unknown_salary (default True).
    """
    salary_floor = hr.get("salary_floor")
    if salary_floor is None:
        return True

    keep_unknown: bool = hr.get("keep_unknown_salary", True)
    salary = listing.salary

    if salary is None or salary.max is None or salary.currency is None or salary.period is None:
        return keep_unknown

    salary_currency: str = hr.get("salary_currency", "")
    fx_rates: dict = hr.get("fx_rates") or {}

    if salary.currency == salary_currency:
        rate = 1.0
    elif salary.currency in fx_rates:
        rate = float(fx_rates[salary.currency])
    else:
        return keep_unknown  # No conversion rate available → unknown.

    multiplier = _PERIOD_MULTIPLIERS.get(salary.period)
    if multiplier is None:
        return keep_unknown  # Unknown period → cannot annualize → unknown.

    annualized_max = salary.max * multiplier * rate
    return annualized_max >= salary_floor


# ---------------------------------------------------------------------------
# Date helper
# ---------------------------------------------------------------------------


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------


def run(
    listings: list[JobListing],
    profile: dict,
    dismissed_ids: Optional[set[str]] = None,
    today: Optional[date] = None,
) -> FilterResult:
    """Stage 4 — apply all hard filters.

    Args:
        listings:      Listings from the dedupe stage.
        profile:       Loaded and validated profile dict.
        dismissed_ids: Set of listing ids permanently dismissed by the user.
        today:         Reference date (defaults to date.today(); inject in tests).

    Returns a FilterResult with the surviving listings, a drop tally, and a
    dict of per-listing unknown-field flags for listings that passed with ambiguity.
    """
    hr: dict = profile.get("hard_requirements", {})
    dismissed: set[str] = dismissed_ids or set()
    ref_date: date = today or date.today()
    max_age: int = int(hr.get("max_age_days", 30))

    tally = FilterTally()
    passed: list[JobListing] = []
    unknown_flags: dict[str, list[str]] = {}

    for listing in listings:
        listing_flags: list[str] = []
        drop_reason: Optional[str] = None

        # 1. Dismissed
        if listing.id in dismissed:
            tally.dismissed += 1
            drop_reason = "dismissed"

        # 2–4. Location (exclude_locations + remote_policy + locations_allowed)
        if drop_reason is None:
            passes, flags = _passes_location(listing, hr)
            if not passes:
                tally.by_location += 1
                drop_reason = "location"
            else:
                listing_flags.extend(flags)

        # 5. Seniority
        if drop_reason is None:
            passes, flags = _passes_seniority(listing, hr)
            if not passes:
                tally.by_seniority += 1
                drop_reason = "seniority"
            else:
                listing_flags.extend(flags)

        # 6. Salary floor
        if drop_reason is None:
            if not _passes_salary(listing, hr):
                tally.by_salary += 1
                drop_reason = "salary"

        # 7. Employment type
        if drop_reason is None:
            excl_emp: list[str] = hr.get("exclude_employment", [])
            if listing.employment and listing.employment in excl_emp:
                tally.by_employment += 1
                drop_reason = "employment"

        # 8. Deal-breaker keywords (word-boundary, case-insensitive, scoped)
        if drop_reason is None:
            for kw in hr.get("exclude_keywords", []):
                term: str = kw["term"]
                scope: str = kw.get("scope", "requirements")
                pattern = term_pattern(term)
                if pattern.search(listing.title):
                    tally.by_keyword += 1
                    drop_reason = "keyword"
                    break
                if scope == "requirements" and pattern.search(listing.description):
                    tally.by_keyword += 1
                    drop_reason = "keyword"
                    break

        # 8b. Required keywords — domain anchor: at least ONE must match
        if drop_reason is None:
            req = hr.get("require_keywords", [])
            if req:
                def _matches(kw: dict) -> bool:
                    pattern = term_pattern(kw["term"])
                    if pattern.search(listing.title):
                        return True
                    scope = kw.get("scope", "requirements")
                    return scope == "requirements" and bool(
                        pattern.search(listing.description)
                    )

                if not any(_matches(kw) for kw in req):
                    tally.by_required += 1
                    drop_reason = "required"

        # 9. Freshness
        if drop_reason is None:
            post_date = _parse_date(listing.posted_at) or _parse_date(listing.first_seen_at)
            if post_date is not None:
                cutoff = ref_date - timedelta(days=max_age)
                if post_date < cutoff:
                    tally.by_age += 1
                    drop_reason = "age"

        if drop_reason is None:
            passed.append(listing)
            if listing_flags:
                unknown_flags[listing.id] = listing_flags

    return FilterResult(passed=passed, tally=tally, unknown_flags=unknown_flags)
