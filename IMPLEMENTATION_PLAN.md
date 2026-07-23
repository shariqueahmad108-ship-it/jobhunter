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

1. **`profile-driven-sources`** — finish the `sources:` profile block
   (partially done: ATS watchlist activation is live via queries.ats_watchlist).
   Move to the unified shape and migrate:

   ```yaml
   sources:
     adzuna:        { enabled: true, country: "au" }
     ats_watchlist: [ { ats: greenhouse, slug: gitlab, name: GitLab } ]
     feeds:         [ { name: "iworkfornsw", url: "https://…" } ]
     remotive:      { enabled: false, categories: ["devrel"] }
     remoteok:      { enabled: false }
     careerjet:     { enabled: false }
   ```

   Keep `queries.ats_watchlist` working with a deprecation note, or migrate
   both live profiles in the same change. END-TO-END criterion: a run with a
   source enabled lists it in `sources_used`; disabled sources are never
   constructed.
   Validation: `python3 -m pytest tests/test_profile.py tests/test_cli.py -q`.

2. **`rss-atom-adapter`** — one generic feed adapter over any RSS/Atom URL in
   `sources.feeds` (per-feed name for the source tally). Query-independent
   (reuse `query_independent = True`); parse title/link/pubDate/description,
   strip HTML via shared normalize. Unlocks WeWorkRemotely category feeds +
   fossjobs.net (Justin) and I Work for NSW (Karynne).
   END-TO-END criterion: a profile with one feed configured shows it in
   sources_used and ingests fixture-feed entries.
   Validation: `python3 -m pytest tests/test_rss.py -q` (fixture feeds).

3. **`remote-board-adapters`** — Remotive + RemoteOK public JSON APIs
   (attribution per their terms). Both mark remoteness explicitly and often
   carry salary; per-source region tag so "remote (US only)" is flaggable.
   END-TO-END criterion as above.
   Validation: `python3 -m pytest tests/test_remotive.py tests/test_remoteok.py -q`.

4. **`aggregator-adapter`** — Careerjet and/or Jooble free search APIs
   (keyword×location model — clone the Adzuna adapter shape). Broad AU
   mainstream recall for volume fields (Karynne's hospitality). Dedupe
   handles the expected Adzuna overlap.
   Validation: `python3 -m pytest tests/test_careerjet.py -q`.

5. **`workday-adapter`** — extend the ATS family with Workday's public
   job-board JSON endpoints (adds Red Hat, Atlassian, HashiCorp to
   watchlists). Same watchlist shape (`ats: workday, slug: …`). After item 1.
   Validation: `python3 -m pytest tests/test_ats.py -q`.

6. **`title-geo-restrictions`** (nice-to-have) — listings whose location is
   bare "Remote" but whose TITLE names a region ("… - EMEA", "Renewals
   Manager Germany") currently pass with the "remote scope unclear" flag.
   Deliberate (title geo-scanning is false-positive-prone), but revisit if
   flagged noise grows: a conservative title scan for region tokens could
   downgrade these to ineligible. Requires fixture cases both ways.

Explicitly out (documented): LinkedIn (no public API; ToS), direct Seek
(partner-only; partial inventory via aggregators), private RTO careers pages
(no standard feeds). Paid Google-Jobs SERP API remains the documented
fallback if free coverage proves insufficient after items 2–4.
