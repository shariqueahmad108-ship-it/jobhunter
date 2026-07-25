# SPDX-License-Identifier: Apache-2.0
"""Run snapshot: persist and load pre-filter listing sets for offline replay.

Written by the `run` command when output.keep_raw is true (default). Read by
`jobhunter replay` to re-run Stages 4-7 without any network access.

See: specs/03-data-model.md §Run snapshot
     specs/05-operator-tooling.md §5.2
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from jobhunter.model import JobListing, Location, Salary, Seniority, Source

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def listing_to_dict(listing: JobListing) -> dict:
    return {
        "id": listing.id,
        "content_hash": listing.content_hash,
        "title": listing.title,
        "company": listing.company,
        "location": {
            "raw": listing.location.raw,
            "city": listing.location.city,
            "region": listing.location.region,
            "country": listing.location.country,
            "is_remote": listing.location.is_remote,
        },
        "description": listing.description,
        "sources": [
            {"name": s.name, "url": s.url, "source_id": s.source_id} for s in listing.sources
        ],
        "first_seen_at": listing.first_seen_at,
        "salary": {
            "min": listing.salary.min,
            "max": listing.salary.max,
            "currency": listing.salary.currency,
            "period": listing.salary.period,
            "raw": listing.salary.raw,
        }
        if listing.salary
        else None,
        "seniority": {
            "track": listing.seniority.track,
            "level": listing.seniority.level,
        }
        if listing.seniority
        else None,
        "employment": listing.employment,
        "posted_at": listing.posted_at,
    }


def listing_from_dict(d: dict) -> JobListing:
    loc = d["location"]
    sal = d.get("salary")
    sen = d.get("seniority")
    return JobListing(
        id=d["id"],
        content_hash=d["content_hash"],
        title=d["title"],
        company=d["company"],
        location=Location(
            raw=loc["raw"],
            city=loc.get("city"),
            region=loc.get("region"),
            country=loc.get("country"),
            is_remote=loc.get("is_remote", False),
        ),
        description=d["description"],
        sources=[
            Source(name=s["name"], url=s["url"], source_id=s["source_id"])
            for s in d.get("sources", [])
        ],
        first_seen_at=d["first_seen_at"],
        salary=Salary(
            min=sal.get("min"),
            max=sal.get("max"),
            currency=sal.get("currency"),
            period=sal.get("period"),
            raw=sal.get("raw"),
        )
        if sal
        else None,
        seniority=Seniority(
            track=sen.get("track"),
            level=sen.get("level"),
        )
        if sen
        else None,
        employment=d.get("employment"),
        posted_at=d.get("posted_at"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_snapshot(
    path: Path,
    run_at: str,
    profile_snapshot: dict,
    listings: list[JobListing],
) -> None:
    """Serialize pre-filter listings and profile to a JSON snapshot file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": SCHEMA_VERSION,
        "run_at": run_at,
        "profile_snapshot": profile_snapshot,
        "listings": [listing_to_dict(li) for li in listings],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_snapshot(path: Path) -> dict:
    """Load a snapshot file; raises ValueError on missing file or bad schema_version.

    Returns a dict with:
      schema_version, run_at, profile_snapshot, listings (list[JobListing]).

    A missing file is a loud error naming output.keep_raw so the user knows
    what setting to enable — never silently falls back to a live fetch.
    """
    path = Path(path)
    if not path.exists():
        raise ValueError(
            f"Snapshot file not found: {path}\n"
            "Set output.keep_raw: true in your profile (this is the default) "
            "and re-run to create one."
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    version = raw.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"Snapshot {path}: schema_version {version} not supported (expected {SCHEMA_VERSION})."
        )

    listings = [listing_from_dict(d) for d in raw.get("listings", [])]
    return {
        "schema_version": version,
        "run_at": raw["run_at"],
        "profile_snapshot": raw.get("profile_snapshot", {}),
        "listings": listings,
    }


def apply_set_overrides(profile: dict, overrides: list[str]) -> dict:
    """Apply dot-path key=value overrides to a profile dict (deep copy).

    Example: "output.display_threshold=65" sets profile["output"]["display_threshold"] = 65.
    Values are auto-parsed to int, float, bool, or left as strings.
    """
    result = copy.deepcopy(profile)
    for override in overrides:
        key, _, value_str = override.partition("=")
        parts = key.strip().split(".")
        d = result
        for part in parts[:-1]:
            if part not in d or not isinstance(d[part], dict):
                d[part] = {}
            d = d[part]
        d[parts[-1]] = _parse_scalar(value_str)
    return result


def _parse_scalar(s: str):
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s
