# SPDX-License-Identifier: Apache-2.0
"""ATS board detection for the `jobhunter probe` command.

Single-target mode probes a careers page URL or bare slug:
  probe_single(target, ats_hint) -> list[ProbeResult]

Check mode probes every entry in the profile's ats_watchlist:
  probe_check(watchlist) -> list[BoardStatus]

A board is "confirmed" only when the API returns valid JSON with at least one
job posting.  404 / HTML / zero jobs all map to "not confirmed" — the probe
never prints a candidate line for an unverified board.

See: specs/05-operator-tooling.md §5.3
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import NamedTuple

import httpx

_USER_AGENT = "jobhunter/1.0 (personal job-search pipeline)"
_TIMEOUT = 20.0
_PROBE_DELAY = 0.5  # seconds between sequential probe requests

SUPPORTED_ATS = ("greenhouse", "lever", "ashby", "workday")

# ---------------------------------------------------------------------------
# URL slug extraction
# ---------------------------------------------------------------------------

_URL_PATTERNS: list[tuple[str, str]] = [
    ("greenhouse", r"boards\.greenhouse\.io/([^/?#]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([^/?#]+)"),
    ("lever", r"jobs\.lever\.co/([^/?#]+)"),
    ("lever", r"api\.lever\.co/v0/postings/([^/?#]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([^/?#]+)"),
    ("ashby", r"([^./]+)\.ashbyhq\.com"),
    ("workday", r"([^./]+)\.wd\d+\.myworkdayjobs\.com"),
]


def extract_slug_from_url(url: str) -> tuple[str | None, str | None]:
    """Return (ats_type, slug) from a known ATS board URL, or (None, None)."""
    for ats_type, pattern in _URL_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return ats_type, m.group(1)
    return None, None


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class ProbeResult(NamedTuple):
    ats: str
    slug: str
    job_count: int
    oldest_date: str | None
    newest_date: str | None


class BoardStatus(NamedTuple):
    entry: dict
    result: ProbeResult | None
    error: str | None


# ---------------------------------------------------------------------------
# Per-ATS probe helpers
# ---------------------------------------------------------------------------


def _probe_greenhouse(slug: str) -> ProbeResult | None:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.get(url, params={"content": "false"})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            if "html" in resp.headers.get("content-type", "").lower():
                return None
            data = resp.json()
    except Exception:
        return None

    jobs = data.get("jobs") or []
    if not jobs:
        return None

    dates = sorted(j["updated_at"][:10] for j in jobs if j.get("updated_at"))
    return ProbeResult(
        ats="greenhouse",
        slug=slug,
        job_count=len(jobs),
        oldest_date=dates[0] if dates else None,
        newest_date=dates[-1] if dates else None,
    )


def _probe_lever(slug: str) -> ProbeResult | None:
    url = f"https://api.lever.co/v0/postings/{slug}"
    try:
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.get(url, params={"mode": "json"})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            if "html" in resp.headers.get("content-type", "").lower():
                return None
            data = resp.json()
    except Exception:
        return None

    jobs = data if isinstance(data, list) else []
    if not jobs:
        return None

    ms_list = sorted(
        j["createdAt"] for j in jobs if isinstance(j.get("createdAt"), (int, float))
    )
    oldest = (
        datetime.fromtimestamp(ms_list[0] / 1000, tz=timezone.utc).date().isoformat()
        if ms_list
        else None
    )
    newest = (
        datetime.fromtimestamp(ms_list[-1] / 1000, tz=timezone.utc).date().isoformat()
        if ms_list
        else None
    )
    return ProbeResult(
        ats="lever",
        slug=slug,
        job_count=len(jobs),
        oldest_date=oldest,
        newest_date=newest,
    )


def _probe_ashby(slug: str) -> ProbeResult | None:
    url = "https://jobs.ashbyhq.com/api/non-authed/job-board/jobs"
    try:
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.post(url, json={"organizationHostedJobsPageName": slug})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            if "html" in resp.headers.get("content-type", "").lower():
                return None
            data = resp.json()
    except Exception:
        return None

    jobs = data.get("jobPostings") or []
    if not jobs:
        return None

    dates = sorted(j["publishedDate"][:10] for j in jobs if j.get("publishedDate"))
    return ProbeResult(
        ats="ashby",
        slug=slug,
        job_count=len(jobs),
        oldest_date=dates[0] if dates else None,
        newest_date=dates[-1] if dates else None,
    )


def _probe_workday(
    slug: str,
    workday_path: str | None = None,
    workday_instance: int = 5,
) -> ProbeResult | None:
    path = (workday_path or f"{slug}/{slug}").strip("/")
    url = f"https://{slug}.wd{workday_instance}.myworkdayjobs.com/wday/cxs/{path}/jobs"
    try:
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as client:
            resp = client.post(
                url,
                json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            if "html" in resp.headers.get("content-type", "").lower():
                return None
            data = resp.json()
    except Exception:
        return None

    total = int(data.get("total", 0))
    if total == 0:
        return None

    return ProbeResult(
        ats="workday",
        slug=slug,
        job_count=total,
        oldest_date=None,
        newest_date=None,
    )


_PROBERS = {
    "greenhouse": _probe_greenhouse,
    "lever": _probe_lever,
    "ashby": _probe_ashby,
    "workday": _probe_workday,
}


# ---------------------------------------------------------------------------
# Single-target probe
# ---------------------------------------------------------------------------


def probe_single(target: str, ats_hint: str | None = None) -> list[ProbeResult]:
    """Probe a careers page URL or bare slug, returning only confirmed hits.

    When target is a URL matching a known ATS pattern, the ATS and slug are
    extracted from it and only that board is probed.  When target is a bare
    slug (and --ats is not given), Greenhouse, Lever, and Ashby are tried in
    sequence (Workday requires additional path info that a bare slug does not
    supply).
    """
    extracted_ats, extracted_slug = extract_slug_from_url(target)

    if extracted_ats and extracted_slug:
        if extracted_ats == "workday":
            result = _probe_workday(extracted_slug)
        else:
            result = _PROBERS[extracted_ats](extracted_slug)
        return [result] if result else []

    # Bare slug path
    slug = target.strip("/")
    if ats_hint:
        ats_types: list[str] = [ats_hint]
    else:
        ats_types = ["greenhouse", "lever", "ashby"]

    results: list[ProbeResult] = []
    for i, ats_type in enumerate(ats_types):
        if i > 0:
            time.sleep(_PROBE_DELAY)
        if ats_type == "workday":
            result = _probe_workday(slug)
        else:
            result = _PROBERS[ats_type](slug)
        if result is not None:
            results.append(result)
    return results


# ---------------------------------------------------------------------------
# Watchlist check mode
# ---------------------------------------------------------------------------


def probe_check(watchlist: list[dict]) -> list[BoardStatus]:
    """Probe every entry in the watchlist, returning one BoardStatus each.

    Delays _PROBE_DELAY seconds between requests.  Never raises — errors are
    captured in BoardStatus.error.
    """
    statuses: list[BoardStatus] = []
    for i, entry in enumerate(watchlist):
        if i > 0:
            time.sleep(_PROBE_DELAY)
        ats_type = (entry.get("ats") or "").lower()
        slug = entry.get("slug") or ""
        if ats_type not in _PROBERS or not slug:
            statuses.append(
                BoardStatus(entry, None, f"Unsupported ATS or missing slug: {entry!r}")
            )
            continue
        try:
            if ats_type == "workday":
                result = _probe_workday(
                    slug,
                    workday_path=entry.get("workday_path"),
                    workday_instance=int(entry.get("workday_instance") or 5),
                )
            else:
                result = _PROBERS[ats_type](slug)
            statuses.append(BoardStatus(entry, result, None))
        except Exception as exc:
            statuses.append(BoardStatus(entry, None, str(exc)))
    return statuses


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def format_probe_result(result: ProbeResult, name: str | None = None) -> str:
    """Return the human-readable hit block with a paste-ready profile YAML line.

    Example output::

        greenhouse / mozilla — 56 jobs (oldest 2026-04-02, newest 2026-07-24)
          - { ats: "greenhouse", slug: "mozilla", name: "Mozilla" }
    """
    display_name = name or _title_from_slug(result.slug)
    date_info = ""
    if result.oldest_date and result.newest_date:
        if result.oldest_date != result.newest_date:
            date_info = f" (oldest {result.oldest_date}, newest {result.newest_date})"
        else:
            date_info = f" (newest {result.newest_date})"
    elif result.newest_date:
        date_info = f" (newest {result.newest_date})"
    elif result.oldest_date:
        date_info = f" (oldest {result.oldest_date})"

    header = f"{result.ats} / {result.slug} — {result.job_count} jobs{date_info}"
    yaml_line = (
        f'  - {{ ats: "{result.ats}", slug: "{result.slug}", name: "{display_name}" }}'
    )
    return f"{header}\n{yaml_line}"
