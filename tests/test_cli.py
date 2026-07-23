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
