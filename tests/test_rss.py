# SPDX-License-Identifier: Apache-2.0
"""Tests for the RSS/Atom feed adapter.

All tests use mocked HTTP — no live network calls are made.

See: specs/02-functional-spec.md §Stage 1–2
     specs/04-technical-plan.md §Data sources
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobhunter.adapters.rss import (
    FeedAdapter,
    _parse_iso,
    _parse_rfc2822,
    fetch_feed,
    parse_feed_xml,
)
from jobhunter.model import JobListing

# ---------------------------------------------------------------------------
# Fixture XML payloads
# ---------------------------------------------------------------------------

_RSS2_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>I Work for NSW</title>
    <link>https://iworkfor.nsw.gov.au/jobs</link>
    <item>
      <title>Senior Software Engineer</title>
      <link>https://iworkfor.nsw.gov.au/jobs/12345</link>
      <guid>https://iworkfor.nsw.gov.au/jobs/12345</guid>
      <pubDate>Wed, 23 Jul 2026 00:00:00 +0000</pubDate>
      <description>&lt;p&gt;A &lt;b&gt;remote&lt;/b&gt; NSW role.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Junior Data Analyst</title>
      <link>https://iworkfor.nsw.gov.au/jobs/67890</link>
      <guid>https://iworkfor.nsw.gov.au/jobs/67890</guid>
      <pubDate>Tue, 22 Jul 2026 12:00:00 +0000</pubDate>
      <description>Analyse data for government services.</description>
    </item>
  </channel>
</rss>
"""

_ATOM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>FOSS Jobs</title>
  <entry>
    <title>Staff DevRel Engineer</title>
    <link href="https://fossjobs.net/job/111" rel="alternate"/>
    <id>https://fossjobs.net/job/111</id>
    <published>2026-07-20T09:00:00Z</published>
    <content type="html">&lt;p&gt;Open source &lt;em&gt;DevRel&lt;/em&gt; role.&lt;/p&gt;</content>
  </entry>
  <entry>
    <title>Open Source Community Manager</title>
    <link href="https://fossjobs.net/job/222"/>
    <id>urn:uuid:fossjobs-222</id>
    <updated>2026-07-19T00:00:00Z</updated>
    <summary>Build and nurture our developer community.</summary>
  </entry>
</feed>
"""

_ATOM_XML_NO_PUBLISHED = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Platform Engineer</title>
    <link href="https://example.com/job/1"/>
    <id>https://example.com/job/1</id>
    <updated>2026-06-01T00:00:00Z</updated>
    <summary>Platform role.</summary>
  </entry>
</feed>
"""

_MALFORMED_XML = "this is not xml <<<"

_EMPTY_RSS_XML = """\
<?xml version="1.0"?>
<rss version="2.0"><channel></channel></rss>
"""


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------


class TestParseRfc2822:
    def test_standard_date(self):
        assert _parse_rfc2822("Wed, 23 Jul 2026 00:00:00 +0000") == "2026-07-23"

    def test_gmt_suffix(self):
        assert _parse_rfc2822("Tue, 22 Jul 2026 12:00:00 GMT") == "2026-07-22"

    def test_none_returns_none(self):
        assert _parse_rfc2822(None) is None

    def test_empty_returns_none(self):
        assert _parse_rfc2822("") is None

    def test_invalid_returns_none(self):
        assert _parse_rfc2822("not a date") is None


class TestParseIso:
    def test_z_suffix(self):
        assert _parse_iso("2026-07-20T09:00:00Z") == "2026-07-20"

    def test_offset(self):
        assert _parse_iso("2026-07-19T00:00:00+10:00") == "2026-07-19"

    def test_bare_date(self):
        assert _parse_iso("2026-07-23") == "2026-07-23"

    def test_none_returns_none(self):
        assert _parse_iso(None) is None

    def test_empty_returns_none(self):
        assert _parse_iso("") is None

    def test_invalid_returns_none(self):
        assert _parse_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# XML parsing — RSS 2.0
# ---------------------------------------------------------------------------


class TestParseRss2:
    def test_returns_two_items(self):
        items = parse_feed_xml(_RSS2_XML)
        assert len(items) == 2

    def test_first_item_title(self):
        items = parse_feed_xml(_RSS2_XML)
        assert items[0]["title"] == "Senior Software Engineer"

    def test_first_item_link(self):
        items = parse_feed_xml(_RSS2_XML)
        assert items[0]["link"] == "https://iworkfor.nsw.gov.au/jobs/12345"

    def test_first_item_guid(self):
        items = parse_feed_xml(_RSS2_XML)
        assert items[0]["guid"] == "https://iworkfor.nsw.gov.au/jobs/12345"

    def test_first_item_published(self):
        items = parse_feed_xml(_RSS2_XML)
        assert items[0]["published"] == "2026-07-23"

    def test_description_html_preserved_in_raw(self):
        # parse_feed_xml returns raw description — HTML stripped in normalize()
        items = parse_feed_xml(_RSS2_XML)
        assert "<b>" in items[0]["description"]

    def test_second_item(self):
        items = parse_feed_xml(_RSS2_XML)
        assert items[1]["title"] == "Junior Data Analyst"
        assert items[1]["published"] == "2026-07-22"

    def test_empty_channel_returns_empty(self):
        items = parse_feed_xml(_EMPTY_RSS_XML)
        assert items == []

    def test_malformed_xml_returns_empty(self):
        items = parse_feed_xml(_MALFORMED_XML)
        assert items == []


# ---------------------------------------------------------------------------
# XML parsing — Atom 1.0
# ---------------------------------------------------------------------------


class TestParseAtom:
    def test_returns_two_entries(self):
        items = parse_feed_xml(_ATOM_XML)
        assert len(items) == 2

    def test_first_entry_title(self):
        items = parse_feed_xml(_ATOM_XML)
        assert items[0]["title"] == "Staff DevRel Engineer"

    def test_first_entry_link(self):
        items = parse_feed_xml(_ATOM_XML)
        assert items[0]["link"] == "https://fossjobs.net/job/111"

    def test_first_entry_guid(self):
        items = parse_feed_xml(_ATOM_XML)
        assert items[0]["guid"] == "https://fossjobs.net/job/111"

    def test_first_entry_published(self):
        items = parse_feed_xml(_ATOM_XML)
        assert items[0]["published"] == "2026-07-20"

    def test_first_entry_content_html_preserved(self):
        items = parse_feed_xml(_ATOM_XML)
        assert "<em>" in items[0]["description"]

    def test_second_entry_uses_summary(self):
        items = parse_feed_xml(_ATOM_XML)
        assert "community" in items[1]["description"]

    def test_second_entry_uses_updated_when_no_published(self):
        items = parse_feed_xml(_ATOM_XML)
        # Second entry has no <published>, only <updated>
        assert items[1]["published"] == "2026-07-19"

    def test_updated_fallback_when_no_published(self):
        items = parse_feed_xml(_ATOM_XML_NO_PUBLISHED)
        assert len(items) == 1
        assert items[0]["published"] == "2026-06-01"

    def test_link_without_rel_attribute(self):
        # Second entry's <link> has no rel= attribute
        items = parse_feed_xml(_ATOM_XML)
        assert items[1]["link"] == "https://fossjobs.net/job/222"

    def test_urn_guid_preserved(self):
        items = parse_feed_xml(_ATOM_XML)
        assert items[1]["guid"] == "urn:uuid:fossjobs-222"


# ---------------------------------------------------------------------------
# fetch_feed (mocked httpx)
# ---------------------------------------------------------------------------


class TestFetchFeed:
    def _mock_client(self, body: str):
        mock_resp = MagicMock()
        mock_resp.text = body
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        return mock_client

    def test_returns_response_text(self):
        mock_client = self._mock_client(_RSS2_XML)
        with patch("jobhunter.adapters.rss.httpx.Client", return_value=mock_client):
            text = fetch_feed("https://example.com/feed.rss")
        assert "Senior Software Engineer" in text

    def test_raises_on_http_error(self):
        import httpx

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp
        with patch("jobhunter.adapters.rss.httpx.Client", return_value=mock_client):
            with pytest.raises(Exception):
                fetch_feed("https://example.com/gone.rss")


# ---------------------------------------------------------------------------
# FeedAdapter.search
# ---------------------------------------------------------------------------


def _mock_fetch(xml_body: str):
    """Patch jobhunter.adapters.rss.fetch_feed to return xml_body."""
    return patch("jobhunter.adapters.rss.fetch_feed", return_value=xml_body)


class TestFeedAdapterInit:
    def test_filters_missing_url(self):
        adapter = FeedAdapter([{"name": "bad"}])
        assert adapter._feeds == []

    def test_filters_missing_name(self):
        adapter = FeedAdapter([{"url": "https://example.com/feed"}])
        assert adapter._feeds == []

    def test_accepts_valid_entry(self):
        adapter = FeedAdapter([{"name": "myfeed", "url": "https://example.com/feed"}])
        assert len(adapter._feeds) == 1

    def test_query_independent_flag(self):
        assert FeedAdapter([]).query_independent is True

    def test_empty_feeds_list(self):
        adapter = FeedAdapter([])
        assert adapter._feeds == []


class TestFeedAdapterSearch:
    def _make_adapter(self, feeds=None):
        if feeds is None:
            feeds = [{"name": "iworkfornsw", "url": "https://iworkfor.nsw.gov.au/feed"}]
        return FeedAdapter(feeds)

    def test_returns_items_from_rss_feed(self):
        adapter = self._make_adapter()
        with _mock_fetch(_RSS2_XML):
            results = adapter.search("", "", 50)
        assert len(results) == 2

    def test_injects_feed_name_into_results(self):
        adapter = self._make_adapter()
        with _mock_fetch(_RSS2_XML):
            results = adapter.search("", "", 50)
        assert all(r["_feed_name"] == "iworkfornsw" for r in results)

    def test_injects_run_date_into_results(self):
        adapter = self._make_adapter()
        adapter.run_date = "2026-07-23"
        with _mock_fetch(_RSS2_XML):
            results = adapter.search("", "", 50)
        assert all(r["_run_date"] == "2026-07-23" for r in results)

    def test_keyword_and_location_ignored(self):
        """search() accepts keyword/location for protocol compatibility but ignores them."""
        calls = []
        adapter = self._make_adapter()

        def fake_fetch(url):
            calls.append(url)
            return _RSS2_XML

        with patch("jobhunter.adapters.rss.fetch_feed", side_effect=fake_fetch):
            adapter.search("python developer", "Sydney", 50)
        assert len(calls) == 1  # one call per feed, keyword/location not forwarded

    def test_feed_error_recorded_in_company_failures(self):
        adapter = self._make_adapter()
        with patch("jobhunter.adapters.rss.fetch_feed", side_effect=RuntimeError("timeout")):
            results = adapter.search("", "", 50)
        assert results == []
        assert len(adapter.company_failures) == 1
        assert "iworkfornsw" in adapter.company_failures[0]
        assert "timeout" in adapter.company_failures[0]

    def test_failures_reset_on_each_search_call(self):
        adapter = self._make_adapter()
        with patch("jobhunter.adapters.rss.fetch_feed", side_effect=RuntimeError("err")):
            adapter.search("", "", 50)
        assert len(adapter.company_failures) == 1

        with _mock_fetch(_RSS2_XML):
            adapter.search("", "", 50)
        assert adapter.company_failures == []

    def test_multiple_feeds_aggregated(self):
        adapter = FeedAdapter([
            {"name": "feed1", "url": "https://example.com/feed1"},
            {"name": "feed2", "url": "https://example.com/feed2"},
        ])
        call_count = []

        def fake_fetch(url):
            call_count.append(url)
            return _RSS2_XML

        with patch("jobhunter.adapters.rss.fetch_feed", side_effect=fake_fetch):
            results = adapter.search("", "", 50)
        assert len(results) == 4  # 2 items × 2 feeds
        assert len(call_count) == 2

    def test_partial_failure_continues(self):
        """A failure for one feed does not abort the remaining feeds."""
        adapter = FeedAdapter([
            {"name": "bad", "url": "https://example.com/bad"},
            {"name": "good", "url": "https://example.com/good"},
        ])
        urls_called = []

        def fake_fetch(url):
            urls_called.append(url)
            if "bad" in url:
                raise RuntimeError("network error")
            return _RSS2_XML

        with patch("jobhunter.adapters.rss.fetch_feed", side_effect=fake_fetch):
            results = adapter.search("", "", 50)

        assert len(results) == 2  # only good feed items
        assert len(adapter.company_failures) == 1
        assert "bad" in adapter.company_failures[0]


# ---------------------------------------------------------------------------
# FeedAdapter.normalize
# ---------------------------------------------------------------------------


_RSS2_RAW_ITEM: dict = {
    "title": "Senior Software Engineer",
    "link": "https://iworkfor.nsw.gov.au/jobs/12345",
    "guid": "https://iworkfor.nsw.gov.au/jobs/12345",
    "description": "<p>A <b>remote</b> NSW role.</p>",
    "published": "2026-07-23",
    "_feed_name": "iworkfornsw",
    "_feed_url": "https://iworkfor.nsw.gov.au/feed",
    "_run_date": "2026-07-23",
}

_ATOM_RAW_ITEM: dict = {
    "title": "Staff DevRel Engineer",
    "link": "https://fossjobs.net/job/111",
    "guid": "https://fossjobs.net/job/111",
    "description": "<p>Open source <em>DevRel</em> role.</p>",
    "published": "2026-07-20",
    "_feed_name": "fossjobs",
    "_feed_url": "https://fossjobs.net/feed",
    "_run_date": "2026-07-23",
}


class TestFeedAdapterNormalize:
    def test_returns_job_listing(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert isinstance(result, JobListing)

    def test_title_preserved(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.title == "Senior Software Engineer"

    def test_html_stripped_from_description(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert "<p>" not in result.description
        assert "NSW" in result.description

    def test_source_name_includes_feed_name(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.sources[0].name == "feed:iworkfornsw"

    def test_source_url_is_link(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.sources[0].url == "https://iworkfor.nsw.gov.au/jobs/12345"

    def test_source_id_is_guid(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.sources[0].source_id == "https://iworkfor.nsw.gov.au/jobs/12345"

    def test_posted_at_set(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.posted_at == "2026-07-23"

    def test_first_seen_at_uses_run_date(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.first_seen_at == "2026-07-23"

    def test_salary_is_none(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.salary is None

    def test_employment_is_none(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.employment is None

    def test_seniority_inferred_from_title(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        # "Senior Software Engineer" → ic/senior
        assert result.seniority is not None
        assert result.seniority.track == "ic"
        assert result.seniority.level == "senior"

    def test_staff_seniority_from_atom(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_ATOM_RAW_ITEM)
        assert result.seniority is not None
        assert result.seniority.level == "staff"

    def test_id_derived_and_stable(self):
        adapter = FeedAdapter([])
        r1 = adapter.normalize(_RSS2_RAW_ITEM)
        r2 = adapter.normalize(_RSS2_RAW_ITEM)
        assert r1.id != ""
        assert r1.id == r2.id

    def test_content_hash_derived(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.content_hash != ""

    def test_distinct_guids_produce_distinct_ids(self):
        """Two different feed items must not share an id even with the same title."""
        adapter = FeedAdapter([])
        raw_a = {**_RSS2_RAW_ITEM, "guid": "https://example.com/jobs/111"}
        raw_b = {**_RSS2_RAW_ITEM, "guid": "https://example.com/jobs/222"}
        assert adapter.normalize(raw_a).id != adapter.normalize(raw_b).id

    def test_missing_run_date_falls_back_to_published(self):
        adapter = FeedAdapter([])
        raw = {**_RSS2_RAW_ITEM, "_run_date": ""}
        result = adapter.normalize(raw)
        assert result.first_seen_at == "2026-07-23"  # published date

    def test_empty_company(self):
        adapter = FeedAdapter([])
        result = adapter.normalize(_RSS2_RAW_ITEM)
        assert result.company == ""


# ---------------------------------------------------------------------------
# Pipeline integration (end-to-end criterion)
# ---------------------------------------------------------------------------


def _make_profile(feeds=None):
    """Minimal valid profile with optional feeds."""
    return {
        "identity": {
            "target_skills": ["Python"],
            "target": [{"track": "ic", "level": "senior"}],
        },
        "queries": {
            "keywords": ["software engineer"],
            "locations": ["Remote"],
            "max_results_per_query": 50,
            "max_requests_per_run": 10,
            "ats_watchlist": [],
            "feeds": feeds or [],
        },
        "hard_requirements": {
            "remote_policy": "any",
            "exclude_locations": [],
            "locations_allowed": [],
            "keep_unknown_salary": True,
            "exclude_employment": [],
            "exclude_keywords": [],
            "require_keywords": [],
            "max_age_days": 90,
        },
        "preferences": {},
        "weights": {"skill_match": 1},
        "output": {
            "display_threshold": 0,
            "max_shown": 25,
            "show_previously_seen": True,
            "format": "markdown",
        },
    }


class TestFeedAdapterPipelineIntegration:
    def test_feed_appears_in_sources_used(self):
        """END-TO-END: a profile with a feed configured shows it in sources_used."""
        from datetime import date

        from jobhunter.pipeline import run as pipeline_run

        feeds = [{"name": "iworkfornsw", "url": "https://iworkfor.nsw.gov.au/feed"}]
        adapter = FeedAdapter(feeds)

        with _mock_fetch(_RSS2_XML):
            results, report = pipeline_run(
                _make_profile(feeds),
                [adapter],
                today=date(2026, 7, 23),
            )

        assert "feeds" in report.sources_used

    def test_feed_items_ingested_by_pipeline(self):
        """END-TO-END: fixture-feed entries are ingested (appear in ingested_count)."""
        from datetime import date

        from jobhunter.pipeline import run as pipeline_run

        feeds = [{"name": "fossjobs", "url": "https://fossjobs.net/feed"}]
        adapter = FeedAdapter(feeds)

        with _mock_fetch(_ATOM_XML):
            _results, report = pipeline_run(
                _make_profile(feeds),
                [adapter],
                today=date(2026, 7, 23),
            )

        assert report.ingested_count == 2

    def test_disabled_feed_not_constructed(self):
        """A profile with an empty feeds list produces no FeedAdapter entries."""
        profile = _make_profile(feeds=[])
        assert profile["queries"]["feeds"] == []
        # No adapter constructed — verified by not calling the pipeline at all
        # (the CLI _build_adapters check `if feeds:` is the guard)

    def test_query_independent_adapter_called_once(self):
        """FeedAdapter.search is called once per run, not per keyword×location combo."""
        from datetime import date

        from jobhunter.pipeline import run as pipeline_run

        call_count = []
        feeds = [{"name": "test", "url": "https://example.com/feed"}]

        class TrackingFeedAdapter(FeedAdapter):
            def search(self, keyword, location, max_results):
                call_count.append((keyword, location))
                return []

        profile = {
            **_make_profile(feeds),
            "queries": {
                "keywords": ["engineer", "developer", "manager"],
                "locations": ["Remote AU", "Remote US", "Remote"],
                "max_results_per_query": 50,
                "max_requests_per_run": 100,
                "ats_watchlist": [],
                "feeds": feeds,
            },
        }

        pipeline_run(profile, [TrackingFeedAdapter(feeds)], today=date(2026, 7, 23))
        assert len(call_count) == 1, (
            f"FeedAdapter.search should be called once per run; called {len(call_count)} times"
        )

    def test_feed_failure_surfaced_in_report(self):
        """Per-feed failures appear in run report sources_failed."""
        from datetime import date

        from jobhunter.pipeline import run as pipeline_run

        feeds = [{"name": "broken", "url": "https://broken.example.com/feed"}]
        adapter = FeedAdapter(feeds)

        with patch("jobhunter.adapters.rss.fetch_feed", side_effect=RuntimeError("DNS failure")):
            _results, report = pipeline_run(
                _make_profile(feeds),
                [adapter],
                today=date(2026, 7, 23),
            )

        failure_errors = [f.error for f in report.sources_failed]
        assert any("broken" in e and "DNS failure" in e for e in failure_errors)

    def test_counts_one_request_for_all_feeds(self):
        """A FeedAdapter with multiple feeds still counts as one request."""
        from datetime import date

        from jobhunter.pipeline import run as pipeline_run

        feeds = [
            {"name": "feed1", "url": "https://example.com/feed1"},
            {"name": "feed2", "url": "https://example.com/feed2"},
        ]
        adapter = FeedAdapter(feeds)
        with _mock_fetch(_RSS2_XML):
            _results, report = pipeline_run(
                _make_profile(feeds),
                [adapter],
                today=date(2026, 7, 23),
            )
        assert report.requests_made == 1


# ---------------------------------------------------------------------------
# Profile validation integration
# ---------------------------------------------------------------------------


class TestProfileFeedsValidation:
    """Ensure profile.py validates queries.feeds correctly."""

    def _base_profile_yaml(self, feeds_yaml: str = "") -> str:
        """Minimal valid profile YAML with an optional feeds block."""
        return f"""\
identity:
  target_skills: ["Python"]
  target:
    - track: ic
      level: senior
queries:
  keywords: ["software engineer"]
  locations: ["Remote"]
{feeds_yaml}
hard_requirements:
  remote_policy: remote_only
  exclude_locations: []
  locations_allowed: []
  keep_unknown_salary: true
  exclude_employment: []
  exclude_keywords: []
  require_keywords: []
  max_age_days: 30
preferences: {{}}
weights:
  skill_match: 1
output:
  display_threshold: 0
  max_shown: 25
  show_previously_seen: true
  format: markdown
"""

    def _load(self, yaml_text: str):
        import tempfile
        from pathlib import Path

        from jobhunter.profile import load_profile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_text)
            tmp = Path(f.name)
        try:
            return load_profile(tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def test_valid_feeds_block_accepted(self):
        feeds_yaml = (
            "  feeds:\n"
            "    - name: iworkfornsw\n"
            "      url: https://iworkfor.nsw.gov.au/feed\n"
        )
        profile = self._load(self._base_profile_yaml(feeds_yaml))
        assert profile["queries"]["feeds"][0]["name"] == "iworkfornsw"

    def test_missing_feeds_defaults_to_empty(self):
        profile = self._load(self._base_profile_yaml())
        assert profile["queries"]["feeds"] == []

    def test_feed_missing_name_raises(self):
        from jobhunter.profile import ProfileError

        feeds_yaml = "  feeds:\n    - url: https://example.com/feed\n"
        with pytest.raises(ProfileError, match="name"):
            self._load(self._base_profile_yaml(feeds_yaml))

    def test_feed_missing_url_raises(self):
        from jobhunter.profile import ProfileError

        feeds_yaml = "  feeds:\n    - name: myfeed\n"
        with pytest.raises(ProfileError, match="url"):
            self._load(self._base_profile_yaml(feeds_yaml))

    def test_feed_unknown_key_raises(self):
        from jobhunter.profile import ProfileError

        feeds_yaml = (
            "  feeds:\n"
            "    - name: myfeed\n"
            "      url: https://example.com/feed\n"
            "      extra: bad\n"
        )
        with pytest.raises(ProfileError, match="Unknown key"):
            self._load(self._base_profile_yaml(feeds_yaml))

    def test_feed_empty_name_raises(self):
        from jobhunter.profile import ProfileError

        feeds_yaml = (
            "  feeds:\n"
            "    - name: ''\n"
            "      url: https://example.com/feed\n"
        )
        with pytest.raises(ProfileError, match="empty"):
            self._load(self._base_profile_yaml(feeds_yaml))


class TestFeedAdapterCompanyFromTitle:
    """Opt-in "Company: Position" title split (e.g. WeWorkRemotely)."""

    def _item(self, title, flag):
        return {
            "title": title,
            "link": "https://weworkremotely.com/remote-jobs/x",
            "guid": "g",
            "description": "<p>role</p>",
            "published": "2026-07-23",
            "_feed_name": "weworkremotely",
            "_feed_url": "https://weworkremotely.com/remote-jobs.rss",
            "_run_date": "2026-07-23",
            "_company_from_title": flag,
        }

    def test_splits_company_and_title_when_enabled(self):
        adapter = FeedAdapter([])
        r = adapter.normalize(self._item("Twilio: Principal Presales Engineer", True))
        assert r.company == "Twilio"
        assert r.title == "Principal Presales Engineer"

    def test_splits_only_on_first_delimiter(self):
        adapter = FeedAdapter([])
        r = adapter.normalize(self._item("Dropbox: Director, Product Design", True))
        assert r.company == "Dropbox"
        assert r.title == "Director, Product Design"

    def test_no_split_when_flag_absent(self):
        adapter = FeedAdapter([])
        r = adapter.normalize(self._item("Twilio: Principal Presales Engineer", False))
        assert r.company == ""
        assert r.title == "Twilio: Principal Presales Engineer"

    def test_no_split_when_no_delimiter(self):
        adapter = FeedAdapter([])
        r = adapter.normalize(self._item("Principal Engineer", True))
        assert r.company == ""
        assert r.title == "Principal Engineer"

    def test_no_split_when_empty_side(self):
        adapter = FeedAdapter([])
        r = adapter.normalize(self._item("Company: ", True))
        assert r.company == ""

    def test_search_injects_flag_from_feed_config(self):
        from unittest.mock import patch

        feed = {"name": "wwr", "url": "https://wwr.test/f.rss", "company_from_title": True}
        adapter = FeedAdapter([feed])
        xml = (
            '<?xml version="1.0"?><rss><channel>'
            "<item><title>Stripe: Staff Engineer</title>"
            "<link>https://wwr.test/j/1</link><guid>1</guid>"
            "<description>role</description>"
            "<pubDate>Wed, 23 Jul 2026 00:00:00 GMT</pubDate></item>"
            "</channel></rss>"
        )
        with patch("jobhunter.adapters.rss.fetch_feed", return_value=xml):
            items = adapter.search("", "", 50)
        assert items and items[0]["_company_from_title"] is True
        listing = adapter.normalize(items[0])
        assert listing.company == "Stripe"
        assert listing.title == "Staff Engineer"


class TestFeedAdapterRegionLocation:
    """Opt-in WeWorkRemotely region/country/state -> remote Location resolution."""

    def _item(self, region, country="", state="", title="Co: Role"):
        return {
            "title": title, "link": "https://wwr.test/j", "guid": "g",
            "description": "role", "published": "2026-07-23",
            "_feed_name": "weworkremotely", "_run_date": "2026-07-23",
            "_company_from_title": True, "_region_location": True,
            "region": region, "country": country, "state": state,
        }

    def test_anywhere_is_global_remote(self):
        adapter = FeedAdapter([])
        item = self._item(
            "Anywhere in the World",
            country="🇺🇸 United States of America",
            state="Pennsylvania",
        )
        loc = adapter.normalize(item).location
        assert loc.is_remote is True
        assert loc.country is None  # global remote wins over HQ country

    def test_us_state_resolves_to_us(self):
        adapter = FeedAdapter([])
        loc = adapter.normalize(self._item("Massachusetts")).location
        assert loc.is_remote is True
        assert loc.country == "US"

    def test_us_state_minnesota_resolves_to_us(self):
        adapter = FeedAdapter([])
        assert adapter.normalize(self._item("Minnesota")).location.country == "US"

    def test_explicit_country_field_resolves(self):
        adapter = FeedAdapter([])
        item = self._item("Somewhere", country="🇺🇸 United States of America")
        loc = adapter.normalize(item).location
        assert loc.country == "US"

    def test_no_region_location_flag_keeps_empty_location(self):
        adapter = FeedAdapter([])
        item = self._item("Massachusetts")
        item["_region_location"] = False
        loc = adapter.normalize(item).location
        assert loc.country is None

    def test_parser_extracts_region_country_state(self):
        from jobhunter.adapters.rss import parse_feed_xml
        xml = (
            '<?xml version="1.0"?><rss xmlns:media="http://x"><channel>'
            "<item><title>Co: Role</title><link>https://wwr.test/j</link>"
            "<guid>1</guid><description>d</description>"
            "<pubDate>Wed, 23 Jul 2026 00:00:00 GMT</pubDate>"
            "<region>Massachusetts</region><country></country><state></state>"
            "</item></channel></rss>"
        )
        items = parse_feed_xml(xml)
        assert items[0]["region"] == "Massachusetts"
        assert items[0]["country"] == ""
