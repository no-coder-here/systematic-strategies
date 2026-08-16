"""The canonical backtest engine (contract §6.0 accounting sequence, §7 funding,
§9 counterfactual). ONE engine. No alpha logic, no second accounting path.

Step numbers in comments map 1:1 to §6.0 of docs/backtest_contract.md (v1.5.1,
FROZEN). An auditor should be able to read this file next to the contract and
match every commented step.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np
import pandas as pd

from .costs import compute_trade, compute_turnover, fee_cost as _fee_cost_fn, slippage_cost as _slippage_cost_fn, fee_basis_notional as _fee_basis_notional_fn
from .metrics import compute_metrics
from .models import (
    AccountingError,
    BacktestConfig,
    BacktestResult,
    ConfigError,
    DataIntegrityError,
    DatasetProvenance,
    FundingCoverage,
    FundingDataError,
    FundingEvent,
    InvalidPriceError,
    MarketData,
    MissingPriceError,
    StrategyOutput,
    UniverseProvenance,
)

# Cost-stage skip set (§6.0): the exact, sole implementation of the skip is
# the early `return` in `_step_period` at the cost-stage-ruin branch (Step
# 4). There is no second, separately-maintained set: Steps 5-12 are simply
# never reached in the source below that early return.

_STATE_INACTIVE = "INACTIVE"
_STATE_ENTERING = "ENTERING"
_STATE_HELD = "HELD"
_STATE_EXITING = "EXITING"


# ---------------------------------------------------------------------------
# §4.3 execution_instant — explicit, separately tested function
# ---------------------------------------------------------------------------


def execution_instant(bar_label: pd.Timestamp, mode: str, delta: pd.Timedelta) -> pd.Timestamp:
    """§4.3 — T_i as a function of bar label t_i, execution_mode and Δ."""
    if mode == "next_open":
        return bar_label
    if mode == "next_close":
        return bar_label + delta
    raise ConfigError(f"unknown execution_mode {mode!r}")


# ---------------------------------------------------------------------------
# §4.2 Execution price series — explicit, separately tested selection
# ---------------------------------------------------------------------------


def _select_execution_price_frame(market_data: MarketData, execution_mode: str) -> pd.DataFrame:
    """§4.2 — explicit price-series selection.

        P = open   if execution_mode == "next_open"
        P = close  if execution_mode == "next_close"

    This is the ONLY place `market_data.open` / `market_data.close` are read.
    The unselected series is never consulted again anywhere in the engine —
    it is not validated (§5.5), not used for sizing, valuation, PnL or
    funding. A caller who supplies genuinely different open/close data and a
    wrong `execution_mode` gets exactly the wrong series, not a silent blend
    of both.
    """
    if execution_mode == "next_open":
        return market_data.open
    if execution_mode == "next_close":
        return market_data.close
    raise ConfigError(f"unknown execution_mode {execution_mode!r}")


# ---------------------------------------------------------------------------
# §5.5 Price validity
# ---------------------------------------------------------------------------


def _validate_price(value: float, symbol: str, timestamp: pd.Timestamp, use: str) -> None:
    """§5.5 — every price actually used MUST be finite and strictly > 0.

    NaN -> MissingPriceError (a price that was never observed).
    +-inf / 0.0 / negative -> InvalidPriceError (a price that was observed
    but violates the constraint). MissingPriceError IS an InvalidPriceError
    (see models.py docstring for the reconciliation of §5.5 and §11.2/S2).
    """
    if math.isnan(value):
        raise MissingPriceError(
            f"missing price for {symbol!r} at {timestamp!r} (required for {use})"
        )
    if not math.isfinite(value) or value <= 0.0:
        raise InvalidPriceError(
            f"invalid price {value!r} for {symbol!r} at {timestamp!r} (required for {use}): "
            "must be finite and strictly > 0"
        )


# ---------------------------------------------------------------------------
# §5.3 Symbol activity classification
# ---------------------------------------------------------------------------


def classify_symbol_state(q_prev: float, will_hold: bool) -> str:
    q_prev_zero = q_prev == 0.0
    if q_prev_zero and not will_hold:
        return _STATE_INACTIVE
    if q_prev_zero and will_hold:
        return _STATE_ENTERING
    if (not q_prev_zero) and will_hold:
        return _STATE_HELD
    return _STATE_EXITING


# ---------------------------------------------------------------------------
# §2.1 Regular grid requirement
# ---------------------------------------------------------------------------


def _validate_grid(index: pd.DatetimeIndex, delta: pd.Timedelta) -> None:
    if not isinstance(index, pd.DatetimeIndex):
        raise DataIntegrityError("price index must be a pandas DatetimeIndex")
    if index.tz is None:
        raise DataIntegrityError("naive timestamps are rejected (§2) — index must be tz-aware UTC")
    if str(index.tz) not in ("UTC", "utc"):
        raise DataIntegrityError(
            f"§2 requires timezone-aware UTC timestamps; index tz is {index.tz!r}, not UTC"
        )
    if index.has_duplicates:
        raise DataIntegrityError("duplicate timestamps in price index (§11.2)")
    if not index.is_monotonic_increasing:
        raise DataIntegrityError("non-monotonic timestamps in price index (§11.2)")
    diffs = index[1:] - index[:-1]
    for pos in range(len(diffs)):
        d = diffs[pos]
        if d != delta:
            raise DataIntegrityError(
                f"irregular grid or grid disagreeing with frequency at pair "
                f"({index[pos]!r}, {index[pos + 1]!r}): expected Δ={delta!r}, got {d!r}"
            )


# ---------------------------------------------------------------------------
# §5.4 Column alignment / target-row resolution
# ---------------------------------------------------------------------------


def _resolve_target_row(
    t_label: pd.Timestamp,
    quantity_prev: np.ndarray,
    target_weights: pd.DataFrame,
    symbols: Sequence[str],
) -> np.ndarray:
    row = target_weights.loc[t_label]
    w_target = np.empty(len(symbols), dtype=float)
    cols = set(target_weights.columns)
    for idx, sym in enumerate(symbols):
        if sym in cols:
            val = float(row[sym])
            if math.isnan(val):
                raise DataIntegrityError(
                    f"NaN target weight for {sym!r} at rebalance bar {t_label!r} (§3 forbids NaN on a rebalance bar)"
                )
            w_target[idx] = val
        else:
            # Defence-in-depth, unreachable: `cols` is `target_weights`'s
            # (static, whole-DataFrame) column set, identical on every call
            # across the simulation loop. A symbol can only ever acquire a
            # nonzero `quantity_prev` via a PRIOR rebalance that read
            # `row[sym]` in the `if sym in cols` branch above -- which
            # requires `sym in cols` to have already been True. Since `cols`
            # never changes between calls, `sym in cols` cannot flip to
            # False afterwards, so `quantity_prev[idx] != 0.0` can never be
            # observed here. Kept as an explicit raise rather than removed,
            # as a defensive guard against a future caller passing a
            # per-call-varying `target_weights` frame.
            if quantity_prev[idx] != 0.0:
                raise DataIntegrityError(
                    f"symbol {sym!r} has nonzero quantity but is absent from target_weights[{t_label!r}] (§5.4)"
                )
            w_target[idx] = 0.0
    return w_target


# ---------------------------------------------------------------------------
# Per-period step result (§6.0 Steps 1-12)
# ---------------------------------------------------------------------------


@dataclass
class PeriodResult:
    rebalance: bool
    symbol_state: np.ndarray  # dtype=object, strings
    trade: np.ndarray
    turnover: float
    fee_cost: float
    slippage_cost: float
    fee_basis_notional: float
    nav_after_cost: float
    ruin_stage: Optional[str]  # None, "cost", "pnl"
    quantity: np.ndarray  # reported quantity (see §6.7.1 substitution at cost-ruin)
    pre_trade_weights: np.ndarray  # w_pre
    positions: np.ndarray  # w_held (reported, incl. §6.7.2 substitution)
    notional: np.ndarray
    gross_exposure: float
    net_exposure: float
    gross_leverage: float
    asset_pnl_cash: float
    funding_pnl_cash: float
    nav_end: float  # floored to 0.0 at ruin
    net_return: float  # clipped to -1.0 at ruin
    gross_return: float
    fee_return: float
    slippage_return: float
    funding_return: float
    uncapped_ruin_return: Optional[float]
    ruin_decomposition_residual: Optional[float]


def _step_period(
    *,
    i: int,
    rebalance: bool,
    w_target: Optional[np.ndarray],
    quantity_prev: np.ndarray,
    P_i: np.ndarray,
    P_ip1: np.ndarray,
    NAV_pre: float,
    fee_bps: float,
    slippage_bps: float,
    symbols: Sequence[str],
    timestamp_i: pd.Timestamp,
    timestamp_ip1: pd.Timestamp,
    funding_fn: Callable[[np.ndarray, np.ndarray], float] = lambda quantity_i, active_mask: 0.0,
) -> PeriodResult:
    """Executes §6.0 Steps 1-12 for a single period `i`.

    LOWER-LEVEL HELPER — usable directly by tests that need to exercise the
    accounting sequence at same-instant ("lag=0-like") granularity without
    constructing a production BacktestConfig with execution_lag < 1 (§18.0.2).
    This is also the sole accounting kernel used internally by `_simulate`,
    so there is exactly one implementation of Steps 1-12 in the codebase.
    """
    n_symbols = len(symbols)

    # -- Step 0 (classification + price validation for P_i) --------------
    q_prev_zero = quantity_prev == 0.0
    if rebalance:
        if w_target is None:
            raise ValueError("rebalance=True requires w_target")
        will_hold = w_target != 0.0
    else:
        will_hold = ~q_prev_zero

    symbol_state = np.empty(n_symbols, dtype=object)
    for j in range(n_symbols):
        symbol_state[j] = classify_symbol_state(quantity_prev[j], bool(will_hold[j]))

    needs_p_i = np.array(
        [symbol_state[j] in (_STATE_ENTERING, _STATE_HELD, _STATE_EXITING) for j in range(n_symbols)]
    )
    for j in range(n_symbols):
        if needs_p_i[j]:
            _validate_price(float(P_i[j]), symbols[j], timestamp_i, use="execution/valuation at period start")

    # -- Step 1: trade -----------------------------------------------------
    w_pre = np.zeros(n_symbols, dtype=float)
    q_prev_nz = ~q_prev_zero
    if np.any(q_prev_nz):
        w_pre[q_prev_nz] = quantity_prev[q_prev_nz] * P_i[q_prev_nz] / NAV_pre

    if rebalance:
        trade = compute_trade(quantity_prev, w_target, w_pre)
        turnover = compute_turnover(trade)
    else:
        trade = np.zeros(n_symbols, dtype=float)
        turnover = 0.0

    # -- Step 2: costs -------------------------------------------------------
    fee_cost_i = _fee_cost_fn(turnover, NAV_pre, fee_bps)
    slippage_cost_i = _slippage_cost_fn(turnover, NAV_pre, slippage_bps)
    fee_basis_notional_i = _fee_basis_notional_fn(turnover, NAV_pre)
    NAV_after_cost = NAV_pre - fee_cost_i - slippage_cost_i

    # -- Step 3: FINITENESS GUARD (before any ruin classification, W-B) ----
    if not math.isfinite(NAV_after_cost):
        raise AccountingError(
            f"non-finite NAV_after_cost at period {i}: {NAV_after_cost!r} (§6.0 Step 3)"
        )

    # -- Step 4: cost-stage ruin test ---------------------------------------
    fee_return = -fee_cost_i / NAV_pre
    slippage_return = -slippage_cost_i / NAV_pre

    if NAV_after_cost <= 0.0:
        # COST-STAGE RUIN. Steps 5-12 are NOT executed. §6.7.2 terminal row.
        quantity_reported = quantity_prev  # §6.7.1 — pre-trade, Step 6 never ran
        positions_reported = w_pre  # §6.7.2 table
        notional_reported = np.zeros(n_symbols, dtype=float)
        nz = quantity_reported != 0.0
        if np.any(nz):
            notional_reported[nz] = quantity_reported[nz] * P_i[nz]
        gross_exposure = float(np.sum(np.abs(w_pre)))
        net_exposure = float(np.sum(w_pre))
        uncapped_ruin_return = NAV_after_cost / NAV_pre - 1.0
        ruin_decomposition_residual = uncapped_ruin_return - (-1.0)
        return PeriodResult(
            rebalance=rebalance,
            symbol_state=symbol_state,
            trade=trade,
            turnover=turnover,
            fee_cost=fee_cost_i,
            slippage_cost=slippage_cost_i,
            fee_basis_notional=fee_basis_notional_i,
            nav_after_cost=NAV_after_cost,
            ruin_stage="cost",
            quantity=quantity_reported,
            pre_trade_weights=w_pre,
            positions=positions_reported,
            notional=notional_reported,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            gross_leverage=gross_exposure,
            asset_pnl_cash=float("nan"),
            funding_pnl_cash=float("nan"),
            nav_end=0.0,
            net_return=-1.0,
            gross_return=float("nan"),
            fee_return=fee_return,
            slippage_return=slippage_return,
            funding_return=float("nan"),
            uncapped_ruin_return=uncapped_ruin_return,
            ruin_decomposition_residual=ruin_decomposition_residual,
        )

    # -- Step 5: validate P[i+1] for ENTERING and HELD ----------------------
    needs_p_ip1 = np.array(
        [symbol_state[j] in (_STATE_ENTERING, _STATE_HELD) for j in range(n_symbols)]
    )
    for j in range(n_symbols):
        if needs_p_ip1[j]:
            _validate_price(float(P_ip1[j]), symbols[j], timestamp_ip1, use="next-period valuation")

    # -- Step 6: quantity ledger (§5.1) -------------------------------------
    # NOTE: an admitted-denormal price (§5.5) can make this division overflow
    # to +-inf by construction (§6.0's own worked example, X11/V3). That is
    # an expected, correctly-handled event: Step 10's finiteness guard raises
    # AccountingError before any ruin classification. The warning is
    # suppressed here only to keep the (expected) overflow silent; the value
    # itself is neither discarded nor treated as zero.
    if rebalance:
        quantity_i = np.zeros(n_symbols, dtype=float)
        w_nz = w_target != 0.0
        if np.any(w_nz):
            with np.errstate(over="ignore", invalid="ignore"):
                quantity_i[w_nz] = w_target[w_nz] * NAV_after_cost / P_i[w_nz]
    else:
        quantity_i = quantity_prev  # exact carry-forward, same stored values

    active_mask = quantity_i != 0.0

    # -- Step 7: asset PnL ---------------------------------------------------
    if np.any(active_mask):
        with np.errstate(over="ignore", invalid="ignore"):
            asset_pnl_cash = float(
                np.sum(quantity_i[active_mask] * (P_ip1[active_mask] - P_i[active_mask]))
            )
    else:
        asset_pnl_cash = 0.0

    # -- Step 8: funding PnL (§7.5), masked to active ------------------------
    funding_pnl_cash = float(funding_fn(quantity_i, active_mask))

    # -- Step 9: ending NAV (authoritative ledger value) ---------------------
    NAV_end = NAV_after_cost + asset_pnl_cash + funding_pnl_cash

    # -- Step 10: FINITENESS GUARD (before any ruin classification, W-B) ----
    if not math.isfinite(NAV_end):
        raise AccountingError(f"non-finite NAV_end at period {i}: {NAV_end!r} (§6.0 Step 10)")

    # w_held / notional / exposures — always from quantity_i and P_i (§5.2, §5.6)
    w_held = np.zeros(n_symbols, dtype=float)
    if np.any(active_mask):
        w_held[active_mask] = quantity_i[active_mask] * P_i[active_mask] / NAV_after_cost
    notional_i = np.zeros(n_symbols, dtype=float)
    if np.any(active_mask):
        notional_i[active_mask] = quantity_i[active_mask] * P_i[active_mask]
    gross_exposure = float(np.sum(np.abs(w_held)))
    net_exposure = float(np.sum(w_held))

    gross_return = asset_pnl_cash / NAV_pre
    funding_return = funding_pnl_cash / NAV_pre

    # -- Step 11: pnl-stage ruin test ----------------------------------------
    if NAV_end <= 0.0:
        uncapped_ruin_return = NAV_end / NAV_pre - 1.0
        ruin_decomposition_residual = uncapped_ruin_return - (-1.0)
        return PeriodResult(
            rebalance=rebalance,
            symbol_state=symbol_state,
            trade=trade,
            turnover=turnover,
            fee_cost=fee_cost_i,
            slippage_cost=slippage_cost_i,
            fee_basis_notional=fee_basis_notional_i,
            nav_after_cost=NAV_after_cost,
            ruin_stage="pnl",
            quantity=quantity_i,
            pre_trade_weights=w_pre,
            positions=w_held,
            notional=notional_i,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            gross_leverage=gross_exposure,
            asset_pnl_cash=asset_pnl_cash,
            funding_pnl_cash=funding_pnl_cash,
            nav_end=0.0,
            net_return=-1.0,
            gross_return=gross_return,
            fee_return=fee_return,
            slippage_return=slippage_return,
            funding_return=funding_return,
            uncapped_ruin_return=uncapped_ruin_return,
            ruin_decomposition_residual=ruin_decomposition_residual,
        )

    # -- Step 12: derive net_return, carry the ledger ------------------------
    net_return = NAV_end / NAV_pre - 1.0

    return PeriodResult(
        rebalance=rebalance,
        symbol_state=symbol_state,
        trade=trade,
        turnover=turnover,
        fee_cost=fee_cost_i,
        slippage_cost=slippage_cost_i,
        fee_basis_notional=fee_basis_notional_i,
        nav_after_cost=NAV_after_cost,
        ruin_stage=None,
        quantity=quantity_i,
        pre_trade_weights=w_pre,
        positions=w_held,
        notional=notional_i,
        gross_exposure=gross_exposure,
        net_exposure=net_exposure,
        gross_leverage=gross_exposure,
        asset_pnl_cash=asset_pnl_cash,
        funding_pnl_cash=funding_pnl_cash,
        nav_end=NAV_end,
        net_return=net_return,
        gross_return=gross_return,
        fee_return=fee_return,
        slippage_return=slippage_return,
        funding_return=funding_return,
        uncapped_ruin_return=None,
        ruin_decomposition_residual=None,
    )


# ---------------------------------------------------------------------------
# §7 Funding engine
# ---------------------------------------------------------------------------


class _FundingEngine:
    """Encapsulates §7: events, coverage, incremental validation (§7.7.2)."""

    def __init__(
        self,
        symbols: Sequence[str],
        funding_events: Sequence[FundingEvent],
        funding_coverage: Sequence[FundingCoverage],
        basis: str,
    ) -> None:
        self.basis = basis
        self.events_by_symbol: dict[str, list[FundingEvent]] = {s: [] for s in symbols}
        for e in funding_events:
            if e.symbol not in self.events_by_symbol:
                # Event for a symbol outside the traded universe: ignore for
                # accounting purposes (it can never be consumed) but do not
                # silently disappear coverage bookkeeping for traded symbols.
                continue
            self.events_by_symbol[e.symbol].append(e)
        for s in symbols:
            self.events_by_symbol[s].sort(key=lambda e: e.timestamp)

        # §7.2 — validate declared coverage metadata: pairwise disjoint,
        # non-touching closures per symbol. Static structural validation,
        # independent of which periods the simulation reaches.
        self.coverage_by_symbol: dict[str, list[FundingCoverage]] = {s: [] for s in symbols}
        for c in funding_coverage:
            if c.symbol in self.coverage_by_symbol:
                self.coverage_by_symbol[c.symbol].append(c)
        for s in symbols:
            records = sorted(self.coverage_by_symbol[s], key=lambda c: c.coverage_start)
            for a, b in zip(records, records[1:]):
                if b.coverage_start <= a.coverage_end:
                    raise DataIntegrityError(
                        f"FundingCoverage records for {s!r} touch or overlap: "
                        f"[{a.coverage_start!r}, {a.coverage_end!r}] and "
                        f"[{b.coverage_start!r}, {b.coverage_end!r}] (§7.2)"
                    )
            self.coverage_by_symbol[s] = records

        self._validated_records: set[int] = set()
        self.gap_tolerance_suspicious = False

    def excluded_count(self, T0: pd.Timestamp, T_last: pd.Timestamp) -> int:
        """§7.5 — events before T_0 or at/after T_{n-1}."""
        count = 0
        for events in self.events_by_symbol.values():
            for e in events:
                if e.timestamp < T0 or e.timestamp >= T_last:
                    count += 1
        return count

    def _find_covering_record(self, symbol: str, T_i, T_ip1) -> FundingCoverage:
        for record in self.coverage_by_symbol.get(symbol, []):
            if record.coverage_start <= T_i and T_ip1 <= record.coverage_end:
                return record
        raise FundingDataError(
            f"no single FundingCoverage record for {symbol!r} covers [{T_i!r}, {T_ip1!r}) (§7.7.2 condition 2)"
        )

    def _validate_record(self, symbol: str, record: FundingCoverage) -> None:
        key = id(record)
        if key in self._validated_records:
            return
        events = [
            e
            for e in self.events_by_symbol.get(symbol, [])
            if record.coverage_start <= e.timestamp <= record.coverage_end
        ]
        boundary = [record.coverage_start] + sorted(e.timestamp for e in events) + [record.coverage_end]
        gaps = [boundary[k + 1] - boundary[k] for k in range(len(boundary) - 1)]
        for gap in gaps:
            if gap > record.max_funding_gap:
                raise FundingDataError(
                    f"funding coverage gap {gap!r} exceeds max_funding_gap={record.max_funding_gap!r} "
                    f"for {symbol!r} in [{record.coverage_start!r}, {record.coverage_end!r}] (§7.7.2 condition 3)"
                )
        # Soft check (non-fatal): modal event spacing more than 2x below max_funding_gap.
        event_gaps = [boundary[k + 1] - boundary[k] for k in range(1, len(boundary) - 2)]
        if len(event_gaps) >= 1:
            counts: dict[pd.Timedelta, int] = {}
            for g in event_gaps:
                counts[g] = counts.get(g, 0) + 1
            modal_gap = max(counts.items(), key=lambda kv: kv[1])[0]
            if modal_gap < record.max_funding_gap / 2:
                self.gap_tolerance_suspicious = True
        self._validated_records.add(key)

    def period_funding(
        self,
        symbols: Sequence[str],
        quantity_i: np.ndarray,
        active_mask: np.ndarray,
        P_i: np.ndarray,
        T_i: pd.Timestamp,
        T_ip1: pd.Timestamp,
        funding_mode: str,
    ) -> float:
        """§6.0 Step 8 / §7.7.1 — funding for a single funding-accruing period.

        Only called for periods that reached Step 8 (never for a cost-stage
        ruin, per §7.7.1 condition 1 — the caller never invokes this for a
        cost-stage-ruined period since Step 8 was skipped entirely).
        """
        if funding_mode == "disabled":
            # Defence-in-depth only: unreachable in production. The caller
            # (`_simulate`) never constructs a `_FundingEngine` at all when
            # `funding_mode == "disabled"` (see `funding_engine = ... if
            # funding_modelled else None` in `run_backtest`), so this method
            # is never invoked under that mode. Kept as an explicit early
            # return rather than removed, in case a future caller invokes
            # `period_funding` directly.
            return 0.0

        total = 0.0
        for idx, symbol in enumerate(symbols):
            if not active_mask[idx]:
                continue
            # §7.7.1 condition 2: quantity[i, j] != 0 as sized at Step 6 — satisfied by active_mask.
            record = self._find_covering_record(symbol, T_i, T_ip1)
            self._validate_record(symbol, record)

            q = quantity_i[idx]
            events = self.events_by_symbol.get(symbol, [])
            lo = bisect.bisect_left([e.timestamp for e in events], T_i)
            for e in events[lo:]:
                if e.timestamp >= T_ip1:
                    break
                if self.basis == "event_price":
                    if e.notional_price is None or not math.isfinite(e.notional_price) or e.notional_price <= 0.0:
                        raise FundingDataError(
                            f"invalid/missing notional_price for {symbol!r} event at {e.timestamp!r} "
                            "under funding_notional_basis='event_price' (§7.6)"
                        )
                    notional_e = q * e.notional_price
                else:  # "period_start" — notional_price ignored entirely (§7.6)
                    notional_e = q * P_i[idx]
                total += -notional_e * e.funding_rate
        return total


# ---------------------------------------------------------------------------
# Full-path simulation (§6.0 loop). Used for BOTH the actual path and the
# counterfactual path (§9.1) — one implementation, different cost/funding
# parameters, so the two paths cannot diverge due to duplicated logic.
# ---------------------------------------------------------------------------


@dataclass
class _PathResult:
    n_periods: int
    equity_curve: np.ndarray  # length n_periods + 1
    equity_timestamps: list
    net_return: np.ndarray
    gross_return: np.ndarray
    fee_return: np.ndarray
    slippage_return: np.ndarray
    funding_return: np.ndarray
    fee_cost: np.ndarray
    slippage_cost: np.ndarray
    funding_pnl_cash: np.ndarray
    asset_pnl_cash: np.ndarray
    fee_basis_notional: np.ndarray
    turnover: np.ndarray
    gross_exposure: np.ndarray
    net_exposure: np.ndarray
    gross_leverage: np.ndarray
    rebalance_flag: np.ndarray
    quantity: np.ndarray  # (n_periods, n_symbols)
    notional: np.ndarray
    positions: np.ndarray
    pre_trade_weights: np.ndarray
    target_weights_used: np.ndarray  # NaN on non-rebalance rows
    trades: np.ndarray
    symbol_state: np.ndarray  # dtype object
    period_timestamps: list
    ruined: bool
    ruin_timestamp: Optional[pd.Timestamp]
    ruin_stage: Optional[str]
    uncapped_ruin_return: Optional[float]
    ruin_decomposition_residual: Optional[float]
    leverage_breach: bool
    leverage_breach_timestamps: list


def _simulate(
    *,
    n: int,
    symbols: Sequence[str],
    prices_matrix: np.ndarray,  # (n, n_symbols)
    prices_index: pd.DatetimeIndex,
    T: list,  # execution instants, length n
    target_weights: pd.DataFrame,
    rebalance_mask: pd.Series,
    execution_lag: int,
    initial_capital: float,
    fee_bps: float,
    slippage_bps: float,
    funding_engine: Optional[_FundingEngine],
    funding_mode: str,
    max_gross_leverage: Optional[float],
) -> _PathResult:
    n_symbols = len(symbols)
    n_periods_max = n - 1

    quantity_prev = np.zeros(n_symbols, dtype=float)
    NAV_pre = initial_capital

    equity = [initial_capital]
    equity_ts = [T[0]]

    net_return_l: list = []
    gross_return_l: list = []
    fee_return_l: list = []
    slippage_return_l: list = []
    funding_return_l: list = []
    fee_cost_l: list = []
    slippage_cost_l: list = []
    funding_pnl_l: list = []
    asset_pnl_l: list = []
    fee_basis_l: list = []
    turnover_l: list = []
    gross_exp_l: list = []
    net_exp_l: list = []
    gross_lev_l: list = []
    rebalance_flag_l: list = []
    quantity_rows: list = []
    notional_rows: list = []
    positions_rows: list = []
    pre_trade_rows: list = []
    target_used_rows: list = []
    trades_rows: list = []
    symbol_state_rows: list = []
    period_ts: list = []

    ruined = False
    ruin_timestamp = None
    ruin_stage = None
    uncapped_ruin_return = None
    ruin_decomposition_residual = None
    leverage_breach = False
    leverage_breach_timestamps: list = []

    for i in range(n_periods_max):
        t_pos = i - execution_lag
        rebalance = False
        w_target = None
        if 0 <= t_pos <= n - 1:
            t_label = prices_index[t_pos]
            if bool(rebalance_mask.loc[t_label]):
                rebalance = True
                w_target = _resolve_target_row(t_label, quantity_prev, target_weights, symbols)

        P_i = prices_matrix[i, :]
        P_ip1 = prices_matrix[i + 1, :]

        funding_fn = (lambda q, a: 0.0)
        if funding_engine is not None and funding_mode == "required":
            # Bind BOTH loop-rebound locals (`i` and `P_i`) as default
            # arguments — not just `_i` — so this closure cannot silently
            # capture a later iteration's `P_i` if the call site is ever
            # refactored to defer invocation (it already binds `_i`; `P_i`
            # was previously read from the enclosing scope, which is
            # correct today only because `funding_fn` happens to be called
            # within the same iteration it is defined in).
            def funding_fn(q, a, _i=i, _P_i=P_i):
                return funding_engine.period_funding(
                    symbols, q, a, _P_i, T[_i], T[_i + 1], funding_mode
                )

        result = _step_period(
            i=i,
            rebalance=rebalance,
            w_target=w_target,
            quantity_prev=quantity_prev,
            P_i=P_i,
            P_ip1=P_ip1,
            NAV_pre=NAV_pre,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            symbols=symbols,
            timestamp_i=prices_index[i],
            timestamp_ip1=prices_index[i + 1],
            funding_fn=funding_fn,
        )

        net_return_l.append(result.net_return)
        gross_return_l.append(result.gross_return)
        fee_return_l.append(result.fee_return)
        slippage_return_l.append(result.slippage_return)
        funding_return_l.append(result.funding_return)
        fee_cost_l.append(result.fee_cost)
        slippage_cost_l.append(result.slippage_cost)
        funding_pnl_l.append(result.funding_pnl_cash)
        asset_pnl_l.append(result.asset_pnl_cash)
        fee_basis_l.append(result.fee_basis_notional)
        turnover_l.append(result.turnover)
        gross_exp_l.append(result.gross_exposure)
        net_exp_l.append(result.net_exposure)
        gross_lev_l.append(result.gross_leverage)
        rebalance_flag_l.append(result.rebalance)
        quantity_rows.append(result.quantity)
        notional_rows.append(result.notional)
        positions_rows.append(result.positions)
        pre_trade_rows.append(result.pre_trade_weights)
        if rebalance:
            target_used_rows.append(w_target.copy())
        else:
            target_used_rows.append(np.full(n_symbols, np.nan))
        trades_rows.append(result.trade)
        symbol_state_rows.append(result.symbol_state)
        period_ts.append(T[i])

        if max_gross_leverage is not None and result.gross_exposure > max_gross_leverage:
            leverage_breach = True
            leverage_breach_timestamps.append(T[i])

        equity.append(0.0 if result.ruin_stage is not None else result.nav_end)
        equity_ts.append(T[i + 1])

        if result.ruin_stage is not None:
            ruined = True
            ruin_timestamp = T[i + 1]
            ruin_stage = result.ruin_stage
            uncapped_ruin_return = result.uncapped_ruin_return
            ruin_decomposition_residual = result.ruin_decomposition_residual
            break

        quantity_prev = result.quantity
        NAV_pre = result.nav_end

    n_periods = len(net_return_l)

    return _PathResult(
        n_periods=n_periods,
        equity_curve=np.asarray(equity, dtype=float),
        equity_timestamps=equity_ts,
        net_return=np.asarray(net_return_l, dtype=float),
        gross_return=np.asarray(gross_return_l, dtype=float),
        fee_return=np.asarray(fee_return_l, dtype=float),
        slippage_return=np.asarray(slippage_return_l, dtype=float),
        funding_return=np.asarray(funding_return_l, dtype=float),
        fee_cost=np.asarray(fee_cost_l, dtype=float),
        slippage_cost=np.asarray(slippage_cost_l, dtype=float),
        funding_pnl_cash=np.asarray(funding_pnl_l, dtype=float),
        asset_pnl_cash=np.asarray(asset_pnl_l, dtype=float),
        fee_basis_notional=np.asarray(fee_basis_l, dtype=float),
        turnover=np.asarray(turnover_l, dtype=float),
        gross_exposure=np.asarray(gross_exp_l, dtype=float),
        net_exposure=np.asarray(net_exp_l, dtype=float),
        gross_leverage=np.asarray(gross_lev_l, dtype=float),
        rebalance_flag=np.asarray(rebalance_flag_l, dtype=bool),
        quantity=np.vstack(quantity_rows) if quantity_rows else np.zeros((0, n_symbols)),
        notional=np.vstack(notional_rows) if notional_rows else np.zeros((0, n_symbols)),
        positions=np.vstack(positions_rows) if positions_rows else np.zeros((0, n_symbols)),
        pre_trade_weights=np.vstack(pre_trade_rows) if pre_trade_rows else np.zeros((0, n_symbols)),
        target_weights_used=np.vstack(target_used_rows) if target_used_rows else np.zeros((0, n_symbols)),
        trades=np.vstack(trades_rows) if trades_rows else np.zeros((0, n_symbols)),
        symbol_state=np.vstack(symbol_state_rows) if symbol_state_rows else np.zeros((0, n_symbols), dtype=object),
        period_timestamps=period_ts,
        ruined=ruined,
        ruin_timestamp=ruin_timestamp,
        ruin_stage=ruin_stage,
        uncapped_ruin_return=uncapped_ruin_return,
        ruin_decomposition_residual=ruin_decomposition_residual,
        leverage_breach=leverage_breach,
        leverage_breach_timestamps=leverage_breach_timestamps,
    )


# ---------------------------------------------------------------------------
# §10 / top-level entry point
# ---------------------------------------------------------------------------


def _compute_unexecuted_rebalances(
    rebalance_mask: pd.Series, prices_index: pd.DatetimeIndex, execution_lag: int, n: int
) -> list:
    """§4.4 — a rebalance flagged at t with t + execution_lag > n - 2."""
    out = []
    for t_pos in range(n):
        if bool(rebalance_mask.iloc[t_pos]):
            i = t_pos + execution_lag
            if i > n - 2:
                out.append(prices_index[t_pos])
    return out


def _series_from_path(path: _PathResult) -> dict:
    idx = pd.DatetimeIndex(path.period_timestamps)
    return {
        "net_return": pd.Series(path.net_return, index=idx, name="net_return"),
        "gross_return": pd.Series(path.gross_return, index=idx, name="gross_return"),
        "fee_return": pd.Series(path.fee_return, index=idx, name="fee_return"),
        "slippage_return": pd.Series(path.slippage_return, index=idx, name="slippage_return"),
        "funding_return": pd.Series(path.funding_return, index=idx, name="funding_return"),
        "fee_cost": pd.Series(path.fee_cost, index=idx, name="fee_cost"),
        "slippage_cost": pd.Series(path.slippage_cost, index=idx, name="slippage_cost"),
        "funding_pnl_cash": pd.Series(path.funding_pnl_cash, index=idx, name="funding_pnl_cash"),
        "asset_pnl_cash": pd.Series(path.asset_pnl_cash, index=idx, name="asset_pnl_cash"),
        "fee_basis_notional": pd.Series(path.fee_basis_notional, index=idx, name="fee_basis_notional"),
        "turnover": pd.Series(path.turnover, index=idx, name="turnover"),
        "gross_exposure": pd.Series(path.gross_exposure, index=idx, name="gross_exposure"),
        "net_exposure": pd.Series(path.net_exposure, index=idx, name="net_exposure"),
        "gross_leverage": pd.Series(path.gross_leverage, index=idx, name="gross_leverage"),
        "rebalance_flag": pd.Series(path.rebalance_flag, index=idx, name="rebalance_flag"),
    }


def _frames_from_path(path: _PathResult, symbols: Sequence[str]) -> dict:
    idx = pd.DatetimeIndex(path.period_timestamps)
    return {
        "quantity": pd.DataFrame(path.quantity, index=idx, columns=symbols),
        "notional": pd.DataFrame(path.notional, index=idx, columns=symbols),
        "positions": pd.DataFrame(path.positions, index=idx, columns=symbols),
        "pre_trade_weights": pd.DataFrame(path.pre_trade_weights, index=idx, columns=symbols),
        "resolved_target_weights": pd.DataFrame(path.target_weights_used, index=idx, columns=symbols),
        "trades": pd.DataFrame(path.trades, index=idx, columns=symbols),
        "symbol_state": pd.DataFrame(path.symbol_state, index=idx, columns=symbols),
    }


_COUNTERFACTUAL_BARRIER_EXCEPTIONS = (DataIntegrityError, FundingDataError, AccountingError)


def _reject_duplicate_columns(frame: pd.DataFrame, label: str) -> None:
    """§11.2 / §5.4 — duplicate `(timestamp, symbol)` cells are a data
    integrity violation: a duplicated column silently doubles that symbol's
    contribution to every aggregate (turnover, exposure, PnL) without ever
    reading an invalid price, so no §5.5 check can catch it."""
    if frame.columns.duplicated().any():
        dup = sorted(set(frame.columns[frame.columns.duplicated()]))
        raise DataIntegrityError(
            f"duplicate symbol column(s) {dup!r} in {label} (§11.2 — duplicate (timestamp, symbol))"
        )


def run_backtest(
    config: BacktestConfig,
    market_data: MarketData,
    strategy_output: StrategyOutput,
    funding_events: Optional[Sequence[FundingEvent]] = None,
    funding_coverage: Optional[Sequence[FundingCoverage]] = None,
    dataset_provenance: Optional[Sequence[DatasetProvenance]] = None,
    universe_provenance: Optional[UniverseProvenance] = None,
) -> BacktestResult:
    if not isinstance(config, BacktestConfig):
        raise ConfigError("config must be a BacktestConfig")
    if not isinstance(market_data, MarketData):
        raise ConfigError("market_data must be a MarketData(open=..., close=...) instance (§4.2)")

    delta = config.delta

    open_df = market_data.open
    close_df = market_data.close

    if not isinstance(open_df.index, pd.DatetimeIndex) or not isinstance(close_df.index, pd.DatetimeIndex):
        raise DataIntegrityError("MarketData.open/close index must be a pandas DatetimeIndex")
    if not open_df.index.equals(close_df.index):
        raise DataIntegrityError(
            "MarketData.open and MarketData.close must share an identical index (§4.2)"
        )
    if set(open_df.columns) != set(close_df.columns):
        raise DataIntegrityError(
            "MarketData.open and MarketData.close must share identical columns (§4.2)"
        )
    # §11.2 / BD-1 — duplicate columns in EITHER supplied series are rejected
    # before selection: the unselected series is never read downstream, but
    # a duplicate there is still a data-integrity defect in what was
    # supplied, and checking only the selected series would silently accept
    # a duplicate-column close frame under next_open (or vice versa).
    _reject_duplicate_columns(open_df, "MarketData.open")
    _reject_duplicate_columns(close_df, "MarketData.close")

    shared_index = open_df.index
    n = len(shared_index)
    if n < 2:
        raise ConfigError("n < 2: at least two price bars are required (§4.4)")

    _validate_grid(shared_index, delta)

    # §4.2 — explicit, separately tested selection. Only `prices` (the
    # selected series) is read or validated anywhere below; the unselected
    # series is never consulted again.
    prices = _select_execution_price_frame(market_data, config.execution_mode)

    target_weights_supplied = strategy_output.target_weights
    rebalance_mask = strategy_output.rebalance_mask

    if rebalance_mask is None or target_weights_supplied is None:
        raise DataIntegrityError("StrategyOutput.rebalance_mask/target_weights are REQUIRED (§3)")
    if not isinstance(rebalance_mask.index, pd.DatetimeIndex) or not rebalance_mask.index.equals(shared_index):
        raise DataIntegrityError("rebalance_mask is misaligned with the price index (§11.2)")
    if not isinstance(target_weights_supplied.index, pd.DatetimeIndex) or not target_weights_supplied.index.equals(shared_index):
        raise DataIntegrityError("target_weights is misaligned with the price index (§11.2)")
    if rebalance_mask.dtype != bool:
        # BD-22 — §3 declares `rebalance_mask : Series (index = bar label,
        # dtype = bool)`. A non-bool dtype (e.g. an object-dtype column of
        # truthy/falsy strings, or a float/NaN column) MUST raise rather than
        # be silently coerced: `pd.Series(["yes", "", ""]).astype(bool)`
        # silently produces `[True, False, False]` for reasons that have
        # nothing to do with the mask's intended boolean semantics.
        raise DataIntegrityError(
            f"rebalance_mask must have dtype bool (§3), got {rebalance_mask.dtype!r}"
        )

    # §11.2 / BD-1 — duplicate columns in target_weights (e.g. a strategy
    # that accidentally concatenates the same symbol twice) are rejected
    # explicitly, before _resolve_target_row's `row[sym]` lookup would
    # otherwise return a Series and raise a bare TypeError (forbidden by
    # §11.2's "never bare ValueError"-class requirement).
    _reject_duplicate_columns(target_weights_supplied, "target_weights")

    for sym in target_weights_supplied.columns:
        if sym not in prices.columns:
            raise DataIntegrityError(
                f"symbol {sym!r} present in target_weights is absent from market data (§11.2)"
            )

    symbols = sorted(prices.columns)
    # `target_weights` (internal, used by `_resolve_target_row`) is the
    # supplied frame unchanged — `_resolve_target_row` already handles a
    # symbol absent from a given row (§5.4). `result.target_weights` (§10)
    # is this exact same object, passed through unmodified.
    target_weights = target_weights_supplied
    prices = prices.reindex(columns=symbols)
    prices_matrix = prices.to_numpy(dtype=float)
    prices_index = prices.index

    T = [execution_instant(prices_index[k], config.execution_mode, delta) for k in range(n)]

    unexecuted_rebalances = _compute_unexecuted_rebalances(
        rebalance_mask, prices_index, config.execution_lag, n
    )

    funding_events = list(funding_events) if funding_events is not None else []
    funding_coverage = list(funding_coverage) if funding_coverage is not None else []

    funding_modelled = config.funding_mode == "required"
    funding_notional_basis = config.funding_notional_basis if funding_modelled else "not_modelled"

    funding_engine = _FundingEngine(symbols, funding_events, funding_coverage, funding_notional_basis) \
        if funding_modelled else None

    # ------------------------------------------------------------------
    # Actual path — executes first, to completion, independently (§9.5.1).
    # ------------------------------------------------------------------
    actual = _simulate(
        n=n,
        symbols=symbols,
        prices_matrix=prices_matrix,
        prices_index=prices_index,
        T=T,
        target_weights=target_weights,
        rebalance_mask=rebalance_mask,
        execution_lag=config.execution_lag,
        initial_capital=config.initial_capital,
        fee_bps=config.fee_bps,
        slippage_bps=config.slippage_bps,
        funding_engine=funding_engine,
        funding_mode=config.funding_mode,
        max_gross_leverage=config.max_gross_leverage,
    )

    funding_events_excluded = (
        funding_engine.excluded_count(T[0], T[n - 1]) if funding_engine is not None else 0
    )
    funding_gap_tolerance_suspicious = (
        funding_engine.gap_tolerance_suspicious if funding_engine is not None else False
    )

    actual_series = _series_from_path(actual)
    actual_frames = _frames_from_path(actual, symbols)
    actual_equity = pd.Series(
        actual.equity_curve, index=pd.DatetimeIndex(actual.equity_timestamps), name="equity_curve"
    )

    actual_metrics = compute_metrics(
        actual.net_return,
        actual.equity_curve,
        actual.turnover,
        af=config.annualization_factor,
        risk_free_per_period=config.risk_free_per_period,
        mar_per_period=config.mar_per_period,
    )

    # ------------------------------------------------------------------
    # Counterfactual path (§9) — runs AFTER the actual path, inside a
    # narrow exception barrier (§9.5). Cannot alter the actual result.
    # ------------------------------------------------------------------
    cf_status = "NOT_COMPUTED"
    cf_error: Optional[str] = None
    cf_gross_equity = None
    cf_gross_return = None
    cf_gross_metrics = None
    cf_total_return = None
    cf_cagr = None
    cf_ruined: Optional[bool] = None
    cf_ruin_timestamp = None
    cf_leverage_breach: Optional[bool] = None
    cf_n_periods = None

    if config.compute_counterfactual:
        try:
            cf_funding_engine = None  # §9.1 — funding_pnl_cash = 0 for all i
            cf = _simulate(
                n=n,
                symbols=symbols,
                prices_matrix=prices_matrix,
                prices_index=prices_index,
                T=T,
                target_weights=target_weights,
                rebalance_mask=rebalance_mask,
                execution_lag=config.execution_lag,
                initial_capital=config.initial_capital,
                fee_bps=0.0,
                slippage_bps=0.0,
                funding_engine=cf_funding_engine,
                funding_mode="disabled",
                max_gross_leverage=config.max_gross_leverage,
            )
            cf_gross_equity = pd.Series(
                cf.equity_curve, index=pd.DatetimeIndex(cf.equity_timestamps), name="counterfactual_gross_equity"
            )
            cf_gross_return = pd.Series(
                cf.net_return, index=pd.DatetimeIndex(cf.period_timestamps), name="counterfactual_gross_return"
            )
            cf_gross_metrics = compute_metrics(
                cf.net_return,
                cf.equity_curve,
                cf.turnover,
                af=config.annualization_factor,
                risk_free_per_period=config.risk_free_per_period,
                mar_per_period=config.mar_per_period,
            )
            cf_total_return = cf_gross_metrics["total_return"]
            cf_cagr = cf_gross_metrics["cagr"]
            cf_ruined = cf.ruined
            cf_ruin_timestamp = cf.ruin_timestamp
            cf_leverage_breach = cf.leverage_breach
            cf_n_periods = cf.n_periods
            cf_status = "RUINED" if cf.ruined else "COMPLETED"
        except _COUNTERFACTUAL_BARRIER_EXCEPTIONS as exc:
            cf_status = "FAILED"
            cf_error = f"{type(exc).__name__}: {exc}"
            cf_gross_equity = None
            cf_gross_return = None
            cf_gross_metrics = None
            cf_total_return = None
            cf_cagr = None
            cf_ruined = None
            cf_ruin_timestamp = None
            cf_leverage_breach = None
            cf_n_periods = None

    # §9.2 — drag comparability rule.
    drag_comparable = (
        cf_status == "COMPLETED"
        and not actual.ruined
        and cf_n_periods is not None
        and cf_n_periods == actual.n_periods
    )
    if drag_comparable:
        total_drag_return = cf_total_return - actual_metrics["total_return"]
        cagr_drag = cf_cagr - actual_metrics["cagr"]
    else:
        total_drag_return = None
        cagr_drag = None

    provenance_supplied = dataset_provenance is not None and len(dataset_provenance) > 0
    provenance_complete = provenance_supplied and all(p.is_complete for p in dataset_provenance)
    uses_proxy_data = provenance_supplied and any(p.native_or_proxy == "proxy" for p in dataset_provenance)

    survivorship_safe = universe_provenance.survivorship_safe if universe_provenance is not None else None

    return BacktestResult(
        equity_curve=actual_equity,
        net_return=actual_series["net_return"],
        gross_return=actual_series["gross_return"],
        fee_return=actual_series["fee_return"],
        slippage_return=actual_series["slippage_return"],
        funding_return=actual_series["funding_return"],
        fee_cost=actual_series["fee_cost"],
        slippage_cost=actual_series["slippage_cost"],
        funding_pnl_cash=actual_series["funding_pnl_cash"],
        asset_pnl_cash=actual_series["asset_pnl_cash"],
        fee_basis_notional=actual_series["fee_basis_notional"],
        turnover=actual_series["turnover"],
        gross_exposure=actual_series["gross_exposure"],
        net_exposure=actual_series["net_exposure"],
        _gross_leverage=actual_series["gross_leverage"],
        rebalance_flag=actual_series["rebalance_flag"],
        quantity=actual_frames["quantity"],
        notional=actual_frames["notional"],
        positions=actual_frames["positions"],
        pre_trade_weights=actual_frames["pre_trade_weights"],
        target_weights=target_weights_supplied,  # §10 ruling — (as supplied), unmodified pass-through
        resolved_target_weights=actual_frames["resolved_target_weights"],
        trades=actual_frames["trades"],
        symbol_state=actual_frames["symbol_state"],
        metrics=actual_metrics,
        counterfactual_gross_equity=cf_gross_equity,
        counterfactual_gross_return=cf_gross_return,
        counterfactual_gross_metrics=cf_gross_metrics,
        counterfactual_total_return=cf_total_return,
        counterfactual_cagr=cf_cagr,
        counterfactual_status=cf_status,
        counterfactual_error=cf_error,
        counterfactual_ruined=cf_ruined,
        counterfactual_ruin_timestamp=cf_ruin_timestamp,
        counterfactual_leverage_breach=cf_leverage_breach,
        total_drag_return=total_drag_return,
        cagr_drag=cagr_drag,
        drag_comparable=drag_comparable,
        ruined=actual.ruined,
        ruin_timestamp=actual.ruin_timestamp,
        ruin_stage=actual.ruin_stage,
        terminal_position_convention="pre_ruin_state" if actual.ruined else None,
        uncapped_ruin_return=actual.uncapped_ruin_return,
        ruin_decomposition_residual=actual.ruin_decomposition_residual,
        funding_modelled=funding_modelled,
        funding_notional_basis=funding_notional_basis,
        funding_events_excluded=funding_events_excluded,
        funding_gap_tolerance_suspicious=funding_gap_tolerance_suspicious,
        liquidation_modelled=False,
        leverage_breach=actual.leverage_breach,
        leverage_breach_timestamps=actual.leverage_breach_timestamps,
        unexecuted_rebalances=unexecuted_rebalances,
        provenance=dataset_provenance,
        provenance_supplied=provenance_supplied,
        provenance_complete=provenance_complete,
        uses_proxy_data=uses_proxy_data,
        universe_provenance=universe_provenance,
        survivorship_safe=survivorship_safe,
        config=config,
    )
