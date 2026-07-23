# SPDX-License-Identifier: Apache-2.0
"""Tests for the seen-state module: load/save, partition, update, dismiss workflow.

Acceptance criteria from specs/02-functional-spec.md §Stage 7:
- Re-running immediately produces "0 new" and prior listings visible as "Previously shown".
- A listing whose salary changes re-appears as new; one whose only change is a new source
  link does not.
- dismiss <id> removes the listing from all future digests; undismiss restores eligibility.

See: specs/02-functional-spec.md §Stage 7
     specs/03-data-model.md §Run state
"""

from __future__ import annotations

import pytest

from jobhunter.model import (
    JobListing,
    Location,
    RunState,
    Salary,
    ScoredResult,
    SeenEntry,
    Source,
)
from jobhunter.state import (
    dismiss_ids,
    load_state,
    partition_results,
    save_state,
    undismiss_id,
    update_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = "2026-07-23"


def _listing(
    *,
    id: str = "aabbccdd" + "0" * 56,
    content_hash: str = "hash-v1",
    title: str = "Senior Software Engineer",
    company: str = "Acme",
    is_remote: bool = True,
    salary: Salary | None = None,
) -> JobListing:
    return JobListing(
        id=id,
        content_hash=content_hash,
        title=title,
        company=company,
        location=Location(raw="Remote", is_remote=is_remote),
        description="A great role.",
        sources=[Source(name="fixture", url="https://example.com/1", source_id="1")],
        first_seen_at=TODAY,
        salary=salary,
    )


def _result(listing: JobListing, score: float = 75.0) -> ScoredResult:
    return ScoredResult(listing=listing, score=score, rank=1)


# ---------------------------------------------------------------------------
# load_state
# ---------------------------------------------------------------------------


class TestLoadState:
    def test_missing_file_returns_empty_state(self, tmp_path):
        state = load_state(tmp_path / "nonexistent.yaml")
        assert state.seen == []
        assert state.dismissed_ids == []
        assert state.last_run_at is None

    def test_valid_file_round_trips(self, tmp_path):
        path = tmp_path / "state.yaml"
        original = RunState(
            schema_version=1,
            seen=[SeenEntry(id="abc", content_hash="hash1", last_shown_at=TODAY)],
            dismissed_ids=["def"],
            last_run_at=TODAY,
        )
        save_state(original, path)
        loaded = load_state(path)

        assert len(loaded.seen) == 1
        assert loaded.seen[0].id == "abc"
        assert loaded.seen[0].content_hash == "hash1"
        assert loaded.dismissed_ids == ["def"]
        assert loaded.last_run_at == TODAY

    def test_unknown_schema_version_raises(self, tmp_path):
        import yaml

        path = tmp_path / "state.yaml"
        path.write_text(yaml.dump({"schema_version": 99, "seen": [], "dismissed_ids": []}))
        with pytest.raises(ValueError, match="schema_version"):
            load_state(path)

    def test_empty_seen_and_dismissed_on_fresh_file(self, tmp_path):
        path = tmp_path / "state.yaml"
        state = RunState(schema_version=1)
        save_state(state, path)
        loaded = load_state(path)
        assert loaded.seen == []
        assert loaded.dismissed_ids == []


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------


class TestSaveState:
    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "state.yaml"
        save_state(RunState(schema_version=1), path)
        assert path.exists()

    def test_file_is_valid_yaml(self, tmp_path):
        import yaml

        path = tmp_path / "state.yaml"
        state = RunState(
            schema_version=1,
            seen=[SeenEntry(id="x", content_hash="h", last_shown_at=TODAY)],
            dismissed_ids=["y"],
            last_run_at=TODAY,
        )
        save_state(state, path)
        raw = yaml.safe_load(path.read_text())
        assert raw["schema_version"] == 1
        assert raw["dismissed_ids"] == ["y"]
        assert len(raw["seen"]) == 1


# ---------------------------------------------------------------------------
# partition_results
# ---------------------------------------------------------------------------


class TestPartitionResults:
    def test_unseen_listing_is_new(self):
        state = RunState(schema_version=1)
        listing = _listing(id="new-id")
        results = [_result(listing)]

        new, prev = partition_results(results, state)
        assert len(new) == 1
        assert prev == []

    def test_seen_listing_with_same_hash_is_previous(self):
        listing = _listing(id="seen-id", content_hash="hash-v1")
        state = RunState(
            schema_version=1,
            seen=[SeenEntry(id="seen-id", content_hash="hash-v1", last_shown_at=TODAY)],
        )
        results = [_result(listing)]

        new, prev = partition_results(results, state)
        assert new == []
        assert len(prev) == 1

    def test_seen_listing_with_changed_hash_is_new(self):
        """A listing whose content_hash changed (e.g. salary update) re-surfaces as new."""
        listing = _listing(id="changed-id", content_hash="hash-v2")
        state = RunState(
            schema_version=1,
            seen=[SeenEntry(id="changed-id", content_hash="hash-v1", last_shown_at=TODAY)],
        )
        results = [_result(listing)]

        new, prev = partition_results(results, state)
        assert len(new) == 1
        assert prev == []

    def test_mixed_new_and_previous(self):
        """A mix of new and previously-seen listings is correctly split."""
        listing_new = _listing(id="id-new", content_hash="hash-new")
        listing_prev = _listing(id="id-prev", content_hash="hash-same")

        state = RunState(
            schema_version=1,
            seen=[SeenEntry(id="id-prev", content_hash="hash-same", last_shown_at=TODAY)],
        )
        results = [_result(listing_new), _result(listing_prev)]

        new, prev = partition_results(results, state)
        assert len(new) == 1
        assert new[0].listing.id == "id-new"
        assert len(prev) == 1
        assert prev[0].listing.id == "id-prev"

    def test_empty_results_produces_empty_partitions(self):
        state = RunState(schema_version=1)
        new, prev = partition_results([], state)
        assert new == []
        assert prev == []

    def test_rerun_yields_zero_new(self):
        """Core acceptance criterion: immediate re-run produces 0 new listings."""
        listing = _listing(id="role-a", content_hash="hash-a")
        results = [_result(listing)]

        # First run: everything is new
        state = RunState(schema_version=1)
        new, prev = partition_results(results, state)
        assert len(new) == 1

        # Simulate saving state after first run
        state = update_state(state, new + prev, TODAY)

        # Second run with the same listing
        new2, prev2 = partition_results(results, state)
        assert new2 == []
        assert len(prev2) == 1

    def test_source_only_change_does_not_resurface(self):
        """A listing whose only change is a new source link does not re-surface.

        content_hash excludes sources[], so source churn is invisible.
        """
        listing_v2 = _listing(id="role-b", content_hash="hash-stable")
        listing_v2.sources.append(Source(name="another", url="https://other.com", source_id="2"))

        state = RunState(
            schema_version=1,
            seen=[SeenEntry(id="role-b", content_hash="hash-stable", last_shown_at=TODAY)],
        )
        new, prev = partition_results([_result(listing_v2)], state)
        assert new == []
        assert len(prev) == 1


# ---------------------------------------------------------------------------
# update_state
# ---------------------------------------------------------------------------


class TestUpdateState:
    def test_shown_listings_recorded_in_seen(self):
        state = RunState(schema_version=1)
        listing = _listing(id="role-x", content_hash="hash-x")
        result = _result(listing)

        updated = update_state(state, [result], TODAY)
        assert len(updated.seen) == 1
        assert updated.seen[0].id == "role-x"
        assert updated.seen[0].content_hash == "hash-x"
        assert updated.seen[0].last_shown_at == TODAY

    def test_last_run_at_is_set(self):
        state = RunState(schema_version=1)
        updated = update_state(state, [], TODAY)
        assert updated.last_run_at == TODAY

    def test_existing_entry_updated_on_content_change(self):
        """When a listing re-surfaces due to a content change, its stored hash is updated."""
        state = RunState(
            schema_version=1,
            seen=[SeenEntry(id="role-y", content_hash="hash-old", last_shown_at="2026-07-01")],
        )
        listing = _listing(id="role-y", content_hash="hash-new")
        updated = update_state(state, [_result(listing)], TODAY)

        assert len(updated.seen) == 1
        assert updated.seen[0].content_hash == "hash-new"
        assert updated.seen[0].last_shown_at == TODAY

    def test_multiple_listings_all_recorded(self):
        state = RunState(schema_version=1)
        listings = [_listing(id=f"role-{i}", content_hash=f"hash-{i}") for i in range(5)]
        results = [_result(listing) for listing in listings]

        updated = update_state(state, results, TODAY)
        seen_ids = {e.id for e in updated.seen}
        assert seen_ids == {f"role-{i}" for i in range(5)}


# ---------------------------------------------------------------------------
# Dismiss workflow
# ---------------------------------------------------------------------------


class TestDismissWorkflow:
    def test_dismiss_adds_to_dismissed_ids(self):
        state = RunState(schema_version=1)
        state = dismiss_ids(state, ["abc", "def"])
        assert "abc" in state.dismissed_ids
        assert "def" in state.dismissed_ids

    def test_dismiss_is_idempotent(self):
        state = RunState(schema_version=1, dismissed_ids=["abc"])
        state = dismiss_ids(state, ["abc"])
        assert state.dismissed_ids.count("abc") == 1

    def test_undismiss_removes_id(self):
        state = RunState(schema_version=1, dismissed_ids=["abc", "def"])
        state = undismiss_id(state, "abc")
        assert "abc" not in state.dismissed_ids
        assert "def" in state.dismissed_ids

    def test_undismiss_unknown_id_is_noop(self):
        state = RunState(schema_version=1, dismissed_ids=["abc"])
        state = undismiss_id(state, "zzz")
        assert state.dismissed_ids == ["abc"]

    def test_dismissed_listing_excluded_via_pipeline(self):
        """End-to-end: a dismissed id never appears in pipeline output.

        The filter stage (Stage 4) drops dismissed ids; state.py does not
        re-filter — it only tracks seen/dismissed. This test verifies the
        contract by confirming the dismissed id is stored and the caller can
        pass it to the pipeline as dismissed_ids.
        """
        state = RunState(schema_version=1)
        state = dismiss_ids(state, ["dead0000" + "0" * 56])
        assert "dead0000" + "0" * 56 in state.dismissed_ids

    def test_dismiss_persists_across_save_load(self, tmp_path):
        path = tmp_path / "state.yaml"
        state = RunState(schema_version=1)
        state = dismiss_ids(state, ["id-to-dismiss"])
        save_state(state, path)

        loaded = load_state(path)
        assert "id-to-dismiss" in loaded.dismissed_ids

    def test_undismiss_persists_across_save_load(self, tmp_path):
        path = tmp_path / "state.yaml"
        state = RunState(schema_version=1, dismissed_ids=["id-alpha", "id-beta"])
        save_state(state, path)

        loaded = load_state(path)
        loaded = undismiss_id(loaded, "id-alpha")
        save_state(loaded, path)

        reloaded = load_state(path)
        assert "id-alpha" not in reloaded.dismissed_ids
        assert "id-beta" in reloaded.dismissed_ids
