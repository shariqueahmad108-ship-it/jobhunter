<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — JobHunter

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR. Regenerated 2026-07-24: ALL previously planned
work items are built — there are currently NO open work items for the loop.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`.

## What's been built (merged to main, verified on live data 2026-07-23)

Pipeline stages 1–7 end-to-end with two live profiles. Sources: Adzuna +
ATS company watchlist (Greenhouse/Lever/Ashby/Workday), RSS/Atom feeds,
Remotive, RemoteOK, Careerjet — ALL activated per profile via the unified
`sources:` block and constructed by `cli._build_adapters(profile)`
(`sources-reconciliation`, commit 5428c09; `queries.ats_watchlist` retains a
deprecated fallback). Filtering calibrated against real runs:
`require_keywords` domain anchors, `remote_countries_allowed`, hardened
location parsing (hub cities extract as cities, e.g. "London, UK"),
word-boundary location matching, skill-match saturation, adaptive remote
detection, search-mode presets, multi-profile state/digest isolation,
overflow-not-seen, golden fixture corpus, Adzuna 429 backoff, CSV export,
global `fx_rates.yaml` with staleness warning (>90 days → stderr + digest
note), title geo-hint flags for bare-Remote listings, and the hybrid_ok
remote-country drop (a "remote (US only)" role is dropped for an AU-bound
profile even under hybrid_ok).

2026-07-23 evening loop run (reviewed 2026-07-24, merged to main):

- `karynne-source-config` (d03e960): workday_path/workday_instance profile
  validation to match the ATS adapter; sibling tests migrated from
  `queries.*` to `sources:`; end-to-end adapter-construction tests for
  feeds/remotive/remoteok/careerjet/workday; hub-city parsing fix;
  live-profile smoke tests (skip when the git-ignored profiles are absent).
- `title-geo-restrictions` (2702706): flag-only title geo hints
  ("remote scope: title hints EMEA") + the hybrid_ok remote-country drop.
- `fx-staleness-warning` (b27a8b7): fx_rates.yaml mtime check.

Lessons encoded: adapter work items need one end-to-end criterion ("X
configured shows X in sources_used"); work items sharing a config surface
must be sequential or given an explicit contract up front.

## Work items (priority order)

NONE. Do not re-plan items for anything listed above — in particular do NOT
recreate `sources-reconciliation`, `karynne-source-config`,
`title-geo-restrictions`, or `fx-staleness-warning`; they are done. New items
come only from new spec amendments or new calibration findings.

## Manual follow-ups (USER-side; not loop work items — do not build these)

These need human accounts, registrations, or judgment; the loop must skip
them:

- Karynne / Careerjet: register a free affiliate id (careerjet.com.au partner
  signup), set `CAREERJET_AFFILIATE_ID` in the env, uncomment `careerjet:` in
  profile-karynne.yaml.
- I Work for NSW: VERIFIED 2026-07-23 — publishes NO RSS/Atom feed (homepage
  and /jobs checked; Taleo-backed). The feed option is dead; do not add a
  guessed URL.
- Karynne / hospitality ATS watchlist: add entries only with slugs verified
  from real careers-page URLs (HelloFresh AU etc.).
- Justin / optional sources: enable `remotive` (devrel category); add
  WeWorkRemotely + fossjobs.net feeds and Workday entries (Red Hat /
  Atlassian / HashiCorp) only after verifying real URLs/slugs from the
  careers pages — never guess.

Explicitly out (documented): LinkedIn (no public API; ToS), direct Seek
(partner-only; partial inventory via aggregators), private RTO careers pages
(no standard feeds). Paid Google-Jobs SERP API remains the documented
fallback if free coverage proves insufficient once the new sources are live.
