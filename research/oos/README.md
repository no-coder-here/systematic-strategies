# Protected out-of-sample reserve — ACCESS CONTROL

**Default-deny (M§9.4).** `oos-evaluator` is the ONLY agent permitted to read
`research/oos/snapshots/**` or `research/oos/results/**`. Every other agent — and the human
researcher during strategy development — is denied by default. Adding a new agent does not grant
access; access must be granted explicitly.

## What is sealed

`protected_windows.json` holds **OOS-001**, the one-time bootstrap reserve (M§9.1.6), sealed
2026-08-20 under user-authorised Option A:

| field | value |
|---|---|
| evaluation window | 2025-08-01T00:00Z .. 2026-07-31T23:00Z (365 days, 8,760 hourly bars) |
| dependency buffer | from 2025-02-01T00:00Z (181 days, warm-up only, contributes NO metric) |
| funding coverage end | 2026-08-01T01:00Z (W6 two-sided coverage only) |
| datasets | 211 (210 Binance USD-M 1h proxy + hyperliquid.funding.BTC), 2,587,789 rows |
| hashing | `col-buffer-v2` over the canonical LONG-FORM slice, upstream of `to_engine_frame` (M§9.9.1) |
| max_reveals | 1 — a reveal burns the window permanently (M§10.1) |

Snapshots are **write-once** and **untracked** (`data_contract.md` D§8.2 forbids committing market
data). Integrity rests on the `content_hash` values recorded in `protected_windows.json`, which
recompute exactly; all 211 hashes are distinct, and dropping a single row changes a hash.

## Rules

1. No non-OOS run may touch `[dependency_start, funding_coverage_end]` (M§9.3, both directions).
2. The dependency buffer initialises indicator state only. It MUST NOT inform parameter, threshold
   or universe selection (M§9.6.3), and `dependency_start` can never be extended (M§9.6.4).
3. Metrics begin strictly at `evaluation_start` (M§9.6.2).
4. All candidate freezes must be enumerated before the first reveal (M§9.7).
5. A strategy materially derived from anything already observed on this period may not claim it
   (M§9.0) — affirmed in writing in the freeze manifest; not mechanically checkable.

## Prior infrastructure use (M§9.1.2, disclosed)

All five existing registry records are `pipeline_validation`/`exploratory` and intersect this
window; they are permitted, disclosed, and MUST be reproduced in every OOS report on OOS-001. Zero
`alpha_research`/`robustness`/`replication` records existed at seal time (M§9.1.7 satisfied).

## KNOWN EXPOSURE — read before using this window

Protection is currently **procedural, not mechanical**:

- The sealed *snapshots* are inaccessible: a separate tree that no data loader reads.
- **But `data/processed/` still contains the reserved span.** A normal research workflow reading
  Binance 1h gets 2025-02..2026-07 along with everything else. Removing it requires a destructive
  truncation of the research corpus (proposed, not executed — awaiting user decision), and even
  that is not durable, because re-running either bulk ingest re-imports the span. A durable guard
  belongs in the loader/ingest path, which is the enforcement machinery deliberately deferred.
- **Hyperliquid-native 1h is NOT sealed** and remains fully readable. It covers 2026-01-20..
  2026-07-31, i.e. roughly half the evaluation window, so the *period* is observable through native
  prices even though the sealed price series is the Binance proxy. It is retained for
  infrastructure/cost/liquidity work; using it for alpha research inside the sealed span would
  violate M§9.0.

Until a mechanical guard exists, treat the reserve as protected by discipline. Anyone who inspects
the reserved period for strategy development MUST say so, because that silently destroys the only
historical OOS window this programme has.

## Limitations of OOS-001

- **Proxy-priced.** Hyperliquid-native 1h reaches back only ~209 days, so a native-priced window of
  this length is impossible today; CLAUDE.md's native-execution preference cannot be met at this
  depth. A native forward window becomes viable around 2027.
- **Funding is BTC-only.** Only `hyperliquid.funding.BTC` existed at seal time, and `snapshot[]` is
  fixed at seal. Because M§9.9.2 requires every loaded timestamp to fall inside a sealed entry, a
  **multi-asset funding/carry strategy cannot use this window at all.** This is the most consequential
  limitation of the bootstrap and cannot be repaired for OOS-001.
- Adequate for Sharpe discrimination at daily rebalance frequency; NOT adequate for
  regime-conditional claims, which need multiple windows.
