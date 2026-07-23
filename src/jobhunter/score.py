# SPDX-License-Identifier: Apache-2.0
"""Stage 5 — Score (soft preferences).

Purely rule-based, deterministic weighted sum of components; weights come from the profile.
score = 100 × Σ(wᵢ · subᵢ) / Σ(wᵢ)  over active (non-zero) weights.
Unknown fields yield the neutral sub-score (0.5), never 0.
No network calls; deterministic given the same listing + profile.

Components:
  skill_match    — overlap of target_skills with title + description
  seniority_fit  — distance from target level on the listing's own track
  compensation   — comparable annualized salary vs floor and target
  location_fit   — preferred location or fully-remote scores higher
  company_signal — bonus for preferred_companies matches
  recency        — newer postings score slightly higher

See: specs/02-functional-spec.md §Stage 5
     specs/03-data-model.md §ScoredResult
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Optional

from jobhunter.model import IC_LEVELS as _IC_LEVELS
from jobhunter.model import MANAGEMENT_LEVELS as _MGMT_LEVELS
from jobhunter.model import PERIOD_MULTIPLIERS as _PERIOD_MULTIPLIERS
from jobhunter.model import JobListing, ScoreComponent, ScoredResult, term_pattern

_RECENCY_HALF_LIFE_DAYS = 14


# Matching this many target skills means a full-marks skill fit. Without
# saturation, a rich skill list (15+ terms) makes full marks unreachable —
# especially against truncated source snippets — deflating every score and
# punishing thorough profiles.
_SKILL_SATURATION = 5


def _score_skill_match(listing: JobListing, target_skills: list[str]) -> tuple[float, str]:
    """Word-boundary, case-insensitive overlap of target skills with title + description.

    Saturating: matching _SKILL_SATURATION (or every) target skill = full marks.
    """
    if not target_skills:
        return 0.5, "no target skills configured"
    text = listing.title + " " + listing.description
    matched = [skill for skill in target_skills if term_pattern(skill).search(text)]
    saturation = min(len(target_skills), _SKILL_SATURATION)
    ratio = min(1.0, len(matched) / saturation)
    if matched:
        shown = ", ".join(matched[:3])
        suffix = f" +{len(matched) - 3} more" if len(matched) > 3 else ""
        reason = f"{len(matched)}/{len(target_skills)} target skills found: {shown}{suffix}"
    else:
        reason = f"0/{len(target_skills)} target skills found"
    return ratio, reason


def _level_distance_sub(level: str, target_level: str, ordered: list[str]) -> float:
    """Map distance between two levels on the same track to a [0, 1] sub-score."""
    if level not in ordered or target_level not in ordered:
        return 0.5
    dist = abs(ordered.index(level) - ordered.index(target_level))
    if dist == 0:
        return 1.0
    elif dist == 1:
        return 0.75
    elif dist == 2:
        return 0.5
    else:
        return max(0.0, 0.5 - 0.1 * (dist - 2))


def _score_seniority_fit(listing: JobListing, targets: list[dict]) -> tuple[float, str]:
    """Distance from profile target level on the listing's own track."""
    seniority = listing.seniority
    if seniority is None or seniority.track is None or seniority.level is None:
        return 0.5, "level unclear"

    track = seniority.track
    level = seniority.level
    ordered = _IC_LEVELS if track == "ic" else _MGMT_LEVELS

    track_targets = [t for t in targets if t.get("track") == track]
    if not track_targets:
        return 0.5, f"{track} track not in profile targets"

    target_level = track_targets[0]["level"]
    sub = _level_distance_sub(level, target_level, ordered)
    if sub == 1.0:
        reason = f"exact match: {level} ({track})"
    else:
        reason = f"{level} vs target {target_level} ({track} track)"
    return sub, reason


def _annualized(salary_max: float, period: str, rate: float) -> Optional[float]:
    multiplier = _PERIOD_MULTIPLIERS.get(period)
    if multiplier is None:
        return None
    return salary_max * multiplier * rate


def _score_compensation(listing: JobListing, profile: dict) -> tuple[float, str]:
    """How far annualized salary max exceeds the floor toward the target."""
    hr = profile.get("hard_requirements", {})
    prefs = profile.get("preferences", {})

    salary_floor = hr.get("salary_floor")
    salary_target = prefs.get("salary_target")
    salary_currency: str = hr.get("salary_currency", "")
    fx_rates: dict = hr.get("fx_rates") or {}

    salary = listing.salary
    if salary is None or salary.max is None or salary.currency is None or salary.period is None:
        return 0.5, "salary unknown"

    if salary.currency == salary_currency:
        rate = 1.0
    elif salary.currency in fx_rates:
        rate = float(fx_rates[salary.currency])
    else:
        return 0.5, f"no FX rate for {salary.currency}"

    ann = _annualized(salary.max, salary.period, rate)
    if ann is None:
        return 0.5, "salary period unknown"

    if salary_target is None:
        return 0.5, f"annualized {salary_currency} {ann:,.0f} (no salary target configured)"

    floor = float(salary_floor) if salary_floor is not None else 0.0
    target = float(salary_target)

    if target <= floor:
        sub = 1.0 if ann >= floor else 0.0
    else:
        sub = max(0.0, min(1.0, (ann - floor) / (target - floor)))

    reason = f"{salary_currency} {ann:,.0f} annualized (floor {floor:,.0f}, target {target:,.0f})"
    return sub, reason


def _score_location_fit(listing: JobListing, preferences: dict) -> tuple[float, str]:
    """Preferred location or fully-remote scores higher than merely-allowed."""
    preferred_locations: list[str] = preferences.get("preferred_locations") or []
    loc = listing.location
    parsed_fields = {f.lower() for f in [loc.city, loc.region, loc.country] if f}

    if not preferred_locations:
        if loc.is_remote:
            return 0.75, "remote role"
        return 0.5, "no location preference configured"

    from jobhunter.normalize import _COUNTRY_NAMES  # country-name → ISO map

    def _geo_word_matches(w: str) -> bool:
        iso = (_COUNTRY_NAMES.get(w) or "").lower()
        for field in parsed_fields:
            if term_pattern(w).search(field):
                return True
            if iso and term_pattern(iso).search(field):
                return True
        return False

    for pref in preferred_locations:
        pref_lower = pref.lower()
        pref_words = set(re.split(r"[\s,]+", pref_lower)) - {""}
        geo_words = pref_words - {"remote"}
        geo_match = any(_geo_word_matches(w) for w in geo_words)
        if "remote" in pref_words:
            # "Remote" alone: any remote role. "Remote Australia": remote AND in AU.
            if loc.is_remote and (not geo_words or geo_match):
                return 1.0, f"matches preferred location: {pref}"
        elif geo_match:
            return 1.0, f"matches preferred location: {pref}"

    if loc.is_remote:
        return 0.75, "remote role (not in preferred locations)"
    return 0.5, "allowed but not in preferred locations"


def _score_company_signal(listing: JobListing, preferences: dict) -> tuple[float, str]:
    """Bonus for preferred_companies matches; 0 for non-matches."""
    preferred_companies: list[str] = preferences.get("preferred_companies") or []
    if not preferred_companies:
        return 0.5, "no preferred companies configured"
    company_lower = listing.company.lower()
    for company in preferred_companies:
        if company.lower() == company_lower:
            return 1.0, f"preferred company: {company}"
    return 0.5, "company not in preferred list (neutral)"


def _score_recency(listing: JobListing, today: Optional[date] = None) -> tuple[float, str]:
    """Exponential decay by posting age; half-life of 14 days."""
    ref = today or date.today()
    date_str = listing.posted_at or listing.first_seen_at
    if not date_str:
        return 0.5, "posting date unknown"
    try:
        posted = date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return 0.5, "posting date unparseable"
    age_days = max(0, (ref - posted).days)
    sub = max(0.0, min(1.0, math.exp(-age_days * math.log(2) / _RECENCY_HALF_LIFE_DAYS)))
    label = f"{age_days} day{'s' if age_days != 1 else ''} ago"
    return sub, f"posted {label}"


def score(
    listing: JobListing,
    profile: dict,
    unknown_flags: Optional[list[str]] = None,
    today: Optional[date] = None,
) -> ScoredResult:
    """Score a single listing against the profile.

    Args:
        listing:       A JobListing that passed the hard filter.
        profile:       Loaded and validated profile dict.
        unknown_flags: Pre-computed flags from the filter stage (carried through).
        today:         Reference date for recency (defaults to date.today()).

    Returns a ScoredResult with rank=0; the rank stage assigns the final rank.
    """
    weights_cfg: dict = profile.get("weights", {})
    identity: dict = profile.get("identity", {})
    prefs: dict = profile.get("preferences", {})
    target_skills: list[str] = identity.get("target_skills", [])
    targets: list[dict] = identity.get("target", [])

    component_fns = [
        ("skill_match", lambda: _score_skill_match(listing, target_skills)),
        ("seniority_fit", lambda: _score_seniority_fit(listing, targets)),
        ("compensation", lambda: _score_compensation(listing, profile)),
        ("location_fit", lambda: _score_location_fit(listing, prefs)),
        ("company_signal", lambda: _score_company_signal(listing, prefs)),
        ("recency", lambda: _score_recency(listing, today)),
    ]

    components: list[ScoreComponent] = []
    for name, fn in component_fns:
        weight = float(weights_cfg.get(name, 0))
        if weight == 0:
            continue
        sub, reason = fn()
        components.append(ScoreComponent(name=name, sub=sub, weight=weight, reason=reason))

    total_weight = sum(c.weight for c in components)
    if total_weight == 0:
        score_val = 0.0
    else:
        score_val = 100.0 * sum(c.sub * c.weight for c in components) / total_weight

    if components:
        top = sorted(components, key=lambda c: c.sub * c.weight, reverse=True)
        summary_reason = "; ".join(c.reason for c in top[:2])
    else:
        summary_reason = "no active scoring components"

    return ScoredResult(
        listing=listing,
        score=round(score_val, 2),
        components=components,
        summary_reason=summary_reason,
        rank=0,
        unknown_flags=list(unknown_flags or []),
    )


def run(
    listings: list[JobListing],
    profile: dict,
    unknown_flags: Optional[dict[str, list[str]]] = None,
    today: Optional[date] = None,
) -> list[ScoredResult]:
    """Score all passed listings.

    Args:
        listings:      Listings from Stage 4 (hard filter passed).
        profile:       Loaded and validated profile dict.
        unknown_flags: Per-listing flags from the filter stage (keyed by listing.id).
        today:         Reference date for recency (defaults to date.today()).

    Returns unranked ScoredResult list; ranking is Stage 6.
    """
    flags_map = unknown_flags or {}
    return [
        score(listing, profile, unknown_flags=flags_map.get(listing.id), today=today)
        for listing in listings
    ]
