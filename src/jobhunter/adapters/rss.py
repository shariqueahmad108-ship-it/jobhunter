# SPDX-License-Identifier: Apache-2.0
"""RSS/Atom feed source adapter.

Fetches any public RSS 2.0 or Atom 1.0 feed and maps each entry to a
canonical JobListing. Configured via profile queries.feeds — a list of
{name, url} entries.

This adapter is query-independent: it fetches each configured feed once per
run rather than once per keyword×location combination. The hard-filter stage
applies keyword and location filtering downstream.

No credentials required — only public feeds are supported.

See: specs/02-functional-spec.md §Stage 1–2
     specs/04-technical-plan.md §Data sources
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from jobhunter.model import (
    JobListing,
    Location,
    Source,
    derive_content_hash,
    derive_id,
    infer_seniority,
)
from jobhunter.normalize import parse_location, strip_html

RawListing = dict[str, Any]

_TIMEOUT = 30.0
_ATOM_NS = "http://www.w3.org/2005/Atom"
_DC_NS = "http://purl.org/dc/elements/1.1/"

_FEED_ACCEPT = (
    "application/rss+xml, application/atom+xml, "
    "application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7"
)


# ---------------------------------------------------------------------------
# WeWorkRemotely-style region resolution (opt-in per feed via region_location)
# ---------------------------------------------------------------------------

_ANYWHERE_RE = re.compile(r"anywhere", re.IGNORECASE)
_US_RE = re.compile(r"\b(u\.?s\.?a?|united states)\b", re.IGNORECASE)

# Full US state names — WWR puts these in <region>/<state> for US-only roles.
_US_STATE_NAMES: frozenset[str] = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
})


def _strip_non_ascii(s: str) -> str:
    """Drop flag emoji / non-ASCII and collapse whitespace."""
    return " ".join(re.sub(r"[^\x00-\x7f]", " ", s).split())


def _wwr_location(region: str, country: str, state: str) -> Location:
    """Resolve WeWorkRemotely region/country/state fields to a Location.

    Every WWR listing is remote. ``<region>`` is the work-scope signal:
      - "Anywhere in the World" -> globally remote (country None -> counts as
        remote for any remote_countries_allowed, so it is kept).
      - a US state name / "USA Only" / "United States" -> country "US" (a
        remote role restricted to the US, which drops for an AU-only searcher).
      - an explicit country name -> that country via parse_location.
    Unresolved scopes leave country None (kept + flagged), never wrongly dropped.
    """
    region = (region or "").strip()
    country = _strip_non_ascii(country or "")
    state = (state or "").strip()
    raw = region or country or state or "Remote"

    if region and _ANYWHERE_RE.search(region):
        return Location(raw=raw, city=None, region=None, country=None, is_remote=True)

    resolved: str | None = None
    if country:
        resolved = parse_location(country).country
    if resolved is None:
        for cand in (region, state):
            if cand.lower() in _US_STATE_NAMES or _US_RE.search(cand):
                resolved = "US"
                break
    if resolved is None and region:
        resolved = parse_location(region.replace(" Only", "")).country

    return Location(raw=raw, city=None, region=None, country=resolved, is_remote=True)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


def _parse_rfc2822(raw: str | None) -> str | None:
    """Parse an RFC 2822 date string (RSS pubDate) to YYYY-MM-DD."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw.strip()).date().isoformat()
    except Exception:
        return None


def _parse_iso(raw: str | None) -> str | None:
    """Parse an ISO 8601 date/datetime string to YYYY-MM-DD."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(
            raw.strip().replace("Z", "+00:00")
        ).date().isoformat()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# XML feed parsing
# ---------------------------------------------------------------------------


def _ns(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _parse_rss2_items(root: ET.Element) -> list[dict]:
    """Extract items from an RSS 2.0 feed root element."""
    channel = root.find("channel")
    if channel is None:
        return []
    items: list[dict] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        description = (item.findtext("description") or "").strip()
        published = _parse_rfc2822(item.findtext("pubDate"))
        items.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "description": description,
                "published": published,
                "region": (item.findtext("region") or "").strip(),
                "country": (item.findtext("country") or "").strip(),
                "state": (item.findtext("state") or "").strip(),
            }
        )
    return items


def _parse_atom_items(root: ET.Element) -> list[dict]:
    """Extract entries from an Atom 1.0 feed root element."""
    items: list[dict] = []
    for entry in root.findall(_ns(_ATOM_NS, "entry")):
        title_el = entry.find(_ns(_ATOM_NS, "title"))
        title = (title_el.text or "").strip() if title_el is not None else ""

        # Prefer rel="alternate" link; fall back to any <link>
        link = ""
        for link_el in entry.findall(_ns(_ATOM_NS, "link")):
            rel = link_el.get("rel", "alternate")
            if rel == "alternate":
                link = link_el.get("href", "")
                break
        if not link:
            # Separate name: link_el above is a loop variable of a different type.
            fallback_el = entry.find(_ns(_ATOM_NS, "link"))
            if fallback_el is not None:
                link = fallback_el.get("href", "")

        id_el = entry.find(_ns(_ATOM_NS, "id"))
        guid = (id_el.text or link).strip() if id_el is not None else link

        # Prefer <published> over <updated>
        pub_el = entry.find(_ns(_ATOM_NS, "published"))
        if pub_el is None:
            pub_el = entry.find(_ns(_ATOM_NS, "updated"))
        published = _parse_iso(pub_el.text if pub_el is not None else None)

        # Prefer <content> over <summary>
        content_el = entry.find(_ns(_ATOM_NS, "content"))
        if content_el is None:
            content_el = entry.find(_ns(_ATOM_NS, "summary"))
        description = (content_el.text or "").strip() if content_el is not None else ""

        items.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "description": description,
                "published": published,
            }
        )
    return items


def parse_feed_xml(xml_text: str) -> list[dict]:
    """Parse RSS 2.0 or Atom 1.0 XML into a list of raw item dicts.

    Returns an empty list on parse failure or unknown format.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    tag = root.tag
    if tag == "rss" or tag.endswith("}rss"):
        return _parse_rss2_items(root)
    if tag == _ns(_ATOM_NS, "feed") or tag == "feed":
        return _parse_atom_items(root)
    # Fallback: some feeds wrap RSS inside a non-standard root
    channel = root.find("channel")
    if channel is not None:
        return _parse_rss2_items(root)
    return []


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------


def fetch_feed(url: str) -> str:
    """Fetch a feed URL and return the response body as text.

    Raises httpx.HTTPError on network or HTTP-status failure.
    """
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(url, follow_redirects=True, headers={"Accept": _FEED_ACCEPT})
        resp.raise_for_status()
        return resp.text


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class FeedAdapter:
    """Source adapter for public RSS 2.0 and Atom 1.0 job feeds.

    Configured via profile ``queries.feeds`` — a list of
    ``{name: str, url: str}`` entries. Each ``name`` appears separately in the
    run report's ``sources_used`` tally as ``feed:<name>``.

    This adapter is query-independent: feeds are fetched once per run, not
    once per keyword×location combination. The hard-filter stage handles
    keyword and location filtering downstream.

    Per-feed failures are caught and recorded in ``company_failures``
    (same attribute name used by ``AtsAdapter``) so the pipeline surfaces
    them in the run report without aborting other feeds.
    """

    name = "feeds"
    query_independent = True

    def __init__(self, feeds: list[dict]) -> None:
        self._feeds = [
            f for f in feeds if isinstance(f.get("name"), str) and isinstance(f.get("url"), str)
        ]
        self.run_date: str = ""
        # Named company_failures for pipeline compatibility (pipeline reads this attribute)
        self.company_failures: list[str] = []

    def search(self, keyword: str, location: str, max_results: int = 50) -> list[RawListing]:
        """Fetch all configured feeds and return a flat list of raw item dicts.

        ``keyword`` and ``location`` are accepted for protocol compatibility but
        ignored — the hard-filter stage handles that downstream.

        Per-feed errors are caught and recorded in ``self.company_failures``.
        """
        self.company_failures = []
        results: list[RawListing] = []

        for feed_cfg in self._feeds:
            feed_name = feed_cfg["name"]
            feed_url = feed_cfg["url"]
            try:
                xml_text = fetch_feed(feed_url)
                items = parse_feed_xml(xml_text)
                for item in items:
                    item["_feed_name"] = feed_name
                    item["_feed_url"] = feed_url
                    item["_run_date"] = self.run_date
                    item["_company_from_title"] = bool(feed_cfg.get("company_from_title"))
                    item["_region_location"] = bool(feed_cfg.get("region_location"))
                results.extend(items)
            except Exception as exc:
                self.company_failures.append(f"{feed_name} ({feed_url}): {exc}")

        return results

    def normalize(self, raw: RawListing) -> JobListing:
        """Map a raw feed item dict to a canonical JobListing.

        The ``_feed_name``, ``_feed_url``, and ``_run_date`` keys are injected
        by ``search()`` and must be present.
        """
        feed_name = raw.get("_feed_name") or "feed"
        run_date = raw.get("_run_date") or ""

        title = (raw.get("title") or "").strip()
        link = (raw.get("link") or "").strip()
        guid = (raw.get("guid") or link).strip()
        description = strip_html(raw.get("description") or "")
        published = raw.get("published")

        # Feeds rarely carry structured company or location data.
        # Opt-in per feed: some boards (e.g. WeWorkRemotely) encode the employer
        # in the title as "Company: Position" — split it so the company shows and
        # the title scores/deduplicates on the role alone.
        company = ""
        if raw.get("_company_from_title") and ": " in title:
            maybe_company, _, rest = title.partition(": ")
            if maybe_company.strip() and rest.strip():
                company = maybe_company.strip()
                title = rest.strip()

        # Opt-in per feed: resolve WeWorkRemotely-style region/country/state
        # into a remote Location so US-only roles can be region-filtered.
        if raw.get("_region_location"):
            location = _wwr_location(
                raw.get("region") or "", raw.get("country") or "", raw.get("state") or ""
            )
        else:
            location = parse_location("")

        first_seen_at = run_date or published or date.today().isoformat()
        seniority = infer_seniority(title, description)

        source = Source(
            name=f"feed:{feed_name}",
            url=link,
            source_id=guid or link,
        )

        listing = JobListing(
            id="",
            content_hash="",
            title=title,
            company=company,
            location=location,
            description=description,
            sources=[source],
            first_seen_at=first_seen_at,
            salary=None,
            seniority=seniority,
            employment=None,
            posted_at=published,
        )
        # No company: salt the id with the source_id so distinct feed entries
        # with the same title don't collapse into one listing.
        listing.id = derive_id(f"__feed__{source.source_id}", title, location)
        listing.content_hash = derive_content_hash(listing)
        return listing
