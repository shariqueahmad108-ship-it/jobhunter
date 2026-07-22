# 04 — Technical Plan & Roadmap

This is *how* the tool gets built from the specs. It commits to as little as possible up front —
the point of the spec-first approach is that these choices can change without rewriting the
product and functional specs.

## Architecture

A single pipeline, one stage per functional-spec stage, wired together by a runner:

```
┌────────────┐   ┌───────────┐   ┌────────┐   ┌────────────┐   ┌────────┐   ┌───────┐
│  Source    │──▶│ Normalizer│──▶│ Deduper│──▶│ HardFilter │──▶│ Scorer │──▶│ Digest│
│  adapters  │   └───────────┘   └────────┘   └────────────┘   └────────┘   └───────┘
└────────────┘         ▲                            ▲               ▲            │
      ▲                └──────────── profile.yaml ──┴───────────────┘            ▼
      │                                                                    seen-state store
  APIs/feeds
```

Design commitments that flow from the spec's principles:

- **Source adapters are pluggable.** Each implements `search(query) -> [RawListing]` and a
  `normalize(raw) -> JobListing`. Adding a board = adding an adapter, no pipeline changes.
- **The profile is the only user-facing config.** Filter and scorer read it; nothing is hardcoded.
- **Scoring is a transparent weighted sum**, not an opaque model — satisfies "explainable over clever."
  (An LLM may *assist* a component, e.g. semantic skill matching, but the aggregation stays inspectable.)
- **State is a local file** (seen/dismissed ids, last run). Single-user, no database needed for v1.

## Data sources — the key constraint

Research finding that shapes everything: **the big consumer boards (LinkedIn, Indeed) no longer
offer open public job-search APIs, and scraping them violates their terms.** So the plan builds
on sources that *permit* programmatic access:

- **Adzuna API** — free developer tier, aggregates listings across 12 countries **including
  Australia**; supports keyword + location + salary queries. Strong primary source for a
  Sydney-based search. ([developer.adzuna.com](https://developer.adzuna.com/))
- **USAJobs API** — official, free, but **US federal only**; include only if US roles matter.
- **Official/company feeds** — many ATSs (Greenhouse, Lever, Ashby, Workable) expose public JSON
  job boards per company; ideal for target-company watchlists. Permitted and stable.
- **Google Jobs via a paid SERP API** (e.g. SerpAPI) — broad coverage without scraping Google
  directly; a cost/coverage tradeoff to decide later.
- **RSS/Atom job feeds** where boards publish them.

Principle: **prefer official APIs and permitted feeds; never build on access that breaks a
site's terms.** Start with Adzuna + a couple of ATS company feeds; add sources as adapters later.

> Note: there are community MCP servers that wrap the Adzuna API. Since this session already runs
> in an MCP-capable environment, wrapping a source as an MCP tool is a viable alternative to a
> code adapter — flagged as an open question below.

## Tech stack (proposed, not locked)

- **Language:** Python 3.11+. Rationale: fast to write, great HTTP/YAML/data libraries, easy to
  schedule, and it matches "generate the implementation from the spec" well.
- **Config:** YAML (`profile.yaml`) validated against a schema on load (fail loud on typos).
- **HTTP:** `httpx` with retries + per-source rate limiting.
- **State:** a local JSON or SQLite file for seen/dismissed ids.
- **Output:** Markdown digest (renders anywhere) + JSON data file; optional HTML for a nicer read.
- **Scheduling:** a scheduled task / cron for the "weekday morning digest" mode.
- **Tests:** each pipeline stage unit-tested against fixtures; the acceptance criteria in
  `02-functional-spec.md` become the test checklist.

## Phased roadmap

Each phase is independently useful and maps to functional-spec stages.

**Phase 1 — Ingest + filter (the core value).**
Adzuna adapter → normalize → dedupe → hard filter → plain Markdown list. Profile drives queries
and hard requirements. Deliverable: "run it, get a filtered list of real Sydney roles." No scoring yet.

**Phase 2 — Scoring + ranking.**
Add the weighted scorer with per-component reasons, ranking, and the display threshold.
Deliverable: the shortlist is now *ordered and explained*.

**Phase 3 — Freshness + digest polish.**
Seen/dismissed state, "new since last run," the run report/tally, HTML digest, and a dismiss workflow.
Deliverable: re-running shows "0 new"; dismissals stick.

**Phase 4 — More sources + scheduling.**
Add ATS company-feed adapters and/or a second API; wire up the scheduled weekday-morning run and
a delivery mechanism.

**Later / out of current scope:** resume tailoring, cover-letter drafting, full application tracker
(these are separate modules, explicitly non-goals of this spec).

## Open questions (resolve before Phase 1)

1. **Geography:** Australia-only (Adzuna AU), or also remote-international / US roles? Affects which sources ship first.
2. **Salary data:** how important is it, given many listings omit it? Confirm `keep_unknown_salary` default.
3. **Delivery for scheduled mode:** file in this folder, email, desktop notification, or a running artifact/dashboard?
4. **Adapter vs MCP:** implement sources as in-code adapters, or wrap them as MCP tools this environment can call?
5. **LLM in scoring:** keep scoring purely rule-based, or allow an LLM sub-score for semantic skill/description fit (still inside the transparent weighted sum)?
6. **Paid sources:** is a paid SERP/aggregator API acceptable for broader coverage, or free sources only?
7. **Company watchlist:** is there a specific set of target companies to seed ATS-feed adapters with?

## Definition of done (per phase)

A phase is done when every acceptance criterion for its functional-spec stages passes, the
profile alone (no code edit) can change its behavior where the spec says so, and a run over real
listings produces the expected digest.
