---
id: proof-estate-review
title: Review the baseline proof estate
depends_on: []
informs: []
order: '1'
parent: null
---

# Review the baseline proof estate

Not started. Every proof was frozen as baseline when the methodology was adopted on 2026-11-20 and awaits review under the bounded adoption recorded in `tests/proof-estate.yaml`.

## Goal

Give each baseline proof a disposition in bounded batches, each started for a concrete signal and with its reason, boundary and stopping point stated first, as `policies/test-suite-governance.md` requires. The first batch is the remote-setup family, whose fixture clocks failed under machine load at adoption.

## Acceptance

- `./bin/test-governance validate` passes with fewer pending baseline proofs than before the batch, and every disposition has its evidence fields.
- No proof is retired without its replacement or reason recorded in the ledger.
