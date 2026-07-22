# JobHunter — Spec Pack

A spec-driven project to build an AI-assisted tool that **finds and filters jobs** for me
(Justin), so I spend my time on the handful of roles worth applying to instead of scrolling
job boards.

This folder contains the **specification first**. Nothing is built yet — the specs are the
source of truth, and the implementation will be generated from them.

## How this pack is organized

| File | Purpose |
|------|---------|
| `README.md` | This overview + the workflow for turning specs into code. |
| `specs/01-product-spec.md` | Why the tool exists: vision, users, goals, non-goals, success criteria. |
| `specs/02-functional-spec.md` | What it does: find/filter behaviors, scoring model, user stories, acceptance criteria. |
| `specs/03-data-model.md` | The shapes: job listing, my profile/criteria, and scored-result schemas. |
| `specs/04-technical-plan.md` | How it gets built: data sources, architecture, tech stack, phased roadmap, open questions. |
| `specs/profile.example.yaml` | A fill-in-the-blanks template of my job criteria that drives filtering. |
| `IMPLEMENTATION_PLAN.md` | Prioritised work items (the gaps) the build loop implements one at a time. |
| `AGENTS.md` | Operational rules for the loop: repo map, validation commands, branch + hard limits. |
| `tools/spec-loop/` | The spec-driven build loop (Ralph-style) that reconciles the code against the specs. |

**Location policy:** the search targets **remote roles only, excluding
Sydney-based postings** — configured (not hardcoded) in `profile.yaml` via
`remote_policy: remote_only` + `exclude_locations: [Sydney]`.

## The spec-driven workflow

1. **Write the spec** (this pack). Keep it the single source of truth.
2. **Review & lock scope.** Resolve the open questions in `04-technical-plan.md` before writing code.
3. **Fill in `profile.yaml`** (copy from `specs/profile.example.yaml`) — this is the config that makes "filter" mean something concrete.
4. **Run the loop to build**, phase by phase. `git init` first, then:
   - `./tools/spec-loop/loop.sh plan` turns the specs into a prioritised `IMPLEMENTATION_PLAN.md`.
   - `./tools/spec-loop/loop.sh build 1` implements the top work item on its own branch, validates it with `pytest`, and stops at a local commit (it never pushes — that's your step).
   - Repeat, reviewing each branch. See `tools/spec-loop/README.md`.
5. **Check work against acceptance criteria** in `02-functional-spec.md` — a feature is "done" only when its criteria pass. The loop uses these as its `pytest` backpressure.
6. **Amend the spec, not the code, when requirements change.** Then re-run `plan` and `build`. If code lands outside the loop, `./tools/spec-loop/loop.sh update` back-fills the specs.

## Status

- [x] Spec pack drafted
- [x] Build loop implemented (`tools/spec-loop/`)
- [ ] `git init` + first commit (the loop needs a git base)
- [ ] Open questions resolved (see `04-technical-plan.md` §Open questions)
- [ ] `profile.yaml` filled in
- [ ] Phase 0–1 (scaffold + ingest + filter) built
- [ ] Phase 2 (scoring + ranking) built
- [ ] Phase 3 (freshness + digest) built
