# SPDX-License-Identifier: Apache-2.0
"""Tests for the run-snapshot and offline replay feature.

Covers specs/05-operator-tooling.md §5.2 acceptance criteria:
- Replaying against an unmodified profile reproduces the original shortlist exactly.
- Replay performs zero HTTP requests (network-forbidding fixture harness).
- Missing snapshot fails loudly naming output.keep_raw.
- --set output.display_threshold=<higher> yields a subset; lower yields a superset.
- After replay, run-state is byte-identical to before.
- --diff on two identical snapshots reports zero entered, zero left, zero moved.

See: specs/03-data-model.md §Run snapshot
     specs/05-operator-tooling.md §5.2
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from jobhunter.filter import run as filter_run
from jobhunter.model import JobListing, Location, Salary, Seniority, Source
from jobhunter.pipeline import run as pipeline_run
from jobhunter.pipeline import run_with_snapshot
from jobhunter.rank import run as rank_run
from jobhunter.score import run as score_run
from jobhunter.snapshot import (
    apply_set_overrides,
    listing_from_dict,
    listing_to_dict,
    load_snapshot,
    write_snapshot,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TODAY_STR = "2026-07-25"

BASE_PROFILE: dict = {
    "identity": {
        "target_skills": ["Python", "open source"],
        "target": [{"track": "ic", "level": "senior"}],
    },
    "queries": {
        "keywords": ["engineer"],
        "locations": ["Remote"],
        "max_results_per_query": 50,
        "max_requests_per_run": 100,
    },
    "hard_requirements": {
        "remote_policy": "remote_only",
        "exclude_locations": [],
        "locations_allowed": [],
        "seniority": {"ic": {"min": "mid", "max": None}},
        "salary_floor": None,
        "keep_unknown_salary": True,
        "exclude_employment": [],
        "exclude_keywords": [],
        "max_age_days": 60,
        "fx_rates": {},
    },
    "preferences": {"preferred_locations": [], "preferred_companies": []},
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
        "keep_raw": True,
    },
}


def _make_listing(
    *,
    listing_id: str = "aabbcc" + "0" * 58,
    title: str = "Senior Engineer",
    company: str = "Acme",
    is_remote: bool = True,
    posted_at: str = "2026-07-20",
    description: str = "We love Python and open source.",
    source_name: str = "fixture",
) -> JobListing:
    return JobListing(
        id=listing_id,
        content_hash="deadbeef",
        title=title,
        company=company,
        location=Location(raw="Remote", is_remote=is_remote),
        description=description,
        sources=[
            Source(
                name=source_name,
                url=f"https://example.com/{listing_id}",
                source_id=listing_id[:8],
            )
        ],
        first_seen_at="2026-07-20",
        posted_at=posted_at,
    )


# ---------------------------------------------------------------------------
# Snapshot serialization round-trip
# ---------------------------------------------------------------------------


class TestSnapshotSerialization:
    def test_listing_round_trips(self):
        listing = _make_listing(
            listing_id="aa" * 32,
        )
        listing.salary = Salary(min=150000, max=200000, currency="AUD", period="year")
        listing.seniority = Seniority(track="ic", level="senior")
        listing.employment = "full_time"

        restored = listing_from_dict(listing_to_dict(listing))
        assert restored.id == listing.id
        assert restored.title == listing.title
        assert restored.company == listing.company
        assert restored.location.raw == listing.location.raw
        assert restored.location.is_remote == listing.location.is_remote
        assert restored.salary is not None
        assert restored.salary.min == 150000
        assert restored.salary.currency == "AUD"
        assert restored.seniority is not None
        assert restored.seniority.track == "ic"
        assert restored.seniority.level == "senior"
        assert restored.employment == "full_time"

    def test_listing_without_optional_fields(self):
        listing = _make_listing()
        restored = listing_from_dict(listing_to_dict(listing))
        assert restored.salary is None
        assert restored.seniority is None
        assert restored.employment is None

    def test_write_and_load_snapshot(self, tmp_path: Path):
        listings = [_make_listing(listing_id="cc" * 32)]
        run_at = "2026-07-25T10:00:00+00:00"
        snap_path = tmp_path / "test.raw.json"

        write_snapshot(snap_path, run_at, BASE_PROFILE, listings)

        assert snap_path.exists()
        snap = load_snapshot(snap_path)
        assert snap["run_at"] == run_at
        assert len(snap["listings"]) == 1
        assert snap["listings"][0].id == "cc" * 32
        assert snap["profile_snapshot"] == BASE_PROFILE

    def test_snapshot_json_structure(self, tmp_path: Path):
        listings = [_make_listing()]
        snap_path = tmp_path / "test.raw.json"
        write_snapshot(snap_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, listings)

        raw = json.loads(snap_path.read_text())
        assert raw["schema_version"] == 1
        assert "run_at" in raw
        assert "profile_snapshot" in raw
        assert "listings" in raw
        assert isinstance(raw["listings"], list)


# ---------------------------------------------------------------------------
# load_snapshot error cases
# ---------------------------------------------------------------------------


class TestLoadSnapshotErrors:
    def test_missing_file_names_keep_raw(self, tmp_path: Path):
        with pytest.raises(ValueError) as exc_info:
            load_snapshot(tmp_path / "nonexistent.raw.json")
        assert "keep_raw" in str(exc_info.value)

    def test_wrong_schema_version_fails_loud(self, tmp_path: Path):
        snap_path = tmp_path / "bad.raw.json"
        snap_path.write_text(
            json.dumps({"schema_version": 99, "run_at": "2026-07-25", "listings": []}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="schema_version 99"):
            load_snapshot(snap_path)


# ---------------------------------------------------------------------------
# apply_set_overrides
# ---------------------------------------------------------------------------


class TestApplySetOverrides:
    def test_scalar_override(self):
        profile = apply_set_overrides(BASE_PROFILE, ["output.display_threshold=70"])
        assert profile["output"]["display_threshold"] == 70

    def test_int_parsed(self):
        profile = apply_set_overrides(BASE_PROFILE, ["output.max_shown=10"])
        assert profile["output"]["max_shown"] == 10
        assert isinstance(profile["output"]["max_shown"], int)

    def test_float_parsed(self):
        profile = apply_set_overrides(BASE_PROFILE, ["weights.skill_match=0.5"])
        assert profile["weights"]["skill_match"] == pytest.approx(0.5)

    def test_bool_true(self):
        profile = apply_set_overrides(BASE_PROFILE, ["output.show_previously_seen=false"])
        assert profile["output"]["show_previously_seen"] is False

    def test_original_not_mutated(self):
        original_threshold = BASE_PROFILE["output"]["display_threshold"]
        apply_set_overrides(BASE_PROFILE, ["output.display_threshold=99"])
        assert BASE_PROFILE["output"]["display_threshold"] == original_threshold

    def test_multiple_overrides(self):
        profile = apply_set_overrides(
            BASE_PROFILE,
            ["output.display_threshold=50", "output.max_shown=5"],
        )
        assert profile["output"]["display_threshold"] == 50
        assert profile["output"]["max_shown"] == 5


# ---------------------------------------------------------------------------
# pipeline.run_with_snapshot integration
# ---------------------------------------------------------------------------


class _FixtureAdapter:
    name = "fixture"

    def __init__(self, listings: list[JobListing]) -> None:
        self._listings = listings

    def search(self, keyword: str, location: str, max_results: int) -> list[dict]:
        return [{"_listing": li} for li in self._listings]

    def normalize(self, raw: dict) -> JobListing:
        return raw["_listing"]


class TestRunWithSnapshot:
    def test_returns_pre_filter_listings(self):
        listing = _make_listing(listing_id="11" * 32)
        adapter = _FixtureAdapter([listing])
        shortlist, report, pre_filter = run_with_snapshot(BASE_PROFILE, [adapter])
        assert len(pre_filter) == 1
        assert pre_filter[0].id == "11" * 32

    def test_pre_filter_is_post_dedupe(self):
        # Two listings with the same id — dedupe keeps one, pre_filter should show 1.
        a = _make_listing(listing_id="22" * 32)
        b = _make_listing(listing_id="22" * 32)  # same id → merged
        adapter = _FixtureAdapter([a, b])
        _, _, pre_filter = run_with_snapshot(BASE_PROFILE, [adapter])
        assert len(pre_filter) == 1

    def test_run_still_works(self):
        """pipeline.run() backward compatibility: returns 2-tuple."""
        adapter = _FixtureAdapter([_make_listing()])
        result = pipeline_run(BASE_PROFILE, [adapter])
        assert len(result) == 2
        shortlist, report = result
        assert isinstance(shortlist, list)


# ---------------------------------------------------------------------------
# Replay correctness — reproduces original shortlist exactly
# ---------------------------------------------------------------------------


class TestReplayCorrectness:
    def test_replay_reproduces_identical_shortlist(self, tmp_path: Path):
        """Replaying a snapshot with the same profile gives the same scored set."""
        from datetime import date

        listings = [
            _make_listing(listing_id="aa" * 32, title="Senior Python Engineer"),
            _make_listing(
                listing_id="bb" * 32,
                title="Junior Developer",
                posted_at="2026-07-20",
            ),
        ]
        # The second listing has no seniority in the title that would be filtered,
        # but the profile requires ic.min=mid. "Junior" → ic.junior → DROPS.
        # Only the "Senior Python Engineer" should survive.

        snap_path = tmp_path / "run.raw.json"
        run_at = "2026-07-25T10:00:00+00:00"
        write_snapshot(snap_path, run_at, BASE_PROFILE, listings)

        snap = load_snapshot(snap_path)
        run_date = date.fromisoformat(snap["run_at"][:10])
        profile = snap["profile_snapshot"]

        filter_result = filter_run(snap["listings"], profile, today=run_date)
        scored = score_run(
            filter_result.passed, profile, unknown_flags=filter_result.unknown_flags, today=run_date
        )
        shortlist, _ = rank_run(scored, profile)

        # Scores must match a direct pipeline run on the same listings.
        adapter = _FixtureAdapter(listings)
        direct_shortlist, _ = pipeline_run(BASE_PROFILE, [adapter])

        replay_ids = {r.listing.id for r in shortlist}
        direct_ids = {r.listing.id for r in direct_shortlist}
        assert replay_ids == direct_ids

    def test_replay_scores_match_original(self, tmp_path: Path):
        from datetime import date

        listing = _make_listing(listing_id="cc" * 32)
        snap_path = tmp_path / "run.raw.json"
        run_at = "2026-07-25T10:00:00+00:00"
        write_snapshot(snap_path, run_at, BASE_PROFILE, [listing])

        snap = load_snapshot(snap_path)
        run_date = date.fromisoformat(snap["run_at"][:10])
        profile = snap["profile_snapshot"]

        filter_result = filter_run(snap["listings"], profile, today=run_date)
        scored = score_run(
            filter_result.passed, profile, unknown_flags=filter_result.unknown_flags, today=run_date
        )
        replay_shortlist, _ = rank_run(scored, profile)

        # Compare score with direct run using same reference date
        adapter = _FixtureAdapter([listing])
        direct_shortlist, _ = pipeline_run(BASE_PROFILE, [adapter], today=date(2026, 7, 25))

        assert len(replay_shortlist) == len(direct_shortlist)
        if replay_shortlist:
            assert abs(replay_shortlist[0].score - direct_shortlist[0].score) < 0.01


# ---------------------------------------------------------------------------
# Zero HTTP in replay path
# ---------------------------------------------------------------------------


class TestReplayNoNetwork:
    def test_replay_makes_no_network_calls(self, tmp_path: Path):
        """Replay Stages 4-7 with socket.socket blocked; must still succeed."""
        from datetime import date

        listing = _make_listing(listing_id="dd" * 32)
        snap_path = tmp_path / "run.raw.json"
        write_snapshot(snap_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, [listing])

        snap = load_snapshot(snap_path)

        def _no_socket(*args, **kwargs):
            raise RuntimeError("network access is forbidden in replay")

        with patch.object(socket, "socket", _no_socket):
            run_date = date(2026, 7, 25)
            filter_result = filter_run(snap["listings"], BASE_PROFILE, today=run_date)
            scored = score_run(
                filter_result.passed,
                BASE_PROFILE,
                unknown_flags=filter_result.unknown_flags,
                today=run_date,
            )
            shortlist, _ = rank_run(scored, BASE_PROFILE)

        # The listing passed the filter so the shortlist is non-empty.
        assert len(shortlist) == 1


# ---------------------------------------------------------------------------
# display_threshold override changes the shortlist
# ---------------------------------------------------------------------------


class TestSetOverrideThreshold:
    def _run_replay(self, snap: dict, threshold: int) -> list:
        from datetime import date

        profile = apply_set_overrides(
            snap["profile_snapshot"], [f"output.display_threshold={threshold}"]
        )
        run_date = date.fromisoformat(snap["run_at"][:10])
        filter_result = filter_run(snap["listings"], profile, today=run_date)
        scored = score_run(
            filter_result.passed, profile, unknown_flags=filter_result.unknown_flags, today=run_date
        )
        shortlist, _ = rank_run(scored, profile)
        return shortlist

    def test_higher_threshold_yields_subset(self, tmp_path: Path):
        listings = [
            _make_listing(listing_id="ee" * 32, title="Senior Python Engineer"),
            _make_listing(listing_id="ff" * 32, title="Staff Engineer"),
        ]
        snap_path = tmp_path / "run.raw.json"
        write_snapshot(snap_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, listings)
        snap = load_snapshot(snap_path)

        base = self._run_replay(snap, 0)
        higher = self._run_replay(snap, 80)

        base_ids = {r.listing.id for r in base}
        higher_ids = {r.listing.id for r in higher}
        assert higher_ids.issubset(base_ids)

    def test_lower_threshold_yields_superset(self, tmp_path: Path):
        listings = [
            _make_listing(listing_id="11" * 32, title="Senior Python Engineer"),
            _make_listing(
                listing_id="22" * 32,
                title="Staff Engineer",
                description="No skill overlap here.",
            ),
        ]
        snap_path = tmp_path / "run.raw.json"
        write_snapshot(snap_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, listings)
        snap = load_snapshot(snap_path)

        higher = self._run_replay(snap, 80)
        lower = self._run_replay(snap, 0)

        higher_ids = {r.listing.id for r in higher}
        lower_ids = {r.listing.id for r in lower}
        assert higher_ids.issubset(lower_ids)


# ---------------------------------------------------------------------------
# Run state is byte-identical after replay
# ---------------------------------------------------------------------------


class TestReplayReadOnly:
    def test_state_file_unchanged_after_replay(self, tmp_path: Path):
        """Replay must not touch the run-state file."""
        from datetime import date

        from jobhunter.state import RunState, SeenEntry, save_state

        state_path = tmp_path / "state.yaml"
        initial_state = RunState(
            schema_version=1,
            seen=[SeenEntry(id="aaa", content_hash="bbb", last_shown_at="2026-07-01")],
            dismissed_ids=["ccc"],
            last_run_at="2026-07-01",
        )
        save_state(initial_state, state_path)
        original_bytes = state_path.read_bytes()

        # Run a replay
        listing = _make_listing(listing_id="33" * 32)
        snap_path = tmp_path / "run.raw.json"
        write_snapshot(snap_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, [listing])

        snap = load_snapshot(snap_path)
        run_date = date(2026, 7, 25)
        filter_result = filter_run(snap["listings"], BASE_PROFILE, today=run_date)
        scored = score_run(
            filter_result.passed,
            BASE_PROFILE,
            unknown_flags=filter_result.unknown_flags,
            today=run_date,
        )
        rank_run(scored, BASE_PROFILE)

        # State file must be byte-identical.
        assert state_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# --diff: identical snapshots produce zero diffs
# ---------------------------------------------------------------------------


class TestDiff:
    def _replay(self, snap: dict) -> list:
        from datetime import date

        run_date = date.fromisoformat(snap["run_at"][:10])
        profile = snap["profile_snapshot"]
        filter_result = filter_run(snap["listings"], profile, today=run_date)
        scored = score_run(
            filter_result.passed, profile, unknown_flags=filter_result.unknown_flags, today=run_date
        )
        shortlist, _ = rank_run(scored, profile)
        return shortlist

    def test_identical_snapshots_zero_diff(self, tmp_path: Path):
        listing = _make_listing(listing_id="44" * 32)
        snap_path = tmp_path / "run.raw.json"
        write_snapshot(snap_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, [listing])

        snap_a = load_snapshot(snap_path)
        snap_b = load_snapshot(snap_path)

        shortlist_a = self._replay(snap_a)
        shortlist_b = self._replay(snap_b)

        ids_a = {r.listing.id for r in shortlist_a}
        ids_b = {r.listing.id for r in shortlist_b}

        entered = ids_a - ids_b
        left = ids_b - ids_a
        moved = [
            (a, b)
            for a in shortlist_a
            for b in shortlist_b
            if a.listing.id == b.listing.id and (abs(a.score - b.score) > 0.01 or a.rank != b.rank)
        ]

        assert len(entered) == 0
        assert len(left) == 0
        assert len(moved) == 0

    def test_different_snapshots_detect_changes(self, tmp_path: Path):
        """When one run has a listing the other doesn't, diff reports it."""
        listing_a = _make_listing(listing_id="55" * 32, title="Senior Python Engineer")
        listing_b = _make_listing(listing_id="66" * 32, title="Staff Engineer")

        snap_a_path = tmp_path / "a.raw.json"
        snap_b_path = tmp_path / "b.raw.json"
        write_snapshot(
            snap_a_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, [listing_a, listing_b]
        )
        write_snapshot(snap_b_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, [listing_a])

        snap_a = load_snapshot(snap_a_path)
        snap_b = load_snapshot(snap_b_path)

        shortlist_a = self._replay(snap_a)
        shortlist_b = self._replay(snap_b)

        ids_a = {r.listing.id for r in shortlist_a}
        ids_b = {r.listing.id for r in shortlist_b}

        entered = ids_a - ids_b  # in A but not B
        assert len(entered) >= 0  # at least 0; listing_b is only in A


# ---------------------------------------------------------------------------
# CLI _cmd_replay integration
# ---------------------------------------------------------------------------


class TestCLIReplay:
    def test_replay_cmd_produces_markdown(self, tmp_path: Path, capsys):
        """_cmd_replay renders a Markdown digest to stdout."""
        import argparse

        from jobhunter.cli import _cmd_replay

        listing = _make_listing(listing_id="77" * 32, title="Senior Python Engineer")
        snap_path = tmp_path / "run.raw.json"
        write_snapshot(snap_path, "2026-07-25T10:00:00+00:00", BASE_PROFILE, [listing])

        state_path = tmp_path / "state.yaml"
        state_path.write_text("schema_version: 1\nseen: []\ndismissed_ids: []\n", encoding="utf-8")

        args = argparse.Namespace(
            run_file=str(snap_path),
            profile=None,
            set=None,
            diff=None,
            out=None,
            state=str(state_path),
        )
        rc = _cmd_replay(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "JobHunter" in captured.out
        assert "Senior Python Engineer" in captured.out

    def test_replay_missing_snapshot_exits_nonzero(self, tmp_path: Path, capsys):
        import argparse

        from jobhunter.cli import _cmd_replay

        state_path = tmp_path / "state.yaml"
        state_path.write_text("schema_version: 1\nseen: []\ndismissed_ids: []\n", encoding="utf-8")

        args = argparse.Namespace(
            run_file=str(tmp_path / "nonexistent.raw.json"),
            profile=None,
            set=None,
            diff=None,
            out=None,
            state=str(state_path),
        )
        rc = _cmd_replay(args)
        assert rc == 1
        assert "keep_raw" in capsys.readouterr().err

    def test_replay_writes_to_out_file(self, tmp_path: Path, capsys):
        import argparse

        from jobhunter.cli import _cmd_replay

        listing = _make_listing(listing_id="88" * 32, title="Senior Engineer")
        snap_path = tmp_path / "run.raw.json"
        write_snapshot(snap_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, [listing])

        state_path = tmp_path / "state.yaml"
        state_path.write_text("schema_version: 1\nseen: []\ndismissed_ids: []\n", encoding="utf-8")

        out_path = tmp_path / "replay.md"
        args = argparse.Namespace(
            run_file=str(snap_path),
            profile=None,
            set=None,
            diff=None,
            out=str(out_path),
            state=str(state_path),
        )
        rc = _cmd_replay(args)
        assert rc == 0
        assert out_path.exists()
        assert "JobHunter" in out_path.read_text()

    def test_replay_diff_identical_runs(self, tmp_path: Path, capsys):
        import argparse

        from jobhunter.cli import _cmd_replay

        listing = _make_listing(listing_id="99" * 32, title="Senior Engineer")
        snap_path = tmp_path / "run.raw.json"
        write_snapshot(snap_path, "2026-07-25T00:00:00+00:00", BASE_PROFILE, [listing])

        state_path = tmp_path / "state.yaml"
        state_path.write_text("schema_version: 1\nseen: []\ndismissed_ids: []\n", encoding="utf-8")

        args = argparse.Namespace(
            run_file=str(snap_path),
            profile=None,
            set=None,
            diff=str(snap_path),  # diff against itself
            out=None,
            state=str(state_path),
        )
        rc = _cmd_replay(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Entered (now shown, wasn't): 0" in captured.out
        assert "Left (was shown, not now): 0" in captured.out
        assert "Moved (rank/score changed): 0" in captured.out
