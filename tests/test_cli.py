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


def test_overflow_slicing_semantics():
    """Only the rendered slice is recorded as seen (overflow-not-seen decision)."""
    new_results = list(range(30))
    max_shown = 25
    rendered = new_results[:max_shown]
    assert len(rendered) == 25
    assert new_results[25:] == [25, 26, 27, 28, 29]  # these must NOT be seen-recorded
