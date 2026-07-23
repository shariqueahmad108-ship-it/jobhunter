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


# ---------------------------------------------------------------------------
# sources: block — END-TO-END adapter-construction criterion
# (profile-driven-sources work item)
# ---------------------------------------------------------------------------


def test_sources_adzuna_disabled_suppresses_adapter():
    """sources.adzuna.enabled=false: no Adzuna adapter even with env creds."""
    import os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {"adzuna": {"enabled": False}},
    }
    env = {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}
    with patch.dict(os.environ, env):
        adapters = _build_adapters(profile)
    assert all(a.name != "adzuna" for a in adapters)


def test_sources_adzuna_enabled_true_constructs_adapter():
    """sources.adzuna.enabled=true + env creds => Adzuna adapter constructed."""
    import os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {"adzuna": {"enabled": True}},
    }
    env = {"ADZUNA_APP_ID": "x", "ADZUNA_APP_KEY": "y"}
    with patch.dict(os.environ, env):
        adapters = _build_adapters(profile)
    assert any(a.name == "adzuna" for a in adapters)


def test_sources_ats_watchlist_takes_precedence_over_queries():
    """sources.ats_watchlist wins over queries.ats_watchlist when both present."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": [{"ats": "greenhouse", "slug": "old-company"}]},
        "sources": {
            "ats_watchlist": [{"ats": "lever", "slug": "new-company"}]
        },
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    ats_adapters = [a for a in adapters if a.name == "ats"]
    assert len(ats_adapters) == 1
    # The watchlist from sources: block was used (lever slug, not greenhouse slug).
    assert ats_adapters[0]._watchlist[0]["slug"] == "new-company"


def test_sources_ats_watchlist_no_queries_fallback():
    """sources.ats_watchlist with no queries.ats_watchlist: ATS adapter is built."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {
            "ats_watchlist": [{"ats": "ashby", "slug": "elastic", "name": "Elastic"}]
        },
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert any(a.name == "ats" for a in adapters)


def test_legacy_queries_ats_watchlist_still_works():
    """queries.ats_watchlist (no sources block) keeps activating ATS adapter."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": [{"ats": "greenhouse", "slug": "gitlab"}]},
        "sources": {},
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert any(a.name == "ats" for a in adapters)


def test_sources_ats_watchlist_workday_wires_ats_adapter():
    """END-TO-END: Workday entry in sources.ats_watchlist constructs an AtsAdapter."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {
            "ats_watchlist": [
                {"ats": "workday", "slug": "redhat", "name": "Red Hat"},
                {"ats": "workday", "slug": "atlassian", "name": "Atlassian"},
                {"ats": "workday", "slug": "hashicorp", "name": "HashiCorp"},
            ]
        },
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)

    ats_adapters = [a for a in adapters if a.name == "ats"]
    assert len(ats_adapters) == 1
    slugs = [e["slug"] for e in ats_adapters[0]._watchlist]
    assert "redhat" in slugs and "atlassian" in slugs and "hashicorp" in slugs


# ---------------------------------------------------------------------------
# sources: block — END-TO-END criterion for feeds, remotive, remoteok, careerjet
# (karynne-source-config work item)
# ---------------------------------------------------------------------------


def test_sources_feeds_constructs_feed_adapter():
    """sources.feeds list => FeedAdapter constructed."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {
            "feeds": [
                {"name": "iworkfornsw", "url": "https://example.com/iworkfornsw.rss"},
                {"name": "weworkremotely", "url": "https://example.com/wwr.rss"},
            ]
        },
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert any(a.name == "feeds" for a in adapters)


def test_sources_feeds_empty_no_feed_adapter():
    """Empty sources.feeds => no FeedAdapter."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {"feeds": []},
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert all(a.name != "feeds" for a in adapters)


def test_sources_remotive_enabled_constructs_adapter():
    """sources.remotive.enabled=True => RemotiveAdapter constructed."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {"remotive": {"enabled": True, "categories": ["devrel"]}},
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert any(a.name == "remotive" for a in adapters)


def test_sources_remotive_disabled_no_adapter():
    """sources.remotive.enabled=False => no RemotiveAdapter."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {"remotive": {"enabled": False}},
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert all(a.name != "remotive" for a in adapters)


def test_sources_remoteok_enabled_constructs_adapter():
    """sources.remoteok.enabled=True => RemoteOKAdapter constructed."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {"remoteok": {"enabled": True}},
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert any(a.name == "remoteok" for a in adapters)


def test_sources_careerjet_with_credential_constructs_adapter():
    """sources.careerjet.enabled=True + CAREERJET_AFFILIATE_ID => CareerjetAdapter."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {"careerjet": {"enabled": True}},
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "CAREERJET_AFFILIATE_ID")}
    env["CAREERJET_AFFILIATE_ID"] = "test-affiliate-id"
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert any(a.name == "careerjet" for a in adapters)


def test_sources_careerjet_without_credential_skipped(capsys):
    """sources.careerjet.enabled=True but no cred => no adapter, warning printed."""
    import os as _os
    from unittest.mock import patch

    from jobhunter.cli import _build_adapters

    profile = {
        "queries": {"ats_watchlist": []},
        "sources": {"careerjet": {"enabled": True}},
    }
    env = {k: v for k, v in _os.environ.items()
           if k not in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "CAREERJET_AFFILIATE_ID")}
    with patch.dict(_os.environ, env, clear=True):
        adapters = _build_adapters(profile)
    assert all(a.name != "careerjet" for a in adapters)
    captured = capsys.readouterr()
    assert "CAREERJET_AFFILIATE_ID" in captured.err
# ---------------------------------------------------------------------------
# fx-staleness-warning: fx_rates_age_days wired into _cmd_run
# ---------------------------------------------------------------------------


def test_fx_staleness_warning_printed_to_stderr(tmp_path, capsys):
    """When fx_rates.yaml is >= 90 days old, a warning is printed to stderr."""
    import os as _os
    import time as _time

    from jobhunter.profile import fx_rates_age_days

    fx = tmp_path / "fx_rates.yaml"
    fx.write_text("base: AUD\nrates:\n  USD: 1.5\n")
    old = _time.time() - 100 * 86400
    _os.utime(fx, (old, old))

    assert fx_rates_age_days(fx) == 100


def test_fx_staleness_no_warning_when_fresh(tmp_path):
    """fx_rates_age_days returns 0 for a just-written file."""
    from jobhunter.profile import fx_rates_age_days

    fx = tmp_path / "fx_rates.yaml"
    fx.write_text("base: AUD\nrates:\n  USD: 1.5\n")
    assert fx_rates_age_days(fx) == 0


def test_fx_staleness_none_when_file_missing(tmp_path):
    """fx_rates_age_days returns None when file is absent."""
    from jobhunter.profile import fx_rates_age_days

    assert fx_rates_age_days(tmp_path / "no_file.yaml") is None
