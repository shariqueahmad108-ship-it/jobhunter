<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — JobHunter

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`.

## Status — 2026-07-25

Single branch `main`. Phases 0–5 are built: stages 1–7 run end to end on two
live profiles, plus source contribution stats, run snapshot + offline replay,
and the ATS probe. `git log` is the record of what was built and why — this
file only carries what is still open.

Re-run `python3 -m pytest -q` before the next build iteration (the bare
`python` on this machine is 2.7).

## Work items (priority order)

### 1. Golden fixture corpus — spec 04 §Fixtures

The Phase 1 deliverable is still thin. Every stage's regeneration gate depends
on it, so this is the highest-value remaining work.

- Real listings with expected outcomes at every stage, covering: unknown
  salary, unknown location, unknown seniority, cross-currency salaries, both
  seniority tracks, and a duplicate spanning two sources.
- **Validation:** `python3 -m pytest -q`.
- **End-to-end criterion:** each pipeline stage has at least one fixture that
  fails if that stage's rules change.

No other work items are queued. Run `./tools/spec-loop/loop.sh plan` to derive
the next batch from the specs, or add items here by hand.

## Guardrails (do not re-plan these)

- **Careerjet: removed on purpose** (f9b6abc — the v4 API's
  publisher/end-user-IP model doesn't fit a personal CLI). Never re-add;
  Jooble is the aggregator in its place.
- **Workday boards**: Red Hat / Atlassian / HashiCorp were dropped because they
  don't serve the JSON endpoint the adapter expects. No new Workday entry
  without a board URL verified to return JSON.
- **Explicitly out**: LinkedIn (no public API; ToS), direct Seek (partner-only;
  partial inventory reachable via aggregators), private careers pages with no
  standard feed. A paid Google-Jobs SERP API stays the documented fallback if
  free coverage proves insufficient.
- **Adapter work items** need one end-to-end criterion ("source X configured
  shows X in `sources_used`"), and items sharing a config surface must be
  sequenced or given an explicit contract up front.
- **Check an aggregator API's personal-use fit BEFORE writing the adapter.**

## Manual follow-ups (USER-side; not loop work items — do not build these)

- Unverified: `https://hnrss.org/whoishiring/jobs` (documented by hnrss,
  supports `?q=`, but a robots timeout blocked confirmation). Confirm it
  returns RSS locally before adding it as a feed.
- Dead, do not retry on guessed slugs: Linux Foundation and Confluent (404 on
  Greenhouse), HashiCorp (404 on Lever), fossjobs.net (`/rss/` is an HTML index
  over a ~7-job board), I Work for NSW (Taleo-backed, no feed).
- Add ATS watchlist entries only with slugs verified from a real careers-page
  URL — use `jobhunter probe` rather than guessing.
- Recurring local cleanup: `rm -rf _to_delete && git gc --prune=now`.
