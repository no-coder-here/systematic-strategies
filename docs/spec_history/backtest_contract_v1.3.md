# Backtest Contract — SPECIFICATION v1.3 (QR-INFRA-001)

Status: **UNDER REVIEW — NOT FROZEN.** Awaiting re-audit of B1–B12, C1–C7 and the v1.3 material.
Owner: Research Lead. Supersedes v1.2 (SPEC FAIL), v1.1 (SPEC FAIL), v1.0 (rejected).
No implementation may begin until this document is marked FROZEN by the platform owner.

This document is normative. Where this document and intuition disagree, this document wins.

---

## 0. Revision history

**v1.0** — rejected by owner. Implicit rebalancing; drift denominator used gross return while
NAV compounded on net; one funding event assumed per bar; `funding_pnl` named as cash but
documented as a ratio; funding disabled by inference; leverage implied survivability.

**v1.1** — SPEC FAIL. 12 blocking defects. Accounting core verified correct (9.9e-17); all
defects at boundaries or in the test suite.

**v1.2** — SPEC FAIL. 7 blocking defects (C1–C7). Accounting core re-verified (3.56e-16).
B1, B3, B4, B6, B12 closed. New defects introduced by v1.2's own material.

**v1.3 — this document.** Resolves C1–C7 under owner rulings, closes the residue of B2, B5,
B7, B8, B9, B10, B11, and fixes all correctness-, integrity-, reproducibility-,
provenance- and interpretation-affecting warnings.

### v1.2 -> v1.3 changes

| ref | defect | resolution | § |
|-----|--------|------------|---|
| C1 | `0 * NaN` contradiction; unlisted symbols poisoned PnL and raised spuriously | Four-state symbol classification; masked aggregation; no arithmetic on inactive symbols | §5.3, §6.4 |
| C2 | Zero/negative prices unguarded; negative price silently inverted positions | Every price in use must be finite and strictly `> 0`; `InvalidPriceError` | §5.5 |
| C3 | Coverage rule vacuous — events a year outside the window passed all four conditions | Explicit `FundingCoverage` metadata; coverage is declared, not inferred | §7.7 |
| C4 | Counterfactual could truncate the actual result | Paths fully independent; counterfactual is diagnostic-only and may never touch the actual | §9.3 |
| C5 | Replacement test M7 impossible and inverted | M7 deleted. No derived identities. Literal pinned values, computed and shown | §18.6 |
| C6 | `==` asserted on floating-point-equivalent paths; failed 32.1% | Explicit exact-vs-tolerance policy and a tolerance table | §17 |
| C7 | Per-event `notional_price` fallback made three mandatory tests unreachable | `funding_notional_basis` is a run-level mode; no per-event mixing | §7.6 |
| B5r | §6.1 decomposition silently breaks at the ruin bar (residual measured −0.2) | Stated explicitly; residual exposed; X6 asserts the NAV identity only | §6.7 |
| B8r | Delisting: held positions could lose their next-period price | Four-state model requires `P[i]` and `P[i+1]` for any held position | §5.3 |
| w1 | Coverage false-rejected non-contiguous exposure | Multiple coverage records per symbol | §7.7 |
| w3 | `NAV_after_cost <= 0` unguarded; costs alone could invert positions | Ruin is checked at `NAV_after_cost` as well as `NAV_end` | §6.7 |
| w4 | E2/E3 unpinned; `lag=2` indistinguishable from `lag=1` | Both pinned with computed values; E3 discriminates three ways | §18.1 |
| w5 | CF3 fixture could spuriously pass | Pinned fixture with nonzero return on a rebalance period | §18.7 |
| w7 | Provenance incomplete against CLAUDE.md | `time_range`, `field_type`, mandatory `proxy_for` | §13 |
| w8 | §5.4 named the wrong row | Reworded to `target_weights[t]`, `t = i - execution_lag` | §5.4 |
| w9 | Symbol renames unaddressed | Stated: they land on the delisting path and must be resolved upstream | §11.4 |
| w12 | Counterfactual auxiliary behaviour undefined | Specified | §9.4 |

Remaining documentation-only warning, accepted and not fixed: **w11** — §4.4 describes
tradeable execution points as `i = 0 .. n-2`, but `i = 0` is unreachable because
`execution_lag >= 1`. Harmless; noted here so no one writes a test for it.

---

## 1. Scope

The common backtesting engine for every systematic strategy in this repository.

The engine contains **no alpha logic**. It does not compute signals, filter universes, or
decide when to trade. It consumes target weights plus market data and produces accounting.

    target_weight = signed notional exposure / NAV

Strategies MUST NOT compute PnL, equity, fees, funding, slippage, turnover or metrics.

---

## 2. Bar timestamp semantics

**Bars are LEFT-LABELLED and half-open.** A bar labelled `t` covers `[t, t + Δ)`.

- `open[t]` is the first trade price at instant `t`
- `close[t]` is the last price in `[t, t+Δ)`, observable only at instant `t + Δ`
- bar `t` is COMPLETE at instant `t + Δ`

All timestamps MUST be timezone-aware UTC. Naive timestamps are rejected, never localized.

### 2.1 Regular grid requirement

The bar index MUST be a regular grid with spacing exactly `Δ`, derived from
`config.frequency`. Missing bars, irregular spacing, or spacing disagreeing with `frequency`
MUST raise `DataIntegrityError` naming **the first offending timestamp pair and the expected
Δ**.

Rationale: §4.3 defines execution instants and §7.5 defines funding windows in terms of `Δ`.
An irregular grid makes both ill-defined, and interpolation inside an accounting engine is
how lookahead gets laundered. QR-DATA-001 owns gap-filling and segmenting policy for real
venue outages.

---

## 3. Strategy output contract

    StrategyOutput:
        target_weights : DataFrame  (index = bar label, columns = symbol)
        rebalance_mask : Series     (index = bar label, dtype = bool)

`rebalance_mask` is **REQUIRED**, no default. The engine MUST NOT infer rebalance intent by
comparing consecutive target weights. "The numbers changed" is not a trade instruction, and
"the numbers are identical" is not a hold instruction. Only the mask decides.

| `rebalance_mask[t]` | meaning |
|---------------------|---------|
| `True`  | `target_weights[t]` becomes the active target, eligible for execution at `t + execution_lag`. A trade occurs. |
| `False` | No new target is issued. Held **quantities** are unchanged (§5.1). Zero turnover, zero cost. `target_weights[t]` is ignored entirely. |

`NaN` in `target_weights` is permitted on non-rebalance bars and is an error on rebalance
bars (§11.2).

Named constructors, no bare default:

    StrategyOutput.rebalance_every_bar(weights)
    StrategyOutput.rebalance_on_dates(weights, dates)
    StrategyOutput.rebalance_on_dates(weights, dates, exit_unnamed=True)
    StrategyOutput.rebalance_on_change(weights)   # opt-in, never automatic

`exit_unnamed=True` materialises explicit `0.0` targets for held symbols the strategy did not
name, **in the strategy's own output frame**, before it reaches the engine. This is the
sanctioned ergonomic answer to §5.4's strictness: the exit becomes visible in the strategy's
output rather than being silently invented by the engine.

### 3.1 Mask granularity (v1 restriction)

Portfolio-level mask only: a rebalance event rebalances all symbols to target. Per-symbol
masks are a deliberate v2 extension.

Supports pairs, cross-sectional ranking and market-neutral books. Does **not** support
per-leg band rebalancing or overlapping-tranche portfolios. The tranche workaround (separate
backtests, averaged) is valid **only when costs are not netted across tranches**, which
overstates turnover; research using it must say so.

---

## 4. Timing

### 4.1 The anti-lookahead rule

    target_weights[t] is computed from information through the CLOSE of bar t.
    It is not known until instant t + Δ.
    It MUST NOT earn any price movement at or before the close of bar t.

### 4.2 Execution price series

    P = open   if execution_mode == "next_open"
    P = close  if execution_mode == "next_close"

A rebalance flagged at bar `t` trades at execution point `i = t + execution_lag`, at `P[i]`.

`execution_lag` MUST be `>= 1`. Lag 0 is a lookahead error and MUST raise `ConfigError`.

The lag MUST be applied by explicit positional index arithmetic after asserting the index is
sorted, unique and regular (§2.1). Bare `.shift()` on an unverified frame is NOT acceptable.

### 4.3 Execution instant `T_i`

`T_i` is the wall-clock instant of execution point `i`. It is NOT the bar label in general.

    T_i = t_i            if execution_mode == "next_open"
    T_i = t_i + Δ        if execution_mode == "next_close"

MUST be implemented as an explicit, separately tested function
`execution_instant(bar_label, mode, delta)`.

| i | `t_i` | `T_i` (`next_open`) | `T_i` (`next_close`) |
|---|-------|---------------------|----------------------|
| 0 | 2026-01-01 00:00 | 2026-01-01 00:00 | 2026-01-02 00:00 |
| 1 | 2026-01-02 00:00 | 2026-01-02 00:00 | 2026-01-03 00:00 |
| 2 | 2026-01-03 00:00 | 2026-01-03 00:00 | 2026-01-04 00:00 |

Holding period `i` spans `[T_i, T_{i+1})`: bar `i`'s own span under `next_open`, bar `i+1`'s
span under `next_close`. Getting this wrong misattributes funding by a full bar and was
measured at a **10x** error on a 1d/hourly-funding fixture.

### 4.4 Sample boundaries

Given `n` price bars indexed `0 .. n-1`:

- **Holding periods**: `i = 0 .. n-2`, exactly `n - 1` of them. Period `i` spans
  `[T_i, T_{i+1})` and earns the move from `P[i]` to `P[i+1]`.
- **Tradeable execution points**: `i = 1 .. n-2` (`i = 0` is unreachable since
  `execution_lag >= 1`).
- **Terminal valuation instant**: `T_{n-1}`. Final NAV is marked here. No trade occurs and
  bar `n-1` earns no return, because `P[n]` does not exist.

A rebalance flagged at bar `t` with `t + execution_lag > n - 2` names a nonexistent execution
point. This is **not an error** but MUST be recorded in
`BacktestResult.unexecuted_rebalances`.

`n < 2` MUST raise `ConfigError`.

### 4.5 Zero-latency assumption

Under `next_open` with contiguous bars, the information instant (`close[t]`, at `t + Δ`) and
the execution instant (`open[t+1]`, at `t + Δ`) are the **same instant**. This is the standard
convention and the tightest defensible assumption, but it grants zero decision, transmission
and matching latency.

Stated as an assumption, not as a proof of lookahead-freedom. Strategies sensitive to sub-bar
latency MUST use `execution_lag >= 2` or `next_close` and must say so. §18.1 E3 tests
`execution_lag = 2` for this reason.

---

## 5. Position state, symbol activity, and price validity

### 5.1 Quantity is primary

`quantity[i, j]` — the signed units of symbol `j` held during holding period `i` — is the
**primary position state**. Weights are derived.

    at a rebalance execution point i, for each symbol j:
        if w_target[i, j] == 0:  quantity[i, j] = 0.0            # assigned, not computed
        else:                    quantity[i, j] = w_target[i, j] * NAV_after_cost[i] / P[i, j]

    at a non-rebalance point i:
        quantity[i, j] = quantity[i-1, j]                        # exact carry-forward

    initial:
        quantity[-1, j] = 0.0

The non-rebalance branch MUST be a direct carry-forward of the stored value. The engine MUST
NOT reconstruct quantity from weights (`w * NAV / P`): the round trip `q -> w -> q` is not
bitwise stable, measured failing on **17.8%** of realistic inputs.

The `w_target == 0` branch MUST assign literal `0.0` rather than evaluating
`0.0 * NAV_after_cost / P[i, j]`. When `P[i, j]` is absent for an inactive symbol that
expression yields `NaN`, which then defeats every `quantity == 0` test downstream, including
§18.3 R4's exact comparison.

### 5.2 Derived weights

    w_pre[i, j]  = 0.0 if quantity[i-1, j] == 0 else quantity[i-1, j] * P[i, j] / NAV_pre[i]
    w_held[i, j] = 0.0 if quantity[i,   j] == 0 else quantity[i,   j] * P[i, j] / NAV_after_cost[i]

The zero branches are **assigned**, never computed, for the same reason as §5.1.

Weight drift is not a formula: it is an emergent consequence of holding quantity constant
while `P` and NAV move.

### 5.3 Symbol activity classification (owner ruling 1)

For each holding period `i` and symbol `j`, define `q_prev = quantity[i-1, j]` and the
**intended** post-trade quantity indicator:

    will_hold[i, j] =  (w_target[i, j] != 0)  if i is a rebalance execution point
                       (q_prev != 0)          otherwise

Every symbol occupies exactly one of four states, and each state has an exhaustive price
requirement:

| state | condition | required prices | contribution |
|-------|-----------|-----------------|--------------|
| **INACTIVE** | `q_prev == 0` and `not will_hold` | **none** | exactly zero, by exclusion from aggregation |
| **ENTERING** | `q_prev == 0` and `will_hold` | `P[i, j]` (execution), `P[i+1, j]` (valuation) | full |
| **HELD** | `q_prev != 0` and `will_hold` | `P[i, j]` (valuation + any trade), `P[i+1, j]` (valuation) | full |
| **EXITING** | `q_prev != 0` and `not will_hold` | `P[i, j]` (execution to close) — `P[i+1, j]` NOT required | trade only; position is flat over period `i` and earns nothing |

The classification MUST be computed, and all required prices validated (§5.5), **before any
accounting arithmetic for period `i`**. This is what makes the requirement decidable: it
depends only on `q_prev` and the target, never on a price.

**The engine MUST NEVER rely on arithmetic such as `0 * NaN` to neutralise an inactive
symbol.** v1.2 claimed quantity-space PnL made inactive symbols contribute zero "regardless
of price"; in IEEE754 `0.0 * NaN = NaN`, so a single unlisted symbol produced a NaN portfolio
PnL, a NaN equity curve that passed the `NAV_end <= 0` ruin test unnoticed, and — because
`0.0 - NaN != 0` evaluates `True` — a spurious `MissingPriceError` on a symbol that had simply
not listed yet.

INACTIVE symbols are excluded by an explicit boolean mask. Their prices are never read,
never validated, and may be absent, `NaN`, zero or negative without consequence.

### 5.4 Column alignment

The engine operates on the **sorted union** of symbols present in the market data; all frames
are reindexed onto it.

When execution point `i` is a rebalance point sourced from signal bar `t = i - execution_lag`,
the row that must name every exit is **`target_weights[t]`**, not `target_weights[i]`:

- every symbol `j` with `quantity[i-1, j] != 0` MUST appear in `target_weights[t]` with a
  finite value. Absent or `NaN` -> `DataIntegrityError`.
- symbols absent from `target_weights[t]` with `quantity[i-1, j] == 0` are treated as target
  0 and are INACTIVE.

Rationale: v1.2 permitted two readings differing by 0.4 turnover on the same input — one
silently liquidating a position nobody asked to close, the other silently holding exposure
through a portfolio-level rebalance. Forcing the strategy to name its exits resolves this
upstream, where the information exists. `exit_unnamed=True` (§3) supplies the ergonomics.

### 5.5 Price validity (owner rulings 2 and 8)

**Every price actually used for execution, valuation, return calculation, position sizing, or
funding-event valuation MUST be finite and strictly greater than zero.**

Any violation raises `InvalidPriceError` (a `DataIntegrityError` subclass) naming the symbol,
timestamp, offending value and the use that required it. This covers:

- `P[i, j]` for ENTERING, HELD and EXITING symbols
- `P[i+1, j]` for ENTERING and HELD symbols
- `notional_price` on any funding event applied to a nonzero position under
  `funding_notional_basis = "event_price"` (§7.6)

`NaN`, `±inf`, `0.0` and negative values are all rejected. v1.2 guarded only non-finite
values; `0.0` and `-5.0` are finite and passed. Measured consequences: `P = 0` produced
`quantity = inf`; `P = -5` produced a **short** position from a long target while
`gross_exposure` read a perfectly normal `1.0`, so even the §6.8 tripwire could not see it.
Zero and negative prints are routine loader and venue artifacts.

Prices for INACTIVE symbols are not "in use" and are deliberately unvalidated (§5.3).

### 5.6 Notional

    notional[i, j] = quantity[i, j] * P[i, j]        # 0.0 exactly for INACTIVE symbols

---

## 6. Accounting sequence (NORMATIVE)

PnL and funding accrue on **every** holding period. Trades occur **only** at rebalance
execution points. For `i = 0 .. n-2`, with `NAV_pre[0] = initial_capital`:

**Step 0 — classify and validate.** Compute §5.3 states for all symbols; validate all
required prices per §5.5. No arithmetic before this completes.

**Step 1 — trade**

    i is a rebalance execution point  iff  ∃ t : t + execution_lag == i
                                            AND rebalance_mask[t] == True

    if rebalance:
        w_target[i]  = target_weights[t]
        trade[i, j]  = 0.0 if (q_prev == 0 and w_target[i, j] == 0)      # INACTIVE, assigned
                       else w_target[i, j] - w_pre[i, j]
        turnover[i]  = Σ_j | trade[i, j] |
    else:
        trade[i] = 0 ;  turnover[i] = 0

The `rebalance_mask[t] == True` condition is part of the definition and must not be dropped
when transcribing.

**Step 2 — costs, charged against pre-trade NAV**

    fee_cost[i]       = turnover[i] * NAV_pre[i] * fee_bps      / 10_000
    slippage_cost[i]  = turnover[i] * NAV_pre[i] * slippage_bps / 10_000
    NAV_after_cost[i] = NAV_pre[i] - fee_cost[i] - slippage_cost[i]

If `NAV_after_cost[i] <= 0`, ruin occurs here (§6.7) **before any sizing**.

**Step 3 — set the quantity ledger** — per §5.1.

**Step 4 — asset PnL (masked aggregation)**

    active = { j : quantity[i, j] != 0 }
    asset_pnl_cash[i] = Σ_{j ∈ active} quantity[i, j] * ( P[i+1, j] - P[i, j] )

The sum runs over the **active set only**. Symbols outside it are excluded from the
summation, not multiplied by zero.

**Step 5 — funding PnL** — per §7. `funding_pnl_cash[i]`, signed USD, also masked to `active`.

**Step 6 — ending NAV**

    NAV_end[i]   = NAV_after_cost[i] + asset_pnl_cash[i] + funding_pnl_cash[i]
    NAV_pre[i+1] = NAV_end[i]                                    # unless ruin, §6.7

**Step 7 — finiteness guard.** If `NAV_end[i]` is not finite, raise `AccountingError`. This
is an internal-consistency guard, not a data guard: given §5.5 and §6 Step 4, a non-finite
NAV is an engine bug. It exists because a `NaN` NAV silently passes the `NAV_end <= 0` ruin
test (`NaN <= 0` is `False`) and would otherwise propagate into the equity curve unnoticed.

### 6.1 Return decomposition

All returns are fractions of `NAV_pre[i]`:

    gross_return[i]     =  asset_pnl_cash[i]   / NAV_pre[i]
    fee_return[i]       = -fee_cost[i]         / NAV_pre[i]      # <= 0
    slippage_return[i]  = -slippage_cost[i]    / NAV_pre[i]      # <= 0
    funding_return[i]   =  funding_pnl_cash[i] / NAV_pre[i]      # signed

    net_return[i] = gross_return[i] + fee_return[i] + slippage_return[i] + funding_return[i]

**Two normative statements, deliberately separated because they have different status:**

**(N-1) NAV identity** — holds on every non-ruin period, to §17 tolerance:

    net_return[i] == NAV_end[i] / NAV_pre[i] - 1

**(N-2) Additive decomposition** — holds on every **non-ruin** period, to §17 tolerance: the
four components sum to `net_return[i]`. It does **NOT** hold at a ruin period (§6.7).

(N-1) has been verified independently twice: max abs error 9.9e-17 and 3.56e-16 over
randomized multi-asset runs with all four cost components nonzero.

### 6.2 `gross_return` is an attribution component, not a path

`gross_return[i]` is the **pre-cost return of the actual portfolio**. Because Step 3 sizes
quantities on `NAV_after_cost`, it already reflects the capital costs removed. It is a
decomposition term, **not** a zero-cost counterfactual, and does **not** compound into a
meaningful gross equity curve.

There is deliberately **no** `gross_equity_curve`, no gross Sharpe and no gross drawdown
derived from this series. The zero-cost counterfactual is a separate simulation (§9).

`turnover[i] * NAV_pre[i]` is the **fee basis**, not traded notional. True traded notional is
`Σ_j | quantity[i,j] * P[i,j] - quantity[i-1,j] * P[i,j] |`. The result exposes the former as
`fee_basis_notional`; it must not be labelled "traded notional".

### 6.3 Turnover

    turnover[i] = Σ_j | trade[i, j] |

**One-way turnover as a fraction of NAV, NO factor of 0.5.** Mandatory consequences:
`0 -> +1` gives turnover 1; `+1 -> -1` gives turnover 2; a non-rebalance period gives exactly
0 regardless of drift. Measured against the drifted `w_pre[i]`, never the previous target.

### 6.4 Masked aggregation (owner ruling 1)

Every portfolio aggregate — `asset_pnl_cash`, `funding_pnl_cash`, `turnover`,
`gross_exposure`, `net_exposure` — MUST be computed over an explicit active mask, excluding
symbols whose relevant quantity is exactly zero. No aggregate may depend on a price belonging
to an INACTIVE symbol.

### 6.5 Exposures

    gross_exposure[i] = Σ_j | w_held[i, j] |
    net_exposure[i]   = Σ_j   w_held[i, j]
    gross_leverage[i] = gross_exposure[i]

`gross_leverage` is an explicit alias of `gross_exposure`, carrying the docstring
*"notional/NAV; NOT a margin ratio; see §14 — liquidation is not modelled."* It is not named
`leverage`. The implementer MUST NOT invent a third definition.

### 6.6 Order of operations

Rebalance, pay, then earn. Costs are a function of `NAV_pre`; positions of `NAV_after_cost`.

### 6.7 Ruin (owner ruling 4, extended)

**Ruin is an economic outcome, not an implementation exception.**

Ruin is tested at **two** points in the sequence:

1. **After Step 2**, if `NAV_after_cost[i] <= 0` — transaction costs alone exhausted the
   account. Detected before sizing, so no quantity is ever formed from a non-positive NAV.
   v1.2 tested only `NAV_end`; with turnover 4 at 3000 total bps, `NAV_after_cost = -200,000`
   and a `+3.0` long target produced a **short** position of `-6,000` units, on which a 40%
   price drop generated a *profit* and the simulation continued with `gross_exposure` reading
   a normal `2.9999`.
2. **After Step 6**, if `NAV_end[i] <= 0`.

On ruin at period `i`:

1. `NAV_end[i] := 0.0` exactly
2. `net_return[i] := -1.0` exactly. The uncapped value is recorded as `uncapped_ruin_return`
3. `ruined = True`; `ruin_timestamp = T_{i+1}`
4. **the simulation terminates.** No period after `i` is computed. `quantity[i+1]` is never
   formed, so §5.2's division by `NAV_pre[i+1] = 0` never occurs
5. artificial zero-return periods MUST NOT be appended; padding deflates volatility and
   flatters Sharpe on a dead strategy
6. all series truncate to the realized range; `equity_curve` ends at `T_{i+1}` with `0.0`

**(N-1) holds at the ruin period by construction** (`0/NAV_pre - 1 = -1`).

**(N-2) does NOT hold at the ruin period.** The four components sum to the uncapped return,
not to `-1`. On the pinned §18.8 fixture the residual is exactly `-0.2`. The engine MUST
expose `ruin_decomposition_residual = uncapped_ruin_return - (-1.0)`. §18.8 X6 asserts (N-1)
only, and MUST NOT assert (N-2), at the ruin period. v1.2 claimed "the §6.1 identity is
preserved at the ruin bar by construction" without saying which identity — the exact
ambiguity that makes an implementer write an impossible test.

`ruined = True` MUST appear in `BacktestResult.__repr__`, in any summary table, and in any
report the result feeds.

### 6.8 Leverage tripwire

Optional `max_gross_leverage: float | None = None`. When set, any period with
`gross_exposure[i] > max_gross_leverage` sets `leverage_breach = True` and records the
timestamps. It does **not** alter the simulation — the engine reports, it does not risk-manage.

---

## 7. Funding

Hyperliquid funds **hourly**; strategies run on 1h, 4h and 1d bars. Therefore:

> **One OHLC bar does NOT correspond to one funding event.**

Funding is an **event stream at its native venue frequency**, independent of bar frequency
and of strategy signal frequency. **No venue's funding cadence may be hardcoded in the
engine.**

### 7.1 Funding events

    FundingEvent:
        timestamp       : tz-aware UTC instant the funding was charged
        symbol          : str
        funding_rate    : float          # realised rate for THAT event, decimal fraction
        notional_price  : float | None   # required iff basis == "event_price" (§7.6)

`funding_rate` is the **per-event realised rate**: not annualized, not rescaled, not a
percentage. An hourly rate of one basis point is `0.0001`. The loader owns normalization; the
engine performs no unit guessing and no cadence inference.

### 7.2 Funding coverage metadata (owner ruling 3)

    FundingCoverage:
        symbol           : str
        coverage_start   : tz-aware UTC
        coverage_end     : tz-aware UTC
        max_funding_gap  : Timedelta        # venue cadence tolerance
        source_venue     : str

A symbol MAY have **multiple** coverage records with disjoint intervals, so a caller that
fetched funding only for the periods it holds is not penalised for the idle stretches between
(v1.2 false-rejected exactly this).

`max_funding_gap` lives here, not in `BacktestConfig`: it is a property of the venue and the
dataset, not a choice of the backtest. `max_funding_gap <= 0` raises `DataIntegrityError`.

Coverage is **declared by the data layer, never inferred from the event stream.** Inferring
cadence is circular — the stream being validated is the stream that would be inferred from,
so a systematically sparse stream infers a permissive tolerance and validates itself.

### 7.3 Sign convention

    funding_rate > 0  =>  LONGS PAY SHORTS

A long with a positive rate produces **negative** funding PnL. The minus sign in §7.5 is the
only place this convention is applied.

### 7.4 No lookahead

Funding is consumed only as a realised cost, never as a signal input. Using the realised
contemporaneous rate is correct and is not lookahead. A strategy wanting funding as a
*signal* must receive it through market data subject to §4 timing.

### 7.5 Aggregation into holding periods

For holding period `i` spanning `[T_i, T_{i+1})` (§4.3), select events `e` for symbol `j`
with

    T_i <= e.timestamp < T_{i+1}

**Half-open**, so an event on a boundary is charged to exactly one period — the later one.
Events strictly before `T_0` or at/after `T_{n-1}` are excluded and counted in
`funding_events_excluded`.

Valuation depends on the run-level basis (§7.6):

    basis == "event_price":   notional_e = quantity[i, j] * e.notional_price
    basis == "period_start":  notional_e = quantity[i, j] * P[i, j]

    funding_pnl_cash[i] = - Σ_{j ∈ active} Σ_e notional_e * e.funding_rate

`quantity[i, j]` is the **post-trade** quantity: funding on a rebalance period is charged on
the position actually held. The outer sum is masked to the active set (§6.4).

### 7.6 `funding_notional_basis` is a run-level mode (owner ruling 7)

    funding_notional_basis = "event_price" | "period_start"
    # REQUIRED when funding_mode == "required"; reported as "not_modelled" when disabled

| mode | behaviour |
|------|-----------|
| `"event_price"` | **Every** funding event applied to a nonzero position MUST carry a finite, strictly positive `notional_price`. Any missing or invalid value raises `FundingDataError`. Venue-accurate: Hyperliquid values funding on position notional at the **oracle price**. |
| `"period_start"` | `notional_price` is **ignored entirely** — not required, not read, not validated. Funding uses the documented start-of-period notional approximation. |

**There is no per-event fallback and no `"mixed"` basis.** v1.2 encoded "absent" as `NaN` and
then raised on non-finite `notional_price`, making the fallback, the `"mixed"` basis and three
mandatory tests simultaneously unreachable under one reading and materially wrong under the
other. Mixing is now impossible by construction rather than by convention.

The `"period_start"` approximation holds notional flat across the period. Its error is bounded
by `|rate| x (max intra-period price move)` and is **not** negligible: on a +15% intra-day
move it misstates the day's funding by **-6.98%**. Choosing it is a disclosed modelling
decision, reported on every result.

Rejected alternative: averaging period-start and period-end notional. That uses `P[i+1]`, a
future price relative to every funding instant in the period, correlated with the period's own
return — lookahead injected directly into a cost term, landing on the alpha of any
funding-carry strategy.

### 7.7 Funding mode and coverage validation (owner ruling 3, resolves C3)

    funding_mode = "required" | "disabled"       # REQUIRED config, no default

Under `"disabled"`: funding is exactly 0, `funding_modelled = False`,
`funding_notional_basis = "not_modelled"`. Every result surface must show it. The engine MUST
NOT infer `"disabled"` from a missing column, an empty event frame, or a symbol with no
events.

Under `"required"`, for every symbol `j` and every holding period `i` with
`quantity[i, j] != 0`, raise `FundingDataError` unless **all** hold:

1. at least one `FundingCoverage` record exists for `j`
2. **`[T_i, T_{i+1}) ⊆ [coverage_start, coverage_end]` for a single coverage record** — the
   exposed interval lies inside one declared window
3. within that record's window, the augmented sequence
   `[coverage_start] + sorted(events in window) + [coverage_end]` has **no consecutive gap
   exceeding `max_funding_gap`**

A gap exactly equal to `max_funding_gap` is **accepted** ("exceeds" means strictly greater).

Symbols with zero exposure throughout need no funding data.

This replaces v1.2's rule, which tested `min`/`max` over the *entire* stream. Counterexample
that passed all four of v1.2's conditions: exposure over 150 hourly periods from 2026-01-01,
`max_funding_gap = 8h`, event stream `{2025-01-01, 2027-01-01}` — **zero funding charged over
150 exposed hours, nothing raised.** That is precisely what `funding_mode="required"` exists
to prevent, and it is the likeliest shape of a real bug: wrong symbol mapping, wrong fetch
window, a renamed ticker.

**Soft check (non-fatal):** if the modal event spacing within a coverage window is more than
2x below `max_funding_gap`, set `funding_gap_tolerance_suspicious = True` on the result. This
flags a caller who pasted the wrong venue's cadence.

---

## 8. Equity curve indexing

`equity_curve` is the one series **not** on the returns index:

| row | timestamp | value |
|-----|-----------|-------|
| 0 | `T_0` | `initial_capital` |
| `k` (1 .. n-2) | `T_k` | `NAV_end[k-1]` = `NAV_pre[k]` |
| `n-1` | `T_{n-1}` | `NAV_end[n-2]` |

Therefore, for `n` price bars:

    len(equity_curve) == n
    len(net_returns)  == n - 1 == len(equity_curve) - 1
    n_periods         := len(net_returns)

and `equity_curve[k+1] ≈ equity_curve[k] * (1 + net_return[k])` **to §17 tolerance — not
bitwise.** v1.2 asserted exact equality here; measured failure rate **32.1%** over 200
randomized runs, worst relative error 4.32e-16, because `net_return` is a sum of four
separately-divided components.

Under ruin at period `i`, `equity_curve` truncates to rows `0 .. i+1` with
`equity_curve[i+1] = 0.0`, and `n_periods = i + 1`.

Consequently:

    total_return  = equity_curve[-1] / equity_curve[0] - 1
    cagr          = (equity_curve[-1] / equity_curve[0]) ** (af / n_periods) - 1
    max_drawdown  = min( equity_curve / cummax(equity_curve) - 1 )

Because row 0 is `initial_capital`, a first-period loss is captured. The exponent is
`af / n_periods` where `n_periods = len(equity_curve) - 1`: it counts periods, not
observations.

---

## 9. Counterfactual zero-cost path

Two distinct concepts, named so they cannot be confused:

**A. `gross_return`** (§6.2) — pre-cost return of the **actual** portfolio, an attribution
component. No equity curve, no metrics.

**B. Counterfactual path** — a **separate, complete, independent simulation**.

### 9.1 Counterfactual accounting

Run the full §6 sequence a second time with the same `target_weights`, `rebalance_mask`,
`execution_mode`, `execution_lag`, `initial_capital`, prices and bar index, and with

    fee_cost[i] = 0 ,  slippage_cost[i] = 0 ,  funding_pnl_cash[i] = 0    for all i

The counterfactual maintains its **own** quantity ledger and its **own** NAV path.

**This is not the actual gross returns compounded.** With `NAV_after_cost = NAV_pre`, Step 3
sizes different quantities from the first rebalance onward and the paths diverge. Verified on
the §18.7 fixture: `cumprod(1 + gross_return) -> 1,213,700.965` versus counterfactual equity
`1,214,888.889`.

Outputs: `counterfactual_gross_equity`, `counterfactual_gross_returns`,
`counterfactual_gross_metrics`.

### 9.2 Drag attribution

    total_drag_return = counterfactual_total_return - total_return
    cagr_drag         = counterfactual_cagr         - cagr

These are the measurable total implementation and carry drag, and are the stated purpose of
the counterfactual.

Drag is **positive when costs dominate and legitimately negative when funding is net income**.
It is NOT decomposable into fee/slippage/funding components by differencing counterfactual
paths, because the components interact through the compounding NAV. The additive §6.1 (N-2)
decomposition is the correct tool for component attribution; the counterfactual is the correct
tool for total drag. The result MUST NOT present a component-wise drag breakdown derived
from §9.

Drag fields are populated **only** when both paths span the same number of periods. Otherwise
they are `None` and `drag_comparable = False`.

### 9.3 Strict isolation (owner ruling 4, resolves C4)

**The counterfactual is diagnostic only. It may NEVER modify the actual result's length,
equity, ruin state, or metrics.**

The two paths are fully independent. Each runs over its own full range and each handles ruin
per §6.7 independently:

    ruined                        ruin_timestamp
    counterfactual_ruined         counterfactual_ruin_timestamp

**Invariant, and a mandatory test:** the actual result MUST be bit-identical whether
`compute_counterfactual` is `True` or `False`.

v1.2 truncated both paths to the earlier of the two ruin periods. Because the counterfactual
zeroes funding, it is not strictly better than the actual path when funding is net income.
Verified counterexample: a 2.5x book with funding income, actual equity
`[1e6, 1.225e6, 4.5e5, 5.85e5]` surviving all 3 periods, counterfactual equity
`[1e6, 1e6, 0]` ruining at period 1 — under v1.2 the **surviving** strategy's equity curve,
`total_return`, `cagr`, `max_drawdown` and Sharpe were all silently recomputed over a
truncated sample because a diagnostic twin blew up, and the primary result changed when
`compute_counterfactual` was toggled.

### 9.4 Counterfactual auxiliary behaviour

- §5.5 price validity and §5.3 classification apply identically
- §7.7 funding coverage is **not** validated for the counterfactual: funding is zero by
  construction, so requiring coverage would make a diagnostic fail on data it never uses
- §6.8's tripwire applies and is reported separately as `counterfactual_leverage_breach`
- `unexecuted_rebalances` is identical by construction and is not duplicated
- §6.7's `AccountingError` finiteness guard applies

---

## 10. Result surface

**Equity** (§8 index, `n_periods + 1` rows) — `equity_curve`.

**Per-period series** (returns index, `n_periods` rows, row `i` = period `[T_i, T_{i+1})`) —
`net_returns`, `gross_returns`, `fee_return`, `slippage_return`, `funding_return`, `fee_cost`,
`slippage_cost`, `funding_pnl_cash`, `asset_pnl_cash`, `fee_basis_notional`, `turnover`,
`gross_exposure`, `net_exposure`, `gross_leverage`, `rebalance_flag`.

**Per-period frames** (period x symbol) — `quantity`, `notional`, `positions` (`w_held`),
`pre_trade_weights` (`w_pre`), `target_weights` (as supplied), `trades`, `symbol_state`
(INACTIVE / ENTERING / HELD / EXITING per §5.3).

`quantity`, `notional`, `asset_pnl_cash` and `symbol_state` are exposed because §18 tests must
assert against the public surface, not engine internals.

`rebalance_flag[i]` is the **execution-point** indicator, not the input mask at signal bar `t`.

**Counterfactual** — `counterfactual_gross_equity`, `counterfactual_gross_returns`,
`counterfactual_gross_metrics`, `counterfactual_ruined`, `counterfactual_ruin_timestamp`,
`counterfactual_leverage_breach`, `total_drag_return`, `cagr_drag`, `drag_comparable`.

**Metrics** — `metrics`, per §12, on `net_returns` and `equity_curve`.

**Status and provenance** — `ruined`, `ruin_timestamp`, `uncapped_ruin_return`,
`ruin_decomposition_residual`, `funding_modelled`, `funding_notional_basis`,
`funding_events_excluded`, `funding_gap_tolerance_suspicious`, `liquidation_modelled` (always
`False`), `leverage_breach`, `leverage_breach_timestamps`, `unexecuted_rebalances`,
`provenance`, `provenance_supplied`, `uses_proxy_data`, `config`.

`__repr__` MUST surface `ruined`, `liquidation_modelled` and `uses_proxy_data`.

---

## 11. Units and error behaviour

### 11.1 Unit naming (NORMATIVE)

| suffix | unit | sign |
|--------|------|------|
| `_cost` | USD cash, absolute | `>= 0` |
| `_pnl` / `_pnl_cash` | USD cash, signed | signed |
| `_return` | fraction of `NAV_pre[i]` | signed |
| `_bps` | basis points, 1e-4 | config input |
| `quantity` | units of the instrument | signed |
| `notional` | USD | signed |
| weights, exposures, turnover | fraction of NAV | signed except turnover |

A cost and its return counterpart carry **opposite signs by construction**. Any field named
`_pnl` returning a ratio is a defect.

### 11.2 Must raise

Specific types — `DataIntegrityError`, `InvalidPriceError` (subclass), `MissingPriceError`
(subclass), `FundingDataError`, `ConfigError`, `AccountingError` — never bare `ValueError`.

Data integrity: duplicate `(timestamp, symbol)`; non-monotonic timestamps; timezone-naive
timestamps; irregular grid (§2.1); `n < 2`; a symbol in `target_weights` absent from market
data; `rebalance_mask` absent or misaligned; `NaN` target weight on a rebalance bar; a symbol
with `quantity[i-1, j] != 0` absent or `NaN` in `target_weights[t]` (§5.4);
`max_funding_gap <= 0`.

Price validity (§5.5): any price in use that is not finite and strictly `> 0`, for any of the
uses enumerated in §5.3's table. This covers opening, valuing, holding and **closing** a
position, and the **next-period** price of any held position.

Funding: coverage failures (§7.7); missing or invalid `notional_price` under
`funding_notional_basis = "event_price"` (§7.6).

Config: `execution_lag < 1`; missing `fee_bps`, `slippage_bps`, `funding_mode`, `frequency`,
`annualization_factor`; `funding_notional_basis` absent under `funding_mode = "required"`.

Accounting: non-finite `NAV_end` (§6 Step 7).

### 11.3 May proceed silently

Only INACTIVE symbols (§5.3): `quantity[i-1, j] == 0`, `not will_hold[i, j]`. Their prices may
be absent, `NaN`, zero or negative and are never read. This is the staggered-listing and
post-exit case, and it is the **only** silent path.

`NaN` target weights on non-rebalance bars (ignored by §3).

### 11.4 Delisting and renames are not silently handled

The engine raises when a held position loses a required price. It does **not** auto-liquidate
at a stale price and does not carry positions forward at last value.

Handling a real delisting requires deciding *when the strategy could have known* — a universe
question with direct lookahead consequences, owned by QR-DATA-001 and the strategy. The
correct pattern: the strategy emits a target of `0` on a rebalance bar where a valid price
still exists, making the symbol EXITING (which requires only `P[i]`) and then INACTIVE.

**Symbol renames** currently land on the delisting path and will raise. This is deliberate —
silently mapping a renamed ticker is a survivorship-bias vector — but symbol-identity
resolution belongs to QR-DATA-001 and must happen before data reaches the engine.

---

## 12. Metrics

`af` = `annualization_factor`. `n_periods` = `len(net_returns)` per §8.

    total_return          = equity_curve[-1] / equity_curve[0] - 1
    cagr                  = (equity_curve[-1] / equity_curve[0]) ** (af / n_periods) - 1
    annualized_volatility = std(net_returns, ddof=1) * sqrt(af)

    ann_excess_arith      = mean(net_returns - risk_free_per_period) * af
    sharpe                = ann_excess_arith / annualized_volatility

    downside_dev_ann      = sqrt( mean( min(net_returns - mar_per_period, 0) ** 2 ) ) * sqrt(af)
    ann_excess_mar_arith  = mean(net_returns - mar_per_period) * af
    sortino               = ann_excess_mar_arith / downside_dev_ann

    max_drawdown          = min( equity_curve / cummax(equity_curve) - 1 )   # NEGATIVE
    calmar                = cagr / abs(max_drawdown)

    avg_turnover          = mean(turnover)
    annualized_turnover   = mean(turnover) * af

### 12.1 Annualization

Sharpe and Sortino share the form `arithmetic-annualized excess / annualized dispersion`; both
reduce to `mean/dispersion * sqrt(af)`. Independently verified as dimensionally sound.

- **Sharpe and Sortino use ARITHMETIC annualization** — their denominators are distributional
  dispersion measures of per-period returns, so the numerator must be the matching per-period
  moment scaled by `af`.
- **Calmar uses GEOMETRIC (CAGR)** — max drawdown is a realized-path quantity, not a
  distributional moment.
- The field is named `cagr`. There is **no** field named `annualized_return`.

### 12.2 Other pinned choices

- volatility uses SAMPLE std, `ddof=1`
- downside deviation divides by the count of **all** periods, not only losing periods.
  Consequently Sortino and Sharpe use different estimators and do **not** coincide on a
  symmetric distribution. No identity relating them is asserted anywhere in this document
  (§18.6).
- max drawdown is signed negative
- `risk_free_per_period` and `mar_per_period` are **scalars**, default 0.0. The Sharpe
  denominator uses `std(net_returns)` rather than `std(net_returns - rf)`, correct only
  because `rf` is constant. If either becomes a series, both denominators must change.

### 12.3 Degenerate cases

Return `nan` — never 0, never an exception: `annualized_volatility == 0` -> `sharpe`;
`downside_dev_ann == 0` (no period below `mar`) -> `sortino`; `max_drawdown == 0` -> `calmar`;
`n_periods < 2` -> every dispersion-based metric.

### 12.4 Metrics under ruin

Metrics use the actual observations through the ruin period. No padding (§6.7).

- `total_return = -1.0` exactly (`equity_curve[-1] = 0`)
- `max_drawdown = -1.0` exactly
- `cagr = -1.0` exactly. Verified: `0.0 ** (af/n_periods)` returns `0.0` with no domain error
  and no `nan` for `(af, n_periods)` in `(365,1), (8760,1), (365,730), (8760,3), (2190,5)`
- `calmar = -1.0`
- `sharpe`, `sortino`, `annualized_volatility` computed normally over the truncated series
  including the terminal `-1.0`, subject to §12.3
- ruin at period 0 -> `n_periods == 1` -> dispersion metrics `nan`, no exception

Arithmetically well defined, **not** economically meaningful. Always presented alongside
`ruined = True`.

---

## 13. Data provenance

The engine does not build provenance and does not validate its content beyond the structural
rule below. It MUST NOT discard provenance supplied to it. Population is QR-DATA-001's job.

    DatasetProvenance:
        source_venue        : str | None       # "hyperliquid", "binance", ...
        field_type          : str | None       # "ohlcv", "funding_rate", "oracle_price", ...
        time_range          : (start, end) | None   # tz-aware UTC
        native_or_proxy     : "native" | "proxy" | None
        proxy_for           : str | None       # MANDATORY when native_or_proxy == "proxy"
        dataset_id          : str | None
        dataset_version     : str | None
        processing_version  : str | None
        retrieval_date      : date | None
        symbol_mapping      : str | None
        notes               : str | None

`field_type` and `time_range` are present because CLAUDE.md requires both at minimum, and v1.2
omitted them.

Carried on `MarketData.provenance` and `FundingEvents.provenance`.
`BacktestResult.provenance` is a mapping with at minimum `"price"` and `"funding"`, holding
the supplied objects verbatim.

Engine obligations:

1. never drop or overwrite supplied provenance
2. absent provenance -> `provenance_supplied = False`, so absence is visible rather than
   assumed benign
3. any `native_or_proxy == "proxy"` -> `uses_proxy_data = True`, surfaced in `__repr__` and
   any summary
4. `native_or_proxy == "proxy"` with `proxy_for` empty or `None` -> `DataIntegrityError`. A
   disclosure that discloses nothing is worse than none, because it satisfies a checkbox

Obligations 3 and 4 close a direct CLAUDE.md violation: §7.1 permits proxy funding datasets,
and without them a result could present Binance funding as if it were Hyperliquid funding.

---

## 14. Liquidation scope

**Liquidation and margin are NOT modelled.** `liquidation_modelled = False`, always.

Correct arithmetic for `gross_exposure > 1` does **not** mean a position was survivable
on-venue. A v1 backtest can report a smooth equity curve through a path that would have been
liquidated: no margin ratio, no maintenance margin, no auto-deleveraging, no funding-driven
margin call, no liquidation penalty.

Any result at meaningful leverage is an **upper bound** on achievable performance. Margin and
liquidation validation belong to a later execution/risk layer. §6.8's tripwire is a reporting
aid, not a risk model.

---

## 15. Config

`BacktestConfig` — immutable (frozen dataclass), self-validating:

| field | default | notes |
|-------|---------|-------|
| `initial_capital` | 1_000_000.0 | |
| `frequency` | REQUIRED | `"1h"`, `"4h"`, `"1d"`; defines `Δ` |
| `fee_bps` | **REQUIRED, no default** | never hardcode Hyperliquid fees |
| `slippage_bps` | **REQUIRED, no default** | |
| `execution_mode` | `"next_open"` | `next_open` \| `next_close` |
| `execution_lag` | 1 | must be `>= 1` |
| `funding_mode` | **REQUIRED, no default** | `required` \| `disabled` |
| `funding_notional_basis` | REQUIRED iff `funding_mode == "required"` | `event_price` \| `period_start` |
| `annualization_factor` | REQUIRED | 8760 (1h), 2190 (4h), 365 (1d) |
| `risk_free_per_period` | 0.0 | scalar |
| `mar_per_period` | 0.0 | scalar |
| `max_gross_leverage` | `None` | tripwire only |
| `compute_counterfactual` | `True` | must not affect the actual result (§9.3) |

`max_funding_gap` is **not** a config field — it lives on `FundingCoverage` (§7.2), because it
is a venue/dataset property, not a backtest choice.

---

## 16. Determinism

Identical inputs MUST produce bit-identical outputs. No RNG. No reliance on dict or set
iteration order. Symbols sorted deterministically. Running the same backtest twice MUST
compare exactly equal.

---

## 17. Floating-point policy (owner ruling 6, resolves C6)

**Exact equality (`==`) is permitted ONLY for genuinely discrete state:**

- booleans and flags (`ruined`, `rebalance_flag`, `leverage_breach`, `drag_comparable`)
- boolean masks and `symbol_state` classifications
- indices, timestamps, lengths, and event counts (`funding_events_excluded`)
- **quantities carried forward unchanged** across a non-rebalance period (§5.1 — stored state,
  bitwise by construction)
- **quantities and weights assigned literal `0.0`** for INACTIVE / zero-target symbols (§5.1,
  §5.2 — assigned, not computed)
- values that are exactly representable *and* reached by a single arithmetic path

**Documented tolerances are REQUIRED for everything reached by a different but mathematically
equivalent floating-point path:**

| quantity | tolerance |
|----------|-----------|
| NAV identity (N-1), equity recursion §8 | `rtol=1e-12`, `atol=1e-9` (USD) |
| return decomposition (N-2) | `rtol=1e-12`, `atol=1e-15` |
| reconstructed / derived weights, exposures | `rtol=1e-12`, `atol=1e-15` |
| turnover and cost assertions | `rtol=1e-12`, `atol=1e-15` |
| metrics | `rtol=1e-10` |

Measured evidence: the equity recursion failed bitwise on **32.1%** of periods (worst relative
error 4.32e-16) because `net_return` is a sum of four separately-divided components; the
`w_held` rebalance branch failed bitwise on **13.9%** of entries. Both are correct engines
failing an incorrect test. This is the third occurrence of this defect class across spec
revisions, so the policy is stated once, centrally, and normatively.

---

## 18. Mandatory tests

Deterministic synthetic datasets. Every numeric assertion MUST show its arithmetic in the test
docstring. No test may assert merely "runs without error". §17 governs `==` versus tolerance
throughout.

Values marked **[computed]** were calculated by the Research Lead and are reproduced here to
be **independently re-derived by the auditor**, not accepted on authority.

### 18.1 Anti-lookahead

**Test E — lag discrimination.** Single symbol, `fee_bps = 0`, `slippage_bps = 0`,
`funding_mode = "disabled"`, `initial_capital = 1_000_000`, `next_open`, target weight `1.0`,
`rebalance_mask` True at **bar 2 only**.

    open = [100, 100, 100, 200, 200, 200]        # 6 bars, 5 holding periods
    holding-period returns = [0, 0, 1.0, 0, 0]

    execution_lag = 1  ->  final NAV == 1_000_000     [computed]
    execution_lag = 0  ->  final NAV == 2_000_000     [computed]

Both assertions are mandatory. The `lag = 0` branch is reached by direct construction or a
test-only bypass of §4.2's `ConfigError`, purely to prove the test discriminates. A test that
checks only the `lag = 1` value cannot demonstrate it would catch the bug.

**Test E2 — execution mode discrimination.** The two modes must give different answers on the
same data, which requires open and close to diverge. Mask True at bar 2, `execution_lag = 1`:

    open  = [100, 100, 100, 100, 200, 200]    -> r_open  = [0, 0, 0, 1.0, 0]
    close = [100, 100, 100, 200, 200, 200]    -> r_close = [0, 0, 1.0, 0, 0]

    next_open   ->  final NAV == 2_000_000     [computed]
    next_close  ->  final NAV == 1_000_000     [computed]

v1.2's E2 reused test E's array, on which both modes give identical results — it tested
nothing.

**Test E3 — `execution_lag = 2`.** Mask True at bar 2, `next_open`:

    open = [100, 100, 100, 200, 400, 400, 400]    # 7 bars
    holding-period returns = [0, 0, 1.0, 1.0, 0, 0]

    execution_lag = 0  ->  final NAV == 4_000_000     [computed]
    execution_lag = 1  ->  final NAV == 2_000_000     [computed]
    execution_lag = 2  ->  final NAV == 1_000_000     [computed]

Three distinct values, so lag 2 is distinguishable from both lag 1 and lag 0. On v1.2's array
`lag=1` and `lag=2` both gave 1,000,000, so the mandatory lag-2 test could not discriminate —
and §4.5 makes E3 the test guarding the zero-latency assumption.

**Test E4.** No output at period `i` depends on any price after `P[i+1]`: perturb a late price
and assert earlier periods are bit-identical.

### 18.2 Core accounting

| id | requirement |
|----|-------------|
| A | zero positions -> zero PnL, zero fees, zero turnover |
| B | constant long -> expected compounded PnL |
| C | constant short -> expected inverse directional PnL |
| D | multi-asset long/short aggregation |
| G | `0 -> +1` produces turnover 1 (§17 tolerance) |
| H | `+1 -> -1` produces turnover 2 (§17 tolerance) |
| I | fees produce exactly the expected cost (§17 tolerance) |
| J | slippage produces exactly the expected cost (§17 tolerance) |
| L | gross exposure |
| M | net exposure |
| N | `gross_leverage > 1` |
| P | determinism: two runs compare exactly equal |

### 18.3 Rebalance and quantity ledger

| id | requirement |
|----|-------------|
| R1 | a rebalance followed by six non-rebalance bars produces **exactly zero** interim turnover and fees, under **trending** prices |
| R2 | identical targets repeated on non-rebalance bars produce no trade; the same targets under `rebalance_every_bar` DO produce drift-correcting trades. Both in one test |
| R3 | weights drift between rebalances while quantity is constant |
| R4 | `quantity[i] == quantity[i-1]` **exactly** (`==`, per §17) on every non-rebalance period across a long trending span, including INACTIVE symbols whose stored quantity is literal `0.0` |
| R5 | quantity changes **only** where `rebalance_flag` is true |

### 18.4 NAV consistency

| id | requirement |
|----|-------------|
| N1 | hand-computed case where **funding alone** changes NAV; next period's `w_pre` matches. Must fail if funding is omitted from `NAV_end` |
| N2 | same for fees and slippage |
| N3 | identity (N-1) on a randomized multi-asset case with all four cost components nonzero, at §17 tolerance |
| N5 | `equity_curve[k+1] ≈ equity_curve[k] * (1 + net_return[k])` at §17 tolerance — **not** `==` |
| N6 | `abs(NAV_after_cost - NAV_pre/(1 + turnover*bps)) <= NAV_pre * (turnover*bps)**2` |
| N7 | decomposition (N-2) sums to `net_return` on all non-ruin periods, at §17 tolerance |

### 18.5 Funding

| id | requirement |
|----|-------------|
| F1 | 24 hourly events inside one 1d bar aggregate to the sum of 24 charges — must fail if the engine assumes one event per bar |
| F2 | an event exactly on a boundary is counted once, in the later period |
| F4 | `funding_mode="required"` with genuinely absent funding data raises `FundingDataError` |
| F5 | `funding_mode="disabled"` -> funding exactly 0, `funding_modelled=False` |
| F6 | sign: long + positive rate -> negative funding PnL; short + positive rate -> positive |
| F7 | irregular event spacing within tolerance aggregates correctly |
| F8 | `next_open` vs `next_close` on an identical event stream produce different, documented windows. Must fail if `T_i` ignores `execution_mode` |
| F9 | 1h bars, complete 8h stream, `max_funding_gap=8h` does **not** raise |
| F10 | events before `T_0` or at/after `T_{n-1}` are excluded and counted |
| F12 | funding on a rebalance period is valued on the **post-trade** quantity |
| F13 | a gap exceeding `max_funding_gap` inside a coverage window raises |
| F14 | a symbol with zero exposure throughout needs no funding data |
| F15 | **coverage false-accept**: events only outside `[coverage_start, coverage_end]` — the `{2025-01-01, 2027-01-01}` counterexample — MUST raise |
| F16 | **non-contiguous exposure** with two disjoint coverage records does **not** raise |
| F17 | a gap exactly equal to `max_funding_gap` is accepted |
| F18 | `basis="event_price"` with any missing/invalid `notional_price` on an applied event raises |
| F19 | `basis="period_start"` ignores `notional_price` entirely — present-but-invalid values do not raise and do not affect the result |
| F20 | modal spacing far below `max_funding_gap` sets `funding_gap_tolerance_suspicious` without raising |

(v1.2's F3 and F11 are **deleted**: mixed per-event basis no longer exists.)

### 18.6 Metrics (owner ruling 5)

**M7 is deleted and MUST NOT be reintroduced in any form.** No test may assert a derived
identity relating Sharpe and Sortino. v1.2's M7 asserted
`sortino/sharpe == sqrt(2)*sqrt((n-1)/n)`; it was `0/0` for any fixture satisfying its own
premise (a symmetric series has mean exactly 0, so both ratios are 0), and the only
well-defined form of that ratio is `sqrt(2)*sqrt(n/(n-1))` — inverted, a 12.5% error at n=8.
It was imported from a prior audit's recommendation without re-derivation.

**Pinned fixture**, used by M1, M2 and M5. `af = 365`, `risk_free_per_period = 0`,
`mar_per_period = 0`, `initial_capital = 1_000_000`:

    net_returns = [0.010, -0.005, 0.020, -0.015, 0.000, 0.008, -0.012, 0.006]
    n_periods   = 8   (3 negative periods)

Intermediates and expected values **[computed]**, to be independently re-derived:

    mean(net_returns)      = 0.0015
    std(net_returns,ddof=1)= 0.011807987611298186
    annualized_volatility  = 0.22559128655918556
    M1  sharpe             = 2.4269554394174677

    mean(min(r,0)**2)      = 4.9250000000000004e-05
    downside_dev_ann       = 0.13407553841025588
    M2  sortino            = 4.0835189363529718

    equity_curve[-1]       = 1011570.8691663996
    M5  total_return       = 0.011570869166399600
        cagr               = 0.690272927570154
        max_drawdown       = -0.019034560000000034

| id | requirement |
|----|-------------|
| M1 | `sharpe` on the pinned fixture equals the literal value above, §17 metric tolerance |
| M2 | `sortino` on the pinned fixture equals the literal value above, §17 metric tolerance |
| M3 | every §12.3 degenerate case returns `nan`, not 0, not an exception |
| M4 | `cagr` differs from arithmetic annualization on a volatile series, guarding a silent swap |
| M5 | `total_return`, `cagr`, `max_drawdown` on the pinned fixture equal the literals above, pinning the §8 index convention and the `af/n_periods` exponent |
| M6 | `max_drawdown` captures a first-period loss below `initial_capital` |

### 18.7 Counterfactual

**Pinned CF3 fixture**, 2 symbols, `fee_bps = 50`, `slippage_bps = 50`, funding disabled,
`initial_capital = 1_000_000`, `next_open`, `execution_lag = 0` for fixture simplicity
(constructed directly, not via config):

    P     = [[100, 50], [110, 45], [121, 40]]
    mask  = [True, True, False]
    W     = [[0.6, -0.4], [0.6, -0.4], [0, 0]]

Expected **[computed]**:

    actual equity          = [1_000_000, 1_089_000, 1_201_865.28]
    counterfactual equity  = [1_000_000, 1_100_000, 1_214_888.888888889]
    gross_return           = [0.099, 0.10436848...]
    cumprod(1+gross)*1e6   = 1_213_700.965      != counterfactual 1_214_888.889

| id | requirement |
|----|-------------|
| CF1 | zero fees, zero slippage, funding disabled -> counterfactual equity equals actual equity |
| CF2 | with nonzero costs and funding a net cost, `counterfactual_gross_equity[-1] > equity_curve[-1]` and `total_drag_return > 0`. With funding as net **income**, drag is legitimately **negative** — asserted in the same test |
| CF3 | on the pinned fixture, `cumprod(1+gross_return)` does **not** equal counterfactual equity — the §9.1 distinction is real and the counterfactual must never be "simplified" into a cumulative product |
| CF4 | hand-computed 3-bar 2-asset counterfactual, all values written out |
| CF5 | counterfactual respects the same execution timing and rebalance mask |
| CF6 | **actual ruins, counterfactual survives**: counterfactual retains its full length |
| CF7 | **counterfactual ruins, actual survives**: the actual result retains full length, equity, ruin state and metrics. Pinned fixture below |
| CF8 | **isolation invariant**: the actual result is bit-identical with `compute_counterfactual` `True` and `False` |

**Pinned CF7 fixture** — 1 symbol, 2.5x long, funding **income** (negative rate), no costs:

    P    = [[100], [100], [60], [60]] ,  mask = [True, F, F, F] ,  W = 2.5
    funding_rate = -0.09 per period (long receives)

    actual         equity = [1_000_000, 1_225_000, 450_000, 585_000]   ruined = False   [computed]
    counterfactual equity = [1_000_000, 1_000_000, 0]                  ruined = True    [computed]

The actual result MUST retain all 4 equity observations.

### 18.8 Ruin

**Pinned X1 fixture** — 1 symbol, 3x long, `fee_bps = 10`, `slippage_bps = 10`, funding
disabled, mask True at bar 0 only:

    P = [[100], [100], [60]]

Expected **[computed]**:

    equity_curve   = [1_000_000, 994_000, 0]
    net_returns    = [-0.006, -1.0]
    ruined = True ,  ruin_timestamp = T_2
    uncapped_ruin_return        = -1.2
    ruin_decomposition_residual = -0.2
    total_return = -1.0 , max_drawdown = -1.0 , cagr = -1.0 , calmar = -1.0

| id | requirement |
|----|-------------|
| X1 | the pinned fixture reproduces every value above |
| X2 | series are **truncated**, not padded: `len(net_returns) == ruin_period + 1`, `len(equity_curve) == ruin_period + 2` |
| X3 | no `inf` or `NaN` anywhere in any output frame after ruin |
| X4 | `total_return`, `max_drawdown`, `cagr`, `calmar` all exactly `-1.0` |
| X5 | ruin at period 0 -> `n_periods == 1`, dispersion metrics `nan`, no exception |
| X6 | identity **(N-1) only** holds at the ruin period. This test MUST NOT assert (N-2); it MUST instead assert `ruin_decomposition_residual == -0.2` on the pinned fixture |
| X7 | `ruined=True` appears in `__repr__` |
| X8 | near-ruin (`NAV_end` small but positive) produces no `inf`; `leverage_breach` fires when `max_gross_leverage` is set |
| X9 | **`NAV_after_cost <= 0`** (turnover 4 at 3000 total bps) ruins at Step 2, **before sizing**: `equity_curve = [1_000_000, 0]`, `net_returns = [-1.0]`, and no negative quantity is ever formed **[computed]** |

### 18.9 Symbol activity, boundaries, missing data

| id | requirement |
|----|-------------|
| S1 | **staggered listing**: symbol B has no price for the first half of the sample and zero weight there; the backtest completes, `np.isfinite(equity_curve).all()` is `True`, and `asset_pnl_cash` equals the hand-computed single-symbol value. Must fail if inactive symbols are neutralised by `0 * NaN` |
| S2 | **delisting**: a held symbol losing `P[i+1]` raises `MissingPriceError` |
| S3 | closing a position (`q_prev != 0`, target `0`) at an invalid execution price raises |
| S4 | EXITING symbol does **not** require `P[i+1]`: valid `P[i]`, absent `P[i+1]`, target 0 -> completes |
| S5 | `symbol_state` matches §5.3's table for every (period, symbol) on a fixture exercising all four states |
| S6 | INACTIVE symbols with `NaN`, `0.0` and negative prices all proceed silently and contribute exactly zero |
| V1 | zero price on a symbol in use raises `InvalidPriceError` |
| V2 | negative price on a symbol in use raises `InvalidPriceError` |
| V3 | denormal/near-zero positive price does not silently produce `inf` quantity |
| T1 | rebalance flagged at `t > n-2-execution_lag` -> no trade, no crash, recorded in `unexecuted_rebalances` |
| T2 | terminal bar: `len(net_returns) == n-1`, `len(equity_curve) == n`, no `NaN` in equity |
| T3 | two-bar backtest returns one period; one-bar raises `ConfigError` |
| T4 | `execution_instant()` unit-tested against the §4.3 table for both modes |
| U1 | symbol with nonzero quantity absent from **`target_weights[t]`**, `t = i - execution_lag`, raises `DataIntegrityError` |
| U2 | symbol entering mid-sample proceeds silently |
| U3 | symbol absent from target columns with zero quantity is treated as target 0, no trade |
| D4 | irregular bar grid raises `DataIntegrityError` naming the offending pair and expected Δ |

### 18.10 Provenance

| id | requirement |
|----|-------------|
| PR1 | supplied provenance appears on the result unmodified, field for field, including `field_type` and `time_range` |
| PR2 | absent provenance -> `provenance_supplied == False` |
| PR3 | any `native_or_proxy == "proxy"` -> `uses_proxy_data == True`, surfaced in `__repr__` |
| PR4 | `native_or_proxy == "proxy"` with `proxy_for` `None` or empty raises `DataIntegrityError` |

### 18.11 Coverage

Both `execution_mode` values across the engine suite. Every exception path in §11.2. Every
config validation in §15. Every state in §5.3.

---

## 19. Out of scope for QR-INFRA-001

No market data ingestion. No alpha. No strategy implementations beyond synthetic test
fixtures. No margin or liquidation modelling. No provenance population (QR-DATA-001). No
symbol-identity resolution. No live trading, keys, orders, withdrawals or transfers.

---

## 20. Resolved design decisions

**20.1 — Costs on `NAV_pre`, sizing on `NAV_after_cost`. ACCEPTED.** The closed form is what
makes (N-1) exact. The `O(bps^2)` bound applies to **NAV**, not the fee: the fee differs from
the exact self-consistent solve by `O(turnover * bps)` relative — measured **2.00%** at 50 bps
with turnover 4. Test N6 pins the NAV bound.

**20.2 — Funding fallback `period_start`; start/end averaging REJECTED** (lookahead, §7.6).
Now a run-level mode rather than a per-event fallback.

**20.3 — Portfolio-level rebalance mask only in v1. ACCEPTED** with §3.1's limitations
documented and `exit_unnamed=True` supplying the ergonomics.

**20.4 — `gross_leverage` as an alias of `gross_exposure`. ACCEPTED**, renamed and
docstring-guarded.

**20.5 — Ruin floors at 0 and terminates. ACCEPTED**, with §6.7 and §12.4 specifying both
detection points, the (N-2) breakdown and the residual.

**20.6 — §5.4 raises rather than warning-and-flattening. ACCEPTED.** A warning that defaults
to flat is the silent behaviour that produced the original 0.4-turnover ambiguity and would
let a delisting liquidate at whatever price happened to be present. Ergonomics are solved
upstream by `exit_unnamed=True` (§3), which makes the exit visible in the strategy's own
output.

**20.7 — §2.1 regular grid required, no config tolerance. ACCEPTED.** QR-DATA-001 owns
gap-filling for real venue outages.

**20.8 — `max_funding_gap` declared, never inferred. ACCEPTED**, and relocated from
`BacktestConfig` to `FundingCoverage` (§7.2) as a venue/dataset property.

---

## 21. Open questions for the auditor

1. §9.4 exempts the counterfactual from §7.7 funding-coverage validation on the grounds that
   it never uses funding. Is that exemption safe, or does it let a coverage defect hide?
2. §7.7 condition 2 requires an exposed interval to lie inside a **single** coverage record.
   Should adjacent records be allowed to compose, or does that reintroduce gap ambiguity at
   the seam?
3. §17's tolerances are set ~3–4 orders of magnitude above measured error. Too loose to catch
   a real accounting defect?
4. §5.3 classifies EXITING as not requiring `P[i+1]`. Confirm no aggregate reads `P[i+1]` for
   an exiting symbol.
