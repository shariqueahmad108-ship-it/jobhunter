# 01 — Product & Vision Spec

## Problem

Job hunting has a signal-to-noise problem. Openings are scattered across many boards, most
listings are irrelevant to any given person, and the good ones get buried or expire before
they're seen. The manual loop — open five sites, re-type the same filters, skim dozens of
postings, mentally score each against a fuzzy set of criteria — is slow, repetitive, and
easy to abandon. As a result, good-fit roles get missed and effort is wasted on poor-fit ones.

## Vision

A tool that runs the boring part of the search automatically: it pulls fresh listings from
multiple sources, filters out everything that clearly doesn't fit my hard requirements, and
ranks the survivors by how well they match what I actually want — so each time I check in, I
see a short, ordered shortlist of roles genuinely worth my attention, with the reasoning shown.

The goal is **less time searching, better roles surfaced.** The tool decides what's worth
looking at; I decide what to apply to.

## Primary user

Me (Justin) — one person running an active or passive job search. Single-user by design in v1;
the tool is a personal assistant, not a multi-tenant product. It should assume a technical
owner comfortable editing a config file and running a command or a scheduled task.

## Goals

1. **Aggregate** current job listings from more than one source into one normalized list.
2. **Filter** out listings that fail my hard requirements (location, seniority, comp floor, employment type, deal-breakers), so I never see obvious no's.
3. **Rank** the remaining listings by fit against my soft preferences, best first.
4. **Explain** each result — why it scored the way it did — so I can trust and tune the ranking.
5. **Deduplicate** the same role cross-posted to several boards.
6. **Stay fresh** — highlight new/changed listings since last run; keep dismissed roles gone for good, while prior shortlists stay reviewable.
7. **Be tunable** from a single profile/criteria file, without code changes for everyday adjustments.

## Non-goals (v1)

- **Not** auto-applying to jobs or submitting anything on my behalf.
- **Not** writing resumes or cover letters (may be a later, separate module — out of scope here).
- **Not** a full application/pipeline tracker (out of scope; may integrate later).
- **Not** scraping sites in violation of their terms of service (see technical plan).
- **Not** multi-user, no accounts, no hosted web service required for v1.
- **Not** a general market-analytics/salary-research product.
- **Not** enriching listings with company data (industry, size) that no permitted source provides — the scoring signals that would need it are deferred with it (see technical plan §Decisions).

## Success criteria

These are the **qualitative goals** the product is judged by. (The *testable* acceptance
criteria that implement them live per-stage in `02-functional-spec.md` — that split is
deliberate: this list says what "good" feels like; 02 says what a test can check.)

The tool is successful if, on a typical run:

- I can go from "run it" to "a ranked shortlist" without touching a job board myself.
- The **top of the list is genuinely worth reading** — precision at the top matters more than total recall.
- Every clearly-disqualified listing (wrong location, wrong seniority, below comp floor) is **absent**, not just down-ranked — while listings that are merely *missing data* are kept and marked, never silently dropped.
- Each shown role includes a **one-line reason** for its score, and I can see the full component breakdown when I want it.
- Adjusting a single criterion in the profile file **visibly changes** what surfaces, with no code edit.
- A run over a normal batch of listings completes in a small number of minutes, unattended.
- The digest stays short enough to review in one sitting (bounded shortlist, not a firehose).

## Guiding principles

- **Recall on filtering, precision on ranking.** Be generous about what passes the hard filter (don't silently drop maybes — unknown data keeps a listing in, marked as unknown), but strict about what floats to the top.
- **Explainable over clever.** A transparent, tunable, deterministic scoring rule beats an opaque model I can't trust or adjust.
- **Config over code.** Everyday changes (new keyword, higher salary floor, new city) live in the profile file.
- **Respect sources.** Prefer official APIs and permitted feeds; never build on access that breaks a site's terms.
- **Fail loud, fail safe.** If a source is down, say so and continue with the others rather than producing a silently incomplete list. If the profile has a typo, refuse to run rather than guessing.
