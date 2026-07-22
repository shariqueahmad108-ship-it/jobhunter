# 03 — Data Model

Three core shapes drive the whole tool: the **JobListing** (what comes in), the **Profile**
(what I want — the config that makes "filter" and "score" concrete), and the **ScoredResult**
(what goes out). Types are shown language-neutrally; the implementation picks concrete types.

---

## JobListing (canonical, post-normalization)

```yaml
id:            string        # stable hash of (company + title + primary_location); the dedupe key
title:         string
company:       string
location:
  raw:         string        # as posted, e.g. "Sydney NSW (Hybrid)"
  city:        string | null
  region:      string | null
  country:     string | null # ISO where possible
  is_remote:   boolean
salary:
  min:         number | null
  max:         number | null
  currency:    string | null # ISO 4217, e.g. AUD, USD
  period:      enum(year, month, day, hour) | null
  raw:         string | null
seniority:     enum(intern, junior, mid, senior, staff, lead, principal, manager, director) | null
employment:    enum(full_time, part_time, contract, temp, internship) | null
description:   string        # plain text, HTML stripped
posted_at:     date | null
sources:                     # ≥1; multiple after dedupe merges cross-posts
  - name:      string        # e.g. "adzuna"
    url:       string
    source_id: string        # the source's own id for this posting
first_seen_at: date          # when this tool first ingested it
```

## Profile (the user's criteria — see `profile.example.yaml`)

The profile has three parts: **queries** (what to search for), **hard requirements** (pass/fail
filters), and **preferences + weights** (how to score survivors).

```yaml
identity:
  target_titles:   [string]      # roles I'd take, used to build source queries
  target_skills:   [string]      # skills that count toward keyword match
  target_seniority: string       # my target level; scoring measures distance from this

queries:                          # cartesian product of terms x locations, per source
  keywords:        [string]
  locations:       [string]       # human location strings, e.g. "Sydney", "Remote AU"
  max_results_per_query: number

hard_requirements:                # STAGE 4 — any failure drops the listing
  remote_policy:     enum(remote_only, hybrid_ok, onsite_ok, any)
  exclude_locations: [string]     # drop roles based here even if labelled remote (e.g. Sydney)
  locations_allowed: [string]     # [] = anywhere (subject to remote_policy + exclude_locations)
  min_seniority:     string
  max_seniority:     string | null
  salary_floor:      number
  salary_currency:   string
  keep_unknown_salary: boolean    # true = don't drop listings with no salary
  exclude_keywords:  [string]     # deal-breakers
  max_age_days:      number

preferences:                      # STAGE 5 — soft scoring inputs
  preferred_locations: [string]
  salary_target:       number
  preferred_companies: [string]
  preferred_industries:[string]
  avoid_industries:    [string]

weights:                          # STAGE 5 — must be tunable; 0 disables a component
  skill_match:   number
  seniority_fit: number
  compensation:  number
  location_fit:  number
  company_signal:number
  recency:       number

output:
  display_threshold: number       # hide scored results below this (0–100)
  format:            enum(markdown, html, both)
```

## ScoredResult (post-scoring, what the digest renders)

```yaml
listing:        JobListing
score:          number            # 0–100, weighted sum normalized
components:                       # one per active weight
  - name:       string            # e.g. "skill_match"
    raw:        number            # 0–1 sub-score
    weight:     number
    reason:     string            # human-readable, e.g. "5/8 target skills present"
summary_reason: string            # single line shown in the digest
rank:           number            # 1 = best
```

## Run state (persisted between runs)

```yaml
seen_ids:       [string]          # listing ids already shown; drives freshness filter
dismissed_ids:  [string]          # ids I explicitly rejected; never re-surface
last_run_at:    date
```

## Run report (per-run metadata, shown in digest header)

```yaml
run_at:            date
sources_used:      [string]
sources_failed:    [ {name, error} ]
ingested_count:    number
after_dedupe:      number
dropped:                         # the filter tally, for debugging criteria
  by_location:     number
  by_seniority:    number
  by_salary:       number
  by_keyword:      number
  by_age:          number
  already_seen:    number
shown_count:       number
```

## Notes on identity & dedupe

- `id` must be **stable across runs** so freshness/dismissal work — derive it from normalized,
  lowercased, whitespace-collapsed (company + title + city/country), not from volatile fields like URL or posted date.
- When two listings share an `id`, merge their `sources[]`; keep the earliest `first_seen_at`
  and the most complete non-null fields.
