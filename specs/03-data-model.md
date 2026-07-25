# 03 — Data Model

Three core shapes drive the whole tool: the **JobListing** (what comes in), the **Profile**
(what I want — the config that makes "filter" and "score" concrete), and the **ScoredResult**
(what goes out). Types are shown language-neutrally; the implementation picks concrete types.

Field annotations: **req** = required (validation fails if absent), everything else optional with
the shown default. The profile is validated against this schema on load — unknown keys and type
mismatches are hard errors ("fail loud on typos").

---

## Seniority: two tracks

Seniority is modeled as a **track + level**, because IC and management ladders aren't comparable
(is "manager" above "principal"? — the model refuses the question):

```yaml
track: enum(ic, management)
# ordered within each track:
#   ic:         intern < junior < mid < senior < staff < principal
#   management: manager < senior_manager < director < vp
```

Filters and scoring compare levels **within a track only**. A profile may target both tracks
(e.g. Staff Engineer *and* Engineering Manager) with independent bounds, or disable a track.

## JobListing (canonical, post-normalization)

```yaml
id:            string        # req; stable hash of the normalized identity key (see notes below)
content_hash:  string        # req; hash of (title, salary min/max/currency/period, location.raw,
                             #      description) — detects material changes; excludes volatile
                             #      fields (sources[], posted_at, first_seen_at)
title:         string        # req
company:       string        # req
location:
  raw:         string        # req; as posted, e.g. "Sydney NSW (Hybrid)"
  city:        string | null
  region:      string | null
  country:     string | null # ISO 3166-1 alpha-2 where possible
  is_remote:   boolean       # req (false when unstated)
salary:
  min:         number | null
  max:         number | null
  currency:    string | null # ISO 4217, e.g. AUD, USD
  period:      enum(year, month, day, hour) | null
  raw:         string | null
seniority:                   # inferred — see 02 §Seniority inference; null = unknown
  track:       enum(ic, management) | null
  level:       string | null # a level from the track's ordered list above
employment:    enum(full_time, part_time, contract, temp, internship) | null
description:   string        # req; plain text, HTML stripped
posted_at:     date | null
sources:                     # req, ≥1; multiple after dedupe merges cross-posts
  - name:      string        # e.g. "adzuna"
    url:       string
    source_id: string        # the source's own id for this posting
first_seen_at: date          # req; when this tool first ingested it
```

## Profile (the user's criteria — see `profile.example.yaml`)

The profile has three parts: **queries** (what to search for), **hard requirements** (pass/fail
filters), and **preferences + weights** (how to score survivors).

```yaml
identity:
  target_skills:    [string]     # req; skills that count toward keyword match
  target:                        # req; one entry per track I'd accept (≥1)
    - track:  enum(ic, management)
      level:  string             # my target level on that track; scoring measures distance

queries:                         # queries.keywords is the ONLY source of search terms
  keywords:        [string]      # req; e.g. titles and phrases to search sources for
  locations:       [string]      # req; human location strings, e.g. "Sydney", "Remote AU"
  max_results_per_query: number  # default 50
  max_requests_per_run:  number  # default 100; hard cap enforced by the runner (see 02 §Stage 1)

hard_requirements:               # STAGE 4 — any failure drops the listing; unknown fields KEEP
  remote_policy:     enum(remote_only, hybrid_ok, onsite_ok, any)   # defined in 02 §Stage 4
  exclude_locations: [string]    # drop roles based here EVEN IF labelled remote (e.g. Sydney);
                                 #   matched against parsed city/region/country, exact, case-insensitive
  remote_countries_allowed: [string] | null   # null = any; else remote listings restricted
                                 #   to a country outside this list lose remote status (02 §Stage 4)
  locations_allowed: [string]    # [] = anywhere (subject to remote_policy + exclude_locations);
                                 #   non-empty = positively restrict to these places; same matching
  seniority:                     # per-track bounds; omit a track to disallow it entirely;
                                 #   omit the WHOLE key = no seniority filtering (all tracks pass)
    ic:         {min: string, max: string | null} | null
    management: {min: string, max: string | null} | null
  salary_floor:      number | null   # null = no floor
  salary_currency:   string          # req if salary_floor set; ISO 4217
  fx_rates:          map<string, number>   # OPTIONAL per-currency OVERRIDES only. The primary
                                           #   rate table is GLOBAL (fx_rates.yaml at repo root,
                                           #   shared by all profiles — market facts, not
                                           #   preferences); cross-rated into salary_currency.
                                           #   Currencies absent everywhere => unknown salary
  keep_unknown_salary: boolean     # default true — don't drop listings with no comparable salary
  exclude_employment: [enum]       # default []; e.g. [contract, internship]
  exclude_keywords:                # deal-breakers; word-boundary, case-insensitive, scoped
    - term:  string
      scope: enum(title, requirements)   # default requirements (= title + description)
  require_keywords:                # domain anchor: match AT LEAST ONE or drop; [] = off
    - term:  string                #   same matching + scope semantics as exclude_keywords
      scope: enum(title, requirements)   # default requirements
  max_age_days:      number        # default 30

preferences:                     # STAGE 5 — soft scoring inputs
  preferred_locations: [string]
  salary_target:       number | null
  preferred_companies: [string]
  deprioritize_keywords: [string]  # title terms (word-boundary, case-insensitive) that
                                   #   PENALISE the skill_match sub-score — off-domain roles
                                   #   sink instead of being dropped (02 §Stage 5)
  # NOTE: industry / company-size preferences are cut from v1 — no configured source provides
  # that data. Reintroduce only alongside an enrichment source (see 04 §Later).

weights:                         # STAGE 5 — relative; 0 disables a component; normalized over
                                 #   the ACTIVE set (see 02 §Stage 5 normalization rule).
                                 #   VALIDATION: at least one weight must be present and > 0;
                                 #   negative weights are invalid (fail loud).
  skill_match:    number
  seniority_fit:  number
  compensation:   number
  location_fit:   number
  company_signal: number
  recency:        number

sources:                         # STAGE 1 — source activation. A source absent from this
                                 #   block, or with enabled: false, is never fetched.
  adzuna:    {enabled: boolean, country: string | null}   # env: ADZUNA_APP_ID/_APP_KEY
  jooble:    {enabled: boolean}                            # env: JOOBLE_API_KEY
  remotive:  {enabled: boolean, categories: [string]}
  remoteok:  {enabled: boolean}
  ats_watchlist:                 # public company job boards; no credentials
    - ats:   enum(greenhouse, lever, ashby, workday)
      slug:  string              # verify with `jobhunter probe` — never guess (05 §5.3)
      name:  string | null       # display name
      workday_path:     string | null    # workday only
      workday_instance: int | null       # workday only
  feeds:                         # any public RSS/Atom job feed
    - name: string
      url:  string
      company_from_title: boolean | null  # feed puts "Company: Title" in <title> — split it
      region_location:    boolean | null  # feed carries scope in <region>; parse it as the
                                          #   location (WeWorkRemotely-style; 02 §Stage 2)

search_mode: enum(active_unemployed, active_employed, passive_employed) | null
                                 # optional posture preset (see 02 §Search posture);
                                 # supplies DEFAULTS for display_threshold, max_shown,
                                 # max_age_days — explicit values below always override

output:
  display_threshold:    number   # default 0; hide scored results below this (0–100)
  max_shown:            number   # default 25; cap on "New this run" section
  show_previously_seen: boolean  # default true; render the "Previously shown" section
  format:               enum(markdown, html, both)   # default markdown
  data_format:          enum(json, csv, both)         # default json; controls the machine-readable
                                 #   data file format written alongside the digest
  keep_raw:             boolean  # default true; persist the pre-filter snapshot that
                                 #   `jobhunter replay` re-scores offline (05 §5.2)
```

## ScoredResult (post-scoring, what the digest renders)

```yaml
listing:        JobListing
score:          number            # 0–100 = 100 × Σ(wᵢ·subᵢ)/Σ(wᵢ) over active weights
components:                       # one per active (non-zero) weight
  - name:       string            # e.g. "skill_match"
    sub:        number            # 0–1 sub-score; 0.5 = neutral (unknown data)
    weight:     number
    reason:     string            # human-readable, e.g. "5/8 target skills present"
summary_reason: string            # single line shown in the digest
rank:           number            # 1 = best; ties: newer first, then id
unknown_flags:  [string]          # e.g. ["level unclear", "remote scope unclear"]
```

## Run state (persisted between runs)

```yaml
schema_version: number            # bump on breaking change; loader migrates or fails loud
seen:                             # replaces bare id list — hash enables change detection
  - id:            string
    content_hash:  string         # as last shown; differs now => "materially changed" => new again
    last_shown_at: date
dismissed_ids:  [string]          # via `jobhunter dismiss`; never re-surface, survives content changes
last_run_at:    date
```

## Run report (per-run metadata, shown in digest header)

```yaml
run_at:            date
sources_used:      [string]
sources_failed:    [ {name, error} ]
requests_made:     number
truncated:         boolean       # true if max_requests_per_run cut ingestion short
ingested_count:    number
after_dedupe:      number
dropped:                         # the filter tally, for debugging criteria
  by_location:     number
  by_seniority:    number
  by_salary:       number
  by_employment:   number
  by_keyword:      number
  by_required:     number          # dropped for matching no require_keywords term
  by_age:          number
  dismissed:       number
fx_rates_stale_days: number | null   # age of fx_rates.yaml when ≥ 90 days, else null;
                                     #   surfaced as a digest banner (02 §Stage 7)
below_threshold:   number        # passed everything, hidden by display_threshold
shown_new:         number
shown_previous:    number
active_weights:    map<string, number>   # so threshold changes are interpretable
search_mode:       string | null  # active posture preset name, or null; shown in digest header
source_stats:      [SourceStat]   # per-source counters (see §Source stats); carried
                                  #   in-memory so the CLI can persist them after render
```

## Source stats (persisted per run; see 05 §5.1)

Appended once per run to `state/<profile>/source_stats.json`. One record per
source per run — history is never rewritten, so trends stay comparable.

```yaml
schema_version: number
runs:
  - run_at:  date
    sources:
      - name:          string    # adapter name as it appears in sources_used
        fetched:       number    # raw listings returned
        contributed:   number    # post-dedupe survivors this source supplied
        sole_source:   number    # survivors ONLY this source supplied
        passed_filter: number    # of contributed, survived Stage 4
        shown:         number    # of passed_filter, rendered in the digest
        dismissed:     number    # of shown, later dismissed by the user
        requests:      number
        failed:        boolean
        error:         string | null
```

Invariant (test-enforced): `shown ≤ passed_filter ≤ contributed ≤ fetched`, and
`sole_source ≤ contributed`. A source absent from the current profile keeps its
historical records and is reported as inactive.

## Run snapshot (pre-filter; see 05 §5.2)

Written when `output.keep_raw` is true, alongside the run's data file. It holds
the **normalized, deduped, pre-filter** listing set plus the profile actually in
force, which is what makes an offline replay reproducible.

```yaml
schema_version:  number
run_at:          date
profile_snapshot: Profile         # the resolved profile, presets already applied
listings:        [JobListing]     # post-normalize, post-dedupe, PRE hard filter
```

Replay reads only this file. It applies `dismissed_ids` from run state (a
dismissal is a real user decision) but writes nothing back — see 05 §5.2.

## Notes on identity & dedupe

- `id` must be **stable across runs** so seen/dismissed state works — derive it from the
  normalized identity key: lowercased, whitespace-collapsed, punctuation-stripped
  `(company + title + city|country)`, with title decorations stripped per the rule table
  (`Sr./Snr → senior`, trailing parenthesized team qualifiers removed). Fully-remote listings
  with no city use `(company + title + country|"remote")`. Never derive `id` from volatile
  fields (URL, posted date).
- `content_hash` is the **change detector**: same `id`, different hash = the posting materially
  changed and may re-surface as new. It deliberately excludes `sources[]` and dates so
  cross-posting churn never re-surfaces a listing.
- When two listings share an `id`, merge their `sources[]`; keep the earliest `first_seen_at`
  and the most complete non-null fields; recompute `content_hash` from the merged record.
