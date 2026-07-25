<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — JobHunter

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`.

## Status — 2026-07-25

Single branch `main`. Pipeline stages 1–7 run end to end on two live profiles;
`git log` is the record of what was built and why.

New this pass: `specs/05-operator-tooling.md` (Phase 5) adds three
maintenance-surface features, with supporting structures in
`specs/03-data-model.md` (§Source stats, §Run snapshot, `output.keep_raw`) and
a Phase 5 entry in `specs/04-technical-plan.md`. The three work items below
implement it.

Suite last confirmed green 2026-07-24 (`python3 -m pytest -q` — the bare
`python` on this machine is 2.7). Re-run before the next build iteration.

## Work items (priority order)

### 1. `source-contribution-stats` — spec 05 §5.1

Record per-source counters each run, persist them to
`state/<profile>/source_stats.json` per 03 §Source stats, add
`jobhunter sources [--last N] [--json]`, and add the per-source line to the
digest header.

- Counters: fetched, contributed, sole_source, passed_filter, shown,
  dismissed, requests, failed/error.
- Credit rule: a post-dedupe survivor credits `contributed` to every source
  that supplied it, `sole_source` only when exactly one did.
- A failing adapter records `failed: true` + error and must not abort the run
  or the stats write.
- **Validation:** `python3 -m pytest tests/test_source_stats.py tests/test_digest.py -q`;
  the invariant `shown ≤ passed_filter ≤ contributed ≤ fetched` asserted over
  the golden corpus.
- **End-to-end criterion:** a fixture run with three sources, one of them
  failing, produces three stats records and a digest header line per source.

### 2. `run-snapshot-and-replay` — spec 05 §5.2

Persist the pre-filter snapshot (`output.keep_raw`, default true, 03 §Run
snapshot), then add `jobhunter replay <run-file> [--profile P] [--set k=v]
[--diff other] [--out FILE]` re-running Stages 4–7 offline.

- Do the snapshot first, in this same item — replay is untestable without it.
- Strictly read-only over run state; dismissals still apply. Zero HTTP
  requests, enforced by a network-forbidding test harness.
- A run file with no snapshot fails loudly naming `output.keep_raw` — never
  silently refetches.
- **Validation:** `python3 -m pytest tests/test_replay.py -q` plus the existing
  filter/score/digest modules (Stages 4–7 must stay shared code, not a
  reimplementation — that identity is what makes replay trustworthy).
- **End-to-end criterion:** replaying a run against its own profile reproduces
  that run's digest exactly, and run state is byte-identical afterwards.

### 3. `ats-board-probe` — spec 05 §5.3

`jobhunter probe <careers-url|slug> [--ats ...]` and `jobhunter probe --check
[--profile P]`.

- Report a candidate ONLY on a confirmed hit (parses as listings, ≥1 job).
  404 / HTML / empty board → "not confirmed", no candidate line. This is the
  entire point of the command; do not soften it into a suggestion.
- Extract the slug from a full careers-page URL.
- `--check` exits non-zero naming dead boards; never writes the profile.
- **Validation:** `python3 -m pytest tests/test_probe.py -q` against fixture
  endpoints (no live network in tests).
- **End-to-end criterion:** the YAML line `probe` prints for a fixture
  Greenhouse board is accepted by the profile validator unedited.

Sequencing: items 1 and 2 both touch the run-output path — build 1, merge, then
2. Item 3 is independent of both and may be built in parallel.

Do not re-plan anything already in `git log`. In particular:

- Careerjet was **removed on purpose** (f9b6abc — the v4 API's
  publisher/end-user-IP model doesn't fit a personal CLI). Do not re-add it;
  Jooble is the aggregator in its place.
- The Red Hat / Atlassian / HashiCorp Workday boards were dropped because they
  don't serve the JSON endpoint the adapter expects. Do not re-add a Workday
  entry without a board URL verified to return JSON.

Lessons encoded: adapter work items need one end-to-end criterion ("X
configured shows X in sources_used"); work items sharing a config surface must
be sequential or given an explicit contract up front; check an aggregator API's
personal-use fit BEFORE writing the adapter.

## Manual follow-ups (USER-side; not loop work items — do not build these)

- Boards VERIFIED 2026-07-25 and added to profile-justin.yaml:
  `greenhouse/mozilla` (56 jobs), `greenhouse/wikimedia` (26, all remote),
  `greenhouse/sourcegraph91` (7 — note the digits).
- Hacker News "Who is Hiring": `https://hnrss.org/whoishiring/jobs` is
  documented by hnrss and supports `?q=` filtering, but could NOT be fetched
  during verification (robots timeout). Confirm it returns RSS locally before
  adding it as a feed.
- NOT available, do not retry on guessed slugs: Linux Foundation, Confluent
  (both 404 on Greenhouse), HashiCorp (404 on Lever) — all have moved ATS.
  fossjobs.net `/rss/` is an HTML index, not a feed, over a ~7-job board.
- I Work for NSW: VERIFIED 2026-07-23 — no RSS/Atom feed (Taleo-backed). Dead
  option; never add a guessed URL.
- Karynne / hospitality ATS watchlist: add entries only with slugs verified
  from real careers-page URLs.
- Justin / optional: fossjobs.net if a real feed URL ever surfaces;
  replacement Workday entries only from a board URL confirmed to return JSON.
- Recurring local cleanup: `rm -rf _to_delete && git gc --prune=now`.

Explicitly out (documented): LinkedIn (no public API; ToS), direct Seek
(partner-only; partial inventory via aggregators), private RTO careers pages
(no standard feeds), Careerjet (API model incompatible). Paid Google-Jobs SERP
API remains the documented fallback if free coverage proves insufficient.
