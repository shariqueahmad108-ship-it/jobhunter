<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — JobHunter

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`.

## Status — 2026-07-25

Single branch `main` at `e11881c`. Nothing in flight: no work branches, no
stashes, no unfinished work anywhere. Pipeline stages 1–7 run end to end on
two live profiles; `git log` is the record of what was built and why.

Suite last confirmed green 2026-07-24 (`python3 -m pytest -q` — the bare
`python` on this machine is 2.7). Re-run it before the next build iteration.

## Work items (priority order)

NONE. New items come only from new spec amendments or new calibration
findings from live runs.

Do not re-plan anything already in `git log`. In particular:

- Careerjet was **removed on purpose** (f9b6abc — the v4 API's
  publisher/end-user-IP model doesn't fit a personal CLI). Do not re-add it;
  Jooble is the aggregator in its place.
- The Red Hat / Atlassian / HashiCorp Workday boards were dropped because
  they don't serve the JSON endpoint the adapter expects. Do not re-add a
  Workday entry without a board URL verified to return JSON.

Lessons encoded: adapter work items need one end-to-end criterion ("X
configured shows X in sources_used"); work items sharing a config surface
must be sequential or given an explicit contract up front; check an
aggregator API's personal-use fit BEFORE writing the adapter.

## Manual follow-ups (USER-side; not loop work items — do not build these)

- Jooble: register a free key at jooble.org/api/about and export
  `JOOBLE_API_KEY` — `sources.jooble` is already on in profile-karynne.yaml
  and does nothing without it.
- I Work for NSW: VERIFIED 2026-07-23 — no RSS/Atom feed (Taleo-backed). Dead
  option; never add a guessed URL.
- Karynne / hospitality ATS watchlist: add entries only with slugs verified
  from real careers-page URLs.
- Justin / optional sources: `remotive` (devrel), fossjobs.net feed,
  replacement Workday entries — only from verified URLs/slugs.
- Recurring local cleanup: `rm -rf _to_delete && git gc --prune=now`.

Explicitly out (documented): LinkedIn (no public API; ToS), direct Seek
(partner-only; partial inventory via aggregators), private RTO careers pages
(no standard feeds), Careerjet (API model incompatible). Paid Google-Jobs SERP
API remains the documented fallback if free coverage proves insufficient.
