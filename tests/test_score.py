# SPDX-License-Identifier: Apache-2.0
"""Tests for Stage 5 — Score (soft preferences).

Covers: per-component breakdown, weight normalization, unknown-field neutrality (0.5),
determinism, golden-corpus acceptance criteria, and run() batch behaviour.

See: specs/02-functional-spec.md §Stage 5
     specs/03-data-model.md §ScoredResult
"""

from __future__ import annotations

from datetime import date

import pytest

from jobhunter.model import JobListing, Location, Salary, Seniority, Source
from jobhunter.score import run as score_run
from jobhunter.score import score

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TODAY = date(2026, 7, 23)

BASE_PROFILE = {
    "identity": {
        "target_skills": ["Python", "AWS", "Kubernetes", "TypeScript"],
        "target": [{"track": "ic", "level": "senior"}],
    },
    "hard_requirements": {
        "salary_floor": 150000,
        "salary_currency": "AUD",
        "fx_rates": {"USD": 1.5},
        "keep_unknown_salary": True,
    },
    "preferences": {
        "preferred_locations": ["Remote Australia"],
        "salary_target": 220000,
        "preferred_companies": ["Atlassian", "Canva"],
    },
    "weights": {
        "skill_match": 30,
        "seniority_fit": 20,
        "compensation": 20,
        "location_fit": 15,
        "company_signal": 10,
        "recency": 5,
    },
    "output": {"display_threshold": 0, "max_shown": 25},
}


def _listing(
    *,
    id: str = "abc" + "0" * 61,
    title: str = "Senior Software Engineer",
    company: str = "Acme Corp",
    is_remote: bool = True,
    city: str | None = None,
    country: str | None = "AU",
    salary: Salary | None = None,
    seniority: Seniority | None = None,
    description: str = "We build distributed systems.",
    posted_at: str | None = "2026-07-20",
    first_seen_at: str = "2026-07-20",
    location_raw: str = "Remote Australia",
) -> JobListing:
    return JobListing(
        id=id,
        content_hash="hash",
        title=title,
        company=company,
        location=Location(
            raw=location_raw,
            city=city,
            region=None,
            country=country,
            is_remote=is_remote,
        ),
        salary=salary,
        seniority=seniority,
        employment="full_time",
        description=description,
        posted_at=posted_at,
        first_seen_at=first_seen_at,
        sources=[Source(name="fixture", url="https://example.com/1", source_id="1")],
    )


# ---------------------------------------------------------------------------
# skill_match component
# ---------------------------------------------------------------------------


class TestSkillMatch:
    def test_all_skills_match(self):
        listing = _listing(
            title="Senior Python Engineer",
            description="AWS Kubernetes and TypeScript experience required.",
        )
        result = score(listing, BASE_PROFILE, today=TODAY)
        sm = next(c for c in result.components if c.name == "skill_match")
        assert sm.sub == pytest.approx(1.0)
        assert "4/4" in sm.reason

    def test_no_skills_match(self):
        listing = _listing(title="Ruby Developer", description="Ruby on Rails only.")
        result = score(listing, BASE_PROFILE, today=TODAY)
        sm = next(c for c in result.components if c.name == "skill_match")
        assert sm.sub == pytest.approx(0.0)
        assert "0/4" in sm.reason

    def test_partial_skill_match(self):
        listing = _listing(title="Python Developer", description="AWS experience a bonus.")
        result = score(listing, BASE_PROFILE, today=TODAY)
        sm = next(c for c in result.components if c.name == "skill_match")
        assert sm.sub == pytest.approx(0.5)  # 2/4

    def test_word_boundary_no_false_positives(self):
        # "Pythonist" should NOT match "Python"; "AWSOME" should NOT match "AWS"
        listing = _listing(title="Pythonist AWSOME Developer", description="No real skills.")
        result = score(listing, BASE_PROFILE, today=TODAY)
        sm = next(c for c in result.components if c.name == "skill_match")
        assert sm.sub == pytest.approx(0.0)

    def test_case_insensitive(self):
        listing = _listing(title="PYTHON developer", description="aws and KUBERNETES systems.")
        result = score(listing, BASE_PROFILE, today=TODAY)
        sm = next(c for c in result.components if c.name == "skill_match")
        assert sm.sub == pytest.approx(0.75)  # python + aws + kubernetes = 3/4

    def test_large_skill_list_saturates(self):
        """A rich skill list must not deflate scores: 5 matches = full marks."""
        profile = {
            **BASE_PROFILE,
            "identity": {
                "target_skills": [
                    "python", "aws", "kubernetes", "typescript", "docker",
                    "terraform", "react", "graphql", "postgres", "redis",
                    "kafka", "grafana", "linux", "networking", "security",
                    "ci/cd", "golang",
                ],
                "target": [{"track": "ic", "level": "senior"}],
            },
        }
        listing = _listing(
            title="Senior Python Engineer",
            description="AWS Kubernetes TypeScript and Docker experience required.",
        )
        result = score(listing, profile, today=TODAY)
        sm = next(c for c in result.components if c.name == "skill_match")
        assert sm.sub == pytest.approx(1.0)  # 5 matches saturates despite 17 targets

    def test_saturation_partial_still_proportional(self):
        profile = {
            **BASE_PROFILE,
            "identity": {
                "target_skills": [
                    "python", "aws", "kubernetes", "typescript", "docker",
                    "terraform", "react", "graphql", "postgres", "redis",
                ],
                "target": [{"track": "ic", "level": "senior"}],
            },
        }
        listing = _listing(title="Python Developer", description="AWS experience a bonus.")
        result = score(listing, profile, today=TODAY)
        sm = next(c for c in result.components if c.name == "skill_match")
        assert sm.sub == pytest.approx(0.4)  # 2 of saturation-5

    def test_empty_target_skills_returns_neutral(self):
        profile = {
            **BASE_PROFILE,
            "identity": {
                "target_skills": [],
                "target": [{"track": "ic", "level": "senior"}],
            },
        }
        result = score(_listing(), profile, today=TODAY)
        sm = next(c for c in result.components if c.name == "skill_match")
        assert sm.sub == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# seniority_fit component
# ---------------------------------------------------------------------------


class TestSeniorityFit:
    def test_exact_match(self):
        listing = _listing(seniority=Seniority(track="ic", level="senior"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        sf = next(c for c in result.components if c.name == "seniority_fit")
        assert sf.sub == pytest.approx(1.0)
        assert "exact match" in sf.reason

    def test_one_step_above(self):
        listing = _listing(seniority=Seniority(track="ic", level="staff"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        sf = next(c for c in result.components if c.name == "seniority_fit")
        assert sf.sub == pytest.approx(0.75)

    def test_one_step_below(self):
        listing = _listing(seniority=Seniority(track="ic", level="mid"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        sf = next(c for c in result.components if c.name == "seniority_fit")
        assert sf.sub == pytest.approx(0.75)

    def test_two_steps_away(self):
        listing = _listing(seniority=Seniority(track="ic", level="principal"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        sf = next(c for c in result.components if c.name == "seniority_fit")
        assert sf.sub == pytest.approx(0.5)

    def test_unknown_seniority_neutral(self):
        listing = _listing(seniority=None)
        result = score(listing, BASE_PROFILE, today=TODAY)
        sf = next(c for c in result.components if c.name == "seniority_fit")
        assert sf.sub == pytest.approx(0.5)
        assert "unclear" in sf.reason

    def test_untargeted_track_neutral(self):
        # Profile only targets IC; management listing gets neutral
        listing = _listing(seniority=Seniority(track="management", level="manager"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        sf = next(c for c in result.components if c.name == "seniority_fit")
        assert sf.sub == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# compensation component
# ---------------------------------------------------------------------------


class TestCompensation:
    def test_salary_at_target_scores_full(self):
        listing = _listing(salary=Salary(min=220000, max=220000, currency="AUD", period="year"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(1.0)

    def test_salary_above_target_capped_at_1(self):
        listing = _listing(salary=Salary(min=280000, max=300000, currency="AUD", period="year"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(1.0)

    def test_salary_at_floor_scores_zero(self):
        listing = _listing(salary=Salary(min=150000, max=150000, currency="AUD", period="year"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(0.0)

    def test_salary_midpoint_scores_half(self):
        # Floor=150k, target=220k → midpoint=185k → sub=0.5
        listing = _listing(salary=Salary(min=180000, max=185000, currency="AUD", period="year"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(0.5)

    def test_unknown_salary_neutral(self):
        listing = _listing(salary=None)
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(0.5)
        assert "unknown" in comp.reason

    def test_fx_conversion_usd(self):
        # USD 130k × 1.5 rate = AUD 195k; (195k-150k)/(220k-150k) = 45/70
        listing = _listing(salary=Salary(min=100000, max=130000, currency="USD", period="year"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        expected = (195000 - 150000) / (220000 - 150000)
        assert comp.sub == pytest.approx(expected, abs=0.01)

    def test_unknown_currency_neutral(self):
        listing = _listing(salary=Salary(min=100000, max=150000, currency="EUR", period="year"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(0.5)

    def test_daily_rate_annualizes_correctly(self):
        # $900/day AUD × 260 = AUD 234k; above target 220k → 1.0
        listing = _listing(salary=Salary(min=800, max=900, currency="AUD", period="day"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(1.0)

    def test_hourly_rate_annualizes(self):
        # $120/hour AUD × 2080 = AUD 249,600; above target → 1.0
        listing = _listing(salary=Salary(min=100, max=120, currency="AUD", period="hour"))
        result = score(listing, BASE_PROFILE, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(1.0)

    def test_no_floor_no_target_neutral(self):
        profile = {
            **BASE_PROFILE,
            "hard_requirements": {
                **BASE_PROFILE["hard_requirements"],
                "salary_floor": None,
            },
            "preferences": {
                **BASE_PROFILE["preferences"],
                "salary_target": None,
            },
        }
        listing = _listing(salary=Salary(min=180000, max=200000, currency="AUD", period="year"))
        result = score(listing, profile, today=TODAY)
        comp = next(c for c in result.components if c.name == "compensation")
        assert comp.sub == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# location_fit component
# ---------------------------------------------------------------------------


class TestLocationFit:
    def test_remote_with_remote_preferred_scores_full(self):
        # is_remote=True and "Remote Australia" contains "remote" → 1.0
        listing = _listing(is_remote=True, country="AU")
        result = score(listing, BASE_PROFILE, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(1.0)
        assert "Remote Australia" in loc.reason

    def test_remote_no_geo_pref_has_geo_is_partial(self):
        """'Remote Australia' preference needs the AU part — unknown-country remote gets 0.75."""
        listing = _listing(is_remote=True, city=None, country=None, location_raw="Remote")
        result = score(listing, BASE_PROFILE, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(0.75)

    def test_remote_in_preferred_country_scores_full(self):
        listing = _listing(is_remote=True, city=None, country="AU", location_raw="Remote Australia")
        result = score(listing, BASE_PROFILE, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(1.0)

    def test_remote_wrong_country_is_partial(self):
        """A US remote role must NOT fully match a 'Remote Australia' preference."""
        listing = _listing(is_remote=True, city=None, country="US", location_raw="Remote US")
        result = score(listing, BASE_PROFILE, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(0.75)

    def test_bare_remote_preference_matches_any_remote(self):
        profile = {
            **BASE_PROFILE,
            "preferences": {**BASE_PROFILE["preferences"], "preferred_locations": ["Remote"]},
        }
        listing = _listing(is_remote=True, city=None, country="US", location_raw="Remote US")
        result = score(listing, profile, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(1.0)

    def test_non_remote_city_not_preferred_is_neutral(self):
        listing = _listing(
            is_remote=False, city="Melbourne", country="AU", location_raw="Melbourne AU"
        )
        result = score(listing, BASE_PROFILE, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(0.5)

    def test_remote_without_preferred_list_scores_0_75(self):
        profile = {
            **BASE_PROFILE,
            "preferences": {**BASE_PROFILE["preferences"], "preferred_locations": []},
        }
        listing = _listing(is_remote=True)
        result = score(listing, profile, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(0.75)

    def test_non_remote_without_preferred_list_is_neutral(self):
        profile = {
            **BASE_PROFILE,
            "preferences": {**BASE_PROFILE["preferences"], "preferred_locations": []},
        }
        listing = _listing(is_remote=False, city="Sydney", country="AU")
        result = score(listing, profile, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(0.5)

    def test_city_match_in_preferred(self):
        profile = {
            **BASE_PROFILE,
            "preferences": {
                **BASE_PROFILE["preferences"],
                "preferred_locations": ["Melbourne"],
            },
        }
        listing = _listing(is_remote=False, city="Melbourne", country="AU")
        result = score(listing, profile, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(1.0)
        assert "Melbourne" in loc.reason

    def test_iso_country_code_not_false_positive(self):
        # "AU" (country code) must NOT match the word "australia" in preferred
        # — pref_words = {"remote","australia"}; "au" not in that set
        listing = _listing(is_remote=False, country="AU", city=None)
        result = score(listing, BASE_PROFILE, today=TODAY)
        loc = next(c for c in result.components if c.name == "location_fit")
        assert loc.sub == pytest.approx(0.5)  # not preferred, not remote → neutral


# ---------------------------------------------------------------------------
# company_signal component
# ---------------------------------------------------------------------------


class TestCompanySignal:
    def test_preferred_company_scores_full(self):
        listing = _listing(company="Atlassian")
        result = score(listing, BASE_PROFILE, today=TODAY)
        co = next(c for c in result.components if c.name == "company_signal")
        assert co.sub == pytest.approx(1.0)
        assert "Atlassian" in co.reason

    def test_case_insensitive_company_match(self):
        listing = _listing(company="CANVA")
        result = score(listing, BASE_PROFILE, today=TODAY)
        co = next(c for c in result.components if c.name == "company_signal")
        assert co.sub == pytest.approx(1.0)

    def test_non_preferred_company_is_neutral(self):
        """Company signal is bonus-only: not being on the list is not a penalty."""
        listing = _listing(company="Random Corp")
        result = score(listing, BASE_PROFILE, today=TODAY)
        co = next(c for c in result.components if c.name == "company_signal")
        assert co.sub == pytest.approx(0.5)

    def test_no_preferred_companies_returns_neutral(self):
        profile = {
            **BASE_PROFILE,
            "preferences": {**BASE_PROFILE["preferences"], "preferred_companies": []},
        }
        result = score(_listing(), profile, today=TODAY)
        co = next(c for c in result.components if c.name == "company_signal")
        assert co.sub == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# recency component
# ---------------------------------------------------------------------------


class TestRecency:
    def test_posted_today_scores_near_1(self):
        listing = _listing(posted_at="2026-07-23")
        result = score(listing, BASE_PROFILE, today=TODAY)
        rec = next(c for c in result.components if c.name == "recency")
        assert rec.sub == pytest.approx(1.0, abs=0.01)

    def test_posted_at_half_life_scores_near_half(self):
        listing = _listing(posted_at="2026-07-09")  # 14 days before TODAY
        result = score(listing, BASE_PROFILE, today=TODAY)
        rec = next(c for c in result.components if c.name == "recency")
        assert rec.sub == pytest.approx(0.5, abs=0.01)

    def test_older_posting_scores_lower(self):
        old = _listing(posted_at="2026-06-01")
        recent = _listing(posted_at="2026-07-15")
        r_old = score(old, BASE_PROFILE, today=TODAY)
        r_recent = score(recent, BASE_PROFILE, today=TODAY)
        old_rec = next(c for c in r_old.components if c.name == "recency").sub
        recent_rec = next(c for c in r_recent.components if c.name == "recency").sub
        assert recent_rec > old_rec

    def test_unknown_date_returns_neutral(self):
        listing = _listing(posted_at=None, first_seen_at="")
        result = score(listing, BASE_PROFILE, today=TODAY)
        rec = next(c for c in result.components if c.name == "recency")
        assert rec.sub == pytest.approx(0.5)

    def test_uses_first_seen_when_no_posted_at(self):
        listing = _listing(posted_at=None, first_seen_at="2026-07-23")
        result = score(listing, BASE_PROFILE, today=TODAY)
        rec = next(c for c in result.components if c.name == "recency")
        assert rec.sub == pytest.approx(1.0, abs=0.01)


# ---------------------------------------------------------------------------
# Overall scoring behaviour (Stage 5 acceptance criteria)
# ---------------------------------------------------------------------------


class TestScoreOverall:
    def test_result_has_all_active_components(self):
        result = score(_listing(), BASE_PROFILE, today=TODAY)
        names = {c.name for c in result.components}
        assert names == {
            "skill_match",
            "seniority_fit",
            "compensation",
            "location_fit",
            "company_signal",
            "recency",
        }

    def test_score_in_0_to_100_range(self):
        result = score(_listing(), BASE_PROFILE, today=TODAY)
        assert 0.0 <= result.score <= 100.0

    def test_summary_reason_present(self):
        result = score(_listing(), BASE_PROFILE, today=TODAY)
        assert isinstance(result.summary_reason, str)
        assert result.summary_reason

    def test_deterministic(self):
        listing = _listing(salary=Salary(min=180000, max=200000, currency="AUD", period="year"))
        r1 = score(listing, BASE_PROFILE, today=TODAY)
        r2 = score(listing, BASE_PROFILE, today=TODAY)
        assert r1.score == r2.score
        assert [(c.name, c.sub) for c in r1.components] == [(c.name, c.sub) for c in r2.components]

    def test_zeroing_weight_removes_component(self):
        profile = {**BASE_PROFILE, "weights": {**BASE_PROFILE["weights"], "recency": 0}}
        result = score(_listing(), profile, today=TODAY)
        assert not any(c.name == "recency" for c in result.components)

    def test_zeroing_weight_rescales_score(self):
        """Removing a component changes the total (weight normalization rule)."""
        r_full = score(_listing(), BASE_PROFILE, today=TODAY)
        profile_no_co = {
            **BASE_PROFILE,
            "weights": {**BASE_PROFILE["weights"], "company_signal": 0},
        }
        r_no_co = score(_listing(), profile_no_co, today=TODAY)
        assert r_full.score != r_no_co.score

    def test_all_weights_zero_scores_zero(self):
        profile = {**BASE_PROFILE, "weights": {k: 0 for k in BASE_PROFILE["weights"]}}
        result = score(_listing(), profile, today=TODAY)
        assert result.score == pytest.approx(0.0)
        assert result.components == []

    def test_unknown_field_never_scores_zero(self):
        listing = _listing(salary=None, seniority=None, posted_at=None, first_seen_at="2026-07-20")
        result = score(listing, BASE_PROFILE, today=TODAY)
        for comp in result.components:
            if comp.name in ("compensation", "seniority_fit"):
                assert comp.sub == pytest.approx(0.5), f"{comp.name} should be neutral 0.5"

    def test_unknown_flags_carried_through(self):
        flags = ["level unclear", "remote scope unclear"]
        result = score(_listing(), BASE_PROFILE, unknown_flags=flags, today=TODAY)
        assert "level unclear" in result.unknown_flags
        assert "remote scope unclear" in result.unknown_flags

    def test_run_scores_all_listings(self):
        listings = [
            _listing(id="a" * 64, company="Atlassian"),
            _listing(id="b" * 64, company="Random Corp"),
        ]
        results = score_run(listings, BASE_PROFILE, today=TODAY)
        assert len(results) == 2

    def test_run_passes_per_listing_flags(self):
        listings = [_listing(id="a" * 64)]
        flags = {"a" * 64: ["level unclear"]}
        results = score_run(listings, BASE_PROFILE, unknown_flags=flags, today=TODAY)
        assert "level unclear" in results[0].unknown_flags

    def test_no_listing_dropped_by_scoring(self):
        """score_run never drops listings; output count == input count."""
        listings = [_listing(id=str(i) * 64) for i in range(5)]
        results = score_run(listings, BASE_PROFILE, today=TODAY)
        assert len(results) == 5

    # --- Golden-corpus tests ---

    def test_golden_ideal_listing_scores_high(self):
        """An ideal listing (all components max) scores near 100."""
        listing = _listing(
            title="Senior Python Engineer",
            company="Atlassian",
            description="AWS Kubernetes TypeScript Python cloud systems.",
            is_remote=True,
            country="AU",
            seniority=Seniority(track="ic", level="senior"),
            salary=Salary(min=220000, max=250000, currency="AUD", period="year"),
            posted_at="2026-07-23",
        )
        result = score(listing, BASE_PROFILE, today=TODAY)
        assert result.score >= 90.0

    def test_golden_poor_listing_scores_low(self):
        """A poor match listing (wrong skills, junior, below-floor salary) scores low."""
        listing = _listing(
            title="Junior Ruby Developer",
            company="Unknown Corp",
            description="Ruby on Rails experience. No cloud experience required.",
            is_remote=False,
            city="Brisbane",
            country="AU",
            seniority=Seniority(track="ic", level="junior"),
            salary=Salary(min=100000, max=120000, currency="AUD", period="year"),
            posted_at="2026-06-01",
        )
        result = score(listing, BASE_PROFILE, today=TODAY)
        assert result.score < 30.0
