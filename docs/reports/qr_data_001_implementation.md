# QR-DATA-001 — Implementation Report (current)

**Spec:** `docs/data_contract.md` **v1.3 FROZEN** (snapshots of v1.0–v1.2 in `docs/spec_history/`)
**Downstream contract:** `docs/backtest_contract.md` v1.5.1 FROZEN — not modified.
**Status:** implementation complete except the live `asset_ctxs` extraction run (D§5.5.1), which is
built and mock-tested but **not executed** (see §6).

This report supersedes the earlier one, which described only spec v1.0 / M1–M18 / 104 data tests and
was stale for roughly half the shipped surface.

---

## 1. What shipped

    src/data/
        base.py schemas.py provenance.py storage.py validation.py universe.py
        aggregation.py rate_limit.py segments.py symbol_map.py
        hyperliquid/  client.py provider.py oracle.py archive.py asset_ctxs_archive.py
        binance/      client.py provider.py
    scripts/  binance_bulk_ingest.py  crossvenue_report.py  asset_ctxs_ingest.py
    tests/data/  (23 modules)

Layering (D§2.1) is enforced by a static AST scan with a self-test proving the scanner
discriminates: `src/backtest` never imports `src/data`; the shared modules never import a provider
package; the two provider packages never import each other.

## 2. Hyperliquid native layer (D§1–D§10)

Universe (232 symbols, 55 `isDelisted` retained), OHLCV 1h/4h/1d, funding events at native hourly
cadence, coverage records with `max_funding_gap` pinned at 90 min, provenance, validation reports,
raw cache + parquet store, offline reload. `survivorship_safe` is hard-pinned `False` (D§6.3).

Backfill quarantine (D§4.4): leading zero-volume/zero-trade runs are excluded as pre-listing
backfill; interior zero-volume bars are retained as genuine illiquidity.

## 3. Binance proxy price layer (D§16)

210 of 232 Hyperliquid names mapped; 22 unmatched retain native-only history. Bulk ingest completed:
**5,575,955 rows, 7,742 monthly files, 0.206 GB processed parquet** (gate limit 5 GB), 210 files
under `data/processed/binance/ohlcv/1h/`. Per-file SHA-256 verified against `.CHECKSUM`; CSV header
sniffed per file (pre-2022 archives have no header row); raw ZIP/CSV never persisted.

Measured divergence from the contract's F14–F16 estimate: the real archive floor is **2020-01 or
later** for every sampled symbol, materially later than several `onboardDate`s (BTC claims
2019-09-08). File count therefore came in at ~half the estimate. No correctness impact — D§16.2
uses the first genuinely archived month and fabricates nothing.

Canonical storage is **1h only**; 4h/1d are derived (D§16.4). Binance and Hyperliquid datasets are
maintained side by side and never merged (D§16.7, DECISION 1).

## 4. Cross-venue proxy validation (D§17)

BTC, ETH + 8 liquid alts, ~200-day overlap, 4,423 bars each: return correlations **0.993–0.9996**,
mean absolute return difference 0.0001–0.0008, volatility ratios 0.997–1.001, one large-discrepancy
event total (AVAX, single bar, `high` off 8.1%). High-volatility decile shows no breakdown. kPEPE on
the unit-normalized path: correlation 0.9973, multipliers 1000/1000. No D§17.4 escalation.

## 5. Unit multipliers (D§16.3.4, DECISION 3)

Mapping entries carry `hl_unit_multiplier`, `venue_unit_multiplier`, `verified_by`, `status`; a
mapped entry missing a multiplier or citation **raises**. Neither venue publishes a machine-readable
multiplier, so `verified_by` is a checked-in reviewed constant citing each venue's naming-convention
docs — runtime parsing of `1000` out of a symbol string is explicitly rejected. Comparison happens
after normalization.

**Known weakness (carried, not closed):** `verified_by` is one boilerplate citation reused across
~195 "standard 1:1" entries rather than per-instrument evidence. No live impact — no mapped Binance
symbol carries a scale prefix other than the four `1000*` k-tokens — but it is a rubber stamp if the
table is later extended with an unusual contract.

## 6. Funding / oracle (D§5.5, D§5.5.1) — DEFERRED BY USER DECISION

**Status: deferred 2026-08-16 (spec v1.4). Not a blocker.** Tracked as `DEFERRED-001` in
`docs/TODO.md`. `event_price` is architecturally supported but **has not been live-backfilled or
validated**; `event_price` provenance MUST NOT be claimed unless the oracle dataset actually exists.

`funding_notional_basis="period_start"` is the operative default for all research and backtests and
must be stated explicitly in research outputs; `notional_price` is NaN.

The `event_price` path (`hyperliquid/oracle.py`, `hyperliquid/asset_ctxs_archive.py`) is fully
implemented and mock-tested — streaming per segment, bounded footprint, 2-minute gap cap, persisted
high-water mark, incremental refresh, alignment reporting — but has **never been run against the
real Requester-Pays archive**. Two implementer agents declined on credentials/funds grounds and a
third attempt was stopped by the permission layer.

Therefore **unverified against real data**: actual transient egress, final processed size, real
alignment statistics, and the archive's real schema across the 3-year span. One property was
confirmed by a single read-only pilot fetch: segment `20250601` contains exactly one UTC calendar
day (198 symbols, 284,922 rows, BTC 00:00→23:59, 1,439 rows — one minute absent).

## 7. Archival trade reconstruction (D§14) — validation-only

Cancelled as a backfill by amendment B. Code retained for bounded validation, including the
fills-vs-trades double-counting trap (D§14.3) and `hash` non-uniqueness (D§14.4). No Requester-Pays
bucket was ever touched. Reconstructed candles appear on no default research path.

## 8. Tests and mutation proof

**446 passed, 6 skipped.** All 6 skips are `@pytest.mark.integration`, gated solely by root
`conftest.py`; no other skip/xfail/env-guard mechanism exists. No mandatory deterministic test is
skipped.

Mutations M1–M49 performed across rounds (mutate → confirm RED → restore → verify clean diff), plus
audit-repair mutations D1–D4 below.

**Inert/dead-code defects found and fixed** — five to date, every one via mutation proof rather than
a failing suite:
1. `test_duplicate_stored_frequency_datasets_prohibited_M37` — asserted against an empty `tmp_path`.
2. `test_binance_provenance_to_engine_provenance_shape` — asserted only the Hyperliquid path.
3. `build_segment_manifest`'s overlap guard — deleting it broke zero tests (audit D4).
4. Aggregation tests all used grid-aligned `window_start`, hiding D1.
5. The cross-day oracle test processed both days in one call, hiding D3.

## 9. Audit repair cycle 1 (defects found by independent data-auditor)

| # | Defect | Fix | Mutation proof |
|---|---|---|---|
| D1 | 4h/1d buckets anchored to caller's `window_start`, so identical 1h data yielded different, non-comparable derived series per query | anchor to fixed UTC epoch grid | re-anchoring → 6 tests RED |
| D2 | `(0.1, 10.0)` **inclusive** ratio bounds passed a 100-vs-1000 multiplier error (ratio exactly 0.1) | `(0.5, 2.0)` compared **strictly** | old bounds → 2 tests RED |
| D3 | `last_row_by_symbol` function-local, so incremental per-day calls lost cross-day carry state and left near-midnight events unpriced | persisted in the high-water mark | not resuming → 2 tests RED |
| D4 | `build_segment_manifest` overlap guard was a surviving mutation | branch proven reachable (same-timestamp/different-provenance) and tested | deleting guard → 2 tests RED |
| D5 | this report was stale | rewritten | — |

D1 is the one with research consequences: since 1h is the sole canonical stored frequency,
aggregation is the only path to 4h/1d, so every non-grid-aligned query silently produced different
bars.

## 10. Open items

All tracked in `docs/TODO.md`; none blocks the research pipeline.

1. `DEFERRED-001` — live `asset_ctxs` run / `event_price` funding. **Deferred by user decision**;
   `period_start` is operative.
2. `DEFERRED-002` — instrument-lineage mapping for renames (22 unmatched symbols stay native-only).
3. `DEFERRED-003` — `verified_by` rubber-stamp risk (§5).
4. `DEFERRED-004` — full-universe live `get_universe()` inference, and the Hyperliquid 1h→4h/1d
   aggregation fallback, both unexercised at real scale.
5. UTC-day boundary for archive segments confirmed on one sampled segment only (see DEFERRED-001).

## 11. Final status

**DATA PASS** (independent re-audit, after repair cycle 1), 446 passed / 6 skipped.
QR-DATA-001 is closed. No alpha research started; QR-SMOKE-001 not begun.
