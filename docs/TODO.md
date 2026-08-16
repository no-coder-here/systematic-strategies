# Deferred items / TODO

Tracked, deliberately deferred work. Nothing here blocks the research pipeline.

---

## DEFERRED-001 — Hyperliquid `event_price` funding via `asset_ctxs`

**Status:** deferred by user decision, 2026-08-16. Not a blocker.
**Spec:** `docs/data_contract.md` v1.4, D§5.5.1 (rule 2), D§12 item 8.
**Code:** `src/data/hyperliquid/asset_ctxs_archive.py`, `src/data/hyperliquid/oracle.py`,
`scripts/asset_ctxs_ingest.py`. Tests: `tests/data/test_asset_ctxs_archive.py`,
`tests/data/test_oracle.py`.

### What is true today

1. **`event_price` funding is supported by the architecture but has NOT been live-backfilled or
   validated.** The extraction pipeline is implemented and unit-tested against mocked segments —
   streaming per segment, bounded raw footprint, 2-minute gap cap, persisted high-water mark,
   incremental refresh, alignment reporting. It has **never been run against the real
   Requester-Pays S3 archive**, so actual egress, processed size, real alignment statistics, and
   the archive's schema stability across its full span are all **unverified**.

2. **Current research and backtests MUST use `funding_notional_basis="period_start"` explicitly.**
   This is the operative default. Its error is bounded by `|rate| × (max intra-period price move)`
   — backtest contract §7.6 records a −6.98% misstatement of a day's funding on a +15% intra-day
   move. This is not negligible and MUST be stated in research outputs.

3. **Do NOT claim `event_price` funding provenance unless the oracle dataset actually exists.**
   A missing compact artifact leaves `notional_price` NaN and the basis `period_start`. It must
   never be silently reported as `event_price`, and no placeholder artifact may be fabricated.
   (Verified at DATA PASS: `oracle_price_lookup` defaults to `None`; no artifact exists under
   `data/`; no default claims `event_price` support.)

4. **A future task may perform the compact `asset_ctxs` extraction and validation.** Requires fresh
   explicit authorization — the archive is Requester Pays and egress bills to our AWS account.

### If it is ever picked up

- Measured cost basis: archive is **1169 files / 9.63 GB / 8.24 MB per day**, ≈ **$0.87** egress,
  ≈ 4.3 h sequential at the measured 13.4 s/day. Retained output ≈ 50–100 MB.
- Raw segments must be discarded as processed; peak local raw footprint ≈ one segment.
- **Unverified assumption to check first:** archive segments are UTC calendar days. Confirmed for
  exactly one sampled segment (`20250601`: 198 symbols, 284,922 rows, BTC 00:00→23:59 with 1,439
  rows — one minute absent), **not** across the three-year span.
- Repair D3 (persisting cross-day carry state in the high-water mark) fixed a defect that only
  manifests in the real incremental usage pattern. Related edge cases in this module are
  correspondingly under-exercised, since none of it has run on real data.

---

## DEFERRED-002 — Instrument-lineage mapping (renames/migrations)

Deferred by user DECISION 2 (2026-08-16). `MATIC`/`POL`, `RNDR`/`RENDER`, `FTM`/`S` and similar stay
**unmapped**; migrations must never be inferred from symbol similarity. A reviewed, explicit lineage
mapping layer is future work. Until then these assets carry native-only history.
See `docs/data_contract.md` D§16.3.3, D§6.4.

---

## DEFERRED-003 — `verified_by` citation granularity

`src/data/symbol_map.py` uses one boilerplate citation reused across ~195 "standard 1:1" entries
rather than per-instrument evidence. No live impact today — no mapped Binance symbol carries a scale
prefix other than the four `1000*` k-tokens — but it becomes a rubber stamp if the table is extended
with an unusual contract. Flagged by the independent audit; not blocking.

---

## DEFERRED-004 — Full-universe live validation

`get_universe()` inference has not been exercised against all 232 symbols at live scale (BTC/ETH
scoped only). Likewise the 1h→4h/1d aggregation fallback for Hyperliquid-native data is
real-data-unexercised, since native 4h/1d reach further back than 1h (contract F2).

---

## Known limitation (not deferred work — a standing property of the data)

Native Hyperliquid **1h** OHLCV reaches back only ~208 days via `candleSnapshot`, and trade-level
archives bottom out at 2025-03-22. This is why Binance USDⓈ-M 1h is the canonical long-history price
series (proxy-labelled). 22 of 232 Hyperliquid assets have no Binance perp and are limited to native
history — universe depth therefore correlates with "is this a Binance-listed major", which is a
selection effect to keep in mind in cross-sectional research.
