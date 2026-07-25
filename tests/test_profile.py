# SPDX-License-Identifier: Apache-2.0
"""Tests for profile loading and schema validation.

Covers specs/03-data-model.md §Profile: all fields, defaults, type errors,
unknown-key rejection, enum validation, and constraint enforcement.

Validation: python -m pytest tests/test_profile.py -q
"""

import os as _os
import time as _time
from pathlib import Path

import pytest
import yaml

from jobhunter.profile import (
    ProfileError,
    effective_fx_rates,
    fx_rates_age_days,
    load_fx_rates,
    load_profile,
)

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
        "weights": {"skill_match": 1},
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
    assert out["data_format"] == "json"


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


def test_output_data_format_valid_values(tmp_path: Path) -> None:
    """All three output.data_format values (json, csv, both) are accepted."""
    for dfmt in ("json", "csv", "both"):
        data = _minimal()
        data["output"] = {"data_format": dfmt}
        p = _write(tmp_path, data)
        result = load_profile(p)
        assert result["output"]["data_format"] == dfmt


def test_output_data_format_default_is_json(tmp_path: Path) -> None:
    """output.data_format defaults to 'json' when omitted."""
    data = _minimal()
    data["output"] = {}
    p = _write(tmp_path, data)
    result = load_profile(p)
    assert result["output"]["data_format"] == "json"


def test_invalid_output_data_format(tmp_path: Path) -> None:
    data = _minimal()
    data["output"] = {"data_format": "xml"}
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="data_format"):
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


# ---------------------------------------------------------------------------
# Validation tightening (spec 03 amendments)
# ---------------------------------------------------------------------------


def test_empty_weights_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="at least one positive weight"):
        load_profile(_write(tmp_path, _minimal({"weights": {}})))


def test_all_zero_weights_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="at least one positive weight"):
        load_profile(_write(tmp_path, _minimal({"weights": {"skill_match": 0, "recency": 0}})))


def test_negative_weight_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="negative"):
        load_profile(_write(tmp_path, _minimal({"weights": {"skill_match": -5, "recency": 5}})))


def test_bool_salary_floor_rejected(tmp_path: Path) -> None:
    """isinstance(True, int) must not let booleans pass number validation."""
    prof = _minimal()
    prof["hard_requirements"] = {
        **prof["hard_requirements"],
        "salary_floor": True,
        "salary_currency": "AUD",
    }
    with pytest.raises(ProfileError, match="salary_floor"):
        load_profile(_write(tmp_path, prof))


def test_bool_weight_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="weights.skill_match"):
        load_profile(_write(tmp_path, _minimal({"weights": {"skill_match": True}})))


def test_empty_locations_rejected(tmp_path: Path) -> None:
    prof = _minimal()
    prof["queries"] = {**prof["queries"], "locations": []}
    with pytest.raises(ProfileError, match="locations must not be empty"):
        load_profile(_write(tmp_path, prof))


# ---------------------------------------------------------------------------
# search_mode posture presets (spec 02 §Search posture)
# ---------------------------------------------------------------------------


def test_search_mode_preset_fills_unset_knobs(tmp_path: Path) -> None:
    prof = load_profile(_write(tmp_path, _minimal({"search_mode": "active_unemployed"})))
    assert prof["output"]["display_threshold"] == 40
    assert prof["output"]["max_shown"] == 40
    assert prof["hard_requirements"]["max_age_days"] == 30


def test_search_mode_explicit_values_win(tmp_path: Path) -> None:
    data = _minimal({"search_mode": "passive_employed", "output": {"display_threshold": 60}})
    prof = load_profile(_write(tmp_path, data))
    assert prof["output"]["display_threshold"] == 60  # explicit beats preset (70)
    assert prof["output"]["max_shown"] == 10  # preset fills the unset knob


def test_search_mode_absent_uses_schema_defaults(tmp_path: Path) -> None:
    prof = load_profile(_write(tmp_path, _minimal()))
    assert prof["output"]["display_threshold"] == 0
    assert prof["output"]["max_shown"] == 25
    assert prof["hard_requirements"]["max_age_days"] == 30


def test_search_mode_invalid_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="search_mode"):
        load_profile(_write(tmp_path, _minimal({"search_mode": "desperate"})))


def test_require_keywords_validated_and_defaulted(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"] = {
        **data["hard_requirements"],
        "require_keywords": [{"term": "kubernetes"}],
    }
    prof = load_profile(_write(tmp_path, data))
    assert prof["hard_requirements"]["require_keywords"][0]["scope"] == "requirements"


def test_require_keywords_bad_scope_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"] = {
        **data["hard_requirements"],
        "require_keywords": [{"term": "x", "scope": "everywhere"}],
    }
    with pytest.raises(ProfileError, match="require_keywords"):
        load_profile(_write(tmp_path, data))


# ---------------------------------------------------------------------------
# queries.ats_watchlist (ATS company watchlist)
# ---------------------------------------------------------------------------


def test_ats_watchlist_defaults_to_empty(tmp_path: Path) -> None:
    """ats_watchlist defaults to [] when absent."""
    r = load_profile(_write(tmp_path, _minimal()))
    assert r["queries"]["ats_watchlist"] == []


def test_ats_watchlist_valid_entries(tmp_path: Path) -> None:
    """Valid ats_watchlist entries for all three supported ATS types load correctly."""
    data = _minimal()
    data["queries"]["ats_watchlist"] = [
        {"ats": "greenhouse", "slug": "github", "name": "GitHub"},
        {"ats": "lever", "slug": "hashicorp"},
        {"ats": "ashby", "slug": "elastic", "name": "Elastic"},
    ]
    r = load_profile(_write(tmp_path, data))
    assert len(r["queries"]["ats_watchlist"]) == 3


def test_ats_watchlist_name_is_optional(tmp_path: Path) -> None:
    """ats_watchlist entries without 'name' are valid."""
    data = _minimal()
    data["queries"]["ats_watchlist"] = [{"ats": "lever", "slug": "canonical"}]
    load_profile(_write(tmp_path, data))  # must not raise


def test_ats_watchlist_unsupported_ats_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["ats_watchlist"] = [{"ats": "sapsuccessfactors", "slug": "microsoft"}]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="ats_watchlist"):
        load_profile(p)


def test_ats_watchlist_missing_ats_field_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["ats_watchlist"] = [{"slug": "github"}]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="ats_watchlist"):
        load_profile(p)


def test_ats_watchlist_missing_slug_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["ats_watchlist"] = [{"ats": "greenhouse"}]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="ats_watchlist"):
        load_profile(p)


def test_ats_watchlist_empty_slug_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["ats_watchlist"] = [{"ats": "greenhouse", "slug": "   "}]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="slug"):
        load_profile(p)


def test_ats_watchlist_unknown_key_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["ats_watchlist"] = [
        {"ats": "greenhouse", "slug": "github", "extra_field": "oops"}
    ]
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_ats_watchlist_entry_not_dict_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["ats_watchlist"] = ["github"]  # should be list of dicts
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_ats_watchlist_not_list_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["queries"]["ats_watchlist"] = {"ats": "greenhouse", "slug": "github"}  # should be list
    p = _write(tmp_path, data)
    with pytest.raises(ProfileError):
        load_profile(p)


def test_remote_countries_allowed_validated(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"] = {**data["hard_requirements"], "remote_countries_allowed": ["AU"]}
    prof = load_profile(_write(tmp_path, data))
    assert prof["hard_requirements"]["remote_countries_allowed"] == ["AU"]


def test_remote_countries_allowed_empty_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["hard_requirements"] = {**data["hard_requirements"], "remote_countries_allowed": []}
    with pytest.raises(ProfileError, match="remote_countries_allowed"):
        load_profile(_write(tmp_path, data))


# ---------------------------------------------------------------------------
# Global FX rates (fx_rates.yaml)
# ---------------------------------------------------------------------------


def test_load_fx_rates_missing_file(tmp_path: Path) -> None:
    fx = load_fx_rates(tmp_path / "nope.yaml")
    assert fx == {"base": None, "rates": {}}


def test_load_fx_rates_parses(tmp_path: Path) -> None:
    f = tmp_path / "fx.yaml"
    f.write_text("base: AUD\nrates:\n  USD: 1.5\n  eur: 1.65\n")
    fx = load_fx_rates(f)
    assert fx["base"] == "AUD" and fx["rates"]["USD"] == 1.5 and fx["rates"]["EUR"] == 1.65


def test_effective_fx_same_base() -> None:
    prof = {"hard_requirements": {"salary_currency": "AUD"}}
    fx = {"base": "AUD", "rates": {"USD": 1.5, "EUR": 1.65}}
    assert effective_fx_rates(prof, fx) == {"USD": 1.5, "EUR": 1.65}


def test_effective_fx_cross_rate() -> None:
    """USD-target profile: 1 EUR = 1.65 AUD, 1 USD = 1.5 AUD => 1 EUR = 1.1 USD."""
    prof = {"hard_requirements": {"salary_currency": "USD"}}
    fx = {"base": "AUD", "rates": {"USD": 1.5, "EUR": 1.65}}
    merged = effective_fx_rates(prof, fx)
    assert merged["EUR"] == pytest.approx(1.1)
    assert merged["AUD"] == pytest.approx(1 / 1.5)


def test_effective_fx_profile_override_wins() -> None:
    prof = {"hard_requirements": {"salary_currency": "AUD", "fx_rates": {"USD": 1.42}}}
    fx = {"base": "AUD", "rates": {"USD": 1.5}}
    assert effective_fx_rates(prof, fx)["USD"] == 1.42


# ---------------------------------------------------------------------------
# sources: block (profile-driven-sources)
# ---------------------------------------------------------------------------


def test_sources_absent_defaults_to_empty(tmp_path: Path) -> None:
    """sources: key is optional; defaults to {} when absent."""
    r = load_profile(_write(tmp_path, _minimal()))
    assert r.get("sources") == {}


def test_sources_empty_dict_loads(tmp_path: Path) -> None:
    """sources: {} is valid."""
    data = _minimal()
    data["sources"] = {}
    r = load_profile(_write(tmp_path, data))
    assert r["sources"] == {}


def test_sources_adzuna_enabled_true(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"adzuna": {"enabled": True, "country": "au"}}
    r = load_profile(_write(tmp_path, data))
    assert r["sources"]["adzuna"]["enabled"] is True
    assert r["sources"]["adzuna"]["country"] == "au"


def test_sources_adzuna_enabled_false(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"adzuna": {"enabled": False}}
    r = load_profile(_write(tmp_path, data))
    assert r["sources"]["adzuna"]["enabled"] is False


def test_sources_adzuna_country_optional(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"adzuna": {"enabled": True}}
    load_profile(_write(tmp_path, data))  # must not raise


def test_sources_adzuna_unknown_key_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"adzuna": {"enabled": True, "tier": "free"}}
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(_write(tmp_path, data))


def test_sources_adzuna_enabled_not_bool_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"adzuna": {"enabled": "yes"}}
    with pytest.raises(ProfileError, match="sources.adzuna.enabled"):
        load_profile(_write(tmp_path, data))


def test_sources_ats_watchlist_valid(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {
        "ats_watchlist": [
            {"ats": "greenhouse", "slug": "gitlab", "name": "GitLab"},
            {"ats": "lever", "slug": "canonical"},
        ]
    }
    r = load_profile(_write(tmp_path, data))
    assert len(r["sources"]["ats_watchlist"]) == 2


def test_sources_ats_watchlist_unsupported_ats_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"ats_watchlist": [{"ats": "sapsuccessfactors", "slug": "red-hat"}]}
    with pytest.raises(ProfileError, match="ats_watchlist"):
        load_profile(_write(tmp_path, data))


def test_sources_ats_watchlist_empty_slug_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"ats_watchlist": [{"ats": "greenhouse", "slug": "  "}]}
    with pytest.raises(ProfileError, match="slug"):
        load_profile(_write(tmp_path, data))


def test_sources_ats_watchlist_unknown_key_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"ats_watchlist": [{"ats": "greenhouse", "slug": "x", "extra": 1}]}
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(_write(tmp_path, data))


def test_sources_feeds_valid(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {
        "feeds": [{"name": "iworkfornsw", "url": "https://example.com/feed.rss"}]
    }
    r = load_profile(_write(tmp_path, data))
    assert r["sources"]["feeds"][0]["name"] == "iworkfornsw"


def test_sources_feeds_missing_url_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"feeds": [{"name": "myfeed"}]}
    with pytest.raises(ProfileError, match="url"):
        load_profile(_write(tmp_path, data))


def test_sources_feeds_missing_name_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"feeds": [{"url": "https://example.com/feed.rss"}]}
    with pytest.raises(ProfileError, match="name"):
        load_profile(_write(tmp_path, data))


def test_sources_feeds_unknown_key_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"feeds": [{"name": "x", "url": "https://x.com", "auth": "token"}]}
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(_write(tmp_path, data))


def test_sources_remotive_valid(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"remotive": {"enabled": False, "categories": ["devrel"]}}
    r = load_profile(_write(tmp_path, data))
    assert r["sources"]["remotive"]["enabled"] is False


def test_sources_remotive_unknown_key_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"remotive": {"enabled": False, "region": "us"}}
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(_write(tmp_path, data))


def test_sources_remoteok_and_jooble_valid(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"remoteok": {"enabled": False}, "jooble": {"enabled": False}}
    r = load_profile(_write(tmp_path, data))
    assert r["sources"]["remoteok"]["enabled"] is False
    assert r["sources"]["jooble"]["enabled"] is False


def test_sources_remoteok_unknown_key_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"remoteok": {"enabled": False, "country": "us"}}
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(_write(tmp_path, data))


def test_sources_unknown_key_rejected(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {"linkedin": {"enabled": True}}
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(_write(tmp_path, data))


# ---------------------------------------------------------------------------
# Workday entries in sources.ats_watchlist
# ---------------------------------------------------------------------------


def test_sources_ats_watchlist_workday_minimal(tmp_path: Path) -> None:
    """Workday entry with only ats+slug is valid."""
    data = _minimal()
    data["sources"] = {"ats_watchlist": [{"ats": "workday", "slug": "redhat"}]}
    r = load_profile(_write(tmp_path, data))
    assert r["sources"]["ats_watchlist"][0]["slug"] == "redhat"


def test_sources_ats_watchlist_workday_with_path_and_instance(tmp_path: Path) -> None:
    """Workday entry with optional workday_path and workday_instance is valid."""
    data = _minimal()
    data["sources"] = {
        "ats_watchlist": [
            {
                "ats": "workday",
                "slug": "redhat",
                "name": "Red Hat",
                "workday_path": "RedHat/jobs",
                "workday_instance": 5,
            }
        ]
    }
    r = load_profile(_write(tmp_path, data))
    entry = r["sources"]["ats_watchlist"][0]
    assert entry["workday_path"] == "RedHat/jobs"
    assert entry["workday_instance"] == 5


def test_sources_ats_watchlist_workday_instance_not_int_rejected(tmp_path: Path) -> None:
    """workday_instance must be an integer."""
    data = _minimal()
    data["sources"] = {
        "ats_watchlist": [{"ats": "workday", "slug": "redhat", "workday_instance": "five"}]
    }
    with pytest.raises(ProfileError, match="workday_instance"):
        load_profile(_write(tmp_path, data))


def test_queries_ats_watchlist_workday_with_path(tmp_path: Path) -> None:
    """workday_path is also accepted in queries.ats_watchlist (deprecated path)."""
    data = _minimal()
    data["queries"]["ats_watchlist"] = [
        {"ats": "workday", "slug": "atlassian", "workday_path": "Atlassian/jobs"}
    ]
    r = load_profile(_write(tmp_path, data))
    assert r["queries"]["ats_watchlist"][0]["workday_path"] == "Atlassian/jobs"


# ---------------------------------------------------------------------------
# Live profile smoke test
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def test_real_profiles_load() -> None:
    """Every root profile-*.yaml on disk loads without ProfileError.

    Real profiles are git-ignored, so this skips on a fresh clone. When they are
    present it is the only test that exercises the validator against a
    hand-edited file rather than a constructed dict.
    """
    profiles = sorted(_REPO_ROOT.glob("profile-*.yaml"))
    if not profiles:
        pytest.skip("no root profile-*.yaml present (git-ignored; copy from the example)")
    for p in profiles:
        load_profile(p)
# fx_rates_age_days (fx-staleness-warning)
# ---------------------------------------------------------------------------


def test_fx_rates_age_days_missing_file(tmp_path: Path) -> None:
    """Returns None when the file does not exist."""
    assert fx_rates_age_days(tmp_path / "nope.yaml") is None


def test_fx_rates_age_days_fresh_file(tmp_path: Path) -> None:
    """Returns 0 for a file written just now."""
    f = tmp_path / "fx.yaml"
    f.write_text("base: AUD\nrates:\n  USD: 1.5\n")
    assert fx_rates_age_days(f) == 0


def test_fx_rates_age_days_old_file(tmp_path: Path) -> None:
    """Returns the correct day count for a file whose mtime is set in the past."""
    f = tmp_path / "fx.yaml"
    f.write_text("base: AUD\nrates:\n  USD: 1.5\n")
    old_mtime = _time.time() - 100 * 86400  # 100 days ago
    _os.utime(f, (old_mtime, old_mtime))
    assert fx_rates_age_days(f) == 100


def test_preferences_deprioritize_keywords_valid(tmp_path: Path) -> None:
    data = _minimal()
    data["preferences"]["deprioritize_keywords"] = ["sales", "marketing", "product manager"]
    r = load_profile(_write(tmp_path, data))
    assert r["preferences"]["deprioritize_keywords"] == ["sales", "marketing", "product manager"]


def test_preferences_deprioritize_keywords_must_be_strings(tmp_path: Path) -> None:
    data = _minimal()
    data["preferences"]["deprioritize_keywords"] = ["sales", 123]
    with pytest.raises(ProfileError, match="deprioritize_keywords"):
        load_profile(_write(tmp_path, data))


def test_sources_feeds_company_from_title_accepted(tmp_path: Path) -> None:
    data = _minimal()
    data["sources"] = {
        "feeds": [
            {"name": "wwr", "url": "https://wwr.test/f.rss", "company_from_title": True}
        ]
    }
    r = load_profile(_write(tmp_path, data))
    assert r["sources"]["feeds"][0]["company_from_title"] is True
