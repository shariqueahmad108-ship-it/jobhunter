<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — JobHunter

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`.

## Status — 2026-07-25

Single branch `main`. Phases 0–5 are built: stages 1–7 run end to end, plus
source contribution stats (05 §5.1), offline replay (05 §5.2), and the ATS
probe (05 §5.3). 1252 tests pass (`python3 -m pytest -q`).

Re-run `python3 -m pytest -q` before the next build iteration (the bare
`python` on this machine is 2.7).

## Work items (priority order)

None queued. Phases 0–5 are built, the golden fixture corpus expansion is
merged, and the CLI command-handler coverage gap is closed (cli.py 49% -> 99%,
tests/test_cli_commands.py). Run `./tools/spec-loop/loop.sh plan` to derive the
next batch from the specs, or add items here by hand.

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
- CI landed 2026-07-30 (`.github/workflows/ci.yml`); mypy blocking, coverage
  floor 95 against 96% measured, every module >=90%. No ratchets outstanding.
- On this machine bare `pip` is Python 2.7. Always `python3 -m pip`.
- Recurring local cleanup: `rm -rf _to_delete && git gc --prune=now`.
