# SPDX-License-Identifier: Apache-2.0
"""Tests for profile loading and schema validation.

Covers specs/03-data-model.md §Profile: all fields, defaults, type errors,
unknown-key rejection, enum validation, and constraint enforcement.

Validation: python -m pytest tests/test_profile.py -q
"""

from pathlib import Path

import pytest
import yaml

from jobhunter.profile import ProfileError, load_profile

SPECS_DIR = Path(__file__).parent.parent / "specs"
EXAMPLE_PROFILE = SPECS_DIR / "profile.example.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal(overrides: dict | None = None) -> dict:
    """Return the smallest valid profile dict, with optional overrides."""
    base = {
        "identity": {
            "target_skills": ["Python"],
            "target": [{"track": "ic", "level": "senior"}],
        },
        "queries": {
            "keywords": ["software engineer"],
            "locations": ["Remote AU"],
        },
        "hard_requirements": {
            "remote_policy": "remote_only",
        },
        "preferences": {},
        "weights": {},
        "output": {},
    }
    if overrides:
        base.update(overrides)
    return base


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "profile.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True))
    return p


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_example_profile_loads() -> None:
    """The example profile validates without error and returns a dict."""
    result = load_profile(EXAMPLE_PROFILE)
    assert isinstance(result, dict)


def test_minimal_profile_loads(tmp_path: Path) -> None:
    """A minimal valid profile (only required fields) loads successfully."""
    p = _write(tmp_path, _minimal())
    result = load_profile(p)
    assert result["hard_requirements"]["remote_policy"] == "remote_only"


def test_defaults_applied(tmp_path: Path) -> None:
    """All optional defaults are applied when fields are absent."""
    p = _write(tmp_path, _minimal())
    r = load_profile(p)

    q = r["queries"]
    assert q["max_results_per_query"] == 50
    assert q["max_requests_per_run"] == 100

    hr = r["hard_requirements"]
    assert hr["exclude_locations"] == []
    assert hr["locations_allowed"] == []
    assert hr["keep_unknown_salary"] is True
    assert hr["exclude_employment"] == []
    assert hr["exclude_keywords"] == []
    assert hr["max_age_days"] == 30

    out = r["output"]
    assert out["display_threshold"] == 0
    assert out["max_shown"] == 25
    assert out["show_previously_seen"] is True
    assert out["format"] == "markdown"


def test_exclude_keywords_scope_default(tmp_path: Path) -> None:
    """exclude_keywords entries without scope get default scope='requirements'."""
    data = _minimal()
    data["hard_requirements"]["exclude_keywords"] = [{"term": "unpaid"}]
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert r["hard_requirements"]["exclude_keywords"][0]["scope"] == "requirements"


def test_exclude_keywords_explicit_title_scope(tmp_path: Path) -> None:
    """explicit scope='title' is preserved as-is."""
    data = _minimal()
    data["hard_requirements"]["exclude_keywords"] = [{"term": "PHP", "scope": "title"}]
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert r["hard_requirements"]["exclude_keywords"][0]["scope"] == "title"


def test_both_tracks_accepted(tmp_path: Path) -> None:
    """identity.target may contain both ic and management entries."""
    data = _minimal()
    data["identity"]["target"] = [
        {"track": "ic", "level": "staff"},
        {"track": "management", "level": "manager"},
    ]
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert len(r["identity"]["target"]) == 2


def test_all_remote_policies_valid(tmp_path: Path) -> None:
    """All four remote_policy enum values are accepted."""
    for policy in ("remote_only", "hybrid_ok", "onsite_ok", "any"):
        data = _minimal()
        data["hard_requirements"]["remote_policy"] = policy
        p = _write(tmp_path, data)
        load_profile(p)  # must not raise


def test_salary_floor_with_currency(tmp_path: Path) -> None:
    """salary_floor with salary_currency is valid."""
    data = _minimal()
    data["hard_requirements"]["salary_floor"] = 120000
    data["hard_requirements"]["salary_currency"] = "AUD"
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert r["hard_requirements"]["salary_floor"] == 120000


def test_fx_rates_map(tmp_path: Path) -> None:
    """fx_rates with valid currency → rate mapping loads correctly."""
    data = _minimal()
    data["hard_requirements"]["fx_rates"] = {"USD": 1.55, "NZD": 0.92}
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert r["hard_requirements"]["fx_rates"]["USD"] == 1.55


def test_seniority_bounds_with_max(tmp_path: Path) -> None:
    """seniority bounds with explicit max load correctly."""
    data = _minimal()
    data["hard_requirements"]["seniority"] = {
        "ic": {"min": "mid", "max": "principal"},
        "management": {"min": "manager", "max": "director"},
    }
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert r["hard_requirements"]["seniority"]["ic"]["min"] == "mid"


def test_seniority_max_null(tmp_path: Path) -> None:
    """seniority.max may be null (no upper bound)."""
    data = _minimal()
    data["hard_requirements"]["seniority"] = {"ic": {"min": "senior", "max": None}}
    p = _write(tmp_path, data)
    load_profile(p)  # must not raise


def test_all_ic_levels_valid(tmp_path: Path) -> None:
    """All valid ic levels are accepted in identity.target."""
    for level in ("intern", "junior", "mid", "senior", "staff", "principal"):
        data = _minimal()
        data["identity"]["target"] = [{"track": "ic", "level": level}]
        p = _write(tmp_path, data)
        load_profile(p)


def test_all_management_levels_valid(tmp_path: Path) -> None:
    """All valid management levels are accepted in identity.target."""
    for level in ("manager", "senior_manager", "director", "vp"):
        data = _minimal()
        data["identity"]["target"] = [{"track": "management", "level": level}]
        p = _write(tmp_path, data)
        load_profile(p)


def test_all_employment_types_valid(tmp_path: Path) -> None:
    """All valid employment types are accepted in exclude_employment."""
    data = _minimal()
    data["hard_requirements"]["exclude_employment"] = [
        "full_time",
        "part_time",
        "contract",
        "temp",
        "internship",
    ]
    p = _write(tmp_path, data)
    load_profile(p)


def test_output_all_formats_valid(tmp_path: Path) -> None:
    """All three output.format values are accepted."""
    for fmt in ("markdown", "html", "both"):
        data = _minimal()
        data["output"] = {"format": fmt}
        p = _write(tmp_path, data)
        load_profile(p)


def test_profile_not_found() -> None:
    """ProfileError raised when file does not exist."""
    with pytest.raises(ProfileError, match="not found"):
        load_profile("/nonexistent/path/profile.yaml")


# ---------------------------------------------------------------------------
# Unknown-key rejection
# ---------------------------------------------------------------------------


def test_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    data = _minimal()
    data["typo_key"] = "oops"
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_rejects_unknown_identity_key(tmp_path: Path) -> None:
    data = _minimal()
    data["identity"]["target_titles"] = ["engineer"]  # removed field
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_rejects_unknown_queries_key(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["extra"] = True
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_rejects_unknown_hard_requirements_key(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["ban_list"] = []
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_rejects_unknown_preferences_key(tmp_path: Path) -> None:
    data = _minimal()
    data["preferences"]["industry"] = "fintech"
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_rejects_unknown_weights_key(tmp_path: Path) -> None:
    data = _minimal()
    data["weights"]["industry_fit"] = 10
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_rejects_unknown_output_key(tmp_path: Path) -> None:
    data = _minimal()
    data["output"]["theme"] = "dark"
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_rejects_unknown_seniority_track(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["seniority"] = {"executive": {"min": "ceo", "max": None}}
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_rejects_unknown_exclude_keyword_key(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["exclude_keywords"] = [
        {"term": "PHP", "scope": "title", "extra": True}
    ]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_missing_identity(tmp_path: Path) -> None:
    data = _minimal()
    del data["identity"]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="identity"):
        load_profile(p)


def test_missing_identity_target_skills(tmp_path: Path) -> None:
    data = _minimal()
    del data["identity"]["target_skills"]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="target_skills"):
        load_profile(p)


def test_missing_identity_target(tmp_path: Path) -> None:
    data = _minimal()
    del data["identity"]["target"]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="target"):
        load_profile(p)


def test_missing_queries_keywords(tmp_path: Path) -> None:
    data = _minimal()
    del data["queries"]["keywords"]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="keywords"):
        load_profile(p)


def test_missing_queries_locations(tmp_path: Path) -> None:
    data = _minimal()
    del data["queries"]["locations"]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="locations"):
        load_profile(p)


def test_missing_remote_policy(tmp_path: Path) -> None:
    data = _minimal()
    del data["hard_requirements"]["remote_policy"]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="remote_policy"):
        load_profile(p)


def test_empty_target_skills_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["identity"]["target_skills"] = []
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="target_skills"):
        load_profile(p)


def test_empty_keywords_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["keywords"] = []
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="keywords"):
        load_profile(p)


def test_empty_target_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["identity"]["target"] = []
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="target"):
        load_profile(p)


# ---------------------------------------------------------------------------
# Invalid enum values
# ---------------------------------------------------------------------------


def test_invalid_remote_policy(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["remote_policy"] = "office_only"
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="remote_policy"):
        load_profile(p)


def test_invalid_identity_track(tmp_path: Path) -> None:
    data = _minimal()
    data["identity"]["target"] = [{"track": "executive", "level": "ceo"}]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="track"):
        load_profile(p)


def test_invalid_ic_level(tmp_path: Path) -> None:
    data = _minimal()
    data["identity"]["target"] = [{"track": "ic", "level": "lead"}]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="level"):
        load_profile(p)


def test_invalid_management_level(tmp_path: Path) -> None:
    data = _minimal()
    data["identity"]["target"] = [{"track": "management", "level": "partner"}]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="level"):
        load_profile(p)


def test_invalid_exclude_employment(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["exclude_employment"] = ["gig"]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="exclude_employment"):
        load_profile(p)


def test_invalid_keyword_scope(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["exclude_keywords"] = [{"term": "PHP", "scope": "everywhere"}]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="scope"):
        load_profile(p)


def test_invalid_output_format(tmp_path: Path) -> None:
    data = _minimal()
    data["output"] = {"format": "pdf"}
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="format"):
        load_profile(p)


# ---------------------------------------------------------------------------
# Type errors
# ---------------------------------------------------------------------------


def test_type_error_target_skills_not_list(tmp_path: Path) -> None:
    data = _minimal()
    data["identity"]["target_skills"] = "Python"
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_type_error_keywords_contains_non_string(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["keywords"] = [42]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_type_error_max_results_not_int(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["max_results_per_query"] = "fifty"
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_type_error_keep_unknown_salary_not_bool(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["keep_unknown_salary"] = "yes"
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_type_error_fx_rates_value_not_number(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["fx_rates"] = {"USD": "one-point-five"}
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_type_error_salary_floor_not_number(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"]["salary_floor"] = "high"
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_type_error_weight_not_number(tmp_path: Path) -> None:
    data = _minimal()
    data["weights"] = {"skill_match": "high"}
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_type_error_show_previously_seen_not_bool(tmp_path: Path) -> None:
    data = _minimal()
    data["output"] = {"show_previously_seen": "yes"}
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


# ---------------------------------------------------------------------------
# Constraint enforcement
# ---------------------------------------------------------------------------


def test_seniority_min_greater_than_max_rejected(tmp_path: Path) -> None:
    """seniority min > max is a hard error."""
    data = _minimal()
    data["hard_requirements"]["seniority"] = {"ic": {"min": "principal", "max": "junior"}}
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="min"):
        load_profile(p)


def test_salary_floor_without_currency_rejected(tmp_path: Path) -> None:
    """salary_floor without salary_currency is a hard error."""
    data = _minimal()
    data["hard_requirements"]["salary_floor"] = 100000
    # deliberately no salary_currency
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="salary_currency"):
        load_profile(p)


def test_exclude_locations_preserved(tmp_path: Path) -> None:
    """exclude_locations values are returned as-is for use by the filter."""
    data = _minimal()
    data["hard_requirements"]["exclude_locations"] = ["Sydney", "Melbourne"]
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert r["hard_requirements"]["exclude_locations"] == ["Sydney", "Melbourne"]


def test_locations_allowed_preserved(tmp_path: Path) -> None:
    """locations_allowed values are returned as-is for use by the filter."""
    data = _minimal()
    data["hard_requirements"]["locations_allowed"] = ["Auckland"]
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert r["hard_requirements"]["locations_allowed"] == ["Auckland"]


def test_remote_policy_preserved_for_filter(tmp_path: Path) -> None:
    """remote_policy is available in the returned dict for the filter stage."""
    data = _minimal()
    data["hard_requirements"]["remote_policy"] = "hybrid_ok"
    p = _write(tmp_path, data)
    r = load_profile(p)
    assert r["hard_requirements"]["remote_policy"] == "hybrid_ok"


def test_not_yaml_mapping(tmp_path: Path) -> None:
    """A YAML file that isn't a mapping raises ProfileError."""
    p = tmp_path / "profile.yaml"
    p.write_text("- item1\n- item2\n")
    with pytest.raises(ProfileError, match="mapping"):
        load_profile(p)
