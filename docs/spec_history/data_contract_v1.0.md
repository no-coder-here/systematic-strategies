# Data Layer Contract — SPECIFICATION v1.0 (QR-DATA-001)

**Status: FROZEN** (2026-08-16, Research Lead)
**Scope: native Hyperliquid market data only.** No Binance/Bybit/OKX providers.
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
        hyperliquid/
            __init__.py
            client.py       thin HTTP client: endpoints, pagination, retry, rate limit
            provider.py     HyperliquidProvider(MarketDataProvider)

### D2.1 Dependency direction (NORMATIVE, testable)

> `src/backtest/**` MUST NOT import `src/data/**`. `src/data/base.py`, `schemas.py`,
> `storage.py`, `validation.py`, `universe.py` and `provenance.py` MUST NOT import
> `src/data/hyperliquid/**`.

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

Column order and dtypes are fixed and asserted.

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

### D5.5 `funding_notional_basis` — only `"period_start"` is supported

`fundingHistory` carries no price (F4) and Hyperliquid exposes no historical oracle-price endpoint,
so `notional_price` cannot be populated natively. Therefore:

- QR-DATA-001 supports `funding_notional_basis = "period_start"` only.
- The layer MUST NOT fabricate `notional_price` from candle closes and pass it off as the venue's
  oracle price — that would be proxy data mislabelled as native.
- This is a **material accepted limitation**: §7.6 bounds the error by
  `|rate| × (max intra-period price move)` and records −6.98% misstatement on a +15% intra-day
  move. It MUST be stated in every research output using this data.

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

Any mutation that **survives** is a finding of the same severity as a wrong number.

### D11.3 Live integration validation (network-gated, separately marked)

Marked `@pytest.mark.integration`, skipped by default, never required by the unit suite.
For **BTC** and **ETH**, report:

- **1h OHLCV**: first timestamp, last timestamp, bar count, gaps, duplicates, timezone
- **Funding**: first event, last event, event count, spacing statistics (min/median/max/modal),
  detected coverage gaps and resulting segments
- **4h and 1d**: demonstrate generation and offline reload

---

## D12. Open decisions deferred (recorded, not resolved)

1. **1h native depth ~208 days (F2)** materially constrains hourly research. Resolving it needs
   either continuous forward archiving or an external proxy — the latter is explicitly out of
   scope here and would require an approved provenance-labelled proxy path.
2. **`event_price` funding basis** is unavailable natively (D§5.5). Revisit only if Hyperliquid
   exposes historical oracle prices.
3. **Pre-hourly funding era (2023-05/06)** will be coverage-fragmented by D§5.4. Accepted.
4. **Rename resolution (D§6.4)** stays manual and advisory.

---

## D13. Verdict vocabulary

The independent data audit returns exactly one of:
`DATA PASS` / `DATA PASS WITH WARNINGS` / `DATA FAIL`.
