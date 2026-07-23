# 02 — Functional Spec

This describes *what the tool does*. Behaviors are grouped into a pipeline: **ingest → normalize
→ dedupe → filter → score → rank → present**. Each stage has acceptance criteria; a stage is
"done" only when its criteria pass.

> **Testability rule for this document:** every acceptance criterion here must be checkable by a
> test against the golden fixture corpus (see `04-technical-plan.md` §Fixtures) or by direct
> inspection of a run's output. Qualitative goals ("worth reading", "skimmable") live in
> `01-product-spec.md` §Success criteria, not here.

## The pipeline at a glance

```
sources ─▶ ingest ─▶ normalize ─▶ dedupe ─▶ HARD FILTER ─▶ SCORE ─▶ rank ─▶ digest
   │                                            │             │
   └─ APIs / feeds                    drop obvious no's   soft-preference fit
```

## Unknown-data policy (applies to every hard filter)

Following the guiding principle *"recall on filtering, precision on ranking"*: **when the field a
hard filter needs is null/unparseable, the listing is KEPT** (never silently dropped for missing
data), and the digest marks the field as "unknown". The single exception is configurable:
`keep_unknown_salary: false` allows dropping salary-less listings. Per filter:

| Filter | Field needed | If unknown |
|---|---|---|
| Location / remote | parsed location, `is_remote` | keep, mark "location unclear" |
| Seniority | inferred `seniority` | keep, mark "level unclear" |
| Compensation floor | `salary` (comparable) | keep iff `keep_unknown_salary` (default true) |
| Employment type | `employment` | keep |
| Freshness | `posted_at` | keep, treat `first_seen_at` as the posting date |
| Deal-breaker keywords | title/description text | n/a — text always present |

Scoring mirrors this: an unknown field scores **neutral** (mid-scale) for its component, never zero.

---

## Stage 1 — Ingest

Pull raw listings from one or more configured sources for the queries defined in my profile
(keywords × locations).

- Each source is a pluggable adapter with a common output shape (see `03-data-model.md`).
- A source failure (timeout, auth error, rate limit) is logged and skipped; other sources still run.
- Respect each source's rate limits and pagination; stop at a configurable max results per query.
- **Run-cost bound:** total requests per run ≤ `sources × keywords × locations × pages`, and the
  runner enforces `max_requests_per_run` (profile) as a hard cap — the run stops ingesting and
  notes truncation in the run report rather than exceeding it.

**Acceptance criteria**
- Given ≥1 configured source and a valid query, ingest returns raw listings or an empty set — never crashes the run.
- If a source errors, the run continues and the final output notes which source(s) were unavailable.
- A run never issues more requests than `max_requests_per_run`; if the cap truncates ingestion, the run report says so.

## Stage 2 — Normalize

Map each source's raw payload into the canonical `JobListing` schema (title, company, location,
remote flag, salary min/max + currency + period, description, url, posted date, source, source id).

- Missing fields are set to null, not guessed.
- Salary strings ("$120k–$150k", "120000", "$900/day") are parsed into numeric min/max + ISO 4217
  currency + period (year/month/day/hour) where possible; unparseable → null (raw string retained).
- Location is captured raw **and** parsed into {city, region, country, is_remote} where possible.

### Seniority inference

Most listings never state a level; `seniority` is **inferred**, in this order, first match wins:

1. **Explicit source field** (some APIs provide one) — mapped to the canonical enum.
2. **Title patterns** — a maintained, ordered rule table (e.g. `principal|staff|senior|sr\.?`
   → that IC level, with `lead` mapping to `staff`; `manager|head of|director` → management track;
   `graduate|junior|intern` → those levels). Word-boundary, case-insensitive matching.
3. **Description patterns** — phrases like "8+ years", "you will manage a team" map via the same
   rule table, at lower confidence.
4. **Otherwise `null`** — the level is unknown; the unknown-data policy applies (keep + mark).

Seniority is modeled as **two tracks** — IC (`intern → junior → mid → senior → staff → principal`)
and management (`manager → senior_manager → director → vp`) — each ordered internally; the tracks
are **not** comparable to each other (see `03-data-model.md`).

**Acceptance criteria**
- Every ingested listing becomes exactly one `JobListing` with a stable `id` and a `content_hash` (see dedupe).
- No normalized field contains raw source-specific junk (HTML tags stripped from description, etc.).
- Each fixture listing's inferred seniority matches its expected value in the golden corpus (including expected `null`s).
- A "$X/day" or "$X/hour" salary parses with the correct `period`; it is never stored as an annual figure.

## Stage 3 — Dedupe

The same role is often cross-posted. Collapse duplicates into one listing that remembers all
the places it was found.

- Two listings are "the same" if their normalized identity keys match — lowercased,
  whitespace-collapsed, punctuation-stripped `(company + title + city|country)` — or if their URLs
  resolve to the same posting. Title normalization also strips common decorations
  (`Sr./Snr → senior`, trailing team qualifiers in parentheses) via the same rule table used for
  seniority inference.
- For fully-remote listings with no city, the identity key uses `(company + title + country|"remote")`.
- The merged listing keeps every `(source, url)` pair so I can pick where to apply, the earliest
  `first_seen_at`, and the most complete non-null fields.
- Each listing also carries a **`content_hash`** — a hash of the substantive fields
  (title, salary min/max/currency/period, location.raw, description) — used later to detect
  material changes to an already-seen listing.

**Acceptance criteria**
- Given the same role from two sources, the output contains one listing with two source links.
- Every expected-merge pair in the golden corpus merges; every expected-distinct pair (e.g. two
  different titles at the same company) stays separate. The corpus includes at least 10 of each.

## Stage 4 — Hard filter (disqualifiers)

Remove listings that fail any **hard requirement** from the profile. These are pass/fail, not scored.
A listing that fails *any one* is dropped. The unknown-data policy above governs every filter.
Configurable hard filters:

- **Location / remote:** three profile knobs combine — all configuration, not hardcoded.
  (This project's policy: **remote-only, excluding Sydney-based roles** — see README.)
  1. **`exclude_locations`** is checked first: a listing whose parsed location matches any entry
     is dropped **even when the role is labelled remote** — this covers "remote, but must be
     Sydney-based" postings.
  1b. **`remote_countries_allowed`** (optional, null = any): a remote listing
     restricted to a parsed country outside this list loses its remoteness for
     pass/fail purposes — "Remote (US)" is not remote for an Australia-bound
     user. A remote listing with NO parsed country is kept (unknown-data
     policy: it may be work-from-anywhere) with the "remote scope unclear" flag.
  2. **`remote_policy`** then applies:
     - `remote_only` — only `is_remote: true` listings pass.
     - `hybrid_ok` — remote, hybrid, or onsite listings pass **if** remote or in an allowed location.
     - `onsite_ok` — same pass set as `hybrid_ok` (the distinction affects *scoring*, not filtering).
     - `any` — remoteness never disqualifies.
  3. **`locations_allowed`** — empty list = anywhere (subject to 1 and 2); non-empty = the parsed
     location must also match an entry.
  Matching is always against **parsed fields** (city, region, country — case-insensitive exact
  match per field), never substring-matching the raw string. A listing that says only "Remote"
  with no country passes `remote_only`/`hybrid_ok` and cannot be matched against
  `exclude_locations` — it is kept (unknown-data policy) and marked "remote scope unclear" in the
  digest (it may be remote-elsewhere-only, or based somewhere excluded).
- **Seniority:** must be within `[min_seniority, max_seniority]` **on its own track**; the profile
  sets bounds per track (either track may be disabled entirely). A listing whose track is
  disallowed is dropped; unknown seniority is kept (policy above). Omitting the whole
  `seniority` key disables seniority filtering entirely (all listings pass this filter).
- **Compensation floor:** applied only when the salary is **comparable**: same currency as
  `salary_currency` (or convertible — see below) and annualizable. Comparison rule:
  annualize (`day × 260`, `hour × 2080`, `month × 12`), convert currency using the pinned rates
  table in the profile (`fx_rates`, e.g. `USD: 1.5` meaning 1 USD = 1.5 AUD); if the currency has
  no pinned rate, the salary is treated as **unknown** (kept per policy), not dropped. Drop only
  when the comparable annualized max < `salary_floor`.
- **Employment type:** drop listings whose `employment` is in `exclude_employment`
  (e.g. `[contract, internship]`). Unknown employment is kept.
- **Deal-breaker keywords:** each exclusion term matches **case-insensitively on word boundaries**
  and is **scoped**: `title` (matches title only), `requirements` (matches title or description —
  the default), with an optional per-term scope override in the profile. A bare mention in a
  "nice to have" laundry list is exactly why scoping exists — put hard tech deal-breakers on
  `title` scope if description matches prove too aggressive.
- **Required keywords (domain anchor):** if `require_keywords` is non-empty, a
  listing must match **at least one** term (same word-boundary + scope semantics
  as deal-breakers) or it is dropped. Use for domain anchoring — e.g. a culinary
  trainer profile requiring one of {cookery, chef, food, kitchen} so trainer
  ads from unrelated fields (counselling, business) never surface. Empty list =
  no requirement.
- **Freshness:** drop listings older than `max_age_days` (by `posted_at`, falling back to `first_seen_at`).
- **Dismissed:** drop anything I explicitly dismissed in a prior run (see Stage 7 — dismissals are
  permanent; *seen* items are handled at presentation, not dropped here).

**Acceptance criteria**
- A listing failing any hard requirement never appears in the output, regardless of how well it would otherwise score.
- A listing with a null value for a filtered field is retained (and marked), per the unknown-data policy — verified per filter against fixtures.
- A listing labelled remote whose parsed location matches `exclude_locations` (e.g. "Remote — Sydney-based") is dropped, tallied under `by_location`.
- With `locations_allowed: []`, no listing is dropped for its location alone — only `remote_policy` or `exclude_locations` can drop it.
- A `$900/day AUD` contract listing correctly annualizes to ~$234k for the floor comparison; a USD salary with a pinned rate converts; one without a pinned rate is treated as unknown.
- Changing a single hard filter in the profile changes the surviving set on the next run, with no code edit.
- An exclusion term matches only on word boundaries and only within its scope ("PHP" with `title` scope does not drop a listing that mentions PHP in the description).
- With `require_keywords` set, a listing matching none of the terms is dropped (tallied as `missing required`); matching any single one passes; an empty list imposes no requirement.
- The run reports how many listings were dropped and by which filter (a small tally), so filters can be debugged.

## Stage 5 — Score (soft preferences)

Score each surviving listing 0–100 for **fit** against my soft preferences. Scoring is a
**purely rule-based, deterministic** weighted sum of components; weights live in the profile.
(LLM-assisted matching is explicitly deferred — see `04-technical-plan.md` §Decisions.)
Components:

- **Skill / keyword match** — overlap between my target skills and the listing's title + description (word-boundary, case-insensitive).
- **Seniority fit** — distance from my target level *on the listing's track* (exact = full marks; unknown = neutral).
- **Compensation** — how far the comparable annualized salary max exceeds my floor toward my target (unknown/incomparable salary = neutral, not zero).
- **Location / remote fit** — preferred location or fully-remote scores higher than merely-allowed.
- **Company signal** — bonus for `preferred_companies` matches. *(Industry and company-size signals
  are cut from v1 — no source provides that data. See `04-technical-plan.md` §Decisions.)*
- **Recency** — newer postings score slightly higher.

Each component yields a sub-score in [0, 1] and a short human-readable reason.

**Normalization rule:** `score = 100 × Σ(weightᵢ × subᵢ) / Σ(weightᵢ)` over the **active** (non-zero)
weights. Weights are therefore *relative*: zeroing a component redistributes its influence, which
shifts all scores — so `display_threshold` is meaningful only relative to the current weight set.
The digest header states the active weight set so a threshold change is never a silent surprise.

**Acceptance criteria**
- Every scored listing carries its total score **and** a per-component breakdown with reasons.
- Component weights are read from the profile; zeroing a weight removes that component's influence.
- Scoring is deterministic and pure: the same listing + same profile → the same score, every run, with no network calls.
- An unknown field yields the neutral sub-score (0.5) for its component, never 0.
- No listing that passed the hard filter is dropped at scoring; scoring only orders, never disqualifies.
- Golden-corpus listings score within ±1 point of their expected scores under the fixture profile.

## Stage 6 — Rank & threshold

Sort by score descending. Optionally hide anything below a "worth-my-time" score threshold from
the profile (still counted in the tally, just not shown).

**Acceptance criteria**
- Output is ordered best-first.
- Ties break by recency (newer first), then by listing `id` (for full determinism).
- Listings below the display threshold are excluded from the shortlist but counted as `below_threshold` in the run report.

## Stage 7 — Present (digest)

Produce the run's output as a human-readable digest plus a machine-readable file.

- **Digest (Markdown/HTML):** two sections —
  1. **New this run** — ranked listings not shown in any prior run (or materially changed since, see below).
  2. **Previously shown** *(optional, collapsed/secondary)* — still-live, still-passing listings
     from prior runs, so yesterday's shortlist remains reviewable. Controlled by
     `output.show_previously_seen` (default true).
  Each entry shows: **listing id** (short form, for the dismiss command), title, company,
  location/remote, salary (or "not listed"), score, the one-line reason, source link(s), and
  posted date. Unknown-field markers ("level unclear" etc.) appear inline. A header summarizes:
  N new, M previously shown, K below threshold, sources used, sources failed, filter tally, and
  the active weight set.
- **Digest size bound:** the "New this run" section shows at most `output.max_shown` entries
  (default 25); overflow is counted and available in the data file.
- **Data file (JSON/CSV):** all scored survivors with full fields, for later tooling or a tracker.
- **Seen-state:** record `(id, content_hash)` for every listing shown. A listing re-enters
  "New this run" only if its stored `content_hash` differs from the current one — this is the
  definition of **materially changed** (title, salary, location, or description changed; source
  list or posted-date churn alone does not qualify since those fields aren't hashed).
- **Dismissals (CLI):** `jobhunter dismiss <id> [<id>…]` appends ids to the dismissed set;
  `jobhunter undismiss <id>` reverses it; `jobhunter dismissed` lists them. Dismissed ids never
  appear in either digest section again (enforced at Stage 4).

**Acceptance criteria**
- Every digest row contains all fields listed above; no row omits its id, score, or reason.
- Re-running immediately produces "0 new" **and** the prior listings still visible under "Previously shown" (when enabled).
- A fixture listing whose salary changes between runs re-appears as new; one whose only change is a new source link does not.
- `jobhunter dismiss <id>` removes that listing from all future digests; `undismiss` restores eligibility.
- The data file round-trips: it can be re-loaded without loss of any scored field.

---

## Core user stories

1. *As Justin, I run the tool and get a ranked shortlist of new-since-last-time roles, best first, each with a reason — without opening a job board.*
2. *As Justin, I raise my salary floor in the profile and the next run drops the now-too-low roles.*
3. *As Justin, I add a deal-breaker keyword and matching roles disappear from future runs.*
4. *As Justin, I see the same role posted on two boards as a single entry with both links.*
5. *As Justin, I run `jobhunter dismiss <id>` on a role from the digest and never see it again.*
6. *As Justin, one board is down and I still get results from the others, with a note that one source failed.*
7. *As Justin, I can still review yesterday's shortlist today — seen roles move to "Previously shown" rather than vanishing.*
8. *As Justin, a role whose salary was updated re-surfaces as new; cosmetic churn doesn't.*
9. *As Justin, "remote but must be Sydney-based" postings never reach my shortlist — `exclude_locations` drops them even though they're labelled remote.*

## Modes of operation (v1)

Two independent axes: **how runs happen** (execution) and **how hungry the
search is** (posture).

### Execution

- **On-demand:** run manually, get a digest for the current moment.
- **Scheduled:** run on a cadence (e.g. each weekday morning) and produce a fresh "new since yesterday" digest. (Delivery mechanism — file, email, notification — is an open question in the technical plan.)

### Search posture (`search_mode` in the profile)

The user's situation changes how wide the net should be. `search_mode` is a
named **preset over existing profile knobs** — pure config, no special-cased
pipeline behavior ("config over code"). A preset supplies defaults; any knob
the profile sets explicitly always wins.

| `search_mode` | Situation | Preset defaults |
|---|---|---|
| `active_unemployed` | Not employed, actively looking — cast wide, move fast | `display_threshold: 40`, `max_shown: 40`, `max_age_days: 30`, suggested cadence: daily |
| `active_employed` | Employed and actively looking — balanced | `display_threshold: 55`, `max_shown: 25`, `max_age_days: 21`, suggested cadence: each weekday |
| `passive_employed` | Employed, only wants to hear about clearly interesting opportunities | `display_threshold: 70`, `max_shown: 10`, `max_age_days: 14`, suggested cadence: weekly |

Omitting `search_mode` applies no preset (all knobs at their schema defaults).
Cadence is advisory — scheduling lives outside the tool (cron / scheduled task);
the suggested cadence is documentation for setting that up.

**Acceptance criteria**
- A preset fills only knobs the profile leaves unset: an explicit `display_threshold` in the profile beats the preset's value.
- Switching `search_mode` alone (no other edits) changes the surfaced set on the next run, with no code edit.
- The digest header names the active `search_mode` (or "none") so a digest is interpretable on its own.
- With no `search_mode`, behavior is identical to pre-posture versions (schema defaults).
