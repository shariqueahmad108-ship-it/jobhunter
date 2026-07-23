# SPDX-License-Identifier: Apache-2.0
"""Acceptance tests against the golden fixture corpus.

Every pipeline stage is verified against the corpus defined in
tests/fixtures/corpus.py.  Each test uses REF_DATE so the suite is
deterministic regardless of when it runs.

Coverage:
  Stage 2 (normalize) — HTML stripping, empty-string canonicalization,
                         content_hash regeneration, seniority inference cases
  Stage 3 (dedupe)    — ≥10 merge pairs (id-based + URL-based)
                        ≥10 no-merge pairs
  Stage 4 (filter)    — 25 listings: expected pass/fail, drop reason,
                        unknown_flags for every corpus entry
  Stage 5 (score)     — score within expected ±3 range for 15 passing listings

Validation: python3 -m pytest tests/test_corpus.py -q

See: specs/04-technical-plan.md §Fixtures
"""

from __future__ import annotations

import pytest

from jobhunter import dedupe, filter, normalize, score
from jobhunter.model import (
    JobListing,
    Location,
    Source,
    derive_content_hash,
    derive_id,
    infer_seniority,
)
from tests.fixtures.corpus import (
    CORPUS,
    CORPUS_EDGE,
    CORPUS_PASS,
    FIXTURE_PROFILE,
    MERGE_PAIRS,
    NO_MERGE_PAIRS,
    REF_DATE,
    CorpusEntry,
    DedupePair,
)

# ---------------------------------------------------------------------------
# Stage 2 — Normalize
# ---------------------------------------------------------------------------


class TestNormalize:
    """Stage 2 acceptance tests: HTML stripping, canonicalization, hash."""

    def _raw(self, description: str, **kwargs) -> JobListing:
        loc = Location(raw="Remote Australia", country="AU", is_remote=True)
        src = Source(name="adzuna", url="https://adzuna.com/1", source_id="n1")
        listing = JobListing(
            id="",
            content_hash="",
            title="Community Director",
            company="Acme",
            location=loc,
            description=description,
            sources=[src],
            first_seen_at="2026-07-23",
            **kwargs,
        )
        listing.id = derive_id("Acme", "Community Director", loc)
        listing.content_hash = derive_content_hash(listing)
        return listing

    def test_html_stripped_from_description(self):
        raw = self._raw("<p><strong>Head of Community</strong> at Acme.</p>")
        [result] = normalize.run([raw])
        assert "<" not in result.description
        assert "Head of Community at Acme." in result.description

    def test_html_entities_unescaped(self):
        raw = self._raw("Open source &amp; community governance.")
        [result] = normalize.run([raw])
        assert "&amp;" not in result.description
        assert "Open source & community governance." in result.description

    def test_content_hash_updates_when_html_stripped(self):
        raw = self._raw("<b>Community role</b>")
        old_hash = raw.content_hash
        [result] = normalize.run([raw])
        assert result.content_hash != old_hash

    def test_content_hash_unchanged_for_clean_description(self):
        raw = self._raw("A plain text description with no HTML.")
        old_hash = raw.content_hash
        [result] = normalize.run([raw])
        assert result.content_hash == old_hash

    def test_empty_employment_canonicalized_to_none(self):
        listing = self._raw("A role.", employment="")
        [result] = normalize.run([listing])
        assert result.employment is None

    def test_empty_posted_at_canonicalized_to_none(self):
        listing = self._raw("A role.", posted_at="")
        [result] = normalize.run([listing])
        assert result.posted_at is None

    def test_nonempty_employment_preserved(self):
        listing = self._raw("A role.", employment="contract")
        [result] = normalize.run([listing])
        assert result.employment == "contract"

    def test_multiple_listings_processed_independently(self):
        a = self._raw("<b>Role A</b>")
        b = self._raw("Plain role B.")
        results = normalize.run([a, b])
        assert len(results) == 2
        assert "Role A" in results[0].description
        assert "<" not in results[0].description
        assert results[1].description == "Plain role B."

    # Seniority inference — domain-specific cases not covered in test_normalize.py

    def test_head_of_infers_director(self):
        s = infer_seniority("Head of Community")
        assert s is not None
        assert s.track == "management"
        assert s.level == "director"

    def test_senior_community_manager_infers_management_manager(self):
        """'Senior' in title doesn't fire when 'Manager' also present —
        management rules are checked first and \bmanager\b wins."""
        s = infer_seniority("Senior Community Manager")
        assert s is not None
        assert s.track == "management"
        assert s.level == "manager"

    def test_senior_manager_infers_senior_manager(self):
        s = infer_seniority("Senior Manager, Developer Community")
        assert s is not None
        assert s.track == "management"
        assert s.level == "senior_manager"

    def test_vp_infers_vp(self):
        s = infer_seniority("VP of Developer Experience")
        assert s is not None
        assert s.track == "management"
        assert s.level == "vp"

    def test_ospo_lead_infers_staff_ic(self):
        s = infer_seniority("OSPO Lead")
        assert s is not None
        assert s.track == "ic"
        assert s.level == "staff"

    def test_community_strategist_returns_none(self):
        """Titles with no seniority keyword return None."""
        s = infer_seniority("Community Strategist")
        assert s is None

    def test_community_coordinator_returns_none(self):
        s = infer_seniority("Community Coordinator")
        assert s is None

    def test_mid_level_devrel_infers_mid(self):
        s = infer_seniority("Mid-Level DevRel Engineer")
        assert s is not None
        assert s.track == "ic"
        assert s.level == "mid"

    def test_intern_community_manager_infers_manager_not_intern(self):
        """Management rules are checked before IC rules, so 'Manager' in the title
        fires first even when 'Intern' is also present."""
        s = infer_seniority("Intern Community Manager")
        assert s is not None
        assert s.track == "management"
        assert s.level == "manager"

    def test_community_intern_infers_ic_intern(self):
        """Without 'Manager', 'Intern' correctly fires the IC/intern rule."""
        s = infer_seniority("Community Intern")
        assert s is not None
        assert s.track == "ic"
        assert s.level == "intern"

    def test_junior_developer_advocate_infers_junior(self):
        s = infer_seniority("Junior Developer Advocate")
        assert s is not None
        assert s.track == "ic"
        assert s.level == "junior"

    def test_sr_developer_advocate_infers_senior(self):
        s = infer_seniority("Sr. Developer Advocate")
        assert s is not None
        assert s.track == "ic"
        assert s.level == "senior"

    def test_php_community_developer_returns_none_no_seniority(self):
        s = infer_seniority("PHP Community Developer")
        assert s is None


# ---------------------------------------------------------------------------
# Stage 3 — Dedupe
# ---------------------------------------------------------------------------


class TestDedupeMergePairs:
    """Each MERGE_PAIRS entry should collapse to exactly 1 listing."""

    @pytest.mark.parametrize("pair", MERGE_PAIRS, ids=[p.case for p in MERGE_PAIRS])
    def test_merge(self, pair: DedupePair):
        result = dedupe.run([pair.a, pair.b])
        assert len(result) == 1, f"{pair.case}: expected 1 merged listing but got {len(result)}"

    def test_merge_preserves_both_sources(self):
        """Merged listing must carry sources from both original listings."""
        pair = MERGE_PAIRS[0]  # MP01: same cross-post, two different source boards
        [merged] = dedupe.run([pair.a, pair.b])
        source_names = {s.name for s in merged.sources}
        assert "adzuna" in source_names
        assert "greenhouse" in source_names

    def test_merge_keeps_earliest_first_seen_at(self):
        """MP07: two listings with different first_seen_at → keep the earlier one."""
        pair = next(p for p in MERGE_PAIRS if p.case.startswith("MP07"))
        [merged] = dedupe.run([pair.a, pair.b])
        # The earlier first_seen_at is whichever of a/b is older
        expected_earliest = min(pair.a.first_seen_at, pair.b.first_seen_at)
        assert merged.first_seen_at == expected_earliest

    def test_merge_recomputes_content_hash(self):
        """Content hash of the merged listing equals derive_content_hash(merged)."""
        pair = MERGE_PAIRS[0]
        [merged] = dedupe.run([pair.a, pair.b])
        assert merged.content_hash == derive_content_hash(merged)


class TestDedupeNoMergePairs:
    """Each NO_MERGE_PAIRS entry must remain as 2 distinct listings."""

    @pytest.mark.parametrize("pair", NO_MERGE_PAIRS, ids=[p.case for p in NO_MERGE_PAIRS])
    def test_no_merge(self, pair: DedupePair):
        result = dedupe.run([pair.a, pair.b])
        assert len(result) == 2, f"{pair.case}: expected 2 listings but got {len(result)}"


class TestDedupeEdgeCases:
    def test_empty_input(self):
        assert dedupe.run([]) == []

    def test_single_listing_passes_through(self):
        listing = CORPUS_PASS[0].listing
        result = dedupe.run([listing])
        assert len(result) == 1
        assert result[0].id == listing.id


# ---------------------------------------------------------------------------
# Stage 4 — Hard filter
# ---------------------------------------------------------------------------


def _run_filter(listings: list[JobListing]) -> filter.FilterResult:
    return filter.run(
        listings,
        profile=FIXTURE_PROFILE,
        dismissed_ids=None,
        today=REF_DATE,
    )


class TestFilterCorpus:
    """Every corpus entry must produce the expected filter verdict."""

    @pytest.mark.parametrize("entry", CORPUS, ids=[e.case for e in CORPUS])
    def test_filter_verdict(self, entry: CorpusEntry):
        if entry.filter_pass is None:
            pytest.skip("filter verdict not specified for this entry")

        result = _run_filter([entry.listing])

        if entry.filter_pass:
            assert len(result.passed) == 1, f"{entry.case}: expected PASS but listing was dropped"
        else:
            assert len(result.passed) == 0, f"{entry.case}: expected DROP but listing passed"

    @pytest.mark.parametrize(
        "entry",
        [e for e in CORPUS if e.filter_pass is True],
        ids=[e.case for e in CORPUS if e.filter_pass is True],
    )
    def test_filter_pass_unknown_flags(self, entry: CorpusEntry):
        """Passed listings carry expected unknown_flags (subset check)."""
        result = _run_filter([entry.listing])
        assert len(result.passed) == 1
        actual_flags = result.unknown_flags.get(entry.listing.id, [])
        for expected_flag in entry.filter_unknown_flags:
            assert expected_flag in actual_flags, (
                f"{entry.case}: expected flag '{expected_flag}' not in actual flags {actual_flags}"
            )

    @pytest.mark.parametrize(
        "entry",
        [e for e in CORPUS if e.filter_pass is False],
        ids=[e.case for e in CORPUS if e.filter_pass is False],
    )
    def test_filter_drop_reason(self, entry: CorpusEntry):
        """Dropped listings increment the expected tally counter."""
        if entry.filter_drop_reason is None:
            pytest.skip("drop reason not specified")

        result = _run_filter([entry.listing])
        tally = result.tally

        reason_to_counter = {
            "location": tally.by_location,
            "seniority": tally.by_seniority,
            "salary": tally.by_salary,
            "employment": tally.by_employment,
            "keyword": tally.by_keyword,
            "required": tally.by_required,
            "age": tally.by_age,
            "dismissed": tally.dismissed,
        }
        actual_count = reason_to_counter.get(entry.filter_drop_reason, 0)
        assert actual_count == 1, (
            f"{entry.case}: expected drop by '{entry.filter_drop_reason}' but tally={tally}"
        )


class TestFilterSpecificCases:
    """Targeted tests for noteworthy filter behaviours in the corpus."""

    def test_c02_remote_scope_unclear_flag(self):
        """Bare Remote with no country → 'remote scope unclear' flag (not a drop)."""
        entry = next(e for e in CORPUS if e.case.startswith("C02"))
        result = _run_filter([entry.listing])
        assert len(result.passed) == 1
        flags = result.unknown_flags.get(entry.listing.id, [])
        assert "remote scope unclear" in flags

    def test_c06_level_unclear_flag(self):
        """Unknown seniority → 'level unclear' flag and kept (unknown-data policy)."""
        entry = next(e for e in CORPUS if e.case.startswith("C06"))
        result = _run_filter([entry.listing])
        assert len(result.passed) == 1
        flags = result.unknown_flags.get(entry.listing.id, [])
        assert "level unclear" in flags

    def test_c07_nil_salary_kept(self):
        """No salary → keep_unknown_salary=True → passes."""
        entry = next(e for e in CORPUS if e.case.startswith("C07"))
        result = _run_filter([entry.listing])
        assert len(result.passed) == 1

    def test_c11_sydney_based_remote_dropped(self):
        """Remote flag does NOT override Sydney exclude_locations."""
        entry = next(e for e in CORPUS if e.case.startswith("C11"))
        result = _run_filter([entry.listing])
        assert result.tally.by_location == 1

    def test_c14_seniority_drops_before_employment(self):
        """Intern seniority check fires before employment check; both would drop it."""
        entry = next(e for e in CORPUS if e.case.startswith("C14"))
        result = _run_filter([entry.listing])
        assert result.tally.by_seniority == 1
        assert result.tally.by_employment == 0

    def test_c20_contract_not_excluded_by_employment(self):
        """Contract employment is NOT in exclude_employment; listing fails on salary."""
        entry = next(e for e in CORPUS if e.case.startswith("C20"))
        result = _run_filter([entry.listing])
        assert result.tally.by_salary == 1
        assert result.tally.by_employment == 0

    def test_c21_day_rate_annualizes_above_floor(self):
        """AUD 1200/day × 260 = AUD 312k > salary_floor=160k."""
        entry = next(e for e in CORPUS if e.case.startswith("C21"))
        result = _run_filter([entry.listing])
        assert len(result.passed) == 1

    def test_c22_eur_no_rate_kept_unknown(self):
        """EUR salary with no FX rate → unknown salary → kept by keep_unknown_salary."""
        entry = next(e for e in CORPUS if e.case.startswith("C22"))
        result = _run_filter([entry.listing])
        assert len(result.passed) == 1

    def test_c23_competitive_salary_kept_unknown(self):
        """'competitive' is unparseable → unknown → kept."""
        entry = next(e for e in CORPUS if e.case.startswith("C23"))
        result = _run_filter([entry.listing])
        assert len(result.passed) == 1

    def test_c24_mid_level_at_seniority_boundary_passes(self):
        """ic/mid == min=mid → passes (boundary included)."""
        entry = next(e for e in CORPUS if e.case.startswith("C24"))
        result = _run_filter([entry.listing])
        assert len(result.passed) == 1

    def test_dismissed_listing_dropped(self):
        """A listing in dismissed_ids is always dropped regardless of other fields."""
        entry = CORPUS_PASS[0]
        result = filter.run(
            [entry.listing],
            profile=FIXTURE_PROFILE,
            dismissed_ids={entry.listing.id},
            today=REF_DATE,
        )
        assert result.tally.dismissed == 1
        assert len(result.passed) == 0

    def test_filter_tally_counts_correctly_for_batch(self):
        """Running the full CORPUS gives the right aggregate tally."""
        all_listings = [e.listing for e in CORPUS]
        result = _run_filter(all_listings)
        expected_pass = sum(1 for e in CORPUS if e.filter_pass is True)
        assert len(result.passed) == expected_pass


# ---------------------------------------------------------------------------
# Stage 5 — Score
# ---------------------------------------------------------------------------


def _score_entry(entry: CorpusEntry) -> score.ScoredResult:
    return score.score(entry.listing, FIXTURE_PROFILE, today=REF_DATE)


class TestScoreCorpus:
    """Score every passing corpus entry and verify it falls within ±3 of the
    hand-computed expected range."""

    @pytest.mark.parametrize(
        "entry",
        [e for e in CORPUS if e.filter_pass is True and e.expected_score_min is not None],
        ids=[e.case for e in CORPUS if e.filter_pass is True and e.expected_score_min is not None],
    )
    def test_score_in_range(self, entry: CorpusEntry):
        result = _score_entry(entry)
        assert entry.expected_score_min <= result.score <= entry.expected_score_max, (
            f"{entry.case}: score={result.score:.2f} not in "
            f"[{entry.expected_score_min}, {entry.expected_score_max}]"
        )

    def test_score_preferred_company_higher_than_non_preferred(self):
        """C01 (Atlassian=preferred) should outscore C03 (HashiCorp=not preferred)
        given equal-or-better salary, same location class, same seniority."""
        c01 = next(e for e in CORPUS if e.case.startswith("C01"))
        c03 = next(e for e in CORPUS if e.case.startswith("C03"))
        r01 = _score_entry(c01)
        r03 = _score_entry(c03)
        assert r01.score > r03.score, (
            f"preferred company should score higher: C01={r01.score}, C03={r03.score}"
        )

    def test_score_higher_salary_scores_higher_ceteris_paribus(self):
        """C04 (AUD 210k) should outscore C09 (AUD 180k) — same track, similar age,
        bare Remote for both."""
        c04 = next(e for e in CORPUS if e.case.startswith("C04"))
        c09 = next(e for e in CORPUS if e.case.startswith("C09"))
        r04 = _score_entry(c04)
        r09 = _score_entry(c09)
        assert r04.score > r09.score

    def test_score_remote_au_beats_bare_remote(self):
        """C03 (Remote Australia, country=AU) should have higher location_fit than
        C07 (bare Remote, no country)."""
        c03 = next(e for e in CORPUS if e.case.startswith("C03"))
        c07 = next(e for e in CORPUS if e.case.startswith("C07"))
        r03 = _score_entry(c03)
        r07 = _score_entry(c07)
        c03_loc = next(c for c in r03.components if c.name == "location_fit")
        c07_loc = next(c for c in r07.components if c.name == "location_fit")
        assert c03_loc.sub > c07_loc.sub, (
            f"Remote-AU sub={c03_loc.sub} should be > bare-Remote sub={c07_loc.sub}"
        )

    def test_score_exact_seniority_beats_one_step_off(self):
        """C01 (director=director, exact) seniority_fit should beat C05 (manager vs director)."""
        c01 = next(e for e in CORPUS if e.case.startswith("C01"))
        c05 = next(e for e in CORPUS if e.case.startswith("C05"))
        r01 = _score_entry(c01)
        r05 = _score_entry(c05)
        c01_sen = next(c for c in r01.components if c.name == "seniority_fit")
        c05_sen = next(c for c in r05.components if c.name == "seniority_fit")
        assert c01_sen.sub > c05_sen.sub

    def test_score_day_rate_salary_compensates_at_max(self):
        """C21 day-rate AUD 1200/day annualizes to 312k → compensation sub=1.0 (capped)."""
        c21 = next(e for e in CORPUS if e.case.startswith("C21"))
        result = _score_entry(c21)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == 1.0, f"Expected comp sub=1.0, got {comp.sub}"

    def test_score_eur_unknown_salary_neutral(self):
        """C22 EUR no FX rate → compensation sub=0.5 (neutral)."""
        c22 = next(e for e in CORPUS if e.case.startswith("C22"))
        result = _score_entry(c22)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == 0.5

    def test_score_competitive_salary_neutral(self):
        """C23 'competitive' salary → compensation sub=0.5 (unknown)."""
        c23 = next(e for e in CORPUS if e.case.startswith("C23"))
        result = _score_entry(c23)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == 0.5

    def test_score_components_present_for_all_active_weights(self):
        """Each passing listing gets a component for every non-zero weight."""
        active_weights = {name for name, w in FIXTURE_PROFILE["weights"].items() if w > 0}
        for entry in CORPUS_PASS[:3]:
            result = _score_entry(entry)
            returned_names = {c.name for c in result.components}
            assert active_weights == returned_names, (
                f"{entry.case}: component names {returned_names} != weights {active_weights}"
            )

    def test_score_run_returns_one_result_per_listing(self):
        """score.run() returns the same count as its input list."""
        listings = [e.listing for e in CORPUS_PASS]
        results = score.run(listings, FIXTURE_PROFILE, today=REF_DATE)
        assert len(results) == len(listings)

    def test_score_unknown_salary_sub_is_neutral(self):
        """C07 has no salary at all → compensation sub=0.5."""
        c07 = next(e for e in CORPUS if e.case.startswith("C07"))
        result = _score_entry(c07)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == 0.5


# ---------------------------------------------------------------------------
# End-to-end pipeline smoke test over the corpus
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Run CORPUS through all four stages and verify aggregate outcomes."""

    def test_e2e_filter_count(self):
        """All passing corpus listings survive the full filter stage."""
        all_listings = [e.listing for e in CORPUS]
        normalized = normalize.run(all_listings)
        deduped = dedupe.run(normalized)
        filter_result = _run_filter(deduped)

        expected_pass_count = sum(1 for e in CORPUS if e.filter_pass is True)
        assert len(filter_result.passed) == expected_pass_count

    def test_e2e_score_ordered(self):
        """Scores from score.run() are all in [0, 100]."""
        listings = [e.listing for e in CORPUS_PASS + CORPUS_EDGE]
        scored = score.run(listings, FIXTURE_PROFILE, today=REF_DATE)
        for sr in scored:
            assert 0 <= sr.score <= 100, f"Score out of range: {sr.score}"

    def test_e2e_dedupe_does_not_drop_corpus_pass_entries(self):
        """Corpus PASS entries are all distinct (no accidental deduplication)."""
        listings = [e.listing for e in CORPUS_PASS]
        deduped = dedupe.run(listings)
        assert len(deduped) == len(listings), (
            "CORPUS_PASS entries should all have unique ids — "
            f"expected {len(listings)}, got {len(deduped)}"
        )
