# SPDX-License-Identifier: Apache-2.0
"""Profile loading and schema validation.

Loads a profile.yaml and validates it against the schema in specs/03-data-model.md.
Unknown keys and type mismatches are hard errors ("fail loud on typos").

See: specs/03-data-model.md §Profile
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from jobhunter.model import IC_LEVELS as _IC_LEVELS
from jobhunter.model import MANAGEMENT_LEVELS as _MGMT_LEVELS

_REMOTE_POLICIES = {"remote_only", "hybrid_ok", "onsite_ok", "any"}
_EMPLOYMENT_TYPES = {"full_time", "part_time", "contract", "temp", "internship"}
_KEYWORD_SCOPES = {"title", "requirements"}
_OUTPUT_FORMATS = {"markdown", "html", "both"}
_DATA_FORMATS = {"json", "csv", "both"}
_TRACKS = {"ic", "management"}

_TOP_LEVEL_KEYS = {
    "identity",
    "queries",
    "hard_requirements",
    "preferences",
    "weights",
    "output",
    "search_mode",
}

# Posture presets (spec 02 §Search posture): defaults only — explicit values win.
_SEARCH_MODES: dict[str, dict] = {
    "active_unemployed": {"display_threshold": 40, "max_shown": 40, "max_age_days": 30},
    "active_employed": {"display_threshold": 55, "max_shown": 25, "max_age_days": 21},
    "passive_employed": {"display_threshold": 70, "max_shown": 10, "max_age_days": 14},
}


class ProfileError(ValueError):
    """Raised when a profile.yaml fails schema validation."""


def _is_number(v) -> bool:
    """True for int/float but NOT bool (isinstance(True, int) is True in Python)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _require(obj: dict, key: str, path: str) -> Any:
    if key not in obj:
        raise ProfileError(f"Missing required field: {path}.{key}")
    return obj[key]


def _unknown_keys(obj: dict, allowed: set[str], path: str) -> None:
    extra = set(obj) - allowed
    if extra:
        raise ProfileError(f"Unknown key(s) at {path}: {sorted(extra)}")


def _expect_type(value: Any, expected: type, path: str) -> None:
    if not isinstance(value, expected):
        raise ProfileError(f"{path}: expected {expected.__name__}, got {type(value).__name__}")


def _expect_list_of_strings(value: Any, path: str) -> None:
    _expect_type(value, list, path)
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ProfileError(f"{path}[{i}]: expected string, got {type(item).__name__}")


def _validate_identity(identity: dict) -> None:
    _unknown_keys(identity, {"target_skills", "target"}, "identity")
    skills = _require(identity, "target_skills", "identity")
    _expect_list_of_strings(skills, "identity.target_skills")
    if not skills:
        raise ProfileError("identity.target_skills must not be empty")

    targets = _require(identity, "target", "identity")
    _expect_type(targets, list, "identity.target")
    if not targets:
        raise ProfileError("identity.target must have at least one entry")
    for i, t in enumerate(targets):
        _expect_type(t, dict, f"identity.target[{i}]")
        _unknown_keys(t, {"track", "level"}, f"identity.target[{i}]")
        track = _require(t, "track", f"identity.target[{i}]")
        if track not in _TRACKS:
            raise ProfileError(
                f"identity.target[{i}].track: must be one of {sorted(_TRACKS)}, got {track!r}"
            )
        level = _require(t, "level", f"identity.target[{i}]")
        _expect_type(level, str, f"identity.target[{i}].level")
        _valid_level = _IC_LEVELS if track == "ic" else _MGMT_LEVELS
        if level not in _valid_level:
            raise ProfileError(
                f"identity.target[{i}].level: {level!r} is not valid for track {track!r}; "
                f"valid: {_valid_level}"
            )


def _validate_queries(queries: dict) -> None:
    _unknown_keys(
        queries,
        {"keywords", "locations", "max_results_per_query", "max_requests_per_run"},
        "queries",
    )
    kw = _require(queries, "keywords", "queries")
    _expect_list_of_strings(kw, "queries.keywords")
    if not kw:
        raise ProfileError("queries.keywords must not be empty")
    locs = _require(queries, "locations", "queries")
    _expect_list_of_strings(locs, "queries.locations")
    if not locs:
        raise ProfileError("queries.locations must not be empty")
    if "max_results_per_query" in queries and not _is_int(queries["max_results_per_query"]):
        raise ProfileError("queries.max_results_per_query: expected int")
    if "max_requests_per_run" in queries and not _is_int(queries["max_requests_per_run"]):
        raise ProfileError("queries.max_requests_per_run: expected int")


def _validate_seniority_bounds(seniority: dict, path: str) -> None:
    _unknown_keys(seniority, {"ic", "management"}, path)
    for track in ("ic", "management"):
        if track not in seniority:
            continue
        bounds = seniority[track]
        if bounds is None:
            continue
        _expect_type(bounds, dict, f"{path}.{track}")
        _unknown_keys(bounds, {"min", "max"}, f"{path}.{track}")
        min_level = _require(bounds, "min", f"{path}.{track}")
        _expect_type(min_level, str, f"{path}.{track}.min")
        valid = _IC_LEVELS if track == "ic" else _MGMT_LEVELS
        if min_level not in valid:
            raise ProfileError(f"{path}.{track}.min: {min_level!r} not valid; valid: {valid}")
        max_level = bounds.get("max")
        if max_level is not None:
            _expect_type(max_level, str, f"{path}.{track}.max")
            if max_level not in valid:
                raise ProfileError(f"{path}.{track}.max: {max_level!r} not valid; valid: {valid}")
            if valid.index(min_level) > valid.index(max_level):
                raise ProfileError(
                    f"{path}.{track}: min ({min_level!r}) must be ≤ max ({max_level!r})"
                )


def _validate_hard_requirements(hr: dict) -> None:
    allowed = {
        "remote_policy",
        "exclude_locations",
        "locations_allowed",
        "seniority",
        "salary_floor",
        "salary_currency",
        "fx_rates",
        "keep_unknown_salary",
        "exclude_employment",
        "exclude_keywords",
        "require_keywords",
        "max_age_days",
    }
    _unknown_keys(hr, allowed, "hard_requirements")

    rp = _require(hr, "remote_policy", "hard_requirements")
    if rp not in _REMOTE_POLICIES:
        raise ProfileError(
            f"hard_requirements.remote_policy: must be one of {sorted(_REMOTE_POLICIES)}, "
            f"got {rp!r}"
        )

    excl_locs = hr.get("exclude_locations", [])
    _expect_list_of_strings(excl_locs, "hard_requirements.exclude_locations")

    locs_allowed = hr.get("locations_allowed", [])
    _expect_list_of_strings(locs_allowed, "hard_requirements.locations_allowed")

    if "seniority" in hr and hr["seniority"] is not None:
        _expect_type(hr["seniority"], dict, "hard_requirements.seniority")
        _validate_seniority_bounds(hr["seniority"], "hard_requirements.seniority")

    salary_floor = hr.get("salary_floor")
    if salary_floor is not None:
        if not _is_number(salary_floor):
            raise ProfileError("hard_requirements.salary_floor: expected number")
        _require(hr, "salary_currency", "hard_requirements")

    if "salary_currency" in hr:
        _expect_type(hr["salary_currency"], str, "hard_requirements.salary_currency")

    fx = hr.get("fx_rates", {})
    if fx is not None:
        _expect_type(fx, dict, "hard_requirements.fx_rates")
        for k, v in fx.items():
            if not isinstance(k, str):
                raise ProfileError(
                    f"hard_requirements.fx_rates key must be string, got {type(k).__name__}"
                )
            if not _is_number(v):
                raise ProfileError(
                    f"hard_requirements.fx_rates.{k}: expected number, got {type(v).__name__}"
                )

    if "keep_unknown_salary" in hr:
        _expect_type(hr["keep_unknown_salary"], bool, "hard_requirements.keep_unknown_salary")

    excl_emp = hr.get("exclude_employment", [])
    _expect_type(excl_emp, list, "hard_requirements.exclude_employment")
    for i, e in enumerate(excl_emp):
        if e not in _EMPLOYMENT_TYPES:
            raise ProfileError(
                f"hard_requirements.exclude_employment[{i}]: {e!r} "
                f"not in {sorted(_EMPLOYMENT_TYPES)}"
            )

    excl_kw = hr.get("exclude_keywords", [])
    _expect_type(excl_kw, list, "hard_requirements.exclude_keywords")
    for i, kw in enumerate(excl_kw):
        _expect_type(kw, dict, f"hard_requirements.exclude_keywords[{i}]")
        _unknown_keys(kw, {"term", "scope"}, f"hard_requirements.exclude_keywords[{i}]")
        _require(kw, "term", f"hard_requirements.exclude_keywords[{i}]")
        _expect_type(kw["term"], str, f"hard_requirements.exclude_keywords[{i}].term")
        if "scope" in kw:
            if kw["scope"] not in _KEYWORD_SCOPES:
                raise ProfileError(
                    f"hard_requirements.exclude_keywords[{i}].scope: must be one of "
                    f"{sorted(_KEYWORD_SCOPES)}, got {kw['scope']!r}"
                )

    req_kw = hr.get("require_keywords", [])
    _expect_type(req_kw, list, "hard_requirements.require_keywords")
    for i, kw in enumerate(req_kw):
        _expect_type(kw, dict, f"hard_requirements.require_keywords[{i}]")
        _unknown_keys(kw, {"term", "scope"}, f"hard_requirements.require_keywords[{i}]")
        _require(kw, "term", f"hard_requirements.require_keywords[{i}]")
        _expect_type(kw["term"], str, f"hard_requirements.require_keywords[{i}].term")
        if "scope" in kw and kw["scope"] not in _KEYWORD_SCOPES:
            raise ProfileError(
                f"hard_requirements.require_keywords[{i}].scope: must be one of "
                f"{sorted(_KEYWORD_SCOPES)}, got {kw['scope']!r}"
            )

    if "max_age_days" in hr:
        if not _is_number(hr["max_age_days"]):
            raise ProfileError("hard_requirements.max_age_days: expected number")


def _validate_preferences(prefs: dict) -> None:
    _unknown_keys(
        prefs, {"preferred_locations", "salary_target", "preferred_companies"}, "preferences"
    )
    if "preferred_locations" in prefs:
        _expect_list_of_strings(prefs["preferred_locations"], "preferences.preferred_locations")
    if "salary_target" in prefs and prefs["salary_target"] is not None:
        if not _is_number(prefs["salary_target"]):
            raise ProfileError("preferences.salary_target: expected number or null")
    if "preferred_companies" in prefs:
        _expect_list_of_strings(prefs["preferred_companies"], "preferences.preferred_companies")


def _validate_weights(weights: dict) -> None:
    allowed = {
        "skill_match",
        "seniority_fit",
        "compensation",
        "location_fit",
        "company_signal",
        "recency",
    }
    _unknown_keys(weights, allowed, "weights")
    for k in allowed:
        if k in weights:
            if not _is_number(weights[k]):
                raise ProfileError(f"weights.{k}: expected number, got {type(weights[k]).__name__}")
            if weights[k] < 0:
                raise ProfileError(f"weights.{k}: negative weights are invalid")
    if not any(_is_number(v) and v > 0 for v in weights.values()):
        raise ProfileError(
            "weights: at least one positive weight is required (spec 03 — an "
            "all-zero/empty weights block would make every score undefined)"
        )


def _validate_output(output: dict) -> None:
    _unknown_keys(
        output,
        {"display_threshold", "max_shown", "show_previously_seen", "format", "data_format"},
        "output",
    )
    if "display_threshold" in output:
        if not _is_number(output["display_threshold"]):
            raise ProfileError("output.display_threshold: expected number")
    if "max_shown" in output:
        if not _is_number(output["max_shown"]):
            raise ProfileError("output.max_shown: expected number")
    if "show_previously_seen" in output:
        _expect_type(output["show_previously_seen"], bool, "output.show_previously_seen")
    if "format" in output:
        if output["format"] not in _OUTPUT_FORMATS:
            raise ProfileError(
                f"output.format: must be one of {sorted(_OUTPUT_FORMATS)}, got {output['format']!r}"
            )
    if "data_format" in output:
        if output["data_format"] not in _DATA_FORMATS:
            raise ProfileError(
                f"output.data_format: must be one of {sorted(_DATA_FORMATS)}, "
                f"got {output['data_format']!r}"
            )


def load_profile(path: str | Path) -> dict:
    """Load and validate a profile.yaml.

    Returns the profile dict with defaults applied.
    Raises ProfileError on any schema violation (unknown keys, type errors, invalid values).
    """
    path = Path(path)
    if not path.exists():
        raise ProfileError(f"Profile not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ProfileError("Profile must be a YAML mapping at the top level")

    _unknown_keys(raw, _TOP_LEVEL_KEYS, "<profile>")

    mode = raw.get("search_mode")
    if mode is not None and mode not in _SEARCH_MODES:
        raise ProfileError(
            f"search_mode: must be one of {sorted(_SEARCH_MODES)} or omitted, got {mode!r}"
        )

    _validate_identity(_require(raw, "identity", "<profile>"))
    _validate_queries(_require(raw, "queries", "<profile>"))
    _validate_hard_requirements(_require(raw, "hard_requirements", "<profile>"))
    _validate_preferences(_require(raw, "preferences", "<profile>"))
    _validate_weights(_require(raw, "weights", "<profile>"))
    _validate_output(_require(raw, "output", "<profile>"))

    # Apply posture preset FIRST (fills only unset knobs), then schema defaults
    if mode is not None:
        preset = _SEARCH_MODES[mode]
        raw["hard_requirements"].setdefault("max_age_days", preset["max_age_days"])
        raw["output"].setdefault("display_threshold", preset["display_threshold"])
        raw["output"].setdefault("max_shown", preset["max_shown"])

    # Apply defaults
    q = raw["queries"]
    q.setdefault("max_results_per_query", 50)
    q.setdefault("max_requests_per_run", 100)

    hr = raw["hard_requirements"]
    hr.setdefault("exclude_locations", [])
    hr.setdefault("locations_allowed", [])
    hr.setdefault("keep_unknown_salary", True)
    hr.setdefault("exclude_employment", [])
    hr.setdefault("exclude_keywords", [])
    hr.setdefault("require_keywords", [])
    for kw in hr["require_keywords"]:
        kw.setdefault("scope", "requirements")
    hr.setdefault("max_age_days", 30)
    for kw in hr["exclude_keywords"]:
        kw.setdefault("scope", "requirements")

    out = raw["output"]
    out.setdefault("display_threshold", 0)
    out.setdefault("max_shown", 25)
    out.setdefault("show_previously_seen", True)
    out.setdefault("format", "markdown")
    out.setdefault("data_format", "json")

    return raw
