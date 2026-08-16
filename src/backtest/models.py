"""Data model for the QR-INFRA-001 backtest contract (v1.5.1, FROZEN).

Contains: exception types (contract §11.2), BacktestConfig (§15),
StrategyOutput (§3), FundingEvent / FundingCoverage (§7), DatasetProvenance /
UniverseProvenance (§13) and BacktestResult (§10).

This module contains no accounting logic. It only defines and validates the
data shapes that the engine consumes and produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# §11.2 Exception types
# ---------------------------------------------------------------------------
# NOTE on InvalidPriceError vs MissingPriceError (resolved ambiguity, see the
# implementation report): §5.5 states that *every* price-validity violation
# (NaN, +-inf, 0.0, negative) "raises InvalidPriceError (a DataIntegrityError
# subclass)". §11.2 separately lists MissingPriceError as a required subclass,
# and §18.9 S2 requires MissingPriceError specifically for a held symbol
# losing a required (NaN / absent) price. These are reconciled, without
# contradiction, by making MissingPriceError a SUBCLASS of InvalidPriceError:
# a "missing" price (NaN) is raised as the more specific MissingPriceError,
# which *is* an InvalidPriceError (satisfying §5.5's general statement and
# `except InvalidPriceError` clauses), while zero/negative/+-inf prices -
# which are present but invalid, not missing - raise InvalidPriceError
# directly (satisfying V1/V2's exact-type expectation).


class DataIntegrityError(Exception):
    """§11.2 — data integrity violation (grid, alignment, missing columns...)."""


class InvalidPriceError(DataIntegrityError):
    """§5.5 — a price in use is not finite and strictly > 0."""


class MissingPriceError(InvalidPriceError):
    """§5.5 / §11.4 — a price in use is absent (NaN) rather than merely invalid."""


class FundingDataError(Exception):
    """§7 — funding coverage or notional-price validation failure."""


class ConfigError(Exception):
    """§11.2 — BacktestConfig / sample-size validation failure."""


class AccountingError(Exception):
    """§6.0 Steps 3 and 10 — non-finite NAV. Never reported as ruin (W-B)."""


# ---------------------------------------------------------------------------
# §15 BacktestConfig
# ---------------------------------------------------------------------------

_ALLOWED_FREQUENCIES = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
_ALLOWED_EXECUTION_MODES = {"next_open", "next_close"}
_ALLOWED_FUNDING_MODES = {"required", "disabled"}
_ALLOWED_FUNDING_BASIS = {"event_price", "period_start"}

# Sentinel used so that omitting a "REQUIRED, no default" field raises the
# contractually-mandated ConfigError (§11.2) rather than a bare TypeError at
# construction time. See the implementation report for the rationale.
_REQUIRED = None


@dataclass(frozen=True, kw_only=True)
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    frequency: Optional[str] = _REQUIRED
    fee_bps: Optional[float] = _REQUIRED
    slippage_bps: Optional[float] = _REQUIRED
    execution_mode: str = "next_open"
    execution_lag: int = 1
    funding_mode: Optional[str] = _REQUIRED
    funding_notional_basis: Optional[str] = None
    annualization_factor: Optional[float] = _REQUIRED
    risk_free_per_period: float = 0.0
    mar_per_period: float = 0.0
    max_gross_leverage: Optional[float] = None
    compute_counterfactual: bool = True

    def __post_init__(self) -> None:
        if self.frequency is None:
            raise ConfigError("BacktestConfig.frequency is REQUIRED (§15)")
        if self.frequency not in _ALLOWED_FREQUENCIES:
            raise ConfigError(
                f"BacktestConfig.frequency must be one of {sorted(_ALLOWED_FREQUENCIES)}, got {self.frequency!r}"
            )
        if self.fee_bps is None:
            raise ConfigError("BacktestConfig.fee_bps is REQUIRED (§15)")
        if self.slippage_bps is None:
            raise ConfigError("BacktestConfig.slippage_bps is REQUIRED (§15)")
        if self.execution_mode not in _ALLOWED_EXECUTION_MODES:
            raise ConfigError(
                f"BacktestConfig.execution_mode must be one of {sorted(_ALLOWED_EXECUTION_MODES)}, "
                f"got {self.execution_mode!r}"
            )
        if self.execution_lag < 1:
            # §4.2 — lag 0 is a lookahead error.
            raise ConfigError("BacktestConfig.execution_lag must be >= 1 (§4.2)")
        if self.funding_mode is None:
            raise ConfigError("BacktestConfig.funding_mode is REQUIRED (§15)")
        if self.funding_mode not in _ALLOWED_FUNDING_MODES:
            raise ConfigError(
                f"BacktestConfig.funding_mode must be one of {sorted(_ALLOWED_FUNDING_MODES)}, "
                f"got {self.funding_mode!r}"
            )
        if self.funding_mode == "required":
            if self.funding_notional_basis is None:
                raise ConfigError(
                    "BacktestConfig.funding_notional_basis is REQUIRED when funding_mode == 'required' (§15)"
                )
            if self.funding_notional_basis not in _ALLOWED_FUNDING_BASIS:
                raise ConfigError(
                    f"BacktestConfig.funding_notional_basis must be one of {sorted(_ALLOWED_FUNDING_BASIS)}, "
                    f"got {self.funding_notional_basis!r}"
                )
        if self.annualization_factor is None:
            raise ConfigError("BacktestConfig.annualization_factor is REQUIRED (§15)")

    @property
    def delta(self) -> pd.Timedelta:
        """Δ implied by `frequency` (§2.1)."""
        return _ALLOWED_FREQUENCIES[self.frequency]


# ---------------------------------------------------------------------------
# §4.2 MarketData — explicit open/close execution-price series
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketData:
    """§4.2 — the engine consumes BOTH an open and a close price frame.

        P = open   if execution_mode == "next_open"
        P = close  if execution_mode == "next_close"

    `open` and `close` MUST share an identical index and identical columns
    (validated by the engine; see `_select_execution_price_frame` and
    `run_backtest`). Only the series selected by `config.execution_mode` is
    ever read or validated (§5.5); the other series is never consulted. This
    is a data shape only — no accounting logic lives here.
    """

    open: pd.DataFrame
    close: pd.DataFrame


# ---------------------------------------------------------------------------
# §3 StrategyOutput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyOutput:
    target_weights: pd.DataFrame
    rebalance_mask: pd.Series

    def __post_init__(self) -> None:
        if self.rebalance_mask is None:
            raise DataIntegrityError("StrategyOutput.rebalance_mask is REQUIRED (§3), no default permitted")

    # -- Named constructors (§3), no bare default -------------------------

    @staticmethod
    def rebalance_every_bar(weights: pd.DataFrame) -> "StrategyOutput":
        mask = pd.Series(True, index=weights.index, dtype=bool)
        return StrategyOutput(target_weights=weights, rebalance_mask=mask)

    @staticmethod
    def rebalance_on_dates(
        weights: pd.DataFrame,
        dates: Sequence[Any],
        exit_unnamed: bool = False,
    ) -> "StrategyOutput":
        dates_set = set(dates)
        mask = pd.Series(weights.index.isin(dates_set), index=weights.index, dtype=bool)

        if not exit_unnamed:
            return StrategyOutput(target_weights=weights, rebalance_mask=mask)

        # §3 — exit_unnamed=True materialises explicit 0.0 targets for held
        # symbols the strategy did not name, in the strategy's own output
        # frame, before it reaches the engine.
        patched = weights.copy()
        held: set = set()
        for ts in weights.index:
            if ts not in dates_set:
                continue
            row = patched.loc[ts]
            for sym in held:
                val = row.get(sym, np.nan)
                if pd.isna(val):
                    patched.loc[ts, sym] = 0.0
            row = patched.loc[ts]
            held = {sym for sym in patched.columns if pd.notna(row.get(sym, np.nan)) and row.get(sym, 0.0) != 0.0}
        return StrategyOutput(target_weights=patched, rebalance_mask=mask)

    @staticmethod
    def rebalance_on_change(weights: pd.DataFrame) -> "StrategyOutput":
        # Opt-in, never automatic (§3): the caller explicitly chose this
        # constructor to infer a mask from consecutive weight changes.
        mask_values = []
        prev = None
        for ts in weights.index:
            row = weights.loc[ts]
            if prev is None or not row.equals(prev):
                mask_values.append(True)
            else:
                mask_values.append(False)
            prev = row
        mask = pd.Series(mask_values, index=weights.index, dtype=bool)
        return StrategyOutput(target_weights=weights, rebalance_mask=mask)


# ---------------------------------------------------------------------------
# §7 Funding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FundingEvent:
    timestamp: pd.Timestamp
    symbol: str
    funding_rate: float
    notional_price: Optional[float] = None


@dataclass(frozen=True)
class FundingCoverage:
    symbol: str
    coverage_start: pd.Timestamp
    coverage_end: pd.Timestamp
    max_funding_gap: pd.Timedelta
    source_venue: str

    def __post_init__(self) -> None:
        if self.max_funding_gap <= pd.Timedelta(0):
            raise DataIntegrityError(
                f"FundingCoverage.max_funding_gap must be > 0 (§7.2), got {self.max_funding_gap!r}"
            )
        if self.coverage_end < self.coverage_start:
            raise DataIntegrityError("FundingCoverage.coverage_end must be >= coverage_start")


# ---------------------------------------------------------------------------
# §13 Provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetProvenance:
    source_venue: Optional[str] = None
    field_type: Optional[str] = None
    time_range: Optional[tuple] = None
    native_or_proxy: Optional[str] = None
    proxy_for: Optional[str] = None
    dataset_id: Optional[str] = None
    dataset_version: Optional[str] = None
    processing_version: Optional[str] = None
    retrieval_date: Optional[date] = None
    symbol_mapping: Optional[str] = None
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if self.native_or_proxy == "proxy" and not self.proxy_for:
            raise DataIntegrityError(
                "DatasetProvenance with native_or_proxy == 'proxy' MUST specify a non-empty proxy_for (§13.1.4)"
            )

    @property
    def is_complete(self) -> bool:
        return (
            self.source_venue is not None
            and self.field_type is not None
            and self.time_range is not None
            and self.native_or_proxy is not None
        )


@dataclass(frozen=True)
class UniverseProvenance:
    universe_source: Optional[str] = None
    universe_asof_policy: Optional[str] = None
    listing_data_source: Optional[str] = None
    survivorship_safe: Optional[bool] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# §10 Result surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class BacktestResult:
    # S-2 — `eq=False` (rather than the dataclass default `eq=True`): a
    # generated `__eq__` on a dataclass with `pd.Series`/`pd.DataFrame`
    # fields calls `==` on those fields, which returns an element-wise
    # Series/DataFrame, not a bool -- `res1 == res2` would then raise
    # `ValueError: The truth value of a Series is ambiguous` instead of
    # ever returning a boolean. §16 requires two runs to "compare exactly
    # equal", which must be EVALUABLE; falling back to `object.__eq__`
    # (identity) at least returns a real bool rather than raising. This
    # does NOT weaken §16 determinism checking: P/P2 (test_core_accounting.py)
    # already compare every field element-wise explicitly and MUST keep
    # doing so -- a custom `__eq__` is deliberately NOT added here, since a
    # hand-rolled dataclass equality covering every field would itself
    # become a second, weaker determinism check no one audits.
    # Equity ledger (§8)
    equity_curve: pd.Series

    # Per-period series (§10), n_periods rows
    net_return: pd.Series
    gross_return: pd.Series
    fee_return: pd.Series
    slippage_return: pd.Series
    funding_return: pd.Series
    fee_cost: pd.Series
    slippage_cost: pd.Series
    funding_pnl_cash: pd.Series
    asset_pnl_cash: pd.Series
    fee_basis_notional: pd.Series
    turnover: pd.Series
    gross_exposure: pd.Series
    net_exposure: pd.Series
    # §6.5 — `gross_leverage` is an explicit alias of `gross_exposure`,
    # carrying a mandated docstring. A bare string expression after a
    # dataclass field (the old `"""..."""` line here) is NOT exposed by
    # Python at runtime — `BacktestResult.gross_leverage.__doc__` would be
    # `None`, so a test asserting it could only ever grep the source text,
    # never the runtime object. `_gross_leverage` is the actual stored
    # field; `gross_leverage` below is a `property` whose OWN `__doc__` IS
    # the mandated text, introspectable via
    # `BacktestResult.gross_leverage.__doc__` (see test_BD5).
    _gross_leverage: pd.Series
    rebalance_flag: pd.Series

    # Per-period frames (period x symbol)
    quantity: pd.DataFrame
    notional: pd.DataFrame
    positions: pd.DataFrame  # w_held
    pre_trade_weights: pd.DataFrame  # w_pre

    # §10 ruling: the explicit per-field qualifier "(as supplied)" governs
    # over the group header's "n_periods rows" — `target_weights` is the
    # strategy's supplied frame, passed through UNMODIFIED (bar-indexed,
    # values exactly as given, NaNs preserved, no 0.0 filling). The
    # execution-indexed, engine-resolved frame (NaN on non-rebalance rows,
    # the raw per-symbol target actually used at each executed rebalance) is
    # exposed separately, under its own name, so neither reading is lost.
    target_weights: pd.DataFrame  # (as supplied) — bar-indexed, unmodified pass-through
    resolved_target_weights: pd.DataFrame  # execution-indexed, NaN on non-rebalance rows

    trades: pd.DataFrame
    symbol_state: pd.DataFrame

    # Metrics
    metrics: dict

    # Counterfactual
    counterfactual_gross_equity: Optional[pd.Series]
    counterfactual_gross_return: Optional[pd.Series]
    counterfactual_gross_metrics: Optional[dict]
    counterfactual_total_return: Optional[float]
    counterfactual_cagr: Optional[float]
    counterfactual_status: str
    counterfactual_error: Optional[str]
    counterfactual_ruined: Optional[bool]
    counterfactual_ruin_timestamp: Optional[pd.Timestamp]
    counterfactual_leverage_breach: Optional[bool]
    total_drag_return: Optional[float]
    cagr_drag: Optional[float]
    drag_comparable: bool

    # Status and provenance
    ruined: bool
    ruin_timestamp: Optional[pd.Timestamp]
    ruin_stage: Optional[str]
    terminal_position_convention: Optional[str]
    uncapped_ruin_return: Optional[float]
    ruin_decomposition_residual: Optional[float]
    funding_modelled: bool
    funding_notional_basis: str
    funding_events_excluded: int
    funding_gap_tolerance_suspicious: bool
    liquidation_modelled: bool
    leverage_breach: bool
    leverage_breach_timestamps: list
    unexecuted_rebalances: list
    provenance: Any
    provenance_supplied: bool
    provenance_complete: bool
    uses_proxy_data: bool
    universe_provenance: Optional[UniverseProvenance]
    survivorship_safe: Optional[bool]
    config: BacktestConfig

    @property
    def gross_leverage(self) -> pd.Series:
        """notional/NAV; NOT a margin ratio; see §14 — liquidation is not modelled."""
        return self._gross_leverage

    def __repr__(self) -> str:
        # S-1 — the field list is joined WITHOUT the "BacktestResult(" /
        # ")" wrapper tokens participating in the `", ".join(...)`, so no
        # stray leading/trailing comma can appear regardless of how many
        # optional fields (e.g. `counterfactual_status`) are appended.
        fields = [
            f"ruined={self.ruined}",
            f"liquidation_modelled={self.liquidation_modelled}",
            f"uses_proxy_data={self.uses_proxy_data}",
            f"survivorship_safe={self.survivorship_safe}",
        ]
        if self.counterfactual_status != "COMPLETED":
            fields.append(f"counterfactual_status={self.counterfactual_status!r}")
        return "BacktestResult(" + ", ".join(fields) + ")"
