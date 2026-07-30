<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Security policy

JobHunter is a personal command-line tool. It has no server, no accounts and
no multi-user surface, so its realistic risk is narrow: it reads a local
config file, makes outbound HTTPS requests to job-board APIs, and writes
Markdown and JSON to disk.

## Reporting a vulnerability

Report suspected vulnerabilities privately via GitHub's
[Report a vulnerability](https://github.com/justinmclean/jobhunter/security/advisories/new)
form rather than in a public issue. Please include what you did, what
happened, and the version or commit.

Expect an acknowledgement within a week. There is no bounty.

## In scope

- Code execution or arbitrary file write triggered by a malicious source
  payload, feed, or `profile.yaml`.
- Leaking credentials (`ADZUNA_APP_ID`/`ADZUNA_APP_KEY`, `JOOBLE_API_KEY`) into
  digests, run snapshots, logs, or error messages.
- Path traversal in digest, state or snapshot handling.
- A dependency vulnerability that JobHunter actually reaches.

## Out of scope

- Anything requiring an attacker who already has write access to your
  `profile.yaml`, `.env`, or shell.
- Rate limits, terms of service, or availability of third-party job boards.
- Vulnerabilities in job-board APIs themselves — report those to the board.

## Handling your own secrets

API keys belong in environment variables or a git-ignored `.env`. `profile.yaml`,
`profile-*.yaml`, `.env`, `state/` and `digests/` are git-ignored on purpose; a
pre-commit hook refuses to stage the first three. If you ever commit a key,
revoke it at the provider — rewriting history is not enough.
