<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

You are running the **build** beat of the spec-driven loop for JobHunter.
Implement exactly ONE work item, on its OWN branch.

Context to load first:

- `AGENTS.md` — operational rules (repo map, validation commands, branch +
  hard-limit rules).
- `IMPLEMENTATION_PLAN.md` — the prioritised work items.
- The appended **Repository snapshot** block from the runner — use it to route
  to the likely spec/source/test files before opening them.
- The appended **Open pull-request context** and **Local work-item branches**
  blocks from the runner. The loop never pushes, so a work item it already
  built shows up in the branch list, not the PR list.
- Only the spec(s) and source files relevant to the chosen work item — do not
  read the whole tree.

Steps:

1. Read the **Repository snapshot** as a routing aid (not proof; verify against
   the plan and real files before changing anything).
2. Read the **Open pull-request context** and **Local work-item branches**.
   Treat both as in-flight work. Pick the single highest-priority work item
   from `IMPLEMENTATION_PLAN.md` that is **not** already covered by an open PR
   and **not** already built as a local work-item branch. One only.
3. **Create its branch off the integration base**, then switch to it:
   `git checkout -b <slug>`, where `<slug>` is the work item's bare branch slug
   (no `spec/` or other prefix, e.g. `adzuna-adapter`). NEVER commit work to
   the integration base. One branch per work item.
4. Read only the relevant `specs/<area>.md` file(s) plus the `src/jobhunter/`
   and `tests/` files the item touches. Confirm what already exists before
   writing — do not assume.
5. Implement the work item **completely** — no placeholders, no stubs. Follow
   `specs/03-data-model.md` for the shapes and `specs/04-technical-plan.md` for
   the architecture. Every pipeline stage you add or change must ship or extend
   its `pytest` module under `tests/` (a stage without a test is incomplete).
   Keep secrets out of the repo (API keys via env/`.env`, never committed).
6. Run the work item's **Validation** command(s) from its spec — at minimum
   `python -m pytest tests/ -q`, plus `ruff check` if configured (the
   backpressure). Fix until they pass.
7. Update **only the served spec's** frontmatter `status:` (→ `done`) and its
   `Known gaps` if the item closed one. Do **not** edit sibling specs and do
   **not** edit `IMPLEMENTATION_PLAN.md` — a later `plan` pass reconciles it.
8. `git add -A` then `git commit` with an imperative subject and a
   `Generated-by: <agent> (<model>)` trailer, where `<agent>`/`<model>` are the
   actual agent and model you are running as (e.g. `Claude (Sonnet 4.5)`) — do
   not hardcode either. **Never** add a `Co-Authored-By:` trailer for an agent.

Then STOP. Do NOT push and do NOT open a PR — that is the human's step. Print
the exact commands the human can run:

```text
git push -u origin <slug>
gh pr create --web --base main --head <slug> \
  --title "<subject>" --body-file <prepared-body>
```

Rules:

- One work item per iteration. Do not bundle.
- Do not duplicate in-flight work. If the top plan item is already covered by
  an open PR or an existing local work-item branch, skip it and take the next
  uncovered item. Checking local branches (not just open PRs) is what keeps the
  loop from rebuilding the same item every iteration.
- If a work item is blocked, note why in its spec's `Known gaps` and pick the
  next item instead.
- Never commit secrets or a real `profile.yaml`. Respect source terms — no
  scraping in violation of a site's terms.
- Single source of truth — no duplicate logic; extend existing modules under
  `src/jobhunter/`.
