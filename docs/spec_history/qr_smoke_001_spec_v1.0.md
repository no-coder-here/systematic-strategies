# QR-SMOKE-001 — End-to-End Pipeline Smoke Test SPECIFICATION

**Status: v1.0 FROZEN (2026-08-16).**
**Depends on:** `docs/backtest_contract.md` v1.5.1 FROZEN, `docs/data_contract.md` v1.4 FROZEN.
**Both dependencies are FROZEN and MUST NOT be edited by this work order.**

This document may not be edited in place once frozen. Any change requires a new numbered
revision, a snapshot in `docs/spec_history/`, and a fresh audit.

## Revision history

| rev | date | cause |
|---|---|---|
| v0.1 | 2026-08-16 | initial draft |
| v1.0 | 2026-08-16 | SPEC FAIL audit: BD1–BD9 blocking, W1–W22 warnings, all adjudicated below. Windows filled from DATA PASS audit. |

### v0.1 → v1.0 changes (all traceable to the spec audit)

- **BD1** §4.2 invariance boundary was off by one and would have gone RED on a *correct* engine
  (period `k` legitimately earns `P[k+1]`). Rewritten to mutate index `>= k+2` per contract §18.1 E4.
- **BD2** §4.2 now mutates funding events as well as prices.
- **BD3** §4.1 no longer re-enumerates §17's EXACT permissions; those are scoped to comparisons
  *within one run* and are invalid across two implementations.
- **BD4** added §4.1.1, a per-field EXACT/TOLERANCE classification table.
- **BD5** §4.1's field list extended to cover contract §6.0 Steps 0–12; the decomposition
  identity (D) — the engine's primary accounting assertion — is now mandatory on every period.
- **BD6** added §4.7 warm-up boundary test; M4 previously pointed at a test that did not exist.
- **BD7** M8 (`>` → `>=`) was vacuous on real data; replaced, plus a general vacuity rule (§4.3.1).
- **BD8** §4.1 reconstruction independence tightened (may not import the signal module or the
  harness price-frame construction).
- **BD9** `max_gross_leverage` 1.0 → 1.05. Measured: 1.0 breaches on 31/199 periods at zero cost
  and zero funding, from the 1-ulp `q→w→q` round trip.
- **W1** §1.3 rationale 2 restated as measured fact (0.2% cost difference), not assertion.
- **W5/W6** Window B split into B1/B2 (§2.2); cross-venue run choices pinned (§4.4).
- **W19** determinism comparison method pinned; `BacktestResult` is `eq=False`.
- remaining warnings folded into §2.2, §3, §4.3–§4.8, §6.

---

## 0. Purpose and non-goals

Prove the **complete pipeline** executes with correct timing and correct accounting on real data:

    market data -> signal -> target weights -> QR-INFRA-001 engine -> execution
    -> fees/slippage -> Hyperliquid funding -> NAV / returns

**This is not alpha research.** The strategy is deliberately trivial and is expected to be
unprofitable. Profitability is irrelevant and MUST NOT be reported as a finding.

Out of scope, and a defect if performed: optimizing/tuning/selecting any parameter (the `100`
in SMA(100) is fixed by this spec); comparing parameter variants; searching for a profitable
configuration; any change to either frozen contract; starting QR-INFRA-002 or alpha research.

---

## 1. Strategy definition (FROZEN — no variants permitted)

Single symbol **BTC**, frequency **1h**, long-or-flat.

### 1.1 Signal

    SMA100[t] = mean( close[t-99], ..., close[t] )    # 100 COMPLETED bars, inclusive of t
    signal[t] = close[t] > SMA100[t]                   # strict inequality

`min_periods = 100`. A partial-window SMA is a defect. No backfill, no fudge.

Bars are LEFT-LABELLED and half-open (contract §2): bar `t` covers `[t, t+Δ)` and is COMPLETE
only at `t + Δ`. So `close[t]`, `SMA100[t]` and `signal[t]` are not known until `t + Δ` — the
anti-lookahead rule of §4.1. No bar strictly after `t` may enter `signal[t]` by any path.

**Disclosure (contract §4.5, W16).** Under `next_open` with contiguous bars the information
instant (`close[t]`) and the execution instant (`open[t+1]`) are the *same instant*. Contract
§4.5 states this as an **assumption, not a proof of lookahead-freedom**: it grants zero
decision, transmission and matching latency. This MUST be disclosed in the final report. This
spec does not claim the design is latency-realistic, only that it is contract-conformant.

### 1.2 Target weight

    target_weight[t, "BTC"] = 1.0 if signal[t] else 0.0

No shorting, no leverage above 1x nominal, no scaling, no volatility targeting.

### 1.3 Rebalance mask — PINNED DECISION

Use `StrategyOutput.rebalance_on_change(weights)`.

**Rationale.** Contract §3 forbids the *engine* from inferring rebalance intent from consecutive
weights and offers `rebalance_on_change` as an explicit opt-in *strategy-layer* constructor.
It is chosen over `rebalance_every_bar` because it yields genuinely cost-free held periods, so
the work order's "held position" and "period containing transaction costs" verification cases
are distinguishable rather than collapsed into one.

*Measured supporting fact (not an assertion — verified against the real engine over 199 hourly
periods at `|rate| = 1e-5`):* `rebalance_every_bar` produces total turnover `1.00197` versus
`on_change`'s `1.000000`, i.e. fees `450.90` vs `450.00` — a **0.2%** difference arising from
funding-induced drift. Algebraically, for a single fully-invested asset `NAV_end[i] = q·P[i+1]`
and `w_pre[i+1] = 1.0`, so turnover is zero absent funding and costs. **This holds
algebraically, not bitwise** (IEEE754), and MUST NOT be asserted with `==` except where §4.1.1
classifies it EXACT.

`rebalance_on_change` emits `True` on the first bar of the frame unconditionally. The signal
value at that bar is **not known in advance** and MUST NOT be assumed (W2): the implementation
must handle both branches, and the actual first-bar signal value MUST be reported. If the
target there is `0.0` with `q_prev = 0`, contract §6.0 Step 1's zero branch applies and turnover
is literal `0.0`; if it is `1.0`, a real entry trade occurs at the first execution point.

### 1.4 Strategy interface obligations

The strategy MUST return only `StrategyOutput(target_weights, rebalance_mask)` via the existing
platform interface. It MUST NOT compute PnL, NAV, equity, returns, fees, slippage or funding,
and MUST NOT contain strategy-specific accounting (CLAUDE.md; contract §1).

---

## 2. Data and windows

### 2.1 Sources (never spliced)

| role | dataset | venue | native_or_proxy |
|---|---|---|---|
| long-history price | Binance USDⓈ-M BTC 1h OHLCV | Binance | **proxy** (`proxy_for` = Hyperliquid BTC perp) |
| bounded-window native price | Hyperliquid BTC 1h OHLCV | Hyperliquid | native |
| funding | Hyperliquid BTC funding history | Hyperliquid | native |

`target_execution_venue = Hyperliquid` in all runs.

**Binance and Hyperliquid candles MUST NEVER be spliced, concatenated, averaged, gap-filled from
one another, or merged.** Separate datasets, separate runs.

Both venues label bars from the bar-**open** millisecond (verified: `src/data/binance/provider.py`
`open_time`; `src/data/hyperliquid/provider.py` `t`), consistent with contract §2.

### 2.2 Windows (PINNED from the QR-SMOKE-001 data audit, DATA PASS 2026-08-16)

**Reproducibility constraint (mandatory).** Hyperliquid `candleSnapshot` serves a **rolling
~208-day window** (5000 bars), not fixed history; at audit time it began 2026-01-20 11:00Z,
leaving only 9 hours of buffer before the warm-up start. **All runs MUST read the persisted
snapshot under `data/` offline (`HyperliquidProvider(offline=True, ...)`) and MUST NOT re-fetch.**
A re-fetch silently moves the start date forward and invalidates the warm-up. Determinism claims
are void if any run re-fetches HL candles.

**Window A — bounded validation window (also Window C, the cross-venue overlap)**

| | |
|---|---|
| SMA warm-up | `2026-01-20 20:00:00Z` → `2026-01-24 23:00:00Z` (100 bars, exact) |
| evaluated frame | `2026-01-25 00:00:00Z` → `2026-07-31 23:00:00Z` (4512 bars) |

Upper bound set by the Binance ingest ceiling; comfortably inside HL OHLCV and HL funding
coverage. Signal transitions in-window (data characterization only, no PnL): HL-native **212**
(106 entries / 106 exits), Binance **214** (107/107).

**Window B — long-history run, SPLIT (W6). This split is mandatory, not optional.**

HL funding history begins 2023-05-12 and contains **84 gaps exceeding the 90-minute
`max_funding_gap`** before **2024-08-15 14:00Z**; it is contiguous thereafter (verified). Under
contract §7.7.2 condition 2 a funding-accruing period must lie inside a **single** coverage
record, so a funding-enabled run over full Binance history would raise `FundingDataError`.

- **B1 — full long history, funding NOT modelled.** Full available Binance BTC 1h history
  (`2020-01-01 00:00Z` → `2026-07-31 23:00Z`, 57,696 bars) with warm-up consuming the first 100
  bars, `funding_mode = "disabled"`, `funding_notional_basis = "not_modelled"`.
  **This is not a fudge and MUST be reported as a first-class limitation:** Hyperliquid did not
  exist for most of this period, so there is no Hyperliquid funding to charge. Fabricating one,
  or substituting Binance funding, would violate CLAUDE.md's data-source policy directly.
  B1 evidences only that the pipeline executes over a long dataset.
- **B2 — long funding-enabled run.** `2024-08-15 15:00Z` → `2026-07-31 23:00Z` on Binance proxy
  prices with `funding_mode = "required"`, `funding_notional_basis = "period_start"`, over the
  maximal window of contiguous HL funding coverage. B2 is what evidences the **complete**
  pipeline (including funding) over a long dataset.

**Funding-coverage window rule (W6, mandatory for every funding-enabled run).** Funding MUST be
fetched/loaded over a window strictly wider than the price window on **both** sides, and the run
MUST verify `coverage_start <= T_0` and `coverage_end >= T_{n-1}` within a **single**
`FundingCoverage` record before running. `_build_coverage_for_symbol` sets coverage bounds to the
first and last *observed event* timestamps; with timestamp jitter the terminal period
`[T_{n-2}, T_{n-1})` otherwise fails condition 2 as a matter of course.

### 2.3 Backtest frame boundary

The `MarketData` frame handed to the engine MUST begin at the **first bar with a fully-defined
SMA(100)**, i.e. index 99 of the loaded series. Warm-up bars are consumed by signal computation
and are not part of the simulated sample. No NaN target may reach a rebalance bar (contract §3).
Verified by §4.7.

### 2.4 Handoff

Use the existing bridge `src/data/base.py::to_engine_frame` with policy `"raise"`. A grid gap
MUST surface as `DataIntegrityError`, never be silently filled; there is no `ffill` policy and
one MUST NOT be added.

*Contingency (W7):* Binance BTC 1h is verified gap-free over its full history, so `"raise"` is
expected to succeed for B1. If a gap is nonetheless encountered, the response is to use the
existing `"segment"` policy and report the segmentation explicitly. Filling, interpolating or
switching to a proxy series is prohibited.

---

## 3. Engine configuration (FROZEN)

| field | value | note |
|---|---|---|
| `frequency` | `"1h"` | |
| `annualization_factor` | `8760` | contract §15 |
| `execution_mode` | `"next_open"` | |
| `execution_lag` | `1` | signal at close of `t` executes at `open[t+1]` |
| `funding_mode` | `"required"` (A, B2) / `"disabled"` (B1) | see §2.2 |
| `funding_notional_basis` | `"period_start"` (A, B2) / `"not_modelled"` (B1) | mandatory; §3.2 |
| `initial_capital` | `1_000_000.0` | |
| `fee_bps` | `4.5` | assumption; §3.1 |
| `slippage_bps` | `1.0` | assumption; §3.1 |
| `risk_free_per_period` | `0.0` | explicit; reported Sharpe is a zero-risk-free figure |
| `mar_per_period` | `0.0` | explicit |
| `compute_counterfactual` | `True` | MUST NOT affect the actual result (§9.5.2); verified §4.8 |
| `max_gross_leverage` | `1.05` | see below |

**`max_gross_leverage` is 1.05, NOT 1.0.** Contract §6.8 tests `gross_exposure > max_gross_leverage`
strictly, and §5.2's `q → w → q` round trip is not bitwise stable. Measured against the real
engine, a flat `w = 1.0` book breaches `1.0` on **31/199 periods at zero cost and zero funding**
(`max gross_exposure = 1.0 + 2.22e-16`), and on 197/199 periods under `rebalance_on_change` with
funding (`1.001993`, genuine funding drift). At `1.0` any test is inert both ways. `1.05` leaves
the tripwire discriminating against genuine drift. The report MUST state `max(gross_exposure)`
and `leverage_breach` explicitly. This is a tripwire, not a risk model; contract §14 —
liquidation and margin are NOT modelled.

### 3.1 Cost assumptions are declared, not calibrated

`fee_bps = 4.5` is the Hyperliquid base-tier **taker** fee (0.045%). **Provenance:** Hyperliquid
public fee schedule, recorded 2026-08-16; not measured from execution data and not stored as a
dataset in this repository. `slippage_bps = 1.0` is a placeholder with **no empirical basis**.
Neither is calibrated against measured Hyperliquid execution. They exist so the cost path carries
non-zero values and can be verified. **No performance conclusion may be drawn from them.**

### 3.2 Funding basis

`funding_notional_basis = "period_start"` is set **explicitly**, per QR-DATA-001 DEFERRED-001.
`event_price` funding is deferred: the `asset_ctxs` oracle dataset does not exist and results
MUST NOT claim `event_price` provenance under any circumstance.

`period_start` error is bounded by `|rate| × (max intra-period price move)`. **Magnitude for this
run (W17):** contract §7.6's headline −6.98% figure is for a **daily** bar on a +15% intra-day
move. On **1h** bars the relevant bound is `|rate| × max intra-hour move`, typically well under
1% of an already-small funding charge. The limitation is real and must be stated, but MUST NOT
be reported at the daily magnitude.

Sign convention (§7.3): `funding_rate > 0` ⇒ longs pay shorts ⇒ a long with a positive rate
produces **negative** funding PnL. Manual verification MUST confirm the sign empirically.

### 3.3 Funding coverage

`FundingCoverage` is **declared by the data layer, never inferred** (§7.2). `max_funding_gap` is
pinned at **90 minutes** by the frozen data contract — below 2× the hourly cadence, so a single
missing event is caught, while tolerating observed jitter. It lives on the coverage record, not
on `BacktestConfig`.

---

## 4. Mandatory verification (this is the deliverable, not the backtest)

### 4.1 Manual path verification

Select **actual periods from the Window A run** containing at least:

1. one `0 -> long` transition
2. one `long -> long` held period
3. one `long -> 0` transition
4. one funding event on a period that is **both a rebalance execution point and
   funding-accruing** — i.e. an **entry** period where pre-trade quantity is `0` and post-trade
   quantity is not (W10). A held period does NOT satisfy this case: there
   `quantity[i] == quantity[i-1]`, so contract §7.5's "post-trade quantity as sized at Step 6"
   is indistinguishable from the pre-trade quantity and the check cannot discriminate.
5. one period carrying transaction costs

**Independence requirement (BD8, W21).** The reconstruction MUST NOT import
`src/backtest/engine.py`, `src/backtest/costs.py`, `src/backtest/metrics.py`, the QR-SMOKE-001
strategy/signal module, or the harness's price-frame construction. It MUST recompute the SMA,
the signal, and the execution price independently from the raw normalized OHLCV frame (`open`
column, selected per contract §4.2). Importing `src/backtest/models.py` is unavoidable and
permitted (`src/data/base.py` imports it transitively); the prohibition targets accounting logic.

For each selected period report:

    signal observation timestamp | signal inputs | SMA value | signal value | target weight
    | rebalance decision | execution timestamp | execution price | pre-trade weight w_pre
    | pre-trade quantity | trade (weight units) | turnover | fee_cost | slippage_cost
    | fee_basis_notional | NAV_pre | NAV_after_cost | post-trade quantity | asset_pnl_cash
    | funding_pnl_cash | NAV_end | net_return
    | gross_return | fee_return | slippage_return | funding_return

`turnover` and `NAV_after_cost` are mandatory: without `turnover` you cannot reconstruct
`fee_cost = turnover · NAV_pre · fee_bps / 10_000`; without `NAV_after_cost` you cannot check
that Step 6 sized on the post-cost NAV rather than `NAV_pre` (contract §20.1 measures that
specific error at 2.00% relative at 50 bps).

**Additionally, contract §6.1's decomposition identity (D) — the engine's primary accounting
assertion — MUST be asserted on EVERY non-ruin period of Window A**, not only the five selected
periods, at §17 `rtol=1e-12, atol=1e-15`:

    gross_return + fee_return + slippage_return + funding_return == net_return

> **Printing engine outputs and asserting they equal themselves is NOT manual verification and
> will be treated as a failed deliverable.** The reconstruction must be able to disagree.

### 4.1.1 Per-field EXACT / TOLERANCE classification (BD3, BD4 — mandatory)

Contract §17's EXACT permissions are scoped to comparisons **within a single run** ("stored
state", "the same stored double"). They do **not** transfer to a comparison between the engine
and an independent reimplementation, where every value is a different double reached by a
different arithmetic path. Applying `==` there is an over-tightening defect of exactly the kind
that recurred in six consecutive QR-INFRA-001 rounds.

Tolerances below are contract §17's: `rtol=1e-12, atol=1e-15` unless stated.

| field | class | reason |
|---|---|---|
| signal observation / execution timestamp | EXACT | timestamps (§17) |
| rebalance decision, signal value | EXACT | discrete boolean state |
| trade count, event counts, `funding_events_excluded` | EXACT | integer counts |
| execution price | EXACT | same stored double read from the same source frame |
| target weight | EXACT | literal `1.0` / `0.0` assigned by the strategy |
| turnover, quantity, `w_pre` on a **zero** branch | EXACT | literal `0.0` assigned, never computed (§5.1, §5.6, §6.0 Step 1) |
| **SMA value** | **TOLERANCE** | `pandas.rolling(100).mean()` uses an online running-sum and is **not** bitwise equal to `np.mean` over each slice |
| pre-trade quantity, post-trade quantity (nonzero) | TOLERANCE | different arithmetic path across implementations, despite being a carry-forward *inside* the engine |
| `w_pre` (nonzero), weights, exposures | TOLERANCE | §17 |
| turnover (nonzero), `fee_cost`, `slippage_cost`, `fee_basis_notional` | TOLERANCE | §17 |
| `NAV_pre`, `NAV_after_cost`, `NAV_end`, `asset_pnl_cash`, `funding_pnl_cash` | TOLERANCE | §17 (equity checks `rtol=1e-12, atol=1e-9` USD) |
| `net_return`, component returns, identity (D) | TOLERANCE | §17 |
| metrics | TOLERANCE | `rtol=1e-10, atol=1e-12` |

**`signal value` is EXACT and is the one field where a 1-ulp SMA disagreement surfaces as a
boolean flip.** Any such flip MUST be reported as a finding, never absorbed by widening a
tolerance.

### 4.2 Lookahead mutation test (deterministic)

Take the real Window A sample. Choose a bar index `k` strictly inside it; let `T = index[k]`.

**Mutate every price bar at index `>= k+2`** (i.e. strictly after `T + Δ`) — substantially, e.g.
scale all subsequent BTC prices by a large factor — **and mutate every funding event with
`timestamp >= T_{k+1}`** (BD2: a price-only mutation cannot detect a funding-aggregation bug).

Re-run. For **periods `0 .. k` inclusive**, every one of the following MUST be unchanged:

SMA values · signals · target weights · rebalance decisions · executions · quantities · NAV · PnL

**The `k+2` boundary is not arbitrary (BD1).** Under contract §4.4, period `k` spans
`[T_k, T_{k+1})` and legitimately **earns the move from `P[k]` to `P[k+1]`**. Mutating index
`k+1` therefore changes period `k` on a *correct* engine. Contract §18.1 E4 states the boundary
correctly: *"Perturb a price after `P[i+1]`; earlier periods bit-identical."* A test written to
the wrong boundary goes RED on correct code and gets loosened until it passes — that is how an
inert lookahead test is built.

**Comparison is BITWISE for every field (W14).** Contract §18.0.1 classifies E4 as EXACT —
identical arithmetic path on identical inputs. **No tolerance is admissible in this test.** A
tolerance clause here has no legitimate application and could only mask a real failure.

**Choice of `T` (W13).** Use **at least two** values of `k`, each chosen so the pre-`T` window
contains at least one entry, one held period, one exit, and one funding charge. Otherwise the
pre-`T` NAV may be `initial_capital` in both runs and every assertion compares `1e6` to `1e6`.

**Proof of discrimination.** Confirm the test goes RED under a deliberately introduced
lookahead — use `close.shift(-1)` in the signal (mutation M2) — then restore.
**Do NOT use `execution_lag = 0` as the discrimination example (W12):** a lag-0 error consumes
only information at or before `T`, so mutating data after `T` structurally cannot detect it. The
same applies to a one-bar backward shift of the execution-price frame. Both are pure
*backward*-looking misalignments and are covered by M11/M17 instead, not by this test.

Any change to a pre-`T` result ⇒ **SMOKE FAIL**.

### 4.3 Anti-inert-test requirement (NON-NEGOTIABLE)

Passing test counts are not evidence. For every test asserting a pipeline property, the
implementer MUST mutate the source, confirm the target test goes **RED**, restore, verify the
restore by diff, and report a mutation table. The independent auditor MUST **redo the mutations
itself** rather than accept that table.

The acceptance criterion for every assertion is *"does this discriminate?"*, not *"does this
pass?"*. On QR-INFRA-001 two consecutive audits returned FAIL purely on inert tests that looked
correct and could not fail; one auditor's 63 independent mutations found 4 survivors the
implementer's own table had missed.

| # | mutation | must break |
|---|---|---|
| M1 | `execution_lag` 1 → 0 | `ConfigError` raised (contract §4.2) |
| M2 | signal uses `close.shift(-1)` | §4.2 lookahead test |
| M3 | SMA window 100 → 99 | manual SMA reconstruction (§4.1) |
| M4 | SMA `min_periods` 100 → 1 | §4.7 warm-up boundary test |
| M5 | funding sign flipped in the engine | funding manual check |
| M6 | `funding_notional_basis` → `"event_price"` | must raise `FundingDataError`, not silently proceed |
| M7 | `fee_bps` → 0 | cost manual check |
| M8 | signal `>` → `<` | signal-agreement / transition count |
| M9 | Binance provenance `native_or_proxy` → `"native"` | provenance test (§4.5) |
| M10 | execution price frame `open` → `close` | execution-price manual check |
| M11 | execution-price frame shifted one bar (`open.shift(1)`) | execution-price / execution-timestamp check |
| M12 | every `FundingEvent.timestamp` shifted `+Δ` in the data adapter | per-period funding assignment + `funding_events_excluded` |
| M13 | funding valued on `quantity[i-1]` instead of `quantity[i]` | §4.1 case 4 (entry period) |
| M14 | remove the §2.3 frame slice | §4.7 warm-up boundary test |
| M15 | `rebalance_on_change` → `rebalance_every_bar` | `turnover == 0.0` EXACT on held periods |
| M16 | negate `funding_rate` in the **data adapter** (not the frozen engine) | funding sign check at the correct layer |
| M17 | `execution_lag` 1 → 2 | execution-timestamp field in §4.1 |
| M18 | feed the Binance frame to the "HL-native" run | §2.1 no-splice + §4.5 `dataset_id` / venue provenance |
| M19 | drop `funding_pnl_cash` from the reconstruction's Step 9 | decomposition identity (D) |

Note M1, M5, M6, M10 mutate frozen, already-audited engine code and M9 a provenance literal;
they are regression checks on the handoff, not coverage of QR-SMOKE-001 source. M2, M3, M4, M7,
M8, M11–M19 are the mutations that exercise smoke-test code.

### 4.3.1 Vacuity rule (BD7, W9 — mandatory)

A mutation that cannot change the data is **VACUOUS** and MUST be reported as vacuous, never as
passed. Specifically:

- **`>` vs `>=` in the signal** differs only where `close[t]` is bitwise equal to `SMA100[t]`.
  Report the count of such bars in Window A. If zero, the boundary is vacuous — hence M8 uses
  `>` → `<`.
- **Contract §7.5's half-open funding boundary** (`T_i <= e.timestamp < T_{i+1}`) differs under
  `<` → `<=` only if an event lands *exactly* on a bar boundary. HL event timestamps carry
  jitter, so such an event likely does not exist in Window A. Report the count of
  boundary-coincident events; if zero, declare the rule **vacuous on this data** rather than
  claiming it verified.

### 4.4 Binance proxy vs Hyperliquid native (Window C = Window A)

Run the identical, unchanged strategy twice over Window C. Do not splice.

**Pinned experimental design (W5) — these are methodological choices, not implementer latitude:**

1. **Each run derives its signal from its own venue's closes and executes at its own venue's
   opens.** The work order asks for "signal agreement rate" and "differing signal timestamps",
   which only exist if each run computes its own signal. This measures the *total* effect of
   substituting the proxy, and consequently **confounds signal and execution effects** — that
   confound MUST be stated, and the differing-signal analysis is what separates them.
2. **Both runs charge the same Hyperliquid-native funding events**, with `period_start` notional
   computed on *that run's own* price frame. Rationale: Hyperliquid is the execution venue in
   both cases (CLAUDE.md), so HL funding is the economically correct cost in both; the only
   intended difference between the runs is the price series. The Binance run therefore applies
   native HL funding to a proxy-priced notional, which MUST be labelled as such.
3. **Identical bar index (W4):** assert EXACT `hl_index.equals(binance_index)`, identical
   warm-up length, identical config. Comparing trade count / total return / max drawdown across
   different horizons is the error contract §9.2 forbids for drag statistics.

**Alignment test — falsifiable, not a report (W3).** Compute contemporaneous log-return
correlation `ρ(0)` and the lagged profile `ρ(-2..+2)` between the two venues' close series on
the identical bar index. **Assert EXACT `argmax_l ρ(l) == 0`, and assert `ρ(0) >= 0.99`.** A
`ρ(0)` below that, or an argmax at any lag other than 0, is a **SMOKE FAIL for alignment**, not
a divergence to be discussed. (Reporting a correlation number is not a test: `ρ = 0.02` would
satisfy a report-only deliverable while indicating a serious defect.)

Then report: signal agreement rate; entry/exit counts; the specific timestamps where signals
differ; trade count; total return; max drawdown; realized volatility.

The runs need not produce identical PnL. The question is whether Binance-as-proxy **materially
changes this strategy's behaviour**. Divergences MUST be investigated individually — identify
what happened at the differing bars. Averaging them into a summary statistic and declaring them
small is not an answer.

### 4.5 Provenance survival

Artifacts of every run MUST preserve end-to-end: price source venue · `native_or_proxy` ·
`dataset_id` · processing version · target execution venue · funding basis · relevant
universe/data provenance. Per contract §13.1, `native_or_proxy == "proxy"` with empty `proxy_for`
MUST raise `DataIntegrityError`, and any proxy input MUST set `uses_proxy_data`.

**A Binance-history backtest must never be presentable as having used Hyperliquid-native
prices.** Prove this survives **serialization to the artifact**, not merely that it is set in
memory.

`survivorship_safe` MUST be `False` for Hyperliquid data and MUST NOT default to `True`
(contract §13.2). B1 additionally MUST record `funding_modelled = False`.

### 4.6 Determinism

Run the identical Window A experiment more than once, in separate processes and under differing
`PYTHONHASHSEED`, and compare bit-identically (contract §16).

**Comparison method is pinned (W19).** `BacktestResult` is declared
`@dataclass(frozen=True, eq=False)`, so `__eq__` falls back to **identity** and `r1 == r2` across
processes is always `False`. Compare element-wise via `.values.tobytes()` on every Series and
DataFrame plus exact comparison of every scalar, as the contract's own P/P2 tests do.

### 4.7 Warm-up boundary test (BD6 — new, named)

Let `raw` be the loaded series including warm-up. Assert:

- EXACT: `frame.index[0] == raw.index[99]`
- EXACT: `len(frame) == len(raw) - 99`
- TOLERANCE (§17): `SMA[frame.index[0]] == mean(raw.close[0:100])`
- `frame` contains no NaN in `close` or in the SMA

M4 and M14 must break **this** test. Without it, M4 survives: if §2.3 is implemented as "slice at
index 99", then with `min_periods = 1` every retained bar still has a full 100-observation window
and nothing changes. (The engine gives only partial protection — a NaN first-row target raises
because `rebalance_on_change` always emits `True` at index 0 — but `min_periods = 1` produces no
NaN at all, so that guard never fires.)

### 4.8 Result-surface properties not otherwise tested (W8)

- **`unexecuted_rebalances`** (contract §4.4): a signal flip at bar `n-2` or `n-1` produces an
  unexecuted rebalance, silently reducing trade count in §4.4. Assert and report it.
- **Terminal bar**: assert `len(equity_curve) == n`, `len(net_return) == n-1`, and that bar `n-1`
  earns nothing.
- **`funding_events_excluded`** (contract §7.5): assert EXACT integer
  `Σ events charged + funding_events_excluded == total events in [T_0, T_{n-1}]`.
- **Counterfactual isolation** (contract §9.5.2): run Window A with `compute_counterfactual`
  `True` and `False`; assert the actual path is **bit-identical**.

### 4.9 Reporting hazards (W22, W15)

`cagr` and `calmar` are meaningless on short samples and, with `af = 8760`, can raise
`OverflowError` on a short 1h sample (contract §12.5, B5). They MUST be suppressed or footnoted
in every report; present `total_return`. Reported Sharpe/Sortino are zero-risk-free-rate figures
(`risk_free_per_period = mar_per_period = 0.0`).

---

## 5. Escalation

Escalate to the user, rather than deciding, if and only if:

1. the frozen QR-INFRA-001 contract would need to change
2. QR-DATA-001 and QR-INFRA-001 have a genuine interface incompatibility
3. an economically meaningful methodological decision is required
4. a data limitation makes the requested validation impossible
5. credentials, paid egress, or funds could be affected

Requester-Pays S3 egress is a spend decision and is **out of scope**. Routine engineering
decisions are not escalated. Maximum **two** implementation/audit repair cycles; if unresolved
after two, STOP and report.

---

## 6. Verdict criteria (self-contained, W20)

`SMOKE PASS` requires all twelve:

1. real data successfully feeds the strategy
2. strategy outputs valid target weights
3. target weights successfully feed the common backtester
4. execution timing matches the frozen contract
5. manual accounting checks match the engine (§4.1, incl. identity (D))
6. funding applied correctly under `period_start` (§3.2, §4.1 case 4)
7. transaction costs correct (§4.1 case 5)
8. future-data mutation produces no earlier changes (§4.2)
9. provenance survives end-to-end (§4.5)
10. repeated runs are deterministic (§4.6)
11. Binance-vs-Hyperliquid differences measured and understood (§4.4)
12. independent auditor passes

Verdict is `SMOKE PASS` / `SMOKE PASS WITH WARNINGS` / `SMOKE FAIL`.
**Any failure of §4.2 (lookahead) or §4.4's alignment assertion is an automatic `SMOKE FAIL`.**
A mutation reported as passed when it is in fact vacuous (§4.3.1) is a defect of the same
severity as a wrong number.
