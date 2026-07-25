# 04 — Technical Plan & Roadmap

This is *how* the tool gets built from the specs. It commits to as little as possible up front —
the point of the spec-first approach is that these choices can change without rewriting the
product and functional specs.

## Decisions (resolved 2026-07-22)

Formerly open questions, now locked into the specs:

1. **Industry / company-size scoring: cut from v1.** No permitted source provides that data.
   Company signal = the preferred-companies list only. Reintroduce industry/size only alongside an
   enrichment source (see §Later).
2. **Scoring is purely rule-based and deterministic in v1.** No LLM in the scoring path; a run
   makes no network calls after ingest. LLM-assisted semantic skill matching is a *later* option
   and, if added, must cache sub-scores by `(listing id, content_hash, profile hash)` so the
   determinism acceptance criterion keeps holding.
3. **Dismissals are a CLI command.** `jobhunter dismiss <id>` / `undismiss` / `dismissed`
   (see 02 §Stage 7). Ids are printed in the digest for copy-paste.
4. **Query terms have one owner.** `queries.keywords` is the only source of search terms;
   `identity.target_titles` was removed from the profile (titles belong in `queries.keywords`,
   skills in `identity.target_skills`).
5. **Location policy is configured, not hardcoded.** The combination
   `remote_policy` + `exclude_locations` + `locations_allowed` expresses any policy
   (e.g. remote-only while excluding "remote, but must be local to X" postings)
   (semantics in 02 §Stage 4). `exclude_locations` drops a role based there even when it's
   labelled remote.

## Architecture

A single pipeline, one stage per functional-spec stage, wired together by a runner:

```
┌────────────┐   ┌───────────┐   ┌────────┐   ┌────────────┐   ┌────────┐   ┌───────┐
│  Source    │──▶│ Normalizer│──▶│ Deduper│──▶│ HardFilter │──▶│ Scorer │──▶│ Digest│
│  adapters  │   └───────────┘   └────────┘   └────────────┘   └────────┘   └───────┘
└────────────┘         ▲                            ▲               ▲            │
      ▲                └──────────── profile.yaml ──┴───────────────┘            ▼
      │                                                                    seen-state store
  APIs/feeds                                                              ▲
                                                              `jobhunter dismiss` CLI
```

Design commitments that flow from the spec's principles:

- **Source adapters are pluggable.** Each implements `search(query) -> [RawListing]` and a
  `normalize(raw) -> JobListing`. Adding a board = adding an adapter, no pipeline changes.
- **The profile is the only user-facing config**, validated against the schema in
  `03-data-model.md` on load (unknown keys and type errors fail loud). Filter and scorer read it;
  nothing is hardcoded.
- **Scoring is a transparent, deterministic weighted sum** — satisfies "explainable over clever."
- **One shared rule table** drives seniority inference *and* title normalization for dedupe
  (02 §Stage 2/3), so the two never drift apart.
- **State is a local, versioned file** (`schema_version` + seen/dismissed + last run). Single-user,
  no database needed for v1.

## Data sources — the key constraint

Research finding that shapes everything: **the big consumer boards (LinkedIn, Indeed) no longer
offer open public job-search APIs, and scraping them violates their terms.** So the plan builds
on sources that *permit* programmatic access:

- **Adzuna API** — free developer tier, aggregates listings across 12 countries **including
  Australia**; supports keyword + location + salary queries. Strong primary source for an
  Australian search. ([developer.adzuna.com](https://developer.adzuna.com/))
- **USAJobs API** — official, free, but **US federal only**; include only if US roles matter.
- **Official/company feeds** — many ATSs (Greenhouse, Lever, Ashby, Workable) expose public JSON
  job boards per company; ideal for target-company watchlists. Permitted and stable.
- **Google Jobs via a paid SERP API** (e.g. SerpAPI) — broad coverage without scraping Google
  directly; a cost/coverage tradeoff to decide later.
- **Jooble API** — free key, aggregator with AU coverage; the second aggregator alongside Adzuna.
- **Remotive / RemoteOK** — remote-only boards with public JSON, no credentials.
- **RSS/Atom job feeds** where boards publish them (e.g. WeWorkRemotely) — see the per-feed
  parsing options in 02 §Stage 1, which exist because RSS has no agreed job schema.

**Rejected after evaluation:** Careerjet — the v4 API is built around a publisher relaying the
*end user's* IP and user-agent, which a personal CLI cannot honestly supply. Jooble fills that
slot. The general lesson: check an aggregator API's personal-use fit before writing the adapter.

Principle: **prefer official APIs and permitted feeds; never build on access that breaks a
site's terms.** Start with Adzuna + a couple of ATS company feeds; add sources as adapters later.

> Note: there are community MCP servers that wrap the Adzuna API. Since this session already runs
> in an MCP-capable environment, wrapping a source as an MCP tool is a viable alternative to a
> code adapter — flagged as an open question below.

## Tech stack (proposed, not locked)

- **Language:** Python 3.11+. Rationale: fast to write, great HTTP/YAML/data libraries, easy to
  schedule, and it matches "generate the implementation from the spec" well.
- **Config:** YAML (`profile.yaml`) validated against a schema on load (fail loud on typos).
- **HTTP:** `httpx` with retries + per-source rate limiting + the `max_requests_per_run` cap.
- **State:** a local JSON or SQLite file for seen/dismissed state, with `schema_version`.
- **CLI:** one entry point with sub-commands:
  `jobhunter run [--profile PATH] [--state PATH] [--output-dir DIR]`,
  `jobhunter dismiss <id>`, `jobhunter undismiss <id>`, `jobhunter dismissed`,
  `jobhunter sources [--profile P] [--last N] [--json]`,
  `jobhunter replay <run-file> [--profile P] [--set k=v] [--diff <file>] [--out FILE]`,
  `jobhunter probe [URL|slug] [--ats TYPE] [--check] [--profile P]`.
  `--output-dir` overrides the default output directory (`digests/` peer to the state dir).
- **Multi-profile namespacing:** when a non-default profile file is used (e.g.
  `profile-ospo.yaml`), the run command automatically namespaces all per-profile files by the
  profile's stem: state → `state-ospo.yaml`, source stats → `source_stats-ospo.json`, digest
  and data files → `YYYY-MM-DD-ospo.md/.json`. This keeps independent profiles from sharing
  seen-state or overwriting each other's output.
- **Output:** Markdown digest (renders anywhere) + JSON/CSV data file; optional HTML for a
  nicer read. Output format controlled by `output.format`; data format by `output.data_format`.
- **Scheduling:** a scheduled task / cron for the "weekday morning digest" mode.
- **Tests:** each pipeline stage unit-tested against the golden fixture corpus; the acceptance
  criteria in `02-functional-spec.md` become the test checklist.

## Fixtures — the golden corpus (Phase 1 deliverable)

The safety net that makes "amend the spec, then regenerate" safe:

- **30–50 real (anonymized) listings** captured from the live sources, stored as raw payloads +
  expected outcomes at every stage: expected normalized `JobListing` (incl. inferred seniority and
  parsed salary), expected merge/no-merge pairs (≥10 of each), expected filter verdict per hard
  filter under a fixture profile, and expected score ±1 under fixture weights.
- The corpus must cover the awkward cases the spec calls out: missing salary, day-rate contracts,
  foreign-currency salaries (with and without pinned rates), "Remote" with no country, a
  remote-labelled role based in an excluded location ("Remote — Sydney-based"), unknown
  seniority, dual-track titles, cross-posted duplicates, and a materially-changed listing.
- **Every regeneration of any stage must pass the corpus before it replaces the old code.**

## Regeneration policy

"Amend the spec, not the code" (README) operates **per stage, gated by the corpus**: change the
relevant spec section, regenerate that stage only, and accept the regeneration only when all
fixture tests for that stage (and the end-to-end run) pass. Full-pipeline regeneration is a last
resort, behind the same gate.

## Phased roadmap

Each phase is independently useful and maps to functional-spec stages.

**Phase 1 — Ingest + filter (the core value).** *(built; the golden fixture corpus is
still thin — see `IMPLEMENTATION_PLAN.md`)*
Adzuna adapter → normalize (incl. seniority inference + salary/period parsing) → dedupe →
hard filter → plain Markdown list. Profile drives queries and hard requirements.
**Includes the golden fixture corpus and its test harness.**
Deliverable: "run it, get a filtered list of real roles," with the filter tally. No scoring yet.

**Phase 2 — Scoring + ranking.** *(built)*
Add the weighted scorer with per-component reasons, the normalization rule, ranking, and the
display threshold. Deliverable: the shortlist is now *ordered and explained*.

**Phase 3 — Freshness + digest polish.** *(built)*
Seen-state with `content_hash` change detection, "New this run" vs "Previously shown" sections,
the run report/tally, HTML digest, and the `dismiss`/`undismiss` CLI.
Deliverable: re-running shows "0 new" with prior roles still reviewable; dismissals stick;
a salary change re-surfaces a listing.

**Phase 4 — More sources + scheduling.** *(built)*
Add ATS company-feed adapters and/or a second API; wire up the scheduled weekday-morning run and
a delivery mechanism.

**Phase 5 — Operator tooling (`specs/05-operator-tooling.md`).** *(built)*
Per-source contribution stats, offline `replay` re-scoring, and ATS board
`probe`/`--check`. Deliverable: source value and calibration changes are
measurable without a live run, and no board enters the watchlist unverified.

**Later / out of current scope:** industry/company-size enrichment (unlocks the cut scoring
signals), LLM-assisted semantic skill matching (cached per Decisions §2), resume tailoring,
cover-letter drafting, full application tracker (separate modules, explicitly non-goals of this spec).

## Open questions (resolve before the affected phase)

1. **Remote scope** *(Phase 1)*: the search is remote-only (Decisions §5) — but remote *where*?
   Australia-only (Adzuna AU), or also remote-international / US roles? Affects which sources
   ship first, which `fx_rates` need pinning, and how "Remote" with no country is treated.
2. **Delivery for scheduled mode** *(Phase 4)*: file in this folder, email, desktop notification,
   or a running artifact/dashboard?
3. **Adapter vs MCP** *(Phase 1)*: implement sources as in-code adapters, or wrap them as MCP
   tools this environment can call?
4. **Paid sources** *(Phase 4)*: is a paid SERP/aggregator API acceptable for broader coverage,
   or free sources only?
5. **Company watchlist** *(Phase 4)*: which target companies seed the ATS-feed adapters?
6. **Seniority rule table seed** *(Phase 1)*: start from a hand-written pattern list — does it
   need review against real AU listings before locking Phase 1 fixtures?

## Definition of done (per phase)

A phase is done when every acceptance criterion for its functional-spec stages passes against the
golden corpus, the profile alone (no code edit) can change its behavior where the spec says so,
and a run over real listings produces the expected digest.
