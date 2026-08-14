# Backtest Contract — SPECIFICATION v1.5.1 (QR-INFRA-001)

Status: **FROZEN — 2026-08-14.**
Frozen by the platform owner's freeze rule after independent audit returned
**SPEC PASS WITH WARNINGS: 0 blocking / runtime-correctness findings, 8 editorial findings.**
Auditor ruling: *"Safe to FREEZE and hand to an implementer."*

Owner: Research Lead. Normative file. History preserved under `docs/spec_history/`.

**This specification is now the frozen contract for QR-INFRA-001.** Implementation may proceed
only on the platform owner's explicit instruction. Any change to this document requires a new
numbered revision, a preserved snapshot, and a fresh audit — it may not be edited in place.

Eight editorial findings (B1–B8) remain open and are recorded in §22. Each was independently
ruled incapable of affecting PnL, execution timing, accounting, data validity, reproducibility
or metric interpretation of any valid run.

This document is normative. Where this document and intuition disagree, this document wins.

**v1.5.1 is a NARROW CLEANUP, not a design revision.** No accounting was redesigned and no
feature was added. It resolves the five warnings from the v1.5 audit (which returned SPEC PASS
WITH WARNINGS, zero blocking) and removes the remaining internal inconsistencies.

---

## 0. Revision history

Snapshots are preserved before replacement and MUST NOT be overwritten:

    docs/spec_history/backtest_contract_v1.3.md
    docs/spec_history/backtest_contract_v1.4.md
    docs/spec_history/backtest_contract_v1.5.md

| version | verdict | outcome |
|---------|---------|---------|
| v1.0 | rejected by owner | implicit rebalancing; inconsistent drift; one-funding-event-per-bar |
| v1.1 | SPEC FAIL | 12 blocking (B1–B12). Core verified 9.9e-17 |
| v1.2 | SPEC FAIL | 7 blocking (C1–C7). Core verified 3.56e-16 |
| v1.3 | SPEC FAIL | 4 blocking (D1–D4) |
| v1.4 | SPEC FAIL | 1 blocking (E1) |
| v1.5 | **SPEC PASS WITH WARNINGS** | 0 blocking; 5 warnings (W-A…W-E). All fixtures reproduced, most bitwise |
| v1.5.1 | this document | W-A…W-E resolved; one normative NAV path; finiteness before ruin |

### v1.5 -> v1.5.1 changes

| ref | issue | resolution | § |
|-----|-------|------------|---|
| **W-B** | Ruin test preceded the finiteness guard, so a non-finite `NAV_end` was classified as **economic ruin** and `-inf` reached the result surface — forbidden by X3 | **Finiteness is now checked BEFORE ruin classification at BOTH stages** (Steps 3 and 10). An arithmetic blow-up can never be reported as a strategy outcome | §6.0 |
| **W-A** | NAV defined three ways (Step-6 carry, §8 recursion, §10 alias) — not simultaneously bitwise true | **One normative path.** The ledger `NAV_end` is authoritative; `equity_curve` **is** that ledger; `net_return` is **derived** from it. The recursion is demoted to a tolerance validation check | §6.1, §8 |
| **W-C** | Nine fixtures inherited values `BacktestConfig` does not default; CF2b's funding stream required back-solving | Every fixture fully specified; irrelevant fields stated as irrelevant; CF2b, CF7 and F22 funding streams written out | §18 |
| **W-D** | §9.5.1 claimed counterfactual-only exceptions arise only *beyond* actual ruin — false at a cost-stage ruin | Corrected: the guarantee comes from **isolation and ordering**, not from a price-set argument | §9.5.1 |
| **W-E** | CF7's `None` assertion and F22(b)/(c) had no classification rows | Added | §18.0.1 |
| opt. | Auditor's optional strengthening | CF3 drag assertion added — discriminates the naive implementation by **17.63%** | §18.7 |

**Control-flow renumbering (W-B).** §6.0's steps were renumbered to remove the `2b` letter
suffix that made the cost-stage skip set ambiguous. Mapping from v1.5: `2b -> 5`, `3 -> 6`,
`4 -> 7`, `5 -> 8`, `6 -> 9`, `7 -> 10 and 3`. §7.7.1's "reached Step 5" is now "reached
Step 8".

**Remaining warnings, documentation-only, accepted and NOT fixed.** None can affect PnL,
execution timing, accounting, data validity, reproducibility or metric interpretation:

- **W7** — a loader declaring `max_funding_gap = 8h` for an hourly-funding venue with 7 of 8
  events missing passes §7.7.2 silently. Intrinsic to trusting declared metadata, which is the
  deliberate choice over circular inference. Disclosed in §7.2.
- **W8** — §7.7.2 condition 3 spans a whole coverage window, so a venue outage in an unexposed
  stretch of a single declared record raises. Mitigated by multi-record declaration.
- **W14 (partial)** — tests N1t, N2t, F1, F7, F12 state their construction and full config but
  defer the final arithmetic to the test docstring rather than pinning a literal here. Each
  asserts a derived aggregate that follows mechanically from stated inputs.
- **W16** — §4.4's note that `i = 0` is unreachable is stated for `execution_lag = 1`; with
  lag 2, `i = 1` is also unreachable.
- **N12** — §10 surfaces `survivorship_safe` unconditionally while §13.2 requires it only when
  `None`/`False`. Unconditional is a superset.

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
name, **in the strategy's own output frame**, before it reaches the engine.

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

Classification depends only on `q_prev` and the target, never on a price, so it is always
decidable before validation.

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
rejected.

§5.5 admits denormal positive prices (e.g. `5e-324`), which are finite and `> 0` but can
produce non-finite downstream arithmetic. That case is caught by §6.0's finiteness guards, not
here — see §6.0 and §18.9 V3.

Prices for INACTIVE symbols are not "in use" and are deliberately unvalidated.

### 5.6 Notional

    notional[i, j] = 0.0 if quantity[i, j] == 0 else quantity[i, j] * P[i, j]

The zero branch is **assigned**, not computed.

---

## 6. Accounting sequence (NORMATIVE)

### 6.0 Control flow

PnL and funding accrue on **every** holding period. Trades occur **only** at rebalance
execution points. Stated as pseudocode because prose describing skipped steps proved readable
two ways.

> **Finiteness is checked BEFORE ruin classification at both stages (Steps 3 and 10).**
> A non-finite NAV is a numerical failure, never an economic outcome (W-B).

    NAV_pre[0] = initial_capital

    for i in 0 .. n-2:

        # -- Step 0: resolve, classify, validate execution prices --
        t            = i - execution_lag
        rebalance[i] = (0 <= t <= n-1) and rebalance_mask[t] == True
        w_target[i]  = target_weights[t] if rebalance[i] else undefined
        classify symbol_state[i, :] per 5.3
        validate P[i, j] per 5.5 for ENTERING, HELD, EXITING

        # -- Step 1: trade --
        if rebalance[i]:
            trade[i, j] = 0.0 if (q_prev == 0 and w_target[i, j] == 0)
                          else w_target[i, j] - w_pre[i, j]
            turnover[i] = sum_j abs(trade[i, j])
        else:
            trade[i] = 0 ; turnover[i] = 0

        # -- Step 2: costs --
        fee_cost[i]       = turnover[i] * NAV_pre[i] * fee_bps      / 10_000
        slippage_cost[i]  = turnover[i] * NAV_pre[i] * slippage_bps / 10_000
        NAV_after_cost[i] = NAV_pre[i] - fee_cost[i] - slippage_cost[i]

        # -- Step 3: FINITENESS GUARD (before any ruin classification) --
        if not isfinite(NAV_after_cost[i]):
            raise AccountingError

        # -- Step 4: cost-stage ruin test (NAV_after_cost is finite here) --
        if NAV_after_cost[i] <= 0:
            # COST-STAGE RUIN.
            # Steps 5, 6, 7, 8, 9, 10, 11 and 12 are NOT EXECUTED for this period.
            # No P[i+1] is validated or read. No quantity is sized.
            # No funding event is consumed. Terminal row per 6.7.2.
            emit terminal row (ruin_stage = "cost")
            TERMINATE the simulation
            break

        # -- Step 5: validate next-period prices --
        validate P[i+1, j] per 5.5 for ENTERING and HELD

        # -- Step 6: quantity ledger -- (5.1)

        # -- Step 7: asset PnL --
        active            = { j : quantity[i, j] != 0 }
        asset_pnl_cash[i] = sum_{j in active} quantity[i,j] * (P[i+1,j] - P[i,j])

        # -- Step 8: funding PnL -- (7.5), masked to active

        # -- Step 9: ending NAV (AUTHORITATIVE LEDGER VALUE) --
        NAV_end[i] = NAV_after_cost[i] + asset_pnl_cash[i] + funding_pnl_cash[i]

        # -- Step 10: FINITENESS GUARD (before any ruin classification) --
        if not isfinite(NAV_end[i]):
            raise AccountingError

        # -- Step 11: pnl-stage ruin test (NAV_end is finite here) --
        if NAV_end[i] <= 0:
            # PNL-STAGE RUIN. Terminal row per 6.7.2. TERMINATE.
            break

        # -- Step 12: derive net_return, carry the ledger --
        net_return[i] = NAV_end[i] / NAV_pre[i] - 1        # DERIVED, see 6.1
        NAV_pre[i+1]  = NAV_end[i]                          # same stored value

**Why Steps 3 and 10 precede Steps 4 and 11 (W-B).** `-inf <= 0` evaluates `True`. With the
guard after the ruin test, a `NAV_end` of `-inf` was classified as an **economic ruin** and
`-inf` reached the result surface — which §18.8 X3 forbids — silently converting an arithmetic
blow-up into a strategy outcome. Verified: `P[i] = 5e-324` (finite and `> 0`, so §5.5 admits
it) with a `-1.0` target gives `quantity = -inf`, `asset_pnl_cash = -inf`, `NAV_end = -inf`.
With the guard first, this raises `AccountingError`. The reorder is a no-op for every finite
`NAV_end`, and `NaN` and `+inf` raised under both orderings; only the `-inf` branch changes.
The X1 and X9 ruin fixtures are bit-for-bit unchanged (verified).

**Cost-stage skip set.** Exactly `{5, 6, 7, 8, 9, 10, 11, 12}`. No letter-suffixed steps exist,
so the set is enumerable and cannot be read two ways.

**Price-validation invariant.** The set Step 5 validates is exactly the set Step 7 reads.
`active = { j : quantity[i,j] != 0 }` equals `ENTERING ∪ HELD`: at a rebalance point
`quantity[i,j] == 0 ⟺ w_target[i,j] == 0 ⟺ ¬will_hold[i,j]` (valid because Step 4 guarantees
`NAV_after_cost > 0` before Step 6 runs), and at a non-rebalance point `quantity[i] = q_prev`
with `will_hold = (q_prev != 0)`. Step 6 sizes on `P[i]`, validated at Step 0. §7.5 never reads
`P[i+1]`. **No position can be established or valued on an unvalidated price.**

### 6.1 The single normative NAV path (W-A)

> **`NAV_end[i]`, produced by the Step-9 ledger, is authoritative. There is exactly one
> normative NAV state transition: `NAV_pre[i+1] = NAV_end[i]`, carrying the same stored
> double. `equity_curve` IS that ledger (§8). `net_return` is DERIVED from it.**

    net_return[i]  :=  NAV_end[i] / NAV_pre[i] - 1                    # Step 12, DEFINITION

`net_return` is **never** used to generate a second NAV path. Any independently reconstructed
equity series is a **validation check with tolerance**, never a normative definition.

v1.5 defined NAV three ways — the Step-6 carry, §8's recursion on `net_return`, and §10's
alias — which cannot all be bitwise true. The measured divergence was ~1e-16 relative
(~3e-11 USD), harmless numerically but a genuine ambiguity about which value is the NAV.

**Component returns** (attribution, all fractions of `NAV_pre[i]`):

    gross_return[i]     =  asset_pnl_cash[i]   / NAV_pre[i]
    fee_return[i]       = -fee_cost[i]         / NAV_pre[i]      # <= 0
    slippage_return[i]  = -slippage_cost[i]    / NAV_pre[i]      # <= 0
    funding_return[i]   =  funding_pnl_cash[i] / NAV_pre[i]      # signed

**(D) Decomposition identity — the substantive accounting check.** On every **non-ruin**
period, to §17 tolerance:

    gross_return[i] + fee_return[i] + slippage_return[i] + funding_return[i]  ==  net_return[i]

This is now the primary accounting assertion. Because `net_return` is derived from the ledger
rather than defined as this sum, (D) genuinely validates that the ledger and the attribution
agree; in v1.5 the equivalent statement was partly definitional. (D) does **not** hold at a
ruin period (§6.7.3).

The underlying identity was verified independently at 9.9e-17 and 3.56e-16 across randomized
multi-asset runs with all four components nonzero.

### 6.2 `gross_return` is an attribution component, not a path

`gross_return[i]` is the pre-cost return of the **actual** portfolio. Because Step 6 sizes on
`NAV_after_cost`, it already reflects the capital costs removed. It is a decomposition term,
**not** a zero-cost counterfactual, and does not compound into a meaningful gross equity curve.

There is deliberately **no** `gross_equity_curve`, no gross Sharpe and no gross drawdown.

`turnover[i] * NAV_pre[i]` is the **fee basis**, not traded notional. True traded notional is
`Σ_j | quantity[i,j] * P[i,j] - quantity[i-1,j] * P[i,j] |`. The result exposes the former as
`fee_basis_notional`.

### 6.3 Turnover

    turnover[i] = Σ_j | trade[i, j] |

**One-way, fraction of NAV, NO factor of 0.5.** `0 -> +1` gives 1; `+1 -> -1` gives 2; a
non-rebalance period gives exactly 0 regardless of drift. Measured against the drifted
`w_pre[i]`, never the previous target.

### 6.4 Masked aggregation

Every portfolio aggregate MUST be computed over an explicit active mask. No aggregate may
depend on a price belonging to an INACTIVE symbol.

### 6.5 Exposures

    gross_exposure[i] = Σ_j | w_held[i, j] |
    net_exposure[i]   = Σ_j   w_held[i, j]
    gross_leverage[i] = gross_exposure[i]

`gross_leverage` is an explicit alias carrying the docstring *"notional/NAV; NOT a margin
ratio; see §14 — liquidation is not modelled."* It is not named `leverage`.

### 6.6 Order of operations

Rebalance, pay, then earn. Costs are a function of `NAV_pre`; positions of `NAV_after_cost`.

### 6.7 Ruin — terminal row definition

**Ruin is an economic outcome, not an implementation exception** — and, per §6.0, a
**non-finite** NAV is never ruin. Two mutually exclusive stages:

    ruin_stage = "cost"   -- finite NAV_after_cost[i] <= 0 at Step 4
                 "pnl"    -- finite NAV_end[i]        <= 0 at Step 11

They cannot both fire: a cost-stage ruin terminates before Step 11 is reached.

#### 6.7.1 Position convention at the terminal row

> **Terminal quantities and exposures represent the LAST ECONOMICALLY VALID POSITION STATE
> IMMEDIATELY BEFORE RUIN. They are NOT a simulated liquidation.**

Chosen because §14 models no liquidation mechanics. Reporting zero positions would imply the
book was closed at a determinable price, which this engine cannot model.

`terminal_position_convention = "pre_ruin_state"` is carried on the result. Downstream reports
MUST NOT infer an exit price, exit timestamp or realised close from the terminal row.

- **pnl-stage ruin**: `quantity[i]` — established at Step 6, held through the period whose PnL
  caused the ruin
- **cost-stage ruin**: `quantity[i-1]` — the intended position was never sized, because Step 6
  never ran. The account died holding its prior book. **This reported quantity MUST NOT be read
  as exposure for funding-coverage purposes — see §7.7.1.**

#### 6.7.2 Terminal row field definitions

**(A)** state immediately before ruin, **(B)** terminal economic outcome, **(C)** genuinely
undefined because the period never economically completed.

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
| `fee_basis_notional[i]` | computed normally | computed normally | A |
| `rebalance_flag[i]` | as determined at Step 0 | as determined at Step 0 | A |
| `quantity[i, :]` | `quantity[i-1, :]` (pre-trade; see §7.7.1) | as sized at Step 6 | A |
| `notional[i, :]` | from `quantity[i-1]` and `P[i]` | from `quantity[i]` and `P[i]` | A |
| `positions[i, :]` (`w_held`) | `w_pre[i, :]` | as computed | A |
| `pre_trade_weights[i, :]` | computed normally | computed normally | A |
| `symbol_state[i, :]` | as classified at Step 0 | as classified at Step 0 | A |
| `gross_exposure`, `net_exposure`, `gross_leverage` | from `w_pre[i]` | from `w_held[i]` | A |
| `asset_pnl_cash[i]` | **`NaN`** | computed normally | C / A |
| `funding_pnl_cash[i]` | **`NaN`** | computed normally | C / A |
| `gross_return[i]` | **`NaN`** | computed normally | C / A |
| `funding_return[i]` | **`NaN`** | computed normally | C / A |
| `uncapped_ruin_return` | `NAV_after_cost[i]/NAV_pre[i] - 1` | `NAV_end[i]/NAV_pre[i] - 1` | B |
| `ruin_decomposition_residual` | `uncapped_ruin_return - (-1.0)` | `uncapped_ruin_return - (-1.0)` | B |

This table is exhaustive over the §10 per-period surface.

**The four `NaN` values at a cost-stage ruin are a documented terminal sentinel**, not a
defect. The holding period never economically occurred: no position was sized, so no asset PnL
and no funding accrued. Writing `0.0` would invent an economically meaningful value to avoid
`NaN`, which is forbidden.

At a pnl-stage ruin **no field is undefined** and no `NaN` appears anywhere. No `inf` appears
at either stage, because §6.0 Steps 3 and 10 raise before ruin classification.

#### 6.7.3 Termination and the decomposition at ruin

1. the simulation terminates; no period after `i` is computed
2. `quantity[i+1]` is never formed, so division by `NAV_pre[i+1] = 0` never occurs
3. artificial zero-return periods MUST NOT be appended
4. all series truncate; `equity_curve` ends at `T_{i+1}` with `0.0`

At a ruin period `net_return[i]` is **clipped to `-1.0`** rather than derived per §6.1 Step 12,
because the ledger value is floored to `0.0`. `0.0 / NAV_pre - 1 == -1.0` exactly, so the
clipped value and the derived value coincide bitwise; the clip is stated for clarity, not to
introduce a second rule.

**(D) does NOT hold at the ruin period**, and the correct statement differs by stage:

- **pnl-stage**: all four components are defined and sum to `uncapped_ruin_return`, not to
  `net_return[i] = -1`. The difference is `ruin_decomposition_residual`.
- **cost-stage**: `gross_return[i]` and `funding_return[i]` are `NaN`, so the four-component
  sum is `NaN`. Only the **defined** components sum meaningfully:
  `fee_return[i] + slippage_return[i] == uncapped_ruin_return`, verified bitwise on §18.8 X9
  (`-0.75 + -0.75 == -1.5`).

§18.8 X6 asserts the residual and the cost-stage two-component sum, never (D), at a ruin period.

`ruin_timestamp = T_{i+1}` at **both** stages, for index consistency with §8. At a cost-stage
ruin the account economically died at `T_i`, when costs were paid; `T_{i+1}` is the timestamp
of the terminal equity observation, not the instant of death. Deliberate, not an error.

`ruined = True` MUST appear in `__repr__`, any summary table, and any report the result feeds.

### 6.8 Leverage tripwire

Optional `max_gross_leverage: float | None = None`. Any period with
`gross_exposure[i] > max_gross_leverage` sets `leverage_breach = True` and records timestamps.
It does **not** alter the simulation.

At a cost-stage terminal row the tripwire reads `w_pre[i]`, a genuinely held book valued at a
validated `P[i]`, so firing there is correct — unlike funding coverage (§7.7.1).

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
percentage. An hourly rate of one basis point is `0.0001`.

### 7.2 Funding coverage metadata

    FundingCoverage:
        symbol           : str
        coverage_start   : tz-aware UTC
        coverage_end     : tz-aware UTC
        max_funding_gap  : Timedelta
        source_venue     : str

A symbol MAY have multiple records. **Records for a symbol MUST be pairwise disjoint with
non-intersecting closures** — touching or overlapping records MUST be merged by the loader
before reaching the engine, else `DataIntegrityError`.

> **Guidance for loader authors.** Merging is expected and is an ordered linear pass over
> declared metadata, not an inference. A loader stitching monthly fetches should merge
> `[Jan, Feb]` and `[Feb, Mar]` into one record; §7.7.2 condition 3 then re-scans the union
> window, so a genuine seam gap is still caught. A **real** data gap must be declared as two
> records separated by a genuine, non-touching interval — never as two touching records.

`max_funding_gap` lives here, not in `BacktestConfig`: it is a venue/dataset property.
`max_funding_gap <= 0` raises `DataIntegrityError`.

Coverage is **declared by the data layer, never inferred**. Inferring cadence is circular: the
stream being validated is the stream that would be inferred from.

> **Known residual risk (W7):** a loader declaring `max_funding_gap = 8h` for a venue that
> funds hourly, with 7 of every 8 events missing, passes §7.7.2 condition 3 with gaps of
> exactly 8h and undercharges funding 8x silently. Declared metadata is trusted by design.

### 7.3 Sign convention

    funding_rate > 0  =>  LONGS PAY SHORTS

A long with a positive rate produces **negative** funding PnL. The minus sign in §7.5 is the
only place this convention is applied.

### 7.4 No lookahead

Funding is consumed only as a realised cost, never as a signal input.

### 7.5 Aggregation into holding periods

For period `i` spanning `[T_i, T_{i+1})`, select events `e` for symbol `j` with

    T_i <= e.timestamp < T_{i+1}

**Half-open**, so a boundary event is charged to exactly one period — the later one. Events
before `T_0` or at/after `T_{n-1}` are excluded and counted in `funding_events_excluded`.

    basis == "event_price":   notional_e = quantity[i, j] * e.notional_price
    basis == "period_start":  notional_e = quantity[i, j] * P[i, j]

    funding_pnl_cash[i] = - Σ_{j ∈ active} Σ_e notional_e * e.funding_rate

`quantity[i, j]` is the **post-trade** quantity as sized at Step 6. The outer sum is masked to
the active set.

### 7.6 `funding_notional_basis` is a run-level mode

    funding_notional_basis = "event_price" | "period_start"
    # REQUIRED when funding_mode == "required"; "not_modelled" when disabled

| mode | behaviour |
|------|-----------|
| `"event_price"` | **Every** event applied to a nonzero position MUST carry a finite, strictly positive `notional_price`; otherwise `FundingDataError`. Venue-accurate for Hyperliquid's oracle-price basis. |
| `"period_start"` | `notional_price` is **ignored entirely** — not required, not read, not validated. |

**No per-event fallback and no `"mixed"` basis.**

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

#### 7.7.1 The funding-accruing exposure interval

> **Funding coverage is required exactly for the intervals in which the engine could actually
> charge funding — no more, no less.**

Period `i` is **funding-accruing for symbol `j`** if and only if **both**:

1. period `i` **reached Step 8** of §6.0 — i.e. it was not terminated by a cost-stage ruin at
   Step 4, and
2. `quantity[i, j] != 0` **as sized at Step 6**

Condition 1 matters because a cost-stage ruin terminates before Step 8, so the holding interval
`[T_i, T_{i+1})` never economically occurs and consumes no funding event. Coverage MUST NOT be
required for it.

§6.7.1 *reports* `quantity[i] = quantity[i-1]` at a cost-stage terminal row — nonzero by design
(§18.8 X9 pins `7000.0`). That **reported** quantity MUST NOT be read as exposure for coverage
purposes. Condition 2 refers to the quantity **as sized at Step 6**, which at a cost-stage ruin
does not exist.

#### 7.7.2 Coverage conditions, validated incrementally

> **Coverage is validated INCREMENTALLY, as the simulation reaches each period.** The engine
> MUST NOT validate coverage for the whole declared span up front, and MUST NOT require
> coverage for periods the simulation never reaches.

This follows from §7.7.1 condition 1: whether a period is funding-accruing is not knowable
before the simulation arrives there, because a cost-stage ruin at an earlier period ends the
run. An engine that pre-validates the entire span would raise `FundingDataError` on intervals
that never occur — and would fail §18.5 F22(a).

Under `"required"`, for every symbol `j` and every **funding-accruing** period `i`, raise
`FundingDataError` unless all hold:

1. at least one `FundingCoverage` record exists for `j`
2. `[T_i, T_{i+1}) ⊆ [coverage_start, coverage_end]` for a **single** record
3. within that record's window, the augmented sequence
   `[coverage_start] + sorted(events in window) + [coverage_end]` has **no consecutive gap
   exceeding `max_funding_gap`**

A gap exactly equal to `max_funding_gap` is **accepted** ("exceeds" means strictly greater).

Symbols with no funding-accruing period need no funding data.

**Soft check (non-fatal):** if modal event spacing within a coverage window is more than 2x
below `max_funding_gap`, set `funding_gap_tolerance_suspicious = True`.

---

## 8. The equity ledger

> **`equity_curve` IS the authoritative NAV ledger of §6.1. It is not a derived series and it
> is not reconstructed from returns.**

| row | timestamp | value | relationship |
|-----|-----------|-------|--------------|
| 0 | `T_0` | `initial_capital` | `= NAV_pre[0]`, same stored value |
| `k` (1 .. n-2) | `T_k` | `NAV_end[k-1]` | `= NAV_pre[k]`, same stored value |
| `n-1` | `T_{n-1}` | `NAV_end[n-2]` | same stored value |

    len(equity_curve) == n
    len(net_return)   == n - 1 == len(equity_curve) - 1
    n_periods         := len(net_return)

Because `equity_curve[k]` and `NAV_pre[k]` are **the same stored double**, comparing them is
EXACT-class (§17). Recomputing `net_return[i]` as `equity_curve[i+1]/equity_curve[i] - 1`
likewise reproduces §6.1 Step 12 bitwise, since it divides the same two stored values.

**Reconstruction is a validation check, never a definition (W-A).** The relation

    equity_curve[k+1] ≈ equity_curve[k] * (1 + net_return[k])

is asserted at §17 tolerance, **not bitwise**: `NAV_pre[k] * (NAV_end[k]/NAV_pre[k])` does not
round-trip to `NAV_end[k]` in IEEE754. Measured bitwise failure rate 32.1%, worst relative
error 4.32e-16.

**Every equity value pinned in §18 is a ledger value** — the Step-9/Step-12 carry — except
§18.6, which is a pure-metrics unit test with an explicitly stated test-local construction.
v1.5 pinned a mixture of ledger and `cumprod` doubles under a rule requiring one path.

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

Run the full §6.0 sequence a second time with the same `target_weights`, `rebalance_mask`,
`execution_mode`, `execution_lag`, `initial_capital`, prices and bar index, and with

    fee_cost[i] = 0 ,  slippage_cost[i] = 0 ,  funding_pnl_cash[i] = 0    for all i

The counterfactual maintains its **own** quantity ledger and its **own** NAV ledger.

**This is not the actual gross returns compounded.** With `NAV_after_cost = NAV_pre`, Step 6
sizes different quantities from the first rebalance and the paths diverge (§18.7 CF3).

### 9.2 Drag attribution — comparability rule

    total_drag_return = counterfactual_total_return - total_return
    cagr_drag         = counterfactual_cagr         - cagr

> **Drag fields are populated ONLY when ALL of the following hold:**
>
> 1. `counterfactual_status == "COMPLETED"`
> 2. the actual path completed the comparable horizon (`ruined == False`)
> 3. both paths span the same number of periods
>
> **Otherwise `total_drag_return` and `cagr_drag` are `None` and `drag_comparable = False`.**

**A drag statistic must never compare different horizons.** If one path ruins early,
differencing its total return against the other's compares 2 periods against 4 and produces a
number that flatters or damns cost attribution by an arbitrary factor.

Drag is **positive when costs dominate and legitimately negative when funding is net income**.
It is NOT decomposable into components by differencing counterfactual paths, because they
interact through the compounding NAV. The additive (D) decomposition is the correct tool for
component attribution.

`counterfactual_total_return` and `counterfactual_cagr` are exposed directly on the result
surface (§10).

### 9.3 Strict value isolation

The two paths are fully independent. Each runs over its own full range and each handles ruin
per §6.7 independently:

    ruined                        ruin_timestamp                ruin_stage
    counterfactual_ruined         counterfactual_ruin_timestamp

The counterfactual may NEVER modify the actual path's length, equity, ruin state or metrics.

### 9.4 Counterfactual auxiliary behaviour

- §5.3 classification and §5.5 price validity apply identically **within the barrier** (§9.5)
- §7.7 funding coverage is **not** validated for the counterfactual: it charges no funding, so
  no result it produces can be misstated by a coverage hole, and the actual path independently
  validates coverage over every funding-accruing period
- §6.8's tripwire applies, reported as `counterfactual_leverage_breach`
- `unexecuted_rebalances` is identical by construction and is not duplicated

### 9.5 Exception isolation — the counterfactual barrier

> **The counterfactual is diagnostic only and MUST NOT be capable of changing whether the
> actual backtest succeeds. This includes exceptions. The actual path is authoritative.**

1. **The actual path executes first and to completion**, independently. The actual result MUST
   be fully computable without any counterfactual state.
2. **The counterfactual runs afterwards, inside an exception barrier**, catching
   `DataIntegrityError` (including `InvalidPriceError` and `MissingPriceError`),
   `FundingDataError` and `AccountingError` raised **from counterfactual execution**.
3. A counterfactual `FAILED` state MUST NOT convert a valid actual backtest into an exception.

    counterfactual_status ∈ { "NOT_COMPUTED", "COMPLETED", "RUINED", "FAILED" }
    counterfactual_error  : str | None
    counterfactual_ruined : bool
    counterfactual_ruin_timestamp : Timestamp | None

| status | meaning |
|--------|---------|
| `NOT_COMPUTED` | `compute_counterfactual = False` |
| `COMPLETED` | ran to the end of the sample without ruin |
| `RUINED` | ran and ruined per §6.7; `counterfactual_ruined = True` |
| `FAILED` | raised inside the barrier; `counterfactual_error` populated; all `counterfactual_*` series and drag fields `None`; `drag_comparable = False` |

#### 9.5.1 Why the barrier is sound (corrected, W-D)

**Errors that invalidate INPUT DATA required by the ACTUAL path are NOT counterfactual failures
and MUST propagate.** The distinction is decided by **ordering**, not by reasoning about which
data each path happens to touch:

- **actual-path data integrity error** — raised while the actual path executes, before the
  barrier is entered. Propagates. The backtest fails, correctly.
- **counterfactual-only diagnostic failure** — raised inside the barrier, after the actual
  result is already complete and authoritative. Recorded as `FAILED`.

**The safety guarantee is isolation and ordering, not a claim about price sets.** By the time
the barrier is entered, the actual path has already validated and consumed everything it needs;
no exception raised afterwards can retroactively invalidate a completed, authoritative result.

**Correction.** v1.5 claimed the counterfactual's exceptions "arise only in periods **beyond**
the actual path's ruin". That is false at a **cost-stage** ruin: the actual skips Step 5 and
never validates `P[i+1]`, while the zero-cost counterfactual — which does not ruin there — does
validate and read it. A counterfactual-only exception can therefore arise **at** a shared
period, not only after one. This does not weaken the barrier: the counterfactual's required
price set is a **superset** of the actual's over shared periods, and the actual's requirements
are all discharged before the barrier is entered.

The barrier MUST catch only the enumerated types. `KeyboardInterrupt`, `MemoryError`,
`SystemExit` and programming errors (`TypeError`, `AttributeError`, `NameError`) MUST propagate.

#### 9.5.2 Mandatory invariant

> For any inputs where the actual path is valid, `compute_counterfactual=False` and
> `compute_counterfactual=True` MUST produce **bit-identical actual-path outputs.**

Holds unconditionally, including when the counterfactual is `FAILED` or `RUINED`.

---

## 10. Result surface

**Equity ledger** (§8, `n_periods + 1` rows) — `equity_curve`. `NAV_pre[i]` **is**
`equity_curve[i]` (same stored value); `NAV_after_cost[i]` is
`equity_curve[i] - fee_cost[i] - slippage_cost[i]`.

**Per-period series** (`n_periods` rows, row `i` = period `[T_i, T_{i+1})`) — `net_return`,
`gross_return`, `fee_return`, `slippage_return`, `funding_return`, `fee_cost`,
`slippage_cost`, `funding_pnl_cash`, `asset_pnl_cash`, `fee_basis_notional`, `turnover`,
`gross_exposure`, `net_exposure`, `gross_leverage`, `rebalance_flag`.

Field names are **singular** and match §6.1 exactly.

**Per-period frames** (period x symbol) — `quantity`, `notional`, `positions` (`w_held`),
`pre_trade_weights` (`w_pre`), `target_weights` (as supplied), `trades`, `symbol_state`.

`rebalance_flag[i]` is the **execution-point** indicator, not the input mask at signal bar `t`.

**Counterfactual** — `counterfactual_gross_equity`, `counterfactual_gross_return`,
`counterfactual_gross_metrics`, `counterfactual_total_return`, `counterfactual_cagr`,
`counterfactual_status`, `counterfactual_error`, `counterfactual_ruined`,
`counterfactual_ruin_timestamp`, `counterfactual_leverage_breach`, `total_drag_return`,
`cagr_drag`, `drag_comparable`.

**Metrics** — `metrics`, per §12, consuming the authoritative equity ledger.

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

Any field named `_pnl` returning a ratio is a defect.

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

Funding: coverage failures (§7.7.2); missing or invalid `notional_price` under `"event_price"`.

Config: `execution_lag < 1`; missing `fee_bps`, `slippage_bps`, `funding_mode`, `frequency`,
`annualization_factor`; `funding_notional_basis` absent under `funding_mode = "required"`.

**Accounting: non-finite `NAV_after_cost` (§6.0 Step 3) or non-finite `NAV_end` (§6.0
Step 10).** Both are checked **before** the corresponding ruin classification, so a non-finite
NAV always raises and is never reported as `ruined = True`.

### 11.3 May proceed silently

Only INACTIVE symbols (§5.3). Their prices may be absent, `NaN`, zero or negative and are
never read. This is the **only** silent path.

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

Metrics consume the **authoritative equity ledger** (§8) and the derived `net_return`.

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

- **Sharpe and Sortino use ARITHMETIC annualization**.
- **Calmar uses GEOMETRIC (CAGR)** — max drawdown is a realized-path quantity.
- The field is named `cagr`. There is **no** field named `annualized_return`.

### 12.2 Other pinned choices

- volatility uses SAMPLE std, `ddof=1`
- downside deviation divides by the count of **all** periods, not only losing periods.
  Consequently Sortino and Sharpe use different estimators and do **not** coincide on a
  symmetric distribution. **No identity relating them is asserted anywhere in this document.**
- max drawdown is signed negative
- `risk_free_per_period` and `mar_per_period` are **scalars**, default 0.0

### 12.3 Degenerate cases

Return `nan` — never 0, never an exception: `annualized_volatility == 0` -> `sharpe`;
`downside_dev_ann == 0` -> `sortino`; `max_drawdown == 0` -> `calmar`; `n_periods < 2` -> every
dispersion-based metric.

### 12.4 Metrics under ruin

Metrics use the actual observations through the ruin period. No padding.

- `total_return = -1.0` exactly
- `max_drawdown = -1.0` exactly
- `cagr = -1.0` exactly. Verified: `0.0 ** (af/n_periods)` returns `0.0`, no domain error
- `calmar = -1.0` exactly
- `sharpe`, `sortino`, `annualized_volatility` computed normally over the truncated series
- ruin at period 0 -> `n_periods == 1` -> dispersion metrics `nan`, no exception

At a **cost-stage** ruin, `gross_return` and `funding_return` contain `NaN` at the terminal row.
Metrics are computed from `net_return`, `equity_curve` and `turnover` only, all fully defined,
so **no metric is contaminated by the terminal sentinel.** Any future metric derived from
`gross_return` or `funding_return` MUST handle the sentinel explicitly; adding one without a
guard is a defect.

### 12.5 Short-horizon CAGR is an extrapolation (interpretation note)

`cagr` raises the total growth factor to `af / n_periods`. On a short fixture this exponent is
enormous — with `af = 365` and `n_periods = 2` it is `182.5`, so a 9.78% two-period gain
reports `cagr ≈ 2.49e7` (verified: `1.0978 ** 182.5 = 24_859_296.045`).

Arithmetically correct and **not** a defect, but `cagr` and `calmar` are meaningless on short
samples and MUST NOT be pinned as expected values in short fixtures (§18.7 CF2 pins
`total_drag_return` for exactly this reason). Any report over a short horizon should present
`total_return` and suppress or footnote `cagr`.

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

The engine does not build provenance and does not validate its content beyond obligations 3–5.

1. never drop or overwrite supplied provenance
2. absent provenance -> `provenance_supplied = False`
3. any `native_or_proxy == "proxy"` -> `uses_proxy_data = True`, surfaced in `__repr__`
4. `native_or_proxy == "proxy"` with `proxy_for` empty or `None` -> `DataIntegrityError`
5. `provenance_complete = True` only when every supplied object has non-`None` `source_venue`,
   `field_type`, `time_range` and `native_or_proxy`

### 13.2 Universe provenance

CLAUDE.md ranks "current Hyperliquid listings retrospectively assumed to have existed
throughout historical periods" alongside proxy data as a policy violation.

    UniverseProvenance:
        universe_source      : str | None
        universe_asof_policy : str | None    # "point_in_time" | "static_current" | ...
        listing_data_source  : str | None
        survivorship_safe    : bool | None
        notes                : str | None

1. never drop supplied universe provenance
2. `survivorship_safe` is `None` when not supplied and MUST NOT default to `True`
3. `survivorship_safe` in `(None, False)` -> surfaced in `__repr__`

**The engine does not verify survivorship safety and cannot.** Pass-through contract so
QR-DATA-001 can populate it without changing `BacktestResult`.

---

## 14. Liquidation scope

**Liquidation and margin are NOT modelled.** `liquidation_modelled = False`, always.

Correct arithmetic for `gross_exposure > 1` does **not** mean a position was survivable
on-venue. Any result at meaningful leverage is an **upper bound** on achievable performance.
§6.7.1's terminal-position convention is explicitly **not** a liquidation model.

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
iteration order. Symbols sorted deterministically. Two runs MUST compare exactly equal, across
processes and `PYTHONHASHSEED` values.

---

## 17. Floating-point policy

**Exact equality (`==`) is permitted ONLY for genuinely discrete state or for the SAME STORED
VALUE:**

- booleans and flags; boolean masks and `symbol_state` classifications
- indices, timestamps, lengths, event counts
- **quantities carried forward unchanged** across a non-rebalance period (stored state)
- **quantities, weights and notionals assigned literal `0.0`**
- **`equity_curve[k]` compared with `NAV_pre[k]`** — the same stored double (§8)
- values that are exactly representable **and** reached by a single arithmetic path

**Documented tolerances are REQUIRED for everything reached by a different but mathematically
equivalent floating-point path**, even when the two paths are algebraically identical:

| quantity | tolerance |
|----------|-----------|
| equity reconstruction check §8 | `rtol=1e-12`, `atol=1e-9` (USD) |
| decomposition identity (D), `ruin_decomposition_residual` | `rtol=1e-12`, `atol=1e-15` |
| reconstructed / derived weights, exposures | `rtol=1e-12`, `atol=1e-15` |
| turnover and cost assertions | `rtol=1e-12`, `atol=1e-15` |
| metrics, drag statistics | `rtol=1e-10`, `atol=1e-12` |

Measured evidence: the equity reconstruction fails bitwise on **32.1%** of periods (worst
relative error 4.32e-16); the `w_held` rebalance branch on **13.9%**; `q -> w -> q` on
**17.8%**; ledger vs `cumprod` on the §18.6 fixture by **2 ulp**.

Justification of the levels — the smallest *real* defect class is orders of magnitude above:

| defect | relative magnitude |
|---|---|
| `ddof=0` instead of `ddof=1` on Sharpe (n=8) | 6.9e-2 |
| one-bar execution lag error (fixture E) | 1.0e0 |
| naive per-period drag summation (CF3) | 1.76e-1 |
| `period_start` vs true funding notional on a 15% move | 7.0e-2 |
| fee charged on `NAV_after_cost` vs `NAV_pre` | 2.0e-2 |
| **measured floating-point noise** | **4.3e-16** |

---

## 18. Mandatory tests

### 18.0 Assertion classification and fixture completeness

#### 18.0.1 Classification table (EXACT vs TOLERANCE)

> **Every numeric assertion in §18 is classified below.** Normative, produced by mechanical
> review of each assertion individually.

Float-equality defects appeared in four consecutive revisions — v1.1 R4, v1.2 N5t and §8, v1.3
X6 — the last inside the revision that introduced §17.

| test | assertion | class | justification |
|------|-----------|-------|---------------|
| A | zero PnL / fees / turnover | EXACT | assigned literals, excluded from summation |
| B, C, D | equity / total_return | TOLERANCE | multi-step arithmetic |
| E, E2, E3 | final NAV | TOLERANCE | exact today (verified bitwise), but tolerance costs nothing — `rtol=1e-12` still separates 1e6 from 2e6 — and avoids making the flagship anti-lookahead tests bit-fragile to a legitimate reassociation |
| E4 | earlier periods unchanged | EXACT | identical arithmetic path |
| G, H | turnover 1 / 2 | TOLERANCE | `w_target - w_pre`; `w_pre` derived |
| I, J | fee / slippage cost | TOLERANCE | derived from turnover |
| L, M, N | exposures | TOLERANCE | derived from `w_held` |
| P, P2 | determinism | EXACT | §16 |
| R1 | zero interim turnover and fees | EXACT | assigned literals |
| R2 | no-trade branch / trade magnitudes | EXACT for the zero branch, TOLERANCE for magnitudes | |
| R3 | weights drift | TOLERANCE | derived |
| R4 | `quantity[i] == quantity[i-1]` | EXACT | §5.1 direct carry-forward of stored state |
| R5 | quantity changes only where `rebalance_flag` | EXACT | boolean |
| R6 | `rebalance_flag` index under `lag=2` | EXACT | index/boolean |
| N1t, N2t | hand-computed `w_pre` | TOLERANCE | derived |
| **N3t** | `equity_curve[i]` **is** `NAV_pre[i]`; `net_return[i] == equity_curve[i+1]/equity_curve[i] - 1` | **EXACT** | §8 — the same stored doubles, single arithmetic path (W-A) |
| N5t | equity reconstruction check | TOLERANCE | §17 row 1 — measured 32.1% bitwise failure |
| N6t | `NAV_after_cost == NAV_pre*(1 - turnover*bps_total)` | TOLERANCE | §17 row 4 |
| N7t | decomposition identity (D) | TOLERANCE | §17 row 2 |
| F1, F7, F12 | funding aggregates | TOLERANCE | sums of products |
| F2, F10 | counted once / excluded counts | EXACT | integer counts |
| F4, F13, F15, F18, F21 | raises | EXACT | exception type |
| F5 | funding exactly 0 | EXACT | assigned literal |
| F6 | funding sign | EXACT | sign comparison |
| F8 | windows differ per mode | TOLERANCE | compares two funding magnitudes |
| F9, F14, F16, F19 | does NOT raise | EXACT | absence of exception |
| F17 | gap `== max_funding_gap` accepted | EXACT | integer-nanosecond Timedelta |
| F20 | `funding_gap_tolerance_suspicious` set | EXACT | boolean |
| **F22(a)** | **no `FundingDataError` on the un-reached interval** | **EXACT** | absence of exception (W-E) |
| **F22(b)** | **`ruined == True`, `ruin_stage == "cost"`** | **EXACT** | boolean / string (W-E) |
| **F22(c)** | **`FundingDataError` IS raised for the reached, uncovered interval** | **EXACT** | exception type (W-E) |
| M1, M2, M5 | Sharpe, Sortino, total_return, cagr, max_drawdown | TOLERANCE | §17 metrics row |
| M3 | degenerate cases `nan` | EXACT | `isnan` predicate |
| M4 | cagr differs from arithmetic annualization | TOLERANCE | inequality with margin |
| M6 | drawdown captures first-period loss | TOLERANCE | inequality with margin |
| CF1 | counterfactual equals actual at zero cost | EXACT | `x - 0.0 - 0.0 == x` for finite `x`; with zero costs `NAV_after_cost` is bitwise `NAV_pre`, so the quantity ledger is bit-identical |
| CF2 | `total_drag_return` values and signs | TOLERANCE for values, EXACT for signs | §17 metrics/drag row |
| **CF2c** | **CF3-fixture `total_drag_return`** | **TOLERANCE** | §17 metrics/drag row |
| CF3 | `cumprod(1+gross) != cf_equity` | TOLERANCE | inequality with margin |
| CF4 | hand-computed counterfactual | TOLERANCE | multi-step |
| CF5 | timing/mask respected | EXACT | index/boolean |
| CF6 | lengths, ruin flags, status, drag `None` | EXACT | integers / booleans / identity |
| **CF7** | **lengths, ruin flags, status, AND `total_drag_return is None`, `drag_comparable == False`** | **EXACT** | identity / boolean (W-E) |
| CF8, CF9 | actual bit-identical across `compute_counterfactual` | EXACT | §9.5.2 mandates bitwise |
| CF10 | `drag_comparable == False`, drag `None` | EXACT | boolean / identity |
| CF11 | actual-path error propagates | EXACT | exception type |
| X1 | equity, net_return, uncapped, residual | TOLERANCE, except `equity[-1] == 0.0` EXACT | |
| X2 | series lengths | EXACT | integers |
| X3 | no `inf` **anywhere**; `NaN` only where §6.7.2 permits | EXACT | predicate on the enumerated field set |
| X4 | `total_return`, `max_drawdown`, `cagr`, `calmar` all `-1.0` | EXACT | verified bitwise on both X1 and X9 |
| X5 | `n_periods == 1`, metrics `nan` | EXACT | integer / predicate |
| X6 | `ruin_decomposition_residual`; cost-stage two-component sum | TOLERANCE | true value `-0.19880000000000008775`, not the literal `-0.1988` |
| X7 | `ruined=True` in `__repr__` | EXACT | string containment |
| X8a | near-ruin finiteness | EXACT | `isfinite` predicate |
| X8b | `leverage_breach` fires | EXACT | boolean |
| X9 | cost-stage ruin row | TOLERANCE, except lengths, `equity[-1] == 0.0`, quantity carry and `NaN` predicates EXACT | |
| X10 | `ruin_stage`, `terminal_position_convention` | EXACT | strings |
| **X11** | **non-finite NAV raises `AccountingError`, `ruined` is NOT set** | **EXACT** | exception type / boolean (W-B) |
| S1 | equity finite / `asset_pnl_cash` value | EXACT for `isfinite`; TOLERANCE for the value |
| S2, S3 | raises | EXACT | exception type |
| S4 | **EXITING** symbol completes without `P[i+1]` | EXACT | absence of exception |
| S5 | `symbol_state` matches §5.3 | EXACT | categorical |
| S6 | INACTIVE contributes exactly zero | EXACT | assigned literals, excluded from summation |
| S7 | `exit_unnamed` materialises `0.0` | EXACT | assigned literal |
| V1, V2 | raises `InvalidPriceError` | EXACT | exception type |
| V3 | denormal price -> `AccountingError`, not ruin | EXACT | exception type / boolean |
| T1 | recorded in `unexecuted_rebalances` | EXACT | membership |
| T2, T3 | lengths / raises | EXACT | integers / exception type |
| T4 | `execution_instant()` values | EXACT | timestamps |
| T5 | grid/frequency mismatch raises | EXACT | exception type |
| U1, U3 | raises / no trade | EXACT | exception type / assigned literal |
| U2 | proceeds silently | EXACT | absence of exception |
| D4t | irregular grid raises | EXACT | exception type |
| PR1–PR6 | provenance field equality, flags | EXACT | field equality / booleans |

Test IDs in §18.4 are suffixed `t` (`N1t`…`N7t`); `D4t` disambiguates from defect ID D4.

#### 18.0.2 Fixture completeness rule

> **Every fixture MUST be constructible from this specification alone. No fixture may inherit
> a value that `BacktestConfig` does not default. Where a field is irrelevant to a fixture,
> that is stated explicitly rather than left undefined.**

`fee_bps`, `slippage_bps`, `frequency`, `funding_mode` and `annualization_factor` have **no
defaults** (§15), so any fixture depending on them MUST state them.

Each §18 subsection carries a **Config** header applying to every fixture in it, with
per-test overrides inline.

**No fixture may use `execution_lag = 0`**, because §4.2 forbids it in a production
`BacktestConfig`. Any test needing artificial same-instant execution MUST use the lower-level
accounting helper `_step_period(...)`, exercising §6.0 Steps 1–12 directly with `w_target`
supplied per period, and MUST be labelled `# LOWER-LEVEL HELPER — not a production config`.

Values marked **[computed]** were calculated by the Research Lead and are reproduced to be
**independently re-derived by the auditor**, not accepted on authority.

#### 18.0.3 Cross-section consistency pass

Before submission, every mandatory test was checked against: (1) the normative rule it tests;
(2) whether the fixture satisfies **all preconditions of that rule**; (3) whether all required
config fields are specified; (4) recomputation of every numeric expected value; (5) EXACT vs
TOLERANCE classification; (6) whether the test would fail under the incorrect behaviour it
targets.

Check 2 exists because v1.4's E1 was a test that violated the preconditions of the rule it
tested. Check 3 exists because v1.5's W-C found nine fixtures inheriting undefaulted config.
Any future revision that adds a precondition to a rule MUST re-run this pass over every test
touching the quantity that rule governs.

---

### 18.1 Anti-lookahead

**Config (E, E2, E3):** `initial_capital = 1_000_000`, `frequency = "1d"`, `fee_bps = 0`,
`slippage_bps = 0`, `funding_mode = "disabled"`, `compute_counterfactual = False`, single
symbol, target weight `1.0`. `annualization_factor = 365` (no metric is asserted, so its value
is immaterial; stated because it has no default).

**Test E — lag discrimination.** `execution_mode = "next_open"`, mask True at **bar 2 only**:

    open = [100, 100, 100, 200, 200, 200]        # 6 bars, 5 holding periods
    holding-period returns = [0, 0, 1.0, 0, 0]

    execution_lag = 1  ->  final NAV == 1_000_000     [computed]
    execution_lag = 0  ->  final NAV == 2_000_000     [computed]   (lower-level helper)

Both assertions mandatory.

**Test E2 — execution mode discrimination.** `execution_lag = 1`, mask True at bar 2:

    open  = [100, 100, 100, 100, 200, 200]    -> r_open  = [0, 0, 0, 1.0, 0]
    close = [100, 100, 100, 200, 200, 200]    -> r_close = [0, 0, 1.0, 0, 0]

    execution_mode = "next_open"   ->  final NAV == 2_000_000     [computed]
    execution_mode = "next_close"  ->  final NAV == 1_000_000     [computed]

**Test E3 — `execution_lag = 2`.** `execution_mode = "next_open"`, mask True at bar 2:

    open = [100, 100, 100, 200, 400, 400, 400]    # 7 bars

    execution_lag = 0  ->  final NAV == 4_000_000     [computed]   (lower-level helper)
    execution_lag = 1  ->  final NAV == 2_000_000     [computed]
    execution_lag = 2  ->  final NAV == 1_000_000     [computed]

**Test E4.** Perturb a price after `P[i+1]`; earlier periods bit-identical.

### 18.2 Core accounting

**Config (all):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `execution_lag = 1`, `funding_mode = "disabled"`,
`compute_counterfactual = False`, `annualization_factor = 365`. `fee_bps` and `slippage_bps`
stated per test — they have no default.

| id | requirement |
|----|-------------|
| A | `fee_bps = 0`, `slippage_bps = 0`, all targets 0 -> zero PnL, zero fees, zero turnover |
| B | `fee_bps = 0`, `slippage_bps = 0`. `w = +1.0`, mask True at bar 0, `P = [100, 110, 121]` -> execution at `i = 1`; `equity_curve = [1e6, 1e6, 1.1e6]`, `total_return = 0.1` |
| C | `fee_bps = 0`, `slippage_bps = 0`. `w = -1.0`, mask True at bar 0, `P = [100, 110, 121]` -> `equity_curve = [1e6, 1e6, 0.9e6]`, `total_return = -0.1` |
| D | `fee_bps = 0`, `slippage_bps = 0`. Two symbols, mask True at bar 0, `w = [+0.5, -0.5]`, `P_A = [100, 110, 110]`, `P_B = [50, 50, 55]` -> period 1 earns `0.5*0 + (-0.5)*0.1 = -0.05` -> `total_return = -0.05` |
| G | `fee_bps = 0`, `slippage_bps = 0`. `0 -> +1` produces turnover 1 |
| H | `fee_bps = 0`, `slippage_bps = 0`. `+1 -> -1` produces turnover 2 |
| I | `fee_bps = 10`, `slippage_bps = 0`. Turnover 2 at `NAV_pre = 1e6` -> `fee_cost = 2000.0` |
| J | `fee_bps = 0`, `slippage_bps = 10`. Turnover 2 at `NAV_pre = 1e6` -> `slippage_cost = 2000.0` |
| L | `fee_bps = 0`, `slippage_bps = 0`. `w = [+0.6, -0.4]` -> `gross_exposure == 1.0` at the execution point |
| M | `fee_bps = 0`, `slippage_bps = 0`. Same fixture -> `net_exposure == 0.2` |
| N | **`fee_bps = 0`, `slippage_bps = 0`** (W-C). `w = [+1.5, -1.5]`, mask True at bar 0, `P_A = P_B = [100, 100, 100]` -> `gross_leverage == 3.0` at `i = 1` |
| P | **`fee_bps = 10`, `slippage_bps = 10`** (W-C), on the §18.3 R1 fixture: two runs compare exactly equal |
| P2 | **`fee_bps = 10`, `slippage_bps = 10`** (W-C), same fixture: determinism holds across separate processes and differing `PYTHONHASHSEED` |

### 18.3 Rebalance and quantity ledger

**Config (all):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `funding_mode = "disabled"`, `fee_bps = 10`,
`slippage_bps = 10`, `compute_counterfactual = False`, `annualization_factor = 365`.
`execution_lag = 1` except R6.

| id | requirement |
|----|-------------|
| R1 | a rebalance followed by six non-rebalance bars produces **exactly zero** interim turnover and fees, under **trending** prices |
| R2 | identical targets repeated on non-rebalance bars produce no trade; the same targets under `rebalance_every_bar` DO produce drift-correcting trades. Both in one test |
| R3 | weights drift between rebalances while quantity is constant |
| R4 | `quantity[i] == quantity[i-1]` EXACT on every non-rebalance period across a long trending span, including INACTIVE symbols whose stored quantity is literal `0.0` |
| R5 | quantity changes **only** where `rebalance_flag` is true |
| R6 | `execution_lag = 2`, `n = 8` bars. Mask True at bar 2 only -> `rebalance_flag` True at index **4 only** |

### 18.4 NAV ledger consistency

**Config (all):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `execution_lag = 1`, `compute_counterfactual = False`,
`annualization_factor = 365`. Cost and funding settings per test.

| id | requirement |
|----|-------------|
| N1t | `fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "required"`, `basis = "period_start"`. Hand-computed case where **funding alone** changes NAV; next period's `w_pre` matches. Must fail if funding is omitted from `NAV_end` |
| N2t | `fee_bps = 10`, `slippage_bps = 10`, `funding_mode = "disabled"`. Same for fees and slippage |
| **N3t** | **single normative NAV path (W-A).** `fee_bps = 5`, `slippage_bps = 3`, `funding_mode = "required"`, `basis = "period_start"`, multi-asset. Assert **EXACT**: `equity_curve[i]` is the same stored value as `NAV_pre[i]` for all `i`, and `net_return[i] == equity_curve[i+1]/equity_curve[i] - 1` bitwise. Must fail if the engine maintains a second NAV path |
| N5t | equity **reconstruction check** at §17 tolerance on the N3t fixture: `equity_curve[k+1] ≈ equity_curve[k] * (1 + net_return[k])`. MUST NOT be asserted bitwise |
| N6t | `fee_bps = 50`, `slippage_bps = 50`, `funding_mode = "disabled"`, `execution_mode = "next_open"`, `execution_lag = 1`, `initial_capital = 1_000_000`. Assert `NAV_after_cost == NAV_pre * (1 - turnover * bps_total)` at §17 tolerance, `bps_total = 0.01`. Pinned: `NAV_pre = 1_000_000`, `turnover = 4` -> `NAV_after_cost == 960_000.0` **[computed]**. The rejected self-consistent solve `1e6/1.04 = 961_538.46153846150264` **[computed]** differs by `1538.46` and **fails** |
| N7t | decomposition identity (D) holds on all non-ruin periods of the N3t fixture, at §17 tolerance |

### 18.5 Funding

**Config (all):** `initial_capital = 1_000_000`, `execution_mode = "next_open"`,
`execution_lag = 1`, `fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "required"`,
`funding_notional_basis = "period_start"`, `compute_counterfactual = False`,
`annualization_factor = 365`. **`frequency` and any override stated per test.**

| id | requirement |
|----|-------------|
| F1 | `frequency = "1d"`. 24 hourly events inside one 1d bar aggregate to the sum of 24 charges |
| F2 | `frequency = "1d"`. An event exactly on a boundary is counted once, in the later period |
| F4 | `frequency = "1d"`. Genuinely absent funding data for a funding-accruing period raises `FundingDataError` |
| F5 | **`frequency = "1d"`, `funding_mode = "disabled"`** (W-C) -> funding exactly 0, `funding_modelled = False`, `funding_notional_basis = "not_modelled"` |
| F6 | `frequency = "1d"`. Long + positive rate -> negative funding PnL; short + positive rate -> positive |
| F7 | `frequency = "1d"`. Irregular event spacing within tolerance aggregates correctly |
| F8 | `frequency = "1d"`, hourly events, one 10x rate day. `next_open` vs `next_close` produce different windows. Must fail if `T_i` ignores `execution_mode` |
| F9 | `frequency = "1h"`, complete 8h stream, `max_funding_gap = 8h` -> does **not** raise |
| F10 | `frequency = "1d"`. Events before `T_0` or at/after `T_{n-1}` excluded and counted |
| F12 | `frequency = "1d"`. Funding on a rebalance period is valued on the **post-trade** quantity |
| F13 | `frequency = "1h"`. A gap exceeding `max_funding_gap` inside a coverage window raises |
| F14 | **`frequency = "1d"`** (W-C). A symbol with no funding-accruing period needs no funding data and does not raise |
| F15 | `frequency = "1h"`. Events only outside `[coverage_start, coverage_end]` — the `{2025-01-01, 2027-01-01}` counterexample — MUST raise |
| F16 | `frequency = "1h"`. Non-contiguous exposure with two disjoint, non-touching coverage records does **not** raise |
| F17 | `frequency = "1h"`. A gap exactly equal to `max_funding_gap` is accepted |
| F18 | **`frequency = "1d"`, `funding_notional_basis = "event_price"`** (W-C). Any missing/invalid `notional_price` on an applied event raises |
| F19 | **`frequency = "1d"`, `funding_notional_basis = "period_start"`** (W-C). Present-but-invalid `notional_price` values do not raise and do not affect the result |
| F20 | **`frequency = "1h"`, `max_funding_gap = 8h`, events every 1h** (W-C). Sets `funding_gap_tolerance_suspicious` without raising |
| F21 | **`frequency = "1d"`** (W-C). Touching or overlapping `FundingCoverage` records for one symbol raise `DataIntegrityError` |
| F22 | **cost-stage ruin before any funding-accruing interval.** Config override: `fee_bps = 1500`, `slippage_bps = 1500`, `frequency = "1d"`, `funding_mode = "required"`, `basis = "period_start"`. Uses the §18.8 X9 price path, mask and targets. Funding events: one event at `T_1`, `funding_rate = 0.0`. **(a)** with `FundingCoverage = [T_1, T_2]`, `max_funding_gap = 1d`: no `FundingDataError` — the cost-stage ruin at period 2 terminates at Step 4, so `[T_2, T_3)` never reaches Step 8 and is not funding-accruing, even though §6.7.1 reports `quantity = 7000.0` at the terminal row. **(b)** the run completes with `ruined == True`, `ruin_stage == "cost"`. **(c)** with `FundingCoverage = [T_0, T_1]` instead, so the genuinely funding-accruing period `[T_1, T_2)` is uncovered, `FundingDataError` **IS** raised. (a) and (c) together prove the rule discriminates rather than suppressing |

### 18.6 Metrics

**M7 is deleted and MUST NOT be reintroduced in any form.** No test may assert a derived
identity relating Sharpe and Sortino.

**This is a PURE-FUNCTION unit test of `metrics.py`**, not an engine run. `net_return` and
`equity_curve` are supplied directly, so `execution_mode`, `execution_lag`, `fee_bps`,
`slippage_bps`, `funding_mode` and `frequency` are **irrelevant and not required**.

**Config:** `annualization_factor = 365`, `risk_free_per_period = 0`, `mar_per_period = 0`.

    net_return   = [0.010, -0.005, 0.020, -0.015, 0.000, 0.008, -0.012, 0.006]
    n_periods    = 8   (3 negative periods)

    equity_curve = [1_000_000.0,
                    1_010_000.0,
                    1_004_950.0,
                    1_025_049.0,
                    1_009_673.265,
                    1_009_673.265,
                    1_017_750.65112,
                    1_005_537.64330656,
                    1_011_570.8691663994]           # [computed], test-local construction

The `equity_curve` above is **supplied to the pure function**, constructed test-locally by
`equity[k+1] = equity[k] * (1 + net_return[k])`. This is a test-local construction for a pure
function, **not** a second normative NAV path: in the engine, `equity_curve` is the §8 ledger.
It is stated in full so the fixture needs no reconstruction. Note `cumprod` would give
`1_011_570.8691663996` — 2 ulp different — which is why §8 forbids mixing the two.

Values **[computed]**:

    mean(net_return)        = 0.0015
    std(net_return, ddof=1) = 0.011807987611298186
    annualized_volatility   = 0.22559128655918556
    M1  sharpe              = 2.4269554394174677

    mean(min(r,0)**2)       = 4.9250000000000004e-05
    downside_dev_ann        = 0.13407553841025588
    M2  sortino             = 4.0835189363529718

    M5  total_return        = 0.011570869166399378
        cagr                = 0.6902729275701369
        max_drawdown        = -0.019034560000000034

| id | requirement |
|----|-------------|
| M1 | `sharpe` equals the literal above |
| M2 | `sortino` equals the literal above |
| M3 | every §12.3 degenerate case returns `nan`, not 0, not an exception |
| M4 | `cagr = 0.6902729275701369` while arithmetic annualization `mean*af = 0.5475` **[computed]** — differing ~26%, guarding a silent swap |
| M5 | `total_return`, `cagr`, `max_drawdown` equal the literals above |
| M6 | `max_drawdown` captures a first-period loss below `initial_capital` |

### 18.7 Counterfactual

**Config (all):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `execution_lag = 1`, `annualization_factor = 365`,
`compute_counterfactual = True`. Costs and funding per fixture.

#### CF2 fixtures — both paths COMPLETE over the SAME horizon

**CF2a — costs dominate, drag POSITIVE.** `fee_bps = 10`, `slippage_bps = 10`,
`funding_mode = "disabled"`, 1 symbol, target `1.0`, mask True at bar 0 only:

    P = [[100], [100], [110]]      # 3 bars, 2 periods; execution at i = 1

Expected **[computed]**:

    actual         equity = [1_000_000, 1_000_000, 1_097_800.0]   ruined = False, 2 periods
    counterfactual equity = [1_000_000, 1_000_000, 1_100_000.0]   ruined = False, 2 periods
    counterfactual_status = "COMPLETED" ,  drag_comparable = True

    period 1: turnover = 1.0 , fee_cost = 1000.0 , slippage_cost = 1000.0
              NAV_after_cost = 998_000.0 , quantity = 9980.0 , asset_pnl_cash = 99_800.0

    total_return = 0.0978 , counterfactual_total_return = 0.10
    total_drag_return = 0.0022        (POSITIVE)

**CF2b — funding income, drag NEGATIVE.** `fee_bps = 0`, `slippage_bps = 0`,
`funding_mode = "required"`, `funding_notional_basis = "period_start"`, 1 symbol, target `1.0`,
mask True at bar 0 only:

    P = [[100], [100], [100]]      # flat, 3 bars, 2 periods; execution at i = 1

**Funding events, stated explicitly (W-C):** exactly **one** event, at timestamp `T_1`, with
`funding_rate = -0.01` (long receives). No event falls in `[T_0, T_1)`, which is correct
because `quantity[0] = 0` makes period 0 non-funding-accruing.
**`FundingCoverage`:** one record, `coverage_start = T_1`, `coverage_end = T_2`,
`max_funding_gap = 1d`. Augmented sequence `[T_1, event@T_1, T_2]` -> gaps `0` and `1d`, both
`<= max_funding_gap`, so §7.7.2 condition 3 passes.

Expected **[computed]**:

    actual         equity = [1_000_000, 1_000_000, 1_010_000.0]   ruined = False, 2 periods
    counterfactual equity = [1_000_000, 1_000_000, 1_000_000.0]   ruined = False, 2 periods
    counterfactual_status = "COMPLETED" ,  drag_comparable = True

    period 1: quantity = 10_000.0 , notional_e = 10_000 * 100 = 1_000_000
              funding_pnl_cash = -(1_000_000 * -0.01) = +10_000.0

    total_return = 0.01 , counterfactual_total_return = 0.0
    total_drag_return = -0.01         (NEGATIVE)

Prices are flat, so the drag is attributable to funding alone.

**`cagr_drag` is NOT pinned on either fixture.** With `n_periods = 2` and `af = 365` the CAGR
exponent is `182.5` (§12.5). CF2 asserts `total_drag_return`, which is horizon-independent.

**Pinned CF3 fixture.** `fee_bps = 50`, `slippage_bps = 50`, `funding_mode = "disabled"`,
2 symbols:

    P     = [[100, 50], [110, 45], [121, 40], [133.1, 36]]     # 4 bars, 3 periods
    mask  = [True, True, False, False]
    W[0]  = [0.6, -0.4] ;  W[1] = [0.6, -0.4]

Expected **[computed]** (execution at `i = 1` and `i = 2`; period 0 flat under lag 1):

    actual equity          = [1_000_000, 1_000_000, 1_093_400.0, 1_201_772.0]
    counterfactual equity  = [1_000_000, 1_000_000, 1_104_444.4444444445, 1_214_888.888888889]
    gross_return           = [0.0, 0.1034, 0.09991951710261567]
    cumprod(1+gross)*1e6   = 1_213_651.1951710260   !=  counterfactual 1_214_888.8888888890

    total_return                = 0.20177200000000006
    counterfactual_total_return = 0.21488888888888891
    total_drag_return           = 0.013116888888888845

All equity values above are **ledger** values (§8), not `cumprod` values.

**Pinned CF7 fixture** — counterfactual ruins, actual survives. `fee_bps = 0`,
`slippage_bps = 0`, `funding_mode = "required"`, `funding_notional_basis = "period_start"`,
1 symbol, target `2.5`, mask True at bar 0 only:

    P = [[100], [100], [60], [60], [60]]        # 5 bars, 4 periods

**Funding events, stated explicitly (W-C):** three events, at `T_1`, `T_2` and `T_3`, each with
`funding_rate = -0.09`. No event in `[T_0, T_1)` (period 0 is not funding-accruing).
**`FundingCoverage`:** one record, `[T_1, T_4]`, `max_funding_gap = 1d`.

Expected **[computed]**:

    actual         equity = [1_000_000, 1_000_000, 225_000.0, 360_000.0, 495_000.0]  ruined = False
    counterfactual equity = [1_000_000, 1_000_000, 0.0]                              ruined = True

| id | requirement |
|----|-------------|
| CF1 | `fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "disabled"` -> counterfactual equity equals actual equity, EXACT |
| CF2 | On **CF2a**, `total_drag_return == 0.0022` and `> 0`. On **CF2b**, `total_drag_return == -0.01` and `< 0`. Both in one test, both with `drag_comparable == True` and `counterfactual_status == "COMPLETED"` |
| **CF2c** | **optional strengthening, accepted.** On the **CF3** fixture (3 periods, both paths complete), `total_drag_return == 0.013116888888888845` **[computed]**. The naive per-period cost-return summation gives `0.010804828973843059` **[computed]**, a **17.63%** miss — so this discriminates the two realistic wrong implementations across multiple rebalances, which the 2-period CF2a/CF2b cannot |
| CF3 | On the CF3 fixture, `cumprod(1+gross_return)` does **not** equal counterfactual equity |
| CF4 | Hand-computed 3-period 2-asset counterfactual (the CF3 fixture), all values in the docstring |
| CF5 | Counterfactual respects the same execution timing and rebalance mask |
| CF6 | **actual ruins, counterfactual survives**: counterfactual retains its full length; actual truncates; `drag_comparable == False`; drag fields `None` |
| CF7 | **counterfactual ruins, actual survives**: actual retains all 5 equity observations, `ruined == False`, `counterfactual_ruined == True`, `counterfactual_status == "RUINED"`, and — because §9.2 precondition 1 fails — **`total_drag_return is None`, `cagr_drag is None`, `drag_comparable == False`** |
| CF8 | **isolation invariant**: actual result **bit-identical** with `compute_counterfactual` `True` and `False`, on the CF7 fixture |
| CF9 | **exception isolation**: actual ruins at period 3; counterfactual survives to period 10; a held symbol's price is absent at period 7. The backtest MUST return successfully, actual **bit-identical** to the `compute_counterfactual=False` run, `counterfactual_status == "FAILED"`, `counterfactual_error` populated, `drag_comparable == False` |
| CF10 | when the paths differ in length, `drag_comparable == False` and drag fields are `None` |
| CF11 | an actual-path data integrity error (invalid price the actual path DOES read) still propagates and is **not** converted to `counterfactual_status = "FAILED"` |

### 18.8 Ruin

**Config (both):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `execution_lag = 1`, `funding_mode = "disabled"`,
`annualization_factor = 365`, `compute_counterfactual = False`, 1 symbol.

**Pinned X1 fixture — pnl-stage ruin.** `fee_bps = 10`, `slippage_bps = 10`, target `3.0`,
mask True at bar 0 only:

    P = [[100], [100], [60]]        # 3 bars, 2 periods; execution at i = 1

Expected **[computed]**:

    equity_curve = [1_000_000, 1_000_000, 0.0]
    net_return   = [0.0, -1.0]
    ruined = True ,  ruin_stage = "pnl" ,  ruin_timestamp = T_2
    uncapped_ruin_return        = -1.1988          (exactly -1.1988000000000000878)
    ruin_decomposition_residual = -0.1988          (exactly -0.19880000000000008775)
    total_return = -1.0 , max_drawdown = -1.0 , cagr = -1.0 , calmar = -1.0

    terminal row (period 1), all class A:
        turnover = 3.0 , fee_cost = 3000.0 , slippage_cost = 3000.0
        quantity = 29820.0 , positions (w_held) = 3.0
        asset_pnl_cash = -1_192_800.0 , funding_pnl_cash = 0.0
        NO NaN and NO inf anywhere

**Pinned X9 fixture — cost-stage ruin with a pre-existing position.** `fee_bps = 1500`,
`slippage_bps = 1500`, mask True at bars 0 and 1, `W[0] = 1.0`, `W[1] = -4.0`:

    P = [[100], [100], [100], [60]]      # 4 bars, 3 periods; executions at i = 1 and i = 2

Expected **[computed]**:

    equity_curve = [1_000_000, 1_000_000, 700_000.0, 0.0]
    net_return   = [0.0, -0.3, -1.0]
    ruined = True ,  ruin_stage = "cost" ,  ruin_timestamp = T_3
    uncapped_ruin_return        = -1.5
    ruin_decomposition_residual = -0.5
    fee_return + slippage_return = -0.75 + -0.75 = -1.5 == uncapped_ruin_return   (§6.7.3)

    terminal row (period 2):
        turnover = 5.0 , fee_cost = 525_000.0 , slippage_cost = 525_000.0    (class A)
        quantity = 7000.0        -- the PRE-TRADE position, per §6.7.1        (class A)
        positions (w_held) = 1.0 -- equals w_pre                              (class A)
        asset_pnl_cash = NaN , funding_pnl_cash = NaN                         (class C)
        gross_return   = NaN , funding_return   = NaN                         (class C)

    total_return = -1.0 , max_drawdown = -1.0 , cagr = -1.0 , calmar = -1.0

**Pinned X11 fixture — non-finite NAV is NOT ruin (W-B).** `fee_bps = 0`, `slippage_bps = 0`,
1 symbol, target `-1.0`, mask True at bar 0 only:

    P = [[5e-324], [5e-324], [1e-300]]     # denormal but finite and > 0; passes §5.5

Expected **[computed]**:

    quantity       = -1.0 * 1_000_000 / 5e-324  = -inf
    asset_pnl_cash = -inf
    NAV_end        = -inf        -> isfinite == False

    §6.0 Step 10 raises AccountingError.
    ruined is NOT set. No result object is produced. No -inf reaches any output.

Under v1.5's ordering (`NAV_end <= 0` tested first) this was reported as `ruined = True` with
`-inf` on the surface, violating X3.

| id | requirement |
|----|-------------|
| X1 | the X1 fixture reproduces every value above |
| X2 | series truncated, not padded: `len(net_return) == ruin_period + 1`, `len(equity_curve) == ruin_period + 2` |
| X3 | **no `inf` anywhere**; `NaN` **only** in the four fields §6.7.2 permits, **only** at a cost-stage terminal row. Asserted on both X1 (no `NaN` at all) and X9 |
| X4 | `total_return`, `max_drawdown`, `cagr`, `calmar` all exactly `-1.0`, EXACT, on both fixtures |
| X5 | ruin at period 0 -> `n_periods == 1`, dispersion metrics `nan`, no exception |
| X6 | asserts `ruin_decomposition_residual` at §17 tolerance: `-0.1988` on X1, `-0.5` on X9; and on X9 `fee_return + slippage_return == uncapped_ruin_return`. MUST NOT assert (D) at a ruin period |
| X7 | `ruined=True` appears in `__repr__` |
| X8a | near-ruin (`NAV_end` small but positive) produces no `inf` in any output |
| X8b | `leverage_breach` fires when `max_gross_leverage` is set and breached |
| X9 | the X9 fixture reproduces every value above, including the class-A pre-trade `quantity` and the four class-C `NaN` sentinels |
| X10 | `ruin_stage == "pnl"` on X1 and `"cost"` on X9; `terminal_position_convention == "pre_ruin_state"` on both |
| **X11** | **the X11 fixture raises `AccountingError`; `ruined` is NOT set; no `-inf` appears in any output.** Must fail if the finiteness guard is placed after the ruin test. Additionally assert that the same fixture with `target = +1.0` (giving `NAV_end = +inf`) also raises, and that X1 and X9 are **bit-for-bit unchanged** by the guard ordering |

### 18.9 Symbol activity, boundaries, missing data

**Config (all):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `execution_lag = 1`, `fee_bps = 0`, `slippage_bps = 0`,
`funding_mode = "disabled"`, `compute_counterfactual = False`, `annualization_factor = 365`.

| id | requirement |
|----|-------------|
| S1 | **staggered listing**: symbol B has no price for the first half and zero weight there; the backtest completes, `np.isfinite(equity_curve).all()` is `True`, `asset_pnl_cash` equals the hand-computed single-symbol value at §17 tolerance. Must fail if inactive symbols are neutralised by `0 * NaN` |
| S2 | **delisting**: a held symbol losing `P[i+1]` raises `MissingPriceError` |
| S3 | closing a position (`q_prev != 0`, target `0`) at an invalid execution price raises |
| S4 | **EXITING** symbol does **not** require `P[i+1]`: valid `P[i]`, absent `P[i+1]`, target 0 -> completes without raising |
| S5 | `symbol_state` matches §5.3 for every (period, symbol) on a fixture exercising all four states |
| S6 | INACTIVE symbols with `NaN`, `0.0` and negative prices all proceed silently and contribute exactly zero |
| S7 | `exit_unnamed=True` materialises explicit `0.0` targets for previously-held unnamed symbols; without it the same input raises `DataIntegrityError` per §5.4 |
| V1 | zero price on a symbol in use raises `InvalidPriceError` |
| V2 | negative price on a symbol in use raises `InvalidPriceError` |
| V3 | denormal positive price passes §5.5 but yields non-finite NAV -> `AccountingError`, **not** `ruined = True`. Uses the §18.8 X11 fixture |
| T1 | rebalance flagged at `t > n-2-execution_lag` -> no trade, no crash, recorded in `unexecuted_rebalances` |
| T2 | terminal bar: `len(net_return) == n-1`, `len(equity_curve) == n`, no `NaN` in equity |
| T3 | two-bar backtest returns one period; one-bar raises `ConfigError` |
| T4 | `execution_instant()` unit-tested against the §4.3 table for both modes |
| T5 | a **regular** grid whose spacing disagrees with `config.frequency` raises `DataIntegrityError` |
| U1 | symbol with nonzero quantity absent from **`target_weights[t]`**, `t = i - execution_lag`, raises |
| U2 | symbol entering mid-sample proceeds silently |
| U3 | symbol absent from target columns with zero quantity is treated as target 0, no trade |
| D4t | irregular bar grid raises `DataIntegrityError` naming the offending pair and expected Δ |

### 18.10 Provenance

**Config:** `initial_capital = 1_000_000`, `frequency = "1d"`, `execution_mode = "next_open"`,
`execution_lag = 1`, `fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "disabled"`,
**`annualization_factor = 365`** (W-C), `compute_counterfactual = False`. A minimal 3-bar
single-symbol run; no numeric result is asserted.

| id | requirement |
|----|-------------|
| PR1 | supplied provenance appears unmodified, field for field, including `field_type` and `time_range` |
| PR2 | absent provenance -> `provenance_supplied == False` |
| PR3 | any `native_or_proxy == "proxy"` -> `uses_proxy_data == True`, surfaced in `__repr__` |
| PR4 | `native_or_proxy == "proxy"` with `proxy_for` `None`/empty raises `DataIntegrityError` |
| PR5 | an all-`None` `DatasetProvenance` gives `provenance_complete == False` |
| PR6 | `UniverseProvenance` passes through unmodified; `survivorship_safe` is `None` when unsupplied and never defaults to `True`; `None`/`False` surfaced in `__repr__` |

### 18.11 Coverage

Both `execution_mode` values across the engine suite. Every exception path in §11.2, including
both `AccountingError` sites (§6.0 Steps 3 and 10). Every config validation in §15. Every state
in §5.3. Both ruin stages. Both finiteness guards.

---

## 19. Out of scope for QR-INFRA-001

No market data ingestion. No alpha. No strategy implementations beyond synthetic test fixtures.
No margin or liquidation modelling. No provenance population and no universe construction
(QR-DATA-001). No symbol-identity resolution. No live trading, keys, orders, withdrawals or
transfers.

---

## 20. Resolved design decisions

**20.1 — Costs on `NAV_pre`, sizing on `NAV_after_cost`. ACCEPTED.** The fee differs from the
self-consistent solve by `O(turnover * bps)` relative — 2.00% at `fee_bps = 50` alone with
turnover 4, and 4.00% at N6t's 50 + 50 bps config.

**20.2 — Funding `period_start`; start/end averaging REJECTED** (lookahead). A run-level mode.

**20.3 — Portfolio-level rebalance mask only in v1. ACCEPTED**, `exit_unnamed=True` for
ergonomics.

**20.4 — `gross_leverage` as an alias of `gross_exposure`. ACCEPTED.**

**20.5 — Ruin floors at 0 and terminates. ACCEPTED**, both stages specified.

**20.6 — §5.4 raises rather than warning-and-flattening. ACCEPTED.**

**20.7 — §2.1 regular grid required, no config tolerance. ACCEPTED.**

**20.8 — `max_funding_gap` declared, never inferred. ACCEPTED.**

**20.9 — Counterfactual exempt from funding-coverage validation. ACCEPTED**, conditioned on
§9.5's barrier.

**20.10 — Coverage records disjoint and non-touching, merged upstream. ACCEPTED.**

**20.11 — §17 tolerances retained. ACCEPTED.**

**20.12 — Terminal ruin row reports the pre-ruin position state, not a liquidation. ACCEPTED.**

**20.13 — §9.2's drag comparability rule retained unchanged; the violating test was fixed
instead. ACCEPTED.**

**20.14 — The ledger `NAV_end` is the single normative NAV; `equity_curve` is that ledger;
`net_return` is derived; reconstruction is a tolerance check only. ACCEPTED** (W-A). Removes
the three-way ambiguity in which the Step-9 carry, §8's recursion and §10's alias could not all
be bitwise true.

**20.15 — Finiteness is checked before ruin classification at both stages. ACCEPTED** (W-B).
An arithmetic blow-up is a numerical failure, never a strategy outcome.

**20.16 — `cagr`/`calmar` are NOT suppressed below an `n_periods` threshold. ACCEPTED.** Any
threshold would be arbitrary, and §12.4 deliberately relies on `cagr = -1.0` at
`n_periods = 1`. Documented as an interpretation hazard in §12.5 instead.

---

## 21. Open editorial findings at freeze (B1–B8)

Recorded at freeze so they are not lost. Each was independently ruled **incapable of affecting
PnL, execution timing, accounting, data validity, reproducibility or metric interpretation of
any valid run**. They are carried as known debt, not as defects requiring a revision.

| id | finding | impact |
|----|---------|--------|
| **B1** | §18.8 X5 ("ruin at period 0") is unreachable in a production config: with `execution_lag >= 1`, period 0 has no rebalance, so `NAV_end[0] = initial_capital > 0`. The test must use the §18.0.2 `_step_period(...)` helper and carry the `# LOWER-LEVEL HELPER` label, as E and E3 do | Test cannot be written as literally stated. Assertion itself is correct and passable via the documented helper; behaviour is independently covered by M3/§12.3 |
| **B2** | §15 and §11.2 impose no `initial_capital > 0` constraint. `initial_capital = 0` fires a period-0 cost-stage ruin and makes `uncapped_ruin_return = 0.0/0.0` | Cannot produce a plausible-but-wrong PnL — it fails loudly or produces obvious nonsense. One-line fix: `initial_capital <= 0 -> ConfigError`. Also removes the illegal route to constructing X5 |
| **B3** | §18.1's config header is scoped to E, E2, E3, leaving E4 without a config block or fixture | E4's assertion holds under any config; literal violation of §18.0.2 |
| **B4** | §18.8's header reads "Config (both)" but the section now holds three fixtures (X1, X9, X11) | X11 inherits from a header that textually excludes it. Change to "all three" |
| **B5** | `cagr` can overflow on a short high-frequency sample (`af/n_periods = 4380` for a 2-period 1h run), raising `OverflowError`, while §12.3 promises degenerate cases are "never an exception" | Spec-internal completeness gap on a degenerate 2-period run. Suggest: non-finite `cagr` -> `nan`, never an uncaught exception |
| **B6** | `funding_events_excluded` is undefined under early termination — events in `[T_{i+1}, T_{n-1})` after a ruin are neither charged nor counted | Diagnostic counter only; no PnL impact |
| **B7** | No explicit "capacity / market impact / spread dynamics are not modelled" disclaimer parallel to §14's liquidation disclaimer. Cost is strictly linear in turnover with no size dependence | Given CLAUDE.md's prohibition on treating external liquidity as Hyperliquid execution capacity, one sentence in §14 would close the loop |
| **B8** | The terminal non-ruin position is marked, never closed, so no exit cost is charged at `T_{n-1}` | Standard for weight-based backtests; bounded one-round-trip optimism (~2 bps of gross exposure at 20 bps total). Material only on short fixtures or high-cost configs |

**Independently verified at freeze and recorded so they are not re-litigated:** CF3's
`1_213_651.1951710260` is correct for `cumprod`; X6's refusal to assert the literal `-0.1988`
is correct (`==` fails at 4.19e-16); M's `net_exposure` `0.2` vs actual `0.19999999999999996`,
CF2a's `0.0022` vs `0.0021999999999999797`, and X9's `-0.3` vs `-0.30000000000000004` are all
correctly classified TOLERANCE; `0.0 ** (af/n) = 0.0` with no domain error. §17's measured
percentages (32.1% reconstruction, 17.8% `q -> w -> q`) are sample-dependent — an independent
draw gave 28.8% and 12.9%, same phenomenon and order of magnitude.

---

## 22. Audit trail

Five independent adversarial audits by `backtest-auditor`. No agent that drafted the
specification certified it. Every pinned numeric fixture was independently re-derived by the
auditor at each round; none was accepted on authority.

| round | version | verdict | blocking findings |
|-------|---------|---------|-------------------|
| 1 | v1.1 | SPEC FAIL | 12 (B1–B12) |
| 2 | v1.2 | SPEC FAIL | 7 (C1–C7) |
| 3 | v1.3 | SPEC FAIL | 4 (D1–D4) |
| 4 | v1.4 | SPEC FAIL | 1 (E1) |
| 5 | v1.5 | SPEC PASS WITH WARNINGS | 0 blocking, 5 warnings (W-A…W-E) |
| 6 | v1.5.1 | **SPEC PASS WITH WARNINGS** | **0 blocking / runtime-correctness, 8 editorial (B1–B8)** |

The §6 accounting core was verified numerically at every round: 9.9e-17, 3.56e-16, and full
bitwise fixture reproduction thereafter. Every defect found across all six rounds lay at a
boundary, in a validation rule, or in the test suite — never in the central accounting
sequence.

Final consistency checklist, mechanically verified at freeze:

| # | property | result |
|---|----------|--------|
| 1 | only one normative NAV/equity path exists | PASS |
| 2 | every mandatory fixture is fully constructible | PASS except B1, B3, B4 (editorial) |
| 3 | every numeric assertion has an EXACT/TOLERANCE classification | PASS — zero gaps |
| 4 | all finiteness checks precede economic ruin classification | PASS (Steps 3<4, 10<11) |
| 5 | actual-path results cannot be affected by counterfactual errors | PASS |
| 6 | funding coverage checks only periods the simulation reaches | PASS |

No lookahead vector was found anywhere in the specification.
