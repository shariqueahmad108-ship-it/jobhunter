# SPDX-License-Identifier: Apache-2.0
"""Stage 4 — Hard filter (disqualifiers).

Remove listings that fail any hard requirement from the profile (pass/fail, not scored).
Unknown-data policy: when the field a filter needs is null/unparseable, the listing is
KEPT (never dropped for missing data) and marked in the digest.

Filters applied in order:
  1. exclude_locations  — drop even when labelled remote
  2. remote_policy      — remote_only | hybrid_ok | onsite_ok | any
  3. locations_allowed  — positive restriction (empty = any)
  4. seniority          — per-track min/max bounds; disallowed track = drop
  5. salary_floor       — comparable annualized max vs floor; unknown = keep
  6. exclude_employment — drop if in list; unknown = keep
  7. exclude_keywords   — word-boundary, case-insensitive, scoped
  8. max_age_days       — by posted_at, fallback first_seen_at
  9. dismissed          — never re-surface

See: specs/02-functional-spec.md §Stage 4
     specs/03-data-model.md §Profile hard_requirements
"""

from __future__ import annotations
