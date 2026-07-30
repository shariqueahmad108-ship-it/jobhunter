<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# 05 — Operator Tooling & Diagnostics

This describes the **maintenance surface**: commands that answer "is this
working, and is it worth keeping?" about the pipeline itself. Nothing here is a
pipeline stage — these tools read what a run produced and help calibrate the
next one.

> **Testability rule** (inherited from `02-functional-spec.md`): every
> acceptance criterion below must be checkable by a test against the golden
> fixture corpus or by direct inspection of a command's output. No criterion
> here may require a live network call.

Motivating principle (`01-product-spec.md` §Guiding principles): precision at
the top beats total recall. Adding sources is cheap; knowing which sources earn
their place is what keeps the digest short.

---

## 5.1 — Source contribution stats *(built)*

**Problem.** The run report names `sources_used` and `sources_failed`, but not
what each source *bought*. A source that returns 400 listings of which none
survive the hard filter is indistinguishable, in the digest header, from one
that contributes three of the top five. Deciding whether to keep an aggregator
is currently guesswork.

**Behavior.** Each run records per-source counters, and the run's counters are
appended to a persisted history so trends survive across runs (see
`03-data-model.md` §Source stats). A new command renders them:

```
jobhunter sources [--profile P] [--last N] [--json]
```

Counters per source, per run:

| counter | meaning |
|---|---|
| `fetched` | raw listings returned by the adapter |
| `contributed` | listings surviving dedupe that this source supplied (a listing found by 3 sources credits all 3) |
| `sole_source` | surviving listings **only** this source supplied |
| `passed_filter` | of `contributed`, how many survived Stage 4 |
| `shown` | of `passed_filter`, how many appeared in the digest (either section) |
| `dismissed` | of `shown`, how many the user has since dismissed |
| `requests` | HTTP requests spent |
| `failed` | true if the adapter raised; the error string is kept |

`sole_source` is the load-bearing number: it is what would be lost by removing
the source. `dismissed` is the noise signal — a source with high `shown` and
high `dismissed` is actively wasting attention.

The digest header gains one line per source in the form
`adzuna: 312 fetched → 41 passed → 12 shown (4 sole)`, so a digest remains
interpretable on its own without running the command.

**Acceptance criteria**

- A fixture run over a corpus with three sources produces a stats record for
  each, and the counters satisfy `shown ≤ passed_filter ≤ contributed ≤ fetched`.
- A listing present in two sources' raw output increments `contributed` for
  both and `sole_source` for neither.
- Removing a source from the profile leaves its historical records intact; the
  command still reports them, marked inactive.
- An adapter that raises records `failed: true` with its error and
  `fetched: 0`, and does not abort the run or the stats write.
- `--json` emits the same data the table renders, and round-trips.
- The stats file is append-only per run and survives a schema version bump via
  the same loader rule as run state (migrate or fail loud).

---

## 5.2 — Offline re-scoring (`replay`) *(built)*

**Problem.** Every calibration change — a threshold, a weight, a new
`deprioritize_keywords` term — currently costs a full live run against
rate-limited APIs, and the result is confounded by the listing set having
changed in the meantime. Tuning is slow and not A/B-comparable.

**Behavior.** A run optionally persists its **normalized, deduped, pre-filter**
listings (`output.keep_raw: true`, default true, see `03-data-model.md`).
`replay` re-executes Stages 4–7 over that snapshot with **no network access**:

```
jobhunter replay <run-file> [--profile P] [--set key=value ...] [--diff <run-file>] [--out FILE]
```

- `--profile` supplies an alternative profile; omitted, the run's own profile
  snapshot is used.
- `--set` applies one-off overrides (`--set output.display_threshold=65`)
  without editing the profile, so a sweep is a shell loop.
- `--diff` compares the replay against another run or replay and reports
  **entered** (now shown, wasn't), **left** (was shown, now isn't), and
  **moved** (rank/score delta), with each listing's score before and after.

`replay` is strictly read-only with respect to run state: it never writes
seen-state, never marks anything shown, and never consumes dismissals as new
information. Dismissed ids are still *applied* (a dismissed listing stays
hidden), because that reflects a real user decision.

**Acceptance criteria**

- Replaying a run against its own unmodified profile reproduces that run's
  digest exactly — same listings, same order, same scores.
- Replay performs zero HTTP requests (asserted by a network-forbidding fixture
  harness); a run file missing its raw snapshot fails loudly with a message
  naming `output.keep_raw`, rather than silently refetching.
- `--set output.display_threshold=<higher>` yields a subset of the original
  shown set; a lower value yields a superset.
- Adding a `deprioritize_keywords` term via `--set` moves the matching
  listings' scores by exactly the penalty factor in `02-functional-spec.md`
  §Stage 5 and leaves every non-matching listing's score unchanged.
- After any replay, run state (`seen`, `dismissed_ids`, `last_run_at`) is
  byte-identical to before.
- `--diff` on two identical runs reports zero entered, zero left, zero moved.

---

## 5.3 — ATS board detection (`probe`) *(built)*

**Problem.** Every watchlist entry needs an `ats` + `slug` pair that only a
human can currently establish, by opening a careers page and reading the URL.
Guessed slugs have produced silent zero-result boards (the Red Hat / Atlassian /
HashiCorp Workday entries) and wasted planning cycles on sources that do not
exist (I Work for NSW). The project rule is "never guess a URL" — this command
makes verification cheap enough that the rule is easy to follow.

**Behavior.**

```
jobhunter probe <careers-page-url | company-slug> [--ats greenhouse|lever|ashby|workday]
jobhunter probe --check [--profile P]
```

Single-target mode resolves a careers page (or a bare slug) to a working board
by trying each supported ATS's public endpoint and reporting only **confirmed**
hits — an endpoint that parses as listings with at least one job. Output is a
paste-ready profile line plus the evidence:

```
greenhouse / mozilla — 56 jobs (oldest 2026-04-02, newest 2026-07-24)
  - { ats: "greenhouse", slug: "mozilla", name: "Mozilla" }
```

A slug that returns 404, HTML, or zero jobs is reported as **not confirmed**,
never as a suggestion. The command must not print a candidate line for an
unverified board — that is the whole point of it.

`--check` mode probes every `sources.ats_watchlist` entry in a profile and
reports each as live (with its job count) or dead (with the failure), so board
rot is caught by running one command instead of by noticing an empty section in
a digest weeks later.

**Acceptance criteria**

- Against fixture endpoints, a valid Greenhouse/Lever/Ashby board is detected
  with the correct `ats` and `slug`, and its printed YAML line is accepted by
  the profile validator without edits.
- A 404, an HTML error page, and a valid-but-empty board are each reported as
  not confirmed, and no candidate line is printed for any of them.
- Given a full careers-page URL containing the slug (e.g.
  `https://boards.greenhouse.io/mozilla` or a company careers page redirecting
  to one), the slug is extracted rather than requiring the user to identify it.
- `--check` over a profile with one live and one dead board exits non-zero,
  names the dead board, and leaves the profile file unmodified.
- The command never writes to the profile — its output is text for the human to
  paste (config stays human-owned).
- Every probe is rate-limited and identifies itself via the project's standard
  User-Agent, per `04-technical-plan.md` §Data sources.

## 5.4 — Configuration and source health (`doctor`) *(built)*

**Problem.** The pipeline is quiet by design: a board that 404s, a source that is
rate-limited, a credential that was never exported, and a keyword that simply
matched nothing all produce the same observable result — a digest without those
listings. Board rot has therefore been found by hand, weeks late, by noticing a
thin digest (Linux Foundation, Confluent, HashiCorp, fossjobs). `probe --check`
covers ATS boards only, and says nothing about credentials, aggregator health,
or whether the profile still validates.

**Behavior.**

```
jobhunter doctor [--profile P] [--state S] [--json] [--offline]
```

Runs an ordered set of checks and prints one row each, then a summary:

| Check | Passes when | Notes |
|---|---|---|
| `profile` | the profile loads and validates | a failure stops the run — nothing downstream is meaningful |
| `fx` | `fx_rates.yaml` exists and is under 90 days old | absent or stale is a **warning** |
| `state` | the state file loads, or is absent | absent is fine: a first run creates it |
| `credential` | each enabled keyed source has its env vars | **explicitly** enabled without a credential is a failure; active-by-default-but-absent is a warning |
| `source` | one live query per enabled source returns without error | zero listings is a **warning**, an exception is a failure |
| `board` | every `ats_watchlist` entry is confirmed | a dead slug is a **failure** — this is the check board rot needs |

`--offline` skips the `source` and `board` checks, validating configuration only.
`--json` emits `{"ok": bool, "checks": [...]}` for a scheduler to act on.

Exit code is **1 if any check failed**, 0 otherwise; warnings never fail the
command. A source enabled without its credential is a failure here even though
`run` treats it as a warning — `run` is doing a day's work and should continue,
whereas `doctor` is answering "is anything broken".

**Acceptance criteria**

- An invalid profile fails, prints the validator's own error, and suppresses
  every later check rather than reporting misleading downstream results.
- A source that raises is reported as failed with the exception type; a source
  that returns zero listings is reported as reachable-but-empty and does not
  fail the command. The two are never conflated.
- A dead watchlist slug fails the command and names `ats/slug`, so a scheduled
  run can act on it.
- Stale `fx_rates.yaml` warns and exits 0.
- A keyed source that is **absent** from the `sources:` block warns rather than
  fails when its credential is unset, and a source explicitly `enabled: true`
  without its credential fails. The shipped `specs/profile.example.yaml` — which
  comments its keyed sources out — must exit 0 under `--offline`, since the
  scheduled canary depends on a clean baseline to be worth reading.
- One bad source does not prevent the remaining sources from being checked.
- `--offline` makes no network requests and still validates profile, state and
  credentials.
- `--json` output parses and its `ok` field agrees with the exit code.
- The command never writes to any file (config and state stay untouched).

