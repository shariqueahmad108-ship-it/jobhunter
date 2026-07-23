<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# AGENTS — JobHunter operational context

This file is the operational context the spec-loop's prompts load in addition
to the beat prompt itself: the repository map, the validation commands, and the
branch rules. JobHunter is a single-developer Python project — there are no
skills, no plugin, and no separate governance layer.

## Repository map (what the loop edits)

- `specs/` — the functional description of the product (the desired state the
  build loop reconciles code against). One file per area. Owned by the human
  and by the `update` beat; the `build` beat only flips a spec's `status`.
- `src/jobhunter/` — the Python package: source adapters, the pipeline stages
  (normalize, dedupe, filter, score, rank), the digest renderer, and the CLI.
- `tests/` — pytest suites, one module per pipeline stage plus fixtures.
- `IMPLEMENTATION_PLAN.md` — prioritised work items (the gaps). One work item =
  one branch = one PR. Owned by the `plan`/`consolidate` beats.
- `AGENTS.md` — this file.
- `profile.yaml` — the user's real job criteria (git-ignored; copied from
  `specs/profile.example.yaml`). Never commit a real `profile.yaml`.

There is no `src/` tree yet — Phase 1 creates it. Until then, `build` scaffolds
it per `specs/04-technical-plan.md`.

## Validation commands (the build "backpressure" step)

Run the chosen work item's own **Validation** block first. General checks:

```bash
# Unit tests for the stage(s) the work item touches (or all of them)
python -m pytest tests/ -q

# Lint & format check (if configured)
ruff check src tests
ruff format --check src tests

# Shell tooling (when a work item touches the loop itself)
bash -n tools/spec-loop/loop.sh tools/spec-loop/lib.sh
bash tools/spec-loop/tests/test_runner_fixtures.sh
```

A work item that adds or changes a pipeline stage must add or extend that
stage's pytest module. A stage without a test is incomplete. Both the new
tests and the existing suite must pass before committing.

## Branch rules (one branch per fix/feature)

- **Never commit feature work to the integration base** (`$SPEC_LOOP_BASE`,
  default `main`). `build` branches a bare `<slug>` off it first.
- **One work item per branch, one branch per PR.** Do not bundle work items.
- A `build` branch edits only **its own** served spec's `status:` (→ `done`) —
  not sibling specs and not `IMPLEMENTATION_PLAN.md` (the plan is reconciled by
  a later `plan` pass; this avoids cross-branch conflicts).
- The `update` beat branches `sync-specs-<timestamp>` and edits `specs/`
  **only** — it documents reality, it never changes source or tests.
- The runner feeds each iteration **both** the open PRs and the local
  work-item branches as in-flight work, because a built-but-unpushed item
  exists only as a local branch — that list is what stops the loop rebuilding
  the same item every iteration.

## Hard limits (do not cross)

- **No push, no PR.** The loop stops at a local commit and prints the human-run
  `git push` + `gh pr create --web` commands. Opening the PR is the human's step.
- **No secrets in the repo.** API keys (e.g. the Adzuna app id/key) come from
  environment variables or a git-ignored `.env`, never committed.
- **No real `profile.yaml`** committed — only `specs/profile.example.yaml`.
- **Respect source terms.** Only add source adapters for APIs/feeds that permit
  programmatic access (see `specs/04-technical-plan.md`); never scrape a site
  in violation of its terms.

## Commits

- Imperative subject describing the user-visible change.
- Trailer `Generated-by: <agent> (<model>)`, where `<agent>` and `<model>` are
  the actual agent and model running (e.g. `Claude (Sonnet 4.5)`). Do not
  hardcode either, and never add a `Co-Authored-By:` trailer for an agent.
- One commit per build iteration (the change + its spec `status` flip).


## Spec files are read-only for build iterations

Build iterations MUST NOT modify any file under `specs/`. No `status:`
frontmatter, no "known gaps" comments, no progress notes — the specs are the
source of truth, not a scratchpad, and header stamps conflict across branches.
Progress and gap notes belong in `IMPLEMENTATION_PLAN.md`. Only `plan`/`update`
beats (or the human) may touch `specs/`.
