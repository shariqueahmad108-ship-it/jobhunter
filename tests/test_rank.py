# SPDX-License-Identifier: Apache-2.0
"""Tests for Stage 6 — Rank & threshold.

Covers: best-first ordering, recency tie-break, id tie-break (full determinism),
display_threshold splits shortlist vs. below_threshold count, rank assignment,
and edge cases (empty input, all-below-threshold, threshold of 0).

See: specs/02-functional-spec.md §Stage 6
     specs/03-data-model.md §ScoredResult
"""

from __future__ import annotations

from jobhunter.model import JobListing, Location, ScoredResult, Source
from jobhunter.rank import run as rank_run

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

BASE_PROFILE = {
    "output": {
        "display_threshold": 0,
        "max_shown": 25,
    }
}


def _listing(
    *,
    id: str = "aabbccdd" + "0" * 56,
    title: str = "Software Engineer",
    company: str = "Acme",
    posted_at: str | None = "2026-07-20",
    first_seen_at: str = "2026-07-20",
) -> JobListing:
    return JobListing(
        id=id,
        content_hash="hash",
        title=title,
        company=company,
        location=Location(raw="Remote", is_remote=True),
        description="A great role.",
        sources=[Source(name="fixture", url="https://example.com/1", source_id="1")],
        first_seen_at=first_seen_at,
        posted_at=posted_at,
    )


def _result(
    *,
    id: str = "aabbccdd" + "0" * 56,
    score: float = 50.0,
    posted_at: str | None = "2026-07-20",
    unknown_flags: list[str] | None = None,
) -> ScoredResult:
    return ScoredResult(
        listing=_listing(id=id, posted_at=posted_at),
        score=score,
        rank=0,
        unknown_flags=unknown_flags or [],
    )


# ---------------------------------------------------------------------------
# Ordering: best-first
# ---------------------------------------------------------------------------


class TestBestFirstOrdering:
    def test_higher_score_ranks_first(self):
        """The result with the higher score gets rank 1."""
        high = _result(id="a" * 64, score=80.0, posted_at="2026-07-10")
        low = _result(id="b" * 64, score=40.0, posted_at="2026-07-15")
        shortlist, below = rank_run([low, high], BASE_PROFILE)

        assert below == 0
        assert shortlist[0].rank == 1
        assert shortlist[0].listing.id == "a" * 64
        assert shortlist[1].rank == 2

    def test_three_results_ordered_by_score(self):
        """Three results come out sorted by descending score."""
        r1 = _result(id="1" * 64, score=90.0)
        r2 = _result(id="2" * 64, score=50.0)
        r3 = _result(id="3" * 64, score=70.0)
        shortlist, _ = rank_run([r1, r2, r3], BASE_PROFILE)

        scores = [r.score for r in shortlist]
        assert scores == sorted(scores, reverse=True)
        assert shortlist[0].score == 90.0

    def test_ranks_are_1_based_sequential(self):
        """Ranks assigned are 1, 2, 3, ... with no gaps."""
        results = [_result(id=str(i) * 64, score=float(100 - i * 10)) for i in range(5)]
        shortlist, _ = rank_run(results, BASE_PROFILE)
        assert [r.rank for r in shortlist] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Tie-breaking: recency then id
# ---------------------------------------------------------------------------


class TestTieBreaking:
    def test_equal_score_newer_first(self):
        """When two results share a score, the newer posting comes first."""
        older = _result(id="a" * 64, score=60.0, posted_at="2026-07-01")
        newer = _result(id="b" * 64, score=60.0, posted_at="2026-07-20")
        shortlist, _ = rank_run([older, newer], BASE_PROFILE)

        assert shortlist[0].listing.id == "b" * 64
        assert shortlist[0].rank == 1

    def test_equal_score_equal_date_id_tiebreak(self):
        """When score and date are equal, smaller id string comes first."""
        r_a = _result(id="a" * 64, score=60.0, posted_at="2026-07-15")
        r_z = _result(id="z" * 64, score=60.0, posted_at="2026-07-15")
        shortlist, _ = rank_run([r_z, r_a], BASE_PROFILE)

        assert shortlist[0].listing.id == "a" * 64

    def test_unknown_date_sorts_after_known_date(self):
        """A result with no date sorts after one with a known date (same score)."""
        known = _result(id="a" * 64, score=50.0, posted_at="2026-07-01")
        no_date = _result(id="b" * 64, score=50.0, posted_at=None)
        no_date.listing.first_seen_at = ""
        shortlist, _ = rank_run([no_date, known], BASE_PROFILE)

        assert shortlist[0].listing.id == "a" * 64

    def test_first_seen_at_used_when_posted_at_missing(self):
        """When posted_at is None, first_seen_at is used for recency tie-breaking."""
        r1 = _result(id="a" * 64, score=70.0, posted_at=None)
        r1.listing.first_seen_at = "2026-07-22"
        r2 = _result(id="b" * 64, score=70.0, posted_at=None)
        r2.listing.first_seen_at = "2026-07-10"
        shortlist, _ = rank_run([r2, r1], BASE_PROFILE)

        assert shortlist[0].listing.id == "a" * 64  # newer first_seen_at wins


# ---------------------------------------------------------------------------
# Display threshold
# ---------------------------------------------------------------------------


class TestDisplayThreshold:
    def test_zero_threshold_returns_all(self):
        """With display_threshold=0, all results appear in the shortlist."""
        results = [_result(id=str(i) * 64, score=float(i * 10)) for i in range(5)]
        profile = {**BASE_PROFILE, "output": {"display_threshold": 0}}
        shortlist, below = rank_run(results, profile)

        assert below == 0
        assert len(shortlist) == 5

    def test_threshold_hides_low_scorers(self):
        """Results below the threshold are excluded from shortlist and counted."""
        high = _result(id="a" * 64, score=80.0)
        mid = _result(id="b" * 64, score=50.0)
        low = _result(id="c" * 64, score=20.0)
        profile = {**BASE_PROFILE, "output": {"display_threshold": 40}}
        shortlist, below = rank_run([high, mid, low], profile)

        shortlist_ids = {r.listing.id for r in shortlist}
        assert "a" * 64 in shortlist_ids
        assert "b" * 64 in shortlist_ids
        assert "c" * 64 not in shortlist_ids
        assert below == 1

    def test_threshold_equal_to_score_is_included(self):
        """A result whose score equals the threshold is included (>=, not >)."""
        r = _result(id="a" * 64, score=50.0)
        profile = {**BASE_PROFILE, "output": {"display_threshold": 50}}
        shortlist, below = rank_run([r], profile)

        assert len(shortlist) == 1
        assert below == 0

    def test_all_below_threshold_empty_shortlist(self):
        """When every result is below the threshold, shortlist is empty."""
        results = [_result(id=str(i) * 64, score=10.0) for i in range(3)]
        profile = {**BASE_PROFILE, "output": {"display_threshold": 50}}
        shortlist, below = rank_run(results, profile)

        assert shortlist == []
        assert below == 3

    def test_ranks_include_below_threshold_positions(self):
        """Ranks are assigned across all results before threshold filtering."""
        high = _result(id="a" * 64, score=90.0, posted_at="2026-07-20")
        low = _result(id="b" * 64, score=10.0, posted_at="2026-07-20")
        profile = {**BASE_PROFILE, "output": {"display_threshold": 50}}
        shortlist, below = rank_run([low, high], profile)

        # high should be rank 1, low should be rank 2 (even though hidden)
        assert shortlist[0].rank == 1
        assert shortlist[0].listing.id == "a" * 64
        assert below == 1
        # Verify the low result actually got rank 2 (mutated in place)
        assert low.rank == 2

    def test_missing_threshold_defaults_to_zero(self):
        """When output.display_threshold is absent, defaults to 0 (show all)."""
        results = [_result(id="a" * 64, score=5.0)]
        profile: dict = {}  # no output key
        shortlist, below = rank_run(results, profile)

        assert len(shortlist) == 1
        assert below == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_input_returns_empty(self):
        """Empty scored list produces empty shortlist and zero below_threshold."""
        shortlist, below = rank_run([], BASE_PROFILE)
        assert shortlist == []
        assert below == 0

    def test_single_result_gets_rank_1(self):
        """A single result is always rank 1."""
        r = _result(id="a" * 64, score=42.0)
        shortlist, below = rank_run([r], BASE_PROFILE)
        assert len(shortlist) == 1
        assert shortlist[0].rank == 1
        assert below == 0

    def test_identical_scores_and_dates_stable_by_id(self):
        """Results with the same score and date are sorted deterministically by id."""
        ids = ["c" * 64, "a" * 64, "b" * 64]
        results = [_result(id=i, score=55.0, posted_at="2026-07-15") for i in ids]
        shortlist, _ = rank_run(results, BASE_PROFILE)
        returned_ids = [r.listing.id for r in shortlist]
        assert returned_ids == sorted(ids)

    def test_run_mutates_rank_field_in_place(self):
        """rank.run() assigns result.rank in place on the original ScoredResult objects."""
        r1 = _result(id="a" * 64, score=90.0)
        r2 = _result(id="b" * 64, score=50.0)
        rank_run([r1, r2], BASE_PROFILE)
        assert r1.rank == 1
        assert r2.rank == 2
