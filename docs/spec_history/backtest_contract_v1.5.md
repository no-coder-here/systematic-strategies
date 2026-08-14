# Backtest Contract — SPECIFICATION v1.5 (QR-INFRA-001)

Status: **UNDER REVIEW — NOT FROZEN.** Awaiting audit of E1, N3–N6, N10 and regression.
Owner: Research Lead. Normative file. History preserved under `docs/spec_history/`.
No implementation may begin until this document is marked FROZEN by the platform owner.

This document is normative. Where this document and intuition disagree, this document wins.

**v1.5 is a NARROW CORRECTIVE REVISION.** The accounting core, timing model, quantity ledger,
symbol classification, price validity rules, funding architecture, counterfactual barrier,
ruin semantics and metric definitions are unchanged from v1.4 and were verified correct at
audit. Only E1, N3, N4, N5, N6, N10 and the cheap residual warnings are addressed. No new
features and no architectural change.

---

## 0. Revision history

Snapshots are preserved before replacement and MUST NOT be overwritten:

    docs/spec_history/backtest_contract_v1.3.md
    docs/spec_history/backtest_contract_v1.4.md

| version | verdict | outcome |
|---------|---------|---------|
| v1.0 | rejected by owner | implicit rebalancing; inconsistent drift; one-funding-event-per-bar |
| v1.1 | SPEC FAIL | 12 blocking (B1–B12). Core verified 9.9e-17 |
| v1.2 | SPEC FAIL | 7 blocking (C1–C7). Core verified 3.56e-16 |
| v1.3 | SPEC FAIL | 4 blocking (D1–D4). All 8 pinned fixtures verified exact |
| v1.4 | SPEC FAIL | 1 blocking (E1). All 8 re-pinned fixtures verified, most bitwise exact. D1–D4 resolved; regression clean |
| v1.5 | this document | E1 + N3/N4/N5/N6/N10 + residual warnings |

### v1.4 -> v1.5 changes

| ref | defect | resolution | § |
|-----|--------|------------|---|
| **E1** | CF2 asserted `total_drag_return < 0` on the CF7 fixture, where §9.2 mandates it be `None`. `None < 0` raises `TypeError` — the test could not pass | §9.2 rule **retained unchanged**. CF2 re-bound to two new fixtures where **both paths complete over the same horizon** | §18.7 |
| N3 | §18.0.2's own completeness rule violated by §18.2–18.5, §18.9, §18.10. Test C not reproducible at all (no cost defaults exist) | Explicit config header on **every** fixture section; C, R6, N6 fixed individually | §18 |
| N4 | §6.7.3 claimed components "sum to `uncapped_ruin_return`" — false at a cost-stage ruin, where two of them are `NaN` | Restated per stage | §6.7.3 |
| N5 | Step 0 justified deferral by "skips Steps 3–6"; Step 2b sits between Step 2 and Step 3 and was unnamed | Explicit normative pseudocode; Step 2b named in the skip set | §6.0 |
| N6 | §7.7 could demand funding coverage for a cost-stage ruin period that charged no funding, because §6.7.1 reports a nonzero pre-trade quantity | Coverage scoped to periods that **reach Step 5**; funding-accruing interval defined | §7.7 |
| N10 | Pinned metric-fixture equity was the cumprod value; §8's normative recursion gives a different double | Re-pinned from the **sequential recursion**, labelled TOLERANCE | §18.6 |
| N1 | §18.0.1 claimed to classify "every numeric assertion"; 20 test IDs had no row | All rows added | §18.0.1 |
| N2 | §18.0.1's S4 row described INACTIVE; §18.9's S4 is about EXITING | Corrected | §18.0.1 |
| N7 | §6.7.2 claimed "every field is classified"; `fee_basis_notional` and `rebalance_flag` absent | Rows added | §6.7.2 |
| N8 | §9.2 referenced `counterfactual_total_return`/`counterfactual_cagr`, not on the §10 surface | Exposed explicitly | §10 |
| N9 | §6 Step 7 claimed to be reachable only via fault injection; a denormal price reaches it | Claim corrected | §6.0 |
| N11 | §20.1's "2.00%" read as describing N6's config | Both figures stated | §20.1 |
| §21.2 | §9.5.1's isolation argument rested on intuition | The `will_hold` invariant stated normatively | §9.5.1 |
| §21.3 | E/E2/E3 TOLERANCE justification was inaccurate | Corrected: exact today, tolerance avoids bit-fragility | §18.0.1 |
| §21.4 | §7.2 gave loader authors no merging guidance | Added | §7.2 |
| — | Short-horizon CAGR is an extrapolation trap (`af/n_periods = 182.5` on a 2-period fixture) | Interpretation note | §12.5 |

**Remaining warnings, documentation-only, accepted and NOT fixed:**

- **W7** — a loader declaring `max_funding_gap = 8h` for an hourly-funding venue with 7 of 8
  events missing passes §7.7 silently. Intrinsic to trusting declared metadata. Disclosed §7.2.
- **W8** — §7.7 condition 3 spans a whole coverage window, so an outage in an unexposed stretch
  of a single declared record raises. Mitigated by multi-record declaration.
- **W14 (partial)** — tests D, N1, N2, F1, F7, F12 state their construction and required
  config but defer the final arithmetic to the test docstring rather than pinning a literal
  in this document. They assert derived aggregates whose value follows mechanically from the
  stated inputs.
- **W16** — §4.4's note that `i = 0` is unreachable is stated for `execution_lag = 1`.
- **N12** — §10 surfaces `survivorship_safe` unconditionally while §13.2 requires it only when
  `None`/`False`. Unconditional is a superset; harmless.

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
rejected. Measured consequences of a finite-only guard: `P = 0` produced `quantity = inf`;
`P = -5` produced a **short** position from a long target while `gross_exposure` read `1.0`.

Prices for INACTIVE symbols are not "in use" and are deliberately unvalidated.

### 5.6 Notional

    notional[i, j] = 0.0 if quantity[i, j] == 0 else quantity[i, j] * P[i, j]

The zero branch is **assigned**, not computed.

---

## 6. Accounting sequence (NORMATIVE)

### 6.0 Control flow (resolves N5)

PnL and funding accrue on **every** holding period. Trades occur **only** at rebalance
execution points. The per-period sequence is stated as pseudocode because prose describing
skipped steps proved readable two ways:

    for i in 0 .. n-2:

        # ---- Step 0: resolve, classify, validate execution prices ----
        t            = i - execution_lag
        rebalance[i] = (0 <= t <= n-1) and rebalance_mask[t] == True
        w_target[i]  = target_weights[t] if rebalance[i] else undefined
        classify symbol_state[i, :] per 5.3
        validate P[i, j] per 5.5 for ENTERING, HELD, EXITING

        # ---- Step 1: trade ----
        if rebalance[i]:
            trade[i, j] = 0.0 if (q_prev == 0 and w_target[i, j] == 0)
                          else w_target[i, j] - w_pre[i, j]
            turnover[i] = sum_j abs(trade[i, j])
        else:
            trade[i] = 0 ; turnover[i] = 0

        # ---- Step 2: costs ----
        fee_cost[i]       = turnover[i] * NAV_pre[i] * fee_bps      / 10_000
        slippage_cost[i]  = turnover[i] * NAV_pre[i] * slippage_bps / 10_000
        NAV_after_cost[i] = NAV_pre[i] - fee_cost[i] - slippage_cost[i]

        if NAV_after_cost[i] <= 0:
            # COST-STAGE RUIN.
            # Steps 2b, 3, 4, 5, 6 and 7 are NOT EXECUTED for this period.
            # No P[i+1] is validated or read. No quantity is sized.
            # No funding event is consumed. Terminal row per 6.7.2.
            emit terminal row (ruin_stage = "cost")
            TERMINATE the simulation
            break

        # ---- Step 2b: validate next-period prices ----
        validate P[i+1, j] per 5.5 for ENTERING and HELD

        # ---- Step 3: quantity ledger ---- (5.1)
        # ---- Step 4: asset PnL ----
        active            = { j : quantity[i, j] != 0 }
        asset_pnl_cash[i] = sum_{j in active} quantity[i,j] * (P[i+1,j] - P[i,j])

        # ---- Step 5: funding PnL ---- (7.5), masked to active
        # ---- Step 6: ending NAV ----
        NAV_end[i] = NAV_after_cost[i] + asset_pnl_cash[i] + funding_pnl_cash[i]

        if NAV_end[i] <= 0:
            # PNL-STAGE RUIN. Terminal row per 6.7.2. TERMINATE.
            break

        # ---- Step 7: finiteness guard ----
        if not isfinite(NAV_end[i]): raise AccountingError
        NAV_pre[i+1] = NAV_end[i]

`NAV_pre[0] = initial_capital`.

**Step 2b exists as a separate named step** so that the cost-stage-ruin skip set is
enumerable without ambiguity: the skipped set is exactly `{2b, 3, 4, 5, 6, 7}`. A cost-stage
ruin MUST NOT raise `MissingPriceError` on a next-period price the engine never reads.

**W4 regression invariant, re-verified for v1.5:** the set of prices Step 2b validates is
exactly the set Step 4 reads. `active = { j : quantity[i,j] != 0 }` equals `ENTERING ∪ HELD`,
because at a rebalance point `quantity[i,j] == 0 ⟺ w_target[i,j] == 0 ⟺ ¬will_hold[i,j]`
(valid since `NAV_after_cost > 0` on every period that reaches Step 3), and on a
non-rebalance point `quantity[i] = q_prev` with `will_hold = (q_prev != 0)`. Step 3 sizes on
`P[i]`, validated at Step 0. §7.5 never reads `P[i+1]` under either basis. No position can be
established or valued on an unvalidated price.

**Step 7 reachability (N9).** Step 7 is not reachable only via fault injection. A denormal but
valid price (e.g. `5e-324`, which passes §5.5: finite and `> 0`) yields `quantity = inf` and
then a non-finite `NAV_end`, reaching Step 7 through ordinary input. §18.9 V3 exercises this
path. A fault-injection hook remains useful for covering the guard from other directions but
is not the only route.

### 6.1 Return decomposition

    gross_return[i]     =  asset_pnl_cash[i]   / NAV_pre[i]
    fee_return[i]       = -fee_cost[i]         / NAV_pre[i]      # <= 0
    slippage_return[i]  = -slippage_cost[i]    / NAV_pre[i]      # <= 0
    funding_return[i]   =  funding_pnl_cash[i] / NAV_pre[i]      # signed

    net_return[i] = gross_return[i] + fee_return[i] + slippage_return[i] + funding_return[i]

**(N-1) NAV identity** — holds on every non-ruin period, to §17 tolerance:

    net_return[i] == NAV_end[i] / NAV_pre[i] - 1

**(N-2) Additive decomposition** — holds on every **non-ruin** period, to §17 tolerance. It
does **NOT** hold at a ruin period (§6.7.3).

(N-1) has been verified independently: 9.9e-17 and 3.56e-16.

### 6.2 `gross_return` is an attribution component, not a path

`gross_return[i]` is the pre-cost return of the **actual** portfolio. Because Step 3 sizes on
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

**Ruin is an economic outcome, not an implementation exception.** Two mutually exclusive
stages, per §6.0:

    ruin_stage = "cost"   -- NAV_after_cost[i] <= 0 at Step 2
                 "pnl"    -- NAV_end[i]        <= 0 at Step 6

They cannot both fire: a cost-stage ruin terminates before Step 6 is reached.

#### 6.7.1 Position convention at the terminal row

> **Terminal quantities and exposures represent the LAST ECONOMICALLY VALID POSITION STATE
> IMMEDIATELY BEFORE RUIN. They are NOT a simulated liquidation.**

Chosen because §14 models no liquidation mechanics. Reporting zero positions would imply the
book was closed at a determinable price, which this engine cannot model.

`terminal_position_convention = "pre_ruin_state"` is carried on the result. Downstream reports
MUST NOT infer an exit price, exit timestamp or realised close from the terminal row.

- **pnl-stage ruin**: `quantity[i]` — established at Step 3, held through the period whose PnL
  caused the ruin
- **cost-stage ruin**: `quantity[i-1]` — the intended position was never sized, because Step 3
  never ran. The account died holding its prior book.

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
| `quantity[i, :]` | `quantity[i-1, :]` (pre-trade) | as sized at Step 3 | A |
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

At a pnl-stage ruin **no field is undefined** and no `NaN` appears anywhere.

#### 6.7.3 Termination and the decomposition at ruin (resolves N4)

1. the simulation terminates; no period after `i` is computed
2. `quantity[i+1]` is never formed, so division by `NAV_pre[i+1] = 0` never occurs
3. artificial zero-return periods MUST NOT be appended
4. all series truncate; `equity_curve` ends at `T_{i+1}` with `0.0`

**(N-1) holds at the ruin period** (`0/NAV_pre - 1 = -1`).

**(N-2) does NOT hold at the ruin period**, and the correct statement differs by stage:

- **pnl-stage**: all four components are defined and sum to `uncapped_ruin_return`, not to
  `net_return[i] = -1`. The difference is `ruin_decomposition_residual`.
- **cost-stage**: `gross_return[i]` and `funding_return[i]` are `NaN`, so the four-component
  sum is `NaN`. Only the **defined** components sum meaningfully:
  `fee_return[i] + slippage_return[i] == uncapped_ruin_return`, verified on §18.8 X9
  (`-0.75 + -0.75 == -1.5`).

v1.4 asserted "the components sum to `uncapped_ruin_return`" without qualification, which is
false at a cost-stage ruin. §18.8 X6 asserts (N-1) and the residual only, never (N-2).

`ruin_timestamp = T_{i+1}` at **both** stages, for index consistency with §8. At a cost-stage
ruin the account economically died at `T_i`, when costs were paid; `T_{i+1}` is the timestamp
of the terminal equity observation, not the instant of death. Deliberate, not an error.

`ruined = True` MUST appear in `__repr__`, any summary table, and any report the result feeds.

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

> **Guidance for loader authors (§21.4).** Merging is expected and is an ordered linear pass
> over declared metadata, not an inference. A loader stitching monthly fetches should merge
> `[Jan, Feb]` and `[Feb, Mar]` into one record; §7.7 condition 3 then re-scans the union
> window, so a genuine seam gap is still caught. A **real** data gap must be declared as two
> records separated by a genuine, non-touching interval — never as two touching records.

`max_funding_gap` lives here, not in `BacktestConfig`: it is a venue/dataset property.
`max_funding_gap <= 0` raises `DataIntegrityError`.

Coverage is **declared by the data layer, never inferred**. Inferring cadence is circular: the
stream being validated is the stream that would be inferred from.

> **Known residual risk (W7):** a loader declaring `max_funding_gap = 8h` for a venue that
> funds hourly, with 7 of every 8 events missing, passes §7.7 condition 3 with gaps of exactly
> 8h and undercharges funding 8x silently. Declared metadata is trusted by design.
> QR-DATA-001 owns declaring it correctly.

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

`quantity[i, j]` is the **post-trade** quantity. The outer sum is masked to the active set.

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

### 7.7 Funding mode and coverage validation (resolves N6)

    funding_mode = "required" | "disabled"       # REQUIRED config, no default

Under `"disabled"`: funding is exactly 0, `funding_modelled = False`,
`funding_notional_basis = "not_modelled"`. The engine MUST NOT infer `"disabled"` from a
missing column, an empty frame, or a symbol with no events.

#### 7.7.1 The funding-accruing exposure interval

> **Funding coverage is required exactly for the intervals in which the engine could actually
> charge funding — no more, no less.**

Period `i` is **funding-accruing for symbol `j`** if and only if **both**:

1. period `i` reached **Step 5** of §6.0 — i.e. it was not terminated by a cost-stage ruin at
   Step 2, and
2. `quantity[i, j] != 0` **as sized at Step 3**

Condition 1 is the N6 fix. A cost-stage ruin terminates before Step 5, so the holding interval
`[T_i, T_{i+1})` never economically occurs and consumes no funding event. Coverage MUST NOT be
required for it.

Critically, §6.7.1 *reports* `quantity[i] = quantity[i-1]` at a cost-stage terminal row — a
nonzero value by design (§18.8 X9 pins `7000.0`). That **reported** quantity MUST NOT be read
as exposure for coverage purposes. Condition 2 refers to the quantity **as sized at Step 3**,
which at a cost-stage ruin does not exist, so the period is not funding-accruing for any
symbol.

#### 7.7.2 Coverage conditions

Under `"required"`, for every symbol `j` and every **funding-accruing** period `i` per
§7.7.1, raise `FundingDataError` unless all hold:

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

## 8. Equity curve indexing

| row | timestamp | value |
|-----|-----------|-------|
| 0 | `T_0` | `initial_capital` |
| `k` (1 .. n-2) | `T_k` | `NAV_end[k-1]` = `NAV_pre[k]` |
| `n-1` | `T_{n-1}` | `NAV_end[n-2]` |

    len(equity_curve) == n
    len(net_returns)  == n - 1 == len(equity_curve) - 1
    n_periods         := len(net_return)

**The equity curve is built by sequential recursion**, which is normative:

    equity_curve[0]   = initial_capital
    equity_curve[k+1] = equity_curve[k] * (1 + net_return[k])

and this relation is asserted to §17 tolerance, **not bitwise**. Measured bitwise failure rate
32.1%, worst relative error 4.32e-16, because `net_return` is a sum of four separately-divided
components.

**Any expected equity value pinned in §18 MUST be derived from this same sequential recursion**
(N10). `cumprod(1 + net_return) * initial_capital` is mathematically equal but is a different
floating-point path and produces a different double — measured differing by 2 ulp on the §18.6
fixture.

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

### 9.2 Drag attribution — comparability rule (UNCHANGED, load-bearing)

    total_drag_return = counterfactual_total_return - total_return
    cagr_drag         = counterfactual_cagr         - cagr

> **Drag fields are populated ONLY when ALL of the following hold:**
>
> 1. `counterfactual_status == "COMPLETED"`
> 2. the actual path completed the comparable horizon (`ruined == False`)
> 3. both paths span the same number of periods
>
> **Otherwise `total_drag_return` and `cagr_drag` are `None` and `drag_comparable = False`.**

**A drag statistic must never compare different horizons.** If one path ruins early, differencing
its total return against the other's compares 2 periods against 4 and produces a number that
flatters or damns cost attribution by an arbitrary factor. This rule is retained verbatim from
v1.4; **v1.5 changes the tests that violated it, not the rule.**

Drag is **positive when costs dominate and legitimately negative when funding is net income**.
It is NOT decomposable into components by differencing counterfactual paths, because they
interact through the compounding NAV. The additive (N-2) decomposition is the correct tool for
component attribution. The result MUST NOT present a component-wise drag breakdown from §9.

`counterfactual_total_return` and `counterfactual_cagr` are exposed directly on the result
surface (§10), not only inside `counterfactual_gross_metrics` (N8).

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

#### 9.5.1 What the barrier MUST NOT suppress, and why the argument is sound

**Errors that invalidate INPUT DATA required by the ACTUAL path are NOT counterfactual failures
and MUST propagate.** The distinction is unambiguous because of requirement 1:

- **actual-path data integrity error** — raised while the actual path executes, before the
  barrier is entered. Propagates. The backtest fails, correctly.
- **counterfactual-only diagnostic failure** — raised inside the barrier. Recorded as `FAILED`.
  The actual result is returned intact.

**Normative invariant justifying this (§21.2).** At every period, `will_hold[i, j]` and the
zero/nonzero pattern of the quantity ledger are **identical between the actual and the
counterfactual paths**. This holds because `will_hold` depends only on `q_prev != 0` and
`w_target[i, j] != 0`, and `quantity[i, j] = w_target[i, j] * NAV_after_cost[i] / P[i, j]` is
zero exactly when `w_target[i, j]` is zero (`NAV_after_cost > 0` on every period reaching
Step 3). The paths therefore differ only in position **magnitude** and in **length**, never in
which symbols are active or which prices are required.

**Consequence:** the two paths require exactly the same prices over the periods they share, so
the only exceptions unique to the counterfactual arise in periods **beyond the actual path's
ruin** — data the actual path never read. This is why the barrier cannot mask a defect that
should have failed the actual path. The argument rests on this invariant, not on intuition.

The barrier MUST catch only the enumerated types. `KeyboardInterrupt`, `MemoryError`,
`SystemExit` and programming errors (`TypeError`, `AttributeError`, `NameError`) MUST propagate.

#### 9.5.2 Mandatory invariant

> For any inputs where the actual path is valid, `compute_counterfactual=False` and
> `compute_counterfactual=True` MUST produce **bit-identical actual-path outputs.**

Holds unconditionally, including when the counterfactual is `FAILED` or `RUINED`.

---

## 10. Result surface

**Equity** (§8 index, `n_periods + 1` rows) — `equity_curve`.

**Per-period series** (`n_periods` rows, row `i` = period `[T_i, T_{i+1})`) — `net_return`,
`gross_return`, `fee_return`, `slippage_return`, `funding_return`, `fee_cost`,
`slippage_cost`, `funding_pnl_cash`, `asset_pnl_cash`, `fee_basis_notional`, `turnover`,
`gross_exposure`, `net_exposure`, `gross_leverage`, `rebalance_flag`.

Field names are **singular** and match §6.1 exactly.

`NAV_pre[i]` is `equity_curve[i]`; `NAV_after_cost[i]` is
`equity_curve[i] - fee_cost[i] - slippage_cost[i]`. Both derivable from the public surface.

**Per-period frames** (period x symbol) — `quantity`, `notional`, `positions` (`w_held`),
`pre_trade_weights` (`w_pre`), `target_weights` (as supplied), `trades`, `symbol_state`.

`rebalance_flag[i]` is the **execution-point** indicator, not the input mask at signal bar `t`.

**Counterfactual** — `counterfactual_gross_equity`, `counterfactual_gross_return`,
`counterfactual_gross_metrics`, `counterfactual_total_return`, `counterfactual_cagr`,
`counterfactual_status`, `counterfactual_error`, `counterfactual_ruined`,
`counterfactual_ruin_timestamp`, `counterfactual_leverage_breach`, `total_drag_return`,
`cagr_drag`, `drag_comparable`.

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

Accounting: non-finite `NAV_end` (§6.0 Step 7).

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
- `risk_free_per_period` and `mar_per_period` are **scalars**, default 0.0. The Sharpe
  denominator uses `std(net_return)`, correct only because `rf` is constant.

### 12.3 Degenerate cases

Return `nan` — never 0, never an exception: `annualized_volatility == 0` -> `sharpe`;
`downside_dev_ann == 0` -> `sortino`; `max_drawdown == 0` -> `calmar`; `n_periods < 2` -> every
dispersion-based metric.

### 12.4 Metrics under ruin

Metrics use the actual observations through the ruin period. No padding.

- `total_return = -1.0` exactly
- `max_drawdown = -1.0` exactly
- `cagr = -1.0` exactly. Verified: `0.0 ** (af/n_periods)` returns `0.0` with no domain error
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
reports `cagr ≈ 2.49e7`.

This is arithmetically correct and is **not** a defect, but `cagr` and `calmar` are
meaningless on short samples and MUST NOT be pinned as expected values in short fixtures
(§18.7 CF2 pins `total_drag_return` for exactly this reason). Any report over a short horizon
should present `total_return` and suppress or footnote `cagr`.

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
processes and `PYTHONHASHSEED` values (§18.2 P2).

---

## 17. Floating-point policy

**Exact equality (`==`) is permitted ONLY for genuinely discrete state:**

- booleans and flags (`ruined`, `rebalance_flag`, `leverage_breach`, `drag_comparable`,
  `survivorship_safe`, `counterfactual_status`)
- boolean masks and `symbol_state` classifications
- indices, timestamps, lengths, event counts
- **quantities carried forward unchanged** across a non-rebalance period (stored state)
- **quantities, weights and notionals assigned literal `0.0`**
- values that are exactly representable **and** reached by a single arithmetic path

**Documented tolerances are REQUIRED for everything reached by a different but mathematically
equivalent floating-point path:**

| quantity | tolerance |
|----------|-----------|
| NAV identity (N-1), equity recursion §8 | `rtol=1e-12`, `atol=1e-9` (USD) |
| return decomposition (N-2), `ruin_decomposition_residual` | `rtol=1e-12`, `atol=1e-15` |
| reconstructed / derived weights, exposures | `rtol=1e-12`, `atol=1e-15` |
| turnover and cost assertions | `rtol=1e-12`, `atol=1e-15` |
| metrics, drag statistics | `rtol=1e-10`, `atol=1e-12` |

The metrics `atol` exists because `rtol` alone cannot compare a true value of `0.0`.

**This policy governs pinned literal values in §18.** A literal such as `-0.006` is compared
under the applicable tolerance row, never with `==`, unless §18.0.1 classifies that specific
assertion EXACT.

Measured evidence: the equity recursion failed bitwise on **32.1%** of periods (worst relative
error 4.32e-16); the `w_held` rebalance branch on **13.9%**; `q -> w -> q` on **17.8%**.

Justification of the levels — the smallest *real* defect class is orders of magnitude above:

| defect | relative magnitude |
|---|---|
| `ddof=0` instead of `ddof=1` on Sharpe (n=8) | 6.9e-2 |
| one-bar execution lag error (fixture E) | 1.0e0 |
| `period_start` vs true funding notional on a 15% move | 7.0e-2 |
| fee charged on `NAV_after_cost` vs `NAV_pre` | 2.0e-2 |
| **measured floating-point noise** | **4.3e-16** |

---

## 18. Mandatory tests

### 18.0 Assertion classification and fixture completeness

#### 18.0.1 Classification table (EXACT vs TOLERANCE)

> **Every numeric assertion in §18 is classified below. This classification is normative and
> was produced by mechanical review of each assertion individually.**

Float-equality defects appeared in four consecutive revisions — v1.1 R4, v1.2 N5 and §8, v1.3
X6 — the last inside the revision that introduced §17. Writing the policy is not the same as
applying it.

| test | assertion | class | justification |
|------|-----------|-------|---------------|
| A | zero PnL / fees / turnover | EXACT | assigned literals, excluded from summation |
| B, C, D | compounded PnL / total_return | TOLERANCE | multi-step arithmetic |
| E, E2, E3 | final NAV | TOLERANCE | exact today (verified bitwise), but tolerance costs nothing — `rtol=1e-12` still separates 1e6 from 2e6 by 100% — and avoids making the flagship anti-lookahead tests bit-fragile to a legitimate reassociation inside Step 6 |
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
| N3t | identity (N-1) | TOLERANCE | §17 row 1 |
| N5t | equity recursion | TOLERANCE | §17 row 1 — measured 32.1% bitwise failure |
| N6t | `NAV_after_cost == NAV_pre*(1 - turnover*bps_total)` | TOLERANCE | §17 row 4 |
| N7t | decomposition (N-2) | TOLERANCE | §17 row 2 |
| F1, F7, F12 | funding aggregates | TOLERANCE | sums of products |
| F2, F10 | counted once / excluded counts | EXACT | integer counts |
| F4, F13, F15, F18, F21 | raises | EXACT | exception type |
| F5 | funding exactly 0 | EXACT | assigned literal |
| F6 | funding sign | EXACT | sign comparison |
| F8 | windows differ per mode | TOLERANCE | compares two funding magnitudes |
| F9, F14, F16, F19, F22 | does NOT raise | EXACT | absence of exception |
| F17 | gap `== max_funding_gap` accepted | EXACT | integer-nanosecond Timedelta |
| F20 | `funding_gap_tolerance_suspicious` set | EXACT | boolean |
| M1, M2, M5 | Sharpe, Sortino, total_return, cagr, max_drawdown | TOLERANCE | §17 metrics row |
| M3 | degenerate cases `nan` | EXACT | `isnan` predicate |
| M4 | cagr differs from arithmetic annualization | TOLERANCE | inequality with margin |
| M6 | drawdown captures first-period loss | TOLERANCE | inequality with margin |
| CF1 | counterfactual equals actual at zero cost | EXACT | `x - 0.0 - 0.0 == x` for finite `x`; with zero costs `NAV_after_cost` is bitwise `NAV_pre`, so the quantity ledger is bit-identical |
| CF2 | `total_drag_return` values | TOLERANCE | §17 metrics/drag row |
| CF3 | `cumprod(1+gross) != cf_equity` | TOLERANCE | inequality with margin |
| CF4 | hand-computed counterfactual | TOLERANCE | multi-step |
| CF5 | timing/mask respected | EXACT | index/boolean |
| CF6, CF7 | path lengths, ruin flags, status | EXACT | integers / booleans / strings |
| CF8, CF9 | actual bit-identical across `compute_counterfactual` | EXACT | §9.5.2 mandates bitwise |
| CF10 | `drag_comparable == False`, drag `None` | EXACT | boolean / identity |
| CF11 | actual-path error propagates | EXACT | exception type |
| X1 | equity, net_return, uncapped, residual | TOLERANCE, except `equity[-1] == 0.0` EXACT | |
| X2 | series lengths | EXACT | integers |
| X3 | no `inf`; `NaN` only where §6.7.2 permits | EXACT | predicate on the enumerated field set |
| X4 | `total_return`, `max_drawdown`, `cagr`, `calmar` all `-1.0` | EXACT | verified bitwise on both X1 and X9 |
| X5 | `n_periods == 1`, metrics `nan` | EXACT | integer / predicate |
| X6 | `ruin_decomposition_residual` | TOLERANCE | true value `-0.19880000000000008775`, not the literal `-0.1988` |
| X7 | `ruined=True` in `__repr__` | EXACT | string containment |
| X8a | near-ruin finiteness | EXACT | `isfinite` predicate |
| X8b | `leverage_breach` fires | EXACT | boolean |
| X9 | cost-stage ruin row | TOLERANCE, except lengths, `equity[-1] == 0.0`, quantity carry and `NaN` predicates EXACT | |
| X10 | `ruin_stage`, `terminal_position_convention` | EXACT | strings |
| S1 | equity finite / `asset_pnl_cash` value | EXACT for `isfinite`; TOLERANCE for the value |
| S2, S3 | raises | EXACT | exception type |
| S4 | **EXITING** symbol completes without `P[i+1]` | EXACT | absence of exception |
| S5 | `symbol_state` matches §5.3 | EXACT | categorical |
| S6 | INACTIVE contributes exactly zero | EXACT | assigned literals, excluded from summation |
| S7 | `exit_unnamed` materialises `0.0` | EXACT | assigned literal |
| V1, V2 | raises `InvalidPriceError` | EXACT | exception type |
| V3 | denormal price -> `AccountingError`, no silent `inf` | EXACT | exception type / predicate |
| T1 | recorded in `unexecuted_rebalances` | EXACT | membership |
| T2, T3 | lengths / raises | EXACT | integers / exception type |
| T4 | `execution_instant()` values | EXACT | timestamps |
| T5 | grid/frequency mismatch raises | EXACT | exception type |
| U1, U3 | raises / no trade | EXACT | exception type / assigned literal |
| U2 | proceeds silently | EXACT | absence of exception |
| D4t | irregular grid raises | EXACT | exception type |
| PR1–PR6 | provenance field equality, flags | EXACT | field equality / booleans |

Test IDs in §18.4 are suffixed `t` (`N1t`…`N7t`) to avoid collision with audit warning IDs;
`D4t` likewise disambiguates from defect ID D4.

#### 18.0.2 Fixture completeness rule

> **Every fixture whose result depends on configuration MUST explicitly state the relevant
> configuration. No fixture may inherit a value that `BacktestConfig` does not default.**

`fee_bps` and `slippage_bps` have **no defaults** (§15), so any fixture asserting a PnL,
equity or return value MUST state both. A fixture that omits them is underdetermined, not
merely default-reliant.

Each §18 subsection below carries a **Config** header applying to every fixture in it, with
per-test overrides stated inline. Where a value is economically material it is stated even if
`BacktestConfig` would default it.

**No fixture may use `execution_lag = 0`**, because §4.2 forbids it in a production
`BacktestConfig`. Any test needing artificial same-instant execution MUST use the lower-level
accounting helper `_step_period(...)`, exercising §6.0 Steps 1–6 directly with `w_target`
supplied per period, and MUST be labelled `# LOWER-LEVEL HELPER — not a production config`.

An independent implementer MUST be able to reproduce every pinned fixture from this
specification alone.

Values marked **[computed]** were calculated by the Research Lead and are reproduced to be
**independently re-derived by the auditor**, not accepted on authority.

#### 18.0.3 Cross-section consistency pass

Before this revision was submitted for audit, every mandatory test was checked against:

1. the normative rule it tests
2. whether the fixture satisfies **all preconditions of that rule**
3. whether all required config fields are specified
4. recomputation of every numeric expected value
5. EXACT vs TOLERANCE classification
6. whether the test would fail under the incorrect behaviour it targets

Check 2 exists because v1.4's E1 was a test that violated the preconditions of the very rule
it tested: CF2 asserted `total_drag_return < 0` on a fixture where §9.2 mandates that field be
`None`, making the assertion `None < 0` — a `TypeError` on every run. The rule was correct;
the test violated it. Any future revision that adds a precondition to a rule MUST re-run this
pass over every test touching the quantity that rule governs.

---

### 18.1 Anti-lookahead

**Config (all of E, E2, E3):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "disabled"`,
`compute_counterfactual = False`, single symbol, target weight `1.0`. No metric is asserted,
so `annualization_factor` is immaterial.

**Test E — lag discrimination.** `execution_mode = "next_open"`, mask True at **bar 2 only**:

    open = [100, 100, 100, 200, 200, 200]        # 6 bars, 5 holding periods
    holding-period returns = [0, 0, 1.0, 0, 0]

    execution_lag = 1  ->  final NAV == 1_000_000     [computed]
    execution_lag = 0  ->  final NAV == 2_000_000     [computed]   (lower-level helper)

Both assertions mandatory. A test checking only the `lag = 1` value cannot demonstrate it
would catch the bug.

**Test E2 — execution mode discrimination.** `execution_lag = 1`, mask True at bar 2. Open and
close must diverge or the test is vacuous:

    open  = [100, 100, 100, 100, 200, 200]    -> r_open  = [0, 0, 0, 1.0, 0]
    close = [100, 100, 100, 200, 200, 200]    -> r_close = [0, 0, 1.0, 0, 0]

    execution_mode = "next_open"   ->  final NAV == 2_000_000     [computed]
    execution_mode = "next_close"  ->  final NAV == 1_000_000     [computed]

**Test E3 — `execution_lag = 2`.** `execution_mode = "next_open"`, mask True at bar 2:

    open = [100, 100, 100, 200, 400, 400, 400]    # 7 bars

    execution_lag = 0  ->  final NAV == 4_000_000     [computed]   (lower-level helper)
    execution_lag = 1  ->  final NAV == 2_000_000     [computed]
    execution_lag = 2  ->  final NAV == 1_000_000     [computed]

Three distinct values, so lag 2 is separable from both lag 1 and lag 0.

**Test E4.** Perturb a price after `P[i+1]` and assert earlier periods are bit-identical.

### 18.2 Core accounting

**Config (all):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `execution_lag = 1`, `funding_mode = "disabled"`,
`compute_counterfactual = False`, `annualization_factor = 365`. `fee_bps` and `slippage_bps`
are stated per test — they have no default.

| id | requirement |
|----|-------------|
| A | `fee_bps = 0`, `slippage_bps = 0`, all target weights 0 -> zero PnL, zero fees, zero turnover |
| B | **`fee_bps = 0`, `slippage_bps = 0`.** `w = +1.0`, mask True at bar 0, `P = [100, 110, 121]` -> execution at `i = 1`; period 0 flat; `equity_curve = [1e6, 1e6, 1.1e6]`, `total_return = 0.1` |
| C | **`fee_bps = 0`, `slippage_bps = 0`** (N3 fix — the v1.4 fixture was unreproducible because these have no default). `w = -1.0`, mask True at bar 0, `P = [100, 110, 121]` -> `equity_curve = [1e6, 1e6, 0.9e6]`, `total_return = -0.1` |
| D | **`fee_bps = 0`, `slippage_bps = 0`.** Two symbols, mask True at bar 0, `w = [+0.5, -0.5]`, `P_A = [100, 110, 110]`, `P_B = [50, 50, 55]` -> execution at `i = 1`; period 1 earns `0.5*0 + (-0.5)*0.1 = -0.05` -> `total_return = -0.05` |
| G | `fee_bps = 0`, `slippage_bps = 0`. `0 -> +1` produces turnover 1 |
| H | `fee_bps = 0`, `slippage_bps = 0`. `+1 -> -1` produces turnover 2 |
| I | **`fee_bps = 10`, `slippage_bps = 0`.** turnover 2 at `NAV_pre = 1e6` -> `fee_cost = 2000.0` |
| J | **`fee_bps = 0`, `slippage_bps = 10`.** turnover 2 at `NAV_pre = 1e6` -> `slippage_cost = 2000.0` |
| L | `fee_bps = 0`, `slippage_bps = 0`. `w = [+0.6, -0.4]` -> `gross_exposure == 1.0` at the execution point |
| M | same fixture -> `net_exposure == 0.2` |
| N | `w = [+1.5, -1.5]` -> `gross_leverage == 3.0` |
| P | two runs compare exactly equal |
| P2 | determinism across separate processes and differing `PYTHONHASHSEED` |

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
| R6 | **`execution_lag = 2`, `n = 8` bars** (N3 fix — v1.4 omitted the bar count; the assertion only holds for `n >= 6`, else §4.4 routes it to `unexecuted_rebalances`). Mask True at bar 2 only -> `rebalance_flag` is True at index **4 only** |

### 18.4 NAV consistency

**Config (all):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `execution_lag = 1`, `compute_counterfactual = False`,
`annualization_factor = 365`. Cost and funding settings stated per test.

| id | requirement |
|----|-------------|
| N1t | `fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "required"`, `basis = "period_start"`. Hand-computed case where **funding alone** changes NAV; next period's `w_pre` matches. Must fail if funding is omitted from `NAV_end` |
| N2t | `fee_bps = 10`, `slippage_bps = 10`, `funding_mode = "disabled"`. Same for fees and slippage |
| N3t | `fee_bps = 5`, `slippage_bps = 3`, `funding_mode = "required"`, `basis = "period_start"`. Identity (N-1) on a randomized multi-asset case with all four cost components nonzero |
| N5t | equity recursion at §17 tolerance, on the N3t fixture |
| N6t | **discriminating (W3 fix).** Config: `fee_bps = 50`, `slippage_bps = 50`, `funding_mode = "disabled"`, `execution_mode = "next_open"`, `execution_lag = 1`, `initial_capital = 1_000_000` (N3 fix — v1.4 omitted these). Assert `NAV_after_cost == NAV_pre * (1 - turnover * bps_total)` at §17 tolerance, `bps_total = (fee_bps + slippage_bps)/10_000 = 0.01`. Pinned: `NAV_pre = 1_000_000`, `turnover = 4` -> `NAV_after_cost == 960_000.0` **[computed]**. The rejected self-consistent solve `NAV_pre/(1 + turnover*bps_total) = 961_538.46153846150264` **[computed]** differs by `1538.46` and **fails** this assertion |
| N7t | decomposition (N-2) sums to `net_return` on all non-ruin periods, on the N3t fixture |

### 18.5 Funding

**Config (all):** `initial_capital = 1_000_000`, `execution_mode = "next_open"`,
`execution_lag = 1`, `fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "required"`,
`funding_notional_basis = "period_start"`, `compute_counterfactual = False`,
`annualization_factor = 365`. `frequency` stated per test. Overrides stated inline.

| id | requirement |
|----|-------------|
| F1 | `frequency = "1d"`. 24 hourly events inside one 1d bar aggregate to the sum of 24 charges — must fail if the engine assumes one event per bar |
| F2 | `frequency = "1d"`. An event exactly on a boundary is counted once, in the later period |
| F4 | `frequency = "1d"`. Genuinely absent funding data for a funding-accruing period raises `FundingDataError` |
| F5 | `funding_mode = "disabled"` -> funding exactly 0, `funding_modelled = False` |
| F6 | `frequency = "1d"`. Long + positive rate -> negative funding PnL; short + positive rate -> positive |
| F7 | `frequency = "1d"`. Irregular event spacing within tolerance aggregates correctly |
| F8 | `frequency = "1d"`, hourly events, one 10x rate day. `next_open` vs `next_close` produce different, documented windows. Must fail if `T_i` ignores `execution_mode` |
| F9 | `frequency = "1h"`, complete 8h stream, `max_funding_gap = 8h` -> does **not** raise |
| F10 | `frequency = "1d"`. Events before `T_0` or at/after `T_{n-1}` excluded and counted |
| F12 | `frequency = "1d"`. Funding on a rebalance period is valued on the **post-trade** quantity |
| F13 | `frequency = "1h"`. A gap exceeding `max_funding_gap` inside a coverage window raises |
| F14 | A symbol with no funding-accruing period needs no funding data |
| F15 | `frequency = "1h"`. **Coverage false-accept**: events only outside `[coverage_start, coverage_end]` — the `{2025-01-01, 2027-01-01}` counterexample — MUST raise |
| F16 | `frequency = "1h"`. Non-contiguous exposure with two disjoint, non-touching coverage records does **not** raise |
| F17 | `frequency = "1h"`. A gap exactly equal to `max_funding_gap` is accepted |
| F18 | `basis = "event_price"`, any missing/invalid `notional_price` on an applied event raises |
| F19 | `basis = "period_start"` ignores `notional_price` entirely — present-but-invalid values do not raise and do not affect the result |
| F20 | Modal spacing far below `max_funding_gap` sets `funding_gap_tolerance_suspicious` without raising |
| F21 | Touching or overlapping `FundingCoverage` records for one symbol raise `DataIntegrityError` |
| **F22** | **N6 fix — cost-stage ruin before any funding-accruing interval.** Config override: `fee_bps = 1500`, `slippage_bps = 1500`, `frequency = "1d"`, `funding_mode = "required"`, `basis = "period_start"`. Use the §18.8 X9 price path and mask, with `FundingCoverage` deliberately **ending at `T_2`** so it does not cover the ruin period `[T_2, T_3)`. Assert: **(a)** no `FundingDataError` is raised — the cost-stage ruin terminates at Step 2, the interval never occurs, and §7.7.1 condition 1 excludes it, even though §6.7.1 reports `quantity = 7000.0` at the terminal row; **(b)** the run completes with `ruined == True`, `ruin_stage == "cost"`; **(c)** with coverage instead ending at `T_1`, so that the genuinely funding-accruing period `[T_1, T_2)` is uncovered, `FundingDataError` **IS** raised. (b) and (c) together prove the rule discriminates rather than merely suppressing |

### 18.6 Metrics

**M7 is deleted and MUST NOT be reintroduced in any form.** No test may assert a derived
identity relating Sharpe and Sortino.

**Config:** `initial_capital = 1_000_000`, `annualization_factor = 365`,
`risk_free_per_period = 0`, `mar_per_period = 0`. `net_return` is supplied directly to the
metrics functions, so `execution_mode`, `execution_lag`, `fee_bps`, `slippage_bps` and
`funding_mode` are immaterial — §17's `metrics.py` purity requirement makes this testable
without an engine run.

    net_return = [0.010, -0.005, 0.020, -0.015, 0.000, 0.008, -0.012, 0.006]
    n_periods  = 8   (3 negative periods)

**Equity is built by §8's sequential recursion, and the pinned values below are derived from
that path (N10 fix).** v1.4 pinned the `cumprod` values, which differ in the last 2 ulp. All
metric assertions are TOLERANCE-class, and both paths pass at `rtol=1e-10` with ~1e-14 margin,
but the normative path is the recursion.

Values **[computed]**:

    mean(net_return)        = 0.0015
    std(net_return, ddof=1) = 0.011807987611298186
    annualized_volatility   = 0.22559128655918556
    M1  sharpe              = 2.4269554394174677

    mean(min(r,0)**2)       = 4.9250000000000004e-05
    downside_dev_ann        = 0.13407553841025588
    M2  sortino             = 4.0835189363529718

    equity_curve[-1]        = 1011570.8691663994          (sequential; cumprod gives …3996)
    M5  total_return        = 0.011570869166399378
        cagr                = 0.6902729275701369
        max_drawdown        = -0.019034560000000034       (bitwise identical on both paths)

| id | requirement |
|----|-------------|
| M1 | `sharpe` equals the literal above |
| M2 | `sortino` equals the literal above |
| M3 | every §12.3 degenerate case returns `nan`, not 0, not an exception |
| M4 | on the pinned fixture `cagr = 0.6902729275701369` while arithmetic annualization `mean*af = 0.5475` **[computed]** — differing by ~26%, guarding a silent swap |
| M5 | `total_return`, `cagr`, `max_drawdown` equal the literals above |
| M6 | `max_drawdown` captures a first-period loss below `initial_capital` |

### 18.7 Counterfactual

**Config (all):** `initial_capital = 1_000_000`, `frequency = "1d"`,
`execution_mode = "next_open"`, `execution_lag = 1`, `annualization_factor = 365`,
`compute_counterfactual = True`. Costs and funding stated per fixture.

#### CF2 fixtures — both paths COMPLETE over the SAME horizon (E1 fix)

v1.4 bound CF2 to the CF7 fixture, where the counterfactual ruins. §9.2 then mandates
`total_drag_return is None`, so the assertion `total_drag_return < 0` evaluated `None < 0` and
raised `TypeError`. **The §9.2 rule is correct and is retained unchanged**; CF2 is re-bound to
two fixtures that satisfy its preconditions.

**CF2a — costs dominate, drag POSITIVE.** `fee_bps = 10`, `slippage_bps = 10`,
`funding_mode = "disabled"`, 1 symbol, target `1.0`, mask True at bar 0 only:

    P = [[100], [100], [110]]      # 3 bars, 2 periods; execution at i = 1

Expected **[computed]**:

    actual         equity = [1_000_000, 1_000_000, 1_097_800.0]   ruined = False, 2 periods
    counterfactual equity = [1_000_000, 1_000_000, 1_100_000.0]   ruined = False, 2 periods
    counterfactual_status = "COMPLETED" ,  drag_comparable = True

    period 1: turnover = 1.0 , fee_cost = 1000.0 , slippage_cost = 1000.0
              NAV_after_cost = 998_000.0 , quantity = 9980.0 , asset_pnl_cash = 99_800.0

    total_return               = 0.0978
    counterfactual_total_return = 0.10
    total_drag_return           = 0.0022        (POSITIVE)

**CF2b — funding income, drag NEGATIVE.** `fee_bps = 0`, `slippage_bps = 0`,
`funding_mode = "required"`, `funding_notional_basis = "period_start"`, 1 symbol, target `1.0`,
mask True at bar 0 only, funding rate `-0.01` per period (long **receives**), coverage spanning
`[T_0, T_2]` with `max_funding_gap = 1d`:

    P = [[100], [100], [100]]      # flat, 3 bars, 2 periods; execution at i = 1

Expected **[computed]**:

    actual         equity = [1_000_000, 1_000_000, 1_010_000.0]   ruined = False, 2 periods
    counterfactual equity = [1_000_000, 1_000_000, 1_000_000.0]   ruined = False, 2 periods
    counterfactual_status = "COMPLETED" ,  drag_comparable = True

    period 1: quantity = 10_000.0 , funding_pnl_cash = +10_000.0

    total_return                = 0.01
    counterfactual_total_return = 0.0
    total_drag_return           = -0.01         (NEGATIVE)

Prices are flat so the drag is attributable to funding alone. Neither path ruins and both span
exactly 2 periods, so all three §9.2 preconditions hold.

**`cagr_drag` is NOT pinned on either fixture.** With `n_periods = 2` and `af = 365` the CAGR
exponent is `182.5`, so `cagr` is ~2.49e7 on CF2a — arithmetically correct but a meaningless
assertion (§12.5). CF2 asserts `total_drag_return`, which is horizon-independent.

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

**Pinned CF7 fixture** — counterfactual ruins, actual survives. `fee_bps = 0`,
`slippage_bps = 0`, `funding_mode = "required"`, `funding_notional_basis = "period_start"`,
1 symbol, target `2.5`, mask True at bar 0 only, funding rate `-0.09` per period:

    P = [[100], [100], [60], [60], [60]]        # 5 bars, 4 periods

    actual         equity = [1_000_000, 1_000_000, 225_000, 360_000, 495_000]  ruined = False  [computed]
    counterfactual equity = [1_000_000, 1_000_000, 0]                          ruined = True   [computed]

| id | requirement |
|----|-------------|
| CF1 | `fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "disabled"` -> counterfactual equity equals actual equity, EXACT |
| CF2 | On **CF2a**, `total_drag_return == 0.0022` and `> 0`. On **CF2b**, `total_drag_return == -0.01` and `< 0`. Both asserted in one test, both with `drag_comparable == True` and `counterfactual_status == "COMPLETED"`. Must fail if drag is computed across mismatched horizons |
| CF3 | On the CF3 fixture, `cumprod(1+gross_return)` does **not** equal counterfactual equity |
| CF4 | Hand-computed 3-period 2-asset counterfactual (the CF3 fixture), all values in the docstring |
| CF5 | Counterfactual respects the same execution timing and rebalance mask |
| CF6 | **actual ruins, counterfactual survives**: counterfactual retains its full length; actual truncates; `drag_comparable == False`; drag fields `None` |
| CF7 | **counterfactual ruins, actual survives**: on the pinned fixture the actual retains all 5 equity observations, `ruined == False`, `counterfactual_ruined == True`, `counterfactual_status == "RUINED"`, **`drag_comparable == False` and `total_drag_return is None`** (§9.2 precondition 1 fails) |
| CF8 | **isolation invariant**: the actual result is **bit-identical** with `compute_counterfactual` `True` and `False`, on the CF7 fixture where the counterfactual RUINS |
| CF9 | **exception isolation**: actual ruins at period 3; counterfactual survives to period 10; a held symbol's price is absent at period 7 — data the actual never reads. The backtest MUST return successfully, actual **bit-identical** to the `compute_counterfactual=False` run, `counterfactual_status == "FAILED"`, `counterfactual_error` populated, `drag_comparable == False`. Must fail if the counterfactual's exception propagates |
| CF10 | when the paths differ in length, `drag_comparable == False` and `total_drag_return`/`cagr_drag` are `None` |
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
        NO NaN anywhere

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

This fixture deliberately holds a nonzero position when the cost-stage ruin fires, so §6.7.1's
convention is exercised. `quantity == 7000.0` is the position the account was carrying when it
died; it is **not** a liquidation.

| id | requirement |
|----|-------------|
| X1 | the X1 fixture reproduces every value above |
| X2 | series are truncated, not padded: `len(net_return) == ruin_period + 1`, `len(equity_curve) == ruin_period + 2` |
| X3 | **no `inf` anywhere**; `NaN` **only** in the four fields §6.7.2 permits, **only** at a cost-stage terminal row. Asserted on both X1 (no `NaN` at all) and X9 |
| X4 | `total_return`, `max_drawdown`, `cagr`, `calmar` all exactly `-1.0`, EXACT, on both fixtures |
| X5 | ruin at period 0 -> `n_periods == 1`, dispersion metrics `nan`, no exception |
| X6 | identity **(N-1) only** at the ruin period. MUST NOT assert (N-2). Asserts `ruin_decomposition_residual` at §17 tolerance: `-0.1988` on X1, `-0.5` on X9. Additionally on X9, asserts `fee_return + slippage_return == uncapped_ruin_return` at tolerance (§6.7.3) |
| X7 | `ruined=True` appears in `__repr__` |
| X8a | near-ruin (`NAV_end` small but positive) produces no `inf` in any output |
| X8b | `leverage_breach` fires when `max_gross_leverage` is set and breached |
| X9 | the X9 fixture reproduces every value above, including the class-A pre-trade `quantity` and the four class-C `NaN` sentinels |
| X10 | `ruin_stage == "pnl"` on X1 and `"cost"` on X9; `terminal_position_convention == "pre_ruin_state"` on both |

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
| V3 | denormal positive price (e.g. `5e-324`) passes §5.5 but yields non-finite `NAV_end` -> `AccountingError` at §6.0 Step 7, never a silent `inf` (N9) |
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

**Config:** immaterial to these tests beyond a valid minimal run —
`fee_bps = 0`, `slippage_bps = 0`, `funding_mode = "disabled"`, `execution_lag = 1`,
`execution_mode = "next_open"`, `frequency = "1d"`, `initial_capital = 1_000_000`.

| id | requirement |
|----|-------------|
| PR1 | supplied provenance appears unmodified, field for field, including `field_type` and `time_range` |
| PR2 | absent provenance -> `provenance_supplied == False` |
| PR3 | any `native_or_proxy == "proxy"` -> `uses_proxy_data == True`, surfaced in `__repr__` |
| PR4 | `native_or_proxy == "proxy"` with `proxy_for` `None`/empty raises `DataIntegrityError` |
| PR5 | an all-`None` `DatasetProvenance` gives `provenance_complete == False` |
| PR6 | `UniverseProvenance` passes through unmodified; `survivorship_safe` is `None` when unsupplied and never defaults to `True`; `None`/`False` surfaced in `__repr__` |

### 18.11 Coverage

Both `execution_mode` values across the engine suite. Every exception path in §11.2. Every
config validation in §15. Every state in §5.3. Both ruin stages.

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
the self-consistent solve by `O(turnover * bps)` relative — **2.00%** at `fee_bps = 50` alone
with turnover 4, and **4.00%** at the §18.4 N6t config of 50 + 50 bps (N11).

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

**20.11 — §17 tolerances retained. ACCEPTED**: ~4 orders above measured noise, ~10 orders
below the smallest plausible real defect.

**20.12 — Terminal ruin row reports the pre-ruin position state, not a liquidation. ACCEPTED.**

**20.13 — §9.2's drag comparability rule retained unchanged; the violating test was fixed
instead. ACCEPTED** (E1). A drag statistic must never compare different horizons; relaxing the
rule to make a test pass would have published drag computed from a truncated counterfactual
against a full-length actual.

---

## 21. Open questions for the auditor

1. §7.7.1 defines funding-accruing periods as those reaching Step 5 with a Step-3-sized nonzero
   quantity. Does any other consumer of "exposure" in the spec need the same distinction between
   *reported* terminal quantity and *economically held* quantity?
2. §18.6 asserts metrics against `metrics.py` as a pure function without an engine run, so
   `equity_curve` is constructed by the test from the supplied `net_return`. Is that a
   legitimate way to pin §8's recursion, or should M5 additionally run through the engine?
3. §12.5 documents short-horizon CAGR as an interpretation hazard but does not suppress it.
   Should `cagr`/`calmar` return `nan` below some `n_periods` threshold, or is documenting it
   sufficient for a research engine?
4. CF2a and CF2b are 2-period fixtures. Is that horizon adequate to test drag attribution, or
   does a longer fixture with multiple rebalances catch a class of error these cannot?
