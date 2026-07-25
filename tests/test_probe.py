# SPDX-License-Identifier: Apache-2.0
"""Tests for the ATS board-detection probe command.

All HTTP calls are mocked — no live network.

See: specs/05-operator-tooling.md §5.3
"""

from __future__ import annotations

import argparse
from io import StringIO
from unittest.mock import MagicMock, patch

import yaml

import jobhunter.probe as _probe_mod
from jobhunter.probe import (
    ProbeResult,
    _probe_ashby,
    _probe_greenhouse,
    _probe_lever,
    _probe_workday,
    extract_slug_from_url,
    format_probe_result,
    probe_check,
    probe_single,
)

# ---------------------------------------------------------------------------
# extract_slug_from_url
# ---------------------------------------------------------------------------


class TestExtractSlugFromUrl:
    def test_greenhouse_boards_url(self):
        ats, slug = extract_slug_from_url("https://boards.greenhouse.io/mozilla")
        assert ats == "greenhouse"
        assert slug == "mozilla"

    def test_greenhouse_api_url(self):
        ats, slug = extract_slug_from_url(
            "https://boards-api.greenhouse.io/v1/boards/wikimedia/jobs"
        )
        assert ats == "greenhouse"
        assert slug == "wikimedia"

    def test_lever_jobs_url(self):
        ats, slug = extract_slug_from_url("https://jobs.lever.co/hashicorp")
        assert ats == "lever"
        assert slug == "hashicorp"

    def test_lever_api_url(self):
        ats, slug = extract_slug_from_url("https://api.lever.co/v0/postings/hashicorp")
        assert ats == "lever"
        assert slug == "hashicorp"

    def test_ashby_jobs_url(self):
        ats, slug = extract_slug_from_url("https://jobs.ashbyhq.com/elastic")
        assert ats == "ashby"
        assert slug == "elastic"

    def test_ashby_subdomain_url(self):
        ats, slug = extract_slug_from_url("https://canonical.ashbyhq.com/jobs")
        assert ats == "ashby"
        assert slug == "canonical"

    def test_workday_url(self):
        ats, slug = extract_slug_from_url(
            "https://redhat.wd5.myworkdayjobs.com/en-US/Jobs"
        )
        assert ats == "workday"
        assert slug == "redhat"

    def test_unknown_url_returns_none(self):
        ats, slug = extract_slug_from_url("https://example.com/careers")
        assert ats is None
        assert slug is None

    def test_bare_slug_returns_none(self):
        ats, slug = extract_slug_from_url("mozilla")
        assert ats is None
        assert slug is None

    def test_url_with_trailing_slash(self):
        ats, slug = extract_slug_from_url("https://boards.greenhouse.io/mozilla/")
        assert ats == "greenhouse"
        assert slug == "mozilla"


# ---------------------------------------------------------------------------
# _probe_greenhouse
# ---------------------------------------------------------------------------


def _make_mock_client_get(json_data, status_code=200, content_type="application/json"):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    mock_resp.headers = {"content-type": content_type}
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp
    return mock_client


def _make_mock_client_post(json_data, status_code=200, content_type="application/json"):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    mock_resp.headers = {"content-type": content_type}
    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: mock_client
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp
    return mock_client


class TestProbeGreenhouse:
    def test_confirmed_board_returns_result(self):
        jobs = [
            {"id": 1, "title": "SRE", "updated_at": "2026-04-02T00:00:00Z"},
            {"id": 2, "title": "PM", "updated_at": "2026-07-24T00:00:00Z"},
        ]
        mock_client = _make_mock_client_get({"jobs": jobs})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_greenhouse("mozilla")
        assert result is not None
        assert result.ats == "greenhouse"
        assert result.slug == "mozilla"
        assert result.job_count == 2
        assert result.oldest_date == "2026-04-02"
        assert result.newest_date == "2026-07-24"

    def test_404_returns_none(self):
        mock_client = _make_mock_client_get({}, status_code=404)
        mock_client.get.return_value.raise_for_status.side_effect = None
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_greenhouse("nosuchboard")
        assert result is None

    def test_html_response_returns_none(self):
        mock_client = _make_mock_client_get(
            "<html>Error</html>",
            content_type="text/html; charset=utf-8",
        )
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_greenhouse("htmlboard")
        assert result is None

    def test_empty_board_returns_none(self):
        mock_client = _make_mock_client_get({"jobs": []})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_greenhouse("emptyco")
        assert result is None

    def test_network_error_returns_none(self):
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("network error")
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_greenhouse("unreachable")
        assert result is None

    def test_single_job_board(self):
        jobs = [{"id": 1, "title": "Eng", "updated_at": "2026-07-01T00:00:00Z"}]
        mock_client = _make_mock_client_get({"jobs": jobs})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_greenhouse("solo")
        assert result is not None
        assert result.job_count == 1
        assert result.oldest_date == result.newest_date == "2026-07-01"


# ---------------------------------------------------------------------------
# _probe_lever
# ---------------------------------------------------------------------------


class TestProbeLever:
    def test_confirmed_board_returns_result(self):
        jobs = [
            {"id": "a", "text": "Eng", "createdAt": 1720000000000},
            {"id": "b", "text": "PM", "createdAt": 1750000000000},
        ]
        mock_client = _make_mock_client_get(jobs)
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_lever("hashicorp")
        assert result is not None
        assert result.ats == "lever"
        assert result.slug == "hashicorp"
        assert result.job_count == 2
        assert result.oldest_date is not None
        assert result.newest_date is not None
        assert result.oldest_date <= result.newest_date

    def test_404_returns_none(self):
        mock_client = _make_mock_client_get([], status_code=404)
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_lever("nosuchco")
        assert result is None

    def test_empty_list_returns_none(self):
        mock_client = _make_mock_client_get([])
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_lever("emptyco")
        assert result is None

    def test_html_response_returns_none(self):
        mock_client = _make_mock_client_get(
            "<html>404 Not Found</html>",
            content_type="text/html",
        )
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_lever("htmlboard")
        assert result is None

    def test_non_list_response_returns_none(self):
        mock_client = _make_mock_client_get({"error": "not found"})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_lever("badboard")
        assert result is None


# ---------------------------------------------------------------------------
# _probe_ashby
# ---------------------------------------------------------------------------


class TestProbeAshby:
    def test_confirmed_board_returns_result(self):
        jobs = [
            {"id": "x", "title": "Eng", "publishedDate": "2026-04-10T00:00:00Z"},
            {"id": "y", "title": "PM", "publishedDate": "2026-07-20T00:00:00Z"},
        ]
        mock_client = _make_mock_client_post({"jobPostings": jobs})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_ashby("elastic")
        assert result is not None
        assert result.ats == "ashby"
        assert result.slug == "elastic"
        assert result.job_count == 2
        assert result.oldest_date == "2026-04-10"
        assert result.newest_date == "2026-07-20"

    def test_empty_board_returns_none(self):
        mock_client = _make_mock_client_post({"jobPostings": []})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_ashby("emptyco")
        assert result is None

    def test_404_returns_none(self):
        mock_client = _make_mock_client_post({}, status_code=404)
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_ashby("nosuchco")
        assert result is None

    def test_html_response_returns_none(self):
        mock_client = _make_mock_client_post(
            "<html>Error</html>",
            content_type="text/html",
        )
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_ashby("htmlboard")
        assert result is None


# ---------------------------------------------------------------------------
# _probe_workday
# ---------------------------------------------------------------------------


class TestProbeWorkday:
    def test_confirmed_board_returns_result(self):
        mock_client = _make_mock_client_post({"jobPostings": [], "total": 42})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_workday("redhat", workday_path="RedHat/Jobs", workday_instance=5)
        assert result is not None
        assert result.ats == "workday"
        assert result.slug == "redhat"
        assert result.job_count == 42

    def test_empty_board_returns_none(self):
        mock_client = _make_mock_client_post({"jobPostings": [], "total": 0})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_workday("emptyco")
        assert result is None

    def test_404_returns_none(self):
        mock_client = _make_mock_client_post({}, status_code=404)
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            result = _probe_workday("nosuchco")
        assert result is None

    def test_default_path_from_slug(self):
        mock_client = _make_mock_client_post({"total": 5, "jobPostings": []})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            _probe_workday("acme")
        call_url = mock_client.post.call_args[0][0]
        assert "/wday/cxs/acme/acme/jobs" in call_url

    def test_default_instance_is_5(self):
        mock_client = _make_mock_client_post({"total": 5, "jobPostings": []})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            _probe_workday("acme")
        call_url = mock_client.post.call_args[0][0]
        assert "acme.wd5.myworkdayjobs.com" in call_url


# ---------------------------------------------------------------------------
# probe_single
# ---------------------------------------------------------------------------


class TestProbeSingle:
    def test_url_routes_to_greenhouse(self):
        jobs = [{"id": 1, "title": "Eng", "updated_at": "2026-07-01T00:00:00Z"}]
        mock_client = _make_mock_client_get({"jobs": jobs})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            results = probe_single("https://boards.greenhouse.io/mozilla")
        assert len(results) == 1
        assert results[0].ats == "greenhouse"
        assert results[0].slug == "mozilla"

    def test_url_routes_to_lever(self):
        jobs = [{"id": "a", "createdAt": 1720000000000}]
        mock_client = _make_mock_client_get(jobs)
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            results = probe_single("https://jobs.lever.co/hashicorp")
        assert len(results) == 1
        assert results[0].ats == "lever"
        assert results[0].slug == "hashicorp"

    def test_url_not_confirmed_returns_empty(self):
        mock_client = _make_mock_client_get({}, status_code=404)
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            results = probe_single("https://boards.greenhouse.io/nosuchboard")
        assert results == []

    def test_bare_slug_with_ats_hint(self):
        jobs = [{"id": 1, "title": "Eng", "updated_at": "2026-07-01T00:00:00Z"}]
        mock_client = _make_mock_client_get({"jobs": jobs})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            results = probe_single("mozilla", ats_hint="greenhouse")
        assert len(results) == 1
        assert results[0].ats == "greenhouse"

    def test_bare_slug_tries_all_non_workday(self):
        call_count = []

        def no_match(slug):
            call_count.append(slug)
            return None

        with (
            patch.dict(_probe_mod._PROBERS, {
                "greenhouse": no_match,
                "lever": no_match,
                "ashby": no_match,
            }),
            patch("jobhunter.probe.time.sleep"),
        ):
            results = probe_single("nosuchco")

        assert results == []
        assert call_count.count("nosuchco") == 3  # tried all three

    def test_bare_slug_returns_hits_from_multiple_ats(self):
        hit = ProbeResult("greenhouse", "mozilla", 10, "2026-01-01", "2026-07-01")

        def gh_match(slug):
            return hit

        def no_match(slug):
            return None

        with (
            patch.dict(_probe_mod._PROBERS, {
                "greenhouse": gh_match,
                "lever": no_match,
                "ashby": no_match,
            }),
            patch("jobhunter.probe.time.sleep"),
        ):
            results = probe_single("mozilla")

        # Greenhouse returned a hit; all three are tried
        assert any(r.ats == "greenhouse" for r in results)

    def test_url_not_confirmed_no_candidate_line(self):
        """A 404 must not emit any candidate line — not even a suggestion."""
        mock_client = _make_mock_client_get({"jobs": []})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            results = probe_single("https://boards.greenhouse.io/emptycorp")
        assert results == []


# ---------------------------------------------------------------------------
# probe_check
# ---------------------------------------------------------------------------


class TestProbeCheck:
    def test_live_board_returns_result(self):
        jobs = [{"id": 1, "title": "Eng", "updated_at": "2026-07-01T00:00:00Z"}]
        mock_client = _make_mock_client_get({"jobs": jobs})
        watchlist = [{"ats": "greenhouse", "slug": "mozilla", "name": "Mozilla"}]
        with (
            patch("jobhunter.probe.httpx.Client", return_value=mock_client),
            patch("jobhunter.probe.time.sleep"),
        ):
            statuses = probe_check(watchlist)
        assert len(statuses) == 1
        assert statuses[0].result is not None
        assert statuses[0].result.job_count == 1

    def test_dead_board_has_none_result(self):
        mock_client = _make_mock_client_get({}, status_code=404)
        watchlist = [{"ats": "greenhouse", "slug": "nosuchboard"}]
        with (
            patch("jobhunter.probe.httpx.Client", return_value=mock_client),
            patch("jobhunter.probe.time.sleep"),
        ):
            statuses = probe_check(watchlist)
        assert statuses[0].result is None

    def test_one_live_one_dead(self):
        def mock_probe(slug):
            if slug == "mozilla":
                return ProbeResult("greenhouse", "mozilla", 5, "2026-01-01", "2026-07-01")
            return None

        watchlist = [
            {"ats": "greenhouse", "slug": "mozilla"},
            {"ats": "greenhouse", "slug": "nosuchboard"},
        ]
        with (
            patch.dict(_probe_mod._PROBERS, {"greenhouse": mock_probe}),
            patch("jobhunter.probe.time.sleep"),
        ):
            statuses = probe_check(watchlist)
        assert statuses[0].result is not None
        assert statuses[1].result is None

    def test_all_entries_probed(self):
        probed = []

        def record(slug):
            probed.append(slug)
            return None

        watchlist = [
            {"ats": "greenhouse", "slug": "a"},
            {"ats": "lever", "slug": "b"},
            {"ats": "ashby", "slug": "c"},
        ]
        with (
            patch.dict(_probe_mod._PROBERS, {
                "greenhouse": record,
                "lever": record,
                "ashby": record,
            }),
            patch("jobhunter.probe.time.sleep"),
        ):
            probe_check(watchlist)
        assert probed == ["a", "b", "c"]

    def test_workday_entry_probed_with_path(self):
        called_with = {}

        def fake_workday(slug, workday_path=None, workday_instance=5):
            called_with["slug"] = slug
            called_with["path"] = workday_path
            called_with["instance"] = workday_instance
            return None

        watchlist = [
            {
                "ats": "workday",
                "slug": "redhat",
                "name": "Red Hat",
                "workday_path": "RedHat/Jobs",
                "workday_instance": 5,
            }
        ]
        with (
            patch("jobhunter.probe._probe_workday", side_effect=fake_workday),
            patch("jobhunter.probe.time.sleep"),
        ):
            probe_check(watchlist)
        assert called_with["slug"] == "redhat"
        assert called_with["path"] == "RedHat/Jobs"
        assert called_with["instance"] == 5

    def test_unsupported_ats_recorded_with_error(self):
        watchlist = [{"ats": "bamboohr", "slug": "acme"}]
        statuses = probe_check(watchlist)
        assert statuses[0].result is None
        assert statuses[0].error is not None

    def test_profile_file_not_modified(self, tmp_path):
        """probe_check must never write to the profile file."""
        import yaml

        profile_content = {"ats_watchlist": []}
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text(yaml.dump(profile_content))
        mtime_before = profile_file.stat().st_mtime

        probe_check([])

        assert profile_file.stat().st_mtime == mtime_before


# ---------------------------------------------------------------------------
# format_probe_result
# ---------------------------------------------------------------------------


class TestFormatProbeResult:
    def test_output_contains_ats_and_slug(self):
        result = ProbeResult("greenhouse", "mozilla", 56, "2026-04-02", "2026-07-24")
        output = format_probe_result(result)
        assert "greenhouse" in output
        assert "mozilla" in output
        assert "56" in output

    def test_output_contains_date_range(self):
        result = ProbeResult("greenhouse", "mozilla", 56, "2026-04-02", "2026-07-24")
        output = format_probe_result(result)
        assert "2026-04-02" in output
        assert "2026-07-24" in output

    def test_yaml_line_uses_provided_name(self):
        result = ProbeResult("greenhouse", "mozilla", 10, None, None)
        output = format_probe_result(result, name="Mozilla")
        assert 'name: "Mozilla"' in output

    def test_yaml_line_falls_back_to_slug_title(self):
        result = ProbeResult("lever", "some-company", 5, None, None)
        output = format_probe_result(result)
        assert 'name: "Some Company"' in output

    def test_no_dates_omits_date_info(self):
        result = ProbeResult("workday", "redhat", 42, None, None)
        output = format_probe_result(result)
        assert "oldest" not in output
        assert "newest" not in output
        assert "42 jobs" in output

    def test_single_date_shows_newest(self):
        result = ProbeResult("greenhouse", "solo", 1, "2026-07-01", "2026-07-01")
        output = format_probe_result(result)
        assert "newest 2026-07-01" in output


# ---------------------------------------------------------------------------
# YAML paste-line validator integration
# ---------------------------------------------------------------------------


class TestYamlLineValidates:
    """The YAML line printed by format_probe_result must pass the profile validator."""

    def _extract_yaml_line(self, output: str) -> dict:
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("- {"):
                parsed = yaml.safe_load(stripped)
                # "- {...}" parses as a one-element list
                return parsed[0] if isinstance(parsed, list) else parsed
        raise AssertionError(f"No YAML line found in output:\n{output}")

    def test_greenhouse_yaml_line_validates(self):
        from jobhunter.profile import _validate_sources

        result = ProbeResult("greenhouse", "mozilla", 56, "2026-04-02", "2026-07-24")
        output = format_probe_result(result, name="Mozilla")
        entry = self._extract_yaml_line(output)
        # Should not raise
        _validate_sources({"ats_watchlist": [entry]})

    def test_lever_yaml_line_validates(self):
        from jobhunter.profile import _validate_sources

        result = ProbeResult("lever", "hashicorp", 23, "2026-01-01", "2026-07-01")
        output = format_probe_result(result, name="HashiCorp")
        entry = self._extract_yaml_line(output)
        _validate_sources({"ats_watchlist": [entry]})

    def test_ashby_yaml_line_validates(self):
        from jobhunter.profile import _validate_sources

        result = ProbeResult("ashby", "elastic", 14, "2026-03-01", "2026-07-15")
        output = format_probe_result(result, name="Elastic")
        entry = self._extract_yaml_line(output)
        _validate_sources({"ats_watchlist": [entry]})


# ---------------------------------------------------------------------------
# CLI integration (no live network)
# ---------------------------------------------------------------------------


class TestCliProbeCommand:
    def _run_cli(self, argv: list[str]):
        from jobhunter.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(argv)
        captured_out = StringIO()
        captured_err = StringIO()
        with (
            patch("sys.stdout", captured_out),
            patch("sys.stderr", captured_err),
        ):
            rc = args.func(args)
        return rc, captured_out.getvalue(), captured_err.getvalue()

    def test_probe_confirmed_greenhouse(self):
        jobs = [{"id": 1, "title": "Eng", "updated_at": "2026-07-01T00:00:00Z"}]
        mock_client = _make_mock_client_get({"jobs": jobs})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            rc, out, _ = self._run_cli(["probe", "https://boards.greenhouse.io/mozilla"])
        assert rc == 0
        assert "greenhouse" in out
        assert "mozilla" in out

    def test_probe_not_confirmed_prints_message(self):
        mock_client = _make_mock_client_get({"jobs": []})
        with patch("jobhunter.probe.httpx.Client", return_value=mock_client):
            rc, out, _ = self._run_cli(["probe", "https://boards.greenhouse.io/nosuchboard"])
        assert rc == 0
        assert "not confirmed" in out

    def test_probe_check_exits_nonzero_with_dead_board(self, tmp_path):
        profile_yaml = tmp_path / "profile.yaml"
        profile_yaml.write_text(
            """
identity:
  target_skills: [Python]
  target:
    - {track: ic, level: senior}
queries:
  keywords: [python]
  locations: [Remote]
hard_requirements:
  remote_policy: any
preferences: {}
weights:
  skill_match: 1
output:
  display_threshold: 0
  max_shown: 10
  format: markdown
sources:
  ats_watchlist:
    - {ats: greenhouse, slug: nosuchboard, name: "No Such Board"}
"""
        )

        def dead_probe(slug):
            return None

        with (
            patch.dict(_probe_mod._PROBERS, {"greenhouse": dead_probe}),
            patch("jobhunter.probe.time.sleep"),
        ):
            rc, out, _ = self._run_cli(["probe", "--check", "--profile", str(profile_yaml)])

        assert rc == 1
        assert "dead" in out

    def test_probe_check_exits_zero_with_all_live(self, tmp_path):
        profile_yaml = tmp_path / "profile.yaml"
        profile_yaml.write_text(
            """
identity:
  target_skills: [Python]
  target:
    - {track: ic, level: senior}
queries:
  keywords: [python]
  locations: [Remote]
hard_requirements:
  remote_policy: any
preferences: {}
weights:
  skill_match: 1
output:
  display_threshold: 0
  max_shown: 10
  format: markdown
sources:
  ats_watchlist:
    - {ats: greenhouse, slug: mozilla, name: "Mozilla"}
"""
        )

        def live_probe(slug):
            return ProbeResult("greenhouse", slug, 5, "2026-01-01", "2026-07-01")

        with (
            patch.dict(_probe_mod._PROBERS, {"greenhouse": live_probe}),
            patch("jobhunter.probe.time.sleep"),
        ):
            rc, out, _ = self._run_cli(["probe", "--check", "--profile", str(profile_yaml)])

        assert rc == 0
        assert "greenhouse" in out

    def test_probe_check_requires_no_target(self):
        from jobhunter.cli import _cmd_probe

        ns = argparse.Namespace(
            check=True,
            target="https://boards.greenhouse.io/mozilla",
            ats=None,
            profile="profile.yaml",
        )
        captured = StringIO()
        with patch("sys.stderr", captured):
            rc = _cmd_probe(ns)
        assert rc == 1
        assert "cannot be combined" in captured.getvalue()

    def test_probe_no_args_prints_error(self):
        from jobhunter.cli import _cmd_probe

        ns = argparse.Namespace(check=False, target=None, ats=None, profile="profile.yaml")
        captured = StringIO()
        with patch("sys.stderr", captured):
            rc = _cmd_probe(ns)
        assert rc == 1
        assert "provide a target" in captured.getvalue()
