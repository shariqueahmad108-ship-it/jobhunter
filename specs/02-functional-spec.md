# 02 — Functional Spec

This describes *what the tool does*. Behaviors are grouped into a pipeline: **ingest → normalize
→ dedupe → filter → score → rank → present**. Each stage has acceptance criteria; a stage is
"done" only when its criteria pass.

## The pipeline at a glance

```
sources ─▶ ingest ─▶ normalize ─▶ dedupe ─▶ HARD FILTER ─▶ SCORE ─▶ rank ─▶ digest
   │                                            │             │
   └─ APIs / feeds                    drop obvious no's   soft-preference fit
```

---

## Stage 1 — Ingest

Pull raw listings from one or more configured sources for the queries defined in my profile
(keywords × locations).

- Each source is a pluggable adapter with a common output shape (see `03-data-model.md`).
- A source failure (timeout, auth error, rate limit) is logged and skipped; other sources still run.
- Respect each source's rate limits and pagination; stop at a configurable max results per query.

**Acceptance criteria**
- Given ≥1 configured source and a valid query, ingest returns raw listings or an empty set — never crashes the run.
- If a source errors, the run continues and the final output notes which source(s) were unavailable.

## Stage 2 — Normalize

Map each source's raw payload into the canonical `JobListing` schema (title, company, location,
remote flag, salary min/max + currency, description, url, posted date, source, source id).

- Missing fields are set to null, not guessed.
- Salary strings ("$120k–$150k", "120000") are parsed into numeric min/max + currency where possible; unparseable → null.
- Location is captured raw **and** parsed into {city, region, country, is_remote} where possible.

**Acceptance criteria**
- Every ingested listing becomes exactly one `JobListing` with a stable `id` (see dedupe).
- No normalized field contains raw source-specific junk (HTML tags stripped from description, etc.).

## Stage 3 — Dedupe

The same role is often cross-posted. Collapse duplicates into one listing that remembers all
the places it was found.

- Two listings are "the same" if (normalized company + normalized title + location) match, or if their URLs resolve to the same posting.
- The merged listing keeps every `(source, url)` pair so I can pick where to apply.

**Acceptance criteria**
- Given the same role from two sources, the output contains one listing with two source links.
- Deduping never merges two genuinely different roles at the same company (e.g. two different titles).

## Stage 4 — Hard filter (disqualifiers)

Remove listings that fail any **hard requirement** from the profile. These are pass/fail, not scored.
A listing that fails *any one* is dropped. Configurable hard filters:

- **Location / remote:** enforced by the profile's `remote_policy`. With `remote_only`
  (the default for this project), a listing must be fully remote — hybrid and on-site are dropped.
  Independently, a listing is dropped if its location matches any entry in `exclude_locations`
  (e.g. `Sydney`) **even when the role is labelled remote** — this covers "remote, but must be
  Sydney-based" postings. If `locations_allowed` is non-empty, the (remote) role must also fall
  within one of those places. All three are configuration, not hardcoded.
- **Seniority:** must not be below the minimum level (junior/mid/senior/lead/etc.) or above a ceiling if set.
- **Compensation floor:** if a salary is present and its max is below my floor, drop it. *If salary is absent, keep it* (don't punish missing data) — configurable.
- **Deal-breaker keywords:** drop if the listing matches any exclusion term (e.g. specific tech, industries, "unpaid", clearance requirements).
- **Freshness:** drop listings older than N days.
- **Already-seen / dismissed:** drop anything I've already been shown or explicitly dismissed in a prior run.

**Acceptance criteria**
- A listing failing any hard requirement never appears in the output, regardless of how well it would otherwise score.
- A listing with *missing* salary is retained when the "keep unknown salary" option is on.
- Changing a single hard filter in the profile changes the surviving set on the next run, with no code edit.
- The run reports how many listings were dropped and by which filter (a small tally), so filters can be debugged.

## Stage 5 — Score (soft preferences)

Score each surviving listing 0–100 for **fit** against my soft preferences. Scoring is a
transparent weighted sum of components; weights live in the profile. Suggested components:

- **Skill / keyword match** — overlap between my target skills and the listing's title + description.
- **Seniority fit** — how close the role's level is to my target level (exact = full marks).
- **Compensation** — how far salary max exceeds my floor toward my target (missing salary = neutral, not zero).
- **Location / remote fit** — preferred location or fully-remote scores higher than merely-allowed.
- **Company signals** — bonus/penalty from profile lists (preferred companies, industries; sizes to avoid).
- **Recency** — newer postings score slightly higher.

Each component yields a sub-score and a short human-readable reason. The final score is the
weighted sum, normalized to 0–100.

**Acceptance criteria**
- Every scored listing carries its total score **and** a per-component breakdown with reasons.
- Component weights are read from the profile; zeroing a weight removes that component's influence.
- Scoring is deterministic: the same listing + same profile → the same score every run.
- No listing that passed the hard filter is dropped at scoring; scoring only orders, never disqualifies.

## Stage 6 — Rank & threshold

Sort by score descending. Optionally hide anything below a "worth-my-time" score threshold from
the profile (still counted in the tally, just not shown).

**Acceptance criteria**
- Output is ordered best-first.
- Ties break by recency (newer first).
- Listings below the display threshold are excluded from the shortlist but reported in the summary count.

## Stage 7 — Present (digest)

Produce the run's output as a human-readable digest plus a machine-readable file.

- **Digest (Markdown/HTML):** ranked shortlist. Each entry shows title, company, location/remote,
  salary (or "not listed"), score, the one-line reason, source link(s), and posted date. A header
  summarizes: N new roles, M shown, sources used, sources that failed, filter tally.
- **Data file (JSON/CSV):** all scored survivors with full fields, for later tooling or a tracker.
- **Seen-state:** record which listing ids were shown this run so they're not re-surfaced next run
  (unless materially changed).

**Acceptance criteria**
- The digest is skimmable in under a minute and every row explains its ranking.
- Re-running immediately produces "0 new" (nothing already-seen resurfaces).
- The data file round-trips: it can be re-loaded without loss of any scored field.

---

## Core user stories

1. *As Justin, I run the tool and get a ranked shortlist of new-since-last-time roles, best first, each with a reason — without opening a job board.*
2. *As Justin, I raise my salary floor in the profile and the next run drops the now-too-low roles.*
3. *As Justin, I add a deal-breaker keyword and matching roles disappear from future runs.*
4. *As Justin, I see the same role posted on two boards as a single entry with both links.*
5. *As Justin, I dismiss a role and never see it again.*
6. *As Justin, one board is down and I still get results from the others, with a note that one source failed.*

## Modes of operation (v1)

- **On-demand:** run manually, get a digest for the current moment.
- **Scheduled:** run on a cadence (e.g. each weekday morning) and produce a fresh "new since yesterday" digest. (Delivery mechanism — file, email, notification — is an open question in the technical plan.)
