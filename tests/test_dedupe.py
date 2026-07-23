# SPDX-License-Identifier: Apache-2.0
"""Tests for Stage 3 — Dedupe.

Covers:
  - id-based deduplication (≥10 merge pairs)
  - URL-based deduplication across different ids
  - Distinct listings are kept separate (≥10 distinct pairs)
  - Merge rules: sources union, earliest first_seen_at, non-null field preference
  - content_hash recomputed after merge
  - Empty input

Validation: python -m pytest tests/test_dedupe.py -q
"""

from __future__ import annotations

from jobhunter.dedupe import run
from jobhunter.model import (
    JobListing,
    Location,
    Salary,
    Seniority,
    Source,
    derive_content_hash,
    derive_id,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_COUNTER = 0


def _src(name: str = "adzuna", url: str | None = None, sid: str | None = None) -> Source:
    global _COUNTER
    _COUNTER += 1
    sid = sid if sid is not None else str(_COUNTER)
    url = url if url is not None else f"http://example.com/{_COUNTER}"
    return Source(name=name, url=url, source_id=sid)


def _listing(
    company: str = "Acme",
    title: str = "Software Engineer",
    city: str | None = "Sydney",
    region: str | None = "NSW",
    country: str | None = "AU",
    is_remote: bool = False,
    location_raw: str | None = None,
    sources: list[Source] | None = None,
    first_seen_at: str = "2024-01-15",
    salary: Salary | None = None,
    seniority: Seniority | None = None,
    employment: str | None = None,
    posted_at: str | None = None,
    description: str = "A great role.",
) -> JobListing:
    if location_raw is None:
        parts = []
        if city:
            parts.append(city)
        if region:
            parts.append(region)
        if country:
            parts.append(country)
        location_raw = ", ".join(parts) if parts else ("Remote" if is_remote else "Unknown")
    loc = Location(
        raw=location_raw,
        city=city,
        region=region,
        country=country,
        is_remote=is_remote,
    )
    if sources is None:
        sources = [_src()]
    listing = JobListing(
        id="",
        content_hash="",
        title=title,
        company=company,
        location=loc,
        description=description,
        sources=sources,
        first_seen_at=first_seen_at,
        salary=salary,
        seniority=seniority,
        employment=employment,
        posted_at=posted_at,
    )
    listing.id = derive_id(company, title, loc)
    listing.content_hash = derive_content_hash(listing)
    return listing


# ---------------------------------------------------------------------------
# Helper assertions
# ---------------------------------------------------------------------------


def _ids(listings: list[JobListing]) -> set[str]:
    return {lst.id for lst in listings}


def _source_count(listing: JobListing) -> int:
    return len(listing.sources)


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_empty_input():
    assert run([]) == []


def test_single_listing_passthrough():
    lst = _listing()
    result = run([lst])
    assert len(result) == 1
    assert result[0].id == lst.id


# ---------------------------------------------------------------------------
# Id-based merge pairs (≥10)
# Each pair has the same normalized identity key → same id → must collapse to 1
# ---------------------------------------------------------------------------


def test_merge_same_title_two_sources():
    """Same role from two different sources → one listing with two source links."""
    src_a = _src(name="adzuna", url="http://adzuna.com/1", sid="a1")
    src_b = _src(name="seek", url="http://seek.com/1", sid="s1")
    a = _listing(sources=[src_a])
    b = _listing(sources=[src_b])
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1
    assert _source_count(result[0]) == 2


def test_merge_sr_vs_senior():
    """'Sr. Software Engineer' and 'Senior Software Engineer' → same id."""
    a = _listing(title="Sr. Software Engineer")
    b = _listing(title="Senior Software Engineer")
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1


def test_merge_snr_vs_senior():
    """'Snr. Software Engineer' and 'Senior Software Engineer' → same id."""
    a = _listing(title="Snr. Software Engineer")
    b = _listing(title="Senior Software Engineer")
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1


def test_merge_lead_vs_staff():
    """'Lead Developer' and 'Staff Developer' → same id (lead maps to staff)."""
    a = _listing(title="Lead Developer")
    b = _listing(title="Staff Developer")
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1


def test_merge_jr_vs_junior():
    """'Jr. Developer' and 'Junior Developer' → same id."""
    a = _listing(title="Jr. Developer")
    b = _listing(title="Junior Developer")
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1


def test_merge_trailing_parenthetical():
    """'Software Engineer (Backend Team)' and 'Software Engineer' → same id."""
    a = _listing(title="Software Engineer (Backend Team)")
    b = _listing(title="Software Engineer")
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1


def test_merge_remote_no_city_same_country():
    """Same remote role (country known, city=None) from two sources → one listing."""
    src_a = _src(name="adzuna", url="http://adzuna.com/2", sid="a2")
    src_b = _src(name="seek", url="http://seek.com/2", sid="s2")
    a = _listing(
        city=None,
        region=None,
        country="AU",
        is_remote=True,
        location_raw="Remote AU",
        sources=[src_a],
    )
    b = _listing(
        city=None,
        region=None,
        country="AU",
        is_remote=True,
        location_raw="Remote AU",
        sources=[src_b],
    )
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1
    assert _source_count(result[0]) == 2


def test_merge_remote_no_city_no_country():
    """Fully remote listing (no city, no country) from two sources → one listing."""
    src_a = _src(name="adzuna", url="http://adzuna.com/3", sid="a3")
    src_b = _src(name="seek", url="http://seek.com/3", sid="s3")
    a = _listing(
        city=None, region=None, country=None, is_remote=True, location_raw="Remote", sources=[src_a]
    )
    b = _listing(
        city=None, region=None, country=None, is_remote=True, location_raw="Remote", sources=[src_b]
    )
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1
    assert _source_count(result[0]) == 2


def test_merge_salary_from_secondary():
    """Primary has no salary; secondary has salary → merged keeps salary."""
    src_a = _src(name="adzuna", url="http://adzuna.com/4", sid="a4")
    src_b = _src(name="seek", url="http://seek.com/4", sid="s4")
    sal = Salary(min=100_000, max=130_000, currency="AUD", period="year", raw="$100k-$130k")
    a = _listing(salary=None, sources=[src_a])
    b = _listing(salary=sal, sources=[src_b])
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1
    assert result[0].salary is not None
    assert result[0].salary.min == 100_000


def test_merge_employment_from_secondary():
    """Primary has no employment type; secondary has it → merged keeps it."""
    src_a = _src(name="adzuna", url="http://adzuna.com/5", sid="a5")
    src_b = _src(name="seek", url="http://seek.com/5", sid="s5")
    a = _listing(employment=None, sources=[src_a])
    b = _listing(employment="full_time", sources=[src_b])
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1
    assert result[0].employment == "full_time"


def test_merge_posted_at_from_secondary():
    """Primary has no posted_at; secondary has it → merged keeps it."""
    src_a = _src(name="adzuna", url="http://adzuna.com/6", sid="a6")
    src_b = _src(name="seek", url="http://seek.com/6", sid="s6")
    a = _listing(posted_at=None, sources=[src_a])
    b = _listing(posted_at="2024-01-10", sources=[src_b])
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1
    assert result[0].posted_at == "2024-01-10"


def test_merge_seniority_from_secondary():
    """Primary has no seniority; secondary has it → merged keeps it."""
    src_a = _src(name="adzuna", url="http://adzuna.com/7", sid="a7")
    src_b = _src(name="seek", url="http://seek.com/7", sid="s7")
    sen = Seniority(track="ic", level="senior")
    a = _listing(seniority=None, sources=[src_a])
    b = _listing(seniority=sen, sources=[src_b])
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1
    assert result[0].seniority is not None
    assert result[0].seniority.level == "senior"


# ---------------------------------------------------------------------------
# Merge: first_seen_at is earliest
# ---------------------------------------------------------------------------


def test_merge_earliest_first_seen_at():
    """Merged listing keeps the earliest first_seen_at."""
    src_a = _src(name="adzuna", url="http://adzuna.com/8", sid="a8")
    src_b = _src(name="seek", url="http://seek.com/8", sid="s8")
    a = _listing(first_seen_at="2024-01-20", sources=[src_a])
    b = _listing(first_seen_at="2024-01-10", sources=[src_b])
    assert a.id == b.id
    result = run([a, b])
    assert result[0].first_seen_at == "2024-01-10"


def test_merge_earliest_first_seen_at_primary_is_earlier():
    """When primary was seen first, that date is kept."""
    src_a = _src(name="adzuna", url="http://adzuna.com/9", sid="a9")
    src_b = _src(name="seek", url="http://seek.com/9", sid="s9")
    a = _listing(first_seen_at="2024-01-05", sources=[src_a])
    b = _listing(first_seen_at="2024-01-15", sources=[src_b])
    assert a.id == b.id
    result = run([a, b])
    assert result[0].first_seen_at == "2024-01-05"


# ---------------------------------------------------------------------------
# Merge: content_hash recomputed
# ---------------------------------------------------------------------------


def test_merge_content_hash_recomputed():
    """After merging, content_hash matches derive_content_hash of merged listing."""
    src_a = _src(name="adzuna", url="http://adzuna.com/10", sid="a10")
    src_b = _src(name="seek", url="http://seek.com/10", sid="s10")
    sal = Salary(min=80_000, max=100_000, currency="AUD", period="year", raw="$80k-$100k")
    a = _listing(salary=None, sources=[src_a])
    b = _listing(salary=sal, sources=[src_b])
    result = run([a, b])
    assert len(result) == 1
    expected_hash = derive_content_hash(result[0])
    assert result[0].content_hash == expected_hash


# ---------------------------------------------------------------------------
# Merge: primary fields win on conflict
# ---------------------------------------------------------------------------


def test_merge_primary_salary_wins_on_conflict():
    """When both have salary, primary's salary is kept."""
    src_a = _src(name="adzuna", url="http://adzuna.com/11", sid="a11")
    src_b = _src(name="seek", url="http://seek.com/11", sid="s11")
    sal_a = Salary(min=100_000, max=130_000, currency="AUD", period="year")
    sal_b = Salary(min=90_000, max=110_000, currency="AUD", period="year")
    a = _listing(salary=sal_a, sources=[src_a])
    b = _listing(salary=sal_b, sources=[src_b])
    assert a.id == b.id
    result = run([a, b])
    assert result[0].salary.min == 100_000


# ---------------------------------------------------------------------------
# URL-based merge (different ids, overlapping URL)
# ---------------------------------------------------------------------------


def test_url_based_merge_different_ids():
    """Two listings with different ids but the same URL are merged into one."""
    shared_url = "http://company.com/jobs/123"
    src_a = _src(name="adzuna", url=shared_url, sid="xa1")
    src_b = _src(name="seek", url=shared_url, sid="xs1")
    # Use different companies so ids differ
    a = _listing(company="Acme Corp", sources=[src_a])
    b = _listing(company="ACME Corporation", sources=[src_b])
    # These have different ids due to different company spellings after normalization
    # (the URL overlap should still merge them)
    # If by coincidence the ids match, the test is still valid (id-based merge)
    result = run([a, b])
    # Should be merged to 1 (either via id or URL)
    assert len(result) == 1
    assert _source_count(result[0]) == 2


def test_url_based_merge_preserves_all_sources():
    """URL merge keeps sources from both listings."""
    shared_url = "http://company.com/jobs/456"
    src_a = _src(name="adzuna", url=shared_url, sid="ya1")
    src_b = _src(name="seek", url=shared_url, sid="ys1")
    a = _listing(
        company="Alpha", title="Backend Engineer", city="Melbourne", country="AU", sources=[src_a]
    )
    b = _listing(
        company="Beta", title="Backend Engineer", city="Melbourne", country="AU", sources=[src_b]
    )
    result = run([a, b])
    assert len(result) == 1
    source_names = {s.name for s in result[0].sources}
    assert "adzuna" in source_names
    assert "seek" in source_names


# ---------------------------------------------------------------------------
# Source deduplication within a merge
# ---------------------------------------------------------------------------


def test_duplicate_source_not_duplicated():
    """If both listings carry the same (name, source_id), it appears only once."""
    src = _src(name="adzuna", url="http://adzuna.com/20", sid="dup1")
    a = _listing(sources=[src])
    b = _listing(sources=[src])
    assert a.id == b.id
    result = run([a, b])
    assert len(result) == 1
    assert _source_count(result[0]) == 1


# ---------------------------------------------------------------------------
# Distinct pairs (≥10): listings that must NOT be merged
# ---------------------------------------------------------------------------


def test_distinct_different_titles():
    """Senior Engineer and Junior Engineer at the same company are distinct."""
    a = _listing(title="Senior Software Engineer")
    b = _listing(title="Junior Software Engineer")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_different_companies():
    """Same title and location, different companies → distinct."""
    a = _listing(company="Acme")
    b = _listing(company="BetaCorp")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_different_cities():
    """Same company + title, Sydney vs Melbourne → distinct."""
    a = _listing(city="Sydney", region="NSW")
    b = _listing(city="Melbourne", region="VIC")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_onsite_vs_remote():
    """Onsite in Sydney vs remote (no city) are distinct."""
    a = _listing(city="Sydney", country="AU", is_remote=False)
    b = _listing(city=None, region=None, country=None, is_remote=True, location_raw="Remote")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_frontend_vs_backend():
    """Frontend Engineer vs Backend Engineer at the same company → distinct."""
    a = _listing(title="Frontend Engineer")
    b = _listing(title="Backend Engineer")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_product_vs_engineering_manager():
    """Product Manager vs Engineering Manager → distinct."""
    a = _listing(title="Product Manager")
    b = _listing(title="Engineering Manager")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_director_vs_vp():
    """Director of Engineering vs VP of Engineering → distinct."""
    a = _listing(title="Director of Engineering")
    b = _listing(title="VP of Engineering")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_staff_vs_principal():
    """Staff Engineer vs Principal Engineer → distinct."""
    a = _listing(title="Staff Software Engineer")
    b = _listing(title="Principal Software Engineer")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_different_countries():
    """Same company + title, Australia vs United States → distinct."""
    a = _listing(city=None, region=None, country="AU", is_remote=True, location_raw="Remote AU")
    b = _listing(city=None, region=None, country="US", is_remote=True, location_raw="Remote US")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_city_vs_country_key():
    """city='Sydney' (key='sydney') vs city=None,country='AU' (key='au') → distinct."""
    a = _listing(city="Sydney", country="AU")
    b = _listing(city=None, country="AU", location_raw="Australia")
    assert a.id != b.id
    result = run([a, b])
    assert len(result) == 2


def test_distinct_different_no_url_overlap():
    """Ten unrelated listings produce ten distinct outputs."""
    listings = [_listing(company=f"Company{i}", title=f"Role{i}") for i in range(10)]
    result = run(listings)
    assert len(result) == 10


# ---------------------------------------------------------------------------
# Multi-listing mix: merge some, keep others distinct
# ---------------------------------------------------------------------------


def test_mix_merge_and_distinct():
    """3 listings where two share an id and one is distinct → 2 results."""
    src_a = _src(name="adzuna", url="http://adzuna.com/30", sid="m1")
    src_b = _src(name="seek", url="http://seek.com/30", sid="m2")
    a = _listing(title="Software Engineer", sources=[src_a])
    b = _listing(title="Software Engineer", sources=[src_b])  # same id as a
    c = _listing(title="Product Manager")  # distinct
    assert a.id == b.id
    assert a.id != c.id
    result = run([a, b, c])
    assert len(result) == 2
    # Find the merged one (has 2 sources)
    merged = next(r for r in result if r.id == a.id)
    assert _source_count(merged) == 2


def test_three_way_id_merge():
    """Three listings with the same id merge into one with three source links."""
    src_a = _src(name="adzuna", url="http://adzuna.com/31", sid="t1")
    src_b = _src(name="seek", url="http://seek.com/31", sid="t2")
    src_c = _src(name="linkedin", url="http://linkedin.com/31", sid="t3")
    a = _listing(sources=[src_a])
    b = _listing(sources=[src_b])
    c = _listing(sources=[src_c])
    assert a.id == b.id == c.id
    result = run([a, b, c])
    assert len(result) == 1
    assert _source_count(result[0]) == 3


# ---------------------------------------------------------------------------
# Empty URLs are not identity signals
# ---------------------------------------------------------------------------


def test_empty_urls_never_merge():
    """Two different roles that both lack a URL must NOT be merged."""
    a = _listing(title="Engineer", company="Acme", sources=[_src(url="")])
    b = _listing(title="Designer", company="Zeta", sources=[_src(url="")])
    out = run([a, b])
    assert len(out) == 2


def test_merge_prefers_more_complete_location():
    """Spec 03: keep the most complete non-null fields — location included."""
    # Same identity key (company+title+city|country) via country-only vs full parse
    sparse = _listing(city=None, region=None, country="AU", location_raw="Australia")
    rich = _listing(city=None, region="NSW", country="AU", location_raw="NSW, Australia")
    out = run([sparse, rich])
    assert len(out) == 1
    assert out[0].location.region == "NSW"
    assert out[0].location.country == "AU"
