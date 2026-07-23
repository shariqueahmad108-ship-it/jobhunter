# SPDX-License-Identifier: Apache-2.0
"""Golden fixture corpus for JobHunter pipeline acceptance tests.

30+ anonymized listings drawn from the open source / community / governance
search domain (OSPO, DevRel, Head of Community, community manager roles),
covering every awkward edge case called out in specs/04-technical-plan.md §Fixtures.

REF_DATE is injected into filter.run() and score.score() so the suite is
deterministic regardless of when it runs.

Sections:
  FIXTURE_PROFILE — community/OSPO/DevRel oriented profile
  CORPUS_PASS     — 10 listings that should pass the hard filter
  CORPUS_FAIL     — 10 listings that should fail the hard filter
  CORPUS_EDGE     —  5 edge-case listings that pass (day-rate, EUR unknown, etc.)
  CORPUS          — the union of all three lists above
  MERGE_PAIRS     — 10 (a, b) pairs that dedupe.run() should collapse to 1
  NO_MERGE_PAIRS  — 10 (a, b) pairs that dedupe.run() must keep as 2

See: specs/04-technical-plan.md §Fixtures
     specs/02-functional-spec.md §Stage 2–5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from jobhunter.model import (
    JobListing,
    Location,
    Salary,
    Seniority,
    Source,
    derive_content_hash,
    derive_id,
)

# ---------------------------------------------------------------------------
# Fixed reference date — inject this into all stage functions in tests
# ---------------------------------------------------------------------------
REF_DATE = date(2026, 7, 23)
REF_DATE_STR = REF_DATE.isoformat()


def _days_ago(n: int) -> str:
    return (REF_DATE - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------
# Fixture profile — community / OSPO / DevRel oriented
# ---------------------------------------------------------------------------
FIXTURE_PROFILE: dict = {
    "identity": {
        "target_skills": [
            "open source",
            "developer relations",
            "community",
            "Python",
            "DevRel",
            "governance",
            "OSPO",
            "documentation",
        ],
        "target": [
            {"track": "ic", "level": "staff"},
            {"track": "management", "level": "director"},
        ],
    },
    "queries": {
        "keywords": ["OSPO", "community", "developer relations", "DevRel"],
        "locations": ["Remote", "Remote Australia"],
        "max_results_per_query": 50,
        "max_requests_per_run": 100,
    },
    "hard_requirements": {
        "remote_policy": "remote_only",
        "exclude_locations": ["Sydney"],
        "locations_allowed": [],
        "seniority": {
            "ic": {"min": "mid", "max": None},
            "management": {"min": "manager", "max": "director"},
        },
        "salary_floor": 160_000,
        "salary_currency": "AUD",
        # GBP included so C05 (GBP 90k) converts cleanly
        "fx_rates": {"USD": 1.50, "NZD": 0.93, "GBP": 1.95},
        "keep_unknown_salary": True,
        "exclude_employment": ["internship"],
        "exclude_keywords": [
            {"term": "PHP", "scope": "title"},
            {"term": "security clearance", "scope": "requirements"},
        ],
        "require_keywords": [],
        "max_age_days": 21,
    },
    "preferences": {
        "preferred_locations": ["Remote Australia"],
        "salary_target": 220_000,
        "preferred_companies": ["GitHub", "Atlassian", "Canonical"],
    },
    "weights": {
        "skill_match": 30,
        "seniority_fit": 20,
        "compensation": 20,
        "location_fit": 15,
        "company_signal": 10,
        "recency": 5,
    },
    "output": {
        "display_threshold": 0,
        "max_shown": 25,
        "show_previously_seen": True,
        "format": "markdown",
    },
}

# ---------------------------------------------------------------------------
# Description constants
# ---------------------------------------------------------------------------

# Rich description: hits all 8 target skills → saturates skill_match at 5/5
_RICH_DESC = (
    "We are looking for an experienced professional to lead our open source "
    "community and developer relations efforts. Responsibilities include "
    "community governance, OSPO strategy, developer relations programs, Python "
    "tooling, and documentation. Experience with open source projects, DevRel, "
    "and governance is required. Join our growing developer community team."
)

_PLAIN_DESC = "A remote community or developer relations role with competitive benefits."

_CLEARANCE_DESC = (
    "This role requires security clearance for government projects. "
    "You will manage the developer community and open source initiatives."
)


# ---------------------------------------------------------------------------
# Helper — build a fully-formed JobListing with stable id and content_hash
# ---------------------------------------------------------------------------

_SRC_COUNTER = 0


def _src(
    name: str = "adzuna",
    url: Optional[str] = None,
    source_id: Optional[str] = None,
) -> Source:
    global _SRC_COUNTER
    _SRC_COUNTER += 1
    sid = source_id if source_id is not None else f"corpus-{_SRC_COUNTER}"
    u = url if url is not None else f"https://www.adzuna.com.au/jobs/details/{sid}"
    return Source(name=name, url=u, source_id=sid)


def _make(
    *,
    title: str,
    company: str,
    location_raw: str,
    description: str = _RICH_DESC,
    is_remote: bool = True,
    city: Optional[str] = None,
    region: Optional[str] = None,
    country: Optional[str] = None,
    salary_raw: Optional[str] = None,
    salary_min: Optional[float] = None,
    salary_max: Optional[float] = None,
    salary_currency: Optional[str] = None,
    salary_period: Optional[str] = None,
    seniority_track: Optional[str] = None,
    seniority_level: Optional[str] = None,
    employment: Optional[str] = None,
    posted_at: Optional[str] = None,
    first_seen_at: str = REF_DATE_STR,
    sources: Optional[list[Source]] = None,
) -> JobListing:
    loc = Location(
        raw=location_raw,
        city=city,
        region=region,
        country=country,
        is_remote=is_remote,
    )
    sal: Optional[Salary] = None
    if salary_raw is not None or salary_min is not None:
        sal = Salary(
            raw=salary_raw,
            min=salary_min,
            max=salary_max if salary_max is not None else salary_min,
            currency=salary_currency,
            period=salary_period,
        )
    sen: Optional[Seniority] = None
    if seniority_track is not None:
        sen = Seniority(track=seniority_track, level=seniority_level)

    if sources is None:
        sources = [_src()]

    listing = JobListing(
        id="",
        content_hash="",
        title=title,
        company=company,
        location=loc,
        description=description,
        sources=sources,
        first_seen_at=first_seen_at,
        salary=sal,
        seniority=sen,
        employment=employment,
        posted_at=posted_at,
    )
    listing.id = derive_id(company, title, loc)
    listing.content_hash = derive_content_hash(listing)
    return listing


# ---------------------------------------------------------------------------
# CorpusEntry — one case with stage-by-stage expected outcomes
# ---------------------------------------------------------------------------


@dataclass
class CorpusEntry:
    """A corpus case with the normalized listing plus expected stage outcomes.

    Fields:
      listing               — post-normalization JobListing (ready for Stage 3+)
      case                  — human label used in pytest failure messages

      filter_pass           — expected Stage-4 verdict
      filter_drop_reason    — expected drop category (location/seniority/salary/
                              keyword/age/employment) when filter_pass=False
      filter_unknown_flags  — expected unknown_flags entries when filter_pass=True

      expected_seniority_track/level — expected from infer_seniority(listing.title)
                              None means inference returns None

      expected_score_min/max — expected Stage-5 score range (inclusive);
                              None means score is not asserted for this case
    """

    listing: JobListing
    case: str

    # Stage 4
    filter_pass: Optional[bool] = None
    filter_drop_reason: Optional[str] = None
    filter_unknown_flags: list[str] = field(default_factory=list)

    # Stage 2 — seniority inference from title only
    expected_seniority_track: Optional[str] = None
    expected_seniority_level: Optional[str] = None

    # Stage 5
    expected_score_min: Optional[float] = None
    expected_score_max: Optional[float] = None


# ---------------------------------------------------------------------------
# CORPUS — Part A  Passes the hard filter
#
# Score formulas (weights total = 100, floor=160k, target=220k AUD):
#   skill_match  (w=30): all rich-desc listings saturate at 5/8 → sub=1.0
#   seniority    (w=20): exact=1.0, dist-1=0.75, dist-2=0.5, unknown=0.5
#   compensation (w=20): (ann_max - 160k) / 60k, clamped 0–1; unknown=0.5
#   location_fit (w=15): Remote AU = 1.0; bare remote or non-AU remote = 0.75
#   company      (w=10): preferred=1.0; other=0.5
#   recency      (w=5):  2^(-age_days/14)
# ---------------------------------------------------------------------------

CORPUS_PASS: list[CorpusEntry] = [
    # C01 — preferred company, exact management/director, Remote AU, AUD 195k, 2 days ago
    # score ≈ 30 + 20 + 11.67 + 15 + 10 + 4.53 = 91.2
    CorpusEntry(
        listing=_make(
            title="Head of Community",
            company="Atlassian",
            location_raw="Remote Australia",
            country="AU",
            salary_min=195_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 195,000 per year",
            seniority_track="management",
            seniority_level="director",
            posted_at=_days_ago(2),
        ),
        case="C01 Atlassian Head-of-Community preferred+director Remote-AU AUD195k",
        filter_pass=True,
        expected_seniority_track="management",
        expected_seniority_level="director",
        expected_score_min=88.0,
        expected_score_max=94.0,
    ),
    # C02 — USD salary converted via FX (130k USD × 1.5 = AUD 195k), ic/staff,
    #        bare Remote → remote_scope_unclear flag
    # score ≈ 30 + 20 + 11.67 + 11.25 + 5 + 3.90 = 81.8
    CorpusEntry(
        listing=_make(
            title="OSPO Lead",
            company="Red Hat",
            location_raw="Remote",
            salary_min=130_000,
            salary_currency="USD",
            salary_period="year",
            salary_raw="USD 130,000 per year",
            seniority_track="ic",
            seniority_level="staff",
            posted_at=_days_ago(5),
        ),
        case="C02 Red-Hat OSPO-Lead USD-FX bare-remote ic/staff",
        filter_pass=True,
        filter_unknown_flags=["remote scope unclear"],
        expected_seniority_track="ic",
        expected_seniority_level="staff",
        expected_score_min=79.0,
        expected_score_max=85.0,
    ),
    # C03 — ic/staff, Remote AU, AUD 185k, non-preferred company, 7 days ago
    # score ≈ 30 + 20 + 8.33 + 15 + 5 + 3.54 = 81.9
    CorpusEntry(
        listing=_make(
            title="Staff Developer Advocate",
            company="HashiCorp",
            location_raw="Remote Australia",
            country="AU",
            salary_min=185_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 185,000 per year",
            seniority_track="ic",
            seniority_level="staff",
            posted_at=_days_ago(7),
        ),
        case="C03 HashiCorp Staff-DevAdv Remote-AU AUD185k",
        filter_pass=True,
        expected_seniority_track="ic",
        expected_seniority_level="staff",
        expected_score_min=79.0,
        expected_score_max=85.0,
    ),
    # C04 — management/director, bare Remote (remote_scope_unclear), AUD 210k, 3 days ago
    # score ≈ 30 + 20 + 16.67 + 11.25 + 5 + 4.31 = 87.2
    CorpusEntry(
        listing=_make(
            title="Director of Developer Relations",
            company="Grafana Labs",
            location_raw="Remote",
            salary_min=210_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 210,000 per year",
            seniority_track="management",
            seniority_level="director",
            posted_at=_days_ago(3),
        ),
        case="C04 Grafana-Labs Director-DevRel bare-remote AUD210k",
        filter_pass=True,
        filter_unknown_flags=["remote scope unclear"],
        expected_seniority_track="management",
        expected_seniority_level="director",
        expected_score_min=84.0,
        expected_score_max=91.0,
    ),
    # C05 — GBP salary (90k × 1.95 = AUD 175.5k, above floor), preferred company Canonical,
    #        Remote UK (non-AU remote, loc=0.75)
    #        "Senior Community Manager" → manager rule fires before IC-senior rule
    #        → seniority=management/manager (not ic/senior!)  ← awkward inference case
    # score ≈ 30 + 10 + 5.17 + 11.25 + 10 + 3.03 = 69.4
    CorpusEntry(
        listing=_make(
            title="Senior Community Manager",
            company="Canonical",
            location_raw="Remote UK",
            country="GB",
            salary_min=90_000,
            salary_currency="GBP",
            salary_period="year",
            salary_raw="GBP 90,000 per year",
            seniority_track="management",
            seniority_level="manager",
            posted_at=_days_ago(10),
        ),
        case="C05 Canonical Senior-CM GBP-FX Remote-UK management/manager (not ic/senior)",
        filter_pass=True,
        expected_seniority_track="management",
        expected_seniority_level="manager",
        expected_score_min=66.0,
        expected_score_max=73.0,
    ),
    # C06 — unknown seniority ("Community Strategist" has no seniority keyword)
    #        → filter keeps it + "level unclear" flag; seniority_fit sub=0.5
    # score ≈ 30 + 10 + 4.0 + 15 + 5 + 4.76 = 68.8
    CorpusEntry(
        listing=_make(
            title="Community Strategist",
            company="GitLab",
            location_raw="Remote Australia",
            country="AU",
            salary_min=172_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 172,000 per year",
            posted_at=_days_ago(1),
        ),
        case="C06 GitLab Community-Strategist unknown-seniority Remote-AU",
        filter_pass=True,
        filter_unknown_flags=["level unclear"],
        expected_seniority_track=None,
        expected_seniority_level=None,
        expected_score_min=66.0,
        expected_score_max=72.0,
    ),
    # C07 — no salary at all (keep_unknown_salary=True), ic/staff, bare Remote, 14 days ago
    # score ≈ 30 + 20 + 10.0 + 11.25 + 5 + 2.5 = 78.75
    CorpusEntry(
        listing=_make(
            title="Open Source Program Office Lead",
            company="Cloudflare",
            location_raw="Remote",
            seniority_track="ic",
            seniority_level="staff",
            posted_at=_days_ago(14),
        ),
        case="C07 Cloudflare OSPO-Lead no-salary bare-remote 14-days-old",
        filter_pass=True,
        filter_unknown_flags=["remote scope unclear"],
        expected_seniority_track="ic",
        expected_seniority_level="staff",
        expected_score_min=75.0,
        expected_score_max=83.0,
    ),
    # C08 — USD salary barely above floor (110k × 1.5 = AUD 165k > 160k), ic/staff, Remote AU
    # score ≈ 30 + 20 + 1.67 + 15 + 5 + 4.10 = 75.8
    CorpusEntry(
        listing=_make(
            title="Developer Relations Lead",
            company="Elastic",
            location_raw="Remote Australia",
            country="AU",
            salary_min=110_000,
            salary_currency="USD",
            salary_period="year",
            salary_raw="USD 110,000 per year",
            seniority_track="ic",
            seniority_level="staff",
            posted_at=_days_ago(4),
        ),
        case="C08 Elastic DevRel-Lead USD-barely-above-floor Remote-AU",
        filter_pass=True,
        expected_seniority_track="ic",
        expected_seniority_level="staff",
        expected_score_min=73.0,
        expected_score_max=79.0,
    ),
    # C09 — management/director, bare Remote (remote_scope_unclear), AUD 180k, 8 days ago
    # score ≈ 30 + 20 + 6.67 + 11.25 + 5 + 3.37 = 76.3
    CorpusEntry(
        listing=_make(
            title="Head of Open Source",
            company="Fictive Corp",
            location_raw="Remote",
            salary_min=180_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 180,000 per year",
            seniority_track="management",
            seniority_level="director",
            posted_at=_days_ago(8),
        ),
        case="C09 FictiveCorp Head-Open-Source bare-remote AUD180k",
        filter_pass=True,
        filter_unknown_flags=["remote scope unclear"],
        expected_seniority_track="management",
        expected_seniority_level="director",
        expected_score_min=73.0,
        expected_score_max=80.0,
    ),
    # C10 — NZD salary barely above floor (175k × 0.93 = AUD 162.75k), management/manager,
    #        Remote NZ (non-AU remote → loc=0.75)
    # score ≈ 30×1 + 20×0.5 + 20×0.046 + 15×0.75 + 10×0.5 + 5×0.743 = 60.9
    CorpusEntry(
        listing=_make(
            title="Community Manager",
            company="Automattic",
            location_raw="Remote New Zealand",
            country="NZ",
            salary_min=175_000,
            salary_currency="NZD",
            salary_period="year",
            salary_raw="NZD 175,000 per year",
            seniority_track="management",
            seniority_level="manager",
            posted_at=_days_ago(6),
        ),
        case="C10 Automattic CM NZD-barely-above-floor Remote-NZ",
        filter_pass=True,
        expected_seniority_track="management",
        expected_seniority_level="manager",
        expected_score_min=58.0,
        expected_score_max=64.0,
    ),
]

# ---------------------------------------------------------------------------
# CORPUS — Part B  Fails the hard filter (ten disqualifiers)
# ---------------------------------------------------------------------------

CORPUS_FAIL: list[CorpusEntry] = [
    # C11 — remote but Sydney-based → exclude_locations="Sydney" fires first
    CorpusEntry(
        listing=_make(
            title="Senior Developer Advocate",
            company="Elastic",
            location_raw="Remote — Sydney-based",
            city="Sydney",
            country="AU",
            salary_min=190_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 190,000 per year",
            seniority_track="ic",
            seniority_level="senior",
            posted_at=_days_ago(2),
        ),
        case="C11 Remote-Sydney-based dropped by exclude_locations",
        filter_pass=False,
        filter_drop_reason="location",
        expected_seniority_track="ic",
        expected_seniority_level="senior",
    ),
    # C12 — salary way below floor (AUD 45k), unknown seniority → salary check drops it
    CorpusEntry(
        listing=_make(
            title="Community Coordinator",
            company="AnyOrg",
            location_raw="Remote Australia",
            country="AU",
            salary_min=45_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 45,000 per year",
            posted_at=_days_ago(3),
        ),
        case="C12 AnyOrg low-salary AUD45k dropped by salary filter",
        filter_pass=False,
        filter_drop_reason="salary",
        expected_seniority_track=None,
        expected_seniority_level=None,
    ),
    # C13 — ic/junior < min=mid → dropped by seniority
    CorpusEntry(
        listing=_make(
            title="Junior Developer Advocate",
            company="StartupX",
            location_raw="Remote Australia",
            country="AU",
            salary_min=110_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 110,000 per year",
            seniority_track="ic",
            seniority_level="junior",
            posted_at=_days_ago(4),
        ),
        case="C13 StartupX Junior-DevAdv below seniority min=mid",
        filter_pass=False,
        filter_drop_reason="seniority",
        expected_seniority_track="ic",
        expected_seniority_level="junior",
    ),
    # C14 — ic/intern (title "Intern Community Manager") + employment=internship
    #        Seniority check fires before employment check → drop_reason=seniority
    CorpusEntry(
        listing=_make(
            title="Intern Community Manager",
            company="UnivOrg",
            location_raw="Remote",
            salary_min=40_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 40,000 per year",
            seniority_track="ic",
            seniority_level="intern",
            employment="internship",
            posted_at=_days_ago(5),
        ),
        case="C14 Intern below seniority min (dropped before employment check)",
        filter_pass=False,
        filter_drop_reason="seniority",
        expected_seniority_track="ic",
        expected_seniority_level="intern",
    ),
    # C15 — "PHP" in title → exclude_keywords scope=title → dropped by keyword
    CorpusEntry(
        listing=_make(
            title="PHP Community Developer",
            company="WebShop",
            location_raw="Remote Australia",
            country="AU",
            salary_min=175_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 175,000 per year",
            posted_at=_days_ago(1),
        ),
        case="C15 PHP-in-title dropped by exclude_keywords (title scope)",
        filter_pass=False,
        filter_drop_reason="keyword",
        expected_seniority_track=None,
        expected_seniority_level=None,
    ),
    # C16 — posted 60 days ago → exceeds max_age_days=21 → dropped by age
    CorpusEntry(
        listing=_make(
            title="Developer Advocate Manager",
            company="RemoteCo",
            location_raw="Remote Australia",
            country="AU",
            salary_min=180_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 180,000 per year",
            seniority_track="management",
            seniority_level="manager",
            posted_at=_days_ago(60),
        ),
        case="C16 Stale posting 60 days old dropped by age (max=21)",
        filter_pass=False,
        filter_drop_reason="age",
        expected_seniority_track="management",
        expected_seniority_level="manager",
    ),
    # C17 — management/vp > max=director → dropped by seniority
    CorpusEntry(
        listing=_make(
            title="VP of Developer Experience",
            company="BigCorp",
            location_raw="Remote Australia",
            country="AU",
            salary_min=280_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 280,000 per year",
            seniority_track="management",
            seniority_level="vp",
            posted_at=_days_ago(3),
        ),
        case="C17 VP above max=director dropped by seniority",
        filter_pass=False,
        filter_drop_reason="seniority",
        expected_seniority_track="management",
        expected_seniority_level="vp",
    ),
    # C18 — onsite Sydney CBD: is_remote=False + city=Sydney → exclude_locations fires
    CorpusEntry(
        listing=_make(
            title="Community Manager",
            company="Megacorp",
            location_raw="Sydney CBD, NSW",
            is_remote=False,
            city="Sydney",
            region="NSW",
            country="AU",
            salary_min=170_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 170,000 per year",
            seniority_track="management",
            seniority_level="manager",
            posted_at=_days_ago(2),
        ),
        case="C18 Onsite Sydney dropped by exclude_locations",
        filter_pass=False,
        filter_drop_reason="location",
        expected_seniority_track="management",
        expected_seniority_level="manager",
    ),
    # C19 — "security clearance" in description → exclude_keywords (requirements scope)
    CorpusEntry(
        listing=_make(
            title="Developer Advocate",
            company="GovTech",
            location_raw="Remote Australia",
            country="AU",
            description=_CLEARANCE_DESC,
            salary_min=175_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 175,000 per year",
            posted_at=_days_ago(3),
        ),
        case="C19 security-clearance in description dropped by exclude_keywords",
        filter_pass=False,
        filter_drop_reason="keyword",
        expected_seniority_track=None,
        expected_seniority_level=None,
    ),
    # C20 — contract employment (not excluded), but salary below floor → dropped by salary
    #        Verifies contract is NOT in exclude_employment (only internship is)
    CorpusEntry(
        listing=_make(
            title="Developer Relations Contractor",
            company="ContractorInc",
            location_raw="Remote Australia",
            country="AU",
            salary_min=95_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 95,000 per year",
            employment="contract",
            posted_at=_days_ago(2),
        ),
        case="C20 Contract (not excluded) but salary below floor dropped by salary",
        filter_pass=False,
        filter_drop_reason="salary",
        expected_seniority_track=None,
        expected_seniority_level=None,
    ),
]

# ---------------------------------------------------------------------------
# CORPUS — Part C  Edge cases that pass
# ---------------------------------------------------------------------------

CORPUS_EDGE: list[CorpusEntry] = [
    # C21 — day-rate AUD 1200/day: annualized = 1200 × 260 = 312k > 160k floor
    #        Verifies that period=day is annualized correctly by filter + scorer
    # score ≈ 30×1 + 20×0.5 + 20×1.0 + 15×1 + 10×0.5 + 5×1 = 85.0
    CorpusEntry(
        listing=_make(
            title="Developer Relations Consultant",
            company="ThinkCo",
            location_raw="Remote Australia",
            country="AU",
            salary_min=1_200,
            salary_currency="AUD",
            salary_period="day",
            salary_raw="AUD 1,200 per day",
            posted_at=_days_ago(0),
        ),
        case="C21 Day-rate AUD1200/day annualizes to 312k above floor",
        filter_pass=True,
        expected_seniority_track=None,
        expected_seniority_level=None,
        expected_score_min=82.0,
        expected_score_max=88.0,
    ),
    # C22 — EUR salary with no EUR→AUD FX rate in profile
    #        → keep_unknown_salary=True → passes; comp sub=0.5 (no rate)
    # score ≈ 30×1 + 20×0.5 + 20×0.5 + 15×1 + 10×0.5 + 5×0.91 = 74.5
    CorpusEntry(
        listing=_make(
            title="OSPO Strategist",
            company="EuropeCo",
            location_raw="Remote Australia",
            country="AU",
            salary_min=85_000,
            salary_currency="EUR",
            salary_period="year",
            salary_raw="EUR 85,000 per year",
            posted_at=_days_ago(2),
        ),
        case="C22 EUR salary no FX rate kept by keep_unknown_salary=True",
        filter_pass=True,
        expected_seniority_track=None,
        expected_seniority_level=None,
        expected_score_min=71.0,
        expected_score_max=78.0,
    ),
    # C23 — unparseable salary string "competitive" → Salary(raw="competitive", min=None)
    #        → treated as unknown → keep; comp sub=0.5
    # score ≈ 30×1 + 20×1 + 20×0.5 + 15×1 + 10×0.5 + 5×0.86 = 84.3
    CorpusEntry(
        listing=_make(
            title="Community Lead",
            company="Platform9",
            location_raw="Remote Australia",
            country="AU",
            salary_raw="competitive",
            seniority_track="ic",
            seniority_level="staff",
            posted_at=_days_ago(3),
        ),
        case="C23 Unparseable salary 'competitive' treated as unknown",
        filter_pass=True,
        expected_seniority_track="ic",
        expected_seniority_level="staff",
        expected_score_min=81.0,
        expected_score_max=87.0,
    ),
    # C24 — ic/mid at the minimum allowed seniority boundary
    #        Verifies mid is ≥ min=mid (exactly at boundary → passes)
    # score ≈ 30×1 + 20×0.5 + 20×0.08 + 15×1 + 10×0.5 + 5×0.78 = 65.6
    CorpusEntry(
        listing=_make(
            title="Mid-Level DevRel Engineer",
            company="MidCo",
            location_raw="Remote Australia",
            country="AU",
            salary_min=165_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 165,000 per year",
            seniority_track="ic",
            seniority_level="mid",
            posted_at=_days_ago(5),
        ),
        case="C24 ic/mid at seniority boundary passes",
        filter_pass=True,
        expected_seniority_track="ic",
        expected_seniority_level="mid",
        expected_score_min=62.0,
        expected_score_max=69.0,
    ),
    # C25 — bare "Remote" with no geographic context → is_remote=True, country=None
    #        → remote_scope_unclear flag; still passes (ambiguous remote is always kept)
    # score ≈ 30×1 + 20×1 + 20×0.67 + 15×0.75 + 10×0.5 + 5×0.95 = 84.3
    CorpusEntry(
        listing=_make(
            title="Head of Developer Community",
            company="CloudOrg",
            location_raw="Remote",
            salary_min=200_000,
            salary_currency="AUD",
            salary_period="year",
            salary_raw="AUD 200,000 per year",
            seniority_track="management",
            seniority_level="director",
            posted_at=_days_ago(1),
        ),
        case="C25 Bare-Remote scope unclear but passes filter",
        filter_pass=True,
        filter_unknown_flags=["remote scope unclear"],
        expected_seniority_track="management",
        expected_seniority_level="director",
        expected_score_min=81.0,
        expected_score_max=88.0,
    ),
]

# ---------------------------------------------------------------------------
# Full corpus list
# ---------------------------------------------------------------------------

CORPUS: list[CorpusEntry] = CORPUS_PASS + CORPUS_FAIL + CORPUS_EDGE


# ---------------------------------------------------------------------------
# DEDUPE TEST CASES
# ---------------------------------------------------------------------------


@dataclass
class DedupePair:
    """A pair of listings with the expected dedupe outcome."""

    a: JobListing
    b: JobListing
    should_merge: bool
    case: str


# ---- Merge pairs (should collapse to 1 listing after dedupe.run()) ----


def _dup_srcs(
    url: str = "https://www.adzuna.com.au/jobs/details/dup-1",
) -> tuple[list[Source], list[Source]]:
    """Two different sources pointing at the same URL for URL-based dedup tests."""
    sa = Source(name="adzuna", url=url, source_id="adzuna-dup")
    sb = Source(name="greenhouse", url=url, source_id="greenhouse-dup")
    return [sa], [sb]


MERGE_PAIRS: list[DedupePair] = [
    # MP01 — same cross-post: identical company/title/location, different source boards
    DedupePair(
        a=_make(
            title="Community Director",
            company="GitHub",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src("adzuna", source_id="gh-cd-adzuna")],
        ),
        b=_make(
            title="Community Director",
            company="GitHub",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src("greenhouse", source_id="gh-cd-greenhouse")],
        ),
        should_merge=True,
        case="MP01 Same cross-post different source boards → id merge",
    ),
    # MP02 — "Sr." vs "Senior" title variant → same normalized id
    DedupePair(
        a=_make(
            title="Sr. Developer Advocate",
            company="Elastic",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="elast-sr")],
        ),
        b=_make(
            title="Senior Developer Advocate",
            company="Elastic",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="elast-senior")],
        ),
        should_merge=True,
        case="MP02 Sr. vs Senior title variant → same normalized id → id merge",
    ),
    # MP03 — "Snr." vs "Senior" variant → same normalized id
    DedupePair(
        a=_make(
            title="Snr. Community Manager",
            company="Canonical",
            location_raw="Remote",
            sources=[_src(source_id="can-snr")],
        ),
        b=_make(
            title="Senior Community Manager",
            company="Canonical",
            location_raw="Remote",
            sources=[_src(source_id="can-senior")],
        ),
        should_merge=True,
        case="MP03 Snr. vs Senior variant → same normalized id → id merge",
    ),
    # MP04 — "Jr." vs "Junior" variant → same normalized id
    DedupePair(
        a=_make(
            title="Jr. DevRel Engineer",
            company="GitHub",
            location_raw="Remote",
            sources=[_src(source_id="gh-jr")],
        ),
        b=_make(
            title="Junior DevRel Engineer",
            company="GitHub",
            location_raw="Remote",
            sources=[_src(source_id="gh-junior")],
        ),
        should_merge=True,
        case="MP04 Jr. vs Junior variant → same normalized id → id merge",
    ),
    # MP05 — "Lead" vs equivalent post-normalization ("lead" → "staff" in id key)
    #        "Lead Developer Advocate" and "Staff Developer Advocate" share an id
    DedupePair(
        a=_make(
            title="Lead Developer Advocate",
            company="HashiCorp",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="hashi-lead")],
        ),
        b=_make(
            title="Staff Developer Advocate",
            company="HashiCorp",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="hashi-staff")],
        ),
        should_merge=True,
        case="MP05 Lead→staff normalization: Lead DevAdv vs Staff DevAdv → id merge",
    ),
    # MP06 — trailing parenthesized qualifier stripped: "Lead (APAC)" vs "Lead"
    DedupePair(
        a=_make(
            title="Community Lead (APAC)",
            company="Mozilla",
            location_raw="Remote",
            sources=[_src(source_id="moz-apac")],
        ),
        b=_make(
            title="Community Lead",
            company="Mozilla",
            location_raw="Remote",
            sources=[_src(source_id="moz-plain")],
        ),
        should_merge=True,
        case="MP06 Trailing parenthesized qualifier stripped → id merge",
    ),
    # MP07 — same job re-posted under slightly different title in same cross-post window
    DedupePair(
        a=_make(
            title="OSPO Lead",
            company="Red Hat",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="rh-ospo-a")],
            first_seen_at=_days_ago(3),
        ),
        b=_make(
            title="OSPO Lead",
            company="Red Hat",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="rh-ospo-b")],
            first_seen_at=_days_ago(1),
        ),
        should_merge=True,
        case="MP07 Same identity different first_seen_at → id merge, keep earliest",
    ),
    # MP08 — URL-based merge: different title but same URL
    DedupePair(
        a=_make(
            title="Community Director",
            company="Cloudflare",
            location_raw="Remote",
            sources=[
                _src("adzuna", url="https://cloudflare.com/jobs/1234", source_id="cf-dir-adz")
            ],
        ),
        b=_make(
            title="Director of Community",
            company="Cloudflare",
            location_raw="Remote",
            sources=[_src("lever", url="https://cloudflare.com/jobs/1234", source_id="cf-dir-lev")],
        ),
        should_merge=True,
        case="MP08 URL-based merge: different titles, same URL",
    ),
    # MP09 — URL-based merge: different company spelling, same URL
    DedupePair(
        a=_make(
            title="Staff DevRel Engineer",
            company="GitLab",
            location_raw="Remote",
            sources=[
                _src("adzuna", url="https://jobs.gitlab.com/devrel/99", source_id="gl-devrel-a")
            ],
        ),
        b=_make(
            title="Staff DevRel Engineer",
            company="GitLab Inc.",
            location_raw="Remote",
            sources=[
                _src("lever", url="https://jobs.gitlab.com/devrel/99", source_id="gl-devrel-b")
            ],
        ),
        should_merge=True,
        case="MP09 URL-based merge: different company spelling, same URL",
    ),
    # MP10 — URL-based merge: same title + company, completely different source ids, same URL
    DedupePair(
        a=_make(
            title="Head of Developer Relations",
            company="Automattic",
            location_raw="Remote",
            sources=[
                _src("adzuna", url="https://automattic.com/jobs/hdr-42", source_id="att-hdr-a")
            ],
        ),
        b=_make(
            title="Head of Developer Relations",
            company="Automattic",
            location_raw="Remote",
            sources=[
                _src("greenhouse", url="https://automattic.com/jobs/hdr-42", source_id="att-hdr-b")
            ],
        ),
        should_merge=True,
        case="MP10 Same identity+URL on two boards → id merge confirmed by URL",
    ),
]

# ---- No-merge pairs (must remain as 2 distinct listings) ----

NO_MERGE_PAIRS: list[DedupePair] = [
    # NM01 — different company: "OSPO Lead" at GitHub vs GitLab
    DedupePair(
        a=_make(
            title="OSPO Lead",
            company="GitHub",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="gh-ospo")],
        ),
        b=_make(
            title="OSPO Lead",
            company="GitLab",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="glab-ospo")],
        ),
        should_merge=False,
        case="NM01 Same title, different company → no merge",
    ),
    # NM02 — different title at same company
    DedupePair(
        a=_make(
            title="OSPO Lead",
            company="GitHub",
            location_raw="Remote",
            sources=[_src(source_id="gh-ospo-nm")],
        ),
        b=_make(
            title="Developer Advocate",
            company="GitHub",
            location_raw="Remote",
            sources=[_src(source_id="gh-devadv-nm")],
        ),
        should_merge=False,
        case="NM02 Same company, different title → no merge",
    ),
    # NM03 — same company+title, different cities (both onsite)
    DedupePair(
        a=_make(
            title="Community Manager",
            company="Atlassian",
            location_raw="Sydney NSW",
            is_remote=False,
            city="Sydney",
            region="NSW",
            country="AU",
            sources=[_src(source_id="atl-syd")],
        ),
        b=_make(
            title="Community Manager",
            company="Atlassian",
            location_raw="Melbourne VIC",
            is_remote=False,
            city="Melbourne",
            region="VIC",
            country="AU",
            sources=[_src(source_id="atl-mel")],
        ),
        should_merge=False,
        case="NM03 Same company+title, different cities → no merge",
    ),
    # NM04 — same title, different companies (Elastic vs HashiCorp)
    DedupePair(
        a=_make(
            title="Head of Community",
            company="Elastic",
            location_raw="Remote",
            sources=[_src(source_id="ela-hoc")],
        ),
        b=_make(
            title="Head of Community",
            company="HashiCorp",
            location_raw="Remote",
            sources=[_src(source_id="hashi-hoc")],
        ),
        should_merge=False,
        case="NM04 Same title, different companies → no merge",
    ),
    # NM05 — "Senior Community Manager" vs "Community Manager": not title variants
    #        (title normalization only replaces sr./snr./jr./lead; "Senior" stays)
    DedupePair(
        a=_make(
            title="Senior Community Manager",
            company="Slack",
            location_raw="Remote",
            sources=[_src(source_id="slk-scm")],
        ),
        b=_make(
            title="Community Manager",
            company="Slack",
            location_raw="Remote",
            sources=[_src(source_id="slk-cm")],
        ),
        should_merge=False,
        case="NM05 Senior CM vs CM: Senior not stripped → different ids",
    ),
    # NM06 — same company+title, different countries (Remote AU vs Remote US)
    DedupePair(
        a=_make(
            title="Community Director",
            company="Cloudflare",
            location_raw="Remote Australia",
            country="AU",
            sources=[_src(source_id="cf-dir-au")],
        ),
        b=_make(
            title="Community Director",
            company="Cloudflare",
            location_raw="Remote USA",
            country="US",
            sources=[_src(source_id="cf-dir-us")],
        ),
        should_merge=False,
        case="NM06 Same company+title, different country → different ids",
    ),
    # NM07 — title not a normalization variant (OSPO vs Open Source PO)
    DedupePair(
        a=_make(
            title="OSPO Director",
            company="Google",
            location_raw="Remote",
            sources=[_src(source_id="goog-ospo")],
        ),
        b=_make(
            title="Open Source Program Director",
            company="Google",
            location_raw="Remote",
            sources=[_src(source_id="goog-ospd")],
        ),
        should_merge=False,
        case="NM07 OSPO Director vs Open-Source-Program Director → different ids",
    ),
    # NM08 — "Community Lead" vs "DevRel Lead": same lead→staff normalization on DIFFERENT words
    DedupePair(
        a=_make(
            title="Community Lead",
            company="GitHub",
            location_raw="Remote",
            sources=[_src(source_id="gh-cl")],
        ),
        b=_make(
            title="DevRel Lead",
            company="GitHub",
            location_raw="Remote",
            sources=[_src(source_id="gh-dl")],
        ),
        should_merge=False,
        case="NM08 Community Lead vs DevRel Lead: different words after lead→staff",
    ),
    # NM09 — different URLs, no shared identity → no merge at all
    DedupePair(
        a=_make(
            title="Developer Advocate",
            company="Mozilla",
            location_raw="Remote",
            sources=[_src("adzuna", url="https://mozilla.jobs/da/1", source_id="moz-da-1")],
        ),
        b=_make(
            title="Open Source Evangelist",
            company="Debian",
            location_raw="Remote",
            sources=[_src("lever", url="https://debian.org/jobs/ose/2", source_id="deb-ose-2")],
        ),
        should_merge=False,
        case="NM09 Entirely different company/title/URL → no merge",
    ),
    # NM10 — Remote vs onsite (city=Melbourne): different location part in id key
    DedupePair(
        a=_make(
            title="Community Lead",
            company="Atlassian",
            location_raw="Remote",
            is_remote=True,
            sources=[_src(source_id="atl-remote")],
        ),
        b=_make(
            title="Community Lead",
            company="Atlassian",
            location_raw="Melbourne VIC",
            is_remote=False,
            city="Melbourne",
            region="VIC",
            country="AU",
            sources=[_src(source_id="atl-mel2")],
        ),
        should_merge=False,
        case="NM10 Remote vs onsite Melbourne: different location key → no merge",
    ),
]
