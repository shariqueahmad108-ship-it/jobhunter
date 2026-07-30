# Contributing to JobHunter

Thanks for your interest in contributing. JobHunter is a small,
deliberately scoped project, and contributions that fit that scope are
very welcome, particularly new source adaptors (see
`docs/ADAPTORS.md`).

## Before you start

JobHunter is spec-driven. The documents in `specs/` are the source of
truth, and the code is reconciled against them. This changes how
contributions work in one important way:

**If your change alters behaviour, amend the spec as well as the
code.** A pull request that changes what a pipeline stage does without
updating the relevant spec will be asked to add the spec change before
it is merged. Doc-only fixes, test improvements, and changes that
implement the spec as written don't need spec amendments.

The specs, in reading order:

| Spec | Covers |
|---|---|
| `specs/01-product-spec.md` | Goals, non-goals, success criteria |
| `specs/02-functional-spec.md` | Stage behaviours, unknown-data policy, scoring model |
| `specs/03-data-model.md` | Listing, profile, scored result, run state shapes |
| `specs/04-technical-plan.md` | Architecture, sources, fixtures, regeneration policy |
| `specs/05-operator-tooling.md` | Source stats, replay, ATS probe |

`AGENTS.md` documents the operational rules used by the build loop in
`tools/spec-loop/`. You don't need to use the loop to contribute, but
your changes should not conflict with it: keep changes small, one
concern per branch, and validated by tests.

## What makes a good contribution

In rough order of usefulness:

1. **New source adaptors.** The highest-value contribution. See
   `docs/ADAPTORS.md` for the contract and a checklist.
2. **Bug fixes with a failing test.** A test that demonstrates the bug
   makes review fast and prevents regression.
3. **Fixture corpus additions.** Real-world listing shapes that the
   pipeline currently mishandles, especially unusual salary, location,
   or seniority formats.
4. **Documentation fixes.**

Things that are out of scope by design (see `specs/01-product-spec.md`
for the reasoning):

- LLM calls anywhere in the pipeline. Scoring stays deterministic and
  rule based. Downstream use of the digest is the right place for AI.
- Automated job applications.
- Application or interview tracking. Other tools do this well.
- A web UI or hosted service.

If you're unsure whether an idea fits, open an issue before writing
code.

## Development setup

```
git clone <your fork> && cd JobHunter
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Validate your changes with:

```
python3 -m pytest -q
ruff check src tests
```

Both must pass. A change to a pipeline stage must add or extend that
stage's test module.

## Pull requests

- One concern per PR, on its own branch.
- Include the spec amendment if behaviour changes.
- New source files need SPDX Apache-2.0 headers, matching the existing
  files.
- Never commit anything personal: `profile.yaml`, `.env`, API keys, and
  `state/` are git-ignored and must stay that way. PRs that add
  credentials or personal profile data will be closed.
- Adaptor PRs must not add sources that require scraping in violation
  of a site's terms of service. API and public-feed sources only.

## Licence

By contributing you agree that your contributions are licensed under
the Apache License 2.0, the same licence as the project.
