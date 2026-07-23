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

## Work items (priority order — fixes from code review first)

### P0 — Crash / correctness fixes

1. **`fix-adapter-credentials`** — `cli.py::_build_adapters` calls
   `AdzunaAdapter(app_id=…, app_key=…)` but the constructor signature is
   `__init__(self, country="au")` reading env vars → TypeError on first real
   run. Give AdzunaAdapter explicit `(app_id, app_key, country="au")` params
   (env fallback for compatibility), update `_build_adapters`, and add a
   construction test that exercises the real constructor (no mocks).
   Validation: `python -m pytest tests/test_adzuna.py tests/test_cli.py -q`.

2. **`fix-dedupe-empty-url`** — `dedupe.py::run` pass 2 treats URL equality as
   identity; Adzuna falls back to `url=""`, so ALL empty-URL listings merge
   into one. Skip falsy URLs in the URL union-find. Test: two distinct
   listings with `url=""` stay separate.
   Validation: `python -m pytest tests/test_dedupe.py -q`.

3. **`fix-salary-currency-default`** — `normalize.py::parse_salary`: bare `$`
   maps to USD even when `default_currency` is given; an AU "$120k–$150k"
   parses as USD and fx-inflates ~1.5×. Bare `$` must resolve to
   `default_currency` (None if unset). Also: filter percent/superannuation
   tokens from amount extraction ("$120,000 + 10% super" currently yields
   max=10) and swap min/max when inverted. In `adapters/adzuna.py`, an
   unrecognised country code silently defaults currency to AUD — use None
   (unknown-salary policy) instead.
   Validation: `python -m pytest tests/test_normalize.py tests/test_adzuna.py -q`.

### P1 — Filter/score correctness

4. **`fix-remote-detection`** — `adzuna.py::_detect_remote` marks any listing
   whose title/location/description-start contains "remote" as remote —
   "no remote work available" and "hybrid, 2 days remote" both pass, and with
   `remote_policy: remote_only` that admits non-remote roles. Make detection
   negation-aware: match title/location on `\bremote\b`; in description accept
   only positive phrases ("fully remote", "100% remote", "remote-first",
   "work from home/anywhere") and reject when preceded by negation ("no",
   "not", "isn't") or qualified as hybrid. Add fixture cases both ways.
   Validation: `python -m pytest tests/test_adzuna.py -q`.

5. **`fix-word-boundary-terms`** — `\b + re.escape(term)` never matches
   symbol-edged terms ("C++", ".NET") in filter exclude_keywords and score
   skill_match. Add a shared `term_pattern(term)` helper (lookarounds when a
   term edge is a non-word char) in `model.py`; use it in both call sites.
   Validation: `python -m pytest tests/test_filter.py tests/test_score.py -q`.

6. **`fix-score-neutrality`** — three scorer adjustments per review:
   (a) `_score_company_signal`: a company not in preferred_companies scores
   0.0 (full-weight penalty) while unconfigured scores 0.5 — make non-match
   0.5 (bonus-only semantics);
   (b) `_score_location_fit`: preference "Remote Australia" awards 1.0 to any
   remote listing anywhere — require the geographic tokens to match parsed
   fields too, else fall back to the 0.75 generic-remote score;
   (c) `_score_compensation`: drop the invented `target = floor × 1.5`
   default — with no salary_target configured return neutral 0.5 with reason.
   Update spec 02 §Stage 5 is NOT needed (behavior already within spec);
   update tests accordingly.
   Validation: `python -m pytest tests/test_score.py -q`.

### P2 — Consistency / hygiene

7. **`consolidate-level-tables`** — IC/MGMT level lists are defined in five
   modules and period-annualization multipliers in two. Single source in
   `model.py`; import everywhere.
   Validation: full `python -m pytest -q`.

8. **`fix-request-accounting`** — `pipeline.py::run`: `requests_made` counts
   keyword×location queries, not HTTP requests (adapter pagination is
   uncounted); failed queries don't count; a dead adapter is retried for
   every combo producing one SourceFailure each. Have adapters report actual
   request counts, count failures, and skip an adapter after an auth-class
   failure (401/403).
   Validation: `python -m pytest tests/test_pipeline.py -q`.

9. **`fix-never-guess`** — `adzuna.py::normalize`: missing company falls back
   to "Unknown" (collides ids across unknown-company roles) — keep the raw
   absence, flag "company unknown", and exclude such listings from id-based
   merging. `first_seen_at = date.today()` inside the adapter: accept an
   injected run date instead (runner passes it), so re-normalization can't
   reset it and tests are deterministic.
   Validation: `python -m pytest tests/test_adzuna.py tests/test_dedupe.py -q`.

### P3 — Features

10. **`ats-feed-adapter`** — Greenhouse/Lever/Ashby company-watchlist adapter
    per specs/04 §Data sources. A 368-line uncommitted draft from a failed
    iteration is saved at `drafts/ats-adapter-draft.py` — review it, fix it
    up (it references a `queries.ats_watchlist` profile field that must be
    added to the profile schema + specs/03), write tests, and land it
    properly. Delete the draft file in the same branch.
    Validation: `python -m pytest tests/test_ats.py tests/test_profile.py -q`.

11. **`golden-fixture-corpus`** — specs/04 §Fixtures: 30–50 real (anonymized)
    listings with expected outcomes at every stage, covering the awkward
    cases listed there (incl. the remote-but-Sydney-based case and the new
    P0/P1 regression cases). Wire as pytest fixtures; every stage's
    acceptance-criteria tests run against it.
    Validation: full `python -m pytest -q`.

12. **`scheduled-run-docs`** — document the cron/scheduled-task invocation for
    the weekday-morning digest (specs/02 §Modes), including state-file and
    digest-output locations. No code beyond small CLI polish (`--output-dir`
    default cleanup — see review note on `state_path.parent.parent`).
    Validation: `python -m pytest tests/test_cli.py -q`.
