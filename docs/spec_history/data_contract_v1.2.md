# Data Layer Contract — SPECIFICATION v1.2 (QR-DATA-001)

**Status: FROZEN** (2026-08-16, Research Lead)
**Scope:** Hyperliquid-native venue data (universe, funding, costs, liquidity) **plus** a
Binance USDⓈ-M perpetual proxy price history (D§16). No Bybit/OKX providers.

## Revision history

| ver | date | cause |
|---|---|---|
| v1.0 | 2026-08-16 | initial freeze (snapshot: `docs/spec_history/data_contract_v1.0.md`) |
| v1.1 | 2026-08-16 | **AMENDMENT QR-DATA-001-A**. v1.0 treated `candleSnapshot`'s ~5000-bar window as the native history ceiling. Superseded: official Hyperliquid S3 trade archives permit trade-based OHLCV reconstruction further back (D§14). Adds source-segment provenance to the OHLCV schema (D§15) so a future Binance-proxy prefix can never silently blend with native data. **Corrects v1.0 D§5.5**: `event_price` funding basis IS obtainable from `asset_ctxs` oracle prices (F12). |
| v1.2 | 2026-08-16 | **AMENDMENT QR-DATA-001-B**. Full Hyperliquid trade/fill backfill **cancelled** — D§14 demoted to bounded validation only (D§14.0). **Binance USDⓈ-M perpetual 1h becomes the canonical long-history price source** (D§16), labelled `proxy`, with Hyperliquid remaining authoritative for universe, funding, costs and liquidity. Canonical storage is **1h only**, with 4h/1d derived (D§16.4). Adds cross-venue proxy validation (D§17) and a compact funding/oracle policy (D§5.5 revised). |

**Downstream consumer:** `docs/backtest_contract.md` v1.5.1 (FROZEN). This document MUST NOT
contradict it. Where this document says "§n" without qualification it means a section of the
*backtest* contract; sections of *this* document are written "D§n".

---

## D0. Purpose and non-goals

Build a reusable, provider-abstracted market-data layer producing normalized, provenance-carrying,
offline-reloadable datasets for Hyperliquid perpetual futures at 1h / 4h / 1d.

**Non-goals.** No alpha research. No signal construction. No proxy/external providers. No changes
to the frozen backtest contract. No live orders.

### D0.1 The governing principle

> **The data layer never invents, never fills, never silently drops, and never presents
> non-native data as native.** Every deviation from raw venue truth is either (a) refused with an
> exception, or (b) recorded explicitly in metadata and surfaced in a validation report.

There is no third option. "Reasonable default cleanup" is prohibited.

---

## D1. Verified venue facts (retrieved 2026-08-16, `https://api.hyperliquid.xyz/info`)

These were empirically confirmed by the Research Lead prior to freeze. The implementer MUST
re-verify each against **current official Hyperliquid documentation** and record any divergence
as a blocking finding rather than silently coding to this table.

| # | Fact | Consequence |
|---|------|-------------|
| F1 | `candleSnapshot` returns at most ~5000 candles and, when the requested window is larger, returns the **most recent** slice — not the oldest. | A single `[start, now]` request silently misrepresents the start of history. Pagination MUST walk **backwards** from `endTime` (or forwards in bounded windows) and MUST NOT trust a single response's first timestamp as "first available". |
| F2 | Effective native depth: **1h ≈ 5000 bars (~208 days)**, 4h → 2024-05-05, 1d → 2020-08-19. Windows older than the retained span return `[]`, not an error. | An empty response is ambiguous: not-listed vs delisted vs beyond-retention vs API failure. D§7 defines how to disambiguate. |
| F3 | 1d BTC candles exist from 2020-08-19, but **921 bars to 2023-02-25 have `v == 0` and `n == 0`** with moving prices. Hyperliquid did not trade then. | These are **backfilled reference prices, not Hyperliquid-native trading**. Treating them as native violates CLAUDE.md's data-source policy. D§4.4 quarantines them. |
| F4 | `fundingHistory` returns at most **500 records** per request and carries `{coin, fundingRate, premium, time}` — **no price**. | Pagination mandatory (D§5.2). `funding_notional_basis="event_price"` is **not supportable** from native data (D§5.5). |
| F5 | Funding is hourly, but event timestamps carry jitter (observed up to ~24 min past the hour) and the 2023-05/06 era shows genuine 8h gaps. | `max_funding_gap` cannot be 1h exactly, and cannot be widened to swallow gaps. D§5.4 pins 90 minutes. |
| F6 | `meta.universe` retains delisted assets with `isDelisted: true` (55 of 232 at freeze) and their candle/funding history remains queryable. Asset index = array position and is stable. | Point-in-time universe reconstruction is *possible* but *inferred* — `meta` carries no listing or delisting **dates**. D§6. |
| F7 | Names are bare coins (`BTC`, not `BTCUSDT`). Seven `k`-prefixed names (`kPEPE`, `kSHIB`, `kBONK`, `kLUNC`, `kFLOKI`, `kDOGS`, `kNEIRO`) quote **1000 tokens per unit**. No duplicate names at freeze. | D§3.3 symbol identity. `k`-prefix is internally consistent for weight-based strategies but MUST be recorded. |
| F8 | Real-world renames landed as delist+relist under different names (`MATIC`→`POL`, `RNDR`→`RENDER`, `FTM`→`S`). | §11.4 forbids silent rename mapping. D§6.4: never splice; record candidates only. |

### D1.1 Archive facts (AMENDMENT QR-DATA-001-A, verified 2026-08-16)

Official source: Hyperliquid Docs → *Historical data*
(`https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data`).

| # | Fact | Consequence |
|---|------|-------------|
| F9 | **Both archive buckets are Requester Pays.** Anonymous access returns HTTP 403 `"Anonymous users cannot invoke requests against Requester Pays buckets"`. Every call needs `--request-payer requester` and AWS credentials; **egress is billed to our account.** | Access is a *cost* decision, not merely a technical one. D§14.6 caps it. |
| F10 | Trade-level archive coverage is **narrow and fragmented**, not back to launch: `node_trades/hourly` 2025-03-22→2025-06-21 (66 day-dirs over a 92-day span ⇒ **sparse**); `node_fills/hourly` 2025-05-25→2025-07-27 (64/64 ⇒ contiguous); `node_fills_by_block/hourly` 2025-07-27→2026-08-16 (386/386 ⇒ contiguous). | Genuine trade-based reconstruction reaches back to **2025-03-22 only**, with holes before 2025-05-25. It does **not** reach 2023. |
| F11 | `node_trades` schema: `{coin, side, time, px, sz, hash, trade_dir_override, side_info:[maker,taker]}`. **One record per trade** (both counterparties inside `side_info`). `time` is **naive nanosecond** (`2025-03-22T10:48:33.216798262`, no `Z`). `hash` is a **transaction** hash and is **NOT unique per trade** — one hash spans multiple trades. | Sufficient for O/H/L/C, volume and trade count. Deduplicating on `hash` alone **collapses distinct trades and understates volume** (D§14.4). Naive time needs an explicit documented UTC assertion, never silent localization. |
| F12 | `s3://hyperliquid-archive/asset_ctxs/[date].csv.lz4` covers **2023-05-20 → 2026-08-01**, ~198 coins, at **1-minute** granularity, columns `time, coin, funding, open_interest, prev_day_px, day_ntl_vlm, premium, oracle_px, mark_px, mid_px, impact_bid_px, impact_ask_px`. | Provides **historical `oracle_px`**, which is exactly the basis Hyperliquid funds on. **This supersedes v1.0 D§5.5's claim that `event_price` is unobtainable.** See D§5.5 (revised). |
| F13 | `s3://hyperliquid-archive/market_data/` (L2 book snapshots) covers 2023-04-15→2026-08-01, and `asset_ctxs` `mid_px` covers 2023-05-20→. Both are **quote data, not executed trades**: neither yields traded volume or trade count. | **Rejected as an OHLCV source** (D§14.7). Using quote-derived OHLC to extend a traded series is a silent regime change in the volume and price-formation process. |

### D1.2 Binance facts (AMENDMENT QR-DATA-001-B, measured 2026-08-16)

| # | Fact | Consequence |
|---|------|-------------|
| F14 | Binance has **654** USDⓈ-M `PERPETUAL`/USDT symbols. Of the 232 Hyperliquid names, **210 map** under the deterministic rule `NAME→NAMEUSDT`, `kNAME→1000NAMEUSDT`; **22 do not**: `MATIC, RNDR, HPOS, RLB, UNIBOT, OX, FRIEND, SHIA, CANTO, REQ, NFTI, PANDORA, MNT, BLAST, kDOGS, kNEIRO, PURR, JELLY, LAUNCHCOIN, YZY, APEX, CASHCAT`. | D§16.2. The unmatched set is mostly HL-native tokens and names Binance renamed. Mapping is an **explicit reviewed table**, never a heuristic (D§16.3). |
| F15 | Estimated **5,606,643** 1h rows across the 210 matched symbols. Deepest: `BTCUSDT` onboard **2019-09-08** (60,814 h), `ETHUSDT` 2019-11-27. | ~7× the depth of Hyperliquid-native 1h, and ~2.4× the depth of HL 1d. |
| F16 | **Measured**, not estimated, from a real `BTCUSDT-1h-2025-01` file: parquet+zstd **49.2 B/row**, source zip 52.3 B/row, unzipped CSV 125.2 B/row. Extrapolated to F15: **processed 0.276 GB**, transient zip 0.293 GB, transient CSV 0.702 GB, ~15,960 monthly files. | **Far below the 5 GB gate** — the download is authorized to proceed under D§16.5. |
| F17 | `data.binance.vision` monthly klines ship a `.zip.CHECKSUM` (SHA-256) — verified correct on the sampled file. **The CSV header changed across eras**: `2020-01` has **no header row**, `2022-06` onward **does**. | Checksums MUST be verified (D§16.6). The header MUST be **sniffed per file**; assuming either form silently shifts every column by one row (D§16.6.2). |
| F18 | `asset_ctxs`: **1169 files, 9.63 GB total, mean 8.24 MB/day**. | Sets the funding/oracle cost at ~9.6 GB **transient** egress for a ~50–100 MB processed artifact (D§5.5). |

---

## D2. Architecture

    src/data/
        __init__.py
        base.py             MarketDataProvider ABC, normalized schema constants
        schemas.py          column/dtype definitions + validators
        provenance.py       DatasetProvenance/UniverseProvenance construction + (de)serialization
        storage.py          raw cache + parquet store; offline reload
        validation.py       integrity checks -> ValidationReport
        universe.py         point-in-time universe construction
        symbol_map.py       checked-in Hyperliquid<->Binance mapping table (D§16.3)
        hyperliquid/
            __init__.py
            client.py       thin HTTP client: endpoints, pagination, retry, rate limit
            provider.py     HyperliquidProvider(MarketDataProvider)
        binance/            (v1.2, AMENDMENT B)
            __init__.py
            client.py       data.binance.vision monthly klines + checksums
            provider.py     BinanceUMProvider(MarketDataProvider) — PRICE HISTORY ONLY

### D2.1 Dependency direction (NORMATIVE, testable)

> `src/backtest/**` MUST NOT import `src/data/**`. `src/data/base.py`, `schemas.py`,
> `storage.py`, `validation.py`, `universe.py` and `provenance.py` MUST NOT import
> `src/data/hyperliquid/**` **or `src/data/binance/**`**. The two provider packages MUST NOT
> import each other — cross-venue logic lives in `symbol_map.py` and the validation layer, so that
> neither venue's quirks can leak into the other's normalization path.

A test MUST assert both by static import inspection (AST scan of the source tree, not a runtime
`sys.modules` check — a runtime check passes vacuously if the module was never imported).

### D2.2 `MarketDataProvider` ABC

Abstract methods, each returning the normalized shapes of D§4/D§5/D§6:

    get_universe(as_of: pd.Timestamp | None) -> UniverseSnapshot
    get_ohlcv(symbols, frequency, start, end) -> pd.DataFrame
    get_funding(symbols, start, end) -> pd.DataFrame
    get_funding_coverage(symbols, start, end) -> list[FundingCoverage]

`venue` is an abstract property. A future `BinanceProvider` MUST be implementable against this
ABC without changing it — but MUST NOT be written in QR-DATA-001.

---

## D3. Universal normalization rules

### D3.1 Time

1. Every timestamp is `pd.Timestamp`, **tz-aware UTC**. Naive timestamps are **rejected, never
   localized**. Non-UTC tz is converted to UTC and the original offset recorded.
2. OHLCV bars are **left-labelled and half-open**: bar `t` covers `[t, t+Δ)`, matching §2. The
   Hyperliquid `t` field is the bar open; `T` (`t + Δ - 1ms`) is discarded after asserting
   `T == t + Δ - 1ms`. A violation is a blocking data error.
3. Funding event timestamps are preserved at **native millisecond precision** and MUST NOT be
   rounded, floored or snapped to the hour. Jitter is real (F5) and snapping it would move events
   across the half-open period boundary of §7.5.

### D3.2 Ordering and determinism

Deterministic sort: OHLCV by `(symbol, timestamp)`, funding by `(symbol, timestamp)`, both
ascending, stable. Two runs over identical inputs MUST produce byte-identical parquet payload
content (D§8.4). Set/dict iteration order MUST NOT influence output.

### D3.3 Symbol identity

`symbol` is the Hyperliquid perp name, uppercase-exact as returned (`BTC`, `kPEPE`). Rules:

1. **Never** rewrite, alias, suffix or normalize a symbol name.
2. Symbol metadata MUST record `asset_index`, `sz_decimals`, `max_leverage`, `is_delisted`, and
   `unit_multiplier` (1000 for `k`-prefixed names, else 1).
3. Perp names MUST NOT be conflated with Hyperliquid spot names (`@N` / `TOKEN/USDC`). This layer
   handles **perps only**; a spot-style name reaching the perp path is a blocking error.
4. Duplicate names within one universe snapshot → `DataIntegrityError`.
5. If a name's `asset_index` differs between two snapshots, that is **asset index reuse** — a
   blocking finding, never auto-resolved.

### D3.4 Prohibited transformations

No forward-fill, back-fill, interpolation, resampling-to-fill, outlier clipping, or silent
de-duplication anywhere in the layer. Duplicates and gaps are **detected and reported**, never
repaired. Aggregation of native 1h into 4h/1d is permitted **only** under D§4.5.

---

## D4. OHLCV

### D4.1 Normalized schema

| column | dtype | notes |
|---|---|---|
| `timestamp` | datetime64[ns, UTC] | bar open, left-labelled |
| `symbol` | string | D§3.3 |
| `open` `high` `low` `close` | float64 | parsed from API strings |
| `volume` | float64 | base units |
| `trade_count` | int64 | API `n` |
| `native_traded` | bool | D§4.4 |
| `source_venue` | string | `"Hyperliquid"` in QR-DATA-001 |
| `native_or_proxy` | string | `"native"` in QR-DATA-001 |
| `source_type` | string | D§15.1 enum |
| `dataset_id` | string | the producing dataset (D§9.1) |

Column order and dtypes are fixed and asserted. The last four columns are **per-observation
source attribution** (D§15) and are mandatory on every row — they are what makes a future
mixed-provenance series auditable. They MUST NOT be dropped, defaulted or back-filled from a
frame-level constant when a frame spans more than one segment.

### D4.2 Pagination (F1)

Bounded-window pagination: request windows of at most `MAX_CANDLES_PER_REQ` bars, walking from
`end` backwards to `start`, until either `start` is reached or a window returns `[]`.

**Blocking requirements.**
1. MUST NOT issue a single unbounded `[start, now]` request and treat the response's first
   timestamp as the beginning of history.
2. A full response (`len == limit`) MUST be treated as **possibly truncated** and trigger a
   further request.
3. Windows MUST overlap by at least one bar; the overlap MUST be verified to agree bar-for-bar.
   **Disagreement across an overlap is a blocking error, not a merge conflict to resolve.**
4. Concatenation MUST de-duplicate on exact `(symbol, timestamp)` identity only after asserting
   that duplicated keys carry **identical OHLCV values**; unequal duplicates → `DataIntegrityError`.

### D4.3 Malformed OHLC

Blocking per-bar checks: all of `o,h,l,c` finite and `> 0`; `h >= max(o,c)`, `l <= min(o,c)`,
`h >= l`; `volume >= 0`, finite; `trade_count >= 0`. Violations are reported with symbol,
timestamp, field and value — never dropped silently.

### D4.4 Backfilled (non-native) bar quarantine — NORMATIVE (F3)

    native_traded[t] = not (volume[t] == 0 and trade_count[t] == 0)

Let `first_native[symbol]` = earliest `t` with `native_traded[t] == True`.

- Bars **before** `first_native` are **pre-listing backfill** and MUST be **excluded** from
  processed datasets. Their count and time range MUST be recorded in metadata and reported.
- Bars **at or after** `first_native` with `native_traded == False` are **genuine illiquid bars**
  and MUST be **retained**, flagged, and counted.

Rationale: a blanket zero-volume filter would punch holes in real illiquid 1h series; a
leading-run rule removes exactly the synthetic prefix F3 identified. `first_native` MUST be
computed from the **1d** series (most complete) and applied consistently across frequencies.

### D4.5 Derived 4h / 1d

Preference order: (1) native `candleSnapshot` at that interval; (2) aggregation from native 1h.
Aggregation is permitted only when **every** constituent 1h bar is present — a partial bucket MUST
NOT be emitted. Aggregation rule: `open`=first, `high`=max, `low`=min, `close`=last, `volume`=sum,
`trade_count`=sum, left-labelled bucket start. `source_type` MUST record `native` vs `aggregated`.
Given F2, native 4h/1d reach further back than 1h; native is expected to dominate.

### D4.6 Gaps and the engine's regular-grid requirement (§2.1 handoff)

§2.1 requires a perfectly regular grid; real venue data has holes. The layer therefore exposes
gaps explicitly and **never** fills them.

`to_engine_frame(df, frequency, policy=...)` returns the `MarketData`-ready open/close frames:

| policy | behaviour |
|---|---|
| `"raise"` (**default**) | any missing bar on the expected grid → `DataIntegrityError` naming the first offending pair |
| `"segment"` | returns an ordered list of maximal contiguous segments, each internally regular |
| `"reindex_nan"` | reindexes onto the full grid inserting `NaN` — explicit opt-in; the engine will then raise for any *active* symbol under §5.5, which is the intended outcome |

There is **no** `"ffill"` policy and one MUST NOT be added.

---

## D5. Funding

### D5.1 Normalized schema

| column | dtype | notes |
|---|---|---|
| `timestamp` | datetime64[ns, UTC] | native ms precision, unrounded (D§3.1.3) |
| `symbol` | string | |
| `funding_rate` | float64 | per-event realised decimal fraction, per §7.1 |
| `premium` | float64 | recorded, not consumed by the engine |
| `notional_price` | float64 | **always NaN/None in QR-DATA-001** (F4, D§5.5) |

`funding_rate` MUST be used exactly as returned: **not annualized, not rescaled, not multiplied by
a period count.** An hourly rate of one basis point is `0.0001` (§7.1). Sign convention is the
venue's; §7.3's `rate > 0 => longs pay shorts` is applied by the **engine**, and this layer MUST
NOT negate, abs, or re-sign anything.

### D5.2 Pagination (F4)

`fundingHistory` caps at 500 records. Walk forward with `startTime` advanced past the last received
event. Requirements mirroring D§4.2: a full response is possibly-truncated; overlaps verified;
duplicates removed only after asserting identical `(rate, premium)`; a repeated identical page
that fails to advance MUST raise rather than loop.

**Native event frequency is preserved.** Funding MUST NOT be collapsed, resampled or averaged to
4h/1d merely because bars are 4h/1d (§7 opening statement).

### D5.3 Coverage records

Produce `FundingCoverage` objects (§7.2) consumable **unchanged** by the frozen engine:
`{symbol, coverage_start, coverage_end, max_funding_gap, source_venue}` with
`source_venue = "Hyperliquid"`.

Per §7.2, records for a symbol MUST be **pairwise disjoint with non-intersecting closures**.
Records from separate fetches MUST be merged by an ordered linear pass **before** emission.

### D5.4 `max_funding_gap` — PINNED at 90 minutes (F5)

    MAX_FUNDING_GAP = pd.Timedelta(minutes=90)

Declared from the venue's documented hourly cadence plus a bounded jitter allowance. It is
**declared, never inferred from observed spacing** (§7.2: inferring cadence from the stream being
validated is circular).

Justification, and why the neighbouring choices are wrong:

- **1h exactly** → fails on benign sub-second and minute-scale jitter (observed spacings such as
  3600250 ms, and offsets to ~24 min).
- **90 min** → strictly below 2× cadence, so **any single missing hourly event** produces a gap
  > 90 min and is caught. Also avoids §7.7.2's soft flag, which fires only when modal spacing is
  more than 2× *below* the tolerance (60 min is not < 45 min), so the flag stays meaningful.
- **8h** (to swallow the 2023-05/06 era) → **PROHIBITED**. This is precisely §7.2's W7 residual
  risk: it would silently undercharge funding up to 8× and the engine would trust it.

**Where observed spacing exceeds 90 minutes, the coverage record MUST be SPLIT** into disjoint,
non-touching records. The consequence is intended and must not be engineered around: under
`funding_mode="required"` the engine will refuse to run across a genuine funding gap. Research must
either start after coverage becomes contiguous or knowingly set `funding_mode="disabled"` and say
so. Every segment boundary MUST appear in metadata and in the validation report.

### D5.5 `funding_notional_basis` (REVISED in v1.1 — v1.0 was wrong)

**v1.0 stated that `event_price` was unobtainable. That was incorrect.** It is unobtainable from
`fundingHistory` (F4), but `asset_ctxs` carries minute-resolution `oracle_px` back to 2023-05-20
(F12) — and oracle price is precisely the basis Hyperliquid funds on (§7.6).

Both bases are therefore supported, under strict labelling:

1. **`"period_start"`** — always available, needs no archive access, no AWS cost. Error bounded by
   `|rate| × (max intra-period price move)`; §7.6 records −6.98% misstatement on a +15% intra-day
   move. Not negligible.
2. **`"event_price"`** — `notional_price` populated by joining each funding event to the
   `asset_ctxs` `oracle_px` row at the **containing minute**, selected as the last row with
   `ctx_minute <= event_timestamp`. **Never interpolated, never forward-filled across a gap
   exceeding 2 minutes** — beyond that the event MUST be left unpriced, which under §7.6 makes the
   engine raise `FundingDataError` rather than silently mis-price.

**Honesty requirements for `event_price`.** This is a *minute-resolution reconstruction* of the
oracle price, not a record of the exact notional the venue used. It MUST be labelled
`source_type = "asset_ctxs_oracle_px"`, and research using it MUST state the ±1-minute resolution.
It is still `native_or_proxy = "native"` (official Hyperliquid archive data).

The layer MUST NOT fabricate `notional_price` from **candle closes** and pass it off as the oracle
price — that remains prohibited, and is a different thing from F12.

Default when the archive is unavailable or unauthorized: `"period_start"`, stated explicitly.

#### D5.5.1 Compact funding/oracle policy (AMENDMENT QR-DATA-001-B)

Accurate Hyperliquid funding matters because strategies execute on Hyperliquid. But the full
minute-resolution `asset_ctxs` archive MUST NOT be retained.

**Do not automatically download the full `asset_ctxs` archive.** The approved shape is a compact
processed artifact holding only what funding accounting actually needs:

    {timestamp, symbol, oracle_price}   at funding-event timestamps only

Measured economics (F18): the archive is 1169 files / 9.63 GB / 8.24 MB per day. There is no
server-side filter, so obtaining oracle prices requires streaming those objects **transiently**,
extracting only the funding-event minutes, and **discarding the raw immediately**. Estimated
processed result: **~50–100 MB** (≈3M events × ~20 B/row), against ~9.6 GB of transient egress
(≈ $0.90 at requester-pays rates).

Rules:
1. Raw `asset_ctxs` files MUST NOT be retained after extraction.
2. The transient download is a **separately authorized** step. Until authorized, the layer
   operates on `funding_notional_basis="period_start"` and says so in its provenance.
3. Extraction MUST stream per file; it MUST NOT materialize the whole archive.
4. The compact artifact carries `source_type = "asset_ctxs_oracle_px"` and `native_or_proxy =
   "native"`, and records the ±1-minute resolution caveat.
5. Funding **events** themselves come from the `fundingHistory` API and are compact already
   (~60 MB); they are unaffected by this rule and remain required.

### D5.6 Coverage truthfulness

Coverage MUST be derived from **actually retrieved events**, never from the requested window.
Declaring `[requested_start, requested_end]` when the fetch returned less is **false coverage** and
is the single most dangerous defect this layer can contain: it makes the engine believe funding was
charged over intervals where no events existed.

---

## D6. Universe and listings

### D6.1 Snapshot

`UniverseSnapshot`: `retrieved_at`, `venue`, and per symbol the D§3.3 metadata plus inferred
`first_native_bar` / `last_native_bar`.

The universe MUST include `isDelisted` assets (F6). Restricting to currently-live names is
**survivorship bias** and is prohibited.

### D6.2 Point-in-time membership — inferred, and labelled as such

    listed_at(symbol)   := first bar with native_traded == True   (1d granularity)
    delisted_at(symbol) := last  bar with native_traded == True, iff is_delisted

    member(symbol, t)   := listed_at <= t <= (delisted_at or +inf)

`meta` carries **no listing or delisting dates** (F6); these are **inferred from trading activity**,
not official venue records. That MUST be recorded, not glossed.

### D6.3 Universe provenance (pins §13.2)

    universe_source      = "hyperliquid.info.meta"
    universe_asof_policy = "point_in_time_inferred_from_first_last_native_trade"
    listing_data_source  = "inferred_from_candle_activity"
    survivorship_safe    = False

**`survivorship_safe = False` is mandatory and MUST NOT be set `True`.** Reason: although `meta`
retains delisted assets, we cannot demonstrate it retains *every* asset ever listed — an asset
removed from `meta` entirely would be invisible to us, and its absence is unobservable by
construction. §13.2 obligation 2 forbids defaulting this to `True`, and the engine surfaces
`False` in `__repr__`, which is the desired standing caveat. Making it `True` requires an official
Hyperliquid listing/delisting registry, which does not exist at freeze.

### D6.4 Renames are never spliced (F8, §11.4)

The layer MUST NOT map `MATIC`→`POL`, `RNDR`→`RENDER`, `FTM`→`S`, or any other rename. A delisted
name and a later-listed name are **distinct symbols**. The layer MAY record *candidate* rename
pairs in metadata as an advisory note; it MUST NOT act on them. Splicing two symbols' price history
manufactures a continuous series that never traded and is a survivorship-bias vector.

---

## D7. Missing-data disambiguation (NORMATIVE)

An empty or short response MUST be classified, and the classification recorded:

| classification | evidence |
|---|---|
| `NOT_YET_LISTED` | window entirely before `first_native_bar` |
| `DELISTED` | window entirely after `last_native_bar` **and** `is_delisted == True` |
| `BEYOND_RETENTION` | window before the earliest timestamp the venue returns for that symbol+interval, while later windows return data (F2) |
| `VENUE_GAP` | window inside `[first_native, last_native]`, adjacent windows populated, this one empty |
| `API_FAILURE` | transport error, non-200, malformed body, or schema violation |

`API_FAILURE` MUST NOT be silently coerced into any of the others. **An exception during fetch MUST
NOT produce an empty dataset that then reads as "not listed".** Retries are permitted with bounded
exponential backoff; exhausted retries raise.

---

## D8. Storage

### D8.1 Layout

    data/raw/hyperliquid/<endpoint>/<symbol>/<interval>/<window>.json.gz   verbatim responses
    data/processed/hyperliquid/ohlcv/<frequency>/<symbol>.parquet
    data/processed/hyperliquid/funding/<symbol>.parquet
    data/metadata/hyperliquid/<dataset_id>.json                            provenance + reports

Raw responses are stored **verbatim, uninterpreted** so any normalization defect is re-derivable
after the fact.

### D8.2 Git exclusion (blocking)

`.gitignore` MUST exclude `data/raw/`, `data/processed/` and downloaded payloads. Metadata and
provenance JSON MAY be committed. A test MUST assert via `git check-ignore` that a representative
raw and processed path is ignored. **No market-data download may ever be committed.**

### D8.3 Offline reload (blocking)

Cached datasets MUST reload with **no network access whatsoever**. A test MUST prove this by
monkeypatching the HTTP transport to raise on any call, then loading a cached dataset
successfully. A provider constructed in offline mode MUST NOT open a socket.

### D8.4 Determinism

Writing the same normalized frame twice MUST produce identical content. Parquet metadata that
embeds a wall-clock timestamp MUST be excluded from the comparison; the comparison MUST be over
the deserialized frame *and* a stable hash of the value payload.

---

## D9. Provenance

### D9.1 Per-dataset (extends §13.1)

Every processed dataset carries, at minimum:

    dataset_id, source_venue="Hyperliquid", source_type ("ohlcv"|"funding_rate"),
    native_or_proxy="native", retrieved_at, start_timestamp, end_timestamp,
    symbols, frequency (OHLCV only), processing_version

Plus, for this layer: `endpoint`, `request_windows`, `api_response_count`, `code_version`
(git SHA if available), `excluded_backfill_bars`, `coverage_segments`.

`native_or_proxy` MUST be `"native"` throughout QR-DATA-001; `"proxy"` MUST NOT appear. It MUST be
emittable as a `DatasetProvenance` (§13.1) accepted by the engine unchanged.

### D9.2 Provenance is not optional

A processed dataset written without complete provenance is a defect. Loading a dataset whose
provenance sidecar is missing or whose `processing_version` does not match the running code MUST
warn loudly (and MUST NOT silently proceed as if current).

---

## D10. Validation

`validate_ohlcv(...)` / `validate_funding(...)` return a `ValidationReport`:
`{severity, code, symbol, timestamp, detail}` per finding, plus counts and an overall
`ok / warnings / failed`. A report is **data**, never a print statement.

Mandatory checks — OHLCV: duplicate `(symbol,timestamp)`; non-monotonic timestamps; naive/non-UTC
timestamps; grid gaps (per D§4.6); malformed OHLC (D§4.3); non-finite or non-positive prices;
negative volume; duplicate/colliding symbols; backfill prefix present (D§4.4); aggregated-vs-native
provenance mismatch.

Funding: duplicate `(symbol,timestamp)`; non-monotonic; naive/non-UTC; spacing > `MAX_FUNDING_GAP`;
coverage segment boundaries; coverage claimed beyond retrieved events (D§5.6); rate non-finite;
`|rate|` implausibly large (advisory, ≥ 1% per hour); events outside any coverage record.

Universe: duplicate names; asset-index reuse (D§3.3.5); symbol in data but absent from universe;
`survivorship_safe != False`.

---

## D11. Testing

### D11.1 Unit tests — deterministic, offline

All unit tests MUST use **recorded/mocked responses** and MUST NOT touch the network. A test MUST
assert that the mocked transport is never bypassed. Fixtures MUST include the adversarial shapes:
truncated pages, a full page exactly equal to the limit, an overlap that disagrees, a duplicate
with differing values, unsorted responses, a naive timestamp, a zero-volume prefix, an interior
zero-volume bar, an 8h funding gap, jittered funding timestamps, a delisted symbol, an empty
response, and a malformed body.

### D11.2 Mutation proof — MANDATORY, non-negotiable

Passing test counts are not evidence. For **every** mandatory behaviour below, the implementer MUST
mutate the source, confirm the target test goes **RED**, restore, verify a clean diff, and report a
mutation table `(mutation, file:line, test, RED?)`.

The acceptance criterion for every assertion is **"does this discriminate?"**, not "does this pass?"

Required mutations, quoted so implementer and auditor results are comparable:

| M | Mutation | Must turn RED |
|---|---|---|
| M1 | Replace backwards-walking pagination with a single unbounded request | D§4.2 truncation test |
| M2 | Delete the `native_traded` leading-run exclusion (D§4.4) | backfill-quarantine test |
| M3 | Change the D§4.4 rule to drop **all** zero-volume bars | interior-illiquid-bar retention test |
| M4 | Insert `.ffill()` into OHLCV normalization | no-forward-fill test |
| M5 | Change de-duplication to `drop_duplicates()` without the equality assertion | unequal-duplicate test |
| M6 | Widen `MAX_FUNDING_GAP` to 8h | coverage-splitting test |
| M7 | Round funding timestamps to the hour | ms-precision / boundary test |
| M8 | Declare coverage as `[requested_start, requested_end]` (D§5.6) | false-coverage test |
| M9 | Emit touching coverage records instead of merging | §7.2 disjointness test |
| M10 | Filter the universe to `is_delisted == False` | survivorship test |
| M11 | Set `survivorship_safe = True` | D§6.3 test |
| M12 | Map `MATIC`→`POL` in symbol resolution | D§6.4 no-splice test |
| M13 | Swallow a transport exception and return `[]` | D§7 `API_FAILURE` test |
| M14 | Localize a naive timestamp instead of rejecting it | D§3.1.1 test |
| M15 | Emit a partial 4h bucket from incomplete 1h bars | D§4.5 test |
| M16 | Add `src/backtest` → `src/data` import | D§2.1 layering test |
| M17 | Drop `processing_version` from provenance | D§9 test |
| M18 | Multiply `funding_rate` by 8 (rescale) | D§5.1 rate-passthrough test |
| M19 | Aggregate fills without reducing to trades (D§14.3) | 2×-volume overlap test |
| M20 | Deduplicate archive records on `hash` alone (D§14.4) | dedup-collision test |
| M21 | Promote quote-derived (`mid_px` / L2) bars into OHLCV (D§14.7) | rejected-source test |
| M22 | Let reconstructed bars win over official candles in overlap (D§14.1) | source-priority test |
| M23 | Drop the per-row `source_type` / `dataset_id` columns (D§4.1) | row-level attribution test |
| M24 | Make a segment manifest disagree with row-level `source_*` (D§15.2.2) | manifest-agreement test |
| M25 | Allow two segments to overlap, or imply the transition instead of declaring it | D§15.2.1 test |
| M26 | Permit `external_proxy` emission (D§15.2.5) | proxy-refusal test |
| M27 | Forward-fill `oracle_px` across a >2-minute gap (D§5.5) | unpriced-event test |
| M28 | Treat a missing archive **hour** as present (D§14.4) | per-hour coverage test |
| M29 | Label a Binance row `native_or_proxy="native"` or `source_venue="hyperliquid"` | proxy-labelling test |
| M30 | Let the Binance provider emit funding data (D§16.1) | funding-source-isolation test |
| M31 | Assume a CSV header is always present (and always absent) (F17) | header-sniff test, both eras |
| M32 | Skip / soften SHA-256 checksum verification (D§16.6.1) | corrupt-archive test |
| M33 | Substitute Binance **Spot** when no USDⓈ-M perp exists (D§16.2) | no-spot-substitution test |
| M34 | Fabricate bars before the Binance contract onboarded (D§16.2) | no-pre-listing-fabrication test |
| M35 | Auto-map a rename (`MATIC`→`POLUSDT`) in the mapping table (D§16.3.3) | rename-mapping test |
| M36 | Drop the `k`/`1000` multiplier so levels differ ~1000× (D§16.3.4) | unit-equivalence test (**must fail, not warn**) |
| M37 | Store 4h/1d as duplicate historical datasets instead of deriving (D§16.4) | single-canonical-frequency test |
| M38 | Retain raw ZIP/CSV after verification (D§16.4) | raw-cleanup test |
| M39 | Skip the >5 GB pre-download gate (D§16.5) | gate test with an inflated estimate |
| M40 | Splice Binance→Hyperliquid mid-symbol without flagging the seam (D§16.7) | seam-flag test |
| M41 | Retain full `asset_ctxs` raw after extraction (D§5.5.1) | compact-oracle test |

Any mutation that **survives** is a finding of the same severity as a wrong number.

### D11.3 Live integration validation (network-gated, separately marked)

Marked `@pytest.mark.integration`, skipped by default, never required by the unit suite.
For **BTC** and **ETH**, report:

- **1h OHLCV**: first timestamp, last timestamp, bar count, gaps, duplicates, timezone
- **Funding**: first event, last event, event count, spacing statistics (min/median/max/modal),
  detected coverage gaps and resulting segments
- **4h and 1d**: demonstrate generation and offline reload

---

## D14. Archival OHLCV reconstruction (A, **demoted by B**)

### D14.0 Status: VALIDATION-ONLY (AMENDMENT QR-DATA-001-B)

**The full Hyperliquid `node_trades` / `node_fills` / `node_fills_by_block` backfill is
CANCELLED and MUST NOT be performed.** ~100 GB of raw trade/fill archive is not part of the local
research dataset.

What survives, and why:

1. Reconstruction **code and design** are retained for bounded validation of Hyperliquid data
   semantics and as an option for a future high-fidelity dataset.
2. Reconstructed Hyperliquid trade candles are **NOT a prerequisite for normal research** and MUST
   NOT appear on any default research path.
3. Bounded samples (a few days per era, BTC/ETH) remain permitted for D§14.5 overlap validation.
4. D§14.6's cost controls remain in force; D§14.6.4's "requires explicit sign-off" is now a
   standing **NO**.

The rest of D§14 is therefore read as governing the *validation* path only. It is retained rather
than deleted because the fills-vs-trades double-counting trap (D§14.3) and the `hash`
non-uniqueness trap (D§14.4) still apply to any bounded sample anyone draws later.

### D14.1 Source priority for Hyperliquid-native OHLCV

    A. candleSnapshot bars that are genuinely traded (native_traded, D§4.4)
    B. OHLCV reconstructed from official executed-trade/fill archives, where A is unavailable
    C. nothing

Pre-trading synthetic candle history (F3) is **never** promoted into A merely because
`candleSnapshot` returns it. Quote-derived series (F13) are **never** promoted into B.

Where A and B both exist for an interval, **A wins** and B is retained only for validation.

### D14.2 Which archive applies to which era

| era | archive | record grain |
|---|---|---|
| 2025-03-22 → 2025-06-21 (sparse) | `node_trades/hourly` | one record **per trade** |
| 2025-05-25 → 2025-07-27 | `node_fills/hourly` | one record **per fill** |
| 2025-07-27 → present | `node_fills_by_block/hourly` | one record **per fill** |

### D14.3 Fills are not trades — the double-counting trap (BLOCKING)

A trade generates **two fills** (maker and taker). Summing `sz` over fills **doubles volume** and
doubles trade count relative to a `node_trades`-derived or `candleSnapshot` bar.

The reconstruction MUST therefore reduce fills to trades before aggregating — pairing counterparty
fills, or equivalently counting one side only — and MUST prove it did so by the D§14.5 overlap
test. An implementation that silently produces 2× volume in the fills era and 1× in the
`node_trades` era manufactures a **regime break in the volume series at the source seam**, which
would corrupt any volume-, liquidity- or turnover-conditioned signal while looking entirely
plausible. This is the single most dangerous defect available in this amendment.

### D14.4 Deduplication (BLOCKING)

`hash` is a transaction hash spanning multiple trades (F11) and MUST NOT be used alone as a trade
identity. Deduplication keys MUST be proven unique on real sampled data before use, and any
collision that is not a byte-identical duplicate record MUST raise rather than be collapsed.

Hourly archive files MUST be checked for boundary duplication (a trade appearing in two adjacent
hour files) and for missing hours — coverage MUST be verified **per hour**, not per day, because
`node_fills_by_block` is streamed from a non-validating node whose downtime would appear as a
silent hole.

### D14.5 Overlap validation against official candles (BLOCKING — reconstruction is not
accepted without it)

Reconstruction MUST NOT be treated as equivalent to official candles until compared, for **BTC and
ETH at minimum**, on `open`, `high`, `low`, `close`, `volume`, bar count and gaps.

Available overlaps — note each archive era has one:

| archive era | validate against | why it works |
|---|---|---|
| `node_fills_by_block` (2025-07-27→) | **1h** candleSnapshot (2026-01-20→) | direct 1h overlap |
| `node_trades` (2025-03-22→2025-06-21) | **4h** (2024-05-05→) and **1d** candles | no 1h candles that far back, but 4h/1d do reach it |
| `node_fills` (2025-05-25→2025-07-27) | **4h** and **1d** candles | same |

Every discrepancy MUST be explained, not tolerated. Expected-and-acceptable causes: trades exactly
on a bar boundary; venue-side candle construction differences. Unacceptable causes, which are
defects: a ~2× volume ratio (D§14.3), systematic OHLC offsets, missing hours, timezone shifts.
Report per-field agreement rates and the distribution of relative differences — **not** a single
pass/fail.

### D14.6 Cost control (Requester Pays — F9)

Egress is billed to our AWS account, so:

1. The implementer MUST NOT bulk-download an entire archive prefix during development.
2. Validation work MUST use a **bounded sample** — a few days per era for BTC/ETH.
3. Measured per-file sizes MUST be reported and extrapolated to a full-backfill estimate
   **before** any full backfill is run.
4. A full historical backfill is **NOT authorized by this amendment** and requires explicit
   sign-off from the user with the cost estimate in hand.
5. Every downloaded object MUST be cached to `data/raw/` and never re-fetched (D§8).

### D14.7 Rejected sources, with reason

- **L2 book snapshots** (`market_data/`, 2023-04-15→) and **`asset_ctxs.mid_px`** (2023-05-20→):
  quote data. No traded volume, no trade count. Rejected for OHLCV per D§14.1. They reach much
  further back than any trade archive, and that is exactly why the temptation must be refused in
  writing: splicing quote-derived OHLC onto traded OHLC changes the price-formation process
  mid-series without changing the column names. `asset_ctxs` remains approved for `oracle_px`
  (D§5.5) — a different use, and one that is honestly labelled.

### D14.8 Unrecoverable natively

No trade-level archive exists before **2025-03-22** (F10). Therefore 1h traded OHLCV before that
date is **not natively recoverable** and MUST be reported as such. 4h and 1d remain available from
`candleSnapshot` further back (2024-05-05 and 2023-02-26 respectively, post-quarantine). Filling
the pre-2025-03-22 1h gap would require a proxy venue, which is out of scope here (D§15).

---

## D15. Source segmentation and mixed-provenance series (AMENDMENT QR-DATA-001-A §5)

The final research system will need a Binance proxy prefix before native history exists. Binance
MUST NOT be implemented now. The **schema** must nonetheless support it now, so that adding it
later cannot silently blend venues.

### D15.1 `source_type` enum

    "hyperliquid_candle"        candleSnapshot, genuinely traded
    "hyperliquid_node_trades"   reconstructed from node_trades
    "hyperliquid_node_fills"    reconstructed from node_fills / node_fills_by_block
    "asset_ctxs_oracle_px"      oracle price for funding (D§5.5), not OHLCV
    "external_proxy"            RESERVED — must not be produced in QR-DATA-001

### D15.2 Segment manifest

Every dataset spanning more than one source carries an ordered `segments` manifest:

    {source_venue, native_or_proxy, source_type, dataset_id, start_timestamp, end_timestamp}

Rules:
1. Segments are **contiguous and non-overlapping**; the **transition timestamp is explicit**, never
   implied by ordering or inferred by the reader.
2. Every observation's own `source_*` columns MUST agree with the segment covering it. A test MUST
   verify row-level and manifest-level agreement (they are two independent records of the same
   fact, and disagreement means one is a lie).
3. A frame containing any `native_or_proxy == "proxy"` row MUST set `uses_proxy_data` on the
   emitted `DatasetProvenance`, which §13.1 obligation 3 then surfaces in the engine's `__repr__`.
4. Mixing venues within a **single bar** is prohibited outright. Splicing happens only at bar
   boundaries, at a declared transition timestamp.
5. QR-DATA-001 MUST refuse to emit `external_proxy`. The path exists to be tested, not used: a test
   MUST assert that attempting to emit it raises.

---

## D16. Canonical price dataset (AMENDMENT QR-DATA-001-B)

### D16.1 Role separation — NORMATIVE

| concern | authoritative source |
|---|---|
| price / signal history | **Binance USDⓈ-M perpetual 1h** (`proxy`) |
| execution venue | **Hyperliquid** |
| funding | **Hyperliquid** (native) |
| universe / listing / delisting | **Hyperliquid** (native) |
| execution costs, spreads, liquidity, capacity | **Hyperliquid** (native) |

**Binance venue-specific fields MUST NEVER be used as Hyperliquid execution fields.** Specifically
prohibited: Binance funding as Hyperliquid funding cost; Binance spreads/depth/volume as
Hyperliquid execution spreads, liquidity or capacity. This restates CLAUDE.md's data-source policy
and is blocking.

Binance `volume`, `trade_count` and `quote_volume` are retained in the schema as **Binance**
observations. They MUST NOT be used to size Hyperliquid execution capacity. A test MUST assert the
Binance provider cannot emit a funding dataset at all.

### D16.2 Per-symbol coverage rule

    IF a corresponding Binance USDⓈ-M perpetual exists (F14):
        use Binance 1h from that contract's first genuine trading candle
    ELSE:
        retain Hyperliquid-native 1h for whatever history exists,
        and mark earlier history explicitly UNAVAILABLE

Binance **Spot** MUST NOT be substituted automatically. History before the Binance contract existed
MUST NOT be fabricated by any means.

### D16.3 Symbol mapping is an explicit reviewed table, never a heuristic

The `NAME→NAMEUSDT` / `kNAME→1000NAMEUSDT` rule (F14) is the *generator* of a **checked-in mapping
table**, not a runtime guess. Rules:

1. The table is data, reviewable in one place, with each entry's status recorded.
2. The 22 unmatched names (F14) are recorded as unmatched with a reason. They MUST NOT be silently
   dropped from the universe — a symbol with no proxy history is a symbol with short history, not
   a symbol that does not exist.
3. **Rename chains stay unmapped.** `MATIC`→Binance `POLUSDT` and `RNDR`→`RENDERUSDT` are NOT
   auto-mapped, consistent with D§6.4. Adding such a mapping is a reviewed decision, never a
   heuristic match.
4. The `k`/`1000` unit equivalence MUST be **verified, not assumed** — D§17 price-level comparison
   catches a unit error as an ~1000× discrepancy, and a mapping whose price levels disagree by
   orders of magnitude MUST fail rather than warn.

### D16.4 Storage policy — one canonical frequency

**1h is the single canonical stored historical frequency.** 4h and 1d are **derived on demand**
from the canonical 1h series using D§4.5's aggregation rule (complete buckets only). Duplicate
stored 4h/1d historical datasets MUST NOT be created without a demonstrated, recorded reason.

Processed research data uses **compressed Parquet** (zstd).

Raw downloads (ZIP/CSV/JSON) MUST NOT be retained after all three of:
(a) checksum/integrity validation, (b) normalization, (c) processed-data verification —
unless a specific reproducibility need is recorded. Note this **narrows** D§8.1's
"store raw verbatim" for bulk Binance history; the reproducibility substitute is the checksum
manifest of D§16.6, which pins exactly which upstream bytes produced each processed file.

### D16.5 Pre-download gate — SATISFIED

Before any bulk download the layer MUST report: matched symbol count, expected 1h row count,
estimated compressed Parquet size, estimated raw download size. **If the estimated processed
dataset exceeds 5 GB, STOP and report before downloading.**

Measured at freeze (F14–F16): 210 symbols, 5,606,643 rows, **0.276 GB processed**, 0.293 GB
transient download. This is **below the gate**, so the Binance download is authorized. The
implementation MUST still recompute and re-check the gate at runtime rather than trusting this
paragraph — if its own estimate exceeds 5 GB it MUST stop.

### D16.6 Integrity of bulk download

1. Every monthly archive's SHA-256 MUST be verified against its `.CHECKSUM` (F17). A mismatch is
   fatal for that file — never a warning, never a retry-and-accept.
2. **The CSV header MUST be sniffed per file** (F17): older files have no header row. Assuming a
   header where none exists discards a real bar; assuming none where one exists parses the literal
   string `open_time` as a timestamp. Both are silent off-by-one corruptions of the whole file.
3. A checksum manifest `{symbol, month, url, sha256, rows, first_ts, last_ts}` MUST be persisted
   and is what makes raw deletion (D§16.4) safe.
4. Monthly boundaries MUST be checked for duplicate and missing bars.
5. Binance klines MUST be verified left-labelled `open_time` on the exact 1h grid, tz-aware UTC.

### D16.7 No intra-symbol venue splicing by default

For a matched symbol, Binance covers the **entire** research window, so the canonical series is
**single-source per symbol** and no Binance→Hyperliquid seam is created.

This is deliberate. A spliced series computes one return across the seam as
`HL_close / Binance_close - 1`, which is a **cross-venue artifact, not a market return**, and it
lands at exactly the moment (Hyperliquid listing) that a listing-effect study would examine.

Splicing therefore requires explicit opt-in, and when enabled the seam bar MUST be flagged so the
seam return can be excluded. The D§15 segment machinery remains fully implemented and tested for
this case and for future proxies.

---

## D17. Cross-venue proxy validation (AMENDMENT QR-DATA-001-B)

**Purpose: establish _when_ Binance prices are a defensible proxy for Hyperliquid-executed
research — not to show the two are identical.** Numerical identity is NOT required and MUST NOT be
asserted.

### D17.1 Scope

BTC and ETH, plus a representative sample of liquid altcoins (≥ 8, selected by Hyperliquid volume
and recorded before results are computed, so the sample is not chosen after seeing outcomes).

### D17.2 Metrics, at 1h on the overlapping window

- close-to-close **return correlation**
- **mean absolute difference** in returns
- **percentile distribution** of return differences (1, 5, 25, 50, 75, 95, 99)
- **volatility** of each series, and their ratio
- **OHLC discrepancies** (per-field relative difference distribution)
- **large-discrepancy events**: enumerated with timestamps, not just counted

### D17.3 Conditioning

Behaviour MUST be reported separately around: Binance listing; Hyperliquid listing where known
(D§6.2's inferred `listed_at`); and high-volatility periods (top decile of realised vol), since
proxy quality is expected to be worst exactly where it matters most for execution.

### D17.4 Escalation rule

Escalate **only** if the analysis indicates Binance prices are **not** a reasonable proxy for an
important part of the intended universe. Ordinary microstructure divergence is expected, is not a
defect, and MUST NOT be escalated.

An `~1000×` level discrepancy is **not** a proxy finding — it is a `k`/`1000` mapping error
(D§16.3.4) and MUST be raised as a mapping defect.

---

## D12. Open decisions deferred (recorded, not resolved)

1. **1h native depth.** `candleSnapshot` gives ~208 days; archival reconstruction (D§14) extends
   this to **2025-03-22**. Before that, 1h traded OHLCV is **not natively recoverable** (D§14.8).
   Closing it requires a labelled external proxy — out of scope here, schema-ready per D§15.
2. ~~`event_price` unavailable~~ — **RESOLVED in v1.1**: available via `asset_ctxs` `oracle_px`
   at minute resolution (F12, D§5.5).
3. **Pre-hourly funding era (2023-05/06)** will be coverage-fragmented by D§5.4. Accepted.
4. **Rename resolution (D§6.4)** stays manual and advisory.
5. **Full archival backfill is CANCELLED** (D§14.0, amendment B). Bounded validation samples only.
6. **`node_trades` era is sparse** (66 day-dirs / 92 days, F10). Whether the missing days are
   venue-side absence or upload failure is unknown and MUST be reported, not smoothed.
7. **22 Hyperliquid assets have no Binance proxy** (F14) and are limited to native history. Whether
   to hand-map the rename cases (`MATIC`→`POLUSDT`, `RNDR`→`RENDERUSDT`) is deferred and requires
   a reviewed decision (D§16.3.3).
8. **`asset_ctxs` oracle extraction is not yet authorized** (D§5.5.1). Default remains
   `period_start`.
9. **Binance-priced research carries a venue caveat**: PnL is computed from Binance prices while
   execution is intended on Hyperliquid. D§17 quantifies when that is defensible; the caveat MUST
   survive into research outputs via provenance (§13.1 obligation 3 sets `uses_proxy_data`).

---

## D13. Verdict vocabulary

The independent data audit returns exactly one of:
`DATA PASS` / `DATA PASS WITH WARNINGS` / `DATA FAIL`.
