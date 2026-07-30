# Writing a source adaptor

Adaptors are how JobHunter ingests listings. Each source (an
aggregator API, an ATS board, an RSS feed) is one adaptor that fetches
raw postings and turns them into normalized listings for the rest of
the pipeline. Everything downstream — dedupe, filtering, scoring,
ranking — is source-agnostic, so a new adaptor is all it takes to add
a new source.

The contract of record is `specs/03-data-model.md` (the listing shape)
and `specs/04-technical-plan.md` (source architecture). This document
is the practical checklist; if it ever disagrees with the specs, the
specs win.

## What an adaptor does

1. **Fetch** raw postings from the source, respecting the per-run
   request caps set in the profile's `queries` block.
2. **Map** each posting to the listing shape defined in
   `specs/03-data-model.md`.
3. **Mark unknowns.** This is the rule that matters most: if the
   source doesn't report a field (salary, location, seniority), the
   adaptor records it as unknown. It never guesses, never fills in a
   default that looks like data, and never drops the listing. The
   filter and scoring stages handle unknowns explicitly; the adaptor's
   job is only to be honest about what the source said.
4. **Report** what it did, so `jobhunter sources` can attribute
   contribution stats to it.

## What an adaptor must not do

- No scoring, filtering, or dedupe logic. That lives in later stages.
- No LLM calls. The pipeline is deterministic end to end.
- No credentials in code. Keys come from environment variables,
  documented in the adaptor's docstring and the README sources table.
- No scraping that violates a site's terms of service. API and public
  feed sources only.

## Checklist for a new adaptor PR

- [ ] Adaptor module in `src/jobhunter/` alongside the existing
      sources; follow the structure of the nearest existing adaptor
      (an aggregator if yours is an aggregator, a board if a board).
- [ ] Activation via `profile.yaml` under `sources:`, off by default,
      consistent with how existing sources are enabled.
- [ ] Any required env vars named `<SOURCE>_...`, documented in the
      README sources table with a link to where to get a key.
- [ ] Fixtures: at least one captured real response (sanitised of
      anything personal) in the test fixture corpus, including at
      least one posting with missing fields, to exercise the
      unknown-data path.
- [ ] A test module for the adaptor covering the happy path, the
      missing-fields path, and an empty or error response from the
      source.
- [ ] `python3 -m pytest -q` and `ruff check src tests` pass.
- [ ] A row added to the Sources table in the README.
- [ ] A spec amendment to `specs/04-technical-plan.md` adding the
      source to the source list.
- [ ] SPDX Apache-2.0 headers on new files.

## Testing against the live source

`jobhunter probe URL_OR_SLUG` detects which ATS a careers page uses;
if your target company is on Greenhouse, Lever, Ashby or Workday, you
may not need a new adaptor at all — add the slug to your watchlist
instead. Write a new adaptor when the source is a genuinely new API or
feed format.

For tuning and offline testing, `jobhunter replay` re-scores a saved
run without network calls, which is also useful for verifying that
your adaptor's output survives the downstream stages unchanged.
