# SPDX-License-Identifier: Apache-2.0
"""Smoke test: import the package and validate the example profile.

Validation: python -m pytest tests/ -q
(project-scaffold acceptance criterion)
"""

from pathlib import Path

import pytest

SPECS_DIR = Path(__file__).parent.parent / "specs"
EXAMPLE_PROFILE = SPECS_DIR / "profile.example.yaml"


def test_package_imports() -> None:
    """All pipeline-stage modules import without error."""
    import jobhunter  # noqa: F401
    from jobhunter import (  # noqa: F401
        cli,
        dedupe,
        digest,
        filter,
        ingest,
        model,
        normalize,
        profile,
        rank,
        score,
    )


def test_example_profile_validates() -> None:
    """specs/profile.example.yaml validates against the profile schema without error."""
    from jobhunter.profile import load_profile

    assert EXAMPLE_PROFILE.exists(), f"Example profile not found at {EXAMPLE_PROFILE}"
    result = load_profile(EXAMPLE_PROFILE)

    # Structural checks
    assert isinstance(result, dict)
    assert "identity" in result
    assert "queries" in result
    assert "hard_requirements" in result
    assert "preferences" in result
    assert "weights" in result
    assert "output" in result


def test_example_profile_defaults_applied() -> None:
    """Defaults are filled in when optional fields are absent."""
    from jobhunter.profile import load_profile

    result = load_profile(EXAMPLE_PROFILE)
    q = result["queries"]
    assert q["max_results_per_query"] == 50
    assert q["max_requests_per_run"] == 100

    hr = result["hard_requirements"]
    assert isinstance(hr["exclude_locations"], list)
    assert isinstance(hr["locations_allowed"], list)
    assert hr["keep_unknown_salary"] is True
    assert isinstance(hr["exclude_employment"], list)
    assert isinstance(hr["exclude_keywords"], list)
    assert hr["max_age_days"] == 21  # profile.example.yaml sets 21, not the default 30


def test_profile_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    """Unknown top-level keys raise ProfileError."""
    import yaml

    from jobhunter.profile import ProfileError, load_profile

    bad = {
        "identity": {"target_skills": ["Python"], "target": [{"track": "ic", "level": "senior"}]},
        "queries": {"keywords": ["engineer"], "locations": ["Remote"]},
        "hard_requirements": {"remote_policy": "remote_only"},
        "preferences": {},
        "weights": {},
        "output": {},
        "typo_key": "oops",
    }
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump(bad))
    with pytest.raises(ProfileError, match="Unknown key"):
        load_profile(p)


def test_profile_rejects_invalid_remote_policy(tmp_path: Path) -> None:
    """Invalid remote_policy values raise ProfileError."""
    import yaml

    from jobhunter.profile import ProfileError, load_profile

    bad = {
        "identity": {"target_skills": ["Python"], "target": [{"track": "ic", "level": "senior"}]},
        "queries": {"keywords": ["engineer"], "locations": ["Remote"]},
        "hard_requirements": {"remote_policy": "office_only"},
        "preferences": {},
        "weights": {},
        "output": {},
    }
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump(bad))
    with pytest.raises(ProfileError, match="remote_policy"):
        load_profile(p)


def test_profile_missing_required_field(tmp_path: Path) -> None:
    """Missing required identity.target_skills raises ProfileError."""
    import yaml

    from jobhunter.profile import ProfileError, load_profile

    bad = {
        "identity": {"target": [{"track": "ic", "level": "senior"}]},  # missing target_skills
        "queries": {"keywords": ["engineer"], "locations": ["Remote"]},
        "hard_requirements": {"remote_policy": "remote_only"},
        "preferences": {},
        "weights": {},
        "output": {},
    }
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.dump(bad))
    with pytest.raises(ProfileError, match="target_skills"):
        load_profile(p)
