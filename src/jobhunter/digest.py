# SPDX-License-Identifier: Apache-2.0
"""Stage 7 — Present (digest).

Produce a human-readable Markdown/HTML digest and a machine-readable JSON data file.

Two sections:
  1. New this run  — ranked listings not shown in any prior run (or materially changed).
  2. Previously shown (optional) — still-live listings from prior runs.

Each row shows: listing id, title, company, location/remote, salary, score, one-line reason,
source link(s), posted date, and unknown-field markers.

Also manages seen-state: records (id, content_hash) for every listing shown; a listing
re-enters "New this run" only when its content_hash differs from what was stored.

See: specs/02-functional-spec.md §Stage 7
     specs/03-data-model.md §Run state, §Run report
"""

from __future__ import annotations
