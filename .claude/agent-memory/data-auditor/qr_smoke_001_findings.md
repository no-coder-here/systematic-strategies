---
name: qr-smoke-001-findings
description: Data audit for QR-SMOKE-001 (BTC SMA100 smoke test) — HL-native BTC 1h/funding fetch, empirical range discovery, recommended validation window
metadata:
  type: project
---

Audit date 2026-08-16, against `docs/data_contract.md` v1.4 FROZEN, `docs/backtest_contract.md`
v1.5.1 FROZEN, and the (then-DRAFT, TBD-windows) `docs/qr_smoke_001_spec.md`. Verdict: **DATA PASS**.

**Why this matters going forward:** HL-native BTC 1h OHLCV is EXTREMELY SHORT — only ~208 days
(≈5000 hourly bars), a rolling window that starts wherever "now minus ~5000h" lands, NOT a fixed
calendar date. On 2026-08-16 that was 2026-01-20 11:00 UTC. **Re-derive this empirically every
time**, don't reuse 2026-01-20 as a constant — by the time this is read the window will have
rolled forward. See [[repo_layout]] for where the code lives.

**How to apply:** Any work order that needs a bounded HL-native + Binance-proxy overlap window
(cross-venue validation, smoke tests, anything needing both venues simultaneously) is bottlenecked
by this ~208-day HL rolling ceiling, not by Binance (which goes back to 2019-09-08 for BTC) or by
HL funding (goes back to 2023-05-12 for BTC, confirmed empirically, matches F12's 2023-05-20
asset_ctxs start closely). Always re-run the empirical discovery method below rather than trusting
a previously-recorded date.

## Empirical discovery method (reuse this, don't ad-hoc `curl`)

Use `HyperliquidClient.fetch_candles_paginated(coin, "1h", anchor_ms, now_ms, bar_ms,
MAX_CANDLES_PER_REQ)` from `src/data/hyperliquid/client.py` with a far-past `anchor` (e.g.
2019-01-01) — this is the *actual* F1-safe backward-walking pagination already implemented in the
codebase (verified by direct probe: a window strictly before the discovered start returns `[]`,
confirming it's the true retention ceiling, not a truncation artifact). Do not write a fresh ad-hoc
paginator; the shipped one already handles the trap correctly. Same technique for
`fetch_funding_paginated` (forward-walking) to find true funding start.

## Sandbox networking gotcha (new, important for future audits)

The command sandbox's network proxy **silently truncates large HTTP responses** (~650-700KB) —
`urllib`'s `resp.read()` raises `http.client.IncompleteRead` on Hyperliquid's full 5000-candle or
500-funding-record pages, but small pages (~100 bars) succeed fine. This looks exactly like a
transient network failure but is 100% reproducible and is a sandbox artifact, not a real API
problem — confirmed by re-running the identical request with `dangerouslyDisableSandbox: true`,
which succeeds immediately. **Any real Hyperliquid data-layer network call in this repo needs
sandbox disabled**, not retried in-sandbox.

## Key empirical numbers from 2026-08-16 (will be stale — re-derive, don't reuse)

- HL-native BTC 1h OHLCV: exactly 5000 bars, [2026-01-20 11:00:00, 2026-08-16 18:00:00) UTC at
  fetch time (matches contract F2's "~208 days" fact, itself dated the same freeze day).
- HL BTC funding: starts 2023-05-12T00:00:00.048Z, essentially zero jitter in the recent
  (2024-08-15→now) contiguous coverage segment (sub-second, not the ~24min historically observed
  in 2023-05/06 per F5) — but the 2023-05/06 era genuinely has many single-event coverage segments
  (gaps > 90min between consecutive funding events), consistent with F5's "genuine 8h gaps" claim.
  Coverage stabilizes into one long contiguous segment from 2024-08-15 onward.
- Binance BTC 1h: 57,696 rows, 2020-01-01→2026-07-31 23:00 UTC, zero gaps/dupes/NaNs/bad OHLC,
  `native_or_proxy=proxy`, `processing_version="qr-data-001-v1.2"` (pre-v1.3 legacy sidecar,
  loads fine via documented backward-compat shim, throws `ProvenanceVersionMismatchWarning` —
  this is the same known/cleared item as [[qr_data_001_findings]], not a new defect).
- Recommended Window A / C (bounded validation, both venues + funding all present, 100-bar warmup
  fully satisfied without partial-window fudge): warm-up `2026-01-20 20:00:00Z` →
  evaluated window `2026-01-25 00:00:00Z` → `2026-07-31 23:00:00Z` (bounded above by Binance's
  ingest ceiling, comfortably below HL's `now`). In this window: 4512 evaluated bars, 106
  long-entries/106 long-exits on HL-native close>SMA100, 107/107 on Binance (data
  characterization only, counted with `min_periods=100`, no PnL) — small transition-count
  divergence between venues is expected and worth investigating per §4.4 of the smoke spec, not
  a data defect.
- Cross-venue (same window): close-close hourly return correlation 0.9995; basis (HL-BN)/BN mean
  ~+0.5bps, p99 abs ~6.2bps; zero timestamps present in one series and missing in the other;
  confirmed NOT half-bar-shifted (correlation collapses to ~0 under a ±1h shift, both series use
  bar-OPEN left-labelled timestamps per D§3.1.2/D§16.6.5).

## Persisted artifacts (this audit, reproducible offline)

Used the EXISTING QR-DATA-001 storage layer, no new layout: `HyperliquidProvider(offline=False,
storage_base_dir="data", archive_raw_responses=True).get_ohlcv`/`get_funding`, then
`storage.write_ohlcv_parquet`/`write_funding_parquet` with a hand-built
`HyperliquidDatasetProvenance`. Wrote `data/processed/hyperliquid/ohlcv/1h/BTC.parquet` (4999
rows), `data/processed/hyperliquid/funding/BTC.parquet` (28056 rows, full 2023-05-12→now native
range, not just the OHLCV-bounded window), matching provenance sidecars under
`data/metadata/hyperliquid/`, and raw verbatim archive under `data/raw/hyperliquid/`. Verified
offline reload (`HyperliquidProvider(offline=True, ...)`) works with zero network access.
`.gitignore` correctly excludes `data/raw|processed` but not the new `data/metadata/hyperliquid/`.

## Note on `docs/qr_smoke_001_spec.md`

Found DRAFT v0.1 pending this exact audit (§2.2 has `TBD_A_START`/`TBD_A_END` placeholders to be
filled once the data audit lands). Deliberately did NOT edit this file — filling in the frozen-
pending spec is a Research Lead / spec-authorship decision, not data-validation scope; reported the
recommended window in the audit response instead for them to transcribe.

See [[audit_method]] and [[repo_layout]] for general technique/layout notes that still apply.
