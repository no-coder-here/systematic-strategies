---
name: audit-method
description: Mutation-testing approach that surfaced real bugs in this repo's data layer — reuse for future data audits
metadata:
  type: feedback
---

For QR-DATA-001 (2026-08-16), independently re-deriving mutations (not trusting the implementer's
mutation table) found real, previously-unreported bugs that a "did the tests pass" review would
have missed entirely. Concretely:

- Wrote small standalone repro scripts (not just re-running the existing pytest mutation) to probe
  **boundary values** of tolerance bands (e.g. `UNIT_NORMALIZATION_RATIO_BOUNDS = (0.1, 10.0)`) —
  found that the exact realistic failure mode the bound exists to catch (a 10x multiplier mixup,
  e.g. 100 vs 1000) lands precisely on the inclusive boundary and does NOT raise. The shipped test
  suite only ever tested the egregious 1000x case, never the boundary itself.
- Tested **cross-call / stateful** behavior, not just single-call unit tests: an incremental
  pipeline (`asset_ctxs_archive.run_incremental_extraction`) had a real bug only visible when
  called twice across a day boundary (state that's function-local, not persisted, gets silently
  dropped) — the one existing "cross-day" test happened to process both days in a single call,
  masking exactly this gap.
- Tested with **misaligned/unusual real-world inputs** the existing tests never used — e.g. every
  aggregation test used a UTC-midnight-aligned `window_start`; feeding a non-aligned start exposed
  that 4h/1d bucket boundaries are anchored to the query window rather than a fixed UTC calendar
  grid (`src/data/aggregation.py::aggregate_ohlcv_1h_to`).
- For "surviving mutation" hunts, mutate the actual mechanism in question (e.g. delete a defensive
  re-assertion loop), not just the sibling function that has its own test — found one defensive
  check (`build_segment_manifest`'s overlap re-assertion in `src/data/segments.py`) is structurally
  unreachable dead code: the only test exercising "overlap rejection" targets a different function
  (`assert_segments_agree_with_rows`) via a hand-built adversarial manifest, never the one actually
  guarded by the removed code.

Takeaway: read the tolerance constants and stateful/incremental code paths specifically for
"what's the realistic failure mode this exists to catch, and does the test suite actually probe
the edge of the range / the multi-call case / the unaligned input," rather than treating "green
CI" as sufficient. See [[qr_data_001_findings]] for the specific bugs this produced.
