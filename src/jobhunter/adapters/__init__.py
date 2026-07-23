# SPDX-License-Identifier: Apache-2.0
"""Source adapters — one module per job board / API.

Each adapter implements the SourceAdapter protocol from jobhunter.ingest:
  - name: str
  - search(keyword, location, max_results) -> list[RawListing]
  - normalize(raw) -> JobListing
"""
