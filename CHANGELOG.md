<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Changelog

Notable changes to JobHunter. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Because behaviour is defined in `specs/`, an entry that changes what the
pipeline does cites the spec section it implements.

## [Unreleased]

### Added

- `jobhunter run -v`/`--verbose` logs one line per source to stderr —
  fetched count, request count, and whether it failed (and why) or fetched
  something Stage 4 filtered out entirely. Previously the digest header's
  aggregate counts couldn't distinguish a dead endpoint, a rate limit, an
  all-filtered source, and a missing credential from one another. See
  `specs/04-technical-plan.md` §Tech stack (CLI).

## [0.1.0] — 2026-07-30

First release. The pipeline runs end to end: it fetches listings from job
boards and company ATS boards, drops the ones failing hard requirements, scores
the survivors against a profile, and writes a ranked digest.

### The pipeline

- Seven stages — ingest, normalize, dedupe, hard filter, score, rank, digest —
  wired end to end by `jobhunter run` (specs/02 §Stages 1-7).
- **Deterministic, rule-based scoring**: a weighted sum over six components
  (skill match, seniority fit, compensation, location fit, company signal,
  recency). No LLM anywhere in the pipeline, so identical inputs always produce
  an identical digest and every score is explainable (specs/02 §Stage 5).
- **Unknown data is kept and marked, never silently dropped** — a listing with
  no salary, unclear seniority or ambiguous remote scope survives filtering and
  carries a flag into the digest (specs/02 §Unknown-data policy).
- Stable listing identity: ids derive from company, normalized title and
  location, so the same role from two sources dedupes to one entry, and a
  content hash detects material changes (specs/03 §Identity & dedupe).
- Dual-track seniority (IC and management) with per-track bounds, so "senior
  engineer" and "engineering manager" are filtered against different ladders.
- Salary annualization across periods and currencies, via a shared
  `fx_rates.yaml` with per-profile overrides. A currency with no rate anywhere
  is treated as unknown rather than as zero.
- Seen-state: a listing appears under "New this run" once, then moves to
  "Previously shown" until it materially changes. Listings beyond `max_shown`
  are **not** recorded as seen, so they resurface next run rather than being
  silently lost (specs/02 §Stage 7).

### Sources

- Aggregators: Adzuna, Jooble (both need a free API key).
- Remote boards: RemoteOK, Remotive (no key).
- Company ATS boards: Greenhouse, Lever, Ashby, Workday, via a watchlist of
  `{ats, slug}` entries (no key).
- Any public RSS/Atom job feed, with optional per-feed parsing flags for feeds
  that pack company into the title or scope into `<region>`.
- Every source activates from the single unified `sources:` block in the
  profile. A source that is absent or `enabled: false` is never constructed; one
  enabled without its credential warns and is skipped rather than failing the
  run.

### Configuration and output

- Everything person-specific lives in one `profile.yaml`, validated on load
  against the schema in specs/03 — unknown keys and type errors fail loudly
  rather than being ignored.
- Search-posture presets (`active_unemployed`, `active_employed`,
  `passive_employed`) set sensible defaults for threshold, digest size and
  listing age; explicit values always win.
- Digest as Markdown and/or HTML, plus a JSON and/or CSV data companion.
- Multi-profile support: a non-default profile automatically gets its own
  seen-state, digest filenames and source stats, so two searches never
  contaminate each other.

### Operator tooling

- `jobhunter dismiss` / `undismiss` / `dismissed` — permanent dismissals that
  survive content changes. Accepts the short id shown in the digest or a full
  id; an unknown or ambiguous id is an error (specs/02 §Stage 7).
- `jobhunter sources` — per-source contribution stats across runs, so a source
  that never earns its requests is visible (specs/05 §5.1).
- `jobhunter replay` — re-score a saved run snapshot offline with `--set`
  overrides and `--diff` against another run. The tool for tuning weights
  without re-fetching (specs/05 §5.2).
- `jobhunter probe` — detect which ATS a careers page uses, or re-check every
  board in the watchlist. Never guess a slug (specs/05 §5.3).
- `jobhunter doctor` — check profile, credentials and every enabled source, and
  exit non-zero if anything is broken. Distinguishes a dead board from a
  rate-limited source from a keyword that matched nothing (specs/05 §5.4).
- `jobhunter --version`.

### Project

- Apache-2.0 licensed, with SPDX headers on every source file.
- CI on Python 3.11, 3.12 and 3.13: ruff, mypy, the test suite, a coverage
  floor, a dependency audit, and a cold install that walks the README quick
  start so the out-of-the-box path cannot rot.
- The test suite is hermetic — `tests/conftest.py` blocks real sockets, so an
  adapter test that forgets to mock `httpx` fails loudly instead of depending on
  a live job board.
- A weekly source canary runs `doctor` against the shipped example profile and
  files an issue when a board dies.
- `make check` runs exactly what CI runs.

### Known limitations

- `fx_rates.yaml` is maintained by hand; `run` warns once it is over 90 days
  old but nothing refreshes it.
- No way yet to ask why one specific listing was dropped — the filter tally is
  aggregate.
- No verbosity control: a source returning nothing is not distinguishable from a
  failing one in `run` output. Use `doctor`.

### Deliberately out of scope

LLM calls anywhere in the pipeline, automated applications, application or
interview tracking, and a web UI or hosted service. See specs/01 §Non-goals.
LinkedIn and direct Seek integration are out for access-model reasons, and
Careerjet was removed after its v4 API proved unfit for personal use — see the
guardrails in `IMPLEMENTATION_PLAN.md`.

[Unreleased]: https://github.com/justinmclean/jobhunter/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/justinmclean/jobhunter/releases/tag/v0.1.0
