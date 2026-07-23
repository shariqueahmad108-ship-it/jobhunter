<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — JobHunter

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR. Regenerated 2026-07-23 after confirming all
previous work items merged to main (522 tests green) and `search-mode-presets`
applied directly.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`.

## What's been built (merged to main, verified on live data 2026-07-23)

Pipeline stages 1–7 end-to-end with two live profiles. Sources: Adzuna +
ATS company watchlist (Greenhouse/Lever/Ashby; GitLab, Grafana Labs, Elastic,
Canonical verified live), activated per profile via `queries.ats_watchlist`
and wired through `_build_adapters(profile)`. Filtering calibrated against
real runs: `require_keywords` domain anchors, `remote_countries_allowed`
(region-restricted remote ≠ remote for this user; APAC/bare-Remote kept +
flagged), hardened location parsing (semicolon country lists, region codes
AMERICAS/EMEA/LATAM, remote-hub cities, AU preferred in multi-country lists),
word-boundary location matching, skill-match saturation, adaptive remote
detection, search-mode presets, multi-profile state/digest isolation,
overflow-not-seen, golden fixture corpus (45 cases), Adzuna 429 backoff,
`--output-dir` + scheduled-run docs, CSV export, global `fx_rates.yaml`
(cross-rated into each profile's salary_currency; profile fx = overrides).

Lesson encoded from the ATS wiring gap: every adapter work item's validation
MUST include one end-to-end criterion ("a run with X configured shows X in
sources_used") — unit tests alone let an unreachable adapter pass.

## Work items (priority order)

Source expansion (former items 1–5) is BUILT on five branches (2026-07-23
evening loop run: profile-driven-sources, rss-atom-adapter,
remote-board-adapters, aggregator-adapter, workday-adapter) — reviewed, all
sound individually, currently being merged. Parallel building exposed a
config divergence that item 1 below resolves.

LESSON (encode in future planning): work items that share a config surface
must be built SEQUENTIALLY or given an explicit contract up front — the five
source branches, built in parallel, wired activation through three different
config paths (`sources:` block vs `queries.*` vs env-only), and the sources
branch shipped "not yet implemented" warnings for adapters its siblings were
implementing at that moment.

1. **`sources-reconciliation`** (IN PROGRESS — applied directly post-merge) —
   unify all source activation under the `sources:` profile block:
   - `_build_adapters(profile)` constructs feeds / remotive / remoteok /
     careerjet from `sources.*` (replacing the "not yet implemented"
     warnings); careerjet = `sources.careerjet.enabled` + env
     `CAREERJET_AFFILIATE_ID` for the credential.
   - Drop the sibling branches' `queries.feeds` / `queries.remotive` /
     `queries.remoteok` activation paths (never released; no legacy burden).
     `queries.ats_watchlist` keeps its deprecation fallback.
   - Update the stale "feeds not yet implemented" warning test; migrate both
     live profiles to `sources:`; example profile documents the full block.
   - END-TO-END criterion: with a fixture profile enabling each source, every
     enabled source appears in `sources_used`; disabled/unconfigured sources
     are never constructed.
   Validation: `python3 -m pytest -q` (full suite).

2. **`karynne-source-config`** — populate Karynne's `sources:` block: the
   I Work for NSW feed (verify the real feed URL from iworkfornsw.nsw.gov.au
   before adding — do NOT guess), `careerjet` enabled once an affiliate id is
   registered, plus any hospitality-sector employers found on standard ATS
   boards (HelloFresh AU etc. — slugs from careers-page URLs). Justin's
   profile: consider `remotive` (devrel category) + WeWorkRemotely and
   fossjobs.net feeds (verify URLs), Workday watchlist entries for Red Hat /
   Atlassian / HashiCorp (slugs from careers pages).

3. **`title-geo-restrictions`** (nice-to-have) — listings whose location is
   bare "Remote" but whose TITLE names a region ("… - EMEA", "Renewals
   Manager Germany") currently pass with the "remote scope unclear" flag.
   Deliberate (title geo-scanning is false-positive-prone), but revisit if
   flagged noise grows. Requires fixture cases both ways.

4. **`fx-staleness-warning`** (small) — scheduled runs warn when
   `fx_rates.yaml` is older than ~90 days (mtime check, one stderr line +
   digest header note).

Explicitly out (documented): LinkedIn (no public API; ToS), direct Seek
(partner-only; partial inventory via aggregators), private RTO careers pages
(no standard feeds). Paid Google-Jobs SERP API remains the documented
fallback if free coverage proves insufficient once the new sources are live.
