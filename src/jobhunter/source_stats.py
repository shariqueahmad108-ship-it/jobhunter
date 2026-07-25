# SPDX-License-Identifier: Apache-2.0
"""Source contribution stats persistence and display.

Persists per-source counters to state/<profile>/source_stats.json (append-only).
Provides the `jobhunter sources` command data and digest header lines.

See: specs/05-operator-tooling.md §5.1
     specs/03-data-model.md §Source stats
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jobhunter.model import SourceStat

_SCHEMA_VERSION = 1


@dataclass
class StatsRun:
    """One run's worth of per-source counters."""

    run_at: str
    sources: list[SourceStat] = field(default_factory=list)


@dataclass
class StatsHistory:
    """Append-only history of per-source stats across runs."""

    schema_version: int = _SCHEMA_VERSION
    runs: list[StatsRun] = field(default_factory=list)


def load_stats(path: str | Path) -> StatsHistory:
    """Load source stats history, returning empty StatsHistory if absent.

    Raises ValueError on schema_version mismatch.
    """
    path = Path(path)
    if not path.exists():
        return StatsHistory()

    with open(path) as f:
        raw = json.load(f)

    version = raw.get("schema_version", _SCHEMA_VERSION)
    if version != _SCHEMA_VERSION:
        raise ValueError(
            f"Stats file {path}: schema_version {version} not supported "
            f"(expected {_SCHEMA_VERSION}). Delete to reset."
        )

    runs: list[StatsRun] = []
    for run_data in raw.get("runs", []):
        sources: list[SourceStat] = []
        for s in run_data.get("sources", []):
            sources.append(
                SourceStat(
                    name=s["name"],
                    fetched=s.get("fetched", 0),
                    contributed=s.get("contributed", 0),
                    sole_source=s.get("sole_source", 0),
                    passed_filter=s.get("passed_filter", 0),
                    shown=s.get("shown", 0),
                    dismissed=s.get("dismissed", 0),
                    requests=s.get("requests", 0),
                    failed=s.get("failed", False),
                    error=s.get("error"),
                )
            )
        runs.append(StatsRun(run_at=run_data["run_at"], sources=sources))

    return StatsHistory(schema_version=_SCHEMA_VERSION, runs=runs)


def save_stats(history: StatsHistory, path: str | Path) -> None:
    """Persist stats history to JSON, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    runs_data = []
    for run in history.runs:
        sources_data = [
            {
                "name": s.name,
                "fetched": s.fetched,
                "contributed": s.contributed,
                "sole_source": s.sole_source,
                "passed_filter": s.passed_filter,
                "shown": s.shown,
                "dismissed": s.dismissed,
                "requests": s.requests,
                "failed": s.failed,
                "error": s.error,
            }
            for s in run.sources
        ]
        runs_data.append({"run_at": run.run_at, "sources": sources_data})

    data: dict = {
        "schema_version": history.schema_version,
        "runs": runs_data,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def append_run(history: StatsHistory, run: StatsRun) -> StatsHistory:
    """Return a new StatsHistory with run appended (history is append-only)."""
    return StatsHistory(
        schema_version=history.schema_version,
        runs=history.runs + [run],
    )


def source_header_lines(source_stats: list[SourceStat]) -> list[str]:
    """Format per-source summary lines for the digest header.

    Example: ``adzuna: 312 fetched → 41 passed → 12 shown (4 sole)``
    """
    lines: list[str] = []
    for s in source_stats:
        if s.failed:
            lines.append(f"  {s.name}: FAILED ({s.error or 'unknown error'})")
        else:
            sole_note = f" ({s.sole_source} sole)" if s.sole_source else ""
            lines.append(
                f"  {s.name}: {s.fetched} fetched → {s.passed_filter} passed"
                f" → {s.shown} shown{sole_note}"
            )
    return lines


def format_table(
    history: StatsHistory,
    last_n: Optional[int] = None,
    active_sources: Optional[set[str]] = None,
) -> str:
    """Format a text table of per-source stats, aggregated across runs.

    Args:
        last_n:         Show only the most recent N runs (None = all).
        active_sources: Source names currently in the profile; absent ones
                        are shown as inactive.
    """
    runs = history.runs
    if last_n is not None:
        runs = runs[-last_n:]

    if not runs:
        return "No source stats recorded yet."

    # Aggregate totals across selected runs
    totals: dict[str, SourceStat] = {}
    for run in runs:
        for s in run.sources:
            if s.name not in totals:
                totals[s.name] = SourceStat(name=s.name)
            t = totals[s.name]
            t.fetched += s.fetched
            t.contributed += s.contributed
            t.sole_source += s.sole_source
            t.passed_filter += s.passed_filter
            t.shown += s.shown
            t.dismissed += s.dismissed
            t.requests += s.requests
            if s.failed:
                t.failed = True
                if s.error:
                    t.error = s.error

    col = "{:<22} {:>8} {:>8} {:>6} {:>8} {:>6} {:>10} {:>9}  {}"
    header = col.format(
        "Source", "Fetched", "Contrib", "Sole", "Passed", "Shown", "Dismissed", "Requests", "Status"
    )
    separator = "-" * len(header)
    rows = [header, separator]

    for name, t in sorted(totals.items()):
        inactive = active_sources is not None and name not in active_sources
        if inactive:
            status = "inactive"
        elif t.failed:
            status = "FAILED"
        else:
            status = "ok"
        rows.append(
            col.format(
                name,
                t.fetched,
                t.contributed,
                t.sole_source,
                t.passed_filter,
                t.shown,
                t.dismissed,
                t.requests,
                status,
            )
        )

    n = len(runs)
    label = "last 1 run" if n == 1 else f"last {n} runs"
    rows.append(f"\n({label})")
    return "\n".join(rows)


def format_json(
    history: StatsHistory,
    last_n: Optional[int] = None,
) -> str:
    """Serialize stats history (or last N runs) as pretty-printed JSON."""
    runs = history.runs
    if last_n is not None:
        runs = runs[-last_n:]

    data = {
        "schema_version": history.schema_version,
        "runs": [
            {
                "run_at": run.run_at,
                "sources": [
                    {
                        "name": s.name,
                        "fetched": s.fetched,
                        "contributed": s.contributed,
                        "sole_source": s.sole_source,
                        "passed_filter": s.passed_filter,
                        "shown": s.shown,
                        "dismissed": s.dismissed,
                        "requests": s.requests,
                        "failed": s.failed,
                        "error": s.error,
                    }
                    for s in run.sources
                ],
            }
            for run in runs
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)
