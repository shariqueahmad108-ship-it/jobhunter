# SPDX-License-Identifier: Apache-2.0
"""Tests for the doctor health checks.

Every check takes its I/O as an injected callable (loader, prober, adapter list)
so the whole module is testable without touching the network — which the suite
blocks anyway. See CONTRIBUTING.md §Testing conventions.

See: specs/05-operator-tooling.md §5.4
"""

from __future__ import annotations

import json
from pathlib import Path

from jobhunter.doctor import (
    FAIL,
    OK,
    SKIP,
    WARN,
    Check,
    check_boards,
    check_credentials,
    check_fx_rates,
    check_profile,
    check_sources,
    check_state,
    exit_code,
    format_json,
    format_table,
)
from jobhunter.probe import BoardStatus, ProbeResult

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Adapter:
    """Minimal SourceAdapter: returns n listings, or raises."""

    def __init__(self, name: str, count: int = 3, error: Exception | None = None):
        self.name = name
        self._count = count
        self._error = error
        self.calls: list[tuple[str, str, int]] = []

    def search(self, keyword: str, location: str, max_results: int) -> list[dict]:
        self.calls.append((keyword, location, max_results))
        if self._error:
            raise self._error
        return [{"i": i} for i in range(self._count)]

    def normalize(self, raw: dict):  # pragma: no cover - unused by doctor
        raise NotImplementedError


def _profile(**sources) -> dict:
    return {
        "queries": {"keywords": ["staff engineer"], "locations": ["Remote"]},
        "sources": dict(sources),
    }


def _board(ats: str, slug: str, jobs: int | None, name: str = "", error: str | None = None):
    result = (
        ProbeResult(ats=ats, slug=slug, job_count=jobs, oldest_date=None, newest_date=None)
        if jobs is not None
        else None
    )
    entry = {"ats": ats, "slug": slug}
    if name:
        entry["name"] = name
    return BoardStatus(entry=entry, result=result, error=error)


# ---------------------------------------------------------------------------
# check_profile
# ---------------------------------------------------------------------------


def test_valid_profile_passes_and_returns_the_profile():
    check, profile = check_profile(Path("p.yaml"), lambda p: {"identity": {}})
    assert check.status == OK
    assert profile == {"identity": {}}


def test_invalid_profile_fails_and_returns_none():
    def boom(path):
        raise ValueError("output.format: must be one of ['both', 'html', 'markdown']")

    check, profile = check_profile(Path("p.yaml"), boom)
    assert check.status == FAIL
    assert profile is None
    assert "output.format" in check.detail  # the actual error, not a generic message


# ---------------------------------------------------------------------------
# check_fx_rates
# ---------------------------------------------------------------------------


def test_fresh_fx_rates_pass():
    assert check_fx_rates("fx_rates.yaml", 3).status == OK


def test_stale_fx_rates_warn_but_do_not_fail():
    """Stale rates degrade salary comparison silently — worth saying, not worth failing."""
    check = check_fx_rates("fx_rates.yaml", 120)
    assert check.status == WARN
    assert "120 days old" in check.detail


def test_absent_fx_rates_warn():
    assert check_fx_rates("fx_rates.yaml", None).status == WARN


# ---------------------------------------------------------------------------
# check_credentials
# ---------------------------------------------------------------------------


def test_adzuna_is_active_by_default_and_fails_without_credentials():
    checks = check_credentials(_profile(), env={})
    adzuna = [c for c in checks if c.target == "adzuna"][0]
    assert adzuna.status == FAIL
    assert "ADZUNA_APP_ID" in adzuna.detail and "ADZUNA_APP_KEY" in adzuna.detail


def test_partial_credentials_name_only_the_missing_variable():
    checks = check_credentials(_profile(), env={"ADZUNA_APP_ID": "x"})
    adzuna = [c for c in checks if c.target == "adzuna"][0]
    assert adzuna.status == FAIL
    assert "ADZUNA_APP_KEY" in adzuna.detail
    assert "ADZUNA_APP_ID" not in adzuna.detail


def test_disabled_source_is_skipped_not_failed():
    checks = check_credentials(_profile(adzuna={"enabled": False}), env={})
    assert [c.status for c in checks if c.target == "adzuna"] == [SKIP]


def test_jooble_is_opt_in_so_absent_config_is_skipped():
    checks = check_credentials(_profile(), env={})
    assert [c.status for c in checks if c.target == "jooble"] == [SKIP]


def test_enabled_jooble_with_key_passes():
    checks = check_credentials(_profile(jooble={"enabled": True}), env={"JOOBLE_API_KEY": "k"})
    assert [c.status for c in checks if c.target == "jooble"] == [OK]


def test_enabled_source_without_its_key_is_a_failure_not_a_warning():
    """`run` warns and carries on; doctor is asked whether anything is broken."""
    checks = check_credentials(_profile(jooble={"enabled": True}), env={})
    jooble = [c for c in checks if c.target == "jooble"][0]
    assert jooble.status == FAIL


def test_empty_string_credential_counts_as_missing():
    checks = check_credentials(_profile(jooble={"enabled": True}), env={"JOOBLE_API_KEY": ""})
    assert [c.status for c in checks if c.target == "jooble"] == [FAIL]


# ---------------------------------------------------------------------------
# check_sources
# ---------------------------------------------------------------------------


def test_source_returning_listings_passes():
    checks = check_sources([_Adapter("remoteok", count=7)], _profile())
    assert checks[0].status == OK
    assert "7 listings" in checks[0].detail


def test_source_returning_nothing_warns_rather_than_fails():
    """Zero results is legitimate — a narrow keyword, a quiet board."""
    checks = check_sources([_Adapter("remotive", count=0)], _profile())
    assert checks[0].status == WARN
    assert "0 listings" in checks[0].detail


def test_source_raising_fails_with_the_exception_type():
    checks = check_sources([_Adapter("adzuna", error=RuntimeError("HTTP 429"))], _profile())
    assert checks[0].status == FAIL
    assert "RuntimeError" in checks[0].detail and "429" in checks[0].detail


def test_sources_are_queried_with_the_profile_first_keyword_and_location():
    adapter = _Adapter("remoteok")
    check_sources([adapter], _profile(), max_results=2)
    assert adapter.calls == [("staff engineer", "Remote", 2)]


def test_sources_with_no_queries_configured_still_run():
    adapter = _Adapter("remoteok")
    check_sources([adapter], {"sources": {}})
    assert adapter.calls == [("", "", 5)]


def test_one_bad_source_does_not_stop_the_others():
    adapters = [
        _Adapter("dead", error=RuntimeError("boom")),
        _Adapter("alive", count=2),
    ]
    statuses = [c.status for c in check_sources(adapters, _profile())]
    assert statuses == [FAIL, OK]


# ---------------------------------------------------------------------------
# check_boards
# ---------------------------------------------------------------------------


def test_live_board_passes_with_its_job_count():
    checks = check_boards(
        [{"ats": "greenhouse", "slug": "mozilla"}],
        lambda wl: [_board("greenhouse", "mozilla", 56, name="Mozilla")],
    )
    assert checks[0].status == OK
    assert "56 jobs (Mozilla)" in checks[0].detail


def test_dead_board_fails_and_names_the_slug():
    """This is the check the whole command exists for — silent board rot."""
    checks = check_boards(
        [{"ats": "lever", "slug": "hashicorp"}],
        lambda wl: [_board("lever", "hashicorp", None, error="404")],
    )
    assert checks[0].status == FAIL
    assert checks[0].target == "lever/hashicorp"
    assert "404" in checks[0].detail


def test_dead_board_without_an_error_still_reports_not_confirmed():
    checks = check_boards(
        [{"ats": "ashby", "slug": "nope"}],
        lambda wl: [_board("ashby", "nope", None)],
    )
    assert "not confirmed" in checks[0].detail


def test_empty_watchlist_is_skipped():
    checks = check_boards([], lambda wl: [])
    assert checks[0].status == SKIP


# ---------------------------------------------------------------------------
# check_state
# ---------------------------------------------------------------------------


def test_absent_state_file_is_fine():
    check = check_state(Path("/nonexistent/state.yaml"), lambda p: None)
    assert check.status == OK
    assert "first run" in check.detail


def test_unreadable_state_file_fails(tmp_path):
    state = tmp_path / "state.yaml"
    state.write_text("schema_version: 99\n")

    def boom(path):
        raise ValueError("schema_version 99 not supported")

    check = check_state(state, boom)
    assert check.status == FAIL
    assert "99" in check.detail


def test_readable_state_file_passes(tmp_path):
    state = tmp_path / "state.yaml"
    state.write_text("schema_version: 1\n")
    assert check_state(state, lambda p: None).status == OK


# ---------------------------------------------------------------------------
# Rendering and exit code
# ---------------------------------------------------------------------------


def _mixed() -> list[Check]:
    return [
        Check("profile", "profile.yaml", OK, "valid"),
        Check("fx", "fx_rates.yaml", WARN, "120 days old"),
        Check("board", "lever/hashicorp", FAIL, "dead — 404"),
        Check("credential", "jooble", SKIP, "not enabled"),
    ]


def test_table_marks_failures_in_caps_so_they_are_scannable():
    table = format_table(_mixed())
    assert "FAIL" in table
    assert "lever/hashicorp" in table
    assert "1 failing, 1 warning" in table


def test_table_says_so_when_everything_passes():
    assert "All checks passed." in format_table([Check("profile", "p.yaml", OK)])


def test_table_counts_warnings_when_there_are_no_failures():
    table = format_table([Check("fx", "fx_rates.yaml", WARN, "120 days old")])
    assert "All checks passed, 1 warning." in table


def test_table_handles_no_checks():
    assert format_table([]) == "No checks ran."


def test_json_is_machine_readable_and_carries_an_ok_flag():
    data = json.loads(format_json(_mixed()))
    assert data["ok"] is False
    assert len(data["checks"]) == 4
    assert data["checks"][2] == {
        "kind": "board",
        "target": "lever/hashicorp",
        "status": "fail",
        "detail": "dead — 404",
    }


def test_json_ok_is_true_when_only_warnings():
    data = json.loads(format_json([Check("fx", "fx_rates.yaml", WARN, "stale")]))
    assert data["ok"] is True


def test_exit_code_is_1_only_for_failures():
    assert exit_code(_mixed()) == 1
    assert exit_code([Check("fx", "f", WARN)]) == 0
    assert exit_code([Check("profile", "p", OK)]) == 0
    assert exit_code([]) == 0
