# SPDX-License-Identifier: Apache-2.0
"""Canonical data model types and stable id/content_hash derivation.

See: specs/03-data-model.md
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Core shape types
# ---------------------------------------------------------------------------


@dataclass
class Location:
    raw: str
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    is_remote: bool = False


@dataclass
class Salary:
    min: Optional[float] = None
    max: Optional[float] = None
    currency: Optional[str] = None
    period: Optional[str] = None  # year | month | day | hour
    raw: Optional[str] = None


@dataclass
class Seniority:
    track: Optional[str] = None  # ic | management
    level: Optional[str] = None


@dataclass
class Source:
    name: str
    url: str
    source_id: str


@dataclass
class JobListing:
    """Canonical post-normalization listing.

    See: specs/03-data-model.md §JobListing
    """

    id: str
    content_hash: str
    title: str
    company: str
    location: Location
    description: str
    sources: list[Source]
    first_seen_at: str  # ISO date string
    salary: Optional[Salary] = None
    seniority: Optional[Seniority] = None
    employment: Optional[str] = None  # full_time | part_time | contract | temp | internship
    posted_at: Optional[str] = None  # ISO date string


@dataclass
class ScoreComponent:
    name: str
    sub: float  # 0–1
    weight: float
    reason: str


@dataclass
class ScoredResult:
    """Post-scoring output fed to the digest renderer.

    See: specs/03-data-model.md §ScoredResult
    """

    listing: JobListing
    score: float  # 0–100
    components: list[ScoreComponent] = field(default_factory=list)
    summary_reason: str = ""
    rank: int = 0
    unknown_flags: list[str] = field(default_factory=list)


@dataclass
class SeenEntry:
    id: str
    content_hash: str
    last_shown_at: str  # ISO date string


@dataclass
class RunState:
    """Persisted between runs.

    See: specs/03-data-model.md §Run state
    """

    schema_version: int
    seen: list[SeenEntry] = field(default_factory=list)
    dismissed_ids: list[str] = field(default_factory=list)
    last_run_at: Optional[str] = None  # ISO date string


@dataclass
class SourceFailure:
    name: str
    error: str


@dataclass
class SourceStat:
    """Per-source counters for one run.

    See: specs/03-data-model.md §Source stats
         specs/05-operator-tooling.md §5.1
    """

    name: str
    fetched: int = 0
    contributed: int = 0
    sole_source: int = 0
    passed_filter: int = 0
    shown: int = 0
    dismissed: int = 0
    requests: int = 0
    failed: bool = False
    error: Optional[str] = None


@dataclass
class RunReport:
    """Per-run metadata shown in the digest header.

    See: specs/03-data-model.md §Run report
    """

    run_at: str  # ISO date string
    sources_used: list[str] = field(default_factory=list)
    sources_failed: list[SourceFailure] = field(default_factory=list)
    requests_made: int = 0
    truncated: bool = False
    ingested_count: int = 0
    after_dedupe: int = 0
    dropped_by_location: int = 0
    dropped_by_seniority: int = 0
    dropped_by_salary: int = 0
    dropped_by_employment: int = 0
    dropped_by_keyword: int = 0
    dropped_by_required: int = 0
    dropped_by_age: int = 0
    dropped_dismissed: int = 0
    below_threshold: int = 0
    shown_new: int = 0
    shown_previous: int = 0
    active_weights: dict[str, float] = field(default_factory=dict)
    search_mode: Optional[str] = None  # active posture preset, for the digest header
    fx_rates_stale_days: Optional[int] = None  # set when fx_rates.yaml is older than 90 days
    source_stats: list["SourceStat"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Canonical seniority tracks and period annualization — SINGLE SOURCE OF TRUTH.
# Import these everywhere; do not redefine (spec 03 §Seniority, 02 §Stage 4).
# ---------------------------------------------------------------------------

IC_LEVELS = ["intern", "junior", "mid", "senior", "staff", "principal"]
MANAGEMENT_LEVELS = ["manager", "senior_manager", "director", "vp"]

PERIOD_MULTIPLIERS: dict[str, float] = {
    "year": 1.0,
    "month": 12.0,
    "day": 260.0,
    "hour": 2080.0,
}


# ---------------------------------------------------------------------------
# Term matching
# ---------------------------------------------------------------------------


def term_pattern(term: str) -> "re.Pattern[str]":
    """Case-insensitive whole-term pattern safe for symbol-edged terms.

    `\b + re.escape(term)` fails for terms like "C++" or ".NET" whose edges are
    non-word characters (\b needs a word char adjacent). Use explicit lookarounds
    instead when a term edge is non-word.
    """
    esc = re.escape(term)
    first, last = term[:1], term[-1:]
    start = r"\b" if (first.isalnum() or first == "_") else r"(?<!\w)"
    end = r"\b" if (last.isalnum() or last == "_") else r"(?!\w)"
    return re.compile(start + esc + end, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Title normalization rules for id derivation
# Ordered list of (pattern, replacement) tuples; applied word-boundary,
# case-insensitive. Same rule table used by seniority inference in Stage 2.
# ---------------------------------------------------------------------------

_TITLE_SUBSTITUTIONS: list[tuple[str, str]] = [
    (r"\bsr\.?\b", "senior"),
    (r"\bsnr\.?\b", "senior"),
    (r"\bjr\.?\b", "junior"),
    (r"\blead\b", "staff"),
]


def _normalize_title_for_id(title: str) -> str:
    """Strip title decorations so that e.g. 'Sr. Engineer' and 'Senior Engineer' share an id."""
    # Remove trailing parenthesized team qualifiers, e.g. "(Backend Team)", "(Fintech)"
    t = re.sub(r"\s*\([^)]*\)\s*$", "", title.strip())
    for pattern, replacement in _TITLE_SUBSTITUTIONS:
        t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)
    return t


def _clean_key_segment(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s)  # drop punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s


def derive_id(company: str, title: str, location: Location) -> str:
    """Return a stable SHA-256 hex id for a listing.

    Derived from the normalized identity key: (company + title + city|country|"remote").
    Volatile fields (URL, posted date, sources) are never included.

    See: specs/03-data-model.md §Notes on identity & dedupe
    """
    norm_title = _normalize_title_for_id(title)

    # For fully-remote listings with no city, use country if present, else "remote".
    if location.city:
        loc_part = location.city
    elif location.country:
        loc_part = location.country
    elif location.is_remote:
        loc_part = "remote"
    else:
        loc_part = ""

    key = " ".join(_clean_key_segment(part) for part in [company, norm_title, loc_part] if part)
    return hashlib.sha256(key.encode()).hexdigest()


def derive_content_hash(listing: "JobListing") -> str:
    """Return a SHA-256 hex hash of the listing's substantive (non-volatile) fields.

    Used to detect material changes between runs (same id, different hash = re-surface as new).
    Excludes sources[], posted_at, first_seen_at.

    See: specs/03-data-model.md §Notes on identity & dedupe
    """
    salary = listing.salary
    parts = [
        listing.title,
        str(salary.min) if salary and salary.min is not None else "",
        str(salary.max) if salary and salary.max is not None else "",
        (salary.currency or "") if salary else "",
        (salary.period or "") if salary else "",
        listing.location.raw,
        listing.description,
    ]
    content = "\x00".join(parts)  # NUL-separated to avoid accidental collisions
    return hashlib.sha256(content.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Seniority inference
# Ordered rules: (pattern, track, level). First title match wins; if no title
# match, first description match wins; otherwise returns None (unknown).
# Same rule table drives title normalization (dedupe) above.
# See: specs/02-functional-spec.md §Seniority inference
# ---------------------------------------------------------------------------

_SENIORITY_RULES: list[tuple[str, str, str]] = [
    # Management track checked before IC so "manager" doesn't match senior-manager's "senior"
    (r"\b(vp|vice[\s-]president)\b", "management", "vp"),
    (r"\bdirector\b", "management", "director"),
    (r"\bsenior[\s-]manager\b", "management", "senior_manager"),
    (r"\b(head of|head,)\b", "management", "director"),
    (r"\bmanager\b", "management", "manager"),
    # IC track
    (r"\bprincipal\b", "ic", "principal"),
    (r"\bstaff\b", "ic", "staff"),
    (r"\b(senior|sr\.?|snr\.?)\b", "ic", "senior"),
    (r"\b(mid|middle|intermediate)\b", "ic", "mid"),
    (r"\b(junior|jr\.?|graduate|grad)\b", "ic", "junior"),
    (r"\b(intern|internship|trainee|apprentice)\b", "ic", "intern"),
    # "lead" maps to IC staff per spec rule table
    (r"\blead\b", "ic", "staff"),
]


def infer_seniority(title: str, description: str = "") -> Optional[Seniority]:
    """Infer seniority from title (primary) then description (secondary).

    Returns None when no rule matches — the unknown-data policy applies downstream.

    See: specs/02-functional-spec.md §Seniority inference
    """
    for pattern, track, level in _SENIORITY_RULES:
        if re.search(pattern, title, re.IGNORECASE):
            return Seniority(track=track, level=level)
    for pattern, track, level in _SENIORITY_RULES:
        if re.search(pattern, description, re.IGNORECASE):
            return Seniority(track=track, level=level)
    return None
