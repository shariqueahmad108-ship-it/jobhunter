# SPDX-License-Identifier: Apache-2.0
"""Stage 3 — Dedupe.

Collapse cross-posted duplicates: two listings with the same normalized identity
key (lowercased, whitespace-collapsed, punctuation-stripped company+title+city|country)
or the same resolved URL are merged into one listing that retains all source links,
the earliest first_seen_at, and the most complete non-null fields.

See: specs/02-functional-spec.md §Stage 3
     specs/03-data-model.md §Notes on identity & dedupe
"""

from __future__ import annotations
