# SPDX-License-Identifier: Apache-2.0
"""Command-handler tests for the CLI.

tests/test_cli.py covers the pure helpers (`_profile_slug`, `_build_adapters`,
parser wiring). This module drives each `_cmd_*` handler end to end through
``build_parser()`` and asserts on exit code, files written and stdout/stderr —
the surface a user actually touches.

The pipeline itself is stubbed: these tests are about the CLI's own logic
(path derivation, output formats, state persistence, error handling), and
Stages 1-7 have their own suites. `replay` and `probe` are the exceptions —
replay runs Stages 4-7 for real off a snapshot (that is the point of it), and
probe's network calls are stubbed at the seam.

See: specs/02-functional-spec.md §Stage 7
     specs/05-operator-tooling.md §5.1-5.3
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from jobhunter import cli
from jobhunter.model import (
    JobListing,
    Location,
    RunReport,
    Salary,
    ScoredResult,
    Seniority,
    Source,
    SourceStat,
)
from jobhunter.probe import BoardStatus, ProbeResult

_EXAMPLE_PROFILE = Path(__file__).resolve().parents[1] / "specs" / "profile.example.yaml"
_TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_dict(**output_overrides) -> dict:
    """The shipped example profile, with every source switched off.

    Sources off keeps these tests independent of the ambient environment: no
    adapter is constructed, so a developer with ADZUNA_APP_ID exported gets the
    same result as CI.
    """
    data = yaml.safe_load(_EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    data["sources"] = {"adzuna": {"enabled": False}}
    if output_overrides:
        data.setdefault("output", {}).update(output_overrides)
    return data


def _write_profile(path: Path, **output_overrides) -> Path:
    path.write_text(yaml.safe_dump(_profile_dict(**output_overrides)), encoding="utf-8")
    return path


def _listing(
    *,
    lid: str = "a" * 64,
    title: str = "Senior Software Engineer",
    company: str = "Acme",
    remote: bool = True,
    salary: bool = True,
    url: str = "https://example.com/job/1",
) -> JobListing:
    return JobListing(
        id=lid,
        content_hash="h" + lid[1:],
        title=title,
        company=company,
        location=Location(raw="Remote", is_remote=remote, country="AU"),
        description="Work on distributed systems with TypeScript and AWS.",
        sources=[Source(name="stub", url=url, source_id=lid[:6])],
        first_seen_at=_TODAY,
        salary=Salary(min=200000.0, max=220000.0, currency="AUD", period="year")
        if salary
        else None,
        seniority=Seniority(track="ic", level="senior"),
        employment="full_time",
        posted_at=_TODAY,
    )


def _result(rank: int = 1, score: float = 88.0, **listing_kwargs) -> ScoredResult:
    return ScoredResult(
        listing=_listing(**listing_kwargs),
        score=score,
        components=[],
        summary_reason="exact match: senior (ic)",
        rank=rank,
        unknown_flags=[],
    )


def _report(**kwargs) -> RunReport:
    defaults = dict(
        run_at=_TODAY,
        sources_used=["stub"],
        requests_made=3,
        ingested_count=5,
        after_dedupe=4,
        source_stats=[SourceStat(name="stub", fetched=5, passed_filter=3, requests=3)],
    )
    defaults.update(kwargs)
    return RunReport(**defaults)


def _stub_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    results: list[ScoredResult],
    report: RunReport | None = None,
) -> None:
    """Replace both pipeline entry points with canned output (no network, no stages)."""
    report_holder = {"report": None}

    def fake_with_snapshot(profile, adapters, dismissed_ids=None, today=None):
        report_holder["report"] = report if report is not None else _report()
        return list(results), report_holder["report"], [r.listing for r in results]

    def fake_run(profile, adapters, dismissed_ids=None, today=None):
        report_holder["report"] = report if report is not None else _report()
        return list(results), report_holder["report"]

    monkeypatch.setattr(cli, "pipeline_run_with_snapshot", fake_with_snapshot)
    monkeypatch.setattr(cli, "pipeline_run", fake_run)


def _run_cli(argv: list[str]) -> int:
    """Parse argv and dispatch, exactly as main() does minus the sys.exit."""
    args = cli.build_parser().parse_args(argv)
    return args.func(args)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A clean cwd: no fx_rates.yaml, no state, no digests."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# _active_source_names / _stats_path
# ---------------------------------------------------------------------------


def test_active_sources_defaults_to_adzuna():
    """Adzuna has no explicit enable flag: absent config means active."""
    assert cli._active_source_names({}) == {"adzuna"}


def test_active_sources_reads_every_source_block():
    profile = {
        "sources": {
            "adzuna": {"enabled": True},
            "ats_watchlist": [{"ats": "greenhouse", "slug": "mozilla"}],
            "feeds": [{"name": "wwr", "url": "https://example.com/f.rss"}],
            "remotive": {"enabled": True},
            "remoteok": {"enabled": True},
            "jooble": {"enabled": True},
        }
    }
    assert cli._active_source_names(profile) == {
        "adzuna",
        "ats",
        "rss",
        "remotive",
        "remoteok",
        "jooble",
    }


def test_active_sources_honours_legacy_queries_watchlist():
    profile = {
        "sources": {"adzuna": {"enabled": False}},
        "queries": {"ats_watchlist": [{"ats": "lever", "slug": "acme"}]},
    }
    assert cli._active_source_names(profile) == {"ats"}


def test_active_sources_disabled_blocks_are_omitted():
    profile = {
        "sources": {
            "adzuna": {"enabled": False},
            "remotive": {"enabled": False},
            "remoteok": {"enabled": False},
            "jooble": {"enabled": False},
            "feeds": [],
            "ats_watchlist": [],
        }
    }
    assert cli._active_source_names(profile) == set()


def test_stats_path_default_and_slugged():
    state = Path("state/state.yaml")
    assert cli._stats_path(state, "") == Path("state/source_stats.json")
    assert cli._stats_path(state, "profile-ospo") == Path("state/source_stats-profile-ospo.json")


# ---------------------------------------------------------------------------
# run — happy path and outputs
# ---------------------------------------------------------------------------


def test_run_writes_digest_data_snapshot_state_and_stats(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml", format="markdown", data_format="json")
    _stub_pipeline(monkeypatch, [_result()])

    code = _run_cli(["run", "--profile", "profile.yaml", "--state", "state/state.yaml"])

    assert code == 0
    digests = workdir / "digests"
    assert (digests / f"{_TODAY}.md").exists()
    assert (digests / f"{_TODAY}.json").exists()
    assert (digests / f"{_TODAY}.raw.json").exists()  # keep_raw default true
    assert (workdir / "state" / "state.yaml").exists()
    assert (workdir / "state" / "source_stats.json").exists()
    out = capsys.readouterr()
    assert "Senior Software Engineer" in out.out  # digest goes to stdout
    assert "Snapshot:" in out.err


def test_run_state_records_only_the_rendered_slice(workdir, monkeypatch):
    """Overflow beyond max_shown must stay unseen so it resurfaces next run."""
    _write_profile(workdir / "profile.yaml", max_shown=1)
    results = [
        _result(rank=1, lid="a" * 64),
        _result(rank=2, lid="b" * 64),
        _result(rank=3, lid="c" * 64),
    ]
    _stub_pipeline(monkeypatch, results)

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0

    state = yaml.safe_load((workdir / "state" / "state.yaml").read_text())
    assert [e["id"] for e in state["seen"]] == ["a" * 64]


def test_run_html_format_writes_html_and_no_markdown(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml", format="html")
    _stub_pipeline(monkeypatch, [_result()])

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0

    digests = workdir / "digests"
    assert (digests / f"{_TODAY}.html").exists()
    assert not (digests / f"{_TODAY}.md").exists()
    assert "HTML digest:" in capsys.readouterr().err


def test_run_both_data_formats_write_json_and_csv(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml", data_format="both")
    _stub_pipeline(monkeypatch, [_result()])

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0

    digests = workdir / "digests"
    assert (digests / f"{_TODAY}.json").exists()
    assert (digests / f"{_TODAY}.csv").exists()
    assert "CSV data file:" in capsys.readouterr().err


def test_run_keep_raw_false_writes_no_snapshot(workdir, monkeypatch):
    _write_profile(workdir / "profile.yaml", keep_raw=False)
    _stub_pipeline(monkeypatch, [_result()])

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert not (workdir / "digests" / f"{_TODAY}.raw.json").exists()


def test_run_output_dir_flag_overrides_default_location(workdir, monkeypatch):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()])

    assert _run_cli(["run", "--profile", "profile.yaml", "--output-dir", "out/here"]) == 0
    assert (workdir / "out" / "here" / f"{_TODAY}.md").exists()
    assert not (workdir / "digests").exists()


def test_run_named_profile_gets_its_own_state_and_digest_names(workdir, monkeypatch):
    """Two profiles must never share seen-state (spec 03 §multi-profile)."""
    _write_profile(workdir / "profile-ospo.yaml")
    _stub_pipeline(monkeypatch, [_result()])

    assert _run_cli(["run", "--profile", "profile-ospo.yaml"]) == 0

    assert (workdir / "state" / "state-profile-ospo.yaml").exists()
    assert not (workdir / "state" / "state.yaml").exists()
    assert (workdir / "digests" / f"{_TODAY}-profile-ospo.md").exists()
    assert (workdir / "state" / "source_stats-profile-ospo.json").exists()


def test_run_explicit_state_path_is_not_slug_rewritten(workdir, monkeypatch):
    _write_profile(workdir / "profile-ospo.yaml")
    _stub_pipeline(monkeypatch, [_result()])

    assert _run_cli(["run", "--profile", "profile-ospo.yaml", "--state", "mine/s.yaml"]) == 0
    assert (workdir / "mine" / "s.yaml").exists()


def test_run_second_run_partitions_previously_seen(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml", show_previously_seen=True)
    _stub_pipeline(monkeypatch, [_result()])

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    capsys.readouterr()
    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0

    second = capsys.readouterr().out
    assert "Previously shown: 1" in second


def test_run_appends_one_stats_entry_per_run(workdir, monkeypatch):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()])

    _run_cli(["run", "--profile", "profile.yaml"])
    _run_cli(["run", "--profile", "profile.yaml"])

    stats = json.loads((workdir / "state" / "source_stats.json").read_text())
    assert len(stats["runs"]) == 2


def test_run_counts_shown_per_source_in_stats(workdir, monkeypatch):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result(lid="a" * 64), _result(rank=2, lid="b" * 64)])

    _run_cli(["run", "--profile", "profile.yaml"])

    stats = json.loads((workdir / "state" / "source_stats.json").read_text())
    stub = [s for s in stats["runs"][0]["sources"] if s["name"] == "stub"][0]
    assert stub["shown"] == 2


# ---------------------------------------------------------------------------
# run — verbose per-source progress (-v / --verbose)
# ---------------------------------------------------------------------------


def test_run_verbose_explains_a_failed_source(workdir, monkeypatch, capsys):
    """A source that errored out (dead endpoint, rate limit, ...) gets its own line."""
    report = _report(
        source_stats=[
            SourceStat(
                name="remotive",
                fetched=0,
                requests=1,
                failed=True,
                error="Client error '429 Too Many Requests' for url 'https://remotive.example/api'",
            )
        ]
    )
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()], report=report)

    _run_cli(["run", "--profile", "profile.yaml", "--verbose"])

    err = capsys.readouterr().err
    assert "remotive: 0 fetched" in err
    assert "429" in err


def test_run_verbose_explains_a_fully_filtered_source(workdir, monkeypatch, capsys):
    """A source that fetched listings but none survived Stage 4 says so, not just '0'."""
    report = _report(
        source_stats=[SourceStat(name="rss", fetched=12, passed_filter=0, requests=1)]
    )
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()], report=report)

    _run_cli(["run", "--profile", "profile.yaml", "--verbose"])

    err = capsys.readouterr().err
    assert "rss: 12 fetched" in err
    assert "filtered out" in err


def test_run_verbose_shows_a_healthy_source_too(workdir, monkeypatch, capsys):
    report = _report(
        source_stats=[SourceStat(name="greenhouse", fetched=56, passed_filter=10, requests=1)]
    )
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()], report=report)

    _run_cli(["run", "--profile", "profile.yaml", "--verbose"])

    assert "greenhouse: 56 fetched (1 request)" in capsys.readouterr().err


def test_run_without_verbose_flag_prints_no_source_progress(workdir, monkeypatch, capsys):
    """Default behaviour is unchanged: no per-source lines without -v."""
    report = _report(
        source_stats=[
            SourceStat(name="remotive", fetched=0, requests=1, failed=True, error="boom"),
        ]
    )
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()], report=report)

    _run_cli(["run", "--profile", "profile.yaml"])

    assert "remotive" not in capsys.readouterr().err


def test_run_verbose_flag_does_not_change_stdout(workdir, monkeypatch, capsys):
    """
    The digest already renders its own source-stats table (source_stats.py,
    unrelated to this feature) — so the real acceptance criterion isn't "no
    source names in stdout", it's that -v changes stderr only. Run twice with
    the same stubbed report and diff stdout.
    """

    def make_report():
        return _report(
            source_stats=[SourceStat(name="greenhouse", fetched=56, passed_filter=10, requests=1)]
        )

    _write_profile(workdir / "profile.yaml")

    # Separate --state per run: the same state file would mark run 1's
    # listing "previously seen" for run 2, changing the digest for a reason
    # that has nothing to do with -v.
    _stub_pipeline(monkeypatch, [_result()], report=make_report())
    _run_cli(["run", "--profile", "profile.yaml", "--state", "state/a.yaml"])
    without_flag = capsys.readouterr().out

    _stub_pipeline(monkeypatch, [_result()], report=make_report())
    _run_cli(["run", "--profile", "profile.yaml", "--state", "state/b.yaml", "--verbose"])
    with_flag = capsys.readouterr().out

    assert with_flag == without_flag


def test_source_skipped_without_credential_is_explained(workdir, monkeypatch, capsys):
    """
    Case 4 from the issue: a source enabled but missing its credential. This
    is explained by _build_adapters's existing warning (unconditional, not
    gated behind -v — the adapter is never even constructed, so there is no
    SourceStat for _log_source_progress to report on).

    Can't use _write_profile here — it hardcodes sources.adzuna.enabled=False
    to keep the other run tests independent of the ambient environment, which
    would take the silent opt-out path instead of the one this test is for.
    """
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    data = yaml.safe_load(_EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    data["sources"] = {}  # absent adzuna block => enabled by default
    (workdir / "profile.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    _stub_pipeline(monkeypatch, [_result()])

    _run_cli(["run", "--profile", "profile.yaml"])

    assert "ADZUNA_APP_ID" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# run — failure paths
# ---------------------------------------------------------------------------


def test_run_missing_profile_exits_1(workdir, capsys):
    assert _run_cli(["run", "--profile", "nope.yaml"]) == 1
    assert "Error:" in capsys.readouterr().err


def test_run_invalid_profile_exits_1(workdir, capsys):
    (workdir / "profile.yaml").write_text("identity: {}\n")
    assert _run_cli(["run", "--profile", "profile.yaml"]) == 1
    assert "Error:" in capsys.readouterr().err


def test_run_unsupported_state_schema_exits_1(workdir, capsys):
    _write_profile(workdir / "profile.yaml")
    state = workdir / "state" / "state.yaml"
    state.parent.mkdir()
    state.write_text("schema_version: 99\n")

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 1
    assert "Error loading state" in capsys.readouterr().err


def test_run_warns_when_fx_rates_are_stale(workdir, monkeypatch, capsys):
    import time

    _write_profile(workdir / "profile.yaml")
    fx = workdir / "fx_rates.yaml"
    fx.write_text("base: AUD\nrates:\n  USD: 1.5\n")
    old = time.time() - 100 * 86400
    os.utime(fx, (old, old))
    _stub_pipeline(monkeypatch, [_result()])

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert "fx_rates.yaml is 100 days old" in capsys.readouterr().err


def test_run_warns_but_succeeds_when_digest_write_fails(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()])

    real_write = Path.write_text

    def failing_write(self, *args, **kwargs):
        if self.suffix == ".md":
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert "could not write digest" in capsys.readouterr().err


def test_run_warns_but_succeeds_when_csv_write_fails(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml", data_format="csv")
    _stub_pipeline(monkeypatch, [_result()])

    real_write = Path.write_text

    def failing_write(self, *args, **kwargs):
        if self.suffix == ".csv":
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert "could not write CSV file" in capsys.readouterr().err


def test_run_warns_but_succeeds_when_html_write_fails(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml", format="html")
    _stub_pipeline(monkeypatch, [_result()])

    real_write = Path.write_text

    def failing_write(self, *args, **kwargs):
        if self.suffix == ".html":
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert "could not write HTML digest" in capsys.readouterr().err


def test_run_warns_but_succeeds_when_json_write_fails(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml", keep_raw=False)
    _stub_pipeline(monkeypatch, [_result()])

    real_write = Path.write_text

    def failing_write(self, *args, **kwargs):
        if self.suffix == ".json":
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert "could not write data file" in capsys.readouterr().err


def test_run_warns_but_succeeds_when_state_save_fails(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()])

    def boom(state, path):
        raise OSError("read-only")

    monkeypatch.setattr(cli, "save_state", boom)

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert "could not save state" in capsys.readouterr().err


def test_run_warns_but_succeeds_when_stats_save_fails(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()])

    def boom(history, path):
        raise OSError("read-only")

    monkeypatch.setattr(cli, "save_stats", boom)

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert "could not save source stats" in capsys.readouterr().err


def test_run_warns_but_succeeds_when_snapshot_write_fails(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result()])

    import jobhunter.snapshot as snapshot_mod

    def boom(path, run_at, profile_snapshot, listings):
        raise OSError("no space")

    monkeypatch.setattr(snapshot_mod, "write_snapshot", boom)

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert "could not write snapshot" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# dismiss / undismiss / dismissed
# ---------------------------------------------------------------------------


def test_dismiss_records_ids_and_reports_each(workdir, capsys):
    a, b = "a" * 64, "b" * 64
    code = _run_cli(["dismiss", a, b, "--state", "state/state.yaml"])

    assert code == 0
    out = capsys.readouterr().out
    assert f"Dismissed: {a}" in out and f"Dismissed: {b}" in out
    state = yaml.safe_load((workdir / "state" / "state.yaml").read_text())
    assert state["dismissed_ids"] == [a, b]


def test_dismiss_is_idempotent(workdir):
    _run_cli(["dismiss", "a" * 64])
    _run_cli(["dismiss", "a" * 64])
    state = yaml.safe_load((workdir / "state" / "state.yaml").read_text())
    assert state["dismissed_ids"] == ["a" * 64]


def test_dismiss_bad_state_exits_1(workdir, capsys):
    state = workdir / "state" / "state.yaml"
    state.parent.mkdir()
    state.write_text("schema_version: 99\n")
    assert _run_cli(["dismiss", "x"]) == 1
    assert "Error loading state" in capsys.readouterr().err


def test_dismiss_save_failure_exits_1(workdir, monkeypatch, capsys):
    monkeypatch.setattr(cli, "save_state", lambda s, p: (_ for _ in ()).throw(OSError("nope")))
    assert _run_cli(["dismiss", "a" * 64]) == 1
    assert "Error saving state" in capsys.readouterr().err


def test_undismiss_removes_id(workdir, capsys):
    keep, drop = "c" * 64, "d" * 64
    _run_cli(["dismiss", keep, drop])
    capsys.readouterr()

    assert _run_cli(["undismiss", drop]) == 0
    assert f"Undismissed: {drop}" in capsys.readouterr().out
    state = yaml.safe_load((workdir / "state" / "state.yaml").read_text())
    assert state["dismissed_ids"] == [keep]


def test_undismiss_bad_state_exits_1(workdir, capsys):
    state = workdir / "state" / "state.yaml"
    state.parent.mkdir()
    state.write_text("schema_version: 99\n")
    assert _run_cli(["undismiss", "a" * 64]) == 1
    assert "Error loading state" in capsys.readouterr().err


def test_undismiss_save_failure_exits_1(workdir, monkeypatch, capsys):
    _run_cli(["dismiss", "a" * 64])  # must be dismissed before it can be undismissed
    monkeypatch.setattr(cli, "save_state", lambda s, p: (_ for _ in ()).throw(OSError("nope")))
    assert _run_cli(["undismiss", "a" * 64]) == 1
    assert "Error saving state" in capsys.readouterr().err


def test_dismissed_empty_says_so(workdir, capsys):
    assert _run_cli(["dismissed"]) == 0
    assert "No dismissed listings." in capsys.readouterr().out


def test_dismissed_lists_ids(workdir, capsys):
    one, two = "1" * 64, "2" * 64
    _run_cli(["dismiss", one, two])
    capsys.readouterr()

    assert _run_cli(["dismissed"]) == 0
    out = capsys.readouterr().out
    assert one in out and two in out


def test_dismissed_bad_state_exits_1(workdir, capsys):
    state = workdir / "state" / "state.yaml"
    state.parent.mkdir()
    state.write_text("schema_version: 99\n")
    assert _run_cli(["dismissed"]) == 1
    assert "Error loading state" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# dismiss — the digest-to-dismiss round trip
#
# The bug this section exists for: the digest renders ids truncated to 8 chars
# (digest._short_id), the filter matches on the full 64-char hash, and dismiss
# used to store whatever string it was handed. Every unit test used the same
# invented id on both sides of that seam, so `dismiss <what the user can see>`
# was a silent no-op that 1,282 passing tests never noticed.
# ---------------------------------------------------------------------------


def _id_from_digest(text: str) -> str:
    """Pull the short id out of a rendered digest exactly as a user would."""
    import re

    match = re.search(r"### #\d+ `([0-9a-f]+)`", text)
    assert match, f"no listing id found in digest:\n{text[:400]}"
    return match.group(1)


def test_dismissing_the_id_shown_in_the_digest_actually_drops_the_listing(
    workdir, monkeypatch, capsys
):
    """THE acceptance criterion: copy an id out of the digest, dismiss it, it's gone."""
    from datetime import date

    from jobhunter.filter import run as filter_run
    from jobhunter.profile import load_profile

    listing = _listing(lid="0" + "f" * 63)
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result(lid="0" + "f" * 63)])
    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    digest = capsys.readouterr().out

    short_id = _id_from_digest(digest)
    assert len(short_id) == 8  # what the user can see, and all they can see

    assert _run_cli(["dismiss", short_id]) == 0

    state = yaml.safe_load((workdir / "state" / "state.yaml").read_text())
    assert state["dismissed_ids"] == [listing.id]  # full id stored, not the prefix

    profile = load_profile(workdir / "profile.yaml")
    profile["hard_requirements"]["fx_rates"] = {}
    result = filter_run(
        [listing], profile, dismissed_ids=set(state["dismissed_ids"]), today=date.today()
    )
    assert result.passed == []
    assert result.tally.dismissed == 1


def test_dismiss_echoes_the_full_id_it_resolved_to(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result(lid="0" + "f" * 63)])
    _run_cli(["run", "--profile", "profile.yaml"])
    capsys.readouterr()

    assert _run_cli(["dismiss", "0fffffff"]) == 0
    out = capsys.readouterr().out
    assert "0" + "f" * 63 in out
    assert "(matched 0fffffff)" in out


def test_dismiss_unknown_prefix_is_an_error_not_a_silent_store(workdir, capsys):
    assert _run_cli(["dismiss", "deadbeef"]) == 1
    err = capsys.readouterr().err
    assert "matches no listing" in err
    assert not (workdir / "state" / "state.yaml").exists()  # nothing written


def test_dismiss_rejects_an_id_too_short_to_be_unique(workdir, capsys):
    assert _run_cli(["dismiss", "abc"]) == 1
    assert "too short" in capsys.readouterr().err


def test_dismiss_ambiguous_prefix_lists_the_candidates(workdir, monkeypatch, capsys):
    _write_profile(workdir / "profile.yaml")
    first, second = "abcdef" + "1" * 58, "abcdef" + "2" * 58
    _stub_pipeline(monkeypatch, [_result(lid=first), _result(rank=2, lid=second)])
    _run_cli(["run", "--profile", "profile.yaml"])
    capsys.readouterr()

    assert _run_cli(["dismiss", "abcdef"]) == 1
    err = capsys.readouterr().err
    assert "ambiguous" in err and "matches 2" in err


def test_dismiss_batch_is_all_or_nothing(workdir, monkeypatch, capsys):
    """A typo in the second id must not leave the first one half-applied."""
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [_result(lid="0" + "f" * 63)])
    _run_cli(["run", "--profile", "profile.yaml"])
    capsys.readouterr()

    assert _run_cli(["dismiss", "0fffffff", "deadbeef"]) == 1

    state = yaml.safe_load((workdir / "state" / "state.yaml").read_text())
    assert state["dismissed_ids"] == []


def test_dismiss_accepts_a_full_id_from_the_json_digest(workdir, capsys):
    """Scripts read full ids from the .json companion; those must still work."""
    assert _run_cli(["dismiss", "9" * 64]) == 0
    state = yaml.safe_load((workdir / "state" / "state.yaml").read_text())
    assert state["dismissed_ids"] == ["9" * 64]


def test_undismiss_accepts_the_short_id(workdir, capsys):
    full = "0" + "f" * 63
    _run_cli(["dismiss", full])
    capsys.readouterr()

    assert _run_cli(["undismiss", "0fffffff"]) == 0
    assert f"Undismissed: {full}" in capsys.readouterr().out
    state = yaml.safe_load((workdir / "state" / "state.yaml").read_text())
    assert state["dismissed_ids"] == []


def test_undismiss_something_never_dismissed_is_an_error(workdir, capsys):
    _run_cli(["dismiss", "a" * 64])
    capsys.readouterr()

    assert _run_cli(["undismiss", "b" * 64]) == 1
    assert "not currently dismissed" in capsys.readouterr().err


def test_undismiss_unknown_prefix_is_an_error(workdir, capsys):
    _run_cli(["dismiss", "a" * 64])
    capsys.readouterr()

    assert _run_cli(["undismiss", "beefbeef"]) == 1
    assert "matches no listing" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


def _write_stats(path: Path, runs: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "runs": [
            {
                "run_at": f"2026-07-2{i}",
                "sources": [
                    {"name": "stub", "fetched": 10, "passed_filter": 4, "shown": 2},
                    {"name": "retired-source", "fetched": 1},
                ],
            }
            for i in range(runs)
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def test_sources_with_no_stats_file_says_nothing_recorded(workdir, capsys):
    assert _run_cli(["sources"]) == 0
    assert "No source stats recorded yet." in capsys.readouterr().out


def test_sources_table_lists_each_source(workdir, capsys):
    _write_stats(workdir / "state" / "source_stats.json")
    assert _run_cli(["sources"]) == 0
    out = capsys.readouterr().out
    assert "stub" in out and "Fetched" in out


def test_sources_marks_sources_absent_from_the_profile_as_inactive(workdir, capsys):
    _write_profile(workdir / "profile.yaml")
    _write_stats(workdir / "state" / "source_stats.json")

    assert _run_cli(["sources", "--profile", "profile.yaml"]) == 0
    assert "inactive" in capsys.readouterr().out


def test_sources_json_is_machine_readable(workdir, capsys):
    _write_stats(workdir / "state" / "source_stats.json")
    assert _run_cli(["sources", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == 1 and len(data["runs"]) == 2


def test_sources_last_n_limits_runs(workdir, capsys):
    _write_stats(workdir / "state" / "source_stats.json", runs=3)
    assert _run_cli(["sources", "--json", "--last", "1"]) == 0
    assert len(json.loads(capsys.readouterr().out)["runs"]) == 1


def test_sources_table_reports_the_run_window(workdir, capsys):
    _write_stats(workdir / "state" / "source_stats.json", runs=1)
    assert _run_cli(["sources"]) == 0
    assert "last 1 run" in capsys.readouterr().out


def test_sources_bad_schema_exits_1(workdir, capsys):
    sp = workdir / "state" / "source_stats.json"
    sp.parent.mkdir()
    sp.write_text(json.dumps({"schema_version": 99, "runs": []}))

    assert _run_cli(["sources"]) == 1
    assert "Error loading source stats" in capsys.readouterr().err


def test_sources_unreadable_profile_still_prints_the_table(workdir, capsys):
    """An invalid profile only costs the inactive marking, not the command."""
    (workdir / "profile.yaml").write_text("not: a valid profile\n")
    _write_stats(workdir / "state" / "source_stats.json")

    assert _run_cli(["sources", "--profile", "profile.yaml"]) == 0
    assert "stub" in capsys.readouterr().out


def test_sources_named_profile_reads_slugged_stats(workdir, capsys):
    _write_profile(workdir / "profile-ospo.yaml")
    _write_stats(workdir / "state" / "source_stats-profile-ospo.json")

    assert _run_cli(["sources", "--profile", "profile-ospo.yaml"]) == 0
    assert "stub" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _print_diff
# ---------------------------------------------------------------------------


def test_print_diff_reports_entered_left_and_moved(capsys):
    stayed_now = _result(rank=1, score=90.0, lid="s" * 64)
    stayed_before = _result(rank=3, score=70.0, lid="s" * 64)
    entered = _result(rank=2, lid="e" * 64)
    left = _result(rank=1, lid="l" * 64)

    cli._print_diff([stayed_now, entered], [stayed_before, left])

    out = capsys.readouterr().out
    assert "Entered (now shown, wasn't): 1" in out
    assert "Left (was shown, not now): 1" in out
    assert "Moved (rank/score changed): 1" in out
    assert "was #3/70, now #1/90" in out


def test_print_diff_ignores_sub_threshold_score_drift(capsys):
    now = _result(rank=1, score=90.001, lid="s" * 64)
    before = _result(rank=1, score=90.0, lid="s" * 64)

    cli._print_diff([now], [before])
    assert "Moved (rank/score changed): 0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot(workdir):
    """A real snapshot file plus the profile that produced it."""
    from jobhunter.profile import load_profile
    from jobhunter.snapshot import write_snapshot

    profile_path = _write_profile(workdir / "profile.yaml")
    profile = load_profile(profile_path)
    listings = [
        _listing(lid="a" * 64),
        _listing(lid="b" * 64, title="PHP Developer", company="Beta"),
    ]
    snap_path = workdir / "digests" / f"{_TODAY}.raw.json"
    write_snapshot(snap_path, _TODAY, profile, listings)
    return snap_path


def test_replay_prints_a_digest_from_a_snapshot(snapshot, capsys):
    assert _run_cli(["replay", str(snapshot), "--set", "output.display_threshold=0"]) == 0
    out = capsys.readouterr().out
    assert "JobHunter — Run Report" in out
    assert "Senior Software Engineer" in out


def test_replay_reports_zero_requests(snapshot, capsys):
    """Replay is offline by construction — the header must say so."""
    assert _run_cli(["replay", str(snapshot)]) == 0
    assert "Requests made: 0" in capsys.readouterr().out


def test_replay_out_file_keeps_stdout_clean(snapshot, workdir, capsys):
    out_file = workdir / "replays" / "one.md"
    assert _run_cli(["replay", str(snapshot), "--out", str(out_file)]) == 0

    assert out_file.exists()
    captured = capsys.readouterr()
    assert "Replay digest:" in captured.err
    assert "JobHunter — Run Report" not in captured.out


def test_replay_warns_when_out_file_cannot_be_written(snapshot, workdir, monkeypatch, capsys):
    real_write = Path.write_text

    def failing_write(self, *args, **kwargs):
        if self.name == "one.md":
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)

    assert _run_cli(["replay", str(snapshot), "--out", str(workdir / "one.md")]) == 0
    assert "could not write" in capsys.readouterr().err


def test_replay_missing_snapshot_exits_1(workdir, capsys):
    assert _run_cli(["replay", "nope.raw.json"]) == 1
    err = capsys.readouterr().err
    assert "Snapshot file not found" in err
    assert "keep_raw" in err  # tells the user which knob to set


def test_replay_alternate_profile_is_used(snapshot, workdir, capsys):
    other = _write_profile(workdir / "profile-other.yaml", display_threshold=0, max_shown=1)
    assert _run_cli(["replay", str(snapshot), "--profile", str(other)]) == 0
    assert "JobHunter — Run Report" in capsys.readouterr().out


def test_replay_invalid_alternate_profile_exits_1(snapshot, workdir, capsys):
    bad = workdir / "bad.yaml"
    bad.write_text("identity: {}\n")
    assert _run_cli(["replay", str(snapshot), "--profile", str(bad)]) == 1
    assert "Error:" in capsys.readouterr().err


def test_replay_set_override_changes_the_shortlist(snapshot, capsys):
    """display_threshold=100 keeps nothing; =0 keeps the passing listing."""
    assert _run_cli(["replay", str(snapshot), "--set", "output.display_threshold=0"]) == 0
    kept = capsys.readouterr().out

    assert _run_cli(["replay", str(snapshot), "--set", "output.display_threshold=100"]) == 0
    dropped = capsys.readouterr().out

    assert "Senior Software Engineer" in kept
    assert "Senior Software Engineer" not in dropped


def test_replay_diff_reports_the_comparison(snapshot, capsys):
    assert _run_cli(
        ["replay", str(snapshot), "--diff", str(snapshot), "--set", "output.display_threshold=0"]
    ) == 0
    out = capsys.readouterr().out
    assert "=== Diff (current vs other) ===" in out
    assert "Entered (now shown, wasn't): 0" in out  # same file both sides


def test_replay_diff_with_explicit_profile(snapshot, workdir, capsys):
    other = _write_profile(workdir / "profile-other.yaml", display_threshold=0)
    argv = ["replay", str(snapshot), "--diff", str(snapshot), "--profile", str(other)]
    assert _run_cli(argv) == 0
    assert "=== Diff" in capsys.readouterr().out


def test_replay_missing_diff_file_exits_1(snapshot, capsys):
    assert _run_cli(["replay", str(snapshot), "--diff", "nope.json"]) == 1
    assert "Error loading diff file" in capsys.readouterr().err


def test_replay_bad_state_exits_1(snapshot, workdir, capsys):
    state = workdir / "state" / "state.yaml"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("schema_version: 99\n")

    assert _run_cli(["replay", str(snapshot)]) == 1
    assert "Error loading state" in capsys.readouterr().err


def test_replay_respects_dismissed_ids(snapshot, workdir, capsys):
    """Replay is read-only but must still honour the dismiss list."""
    _run_cli(["dismiss", "a" * 64])
    capsys.readouterr()

    assert _run_cli(["replay", str(snapshot), "--set", "output.display_threshold=0"]) == 0
    assert "Senior Software Engineer" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def test_probe_check_with_target_is_an_error(workdir, capsys):
    assert _run_cli(["probe", "mozilla", "--check"]) == 1
    assert "cannot be combined" in capsys.readouterr().err


def test_probe_with_neither_target_nor_check_is_an_error(workdir, capsys):
    assert _run_cli(["probe"]) == 1
    assert "provide a target" in capsys.readouterr().err


def test_probe_check_invalid_profile_exits_1(workdir, capsys):
    (workdir / "profile.yaml").write_text("identity: {}\n")
    assert _run_cli(["probe", "--check", "--profile", "profile.yaml"]) == 1
    assert "Error:" in capsys.readouterr().err


def test_probe_check_empty_watchlist_is_not_a_failure(workdir, capsys):
    _write_profile(workdir / "profile.yaml")  # example profile, sources stripped
    assert _run_cli(["probe", "--check", "--profile", "profile.yaml"]) == 0
    assert "No ats_watchlist entries" in capsys.readouterr().out


def test_probe_check_all_confirmed_exits_0(workdir, monkeypatch, capsys):
    profile = _profile_dict()
    profile["sources"]["ats_watchlist"] = [
        {"ats": "greenhouse", "slug": "mozilla", "name": "Mozilla"}
    ]
    (workdir / "profile.yaml").write_text(yaml.safe_dump(profile))

    def fake_check(watchlist):
        return [
            BoardStatus(
                entry=watchlist[0],
                result=ProbeResult(
                    ats="greenhouse",
                    slug="mozilla",
                    job_count=56,
                    oldest_date="2026-04-02",
                    newest_date="2026-07-24",
                ),
                error=None,
            )
        ]

    monkeypatch.setattr(cli, "probe_check", fake_check)

    assert _run_cli(["probe", "--check", "--profile", "profile.yaml"]) == 0
    out = capsys.readouterr().out
    assert "greenhouse / mozilla — 56 jobs" in out


def test_probe_check_dead_board_exits_1(workdir, monkeypatch, capsys):
    profile = _profile_dict()
    profile["sources"]["ats_watchlist"] = [{"ats": "lever", "slug": "hashicorp"}]
    (workdir / "profile.yaml").write_text(yaml.safe_dump(profile))

    monkeypatch.setattr(
        cli,
        "probe_check",
        lambda wl: [BoardStatus(entry=wl[0], result=None, error="404")],
    )

    assert _run_cli(["probe", "--check", "--profile", "profile.yaml"]) == 1
    out = capsys.readouterr().out
    assert "dead: lever/hashicorp" in out
    assert "Hashicorp" in out  # name derived from the slug when absent


def test_probe_check_reads_legacy_queries_watchlist(workdir, monkeypatch, capsys):
    profile = _profile_dict()
    profile["queries"]["ats_watchlist"] = [{"ats": "greenhouse", "slug": "acme"}]
    (workdir / "profile.yaml").write_text(yaml.safe_dump(profile))

    monkeypatch.setattr(
        cli, "probe_check", lambda wl: [BoardStatus(entry=wl[0], result=None, error=None)]
    )

    assert _run_cli(["probe", "--check", "--profile", "profile.yaml"]) == 1
    assert "greenhouse/acme" in capsys.readouterr().out


def test_probe_single_unconfirmed_is_not_a_failure(workdir, monkeypatch, capsys):
    monkeypatch.setattr(cli, "probe_single", lambda target, ats_hint=None: [])
    assert _run_cli(["probe", "unknown-co"]) == 0
    assert "not confirmed: unknown-co" in capsys.readouterr().out


def test_probe_single_prints_each_hit(workdir, monkeypatch, capsys):
    hits = [
        ProbeResult(ats="greenhouse", slug="acme", job_count=3, oldest_date=None, newest_date=None),
        ProbeResult(ats="lever", slug="acme", job_count=1, oldest_date=None, newest_date=None),
    ]
    monkeypatch.setattr(cli, "probe_single", lambda target, ats_hint=None: hits)

    assert _run_cli(["probe", "acme"]) == 0
    out = capsys.readouterr().out
    assert "greenhouse / acme — 3 jobs" in out
    assert "lever / acme — 1 jobs" in out


def test_probe_single_passes_the_ats_hint_through(workdir, monkeypatch):
    seen = {}

    def fake_single(target, ats_hint=None):
        seen["target"] = target
        seen["hint"] = ats_hint
        return []

    monkeypatch.setattr(cli, "probe_single", fake_single)

    assert _run_cli(["probe", "acme", "--ats", "ashby"]) == 0
    assert seen == {"target": "acme", "hint": "ashby"}


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_offline_validates_config_without_touching_the_network(workdir, capsys):
    _write_profile(workdir / "profile.yaml")
    assert _run_cli(["doctor", "--profile", "profile.yaml", "--offline"]) == 0
    out = capsys.readouterr().out
    assert "profile" in out and "All checks passed" in out


def test_shipped_example_profile_passes_doctor_offline(workdir, capsys):
    """The canary's precondition: the example profile must exit 0 offline.

    It ships with Adzuna commented out, so an absent-means-enabled reading of
    the sources block made doctor exit 1 — which would have had the weekly
    canary file an issue on every run regardless of board health.
    """
    import shutil

    shutil.copy(_EXAMPLE_PROFILE, workdir / "profile.yaml")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY")
    }
    with patch.dict(os.environ, env, clear=True):
        code = _run_cli(["doctor", "--profile", "profile.yaml", "--offline"])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "FAIL" not in out


def test_doctor_reports_an_invalid_profile_and_stops_there(workdir, capsys):
    (workdir / "profile.yaml").write_text("identity: {}\n")
    assert _run_cli(["doctor", "--profile", "profile.yaml", "--offline"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    # Nothing downstream is meaningful without a profile, so no source rows.
    assert "source" not in out


def test_doctor_json_is_parseable(workdir, capsys):
    _write_profile(workdir / "profile.yaml")
    assert _run_cli(["doctor", "--profile", "profile.yaml", "--offline", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert any(c["kind"] == "profile" for c in data["checks"])


def test_doctor_fails_when_an_explicitly_enabled_source_has_no_credential(workdir, capsys):
    profile = _profile_dict()
    profile["sources"] = {"jooble": {"enabled": True}}
    (workdir / "profile.yaml").write_text(yaml.safe_dump(profile))

    env = {k: v for k, v in os.environ.items() if k != "JOOBLE_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        code = _run_cli(["doctor", "--profile", "profile.yaml", "--offline"])

    assert code == 1
    assert "JOOBLE_API_KEY" in capsys.readouterr().out


def test_doctor_queries_sources_and_probes_boards_when_online(workdir, monkeypatch, capsys):
    profile = _profile_dict()
    profile["sources"] = {
        "adzuna": {"enabled": False},
        "ats_watchlist": [{"ats": "greenhouse", "slug": "mozilla", "name": "Mozilla"}],
    }
    (workdir / "profile.yaml").write_text(yaml.safe_dump(profile))

    class _Stub:
        name = "stub"

        def search(self, keyword, location, max_results):
            return [{"a": 1}]

        def normalize(self, raw):  # pragma: no cover - unused
            raise NotImplementedError

    monkeypatch.setattr(cli, "_build_adapters", lambda p: [_Stub()])
    monkeypatch.setattr(
        cli,
        "probe_check",
        lambda wl: [
            BoardStatus(
                entry=wl[0],
                result=ProbeResult(
                    ats="greenhouse", slug="mozilla", job_count=56,
                    oldest_date=None, newest_date=None,
                ),
                error=None,
            )
        ],
    )

    assert _run_cli(["doctor", "--profile", "profile.yaml"]) == 0
    out = capsys.readouterr().out
    assert "stub" in out and "greenhouse/mozilla" in out and "56 jobs" in out


def test_doctor_exits_1_on_a_dead_board(workdir, monkeypatch, capsys):
    profile = _profile_dict()
    profile["sources"] = {
        "adzuna": {"enabled": False},
        "ats_watchlist": [{"ats": "lever", "slug": "hashicorp"}],
    }
    (workdir / "profile.yaml").write_text(yaml.safe_dump(profile))

    monkeypatch.setattr(cli, "_build_adapters", lambda p: [])
    monkeypatch.setattr(
        cli, "probe_check", lambda wl: [BoardStatus(entry=wl[0], result=None, error="404")]
    )

    assert _run_cli(["doctor", "--profile", "profile.yaml"]) == 1
    assert "dead" in capsys.readouterr().out


def test_doctor_named_profile_checks_the_slugged_state_file(workdir, capsys):
    _write_profile(workdir / "profile-ospo.yaml")
    state = workdir / "state" / "state-profile-ospo.yaml"
    state.parent.mkdir()
    state.write_text("schema_version: 99\n")

    assert _run_cli(["doctor", "--profile", "profile-ospo.yaml", "--offline"]) == 1
    out = capsys.readouterr().out
    assert "state-profile-ospo.yaml" in out


def test_doctor_warns_on_stale_fx_rates_without_failing(workdir, capsys):
    import time

    _write_profile(workdir / "profile.yaml")
    fx = workdir / "fx_rates.yaml"
    fx.write_text("base: AUD\nrates:\n  USD: 1.5\n")
    old = time.time() - 120 * 86400
    os.utime(fx, (old, old))

    assert _run_cli(["doctor", "--profile", "profile.yaml", "--offline"]) == 0
    assert "120 days old" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_exits_with_the_handler_return_code(workdir, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["jobhunter", "dismissed"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "No dismissed listings." in capsys.readouterr().out


def test_main_propagates_a_failure_code(workdir, monkeypatch, capsys):
    state = workdir / "state" / "state.yaml"
    state.parent.mkdir()
    state.write_text("schema_version: 99\n")
    monkeypatch.setattr("sys.argv", ["jobhunter", "dismissed"])

    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_version_flag_reports_the_package_version(capsys):
    """The bug-report template asks for a version; the CLI must be able to give one."""
    from jobhunter import __version__

    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_parser_requires_a_subcommand(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_build_adapters_is_wired_into_run(workdir, monkeypatch):
    """_cmd_run must build adapters from the profile, not from a hardcoded list."""
    called = {}

    def fake_build(profile):
        called["yes"] = True
        return []

    _write_profile(workdir / "profile.yaml")
    monkeypatch.setattr(cli, "_build_adapters", fake_build)
    _stub_pipeline(monkeypatch, [])

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert called == {"yes": True}


def test_run_with_no_results_still_writes_a_digest(workdir, monkeypatch):
    _write_profile(workdir / "profile.yaml")
    _stub_pipeline(monkeypatch, [])

    assert _run_cli(["run", "--profile", "profile.yaml"]) == 0
    assert (workdir / "digests" / f"{_TODAY}.md").exists()


def test_patch_dict_env_does_not_leak_adzuna_creds(workdir, monkeypatch):
    """Guard: sources.adzuna.enabled=false must win over an exported credential."""
    with patch.dict(os.environ, {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}):
        adapters = cli._build_adapters(_profile_dict())
    assert adapters == []
