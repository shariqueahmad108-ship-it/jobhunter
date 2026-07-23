# SPDX-License-Identifier: Apache-2.0
"""Stage 5 — Score (soft preferences).

Purely rule-based, deterministic weighted sum of components; weights come from the profile.
score = 100 × Σ(wᵢ · subᵢ) / Σ(wᵢ)  over active (non-zero) weights.
Unknown fields yield the neutral sub-score (0.5), never 0.
No network calls; deterministic given the same listing + profile.

Components:
  skill_match    — overlap of target_skills with title + description
  seniority_fit  — distance from target level on the listing's own track
  compensation   — comparable annualized salary vs floor and target
  location_fit   — preferred location or fully-remote scores higher
  company_signal — bonus for preferred_companies matches
  recency        — newer postings score slightly higher

See: specs/02-functional-spec.md §Stage 5
     specs/03-data-model.md §ScoredResult
"""

from __future__ import annotations
