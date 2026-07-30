<!-- SPDX-License-Identifier: Apache-2.0 -->

## What this changes

<!-- One concern per PR. Say what behaviour differs after this merges. -->

## Why

<!-- Link an issue, or explain the problem it solves. -->

## Checklist

- [ ] `python3 -m pytest -q` passes
- [ ] `ruff check src tests tools` passes
- [ ] Behaviour change? The relevant `specs/` document is amended in this PR
- [ ] Pipeline-stage change? That stage's test module is added to or extended
- [ ] New source files carry an SPDX Apache-2.0 header
- [ ] No personal data or credentials (`profile.yaml`, `profile-*.yaml`, `.env`, real digests)
- [ ] New source adaptor? Endpoint verified live, fixture payload recorded, `ADAPTORS.md` checklist followed
