# SPDX-License-Identifier: Apache-2.0
"""Configuration and source health checks for `jobhunter doctor`.

The pipeline is deliberately quiet: a source that 404s, is rate-limited, has no
credential, or simply matched nothing all produce the same thing — a digest
without those listings. `doctor` is the command that tells them apart, and the
one a scheduled canary runs to catch board rot before a digest silently thins.

Every check returns a Check rather than printing, so the same results render as
a table for a human or as JSON for a workflow.

See: specs/05-operator-tooling.md §5.4
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from jobhunter.ingest import SourceAdapter

# Status vocabulary, ordered by severity. `warn` never fails the command; a
# `fail` does, because doctor exists to answer "is anything broken".
OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

_STALE_FX_DAYS = 90
_PROBE_SAMPLE = 5


@dataclass
class Check:
    """One health check outcome."""

    kind: str  # profile | fx | credential | source | board | state
    target: str  # what was checked: a path, a source name, a board slug
    status: str  # OK | WARN | FAIL | SKIP
    detail: str = ""

    @property
    def failed(self) -> bool:
        return self.status == FAIL


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_profile(path: Path, loader: Callable[[Path], dict]) -> tuple[Check, Optional[dict]]:
    """Validate the profile. Every later check needs it, so this gates the rest."""
    try:
        profile = loader(path)
    except Exception as e:
        return Check("profile", str(path), FAIL, str(e)), None
    return Check("profile", str(path), OK, "valid"), profile


def check_fx_rates(path: str, age_days: Optional[int]) -> Check:
    """Stale rates don't error — they quietly compare salaries at the wrong rate."""
    if age_days is None:
        return Check(
            "fx",
            path,
            WARN,
            "absent — profile fx_rates only; a salary in an unlisted currency counts as unknown",
        )
    if age_days >= _STALE_FX_DAYS:
        return Check("fx", path, WARN, f"{age_days} days old — refresh exchange rates")
    return Check("fx", path, OK, f"{age_days} days old")


# Sources that need a credential, and the env vars that supply it.
_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "adzuna": ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"),
    "jooble": ("JOOBLE_API_KEY",),
}

# Sources `_build_adapters` constructs unless the profile disables them. Keep in
# step with cli._build_adapters, which is the behaviour this mirrors.
_DEFAULT_ON = frozenset({"adzuna"})


def check_credentials(profile: dict, env: Optional[dict] = None) -> list[Check]:
    """A source EXPLICITLY enabled without its credential is broken. Absent is not.

    The distinction matters more than it looks. Adzuna is active-unless-disabled
    in `_build_adapters`, and the shipped example profile leaves it commented
    out — so treating "absent" as "enabled" made `doctor` fail for a source the
    user never turned on, and would have made the weekly canary file an issue
    every run forever.

    - explicitly `enabled: true`, credential missing  -> FAIL (you asked for a
      source that cannot possibly fetch)
    - absent from `sources:`, credential missing      -> WARN (probably not
      wanted; say so once, don't fail the command)
    - explicitly `enabled: false`                     -> SKIP
    """
    environ = env if env is not None else dict(os.environ)
    sources = profile.get("sources") or {}
    checks: list[Check] = []

    for name, variables in _CREDENTIALS.items():
        cfg = sources.get(name)
        # None = the profile says nothing about this source. Narrowing has to
        # happen inside the isinstance check: mypy does not carry it through a
        # boolean stored in a variable.
        explicit: Optional[bool] = None
        if isinstance(cfg, dict) and "enabled" in cfg:
            explicit = bool(cfg["enabled"])

        if explicit is False or (explicit is None and name not in _DEFAULT_ON):
            checks.append(Check("credential", name, SKIP, "not enabled"))
            continue

        missing = [v for v in variables if not environ.get(v)]
        if not missing:
            checks.append(Check("credential", name, OK, "credential present"))
        elif explicit:
            checks.append(
                Check("credential", name, FAIL, f"enabled but {', '.join(missing)} not set")
            )
        else:
            checks.append(
                Check(
                    "credential",
                    name,
                    WARN,
                    f"active by default but {', '.join(missing)} not set — "
                    f"set sources.{name}.enabled: false to silence",
                )
            )
    return checks


def check_sources(
    adapters: list[SourceAdapter],
    profile: dict,
    max_results: int = _PROBE_SAMPLE,
) -> list[Check]:
    """One real query per adapter — the only way to tell 'dead' from 'nothing matched'.

    A source that answers with zero listings is a WARN, not a FAIL: an empty
    result is legitimate (a narrow keyword, a quiet board) while an exception is
    not.
    """
    keywords = (profile.get("queries") or {}).get("keywords") or [""]
    locations = (profile.get("queries") or {}).get("locations") or [""]
    keyword, location = keywords[0], locations[0]

    checks: list[Check] = []
    for adapter in adapters:
        try:
            results = adapter.search(keyword, location, max_results)
        except Exception as e:
            checks.append(Check("source", adapter.name, FAIL, f"{type(e).__name__}: {e}"))
            continue
        count = len(results)
        if count:
            checks.append(Check("source", adapter.name, OK, f"{count} listings for {keyword!r}"))
        else:
            checks.append(
                Check("source", adapter.name, WARN, f"reachable but 0 listings for {keyword!r}")
            )
    return checks


def check_boards(watchlist: list[dict], prober: Callable[[list[dict]], list]) -> list[Check]:
    """Probe every ATS watchlist entry. A dead slug is the failure this catches."""
    if not watchlist:
        return [Check("board", "ats_watchlist", SKIP, "no entries in profile")]

    checks: list[Check] = []
    for status in prober(watchlist):
        entry = status.entry
        label = f"{entry.get('ats', '?')}/{entry.get('slug', '?')}"
        name = entry.get("name") or ""
        if status.result:
            suffix = f" ({name})" if name else ""
            checks.append(
                Check("board", label, OK, f"{status.result.job_count} jobs{suffix}")
            )
        else:
            reason = status.error or "not confirmed"
            checks.append(Check("board", label, FAIL, f"dead — {reason}"))
    return checks


def check_state(path: Path, loader: Callable[[Path], object]) -> Check:
    """An unreadable state file blocks every run; better to hear it here."""
    if not Path(path).exists():
        return Check("state", str(path), OK, "absent — a first run will create it")
    try:
        loader(Path(path))
    except Exception as e:
        return Check("state", str(path), FAIL, str(e))
    return Check("state", str(path), OK, "loads")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_SYMBOL = {OK: "ok", WARN: "warn", FAIL: "FAIL", SKIP: "skip"}


def format_table(checks: list[Check]) -> str:
    """Render checks as an aligned table, worst news easiest to spot."""
    if not checks:
        return "No checks ran."
    kind_w = max(len(c.kind) for c in checks)
    target_w = min(max(len(c.target) for c in checks), 40)
    rows = [
        f"{c.kind:<{kind_w}}  {c.target[:target_w]:<{target_w}}  "
        f"{_SYMBOL.get(c.status, c.status):<4}  {c.detail}".rstrip()
        for c in checks
    ]
    failures = [c for c in checks if c.failed]
    warnings = [c for c in checks if c.status == WARN]
    rows.append("")
    if failures:
        rows.append(f"{len(failures)} failing, {len(warnings)} warning — see FAIL rows above.")
    elif warnings:
        rows.append(f"All checks passed, {len(warnings)} warning.")
    else:
        rows.append("All checks passed.")
    return "\n".join(rows)


def format_json(checks: list[Check]) -> str:
    """Machine-readable form, for the scheduled canary to post into an issue."""
    import json

    return json.dumps(
        {
            "ok": not any(c.failed for c in checks),
            "checks": [
                {"kind": c.kind, "target": c.target, "status": c.status, "detail": c.detail}
                for c in checks
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


def exit_code(checks: list[Check]) -> int:
    """1 when anything FAILed, so a scheduler can act on it. Warnings are not failures."""
    return 1 if any(c.failed for c in checks) else 0
