# SPDX-License-Identifier: Apache-2.0
"""Stage 2 — Normalize.

Map each source's raw payload into the canonical JobListing schema:
title, company, location (raw + parsed), salary (parsed into numeric min/max +
currency + period), description (HTML stripped), posted date, seniority (inferred).
Missing fields are null, never guessed.

See: specs/02-functional-spec.md §Stage 2
     specs/03-data-model.md §JobListing
"""

from __future__ import annotations

# Seniority inference uses ordered rule tables applied word-boundary, case-insensitive.
# The same table is used by the dedupe stage for title normalization so the two never drift.
# IC track:         intern < junior < mid < senior < staff < principal
# Management track: manager < senior_manager < director < vp

IC_LEVELS = ["intern", "junior", "mid", "senior", "staff", "principal"]
MANAGEMENT_LEVELS = ["manager", "senior_manager", "director", "vp"]
