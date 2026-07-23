# SPDX-License-Identifier: Apache-2.0
"""Stage 6 — Rank & threshold.

Sort scored results best-first. Ties break by recency (newer first), then by listing id
(for full determinism). Apply the display_threshold from the profile: listings below it
are counted as below_threshold in the run report but not returned in the shortlist.

See: specs/02-functional-spec.md §Stage 6
"""

from __future__ import annotations
