<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Contributing to JobHunter

Thanks for your interest in contributing. JobHunter is a small,
deliberately scoped project, and contributions that fit that scope are
very welcome, particularly new source adaptors (see
`ADAPTORS.md`).

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

## Where things live

`src/jobhunter/` is one module per pipeline stage, in the order the data moves.
The fastest way to find code is to decide which stage owns the behaviour:

| Module | Owns |
|---|---|
| `cli.py` | Argument parsing, command handlers, wiring stages together |
| `profile.py` | Loading and validating `profile.yaml`, fx-rate merging |
| `ingest.py` | The `SourceAdapter` protocol every source implements |
| `adapters/` | One module per source: `adzuna`, `jooble`, `remoteok`, `remotive`, `ats` (Greenhouse/Lever/Ashby/Workday), `rss` |
| `normalize.py` | Salary parsing, location parsing, HTML stripping, seniority inference |
| `dedupe.py` | Merging the same role seen from several sources |
| `filter.py` | Stage 4 — the hard requirements, and the unknown-data policy |
| `score.py` | Stage 5 — the weighted-sum scorer and its six components |
| `rank.py` | Stage 6 — ordering and the display threshold |
| `digest.py` | Stage 7 — Markdown, HTML, JSON and CSV rendering |
| `state.py` | Seen-state, dismissals, short-id resolution |
| `model.py` | The dataclasses every stage passes around, plus id derivation |
| `pipeline.py` | Running the stages in order, request budget, per-source counters |
| `snapshot.py` | Saving and loading run snapshots for `replay` |
| `source_stats.py` | Per-source contribution history for `jobhunter sources` |
| `probe.py` | ATS board detection for `jobhunter probe` |
| `doctor.py` | The health checks behind `jobhunter doctor` |

Common questions: salary parsing is `normalize.py`; *whether* a salary passes is
`filter.py`; how much it contributes to the score is `score.py`. Each module has
a matching `tests/test_<module>.py`.

## What makes a good contribution

In rough order of usefulness:

1. **New source adaptors.** The highest-value contribution. See
   `ADAPTORS.md` for the contract and a checklist.
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
python3 -m pip install -e ".[dev]"
```

Install the pre-commit hooks once — they catch the whole class of mistakes
that matters here (stray whitespace, a missing SPDX header, and above all a
staged `profile.yaml` or `.env`):

```
pre-commit install
```

Validate your changes with:

```
python3 -m pytest -q
ruff check src tests tools
mypy src/jobhunter
```

All three must pass; CI runs them on Python 3.11, 3.12 and 3.13, plus a
coverage floor and a cold install of the README quick start.

The test suite is hermetic: `tests/conftest.py` blocks real sockets, so an
adapter test that forgets to mock `httpx` fails with a clear error instead of
hitting a live job board. A test that genuinely needs the network must be
marked `@pytest.mark.allow_network` — CI deselects those. A change to a pipeline stage must add or extend that
stage's test module.

## Testing conventions

The suite is fast (under two seconds) and hermetic. Keep it that way:

- **Never touch the network.** `tests/conftest.py` blocks real sockets;
  mock `httpx` as `tests/test_remoteok.py` does. A test that must
  reach a live source is marked `@pytest.mark.allow_network` and is
  deselected in CI.
- **Test a stage through its own module.** A change to filtering belongs
  in `tests/test_filter.py`, not in a CLI test that happens to exercise
  it.
- **CLI tests stub the pipeline.** `tests/test_cli_commands.py` replaces
  `cli.pipeline_run` / `cli.pipeline_run_with_snapshot` with canned
  results and asserts on exit codes, files written and output. The
  exception is `replay`, which runs Stages 4-7 for real against a
  snapshot — offline re-scoring is the feature, so stubbing it would
  test nothing.
- **Derive dates from `date.today()`**, never hardcode them. Profiles
  drop listings older than `max_age_days`, so a fixed `posted_at` makes
  a passing test fail weeks later.
- **Build profiles from `specs/profile.example.yaml`** and disable the
  sources you are not testing, so an exported `ADZUNA_APP_ID` in a
  developer's shell cannot change the result.
- **Test across module boundaries where a value changes shape.** Full
  coverage of both sides of a seam proves nothing about the seam: the
  digest rendered ids truncated to 8 characters while the filter matched
  the full 64-character hash, and `dismiss` silently did nothing for
  every id a human could see. Every module's tests passed. If a value
  crosses a boundary, test the round trip.

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

## Releasing

Releases are cut by tagging. `pyproject.toml` holds the version, and
`.github/workflows/release.yml` refuses to build if the tag and that version
disagree.

1. Make sure `main` is green and `make check` passes locally.
2. Move the `## [Unreleased]` items in `CHANGELOG.md` under a new version
   heading with today's date, and update the compare links at the bottom.
3. Bump `version` in `pyproject.toml` if it does not already match.
4. Commit, then tag and push:

   ```
   git tag -a v0.1.0 -m "JobHunter 0.1.0"
   git push origin main --follow-tags
   ```

5. The release workflow builds an sdist and wheel, runs `twine check`, verifies
   the tag against the pyproject version, and opens a **draft** GitHub release
   with generated notes. Review it, paste in the changelog entry, publish.

Nothing is published to PyPI. `pipx install git+https://github.com/justinmclean/jobhunter`
installs from a tag, which is the intended distribution for a personal tool.

## Licence

By contributing you agree that your contributions are licensed under
the Apache License 2.0, the same licence as the project.
