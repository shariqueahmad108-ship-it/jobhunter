<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — JobHunter

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR. Regenerated 2026-07-23 after merging the Phase 1–3
chain to `main` and reviewing all twelve completed work items.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`.

## What's been built (merged to main @ 6d69d8c + review commit)

Pipeline stages 1–7 end-to-end: profile schema + validation, JobListing model
with id/content_hash, Adzuna adapter, normalize (salary/location parsing,
seniority inference), dedupe (id + URL merge), hard filter (nine
disqualifiers, unknown-data policy), scorer (six components), rank/threshold,
seen-state + dismiss CLI, Markdown/HTML/JSON digests.

## Done (applied directly from review, 2026-07-23, commit on main)

Work items 1–9 and 12 were implemented outside the loop with the full test
suite green (516 tests): adapter credential params, empty-URL dedupe skip,
bare-$ → default currency + percent-token filtering + inverted-range swap,
negation-aware remote detection, symbol-safe term matching (`model.term_pattern`),
scorer neutrality (company non-match 0.5, remote-pref geo matching, no invented
compensation target), single-source level tables + period multipliers in
model.py, real HTTP request accounting + 401/403 adapter short-circuit,
never-guess company ("" + salted id) + injected run_date, profile validation
tightening (≥1 positive weight, bool/number, non-empty locations), and
most-complete-location dedupe merges.

## Manual prerequisites (Justin's terminal — the loop and the remote session can't do these)

The device bridge cannot delete files or branch refs, so these must run locally
before the next build round:

```bash
cd ~/JobHunter
git branch -D ats-feed-adapter joblisting-model
git branch -d project-scaffold profile-schema adzuna-adapter normalize-stage \
  dedupe-stage hard-filter-stage phase1-cli-digest scoring-stage \
  rank-threshold seen-state digest-html
rm -rf _to_delete
find .git -name 'tmp_obj_*' -delete
git gc --prune=now
python -m pytest -q     # expect 516 passed on main
```

The `ats-feed-adapter` branch deletion is REQUIRED before running the loop —
while it exists, the loop treats that work item as in-progress and skips it.

## Work items (priority order)

1. **`ats-feed-adapter`** — Greenhouse/Lever/Ashby company-watchlist adapter
    per specs/04 §Data sources. A 368-line uncommitted draft from a failed
    iteration is saved at `drafts/ats-adapter-draft.py` — review it, fix it
    up (it references a `queries.ats_watchlist` profile field that must be
    added to the profile schema + specs/03), write tests, and land it
    properly. Delete the draft file in the same branch.
    Draft review findings to address:
    - `queries.ats_watchlist` must be added to the profile schema (specs/03 +
      profile.py + profile.example.yaml): list of {ats, slug, name?}.
    - search() has NO per-company error handling despite its docstring — first
      company failure aborts the whole watchlist. Catch per company, aggregate
      failures, continue.
    - The pipeline calls search() per keyword×location but this adapter is
      query-independent — it refetches the whole watchlist every combo. Add a
      query-independent adapter concept (fetch once per run) to pipeline.py,
      and drop the cross-company `[:max_results]` truncation.
    - Dead code in _ashby_employment (unused `emp` variable).
    - Same never-guess issues as item 9 ("Unknown" company, date.today()).
    - Ashby `descriptionSocial` is a teaser, not the full description — note
      the limitation or fetch the detail endpoint.
    Validation: `python -m pytest tests/test_ats.py tests/test_profile.py tests/test_pipeline.py -q`.

2. **`golden-fixture-corpus`** — specs/04 §Fixtures: 30–50 real (anonymized)
    listings with expected outcomes at every stage, covering the awkward
    cases listed there (incl. the remote-but-Sydney-based case and the new
    P0/P1 regression cases). Wire as pytest fixtures; every stage's
    acceptance-criteria tests run against it.
    Validation: full `python -m pytest -q`.

3. **`scheduled-run-docs`** — document the cron/scheduled-task invocation for
    the weekday-morning digest (specs/02 §Modes), including state-file and
    digest-output locations. No code beyond small CLI polish (`--output-dir`
    default cleanup — see review note on `state_path.parent.parent`).
    Validation: `python -m pytest tests/test_cli.py -q`.

4. **`adzuna-rate-limit-backoff`** — the Adzuna adapter has no inter-page
   politeness delay and no 429/Retry-After backoff; fine while
   `max_requests_per_run` caps volume, needed before adding more sources or
   raising the cap. Add a configurable per-page delay and honour Retry-After
   on 429 with bounded retries. Do together with or after item 1.
   Validation: `python -m pytest tests/test_adzuna.py -q`.

5. **`overflow-seen-ux`** — decide and implement the intended UX for listings
   beyond `output.max_shown`: they are currently marked seen even though they
   only ever appear in the JSON data file (spec-consistent — "counted and
   available in the data file" — but means a role you never saw in the digest
   won't resurface). Options: (a) keep, but say "N more in data file" in the
   digest; (b) don't mark overflow as seen so it resurfaces next run; (c) spill
   overflow into the "Previously shown" section. Pick one, update spec 02
   §Stage 7 and the seen-state logic to match.
   Validation: `python -m pytest tests/test_state.py tests/test_digest.py -q`.
