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
| `specs/01-product-spec.md` | Why the tool exists: vision, users, goals, non-goals, qualitative success criteria. |
| `specs/02-functional-spec.md` | What it does: pipeline behaviors, unknown-data policy, scoring model, user stories, testable acceptance criteria. |
| `specs/03-data-model.md` | The shapes: job listing, profile/criteria (with required fields + defaults), scored result, run state, run report. |
| `specs/04-technical-plan.md` | How it gets built: resolved decisions, data sources, architecture, fixture corpus, regeneration policy, phased roadmap, open questions. |
| `specs/profile.example.yaml` | A fill-in-the-blanks template of my job criteria that drives filtering. |
| `IMPLEMENTATION_PLAN.md` | Prioritised work items (the gaps) the build loop implements one at a time. |
| `AGENTS.md` | Operational rules for the loop: repo map, validation commands, branch + hard limits. |
| `tools/spec-loop/` | The spec-driven build loop (Ralph-style) that reconciles the code against the specs. |

**Location policy:** remote work is **preferred via scoring, not hard-filtered**
— configured (not hardcoded) in `profile.yaml` via `remote_policy: any` with a
high `location_fit` weight. Rationale: source data (Adzuna) tags roles by
suburb and company HQ, so location-based hard filtering dropped genuinely
remote roles; scoring floats remote to the top while keeping everything
reviewable. (See `02-functional-spec.md` §Stage 4 for the filter semantics
that remain available.)

## The spec-driven workflow

1. **Write the spec** (this pack). Keep it the single source of truth.
2. **Review & lock scope.** Resolve the open questions in `04-technical-plan.md` before building the affected phase (each is tagged with the phase it blocks).
3. **Fill in `profile.yaml`** (copy from `specs/profile.example.yaml`) — this is the config that makes "filter" mean something concrete.
4. **Build the golden fixture corpus** (Phase 1 deliverable, `04-technical-plan.md` §Fixtures) — real listings with expected outcomes at every stage. This is the loop's backpressure.
5. **Run the loop to build**, phase by phase. `git init` first, then:
   - `./tools/spec-loop/loop.sh plan` turns the specs into a prioritised `IMPLEMENTATION_PLAN.md`.
   - `./tools/spec-loop/loop.sh build 1` implements the top work item on its own branch, validates it with `pytest`, and stops at a local commit (it never pushes — that's your step).
   - Repeat, reviewing each branch. See `tools/spec-loop/README.md`.
6. **Check work against acceptance criteria** in `02-functional-spec.md` — a feature is "done" only when its criteria pass against the fixture corpus (the loop runs these as `pytest`).
7. **Amend the spec, not the code, when requirements change.** Then re-run `plan` and `build` — regeneration is **per stage, gated by the fixture tests** (see `04-technical-plan.md` §Regeneration policy), never an ungated full rewrite. If code lands outside the loop, `./tools/spec-loop/loop.sh update` back-fills the specs.

## Status

- [x] Spec pack drafted
- [x] Build loop implemented (`tools/spec-loop/`)
- [x] Spec review applied (2026-07-22): unknown-data policy, dual-track seniority + inference, currency/period comparison, scoped deal-breakers, `exclude_locations` + remote-only location policy, content-hash change detection, seen-vs-dismissed rework, CLI dismissals, score normalization rule, employment filter, fixture corpus + per-stage regeneration policy; industry/size scoring cut from v1; scoring locked rule-based/deterministic
- [ ] `git init` + first commit (the loop needs a git base)
- [ ] Remaining open questions resolved (see `04-technical-plan.md` §Open questions — tagged by phase)
- [ ] `profile.yaml` filled in
- [ ] Golden fixture corpus captured
- [ ] Phase 0–1 (scaffold + ingest + filter) built
- [ ] Phase 2 (scoring + ranking) built
- [ ] Phase 3 (freshness + digest + dismiss CLI) built
