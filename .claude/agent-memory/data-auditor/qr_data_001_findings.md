---
name: qr-data-001-findings
description: Independent data audit findings for QR-DATA-001 (Hyperliquid+Binance data layer), 2026-08-16
metadata:
  type: project
---

Audited `docs/data_contract.md` v1.3 FROZEN against `src/data/**`, `tests/data/**`, real ingested
data in `data/processed/binance` (210 parquet, ~206MB) and `data/metadata/`. Verdict: DATA PASS
WITH WARNINGS (see full report given to Research Lead for exact blocking/material breakdown).

**Why: this is a snapshot of what was true 2026-08-16 — if asked about "current" state of this
work order, re-verify rather than reciting this.**

Key reproducible findings (all independently reproduced with standalone scripts, not just
re-running shipped tests):

1. `validation.UNIT_NORMALIZATION_RATIO_BOUNDS`/`binance.provider.UNIT_EQUIVALENCE_RATIO_BOUNDS =
   (0.1, 10.0)` — a 10x multiplier mixup (e.g. hl_unit_multiplier recorded as 100 instead of the
   correct 1000) produces a ratio of exactly 0.1, which is INSIDE the inclusive bound and does NOT
   raise `UnitNormalizationError`/`UnitEquivalenceError`. Only ~1000x-class errors are reliably
   caught; realistic order-of-magnitude mapping bugs sitting near the boundary can slip through.
2. `src/data/aggregation.py::aggregate_ohlcv_1h_to` anchors 4h/1d bucket boundaries to the
   caller's query `window_start`, not a fixed UTC calendar grid. Since D§16.4 makes this the ONLY
   path for Binance 4h/1d (used on every single query, unlike the Hyperliquid side where native
   4h/1d usually pre-empt it), two queries for the same symbol/frequency with different
   (non-grid-aligned) start times silently produce different, non-comparable bars. No test uses a
   misaligned `window_start`.
3. `src/data/hyperliquid/asset_ctxs_archive.py::run_incremental_extraction` loses
   `last_row_by_symbol` carry-forward state across SEPARATE calls (it's function-local, not
   persisted in the high-water mark). A funding event shortly after UTC midnight gets wrongly left
   unpriced if the day containing its priming row was processed in an earlier, separate incremental
   call. The only existing cross-day test processes both days in one call, masking this.
4. `src/data/segments.py::build_segment_manifest`'s internal overlap re-assertion is dead code —
   removing it causes zero test failures (the grouping algorithm makes that branch structurally
   unreachable via the public API; the actually-tested overlap rejection targets the sibling
   function `assert_segments_agree_with_rows` via a hand-built manifest).
5. `docs/reports/qr_data_001_implementation.md` (implementer self-report) was written mid-stream
   (mtime 20:06) — `validation.py`, `universe.py`, `provenance.py`, `symbol_map.py`,
   `asset_ctxs_archive.py`, `oracle.py`, `binance/provider.py` were all modified AFTER it (through
   20:34). The report only documents M1-M18 against spec v1.0 and a 104-test suite; the real
   suite has 280 data tests (274 pass + 6 integration-skipped) and spec is now v1.3 with M1-M49.
   **How to apply: always check implementer-report mtime vs. source mtimes before trusting a
   report's claims, on this repo or similar ones.**

Positive/cleared items (don't re-litigate absent new changes): `.gitignore` correctly excludes
`data/raw|processed` but not `data/metadata` (verified via `git check-ignore`); the 210 real
Binance sidecars are pre-v1.3 (`processing_version="qr-data-001-v1.2"`, legacy single
`unit_multiplier` field) and `BinanceDatasetProvenance.from_json_dict`'s backward-compat shim
loads them correctly with a loud `ProvenanceVersionMismatchWarning`; symbol_map.py's 210
mapped/22 unmatched exactly matches spec's F14; layering test (`test_layering.py`) is a solid
AST-based static scan that self-tests its own discrimination; the 6 skipped tests are genuinely
only `@pytest.mark.integration`-gated (no hidden skip/xfail/env-guard mechanisms found).

## Repair cycle 1 re-audit (2026-08-16, after Research Lead's fixes) — verdict DATA PASS

All five D1-D5 defects independently re-verified as genuinely fixed, not just green-suite theater:
- D1 (`aggregation.py`): now anchors to a fixed UTC epoch grid (`pd.Timestamp(0, tz="UTC")`),
  confirmed via direct repro that two differently-windowed queries over identical 1h data produce
  identical overlapping bucket boundaries/values, and clipped/partial buckets are still suppressed.
  Reverting to `bucket_start = window_start` turns 6 tests RED.
- D2 (`UNIT_NORMALIZATION_RATIO_BOUNDS`/`UNIT_EQUIVALENCE_RATIO_BOUNDS` now `(0.5, 2.0)` strict):
  confirmed 100-vs-1000 mixup raises, exact-2x raises, real ~0.04% kPEPE divergence (from the real
  `data/metadata/_crossvenue_report.json`) passes with huge headroom. (0.5, 2.0) is defensible for
  crypto perps — a >2x same-asset cross-venue price gap is not economically plausible without
  arbitrage, so this isn't over-tight for genuine illiquidity.
- D3 (`asset_ctxs_archive.py`): carry state now persisted in `AssetCtxsHighWaterMark.carry_rows`,
  bounded at one row/symbol. Confirmed separate-vs-single-call agreement, M46 no-op still holds,
  and a synthetic legacy HWM JSON file (missing the new `carry_rows` key) loads without crashing
  (`from_json_dict` uses `.get(..., {})`) — genuinely backward compatible. No real HWM file exists
  yet in this repo (asset_ctxs was never run for real), so this compat path is currently untested
  by real data, only by a synthetic repro. Reverting `hwm.to_carry_state()` to `{}` turns 2 tests RED.
- D4 (`segments.py` overlap guard): reachable via a realistic same-timestamp/different-`dataset_id`
  input (a provenance change landing mid-bar); deleting the guard now turns 2 tests RED (was 0
  before the fix).
- D5 (report): rewritten report's mtime (20:58-21:02) postdates all D1-D4 source file mtimes;
  claimed real numbers (kPEPE median/p99 relative diff) roughly match `_crossvenue_report.json`.

Broadly re-attacked M1-M49 beyond the five repairs (not just re-running the shipped mutation
table): independently mutated M6 (funding gap widened to 8h), M20 (hash-alone dedup guard removed),
M31 (CSV header sniff forced to always-no-header), M32 (checksum mismatch check removed), M39 (5GB
gate check disabled) — all correctly turn tests RED. Also confirmed `get_funding`'s
`oracle_price_lookup` genuinely defaults to `None`/NaN `notional_price` (never fabricates
`event_price` support when the compact asset_ctxs artifact is absent — no artifact exists under
`data/` at all). No new surviving mutations found in this pass; see [[audit-method]] for technique.

See [[audit-method]] for the mutation-testing techniques that surfaced these.
