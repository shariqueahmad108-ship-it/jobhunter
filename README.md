<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# JobHunter

[![CI](https://github.com/justinmclean/jobhunter/actions/workflows/ci.yml/badge.svg)](https://github.com/justinmclean/jobhunter/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Licence: Apache-2.0](https://img.shields.io/badge/licence-Apache--2.0-green.svg)](LICENSE)

A spec-driven job-search pipeline. It fetches listings from job
boards and company ATS boards, drops the ones that fail your hard
requirements, scores the survivors against your profile, and writes a ranked
digest — so you review a short list instead of scrolling job sites.

Everything that makes the tool *yours* lives in one file, `profile.yaml`.
There is nothing person-specific in the code.

- Deterministic, rule-based scoring — no LLM in the pipeline, so the same
  inputs always produce the same digest and every score is explainable.
- Unknown data is kept and marked, never silently dropped.
- Stateless between runs apart from a small state file: what you have already
  been shown, and what you have dismissed.

## Quick start

```bash
git clone <this repo> && cd JobHunter
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -e ".[dev]"

cp specs/profile.example.yaml profile.yaml   # then edit it — this is the whole config
jobhunter run
```

The example profile ships with the credential-free sources on (RemoteOK,
Remotive, three Greenhouse boards and one RSS feed), so that first run returns
real listings before you have configured anything. The keyed aggregators are
commented out in the same block — uncomment them once their env vars are set:

```bash
export ADZUNA_APP_ID=... ADZUNA_APP_KEY=...  # free key: https://developer.adzuna.com
export JOOBLE_API_KEY=...                    # free key: https://jooble.org/api/about
```

The digest lands in `digests/YYYY-MM-DD.md` (plus a `.json` companion).
`profile.yaml` is git-ignored — your criteria stay out of the repo.

## What a digest looks like

```markdown
# JobHunter — Run Report

Run at: 2026-07-30
Sources: greenhouse, remoteok, remotive, feeds
Requests made: 24
Ingested: 412 | After dedupe: 388 | Dropped: 351 | New: 9 | Previously shown: 4
Filter tally: location: 210, seniority: 74, salary: 41, employment: 0,
keyword: 18, missing required: 0, too old: 8, dismissed: 0
Active weights: skill_match: 30, seniority_fit: 20, compensation: 20,
location_fit: 15, company_signal: 10, recency: 5

## New This Run (9)

### #1 `4b1c9f02` — Staff Engineer, Platform at Example Corp
Score: 84/100 | Location: Remote (AU) | Salary: AUD 210,000–240,000 /year | Posted: 2026-07-29
Reason: 4 of 5 target skills; exact match: staff (ic); salary above target
Sources: [greenhouse](https://boards.greenhouse.io/examplecorp/jobs/1)

### #2 `9de4a115` — Engineering Manager, Developer Experience at Sample Inc
Score: 77/100 | Location: Remote | Salary: not listed | Posted: 2026-07-28
Reason: exact match: manager (management); remote role
Sources: [remotive](https://remotive.com/remote-jobs/1)
Flags: salary unknown, remote scope unclear
```

Every score is explainable because scoring is a rule-based weighted sum — no
LLM in the pipeline, so the same inputs always produce the same digest. The
short id in backticks is what you pass to `jobhunter dismiss`.

## Commands

| Command | What it does |
|---|---|
| `jobhunter run` | The pipeline: ingest → normalize → dedupe → filter → score → rank → digest. |
| `jobhunter dismiss <id> [<id>...]` | Permanently hide listings from future digests. The short id shown in the digest is enough. |
| `jobhunter undismiss <id>` | Restore a dismissed listing (short id also fine). |
| `jobhunter dismissed` | List currently dismissed ids. |
| `jobhunter sources [--last N] [--json]` | Per-source contribution stats across runs — which sources actually earn their requests. |
| `jobhunter replay RUN-FILE [--set K=V] [--diff OTHER]` | Re-score a saved run offline, with no network calls. |
| `jobhunter probe URL_OR_SLUG` / `probe --check` | Detect which ATS a company careers page uses, or re-check every board in your watchlist. |
| `jobhunter doctor [--offline] [--json]` | Check the profile, credentials and every enabled source; exits 1 if anything is broken. |

`replay` is the one to reach for when tuning: run once, then replay the same
snapshot with different weights or thresholds and `--diff` the shortlists.

`doctor` is the one to reach for when a digest looks thin. It distinguishes the
four cases the pipeline otherwise renders identically — a dead board, a
rate-limited source, a missing credential, and a keyword that matched nothing:

```
$ jobhunter doctor
profile     profile.yaml            ok    valid
fx          fx_rates.yaml           warn  104 days old — refresh exchange rates
state       state/state.yaml        ok    loads
credential  adzuna                  FAIL  enabled but ADZUNA_APP_KEY not set
source      remoteok                ok    112 listings for 'staff engineer'
source      remotive                warn  reachable but 0 listings for 'staff engineer'
board       greenhouse/mozilla      ok    56 jobs (Mozilla)
board       lever/hashicorp         FAIL  dead — 404

1 failing, 2 warning — see FAIL rows above.
```

## Sources

Enabled per-source in `profile.yaml` under `sources:` — anything absent or
`enabled: false` is never fetched.

| Source | Notes |
|---|---|
| Adzuna | Aggregator. Needs `ADZUNA_APP_ID` + `ADZUNA_APP_KEY`. |
| Jooble | Aggregator. Needs `JOOBLE_API_KEY`. |
| Remotive, RemoteOK | Remote-only boards. No key. |
| ATS watchlist | Greenhouse, Lever, Ashby and Workday boards, by company slug. No key. Use `jobhunter probe` to find slugs. |
| RSS/Atom feeds | Any public job feed, by URL. |

Company ATS boards are usually the highest-signal source: they are the
employer's own listings, fresh and unduplicated. Aggregators give breadth.
Check `jobhunter sources` after a few runs and turn off whatever is not
contributing.

## Configuration

`specs/profile.example.yaml` is the annotated template; `specs/03-data-model.md`
is the schema of record. The profile is validated on load, so typos fail loudly
rather than silently changing what you see. The main blocks:

- `identity` — target skills, and one or more target tracks/levels. Seniority
  is dual-track: an IC ladder and a management ladder, scored independently, so
  a senior-engineer target does not accidentally match a director role.
- `queries` — the only source of search terms, plus per-run request caps.
- `hard_requirements` — the filters. Remote policy, excluded locations,
  seniority bounds, salary floor (with pinned FX rates for cross-currency
  comparison), excluded keywords scoped to title or full text, max listing age.
- `preferences` / `weights` — soft signals and their relative importance.
  Score is `100 × Σ(w·sub)/Σ(w)` over active weights, so zeroing one component
  rescales the rest — recheck `output.display_threshold` after changing weights.
- `sources`, `search_mode`, `output` — activation, search-posture preset, and
  digest formatting.

Multiple profiles are supported: pass `--profile profile-<name>.yaml` and the
state and digest filenames are namespaced automatically.

### Filtering vs. scoring

Hard requirements drop listings; everything else only moves them up or down the
ranking. Prefer scoring over filtering for anything the source data reports
unreliably — location especially. Aggregators tag roles by suburb or company
HQ, so hard-filtering on location drops genuinely remote roles. A high
`location_fit` weight floats the right ones to the top while keeping the rest
reviewable. (`02-functional-spec.md` §Stage 4 covers the filter semantics that
remain available.)

### Files and paths

| Path | Contents | Override |
|---|---|---|
| `profile.yaml` | Your criteria (git-ignored) | `--profile PATH` |
| `state/state.yaml` | Seen state + dismissals | `--state PATH` |
| `digests/YYYY-MM-DD.{md,json}` | The day's digest | `--output-dir DIR` |
| `state/source_stats.json` | Per-source counters over time | follows `--state` |
| `digests/YYYY-MM-DD.raw.json` | Pre-filter snapshot for `replay`, when `output.keep_raw` is on | follows `--output-dir` |

Keys go in a git-ignored `.env` or your OS keychain — never in `profile.yaml`.

## Scheduled runs

Run on whatever scheduler you have. A wrapper that sources secrets is the
simplest approach:

```bash
#!/usr/bin/env bash
# run-jobhunter.sh
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
python3 -m jobhunter run --output-dir digests "$@"
```

```cron
# weekday mornings at 07:30
30 7 * * 1-5 /path/to/JobHunter/run-jobhunter.sh >> /path/to/JobHunter/logs/jobhunter.log 2>&1
```

Cadence follows your `search_mode`: `active_unemployed` daily,
`active_employed` each weekday, `passive_employed` weekly. The active preset is
printed in each digest header.

### Catching source rot

Job boards go away quietly. `jobhunter doctor` is the manual check; the
`Source canary` workflow runs it weekly against `specs/profile.example.yaml` and
opens (or updates) a single issue when a source or board fails, closing it again
once the canary is green. That keeps the quick start honest — if the example
profile stops returning listings, the repo says so before a new user does.

## How the project is built

JobHunter is spec-driven: `specs/` is the source of truth, and the code is
reconciled against it by a build loop rather than edited ad hoc.

| Path | Purpose |
|---|---|
| `specs/01-product-spec.md` | Why the tool exists: goals, non-goals, success criteria. |
| `specs/02-functional-spec.md` | What it does: stage behaviours, unknown-data policy, scoring model, acceptance criteria. |
| `specs/03-data-model.md` | The shapes: listing, profile, scored result, run state, run report. |
| `specs/04-technical-plan.md` | How it is built: decisions, sources, architecture, fixtures, regeneration policy. |
| `specs/05-operator-tooling.md` | Maintenance surface: source stats, run snapshot/replay, ATS probe. |
| `specs/profile.example.yaml` | Annotated profile template. |
| `IMPLEMENTATION_PLAN.md` | Prioritised work items the loop implements one at a time. |
| `AGENTS.md` | Operational rules for the loop: repo map, validation commands, branch limits. |
| `tools/spec-loop/` | The build loop itself. |

The workflow: amend the spec, not the code, when requirements change; then
`./tools/spec-loop/loop.sh plan` re-derives the work items and
`./tools/spec-loop/loop.sh build 1` implements the top one on its own branch,
validated by `pytest`, stopping at a local commit. It never pushes. If code
lands outside the loop, `loop.sh update` back-fills the specs. See
`tools/spec-loop/README.md`.

Regeneration is per stage and gated by the fixture corpus — never an ungated
rewrite.

## Development

```bash
make install                  # editable install with dev extras + pre-commit hooks
make check                    # exactly what CI runs: ruff, SPDX headers, mypy, tests
make cov                      # tests with coverage, enforcing the floor
```

`make` with no target lists everything. The individual gates are `make lint`,
`make types`, `make test` and `make hooks` if you want them one at a time.

A change to a pipeline stage must add or extend that stage's test module. The
suite is hermetic — `tests/conftest.py` blocks real sockets, so an adapter test
that forgets to mock `httpx` fails loudly rather than depending on a live job
board. See CONTRIBUTING.md for the testing conventions.

## Status

Phases 0–5 are built: the pipeline runs end to end, with digests, dismissals,
multi-profile support, source contribution stats, offline replay and ATS
probing.

See [CHANGELOG.md](CHANGELOG.md) for what landed in each release.

## License

Apache-2.0 (see the SPDX headers on source files).
