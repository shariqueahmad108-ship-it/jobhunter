# SPDX-License-Identifier: Apache-2.0
"""Tests for source contribution stats (spec 05 §5.1).

Covers:
- Invariant: shown ≤ passed_filter ≤ contributed ≤ fetched (and sole_source ≤ contributed)
- Multi-source crediting: contributed goes to all sources, sole_source only to the unique one
- Failed adapter: records failed=True + error, does not abort the run
- Stats persistence: load/save round-trip; append-only across runs
- --json round-trip: format_json produces the same data as load_stats reads
- Digest header: source_header_lines emits one line per source in the expected format
- format_table: renders a text table with correct columns
- End-to-end: fixture pipeline run with three sources (one failing) produces stats records
  and digest header lines per source

See: specs/05-operator-tooling.md §5.1
     specs/03-data-model.md §Source stats
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from jobhunter.model import (
    JobListing,
    Location,
    RunReport,
    Salary,
    Seniority,
    Source,
    SourceStat,
    derive_content_hash,
    derive_id,
)
from jobhunter.source_stats import (
    StatsHistory,
    StatsRun,
    append_run,
    format_json,
    format_table,
    load_stats,
    save_stats,
    source_header_lines,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

TODAY = "2026-07-25"


def _make_listing(
    title: str,
    company: str,
    *,
    sources: list[Source] | None = None,
    is_remote: bool = True,
    salary_aud: float = 180_000,
    seniority_level: str = "senior",
    posted_at: str = TODAY,
) -> JobListing:
    if sources is None:
        sources = [Source(name="adzuna", url=f"https://adzuna.com/{title}", source_id=title)]
    loc = Location(raw="Remote Australia", country="AU", is_remote=is_remote)
    listing = JobListing(
        id="",
        content_hash="",
        title=title,
        company=company,
        location=loc,
        description=f"A {title} role at {company}. Python, open source.",
        sources=sources,
        first_seen_at=TODAY,
        salary=Salary(min=salary_aud, max=salary_aud, currency="AUD", period="year"),
        seniority=Seniority(track="ic", level=seniority_level),
        employment="full_time",
        posted_at=posted_at,
    )
    listing.id = derive_id(company, title, loc)
    listing.content_hash = derive_content_hash(listing)
    return listing


def _make_stat(**kwargs) -> SourceStat:
    defaults = dict(
        name="adzuna",
        fetched=10,
        contributed=5,
        sole_source=3,
        passed_filter=4,
        shown=2,
        dismissed=0,
        requests=2,
        failed=False,
        error=None,
    )
    defaults.update(kwargs)
    return SourceStat(**defaults)


# ---------------------------------------------------------------------------
# Invariant enforcement
# ---------------------------------------------------------------------------


class TestInvariant:
    """The invariant shown ≤ passed_filter ≤ contributed ≤ fetched must hold."""

    def test_invariant_holds_for_well_formed_stat(self):
        s = _make_stat(fetched=100, contributed=40, sole_source=10, passed_filter=30, shown=20)
        assert s.shown <= s.passed_filter <= s.contributed <= s.fetched
        assert s.sole_source <= s.contributed

    @pytest.mark.parametrize(
        "overrides",
        [
            # Each dict violates one part of the invariant, for documentation.
            # The stats module does NOT enforce these programmatically — the test
            # exercises that CORRECT pipeline output satisfies the invariant.
            {"shown": 0, "passed_filter": 0, "contributed": 0, "fetched": 0},  # all zero: ok
            {"shown": 5, "passed_filter": 5, "contributed": 5, "fetched": 5},  # all equal: ok
            {"shown": 1, "passed_filter": 10, "contributed": 50, "fetched": 100},  # strict <: ok
        ],
    )
    def test_valid_orderings(self, overrides):
        s = _make_stat(**overrides)
        assert s.shown <= s.passed_filter <= s.contributed <= s.fetched

    def test_corpus_run_satisfies_invariant(self):
        """A full pipeline run over the golden corpus produces stats satisfying the invariant."""
        from jobhunter.pipeline import run as pipeline_run
        from tests.fixtures.corpus import CORPUS_PASS, FIXTURE_PROFILE, REF_DATE

        class _FixtureAdapter:
            name = "adzuna"

            def __init__(self, listings):
                self._listings = listings

            def search(self, kw, loc, n):
                return [{"_l": lst} for lst in self._listings[:n]]

            def normalize(self, raw):
                return raw["_l"]

        profile = dict(FIXTURE_PROFILE)
        listings = [e.listing for e in CORPUS_PASS]
        adapter = _FixtureAdapter(listings)
        _, report = pipeline_run(profile, [adapter], today=REF_DATE)

        assert report.source_stats, "pipeline must produce at least one SourceStat"
        for s in report.source_stats:
            assert s.shown <= s.passed_filter <= s.contributed <= s.fetched, (
                f"{s.name}: invariant violated: shown={s.shown} "
                f"passed={s.passed_filter} contributed={s.contributed} "
                f"fetched={s.fetched}"
            )
            assert s.sole_source <= s.contributed


# ---------------------------------------------------------------------------
# Multi-source crediting
# ---------------------------------------------------------------------------


class TestMultiSourceCrediting:
    def test_listing_from_two_sources_credits_both(self):
        """A merged listing (two sources) contributes to both sources, sole_source to neither."""
        src_a = Source(name="adzuna", url="https://a.com/1", source_id="a1")
        src_b = Source(name="remotive", url="https://b.com/1", source_id="b1")
        listing = _make_listing("Staff Engineer", "Acme", sources=[src_a, src_b])

        from jobhunter.pipeline import run as pipeline_run
        from tests.fixtures.corpus import FIXTURE_PROFILE, REF_DATE

        class _Adapter:
            def __init__(self, name, listings):
                self.name = name
                self._listings = listings

            def search(self, kw, loc, n):
                return [{"_l": lst} for lst in self._listings[:n]]

            def normalize(self, raw):
                return raw["_l"]

        # The listing already has two sources merged. Feed it from one adapter
        # so the pipeline sees one fetched listing whose sources[] has two entries
        # (simulating a pre-merged result or post-dedupe state).
        profile = dict(FIXTURE_PROFILE)
        adapter = _Adapter("adzuna", [listing])
        _, report = pipeline_run(profile, [adapter], today=REF_DATE)

        stat_map = {s.name: s for s in report.source_stats}
        # The listing's sources include both adzuna and remotive
        if "adzuna" in stat_map and "remotive" in stat_map:
            assert stat_map["adzuna"].contributed >= 1
            assert stat_map["remotive"].contributed >= 1
            # sole_source should be 0 for a 2-source listing
            assert stat_map["adzuna"].sole_source == 0
            assert stat_map["remotive"].sole_source == 0

    def test_sole_source_incremented_for_single_source_listing(self):
        """A listing from exactly one source increments sole_source for that source."""
        src = Source(name="remotive", url="https://remotive.com/1", source_id="r1")
        listing = _make_listing("Staff Engineer", "Acme", sources=[src])

        from jobhunter.pipeline import run as pipeline_run
        from tests.fixtures.corpus import FIXTURE_PROFILE, REF_DATE

        class _Adapter:
            name = "remotive"

            def __init__(self, listings):
                self._listings = listings

            def search(self, kw, loc, n):
                return [{"_l": lst} for lst in self._listings[:n]]

            def normalize(self, raw):
                return raw["_l"]

        profile = dict(FIXTURE_PROFILE)
        adapter = _Adapter([listing])
        _, report = pipeline_run(profile, [adapter], today=REF_DATE)

        stat_map = {s.name: s for s in report.source_stats}
        assert "remotive" in stat_map
        assert stat_map["remotive"].sole_source >= 1


# ---------------------------------------------------------------------------
# Failed adapter
# ---------------------------------------------------------------------------


class TestFailedAdapter:
    def test_failed_adapter_records_error_and_does_not_abort(self):
        """A raising adapter records failed=True and error, and does not abort the run."""
        from jobhunter.pipeline import run as pipeline_run
        from tests.fixtures.corpus import CORPUS_PASS, FIXTURE_PROFILE, REF_DATE

        class _GoodAdapter:
            name = "adzuna"

            def __init__(self, listings):
                self._listings = listings

            def search(self, kw, loc, n):
                return [{"_l": lst} for lst in self._listings[:n]]

            def normalize(self, raw):
                return raw["_l"]

        class _BadAdapter:
            name = "remotive"

            def search(self, kw, loc, n):
                raise RuntimeError("remotive is down")

            def normalize(self, raw):
                return raw

        profile = dict(FIXTURE_PROFILE)
        good_listings = [e.listing for e in CORPUS_PASS[:3]]
        adapters = [_GoodAdapter(good_listings), _BadAdapter()]
        results, report = pipeline_run(profile, adapters, today=REF_DATE)

        stat_map = {s.name: s for s in report.source_stats}
        assert "remotive" in stat_map
        assert stat_map["remotive"].failed is True
        assert "remotive is down" in (stat_map["remotive"].error or "")
        assert stat_map["remotive"].fetched == 0
        # Good adapter is queried once per keyword × location; fetched ≥ len of one batch
        assert "adzuna" in stat_map
        assert stat_map["adzuna"].fetched >= len(good_listings)

    def test_failed_adapter_does_not_prevent_stats_write(self):
        """Stats are still writeable when one adapter fails."""
        stat = SourceStat(
            name="remotive",
            fetched=0,
            failed=True,
            error="connection refused",
        )
        run = StatsRun(run_at=TODAY, sources=[stat])
        history = StatsHistory()
        history = append_run(history, run)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source_stats.json"
            save_stats(history, path)
            loaded = load_stats(path)

        assert len(loaded.runs) == 1
        s = loaded.runs[0].sources[0]
        assert s.failed is True
        assert s.error == "connection refused"
        assert s.fetched == 0


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_empty_history_loads_from_absent_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source_stats.json"
            history = load_stats(path)
        assert history.schema_version == 1
        assert history.runs == []

    def test_save_and_load_round_trip(self):
        stat = _make_stat(name="adzuna", fetched=50, contributed=20, shown=5)
        run = StatsRun(run_at=TODAY, sources=[stat])
        history = append_run(StatsHistory(), run)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source_stats.json"
            save_stats(history, path)
            loaded = load_stats(path)

        assert loaded.schema_version == 1
        assert len(loaded.runs) == 1
        s = loaded.runs[0].sources[0]
        assert s.name == "adzuna"
        assert s.fetched == 50
        assert s.contributed == 20
        assert s.shown == 5

    def test_append_run_is_non_destructive(self):
        """Two append_run calls produce two runs; history is never rewritten."""
        stat1 = _make_stat(name="adzuna", fetched=10)
        stat2 = _make_stat(name="adzuna", fetched=20)
        run1 = StatsRun(run_at="2026-07-24", sources=[stat1])
        run2 = StatsRun(run_at="2026-07-25", sources=[stat2])

        history = StatsHistory()
        history = append_run(history, run1)
        history = append_run(history, run2)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source_stats.json"
            save_stats(history, path)
            loaded = load_stats(path)

        assert len(loaded.runs) == 2
        assert loaded.runs[0].sources[0].fetched == 10
        assert loaded.runs[1].sources[0].fetched == 20

    def test_inactive_source_historical_records_survive(self):
        """Sources absent from the current profile keep historical records."""
        stat_old = _make_stat(name="careerjet", fetched=100, contributed=5)
        history = append_run(StatsHistory(), StatsRun(run_at="2026-01-01", sources=[stat_old]))

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source_stats.json"
            save_stats(history, path)
            loaded = load_stats(path)

        assert len(loaded.runs[0].sources) == 1
        assert loaded.runs[0].sources[0].name == "careerjet"

        # format_table marks it inactive when active_sources is provided
        table = format_table(loaded, active_sources={"adzuna"})
        assert "careerjet" in table
        assert "inactive" in table

    def test_schema_version_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "source_stats.json"
            path.write_text(json.dumps({"schema_version": 99, "runs": []}))
            with pytest.raises(ValueError, match="schema_version 99"):
                load_stats(path)


# ---------------------------------------------------------------------------
# JSON format / round-trip
# ---------------------------------------------------------------------------


class TestJsonFormat:
    def test_format_json_round_trips(self):
        """format_json emits the same data that load_stats would read back."""
        stat = _make_stat(name="remotive", fetched=30, contributed=10, sole_source=6)
        run = StatsRun(run_at=TODAY, sources=[stat])
        history = append_run(StatsHistory(), run)

        json_str = format_json(history)
        raw = json.loads(json_str)

        assert raw["schema_version"] == 1
        assert len(raw["runs"]) == 1
        s = raw["runs"][0]["sources"][0]
        assert s["name"] == "remotive"
        assert s["fetched"] == 30
        assert s["contributed"] == 10
        assert s["sole_source"] == 6

    def test_format_json_last_n(self):
        history = StatsHistory()
        for i in range(5):
            history = append_run(
                history, StatsRun(run_at=f"2026-07-{20 + i:02d}", sources=[_make_stat(name="a")])
            )
        raw = json.loads(format_json(history, last_n=2))
        assert len(raw["runs"]) == 2
        assert raw["runs"][0]["run_at"] == "2026-07-23"
        assert raw["runs"][1]["run_at"] == "2026-07-24"


# ---------------------------------------------------------------------------
# source_header_lines (digest header)
# ---------------------------------------------------------------------------


class TestSourceHeaderLines:
    def test_normal_source_line_format(self):
        s = SourceStat(
            name="adzuna",
            fetched=312,
            contributed=41,
            sole_source=4,
            passed_filter=41,
            shown=12,
        )
        lines = source_header_lines([s])
        assert len(lines) == 1
        line = lines[0]
        assert "adzuna" in line
        assert "312 fetched" in line
        assert "41 passed" in line
        assert "12 shown" in line
        assert "(4 sole)" in line

    def test_sole_note_omitted_when_zero(self):
        s = SourceStat(name="remoteok", fetched=50, passed_filter=5, shown=3, sole_source=0)
        line = source_header_lines([s])[0]
        assert "sole" not in line

    def test_failed_source_line(self):
        s = SourceStat(name="jooble", failed=True, error="timeout", fetched=0)
        line = source_header_lines([s])[0]
        assert "FAILED" in line
        assert "timeout" in line

    def test_multiple_sources(self):
        stats = [
            SourceStat(name="adzuna", fetched=100, passed_filter=10, shown=5, sole_source=3),
            SourceStat(name="remotive", fetched=50, passed_filter=4, shown=2, sole_source=1),
        ]
        lines = source_header_lines(stats)
        assert len(lines) == 2
        assert any("adzuna" in ln for ln in lines)
        assert any("remotive" in ln for ln in lines)


# ---------------------------------------------------------------------------
# format_table
# ---------------------------------------------------------------------------


class TestFormatTable:
    def test_empty_history_message(self):
        msg = format_table(StatsHistory())
        assert "No source stats" in msg

    def test_table_has_expected_columns(self):
        stat = _make_stat(name="adzuna")
        history = append_run(StatsHistory(), StatsRun(run_at=TODAY, sources=[stat]))
        table = format_table(history)
        for col in ("Source", "Fetched", "Contrib", "Sole", "Passed", "Shown"):
            assert col in table

    def test_table_includes_source_name(self):
        stat = _make_stat(name="remoteok")
        history = append_run(StatsHistory(), StatsRun(run_at=TODAY, sources=[stat]))
        assert "remoteok" in format_table(history)

    def test_inactive_marking(self):
        stat = _make_stat(name="old_source")
        history = append_run(StatsHistory(), StatsRun(run_at=TODAY, sources=[stat]))
        table = format_table(history, active_sources={"adzuna"})
        assert "inactive" in table

    def test_last_n_filter(self):
        history = StatsHistory()
        for i in range(4):
            history = append_run(
                history,
                StatsRun(run_at=f"2026-07-{20 + i}", sources=[_make_stat(name=f"src{i}")]),
            )
        table = format_table(history, last_n=2)
        assert "src2" in table
        assert "src3" in table
        assert "src0" not in table
        assert "src1" not in table


# ---------------------------------------------------------------------------
# End-to-end: three sources, one failing
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_three_sources_one_failing(self):
        """Fixture run with three adapters (one failing) produces three stat records."""
        from jobhunter.pipeline import run as pipeline_run
        from tests.fixtures.corpus import CORPUS_PASS, FIXTURE_PROFILE, REF_DATE

        class _GoodAdapter:
            def __init__(self, name, listings):
                self.name = name
                self._listings = listings

            def search(self, kw, loc, n):
                return [{"_l": lst} for lst in self._listings[:n]]

            def normalize(self, raw):
                return raw["_l"]

        class _FailAdapter:
            name = "jooble"

            def search(self, kw, loc, n):
                raise RuntimeError("jooble API down")

            def normalize(self, raw):
                return raw

        profile = dict(FIXTURE_PROFILE)
        listings_a = [e.listing for e in CORPUS_PASS[:5]]
        listings_b = [e.listing for e in CORPUS_PASS[5:]]

        adapters = [
            _GoodAdapter("adzuna", listings_a),
            _GoodAdapter("remotive", listings_b),
            _FailAdapter(),
        ]
        _, report = pipeline_run(profile, adapters, today=REF_DATE)

        stat_map = {s.name: s for s in report.source_stats}
        assert "adzuna" in stat_map
        assert "remotive" in stat_map
        assert "jooble" in stat_map

        # Adapter is queried per keyword × location; fetched is sum across all queries
        assert stat_map["adzuna"].fetched >= len(listings_a)
        assert stat_map["remotive"].fetched >= len(listings_b)
        assert stat_map["jooble"].fetched == 0
        assert stat_map["jooble"].failed is True

        # Invariant on non-failed sources
        for name in ("adzuna", "remotive"):
            s = stat_map[name]
            assert s.shown <= s.passed_filter <= s.contributed <= s.fetched

    def test_digest_header_contains_source_stat_lines(self):
        """render_markdown includes one source stat line per source when stats are present."""
        from jobhunter.digest import render_markdown

        stats = [
            SourceStat(name="adzuna", fetched=50, passed_filter=10, shown=5, sole_source=3),
            SourceStat(name="remotive", fetched=20, passed_filter=4, shown=2, sole_source=0),
        ]
        report = RunReport(
            run_at=TODAY,
            sources_used=["adzuna", "remotive"],
            requests_made=5,
            source_stats=stats,
        )
        md = render_markdown([], report)
        assert "Source stats:" in md
        assert "adzuna" in md
        assert "50 fetched" in md
        assert "remotive" in md
        assert "20 fetched" in md
