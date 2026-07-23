<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — JobHunter

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR. Regenerated 2026-07-23 after confirming all
previous work items merged to main (522 tests green) and `search-mode-presets`
applied directly.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`.

## What's been built (merged to main)

Pipeline stages 1–7 end-to-end: profile schema + validation (with
`search_mode` posture presets), JobListing model with id/content_hash, Adzuna
adapter, normalize (salary/location parsing, seniority inference), dedupe (id +
URL merge), hard filter (nine disqualifiers, unknown-data policy), scorer (six
components), rank/threshold, seen-state + dismiss CLI, Markdown/HTML/JSON
digests, search-mode presets (`active_unemployed` / `active_employed` /
`passive_employed`).

## Manual prerequisites (Justin's terminal)

Branch cleanup and git-litter quarantine were completed remotely on 2026-07-23:
all 13 work-item branches are deleted (only `main` exists) and temp objects are
moved into `_to_delete/`. What remains needs local delete permissions:

```bash
cd ~/JobHunter
rm -rf _to_delete           # quarantined locks/refs/temp objects (~1.7 MB)
git gc --prune=now
python3 -m pytest -q        # expect 522 passed
```

## Work items (priority order)

1. **`ats-feed-adapter`** — Greenhouse/Lever/Ashby company-watchlist adapter
    per specs/04 §Data sources. A draft is saved at `drafts/ats-adapter-draft.py`
    — review it, fix the issues below, write tests, and land it. Delete the
    draft file in the same branch.
    Seed the example watchlist with open-source-heavy employers matching the
    user's targeting (OSPO / community / governance roles) — e.g. GitHub,
    GitLab, Canonical, Red Hat, HashiCorp, Grafana Labs, Elastic — since
    Adzuna's coverage of these role families is thin. This makes the ATS adapter
    the highest-value remaining item for the actual search.
    Issues to address:
    - `queries.ats_watchlist` must be added to the profile schema (profile.py
      `_validate_queries`, `_TOP_LEVEL_KEYS`, defaults) and to
      `specs/profile.example.yaml`. Shape: list of `{ats, slug, name?}`.
    - `search()` has no per-company error handling despite its docstring —
      first company failure aborts the whole watchlist. Catch per company,
      aggregate failures, continue.
    - The pipeline calls `search()` per keyword×location but this adapter is
      query-independent — it refetches the whole watchlist every combo. Add a
      query-independent adapter concept (fetch once per run) to pipeline.py,
      and drop the cross-company `[:max_results]` truncation.
    - Dead code in `_ashby_employment` (unused `emp` variable).
    - Same never-guess issues as prior items ("Unknown" company, `date.today()`).
    - Ashby `descriptionSocial` is a teaser, not the full description — note
      the limitation or fetch the detail endpoint.
    Validation: `python3 -m pytest tests/test_ats.py tests/test_profile.py tests/test_pipeline.py -q`.

2. **`overflow-not-seen`** — DONE (2026-07-23, applied directly): cli.py
    records seen-state only for the rendered slice (`new_results[:max_shown]`);
    overflow stays eligible and resurfaces next run. 541 tests green.

3. **`golden-fixture-corpus`** — specs/04 §Fixtures: 30–50 real (anonymized)
    listings with expected outcomes at every stage, covering the awkward
    cases listed there (incl. the remote-but-Sydney-based case and P0/P1
    regression cases). Wire as pytest fixtures under `tests/fixtures/`; every
    stage's acceptance-criteria tests run against it.
    Draw the corpus from the PROFILE'S ACTUAL SEARCH DOMAIN — open source /
    community / governance roles (OSPO, DevRel, head of community), not
    generic software-engineering listings — including the low-paid
    community-coordinator lookalikes the salary floor must catch and
    management-track titles the seniority inference must classify.
    Validation: full `python3 -m pytest -q`.

4. **`adzuna-rate-limit-backoff`** — the Adzuna adapter has no inter-page
    politeness delay and no 429/Retry-After backoff; fine while
    `max_requests_per_run` caps volume, needed before adding more sources or
    raising the cap. Add a configurable per-page delay and honour Retry-After
    on 429 with bounded retries. Do together with or after item 1.
    Validation: `python3 -m pytest tests/test_adzuna.py -q`.

5. **`scheduled-run-docs`** — document the cron/scheduled-task invocation for
    the weekday-morning digest (specs/02 §Modes), including state-file and
    digest-output locations. Small CLI polish: add `--output-dir` to the `run`
    subparser (cli.py `build_parser`) so the existing `hasattr(args, "output_dir")`
    guard in `_cmd_run` actually fires, and clean up the
    `state_path.parent.parent` default digest path.
    Validation: `python3 -m pytest tests/test_cli.py -q`.
