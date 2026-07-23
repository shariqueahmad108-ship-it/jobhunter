# SPDX-License-Identifier: Apache-2.0
"""Canonical data model types.

See: specs/03-data-model.md
"""

from dataclasses import dataclass, field
from typing import Optional


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
