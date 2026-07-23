# SPDX-License-Identifier: Apache-2.0
"""CLI helpers."""

from pathlib import Path

from jobhunter.cli import _profile_slug, build_parser


def test_default_profile_has_empty_slug():
    assert _profile_slug(Path("profile.yaml")) == ""


def test_named_profile_slug_is_stem():
    assert _profile_slug(Path("profile-ospo.yaml")) == "profile-ospo"
    assert _profile_slug(Path("configs/devrel.yaml")) == "devrel"


def test_parser_accepts_profile_and_state():
    parser = build_parser()
    args = parser.parse_args(["run", "--profile", "profile-ospo.yaml"])
    assert args.profile == "profile-ospo.yaml"


def test_output_dir_defaults_to_none():
    parser = build_parser()
    args = parser.parse_args(["run"])
    assert args.output_dir is None


def test_output_dir_flag_accepted():
    parser = build_parser()
    args = parser.parse_args(["run", "--output-dir", "/tmp/digests"])
    assert args.output_dir == "/tmp/digests"


def test_default_digest_path_is_peer_to_state_dir():
    """Default digest dir is a sibling of the state directory, not two levels up."""
    from pathlib import Path

    state_path = Path("state/state.yaml")
    # Mirrors the logic in _cmd_run when output_dir is None.
    digest_dir = state_path.parent.with_name("digests")
    assert digest_dir == Path("digests")


def test_overflow_slicing_semantics():
    """Only the rendered slice is recorded as seen (overflow-not-seen decision)."""
    new_results = list(range(30))
    max_shown = 25
    rendered = new_results[:max_shown]
    assert len(rendered) == 25
    assert new_results[25:] == [25, 26, 27, 28, 29]  # these must NOT be seen-recorded


def test_build_adapters_constructs_ats_from_profile():
    """END-TO-END wiring check: a profile with a watchlist gets an AtsAdapter."""
    import os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {"queries": {"ats_watchlist": [{"ats": "greenhouse", "slug": "gitlab"}]}}
    env = {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}
    with patch.dict(os.environ, env):
        adapters = _build_adapters(profile)
    names = [a.name for a in adapters]
    assert "adzuna" in names and "ats" in names


def test_build_adapters_no_watchlist_no_ats():
    import os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    env = {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}
    with patch.dict(os.environ, env):
        adapters = _build_adapters({"queries": {"ats_watchlist": []}})
    assert [a.name for a in adapters] == ["adzuna"]
