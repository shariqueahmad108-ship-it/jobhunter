# SPDX-License-Identifier: Apache-2.0
"""Stage 3 — Dedupe.

Collapse cross-posted duplicates: two listings with the same normalized identity
key (lowercased, whitespace-collapsed, punctuation-stripped company+title+city|country)
or the same resolved URL are merged into one listing that retains all source links,
the earliest first_seen_at, and the most complete non-null fields.

See: specs/02-functional-spec.md §Stage 3
     specs/03-data-model.md §Notes on identity & dedupe
"""

from __future__ import annotations

from jobhunter.model import JobListing, Source, derive_content_hash


def _merge_sources(a_sources: list[Source], b_sources: list[Source]) -> list[Source]:
    """Union two source lists, deduplicating by (name, source_id)."""
    seen: set[tuple[str, str]] = set()
    result: list[Source] = []
    for src in a_sources + b_sources:
        key = (src.name, src.source_id)
        if key not in seen:
            seen.add(key)
            result.append(src)
    return result


def _prefer_nonnull(a, b):
    """Return a if a is not None, else b."""
    return a if a is not None else b


def _location_completeness(loc) -> int:
    return sum(x is not None for x in (loc.city, loc.region, loc.country))


def _merge_two(primary: JobListing, secondary: JobListing) -> JobListing:
    """Merge secondary into primary and return the combined listing.

    Rules:
    - sources: union, deduped by (name, source_id), primary order first
    - first_seen_at: earliest of the two
    - Non-optional identity fields (id, title, company, description): primary wins
    - location: the more-completely-parsed one wins (spec 03: "most complete
      non-null fields"); primary wins ties
    - Optional fields (salary, seniority, employment, posted_at): non-null wins;
      primary wins on conflict
    - content_hash: recomputed from the merged record
    """
    location = (
        primary.location
        if _location_completeness(primary.location) >= _location_completeness(secondary.location)
        else secondary.location
    )
    merged = JobListing(
        id=primary.id,
        content_hash="",  # recomputed below
        title=primary.title,
        company=primary.company,
        location=location,
        description=primary.description,
        sources=_merge_sources(primary.sources, secondary.sources),
        first_seen_at=min(primary.first_seen_at, secondary.first_seen_at),
        salary=_prefer_nonnull(primary.salary, secondary.salary),
        seniority=_prefer_nonnull(primary.seniority, secondary.seniority),
        employment=_prefer_nonnull(primary.employment, secondary.employment),
        posted_at=_prefer_nonnull(primary.posted_at, secondary.posted_at),
    )
    merged.content_hash = derive_content_hash(merged)
    return merged


def run(listings: list[JobListing]) -> list[JobListing]:
    """Stage 3 — Dedupe pipeline function.

    Collapses duplicates in two passes:

    Pass 1 — id-based: listings sharing the same stable id (derived from the
      normalized identity key: company + title + city|country) are merged into
      one listing.

    Pass 2 — URL-based: any surviving listings that share a URL across sources
      are further merged via union-find, even when their ids differ (e.g. the
      same posting indexed under slightly different company spellings).

    The merged listing keeps:
    - All (source, url) pairs (deduped by name+source_id)
    - The earliest first_seen_at
    - The most complete non-null optional fields (primary's value wins on conflict)
    - A recomputed content_hash

    Returns one listing per unique job. Output order follows input order of
    first encounter for each identity group.

    See: specs/02-functional-spec.md §Stage 3
    """
    if not listings:
        return []

    # --- Pass 1: group and merge by id ---
    # Preserve insertion order for determinism.
    primary_for: dict[str, JobListing] = {}
    for listing in listings:
        lid = listing.id
        if lid in primary_for:
            primary_for[lid] = _merge_two(primary_for[lid], listing)
        else:
            primary_for[lid] = listing

    # --- Pass 2: URL-based merge via union-find ---
    parent: dict[str, str] = {lid: lid for lid in primary_for}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    url_to_lid: dict[str, str] = {}
    for lid, listing in primary_for.items():
        for src in listing.sources:
            if not src.url:
                continue  # empty/missing URL is not an identity signal
            if src.url in url_to_lid:
                union(lid, url_to_lid[src.url])
            else:
                url_to_lid[src.url] = lid

    # Group ids by their root; preserve insertion order
    root_groups: dict[str, list[str]] = {}
    for lid in primary_for:
        root = find(lid)
        root_groups.setdefault(root, []).append(lid)

    result: list[JobListing] = []
    for root, members in root_groups.items():
        merged = primary_for[root]
        for other_lid in members:
            if other_lid == root:
                continue
            merged = _merge_two(merged, primary_for[other_lid])
        result.append(merged)

    return result
