# Backtest Contract — SPECIFICATION v1.4 (QR-INFRA-001)

Status: **UNDER REVIEW — NOT FROZEN.** Awaiting audit of D1–D4, W1/W3/W5/W10 and regression.
Owner: Research Lead. Normative file. History preserved under `docs/spec_history/`.
No implementation may begin until this document is marked FROZEN by the platform owner.

This document is normative. Where this document and intuition disagree, this document wins.

**v1.4 is a NARROW CORRECTIVE REVISION.** The accounting core, timing model, quantity ledger,
symbol classification, price validity rules, funding architecture and metric definitions are
unchanged from v1.3 and were verified correct at audit. Only D1–D4 and the listed warnings are
addressed. No new architecture has been introduced beyond the minimum required.

---

## 0. Revision history

Snapshots are preserved before replacement and MUST NOT be overwritten:

    docs/spec_history/backtest_contract_v1.3.md

| version | verdict | outcome |
|---------|---------|---------|
| v1.0 | rejected by owner | implicit rebalancing; inconsistent drift; one-funding-event-per-bar; unit-mislabelled fields |
| v1.1 | SPEC FAIL | 12 blocking (B1–B12). Core verified 9.9e-17; all defects at boundaries or in tests |
| v1.2 | SPEC FAIL | 7 blocking (C1–C7). Core verified 3.56e-16 |
| v1.3 | SPEC FAIL | 4 blocking (D1–D4). **All 8 pinned fixtures verified exact.** C1, C2, C3, C5, C7 resolved |
| v1.4 | this document | D1–D4 + W1/W3/W5/W10 + all correctness-affecting warnings |

### v1.3 -> v1.4 changes

| ref | defect | resolution | § |
|-----|--------|------------|---|
| D1 | X6 asserted `residual == -0.2`; true value `-0.19999999999999996`, failing 100% of the time | X6 is tolerance-based; **every** §18 numeric assertion mechanically re-classified in §18.0 | §18.0, §18.8 |
| D2 | Counterfactual exceptions could abort the actual run; CF8's invariant unachievable | Counterfactual runs behind an exception barrier; `counterfactual_status`; actual path authoritative | §9.5 |
| D3 | X1 and CF7 pinned at an undisclosed `execution_lag = 0`, unreproducible under config default | Every fixture re-pinned at production-valid config with `execution_mode` and `execution_lag` stated | §18.0.2 |
| D4 | Step-2 ruin row entirely undefined | Complete terminal-row definition; two ruin stages; explicit position convention | §6.7 |
| W1 | §17 metrics row had no `atol`; could not compare a true value of 0 | `atol=1e-12` added | §17 |
| W3 | N6 was satisfied by both the specified formula and the one it excludes | Replaced by a discriminating equality assertion | §18.4 |
| W5 | §6 Step 0 referenced `w_target[i]` before Step 1 defines it | Step 0 resolves `t = i - execution_lag` itself | §6 |
| W10 | No universe/survivorship disclosure, though CLAUDE.md ranks it with proxy data | `UniverseProvenance` + `survivorship_safe` on the result | §13.2 |
| W2 | §17 did not clearly govern pinned literals in §18 | Stated normatively | §18.0 |
| W4 | `P[i+1]` validated before the Step-2 ruin check could fire | `P[i+1]` validation deferred until after Step 2 | §6 |
| W6 | Coverage seam false-reject | Records must be pairwise disjoint with non-intersecting closures; loader merges | §7.2 |
| W9 | All-`None` provenance satisfied the disclosure checkbox | `provenance_complete`; required core fields | §13.1 |
| W11 | `ruin_timestamp = T_{i+1}` unexplained at a cost-stage ruin | Explained normatively | §6.7 |
| W12 | `gross_returns`/`gross_return` naming; `NAV_pre` not on the surface | Names reconciled; NAV derivation stated | §10 |
| W13 | `notional` computed rather than assigned for zero quantity | Assign-`0.0` rule extended | §5.6 |
| W14 | Nine tests carried no arithmetic | All now pinned or explicitly parameterised | §18.2, §18.6 |
| W15 | X8 bundled two unrelated assertions | Split into X8a / X8b | §18.8 |
| W17 | Seven missing tests | Added: CF9, CF10, R6, T5, P2, X10, S7 | §18 |

**Remaining warnings, documentation-only, accepted and NOT fixed:**

- **W7** — a loader declaring `max_funding_gap = 8h` for an hourly-funding venue with 7 of 8
  events missing passes §7.7 condition 3 silently. Intrinsic to trusting declared metadata,
  which remains the right call versus inference. Disclosed in §7.2.
- **W8** — §7.7 condition 3 spans a whole coverage window, so a genuine venue outage in an
  unexposed stretch of a single declared record raises. Mitigated by multi-record declaration.
- **W16** — §4.4's note that `i = 0` is unreachable is stated for `execution_lag = 1`; with
  lag 2, `i = 1` is also unreachable. Cosmetic.

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
`config.frequency`. Irregular spacing, missing bars, or a regular spacing that **disagrees
with `frequency`** MUST raise `DataIntegrityError` naming the first offending timestamp pair
and the expected `Δ`.

QR-DATA-001 owns gap-filling and segmenting policy for real venue outages.

---

## 3. Strategy output contract

    StrategyOutput:
        target_weights : DataFrame  (index = bar label, columns = symbol)
        rebalance_mask : Series     (index = bar label, dtype = bool)

`rebalance_mask` is **REQUIRED**, no default. The engine MUST NOT infer rebalance intent by
comparing consecutive target weights. Only the mask decides.

| `rebalance_mask[t]` | meaning |
|---------------------|---------|
| `True`  | `target_weights[t]` becomes the active target, eligible for execution at `t + execution_lag`. A trade occurs. |
| `False` | No new target is issued. Held **quantities** are unchanged. Zero turnover, zero cost. `target_weights[t]` is ignored entirely. |

`NaN` in `target_weights` is permitted on non-rebalance bars and is an error on rebalance bars.

Named constructors, no bare default:

    StrategyOutput.rebalance_every_bar(weights)
    StrategyOutput.rebalance_on_dates(weights, dates)
    StrategyOutput.rebalance_on_dates(weights, dates, exit_unnamed=True)
    StrategyOutput.rebalance_on_change(weights)   # opt-in, never automatic

`exit_unnamed=True` materialises explicit `0.0` targets for held symbols the strategy did not
name, **in the strategy's own output frame**, before it reaches the engine — the sanctioned
ergonomic answer to §5.4's strictness.

### 3.1 Mask granularity (v1 restriction)

Portfolio-level mask only. Supports pairs, cross-sectional ranking and market-neutral books.
Does **not** support per-leg band rebalancing or overlapping-tranche portfolios. The tranche
workaround (separate backtests, averaged) is valid **only when costs are not netted across
tranches**, which overstates turnover; research using it must say so.

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
sorted, unique and regular. Bare `.shift()` on an unverified frame is NOT acceptable.

### 4.3 Execution instant `T_i`

    T_i = t_i            if execution_mode == "next_open"
    T_i = t_i + Δ        if execution_mode == "next_close"

MUST be an explicit, separately tested function `execution_instant(bar_label, mode, delta)`.

| i | `t_i` | `T_i` (`next_open`) | `T_i` (`next_close`) |
|---|-------|---------------------|----------------------|
| 0 | 2026-01-01 00:00 | 2026-01-01 00:00 | 2026-01-02 00:00 |
| 1 | 2026-01-02 00:00 | 2026-01-02 00:00 | 2026-01-03 00:00 |
| 2 | 2026-01-03 00:00 | 2026-01-03 00:00 | 2026-01-04 00:00 |

Holding period `i` spans `[T_i, T_{i+1})`: bar `i`'s own span under `next_open`, bar `i+1`'s
span under `next_close`. Getting this wrong misattributes funding by a full bar (measured 10x).

### 4.4 Sample boundaries

Given `n` price bars indexed `0 .. n-1`:

- **Holding periods**: `i = 0 .. n-2`, exactly `n - 1`. Period `i` spans `[T_i, T_{i+1})` and
  earns the move from `P[i]` to `P[i+1]`.
- **Tradeable execution points**: `i = execution_lag .. n-2`.
- **Terminal valuation instant**: `T_{n-1}`. No trade occurs and bar `n-1` earns no return.

A rebalance flagged at `t` with `t + execution_lag > n - 2` MUST be recorded in
`unexecuted_rebalances`, not raised.

`n < 2` MUST raise `ConfigError`.

### 4.5 Zero-latency assumption

Under `next_open` with contiguous bars, the information instant (`close[t]`) and the execution
instant (`open[t+1]`) are the **same instant**. This is the standard convention and the
tightest defensible assumption, but it grants zero decision, transmission and matching latency.

Stated as an assumption, not a proof of lookahead-freedom. Strategies sensitive to sub-bar
latency MUST use `execution_lag >= 2` or `next_close` and must say so.

---

## 5. Position state, symbol activity, price validity

### 5.1 Quantity is primary

    at a rebalance execution point i, for each symbol j:
        if w_target[i, j] == 0:  quantity[i, j] = 0.0            # assigned, not computed
        else:                    quantity[i, j] = w_target[i, j] * NAV_after_cost[i] / P[i, j]

    at a non-rebalance point i:
        quantity[i, j] = quantity[i-1, j]                        # exact carry-forward

    initial:  quantity[-1, j] = 0.0

The non-rebalance branch MUST be a direct carry-forward of stored state. The engine MUST NOT
reconstruct quantity from weights: the round trip `q -> w -> q` is not bitwise stable
(measured failing on 17.8% of realistic inputs).

The `w_target == 0` branch MUST assign literal `0.0`, never evaluate
`0.0 * NAV_after_cost / P[i, j]`, which yields `NaN` when the price is absent.

### 5.2 Derived weights

    w_pre[i, j]  = 0.0 if quantity[i-1, j] == 0 else quantity[i-1, j] * P[i, j] / NAV_pre[i]
    w_held[i, j] = 0.0 if quantity[i,   j] == 0 else quantity[i,   j] * P[i, j] / NAV_after_cost[i]

Zero branches are **assigned**, never computed.

Weight drift is not a formula: it is an emergent consequence of holding quantity constant
while `P` and NAV move.

### 5.3 Symbol activity classification

For period `i` and symbol `j`, with `q_prev = quantity[i-1, j]`:

    will_hold[i, j] =  (w_target[i, j] != 0)  if i is a rebalance execution point
                       (q_prev != 0)          otherwise

| state | condition | required prices | contribution |
|-------|-----------|-----------------|--------------|
| **INACTIVE** | `q_prev == 0` and `not will_hold` | **none** | exactly zero, by exclusion from aggregation |
| **ENTERING** | `q_prev == 0` and `will_hold` | `P[i, j]`, `P[i+1, j]` | full |
| **HELD** | `q_prev != 0` and `will_hold` | `P[i, j]`, `P[i+1, j]` | full |
| **EXITING** | `q_prev != 0` and `not will_hold` | `P[i, j]` only | trade only; flat over period `i`, earns nothing |

Classification and price validation happen in §6 Step 0, before any accounting arithmetic. The
classification depends only on `q_prev` and the target, never on a price, so it is always
decidable.

**The engine MUST NEVER rely on arithmetic such as `0 * NaN` to neutralise an inactive
symbol.** In IEEE754 `0.0 * NaN = NaN`, and `0.0 - NaN != 0` evaluates `True`. INACTIVE
symbols are excluded by an explicit boolean mask; their prices are never read, never
validated, and may be absent, `NaN`, zero or negative without consequence.

### 5.4 Column alignment

The engine operates on the **sorted union** of symbols in the market data.

When execution point `i` is a rebalance point sourced from signal bar `t = i - execution_lag`,
the row that must name every exit is **`target_weights[t]`**:

- every symbol `j` with `quantity[i-1, j] != 0` MUST appear in `target_weights[t]` with a
  finite value. Absent or `NaN` -> `DataIntegrityError`.
- symbols absent from `target_weights[t]` with `quantity[i-1, j] == 0` are treated as target 0
  and are INACTIVE.

### 5.5 Price validity

**Every price actually used for execution, valuation, return calculation, position sizing, or
funding-event valuation MUST be finite and strictly greater than zero.**

Violations raise `InvalidPriceError` (a `DataIntegrityError` subclass) naming symbol,
timestamp, value and the use that required it. `NaN`, `±inf`, `0.0` and negatives are all
rejected. Measured consequences of v1.2's finite-only guard: `P = 0` produced
`quantity = inf`; `P = -5` produced a **short** position from a long target while
`gross_exposure` read a normal `1.0`.

Prices for INACTIVE symbols are not "in use" and are deliberately unvalidated.

### 5.6 Notional

    notional[i, j] = 0.0 if quantity[i, j] == 0 else quantity[i, j] * P[i, j]

The zero branch is **assigned**, not computed (W13). This is the last `0 x price` expression
in the document and it is now closed.

---

## 6. Accounting sequence (NORMATIVE)

PnL and funding accrue on **every** holding period. Trades occur **only** at rebalance
execution points. For `i = 0 .. n-2`, with `NAV_pre[0] = initial_capital`:

**Step 0 — resolve, classify, validate.**

    t = i - execution_lag
    rebalance[i] = (0 <= t <= n-1) AND rebalance_mask[t] == True
    w_target[i]  = target_weights[t]   if rebalance[i]   else undefined

Step 0 resolves `t` and reads `target_weights[t]` **itself**; it does not depend on Step 1
(W5). It then computes §5.3 classifications and validates all required `P[i, j]` per §5.5.

Validation of `P[i+1, j]` is **deferred until after Step 2** (W4), because a cost-stage ruin
skips Steps 3–6 entirely and must not be pre-empted by an error on a price the engine would
never have read.

**Step 1 — trade**

    if rebalance[i]:
        trade[i, j]  = 0.0 if (q_prev == 0 and w_target[i, j] == 0)      # assigned
                       else w_target[i, j] - w_pre[i, j]
        turnover[i]  = Σ_j | trade[i, j] |
    else:
        trade[i] = 0 ;  turnover[i] = 0

**Step 2 — costs, charged against pre-trade NAV**

    fee_cost[i]       = turnover[i] * NAV_pre[i] * fee_bps      / 10_000
    slippage_cost[i]  = turnover[i] * NAV_pre[i] * slippage_bps / 10_000
    NAV_after_cost[i] = NAV_pre[i] - fee_cost[i] - slippage_cost[i]

If `NAV_after_cost[i] <= 0`, **cost-stage ruin** occurs here (§6.7) before any sizing.

**Step 2b — validate `P[i+1, j]`** for every ENTERING and HELD symbol (deferred from Step 0).

**Step 3 — set the quantity ledger** — per §5.1.

**Step 4 — asset PnL (masked aggregation)**

    active = { j : quantity[i, j] != 0 }
    asset_pnl_cash[i] = Σ_{j ∈ active} quantity[i, j] * ( P[i+1, j] - P[i, j] )

The sum runs over the active set only; symbols outside it are excluded from the summation,
not multiplied by zero.

**Step 5 — funding PnL** — per §7, masked to `active`.

**Step 6 — ending NAV**

    NAV_end[i]   = NAV_after_cost[i] + asset_pnl_cash[i] + funding_pnl_cash[i]
    NAV_pre[i+1] = NAV_end[i]

If `NAV_end[i] <= 0`, **pnl-stage ruin** occurs here (§6.7).

**Step 7 — finiteness guard.** If `NAV_end[i]` is not finite, raise `AccountingError`. Given
§5.5 and Step 4 this is unreachable in normal operation and is an internal-consistency guard;
it exists because a `NaN` NAV silently passes `NAV_end <= 0` (`NaN <= 0` is `False`). It is
testable only via a fault-injection hook, which the implementation MUST provide for §18.11.

### 6.1 Return decomposition

    gross_return[i]     =  asset_pnl_cash[i]   / NAV_pre[i]
    fee_return[i]       = -fee_cost[i]         / NAV_pre[i]      # <= 0
    slippage_return[i]  = -slippage_cost[i]    / NAV_pre[i]      # <= 0
    funding_return[i]   =  funding_pnl_cash[i] / NAV_pre[i]      # signed

    net_return[i] = gross_return[i] + fee_return[i] + slippage_return[i] + funding_return[i]

**(N-1) NAV identity** — holds on every non-ruin period, to §17 tolerance:

    net_return[i] == NAV_end[i] / NAV_pre[i] - 1

**(N-2) Additive decomposition** — holds on every **non-ruin** period, to §17 tolerance. It
does **NOT** hold at a ruin period (§6.7).

(N-1) has been verified independently twice: 9.9e-17 and 3.56e-16.

### 6.2 `gross_return` is an attribution component, not a path

`gross_return[i]` is the pre-cost return of the **actual** portfolio. Because Step 3 sizes on
`NAV_after_cost`, it already reflects the capital costs removed. It is a decomposition term,
**not** a zero-cost counterfactual, and does not compound into a meaningful gross equity curve.

There is deliberately **no** `gross_equity_curve`, no gross Sharpe and no gross drawdown from
this series.

`turnover[i] * NAV_pre[i]` is the **fee basis**, not traded notional. True traded notional is
`Σ_j | quantity[i,j] * P[i,j] - quantity[i-1,j] * P[i,j] |`. The result exposes the former as
`fee_basis_notional`.

### 6.3 Turnover

    turnover[i] = Σ_j | trade[i, j] |

**One-way, fraction of NAV, NO factor of 0.5.** `0 -> +1` gives 1; `+1 -> -1` gives 2; a
non-rebalance period gives exactly 0 regardless of drift. Measured against the drifted
`w_pre[i]`, never the previous target.

### 6.4 Masked aggregation

Every portfolio aggregate — `asset_pnl_cash`, `funding_pnl_cash`, `turnover`,
`gross_exposure`, `net_exposure` — MUST be computed over an explicit active mask. No aggregate
may depend on a price belonging to an INACTIVE symbol.

### 6.5 Exposures

    gross_exposure[i] = Σ_j | w_held[i, j] |
    net_exposure[i]   = Σ_j   w_held[i, j]
    gross_leverage[i] = gross_exposure[i]

`gross_leverage` is an explicit alias carrying the docstring *"notional/NAV; NOT a margin
ratio; see §14 — liquidation is not modelled."* It is not named `leverage`.

### 6.6 Order of operations

Rebalance, pay, then earn. Costs are a function of `NAV_pre`; positions of `NAV_after_cost`.

### 6.7 Ruin — terminal row definition (resolves D4)

**Ruin is an economic outcome, not an implementation exception.** It is detected at two
mutually exclusive stages:

    ruin_stage = "cost"   -- NAV_after_cost[i] <= 0 at Step 2 (transaction costs alone)
                 "pnl"    -- NAV_end[i]        <= 0 at Step 6 (asset PnL and/or funding)

They cannot both fire: a cost-stage ruin terminates the run before Step 6 is reached, and ruin
terminates the run, so no later period exists.

#### 6.7.1 Position convention at the terminal row

> **Terminal quantities and exposures represent the LAST ECONOMICALLY VALID POSITION STATE
> IMMEDIATELY BEFORE RUIN. They are NOT a simulated liquidation.**

This convention is chosen because §14 models no liquidation mechanics. Reporting zero
positions at the terminal row would imply the book was closed at a determinable price, which
this engine does not and cannot model. The reported positions are the ones the account was
carrying when it died.

The result carries `terminal_position_convention = "pre_ruin_state"` so no consumer can
interpret it as a liquidation. Downstream reports MUST NOT infer an exit price, an exit
timestamp, or a realised close from the terminal row.

Concretely, the "last economically valid position state" is:

- **pnl-stage ruin**: `quantity[i]` — the position was established at Step 3 and was held
  through the period whose PnL caused the ruin
- **cost-stage ruin**: `quantity[i-1]` — the intended new position was never sized, because
  Step 3 never ran. The account died holding its prior book.

#### 6.7.2 Terminal row field definitions

Every field is classified as **(A)** state immediately before ruin, **(B)** terminal economic
outcome, or **(C)** genuinely undefined because the period never economically completed.

| field | cost-stage ruin | pnl-stage ruin | class |
|-------|-----------------|----------------|-------|
| `equity_curve[i+1]` | `0.0` exactly | `0.0` exactly | B |
| `net_return[i]` | `-1.0` exactly | `-1.0` exactly | B |
| `ruined` | `True` | `True` | B |
| `ruin_timestamp` | `T_{i+1}` | `T_{i+1}` | B |
| `ruin_stage` | `"cost"` | `"pnl"` | B |
| `turnover[i]` | computed normally | computed normally | A |
| `trade[i, :]` | computed normally | computed normally | A |
| `fee_cost[i]`, `slippage_cost[i]` | computed normally | computed normally | A |
| `fee_return[i]`, `slippage_return[i]` | computed normally | computed normally | A |
| `quantity[i, :]` | `quantity[i-1, :]` (pre-trade) | as sized at Step 3 | A |
| `notional[i, :]` | from `quantity[i-1]` and `P[i]` | from `quantity[i]` and `P[i]` | A |
| `positions[i, :]` (`w_held`) | `w_pre[i, :]` | as computed | A |
| `pre_trade_weights[i, :]` | computed normally | computed normally | A |
| `symbol_state[i, :]` | as classified in Step 0 | as classified in Step 0 | A |
| `gross_exposure`, `net_exposure`, `gross_leverage` | from `w_pre[i]` | from `w_held[i]` | A |
| `asset_pnl_cash[i]` | **`NaN`** | computed normally | C / A |
| `funding_pnl_cash[i]` | **`NaN`** | computed normally | C / A |
| `gross_return[i]` | **`NaN`** | computed normally | C / A |
| `funding_return[i]` | **`NaN`** | computed normally | C / A |
| `uncapped_ruin_return` | `NAV_after_cost[i]/NAV_pre[i] - 1` | `NAV_end[i]/NAV_pre[i] - 1` | B |
| `ruin_decomposition_residual` | `uncapped_ruin_return - (-1.0)` | `uncapped_ruin_return - (-1.0)` | B |

**The four `NaN` values at a cost-stage ruin are a documented terminal sentinel**, not a
defect. The holding period never economically occurred: no position was sized, so no asset PnL
and no funding accrued. Writing `0.0` there would be inventing an economically meaningful
value to avoid `NaN`, which is forbidden. `NaN` is the honest encoding of "undefined".

At a pnl-stage ruin **no field is undefined** and no `NaN` appears anywhere.

#### 6.7.3 Termination

1. the simulation terminates; no period after `i` is computed
2. `quantity[i+1]` is never formed, so division by `NAV_pre[i+1] = 0` never occurs
3. artificial zero-return periods MUST NOT be appended — padding deflates volatility and
   flatters Sharpe on a dead strategy
4. all series truncate to the realized range; `equity_curve` ends at `T_{i+1}` with `0.0`

**(N-1) holds at the ruin period** (`0/NAV_pre - 1 = -1`).
**(N-2) does NOT hold at the ruin period**; the components sum to `uncapped_ruin_return`.
§18.8 X6 asserts (N-1) only and asserts the residual to §17 tolerance.

`ruin_timestamp = T_{i+1}` at **both** stages, for index consistency with §8 (W11). Note that
at a cost-stage ruin the account economically died at `T_i`, when the costs were paid; `T_{i+1}`
is the timestamp of the terminal equity observation, not the instant of death. This is a
deliberate indexing choice, not an error to be "fixed".

`ruined = True` MUST appear in `__repr__`, in any summary table, and in any report the result
feeds.

### 6.8 Leverage tripwire

Optional `max_gross_leverage: float | None = None`. Any period with
`gross_exposure[i] > max_gross_leverage` sets `leverage_breach = True` and records timestamps.
It does **not** alter the simulation.

---

## 7. Funding

Hyperliquid funds **hourly**; strategies run on 1h, 4h and 1d bars. **One OHLC bar does NOT
correspond to one funding event.** No venue's funding cadence may be hardcoded in the engine.

### 7.1 Funding events

    FundingEvent:
        timestamp       : tz-aware UTC
        symbol          : str
        funding_rate    : float          # realised rate for THAT event, decimal fraction
        notional_price  : float | None   # required iff basis == "event_price"

`funding_rate` is the **per-event realised rate**: not annualized, not rescaled, not a
percentage. An hourly rate of one basis point is `0.0001`. The loader owns normalization; the
engine performs no unit guessing and no cadence inference.

### 7.2 Funding coverage metadata

    FundingCoverage:
        symbol           : str
        coverage_start   : tz-aware UTC
        coverage_end     : tz-aware UTC
        max_funding_gap  : Timedelta
        source_venue     : str

A symbol MAY have multiple records. **Records for a symbol MUST be pairwise disjoint with
non-intersecting closures** — touching or overlapping records MUST be merged by the loader
before reaching the engine, else `DataIntegrityError` (W6). This guarantees a holding period
can never straddle a seam, so §7.7 condition 2 never false-rejects, without composing records
(which would hide a gap at the seam).

`max_funding_gap` lives here, not in `BacktestConfig`: it is a venue/dataset property, not a
backtest choice. `max_funding_gap <= 0` raises `DataIntegrityError`.

Coverage is **declared by the data layer, never inferred**. Inferring cadence is circular: the
stream being validated is the stream that would be inferred from, so a sparse stream infers a
permissive tolerance and validates itself.

> **Known residual risk (W7):** a loader that declares `max_funding_gap = 8h` for a venue that
> actually funds hourly, with 7 of every 8 events missing, passes §7.7 condition 3 with gaps of
> exactly 8h and undercharges funding 8x silently. Declared metadata is trusted by design.
> QR-DATA-001 owns declaring it correctly. §7.7's soft check catches the coarser cases.

### 7.3 Sign convention

    funding_rate > 0  =>  LONGS PAY SHORTS

A long with a positive rate produces **negative** funding PnL. The minus sign in §7.5 is the
only place this convention is applied.

### 7.4 No lookahead

Funding is consumed only as a realised cost, never as a signal input. A strategy wanting
funding as a *signal* must receive it through market data subject to §4 timing.

### 7.5 Aggregation into holding periods

For period `i` spanning `[T_i, T_{i+1})`, select events `e` for symbol `j` with

    T_i <= e.timestamp < T_{i+1}

**Half-open**, so a boundary event is charged to exactly one period — the later one. Events
before `T_0` or at/after `T_{n-1}` are excluded and counted in `funding_events_excluded`.

    basis == "event_price":   notional_e = quantity[i, j] * e.notional_price
    basis == "period_start":  notional_e = quantity[i, j] * P[i, j]

    funding_pnl_cash[i] = - Σ_{j ∈ active} Σ_e notional_e * e.funding_rate

`quantity[i, j]` is the **post-trade** quantity. The outer sum is masked to the active set.

### 7.6 `funding_notional_basis` is a run-level mode

    funding_notional_basis = "event_price" | "period_start"
    # REQUIRED when funding_mode == "required"; "not_modelled" when disabled

| mode | behaviour |
|------|-----------|
| `"event_price"` | **Every** event applied to a nonzero position MUST carry a finite, strictly positive `notional_price`; otherwise `FundingDataError`. Venue-accurate for Hyperliquid's oracle-price basis. |
| `"period_start"` | `notional_price` is **ignored entirely** — not required, not read, not validated. |

**No per-event fallback and no `"mixed"` basis.** Mixing is impossible by construction.

The `"period_start"` approximation holds notional flat across the period. Its error is bounded
by `|rate| x (max intra-period price move)` and is **not** negligible: on a +15% intra-day move
it misstates the day's funding by **-6.98%** (verified: `1/1.075 - 1`).

Rejected alternative: averaging period-start and period-end notional — it uses `P[i+1]`, a
future price relative to every funding instant, correlated with the period's own return.

### 7.7 Funding mode and coverage validation

    funding_mode = "required" | "disabled"       # REQUIRED config, no default

Under `"disabled"`: funding is exactly 0, `funding_modelled = False`,
`funding_notional_basis = "not_modelled"`. The engine MUST NOT infer `"disabled"` from a
missing column, an empty frame, or a symbol with no events.

Under `"required"`, for every symbol `j` and every period `i` with `quantity[i, j] != 0`,
raise `FundingDataError` unless all hold:

1. at least one `FundingCoverage` record exists for `j`
2. `[T_i, T_{i+1}) ⊆ [coverage_start, coverage_end]` for a **single** record
3. within that record's window, the augmented sequence
   `[coverage_start] + sorted(events in window) + [coverage_end]` has **no consecutive gap
   exceeding `max_funding_gap`**

A gap exactly equal to `max_funding_gap` is **accepted** ("exceeds" means strictly greater).

Symbols with zero exposure throughout need no funding data.

**Soft check (non-fatal):** if modal event spacing within a coverage window is more than 2x
below `max_funding_gap`, set `funding_gap_tolerance_suspicious = True`.

---

## 8. Equity curve indexing

| row | timestamp | value |
|-----|-----------|-------|
| 0 | `T_0` | `initial_capital` |
| `k` (1 .. n-2) | `T_k` | `NAV_end[k-1]` = `NAV_pre[k]` |
| `n-1` | `T_{n-1}` | `NAV_end[n-2]` |

    len(equity_curve) == n
    len(net_returns)  == n - 1 == len(equity_curve) - 1
    n_periods         := len(net_returns)

`equity_curve[k+1] ≈ equity_curve[k] * (1 + net_return[k])` **to §17 tolerance — not bitwise.**
Measured bitwise failure rate 32.1%, worst relative error 4.32e-16, because `net_return` is a
sum of four separately-divided components.

Under ruin at period `i`, `equity_curve` truncates to rows `0 .. i+1` with
`equity_curve[i+1] = 0.0`, and `n_periods = i + 1`.

    total_return  = equity_curve[-1] / equity_curve[0] - 1
    cagr          = (equity_curve[-1] / equity_curve[0]) ** (af / n_periods) - 1
    max_drawdown  = min( equity_curve / cummax(equity_curve) - 1 )

Because row 0 is `initial_capital`, a first-period loss is captured. The exponent counts
periods, not observations.

---

## 9. Counterfactual zero-cost path

**A. `gross_return`** (§6.2) — pre-cost return of the **actual** portfolio, an attribution
component. No equity curve, no metrics.

**B. Counterfactual path** — a separate, complete, independent simulation.

### 9.1 Counterfactual accounting

Run the full §6 sequence a second time with the same `target_weights`, `rebalance_mask`,
`execution_mode`, `execution_lag`, `initial_capital`, prices and bar index, and with

    fee_cost[i] = 0 ,  slippage_cost[i] = 0 ,  funding_pnl_cash[i] = 0    for all i

The counterfactual maintains its **own** quantity ledger and NAV path.

**This is not the actual gross returns compounded.** With `NAV_after_cost = NAV_pre`, Step 3
sizes different quantities from the first rebalance and the paths diverge (§18.7 CF3).

### 9.2 Drag attribution

    total_drag_return = counterfactual_total_return - total_return
    cagr_drag         = counterfactual_cagr         - cagr

Drag is **positive when costs dominate and legitimately negative when funding is net income**.
It is NOT decomposable into components by differencing counterfactual paths, because they
interact through the compounding NAV. The additive (N-2) decomposition is the correct tool for
component attribution. The result MUST NOT present a component-wise drag breakdown from §9.

Drag fields are populated **only** when `counterfactual_status == "COMPLETED"` and both paths
span the same number of periods. Otherwise they are `None` and `drag_comparable = False`.

### 9.3 Strict value isolation

The two paths are fully independent. Each runs over its own full range and each handles ruin
per §6.7 independently:

    ruined                        ruin_timestamp                ruin_stage
    counterfactual_ruined         counterfactual_ruin_timestamp

The counterfactual may NEVER modify the actual path's length, equity, ruin state or metrics.

Verified counterexample driving this rule: a 2.5x book with funding **income** — the actual
survives all 4 periods while the counterfactual (funding zeroed) ruins at period 1 (§18.7 CF7).

### 9.4 Counterfactual auxiliary behaviour

- §5.3 classification and §5.5 price validity apply identically **within the barrier** (§9.5)
- §7.7 funding coverage is **not** validated for the counterfactual: it charges no funding, so
  no result it produces can be misstated by a coverage hole, and the actual path independently
  validates coverage over every period in which it holds exposure
- §6.8's tripwire applies, reported separately as `counterfactual_leverage_breach`
- `unexecuted_rebalances` is identical by construction and is not duplicated

### 9.5 Exception isolation — the counterfactual barrier (resolves D2)

> **The counterfactual is diagnostic only and MUST NOT be capable of changing whether the
> actual backtest succeeds. This includes exceptions. The actual path is authoritative.**

Requirements:

1. **The actual path executes first and to completion**, independently. The actual result MUST
   be fully computable without any counterfactual state.
2. **The counterfactual runs afterwards, inside an exception barrier.** It catches
   `DataIntegrityError` (including `InvalidPriceError` and `MissingPriceError`),
   `FundingDataError` and `AccountingError` raised **from counterfactual execution**, records
   them, and re-raises nothing.
3. A counterfactual `FAILED` state MUST NOT convert a valid actual backtest into an exception.

    counterfactual_status ∈ { "NOT_COMPUTED", "COMPLETED", "RUINED", "FAILED" }
    counterfactual_error  : str | None      # exception type and message when FAILED
    counterfactual_ruined : bool
    counterfactual_ruin_timestamp : Timestamp | None

| status | meaning |
|--------|---------|
| `NOT_COMPUTED` | `compute_counterfactual = False` |
| `COMPLETED` | ran to the end of the sample without ruin |
| `RUINED` | ran and ruined per §6.7; `counterfactual_ruined = True` |
| `FAILED` | raised inside the barrier; `counterfactual_error` populated; all `counterfactual_*` series and drag fields `None`; `drag_comparable = False` |

#### 9.5.1 What the barrier MUST NOT suppress

**Errors that invalidate INPUT DATA required by the ACTUAL path are NOT counterfactual
failures and MUST propagate.** The distinction is unambiguous because of requirement 1:

- **actual-path data integrity error** — raised while the actual path is executing, i.e.
  before the barrier is entered. Propagates. The backtest fails, correctly.
- **counterfactual-only diagnostic failure** — raised inside the barrier. Recorded as
  `FAILED`. The actual result is returned intact.

Because the actual path runs to completion first, any input defect that matters to it has
already raised. Anything the barrier catches is by construction a defect in data the actual
path never needed — most commonly data in periods after an actual-path ruin, or for symbols the
actual path stopped holding earlier.

The barrier MUST catch only the enumerated exception types. `KeyboardInterrupt`,
`MemoryError`, `SystemExit` and programming errors (`TypeError`, `AttributeError`,
`NameError`) MUST propagate: they indicate an engine bug, not a data condition.

#### 9.5.2 Mandatory invariant

> For any inputs where the actual path is valid, `compute_counterfactual=False` and
> `compute_counterfactual=True` MUST produce **bit-identical actual-path outputs.**

This holds unconditionally, including when the counterfactual is `FAILED` or `RUINED`. §18.7
CF8 tests it, and CF9 tests it specifically on the delisting counterexample that made it
unachievable in v1.3: the actual ruins at period 3, the counterfactual survives to period 10,
and a held symbol delists at period 7 — data the actual path never reads.

---

## 10. Result surface

**Equity** (§8 index, `n_periods + 1` rows) — `equity_curve`.

**Per-period series** (`n_periods` rows, row `i` = period `[T_i, T_{i+1})`) — `net_return`,
`gross_return`, `fee_return`, `slippage_return`, `funding_return`, `fee_cost`,
`slippage_cost`, `funding_pnl_cash`, `asset_pnl_cash`, `fee_basis_notional`, `turnover`,
`gross_exposure`, `net_exposure`, `gross_leverage`, `rebalance_flag`.

Field names are **singular** throughout and match §6.1 exactly (W12). There is no
`gross_returns`/`gross_return` split.

`NAV_pre[i]` is `equity_curve[i]`; `NAV_after_cost[i]` is
`equity_curve[i] - fee_cost[i] - slippage_cost[i]`. Both are derivable from the public surface,
which is what §18.4 N6 asserts against (W12).

**Per-period frames** (period x symbol) — `quantity`, `notional`, `positions` (`w_held`),
`pre_trade_weights` (`w_pre`), `target_weights` (as supplied), `trades`, `symbol_state`.

`rebalance_flag[i]` is the **execution-point** indicator, not the input mask at signal bar `t`.

**Counterfactual** — `counterfactual_gross_equity`, `counterfactual_gross_return`,
`counterfactual_gross_metrics`, `counterfactual_status`, `counterfactual_error`,
`counterfactual_ruined`, `counterfactual_ruin_timestamp`, `counterfactual_leverage_breach`,
`total_drag_return`, `cagr_drag`, `drag_comparable`.

**Metrics** — `metrics`, per §12.

**Status and provenance** — `ruined`, `ruin_timestamp`, `ruin_stage`,
`terminal_position_convention`, `uncapped_ruin_return`, `ruin_decomposition_residual`,
`funding_modelled`, `funding_notional_basis`, `funding_events_excluded`,
`funding_gap_tolerance_suspicious`, `liquidation_modelled` (always `False`), `leverage_breach`,
`leverage_breach_timestamps`, `unexecuted_rebalances`, `provenance`, `provenance_supplied`,
`provenance_complete`, `uses_proxy_data`, `universe_provenance`, `survivorship_safe`, `config`.

`__repr__` MUST surface `ruined`, `liquidation_modelled`, `uses_proxy_data`,
`survivorship_safe` and `counterfactual_status` when not `COMPLETED`.

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

Types: `DataIntegrityError`, `InvalidPriceError` (subclass), `MissingPriceError` (subclass),
`FundingDataError`, `ConfigError`, `AccountingError`. Never bare `ValueError`.

Data integrity: duplicate `(timestamp, symbol)`; non-monotonic timestamps; naive timestamps;
irregular grid or grid disagreeing with `frequency`; `n < 2`; a symbol in `target_weights`
absent from market data; `rebalance_mask` absent or misaligned; `NaN` target on a rebalance
bar; a symbol with `quantity[i-1, j] != 0` absent or `NaN` in `target_weights[t]`;
`max_funding_gap <= 0`; overlapping or touching `FundingCoverage` records for one symbol;
`native_or_proxy == "proxy"` with empty `proxy_for`.

Price validity (§5.5): any price in use that is not finite and strictly `> 0`.

Funding: coverage failures (§7.7); missing or invalid `notional_price` under `"event_price"`.

Config: `execution_lag < 1`; missing `fee_bps`, `slippage_bps`, `funding_mode`, `frequency`,
`annualization_factor`; `funding_notional_basis` absent under `funding_mode = "required"`.

Accounting: non-finite `NAV_end` (§6 Step 7).

### 11.3 May proceed silently

Only INACTIVE symbols (§5.3). Their prices may be absent, `NaN`, zero or negative and are
never read. This is the staggered-listing and post-exit case and it is the **only** silent path.

`NaN` target weights on non-rebalance bars.

### 11.4 Delisting and renames

The engine raises when a held position loses a required price. It does **not** auto-liquidate
at a stale price and does not carry positions forward at last value.

The correct pattern: the strategy emits a target of `0` on a rebalance bar where a valid price
still exists, making the symbol EXITING (requiring only `P[i]`) and then INACTIVE.

**Symbol renames** land on the delisting path and will raise. Deliberate — silently mapping a
renamed ticker is a survivorship-bias vector — but identity resolution belongs to QR-DATA-001.

---

## 12. Metrics

`af` = `annualization_factor`. `n_periods` = `len(net_return)`.

    total_return          = equity_curve[-1] / equity_curve[0] - 1
    cagr                  = (equity_curve[-1] / equity_curve[0]) ** (af / n_periods) - 1
    annualized_volatility = std(net_return, ddof=1) * sqrt(af)

    ann_excess_arith      = mean(net_return - risk_free_per_period) * af
    sharpe                = ann_excess_arith / annualized_volatility

    downside_dev_ann      = sqrt( mean( min(net_return - mar_per_period, 0) ** 2 ) ) * sqrt(af)
    ann_excess_mar_arith  = mean(net_return - mar_per_period) * af
    sortino               = ann_excess_mar_arith / downside_dev_ann

    max_drawdown          = min( equity_curve / cummax(equity_curve) - 1 )   # NEGATIVE
    calmar                = cagr / abs(max_drawdown)

    avg_turnover          = mean(turnover)
    annualized_turnover   = mean(turnover) * af

### 12.1 Annualization

- **Sharpe and Sortino use ARITHMETIC annualization** — their denominators are distributional
  dispersion measures of per-period returns.
- **Calmar uses GEOMETRIC (CAGR)** — max drawdown is a realized-path quantity.
- The field is named `cagr`. There is **no** field named `annualized_return`.

### 12.2 Other pinned choices

- volatility uses SAMPLE std, `ddof=1`
- downside deviation divides by the count of **all** periods, not only losing periods.
  Consequently Sortino and Sharpe use different estimators and do **not** coincide on a
  symmetric distribution. **No identity relating them is asserted anywhere in this document.**
- max drawdown is signed negative
- `risk_free_per_period` and `mar_per_period` are **scalars**, default 0.0. The Sharpe
  denominator uses `std(net_return)` rather than `std(net_return - rf)`, correct only because
  `rf` is constant. If either becomes a series, both denominators must change.

### 12.3 Degenerate cases

Return `nan` — never 0, never an exception: `annualized_volatility == 0` -> `sharpe`;
`downside_dev_ann == 0` -> `sortino`; `max_drawdown == 0` -> `calmar`; `n_periods < 2` -> every
dispersion-based metric.

### 12.4 Metrics under ruin

Metrics use the actual observations through the ruin period. No padding.

- `total_return = -1.0` exactly
- `max_drawdown = -1.0` exactly
- `cagr = -1.0` exactly. Verified: `0.0 ** (af/n_periods)` returns `0.0` with no domain error
  for `(af, n_periods)` in `(365,1), (8760,1), (365,730), (8760,3), (2190,5)`
- `calmar = -1.0` exactly
- `sharpe`, `sortino`, `annualized_volatility` computed normally over the truncated series,
  subject to §12.3
- ruin at period 0 -> `n_periods == 1` -> dispersion metrics `nan`, no exception

At a **cost-stage** ruin, `gross_return` and `funding_return` contain `NaN` at the terminal row
(§6.7.2). Metrics are computed from `net_return` and `equity_curve` only, both of which are
fully defined, so **no metric is contaminated by the terminal sentinel.** Any future metric
derived from `gross_return` MUST handle the sentinel explicitly.

Arithmetically well defined, **not** economically meaningful. Always presented alongside
`ruined = True`.

---

## 13. Provenance

### 13.1 Dataset provenance

    DatasetProvenance:
        source_venue        : str            # REQUIRED when an object is supplied
        field_type          : str            # REQUIRED. "ohlcv", "funding_rate", "oracle_price"
        time_range          : (start, end)   # REQUIRED. tz-aware UTC
        native_or_proxy     : "native" | "proxy"    # REQUIRED
        proxy_for           : str | None     # MANDATORY when native_or_proxy == "proxy"
        dataset_id          : str | None
        dataset_version     : str | None
        processing_version  : str | None
        retrieval_date      : date | None
        symbol_mapping      : str | None
        notes               : str | None

Carried on `MarketData.provenance` and `FundingEvents.provenance`. `BacktestResult.provenance`
is a mapping with at minimum `"price"` and `"funding"`, holding the supplied objects verbatim.

The engine does not build provenance and does not validate its content beyond obligations 3–5.

Engine obligations:

1. never drop or overwrite supplied provenance
2. absent provenance -> `provenance_supplied = False`
3. any `native_or_proxy == "proxy"` -> `uses_proxy_data = True`, surfaced in `__repr__`
4. `native_or_proxy == "proxy"` with `proxy_for` empty or `None` -> `DataIntegrityError`.
   A disclosure that discloses nothing is worse than none, because it satisfies a checkbox
5. `provenance_complete = True` only when every supplied object has non-`None`
   `source_venue`, `field_type`, `time_range` and `native_or_proxy` (W9). An all-`None`
   provenance object MUST NOT satisfy the disclosure requirement

### 13.2 Universe provenance (resolves W10)

CLAUDE.md ranks "current Hyperliquid listings retrospectively assumed to have existed
throughout historical periods" alongside proxy data as a policy violation. v1.3 had no
survivorship disclosure, and §5.4 silently takes "the sorted union of symbols present in the
market data" as the universe.

    UniverseProvenance:
        universe_source      : str | None    # how the symbol set was constructed
        universe_asof_policy : str | None    # "point_in_time" | "static_current" | ...
        listing_data_source  : str | None    # source of listing/delisting dates
        survivorship_safe    : bool | None   # True only if point-in-time construction is proven
        notes                : str | None

Carried on `MarketData.universe_provenance` and surfaced on the result as
`universe_provenance` and the convenience flag `survivorship_safe`.

Engine obligations — deliberately minimal, since QR-DATA-001 owns construction:

1. never drop supplied universe provenance
2. `survivorship_safe` is `None` when not supplied and MUST NOT default to `True`
3. `survivorship_safe` in `(None, False)` -> surfaced in `__repr__`, exactly as
   `uses_proxy_data` is

**The engine does not verify survivorship safety and cannot.** This is a pass-through contract
so QR-DATA-001 can populate it without changing `BacktestResult`. No universe-construction
system is built here.

---

## 14. Liquidation scope

**Liquidation and margin are NOT modelled.** `liquidation_modelled = False`, always.

Correct arithmetic for `gross_exposure > 1` does **not** mean a position was survivable
on-venue: no margin ratio, no maintenance margin, no auto-deleveraging, no funding-driven
margin call, no liquidation penalty.

Any result at meaningful leverage is an **upper bound** on achievable performance. §6.7.1's
terminal-position convention is explicitly **not** a liquidation model. §6.8's tripwire is a
reporting aid, not a risk model.

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
| `compute_counterfactual` | `True` | MUST NOT affect the actual result (§9.5.2) |

`max_funding_gap` is **not** a config field — it lives on `FundingCoverage` (§7.2).

---

## 16. Determinism

Identical inputs MUST produce bit-identical outputs. No RNG. No reliance on dict or set
iteration order. Symbols sorted deterministically. Two runs MUST compare exactly equal, and
this MUST hold across processes and `PYTHONHASHSEED` values (§18.2 P2).

---

## 17. Floating-point policy

**Exact equality (`==`) is permitted ONLY for genuinely discrete state:**

- booleans and flags (`ruined`, `rebalance_flag`, `leverage_breach`, `drag_comparable`,
  `survivorship_safe`, `counterfactual_status`)
- boolean masks and `symbol_state` classifications
- indices, timestamps, lengths, event counts
- **quantities carried forward unchanged** across a non-rebalance period (§5.1 — stored state,
  bitwise by construction)
- **quantities, weights and notionals assigned literal `0.0`** (§5.1, §5.2, §5.6)
- values that are exactly representable **and** reached by a single arithmetic path

**Documented tolerances are REQUIRED for everything reached by a different but mathematically
equivalent floating-point path:**

| quantity | tolerance |
|----------|-----------|
| NAV identity (N-1), equity recursion §8 | `rtol=1e-12`, `atol=1e-9` (USD) |
| return decomposition (N-2), `ruin_decomposition_residual` | `rtol=1e-12`, `atol=1e-15` |
| reconstructed / derived weights, exposures | `rtol=1e-12`, `atol=1e-15` |
| turnover and cost assertions | `rtol=1e-12`, `atol=1e-15` |
| metrics | `rtol=1e-10`, **`atol=1e-12`** |

The metrics `atol` (W1) exists because `rtol` alone cannot compare a true value of `0.0` —
e.g. `total_drag_return` in CF1, or Sharpe on a zero-mean fixture.

**This policy governs pinned literal values in §18** (W2). A literal such as `-0.006` written
in a fixture is compared under the applicable tolerance row, never with `==`, unless §18.0
classifies that specific assertion EXACT.

Measured evidence: the equity recursion failed bitwise on **32.1%** of periods (worst relative
error 4.32e-16); the `w_held` rebalance branch on **13.9%** of entries; `q -> w -> q` on
**17.8%**. All are correct engines failing incorrect tests.

Justification of the tolerance levels — the smallest *real* defect class is orders of magnitude
above them:

| defect | relative magnitude |
|---|---|
| `ddof=0` instead of `ddof=1` on Sharpe (n=8) | 6.9e-2 |
| one-bar execution lag error (fixture E) | 1.0e0 |
| `period_start` vs true funding notional on a 15% move | 7.0e-2 |
| fee charged on `NAV_after_cost` vs `NAV_pre` | 2.0e-2 |
| **measured floating-point noise** | **4.3e-16** |

The tightest tolerance sits ~4 orders above noise and ~10 orders below the smallest plausible
real defect. Nothing lives in the `1e-16`–`1e-10` band.

---

## 18. Mandatory tests

### 18.0 Assertion classification (resolves D1)

> **Every numeric assertion in §18 is classified below as EXACT or TOLERANCE. This
> classification is normative and was produced by mechanical review of each assertion
> individually, not by appeal to §17 existing elsewhere in this document.**

This section exists because float-equality defects appeared in four consecutive revisions —
v1.1 R4, v1.2 N5 and §8, v1.3 X6 — the last of these inside the very revision that introduced
§17. Writing the policy is demonstrably not the same as applying it.

#### 18.0.1 Classification table

| test | assertion | class | justification |
|------|-----------|-------|---------------|
| A | zero PnL / fees / turnover | EXACT | assigned literals, excluded from summation |
| B, C, D | compounded PnL values | TOLERANCE | multi-step arithmetic |
| E, E2, E3 | final NAV | TOLERANCE | products of exactly-representable values, but reached by a multi-step path |
| E4 | earlier periods unchanged | EXACT | identical arithmetic path, bitwise |
| G, H | turnover 1 / turnover 2 | TOLERANCE | `w_target - w_pre`; `w_pre` is derived |
| I, J | fee / slippage cost | TOLERANCE | derived from turnover |
| L, M, N | exposures | TOLERANCE | derived from `w_held` |
| P | two runs equal | EXACT | §16 determinism |
| P2 | cross-process determinism | EXACT | §16 |
| R1 | zero interim turnover and fees | EXACT | assigned literals on non-rebalance periods |
| R2 | no trade vs drift-correcting trades | EXACT for the zero branch, TOLERANCE for magnitudes | |
| R3 | weights drift | TOLERANCE | derived |
| **R4** | `quantity[i] == quantity[i-1]` | **EXACT** | §5.1 mandates direct carry-forward of stored state; §17 bullet 4 |
| R5 | quantity changes only where `rebalance_flag` | EXACT | boolean comparison |
| R6 | `rebalance_flag` index under `lag=2` | EXACT | index/boolean |
| N1, N2 | hand-computed `w_pre` | TOLERANCE | derived |
| N3 | identity (N-1) | TOLERANCE | §17 row 1 |
| N5 | equity recursion | TOLERANCE | §17 row 1 — measured 32.1% bitwise failure |
| N6 | `NAV_after_cost == NAV_pre*(1 - turnover*bps_total)` | TOLERANCE | §17 row 4 |
| N7 | decomposition (N-2) | TOLERANCE | §17 row 2 |
| F1, F7, F12 | funding aggregate values | TOLERANCE | sums of products |
| F2, F10 | event counted once / excluded counts | EXACT | integer counts |
| F5 | funding exactly 0 | EXACT | assigned literal |
| F6 | funding sign | EXACT | sign comparison, not magnitude |
| F17 | gap exactly `== max_funding_gap` accepted | EXACT | integer-nanosecond Timedelta |
| M1, M2, M5 | Sharpe, Sortino, total_return, cagr, max_drawdown | TOLERANCE | §17 metrics row |
| M3 | degenerate cases are `nan` | EXACT | `isnan` predicate |
| M4 | cagr differs from arithmetic annualization | TOLERANCE | inequality with margin |
| CF1 | counterfactual equals actual at zero cost | EXACT | `x - 0.0 - 0.0 == x` for finite `x`, identical path |
| CF2 | drag sign | EXACT | sign comparison |
| CF3 | `cumprod(1+gross) != cf_equity` | TOLERANCE | inequality asserted with margin |
| CF4 | hand-computed counterfactual | TOLERANCE | multi-step |
| CF7 | actual retains full length | EXACT | length is an integer |
| CF8, CF9 | actual bit-identical across `compute_counterfactual` | EXACT | §9.5.2 mandates bitwise |
| CF10 | `drag_comparable == False`, drag fields `None` | EXACT | boolean / identity |
| X1 | equity, net_return, uncapped, residual | TOLERANCE except `equity[-1] == 0.0` | see X1 |
| X2 | series lengths | EXACT | integers |
| X3 | no `inf`; `NaN` only where §6.7.2 permits | EXACT | predicate on the enumerated field set |
| X4 | `total_return`, `max_drawdown`, `cagr`, `calmar` all `-1.0` | **EXACT** | verified bitwise: `0.0/1e6 - 1 == -1.0`; `min(0/cummax - 1) == -1.0`; `0.0**(af/n) - 1 == -1.0` for all five §12.4 pairs; `-1.0/1.0 == -1.0` |
| X5 | `n_periods == 1`, metrics `nan` | EXACT | integer / predicate |
| **X6** | `ruin_decomposition_residual` | **TOLERANCE** | **D1 fix.** True value `-0.19880000000000008775`, not `-0.1988`. §17 row 2 |
| X7 | `ruined=True` in `__repr__` | EXACT | string containment |
| X8a | near-ruin finiteness | EXACT | `isfinite` predicate |
| X8b | `leverage_breach` fires | EXACT | boolean |
| X9 | cost-stage ruin row | TOLERANCE except lengths, `equity[-1] == 0.0`, `quantity` carry, `NaN` predicates | see X9 |
| X10 | `ruin_stage` value | EXACT | string |
| S1 | equity finite; `asset_pnl_cash` value | EXACT for `isfinite`; TOLERANCE for the value |
| S4, S6 | INACTIVE contributes exactly zero | EXACT | assigned literals, excluded from summation |
| S5 | `symbol_state` matches §5.3 | EXACT | categorical |
| S7 | `exit_unnamed` materialises `0.0` | EXACT | assigned literal |
| V1, V2, V3 | raises / no `inf` | EXACT | exception type / predicate |
| T1–T5, U1–U3, D4, PR1–PR4 | raises, lengths, field equality | EXACT | discrete |

#### 18.0.2 Fixture completeness rule (resolves D3)

> **Every fixture whose expected result depends on execution timing MUST state its complete
> relevant configuration explicitly. No fixture may rely on a default.**

At minimum every such fixture states: `execution_mode`, `execution_lag`, `fee_bps`,
`slippage_bps`, `funding_mode`, `initial_capital`, and `annualization_factor` where a metric
is asserted.

**No fixture may use `execution_lag = 0`**, because §4.2 forbids it in a production
`BacktestConfig`. v1.3's X1 and CF7 were computed at lag 0 without saying so and were
unreproducible under the config default; all fixtures in v1.4 are re-pinned at
`execution_lag = 1`.

If a future test needs artificial same-instant execution to isolate accounting logic, it MUST
use a lower-level accounting helper (`_step_period(...)`) exercising §6 Steps 1–6 directly,
and MUST NOT construct an invalid production `BacktestConfig`. Any such test must be labelled
`# LOWER-LEVEL HELPER — not a production config`.

A fixture MUST contain enough information for an independent implementer to reproduce it
exactly.

Values marked **[computed]** were calculated by the Research Lead and are reproduced here to
be **independently re-derived by the auditor**, not accepted on authority.

---

### 18.1 Anti-lookahead

Common configuration for E, E2, E3: `fee_bps = 0`, `slippage_bps = 0`,
`funding_mode = "disabled"`, `initial_capital = 1_000_000`, single symbol, target weight `1.0`.

**Test E — lag discrimination.** `execution_mode = "next_open"`, mask True at **bar 2 only**:

    open = [100, 100, 100, 200, 200, 200]        # 6 bars, 5 holding periods
    holding-period returns = [0, 0, 1.0, 0, 0]

    execution_lag = 1  ->  final NAV == 1_000_000     [computed]
    execution_lag = 0  ->  final NAV == 2_000_000     [computed]

Both assertions mandatory. The `lag = 0` branch MUST use the §18.0.2 lower-level helper, never
a production `BacktestConfig`. A test checking only the `lag = 1` value cannot demonstrate it
would catch the bug.

**Test E2 — execution mode discrimination.** `execution_lag = 1`, mask True at bar 2. Open and
close must diverge or the test is vacuous:

    open  = [100, 100, 100, 100, 200, 200]    -> r_open  = [0, 0, 0, 1.0, 0]
    close = [100, 100, 100, 200, 200, 200]    -> r_close = [0, 0, 1.0, 0, 0]

    execution_mode = "next_open"   ->  final NAV == 2_000_000     [computed]
    execution_mode = "next_close"  ->  final NAV == 1_000_000     [computed]

**Test E3 — `execution_lag = 2`.** `execution_mode = "next_open"`, mask True at bar 2:

    open = [100, 100, 100, 200, 400, 400, 400]    # 7 bars
    holding-period returns = [0, 0, 1.0, 1.0, 0, 0]

    execution_lag = 0  ->  final NAV == 4_000_000     [computed]   (lower-level helper)
    execution_lag = 1  ->  final NAV == 2_000_000     [computed]
    execution_lag = 2  ->  final NAV == 1_000_000     [computed]

Three distinct values, so lag 2 is separable from both lag 1 and lag 0.

**Test E4.** Perturb a price after `P[i+1]` and assert earlier periods are bit-identical.

### 18.2 Core accounting

All: `execution_mode = "next_open"`, `execution_lag = 1`, `funding_mode = "disabled"`,
`initial_capital = 1_000_000`, unless stated. Every test MUST show its arithmetic in the
docstring (W14).

| id | requirement |
|----|-------------|
| A | zero positions -> zero PnL, zero fees, zero turnover |
| B | constant long, `w = 1.0`, `P = [100, 110, 121]`, zero costs -> `equity = [1e6, 1e6, 1.1e6]` (lag 1 means period 0 is flat); final `total_return = 0.1` |
| C | constant short, `w = -1.0`, same prices -> final `total_return = -0.1` |
| D | two symbols, `w = [+0.5, -0.5]`, `P_A = [100, 110, 110]`, `P_B = [50, 50, 55]` -> hand-computed aggregate stated in the docstring |
| G | `0 -> +1` produces turnover 1 |
| H | `+1 -> -1` produces turnover 2 |
| I | `fee_bps = 10`, turnover 2, `NAV_pre = 1e6` -> `fee_cost = 2000.0` |
| J | `slippage_bps = 10`, turnover 2, `NAV_pre = 1e6` -> `slippage_cost = 2000.0` |
| L | gross exposure on a `[+0.6, -0.4]` book == 1.0 at the execution point |
| M | net exposure on the same book == 0.2 |
| N | `w = [+1.5, -1.5]` -> `gross_leverage == 3.0` |
| P | two runs compare exactly equal |
| P2 | determinism holds across separate processes and differing `PYTHONHASHSEED` (W17) |

### 18.3 Rebalance and quantity ledger

| id | requirement |
|----|-------------|
| R1 | a rebalance followed by six non-rebalance bars produces **exactly zero** interim turnover and fees, under **trending** prices |
| R2 | identical targets repeated on non-rebalance bars produce no trade; the same targets under `rebalance_every_bar` DO produce drift-correcting trades. Both in one test |
| R3 | weights drift between rebalances while quantity is constant |
| R4 | `quantity[i] == quantity[i-1]` **EXACT** on every non-rebalance period across a long trending span, including INACTIVE symbols whose stored quantity is literal `0.0` |
| R5 | quantity changes **only** where `rebalance_flag` is true |
| R6 | with `execution_lag = 2` and mask True at bar 2 only, `rebalance_flag` is True at index **4 only** — `rebalance_flag` is the execution-point indicator, not the input mask (W17) |

### 18.4 NAV consistency

| id | requirement |
|----|-------------|
| N1 | hand-computed case where **funding alone** changes NAV; next period's `w_pre` matches. Must fail if funding is omitted from `NAV_end` |
| N2 | same for fees and slippage |
| N3 | identity (N-1) on a randomized multi-asset case with all four cost components nonzero |
| N5 | `equity_curve[k+1] ≈ equity_curve[k] * (1 + net_return[k])` at §17 tolerance |
| **N6** | **discriminating (W3 fix).** `NAV_after_cost == NAV_pre * (1 - turnover * bps_total)` at §17 tolerance, where `bps_total = (fee_bps + slippage_bps)/10_000`. Pinned: `NAV_pre = 1_000_000`, `turnover = 4`, `fee_bps = 50`, `slippage_bps = 50` -> `bps_total = 0.01`, `NAV_after_cost == 960_000.0` **[computed]**. The rejected self-consistent solve `NAV_pre/(1 + turnover*bps_total) = 961_538.46153846150264` **[computed]** differs by `1538.46` (rel `1.538e-3`) and **fails** this assertion. v1.3's inequality form was satisfied by both implementations and was therefore non-discriminating |
| N7 | decomposition (N-2) sums to `net_return` on all non-ruin periods |

### 18.5 Funding

| id | requirement |
|----|-------------|
| F1 | 24 hourly events inside one 1d bar aggregate to the sum of 24 charges — must fail if the engine assumes one event per bar |
| F2 | an event exactly on a boundary is counted once, in the later period |
| F4 | `funding_mode="required"` with genuinely absent funding data raises `FundingDataError` |
| F5 | `funding_mode="disabled"` -> funding exactly 0, `funding_modelled=False` |
| F6 | long + positive rate -> negative funding PnL; short + positive rate -> positive |
| F7 | irregular event spacing within tolerance aggregates correctly |
| F8 | `next_open` vs `next_close` on an identical event stream produce different, documented windows. Must fail if `T_i` ignores `execution_mode` |
| F9 | 1h bars, complete 8h stream, `max_funding_gap=8h` does **not** raise |
| F10 | events before `T_0` or at/after `T_{n-1}` are excluded and counted |
| F12 | funding on a rebalance period is valued on the **post-trade** quantity |
| F13 | a gap exceeding `max_funding_gap` inside a coverage window raises |
| F14 | a symbol with zero exposure throughout needs no funding data |
| F15 | **coverage false-accept**: events only outside `[coverage_start, coverage_end]` — the `{2025-01-01, 2027-01-01}` counterexample — MUST raise |
| F16 | **non-contiguous exposure** with two disjoint, non-touching coverage records does **not** raise |
| F17 | a gap exactly equal to `max_funding_gap` is accepted |
| F18 | `basis="event_price"` with any missing/invalid `notional_price` on an applied event raises |
| F19 | `basis="period_start"` ignores `notional_price` entirely — present-but-invalid values do not raise and do not affect the result |
| F20 | modal spacing far below `max_funding_gap` sets `funding_gap_tolerance_suspicious` without raising |
| F21 | touching or overlapping `FundingCoverage` records for one symbol raise `DataIntegrityError` (W6) |

### 18.6 Metrics

**M7 is deleted and MUST NOT be reintroduced in any form.** No test may assert a derived
identity relating Sharpe and Sortino. v1.2's M7 asserted
`sortino/sharpe == sqrt(2)*sqrt((n-1)/n)`; it was `0/0` for any fixture satisfying its own
premise, and the only well-defined form is `sqrt(2)*sqrt(n/(n-1))` — inverted, 12.5% error at
n=8. It was imported from a prior audit's recommendation without re-derivation.

**Pinned fixture.** `af = 365`, `risk_free_per_period = 0`, `mar_per_period = 0`,
`initial_capital = 1_000_000`. `net_return` is supplied directly, so this fixture is
independent of `execution_mode` and `execution_lag`:

    net_return = [0.010, -0.005, 0.020, -0.015, 0.000, 0.008, -0.012, 0.006]
    n_periods  = 8   (3 negative periods)

Values **[computed]**, all TOLERANCE-class per §18.0.1:

    mean(net_return)        = 0.0015
    std(net_return, ddof=1) = 0.011807987611298186
    annualized_volatility   = 0.22559128655918556
    M1  sharpe              = 2.4269554394174677

    mean(min(r,0)**2)       = 4.9250000000000004e-05
    downside_dev_ann        = 0.13407553841025588
    M2  sortino             = 4.0835189363529718

    equity_curve[-1]        = 1011570.8691663996
    M5  total_return        = 0.011570869166399600
        cagr                = 0.690272927570154
        max_drawdown        = -0.019034560000000034

| id | requirement |
|----|-------------|
| M1 | `sharpe` equals the literal above |
| M2 | `sortino` equals the literal above |
| M3 | every §12.3 degenerate case returns `nan`, not 0, not an exception |
| M4 | on the pinned fixture, `cagr = 0.690272927570154` while arithmetic annualization `mean*af = 0.5475` **[computed]** — they differ by ~26%, guarding a silent swap (W14) |
| M5 | `total_return`, `cagr`, `max_drawdown` equal the literals above |
| M6 | `max_drawdown` captures a first-period loss below `initial_capital` |

### 18.7 Counterfactual

**Pinned CF3 fixture.** `execution_mode = "next_open"`, `execution_lag = 1`, `fee_bps = 50`,
`slippage_bps = 50`, `funding_mode = "disabled"`, `initial_capital = 1_000_000`, 2 symbols:

    P     = [[100, 50], [110, 45], [121, 40], [133.1, 36]]     # 4 bars, 3 periods
    mask  = [True, True, False, False]
    W[0]  = [0.6, -0.4] ;  W[1] = [0.6, -0.4]

Expected **[computed]** (execution at `i = 1` and `i = 2`; period 0 is flat under lag 1):

    actual equity          = [1_000_000, 1_000_000, 1_093_400.0, 1_201_772.0]
    counterfactual equity  = [1_000_000, 1_000_000, 1_104_444.4444444445, 1_214_888.888888889]
    gross_return           = [0.0, 0.1034, 0.09991951710261567]
    cumprod(1+gross)*1e6   = 1_213_651.1951710260   !=  counterfactual 1_214_888.8888888890

**Pinned CF7 fixture** — counterfactual ruins, actual survives. `execution_mode = "next_open"`,
`execution_lag = 1`, `fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "required"`,
`funding_notional_basis = "period_start"`, `initial_capital = 1_000_000`, 1 symbol,
target `2.5`, mask True at bar 0 only, funding rate `-0.09` per period (long **receives**):

    P = [[100], [100], [60], [60], [60]]        # 5 bars, 4 periods

    actual         equity = [1_000_000, 1_000_000, 225_000, 360_000, 495_000]  ruined = False  [computed]
    counterfactual equity = [1_000_000, 1_000_000, 0]                          ruined = True   [computed]

The actual result MUST retain all **5** equity observations.

| id | requirement |
|----|-------------|
| CF1 | zero fees, zero slippage, funding disabled -> counterfactual equity equals actual equity, EXACT |
| CF2 | with funding a net **cost**, `total_drag_return > 0`; with funding a net **income** (CF7 fixture), drag is legitimately **negative**. Both asserted in one test |
| CF3 | on the pinned fixture, `cumprod(1+gross_return)` does **not** equal counterfactual equity |
| CF4 | hand-computed 3-period 2-asset counterfactual, all values in the docstring |
| CF5 | counterfactual respects the same execution timing and rebalance mask |
| CF6 | **actual ruins, counterfactual survives**: counterfactual retains its full length; actual truncates |
| CF7 | **counterfactual ruins, actual survives**: on the pinned fixture the actual retains all 5 equity observations, `ruined == False`, `counterfactual_ruined == True`, `counterfactual_status == "RUINED"` |
| **CF8** | **isolation invariant**: the actual result is **bit-identical** with `compute_counterfactual` `True` and `False`, on a fixture where the counterfactual RUINS |
| **CF9** | **exception isolation (D2)**: actual ruins at period 3; counterfactual survives to period 10; a held symbol's price is absent at period 7 — data the actual path never reads. The backtest MUST return successfully with the actual result **bit-identical** to the `compute_counterfactual=False` run, `counterfactual_status == "FAILED"`, `counterfactual_error` populated, `drag_comparable == False`. Must fail if the counterfactual's exception propagates |
| CF10 | when statuses differ in length, `drag_comparable == False` and `total_drag_return`/`cagr_drag` are `None` |
| CF11 | an actual-path data integrity error (invalid price the actual path DOES read) still propagates and is **not** converted to `counterfactual_status = "FAILED"` (§9.5.1) |

### 18.8 Ruin

**Pinned X1 fixture — pnl-stage ruin.** `execution_mode = "next_open"`, `execution_lag = 1`,
`fee_bps = 10`, `slippage_bps = 10`, `funding_mode = "disabled"`,
`initial_capital = 1_000_000`, `annualization_factor = 365`, 1 symbol, target `3.0`, mask True
at bar 0 only:

    P = [[100], [100], [60]]        # 3 bars, 2 periods; execution at i = 1

Expected **[computed]**:

    equity_curve = [1_000_000, 1_000_000, 0.0]
    net_return   = [0.0, -1.0]
    ruined = True ,  ruin_stage = "pnl" ,  ruin_timestamp = T_2
    uncapped_ruin_return        = -1.1988          (exactly -1.1988000000000000878)
    ruin_decomposition_residual = -0.1988          (exactly -0.19880000000000008775)
    total_return = -1.0 , max_drawdown = -1.0 , cagr = -1.0 , calmar = -1.0

    terminal row (period 1), all class A per §6.7.2:
        turnover = 3.0 , fee_cost = 3000.0 , slippage_cost = 3000.0
        quantity = 29820.0 , positions (w_held) = 3.0
        asset_pnl_cash = -1_192_800.0 , funding_pnl_cash = 0.0
        NO NaN anywhere

**Pinned X9 fixture — cost-stage ruin with a pre-existing position.**
`execution_mode = "next_open"`, `execution_lag = 1`, `fee_bps = 1500`, `slippage_bps = 1500`,
`funding_mode = "disabled"`, `initial_capital = 1_000_000`, `annualization_factor = 365`,
1 symbol, mask True at bars 0 and 1, `W[0] = 1.0`, `W[1] = -4.0`:

    P = [[100], [100], [100], [60]]      # 4 bars, 3 periods; executions at i = 1 and i = 2

Expected **[computed]**:

    equity_curve = [1_000_000, 1_000_000, 700_000.0, 0.0]
    net_return   = [0.0, -0.3, -1.0]
    ruined = True ,  ruin_stage = "cost" ,  ruin_timestamp = T_3
    uncapped_ruin_return        = -1.5
    ruin_decomposition_residual = -0.5

    terminal row (period 2):
        turnover = 5.0 , fee_cost = 525_000.0 , slippage_cost = 525_000.0    (class A)
        quantity = 7000.0        -- the PRE-TRADE position, per §6.7.1        (class A)
        positions (w_held) = 1.0 -- equals w_pre                              (class A)
        asset_pnl_cash  = NaN                                                 (class C)
        funding_pnl_cash = NaN                                                (class C)
        gross_return     = NaN                                                (class C)
        funding_return   = NaN                                                (class C)

    total_return = -1.0 , max_drawdown = -1.0 , cagr = -1.0 , calmar = -1.0

This fixture deliberately holds a nonzero position when the cost-stage ruin fires, so the
§6.7.1 convention is actually exercised. `quantity == 7000.0` is the position the account was
carrying when it died; it is **not** a liquidation.

| id | requirement |
|----|-------------|
| X1 | the pinned X1 fixture reproduces every value above |
| X2 | series are **truncated**, not padded: `len(net_return) == ruin_period + 1`, `len(equity_curve) == ruin_period + 2` |
| X3 | **no `inf` anywhere**; `NaN` appears **only** in the four fields §6.7.2 permits, **only** at a cost-stage terminal row. Asserted on both X1 (no `NaN` at all) and X9 (`NaN` in exactly those four fields and nowhere else) |
| X4 | `total_return`, `max_drawdown`, `cagr`, `calmar` all exactly `-1.0`, EXACT, on both fixtures |
| X5 | ruin at period 0 -> `n_periods == 1`, dispersion metrics `nan`, no exception |
| **X6** | identity **(N-1) only** holds at the ruin period. MUST NOT assert (N-2). Asserts `ruin_decomposition_residual` at §17 tolerance: `-0.1988` on X1, `-0.5` on X9. **D1 fix — this assertion was `== -0.2` in v1.3 and failed 100% of the time** |
| X7 | `ruined=True` appears in `__repr__` |
| X8a | near-ruin (`NAV_end` small but positive) produces no `inf` in any output (W15) |
| X8b | `leverage_breach` fires when `max_gross_leverage` is set and breached (W15) |
| X9 | the pinned X9 fixture reproduces every value above, including the class-A pre-trade `quantity` and the four class-C `NaN` sentinels |
| X10 | `ruin_stage == "pnl"` on X1 and `"cost"` on X9; `terminal_position_convention == "pre_ruin_state"` on both (W17) |

### 18.9 Symbol activity, boundaries, missing data

| id | requirement |
|----|-------------|
| S1 | **staggered listing**: symbol B has no price for the first half of the sample and zero weight there; the backtest completes, `np.isfinite(equity_curve).all()` is `True`, and `asset_pnl_cash` equals the hand-computed single-symbol value at §17 tolerance. Must fail if inactive symbols are neutralised by `0 * NaN` |
| S2 | **delisting**: a held symbol losing `P[i+1]` raises `MissingPriceError` |
| S3 | closing a position (`q_prev != 0`, target `0`) at an invalid execution price raises |
| S4 | EXITING symbol does **not** require `P[i+1]`: valid `P[i]`, absent `P[i+1]`, target 0 -> completes |
| S5 | `symbol_state` matches §5.3 for every (period, symbol) on a fixture exercising all four states |
| S6 | INACTIVE symbols with `NaN`, `0.0` and negative prices all proceed silently and contribute exactly zero |
| S7 | `exit_unnamed=True` materialises explicit `0.0` targets for previously-held unnamed symbols in the strategy's own frame; without it, the same input raises `DataIntegrityError` per §5.4 (W17) |
| V1 | zero price on a symbol in use raises `InvalidPriceError` |
| V2 | negative price on a symbol in use raises `InvalidPriceError` |
| V3 | denormal/near-zero positive price does not silently produce `inf` quantity |
| T1 | rebalance flagged at `t > n-2-execution_lag` -> no trade, no crash, recorded in `unexecuted_rebalances` |
| T2 | terminal bar: `len(net_return) == n-1`, `len(equity_curve) == n`, no `NaN` in equity |
| T3 | two-bar backtest returns one period; one-bar raises `ConfigError` |
| T4 | `execution_instant()` unit-tested against the §4.3 table for both modes |
| T5 | a **regular** grid whose spacing disagrees with `config.frequency` raises `DataIntegrityError` (W17) |
| U1 | symbol with nonzero quantity absent from **`target_weights[t]`**, `t = i - execution_lag`, raises |
| U2 | symbol entering mid-sample proceeds silently |
| U3 | symbol absent from target columns with zero quantity is treated as target 0, no trade |
| D4t | irregular bar grid raises `DataIntegrityError` naming the offending pair and expected Δ |

### 18.10 Provenance

| id | requirement |
|----|-------------|
| PR1 | supplied provenance appears unmodified, field for field, including `field_type` and `time_range` |
| PR2 | absent provenance -> `provenance_supplied == False` |
| PR3 | any `native_or_proxy == "proxy"` -> `uses_proxy_data == True`, surfaced in `__repr__` |
| PR4 | `native_or_proxy == "proxy"` with `proxy_for` `None`/empty raises `DataIntegrityError` |
| PR5 | an all-`None` `DatasetProvenance` gives `provenance_complete == False` (W9) |
| PR6 | `UniverseProvenance` passes through unmodified; `survivorship_safe` is `None` when unsupplied and never defaults to `True`; `None`/`False` is surfaced in `__repr__` (W10) |

### 18.11 Coverage

Both `execution_mode` values across the engine suite. Every exception path in §11.2 — including
§6 Step 7's `AccountingError`, which requires the fault-injection hook mandated in §6 Step 7.
Every config validation in §15. Every state in §5.3. Both ruin stages.

---

## 19. Out of scope for QR-INFRA-001

No market data ingestion. No alpha. No strategy implementations beyond synthetic test fixtures.
No margin or liquidation modelling. No provenance population and no universe construction
(QR-DATA-001). No symbol-identity resolution. No live trading, keys, orders, withdrawals or
transfers.

---

## 20. Resolved design decisions

**20.1 — Costs on `NAV_pre`, sizing on `NAV_after_cost`. ACCEPTED.** The closed form is what
makes (N-1) exact. The `O(bps^2)` bound applies to **NAV**, not the fee: the fee differs from
the self-consistent solve by `O(turnover * bps)` relative — measured **2.00%** at 50 bps with
turnover 4. §18.4 N6 now pins the formula by equality, not by an inequality both
implementations satisfy.

**20.2 — Funding `period_start` fallback; start/end averaging REJECTED** (lookahead). A
run-level mode, not a per-event fallback.

**20.3 — Portfolio-level rebalance mask only in v1. ACCEPTED**, with `exit_unnamed=True`
supplying the ergonomics.

**20.4 — `gross_leverage` as an alias of `gross_exposure`. ACCEPTED**, renamed and
docstring-guarded.

**20.5 — Ruin floors at 0 and terminates. ACCEPTED**, with §6.7 specifying both stages, the
terminal row, the position convention and the (N-2) breakdown.

**20.6 — §5.4 raises rather than warning-and-flattening. ACCEPTED**, ergonomics upstream.

**20.7 — §2.1 regular grid required, no config tolerance. ACCEPTED.**

**20.8 — `max_funding_gap` declared, never inferred. ACCEPTED**, on `FundingCoverage`.

**20.9 — Counterfactual exempt from funding-coverage validation. ACCEPTED** (§9.4): it charges
no funding, and the actual path independently validates coverage wherever it holds exposure.
Conditioned on §9.5's barrier, which stops the other exception classes leaking.

**20.10 — Coverage records must be disjoint and non-touching, merged upstream; adjacent
records do NOT compose. ACCEPTED** (§7.2): composition would make a gap at the seam invisible
to condition 3.

**20.11 — §17 tolerances retained at `rtol=1e-12` / `1e-10`. ACCEPTED**: ~4 orders above
measured noise, ~10 orders below the smallest plausible real defect.

**20.12 — Terminal ruin row reports the pre-ruin position state, not a liquidation. ACCEPTED**
per owner ruling (§6.7.1), labelled `terminal_position_convention = "pre_ruin_state"`.

---

## 21. Open questions for the auditor

1. §6.7.2 emits `NaN` in four fields at a cost-stage ruin. §12.4 argues no metric is
   contaminated because metrics derive only from `net_return` and `equity_curve`. Confirm no
   metric, exposure or drag calculation reads `gross_return` or `funding_return`.
2. §9.5.1 argues that because the actual path completes first, anything the barrier catches is
   by construction irrelevant to the actual path. Is there a case where a counterfactual-only
   exception indicates a defect that *should* have failed the actual path?
3. §18.0.1 classifies E/E2/E3 as TOLERANCE despite the values being exactly representable
   powers of two times `1e6`. Too conservative, or correct?
4. §7.2 now requires coverage records to be non-touching. Does that impose an unreasonable
   burden on a loader stitching monthly fetches, given it must merge them first?
