"""§18.11 coverage gaps (CG-1..CG-5) and BD-1/BD-3 defect-repair tests.

These target mutations that were proven, by direct source mutation, to
leave the pre-existing 122/122 suite fully green (see the round-2 defect
repair work order). Each test here was verified to go RED under the exact
mutation it targets (mutation-proof recorded in the implementation report).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import (
    BacktestConfig,
    ConfigError,
    DataIntegrityError,
    FundingCoverage,
    FundingDataError,
    FundingEvent,
    MarketData,
    StrategyOutput,
)

from helpers import dates, mask_series, md, single_symbol_frame


def _cfg(**overrides):
    kwargs = dict(
        initial_capital=1_000_000,
        frequency="1d",
        execution_mode="next_open",
        execution_lag=1,
        fee_bps=0,
        slippage_bps=0,
        funding_mode="disabled",
        compute_counterfactual=False,
        annualization_factor=365,
    )
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


# ---------------------------------------------------------------------------
# BD-1 — duplicate symbol columns
# ---------------------------------------------------------------------------


def test_BD1_duplicate_price_columns_raise():
    idx = dates(3)
    prices = pd.DataFrame([[100.0, 100.0], [100.0, 100.0], [100.0, 100.0]], index=idx, columns=["A", "A"])
    weights = pd.DataFrame([[1.0], [np.nan], [np.nan]], index=idx, columns=["A"])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError, match="A"):
        run_backtest(_cfg(), MarketData(open=prices, close=prices), so)


def test_BD1_duplicate_target_weight_columns_raise():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = pd.DataFrame([[1.0, 1.0], [np.nan, np.nan], [np.nan, np.nan]], index=idx, columns=["A", "A"])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError, match="A"):
        run_backtest(_cfg(), md(prices), so)


def test_BD1_no_regression_gross_exposure_and_total_return_single_column():
    """Reproduces the auditor's proof fixture: BEFORE the fix, duplicate
    columns silently doubled a symbol's contribution (`gross_exposure ==
    2.0`, `total_return == 0.20` for a single 1.0 target). Confirms it now
    raises instead of silently computing wrong PnL."""
    idx = dates(3)
    prices = pd.DataFrame([[100.0, 100.0], [100.0, 100.0], [120.0, 120.0]], index=idx, columns=["A", "A"])
    weights = pd.DataFrame([[1.0], [np.nan], [np.nan]], index=idx, columns=["A"])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_cfg(), MarketData(open=prices, close=prices), so)


# ---------------------------------------------------------------------------
# BD-5 — §6.5's mandated `gross_leverage` docstring
# ---------------------------------------------------------------------------


def test_BD5_gross_leverage_docstring_present():
    """§6.5 mandates the exact docstring text on `gross_leverage`.

    `gross_leverage` is implemented as a `property` specifically so this
    text is introspectable on the RUNTIME object
    (`BacktestResult.gross_leverage.__doc__`), not merely present somewhere
    in the source text. The source-grep is retained as a redundant,
    belt-and-braces check.
    """
    import inspect

    from backtest import models as models_mod
    from backtest.models import BacktestResult

    expected = "notional/NAV; NOT a margin ratio; see §14 — liquidation is not modelled."

    # Runtime introspection — the property's own docstring.
    assert BacktestResult.gross_leverage.__doc__ == expected

    # Belt-and-braces source-level check.
    source = inspect.getsource(models_mod)
    assert expected in source


# ---------------------------------------------------------------------------
# BD-3 — non-UTC tz-aware index
# ---------------------------------------------------------------------------


def test_BD3_non_utc_tz_aware_index_raises():
    idx = pd.date_range("2026-01-01", periods=3, freq="1D", tz="America/New_York")
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan])
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_cfg(), md(prices), so)


def test_BD3_utc_tz_aware_index_does_not_raise():
    idx = dates(3)
    assert str(idx.tz) == "UTC"
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan])
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    run_backtest(_cfg(), md(prices), so)  # must not raise


# ---------------------------------------------------------------------------
# CG-1 — the cost-stage skip of Step 5
# ---------------------------------------------------------------------------


def test_CG1_step5_price_validation_skipped_on_cost_stage_ruin():
    """A cost-stage ruin at period i=2 must NOT read or validate P[3]. If
    Step 5's `P[i+1]` validation ran before the Step-4 cost-ruin return, an
    invalid (NaN) P[3] would raise instead of the run completing with
    `ruin_stage == 'cost'`."""
    idx = dates(4)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0, np.nan]}, index=idx)
    weights = pd.DataFrame({"A": [1.0, -4.0, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", execution_mode="next_open", execution_lag=1,
        fee_bps=1500, slippage_bps=1500, funding_mode="disabled",
        compute_counterfactual=False, annualization_factor=365,
    )
    res = run_backtest(cfg, MarketData(open=prices, close=prices), so)  # must not raise
    assert res.ruined is True
    assert res.ruin_stage == "cost"


# ---------------------------------------------------------------------------
# CG-2 — counterfactual barrier catches ONLY the enumerated types
# ---------------------------------------------------------------------------


def test_CG2_counterfactual_barrier_lets_typeerror_propagate(monkeypatch):
    """§9.5.1 — the barrier MUST catch only DataIntegrityError,
    FundingDataError and AccountingError. A programming error (TypeError)
    raised from counterfactual execution MUST propagate, not be swallowed
    into `counterfactual_status = 'FAILED'`. Broadening the barrier to
    `except Exception` would make this test hang forever waiting for an
    exception that never arrives -- `pytest.raises` would fail."""
    import backtest.engine as engine_mod

    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 110.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", execution_mode="next_open", execution_lag=1,
        fee_bps=0, slippage_bps=0, funding_mode="disabled",
        compute_counterfactual=True, annualization_factor=365,
    )

    original_simulate = engine_mod._simulate
    call_count = {"n": 0}

    def fake_simulate(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # the actual path — run for real, must complete first (§9.5.1).
            return original_simulate(*args, **kwargs)
        # the counterfactual path — a programming error, not a data error.
        raise TypeError("injected programming error inside counterfactual")

    monkeypatch.setattr(engine_mod, "_simulate", fake_simulate)

    with pytest.raises(TypeError):
        run_backtest(cfg, md(prices), so)


# ---------------------------------------------------------------------------
# CG-3 — unexecuted_rebalances exact boundary (§4.4)
# ---------------------------------------------------------------------------


def test_CG3_unexecuted_rebalances_exact_boundary_bar_is_executed():
    """`n=5`, `execution_lag=1`: the last TRADEABLE execution point is
    `i = n - 2 = 3` (t = 2). This rebalance MUST execute normally (nonzero
    turnover) and MUST NOT appear in `unexecuted_rebalances`. The existing
    T1 test only flags a rebalance two full bars past this boundary
    (`i = n > n - 2`), which cannot distinguish `i > n-2` from `i >= n-2`."""
    idx = dates(5)
    prices = single_symbol_frame(idx, [100.0] * 5)
    weights = single_symbol_frame(idx, [np.nan, np.nan, 1.0, np.nan, np.nan])
    mask = mask_series(idx, [2])  # t=2, lag=1 -> i=3=n-2, the exact boundary
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_cfg(), md(prices), so)
    assert idx[2] not in res.unexecuted_rebalances
    assert res.turnover.iloc[3] == pytest.approx(1.0, rel=1e-12)


def test_CG3_unexecuted_rebalances_one_past_boundary_is_unexecuted():
    """The immediately NEXT bar (t=3 -> i=4=n-1) is one past the boundary
    and MUST be recorded as unexecuted, with zero turnover throughout."""
    idx = dates(5)
    prices = single_symbol_frame(idx, [100.0] * 5)
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan, 1.0, np.nan])
    mask = mask_series(idx, [3])  # t=3, lag=1 -> i=4=n-1 > n-2=3
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_cfg(), md(prices), so)
    assert idx[3] in res.unexecuted_rebalances
    assert (res.turnover.to_numpy() == 0.0).all()


# ---------------------------------------------------------------------------
# CG-4 — §7.6 funding-notional-basis lookahead (moving-price fixture)
# ---------------------------------------------------------------------------


def test_CG4_period_start_basis_uses_Pi_not_Pi_plus_1():
    """Every §18.5 fixture uses FLAT prices, so `q * P[i]` (correct) and the
    REJECTED `q * P[i+1]` (a funding-notional lookahead) are numerically
    indistinguishable. This fixture moves the price from P[i]=100 to
    P[i+1]=150 across the funded period, so the two bases diverge sharply
    (-10_000.0 vs -15_000.0) and only the P[i]-based value is correct."""
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 150.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.01, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", execution_mode="next_open", execution_lag=1,
        fee_bps=0, slippage_bps=0, funding_mode="required", funding_notional_basis="period_start",
        compute_counterfactual=False, annualization_factor=365,
    )
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    q = res.quantity["A"].iloc[1]
    correct = -(q * 100.0 * 0.01)  # P[i] -- correct, no lookahead
    lookahead = -(q * 150.0 * 0.01)  # P[i+1] -- rejected, a funding lookahead
    assert res.funding_pnl_cash.iloc[1] == pytest.approx(correct, rel=1e-12)
    assert res.funding_pnl_cash.iloc[1] != pytest.approx(lookahead, rel=1e-6)


# ---------------------------------------------------------------------------
# CG-5 — untested §11.2 raise paths
# ---------------------------------------------------------------------------


def test_BD20_duplicate_price_index_timestamps_raise():
    """BD-20 (§11.2) — duplicate price-index TIMESTAMPS raise correctly
    (engine.py's `_validate_grid`, `index.has_duplicates` branch) but were
    covered by no test. Note this is a duplicate ROW (same timestamp
    appearing twice in the index), distinct from BD-1's duplicate COLUMN
    (same symbol appearing twice)."""
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-01-01", tz="UTC"),
            pd.Timestamp("2026-01-02", tz="UTC"),
            pd.Timestamp("2026-01-02", tz="UTC"),  # duplicate timestamp
        ]
    )
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan])
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError, match="duplicate"):
        run_backtest(_cfg(), md(prices), so)


def test_CG5_non_monotonic_timestamps_raise():
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-01-01", tz="UTC"),
            pd.Timestamp("2026-01-03", tz="UTC"),
            pd.Timestamp("2026-01-02", tz="UTC"),  # out of order
        ]
    )
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan])
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_cfg(), md(prices), so)


def test_CG5_naive_timestamps_raise():
    idx = pd.date_range("2026-01-01", periods=3, freq="1D")  # no tz
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan])
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_cfg(), md(prices), so)


def test_CG5_target_weights_symbol_absent_from_market_data_raises():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = pd.DataFrame({"B": [1.0, np.nan, np.nan]}, index=idx)  # "B" has no price data
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_cfg(), md(prices), so)


def test_CG5_misaligned_rebalance_mask_raises():
    idx = dates(3)
    other_idx = dates(3, freq="1D") + pd.Timedelta(hours=1)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan])
    mask = pd.Series([False, False, False], index=other_idx, dtype=bool)  # misaligned index
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_cfg(), md(prices), so)


def test_CG5_max_funding_gap_le_zero_raises():
    idx = dates(3)
    with pytest.raises(DataIntegrityError):
        FundingCoverage(
            symbol="A", coverage_start=idx[0], coverage_end=idx[2],
            max_funding_gap=pd.Timedelta(0), source_venue="t",
        )
    with pytest.raises(DataIntegrityError):
        FundingCoverage(
            symbol="A", coverage_start=idx[0], coverage_end=idx[2],
            max_funding_gap=pd.Timedelta(hours=-1), source_venue="t",
        )


# ---------------------------------------------------------------------------
# BD-22 — non-bool `rebalance_mask` is silently coerced
# ---------------------------------------------------------------------------


def test_BD22_non_bool_rebalance_mask_raises():
    """BD-22 — §3 declares `rebalance_mask : Series (index = bar label,
    dtype = bool)`. An object-dtype mask of truthy/falsy STRINGS (e.g.
    `["yes", "", ""]`) must NOT be silently coerced via `.astype(bool)`
    (which would turn it into `[True, False, False]` for reasons that have
    nothing to do with the mask's intended semantics) -- it must raise
    `DataIntegrityError`.

    `weights` is deliberately valid and non-NaN on every row (`0.0`
    throughout), so that if a mutated engine silently coerced the mask
    instead of raising, `run_backtest` would complete without any OTHER
    (confounding) `DataIntegrityError` -- e.g. the unrelated "NaN target on
    a rebalance bar" check -- making this test a clean discriminator of the
    coercion behaviour specifically, not of some other validation path.
    """
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [0.0, 0.0, 0.0])  # valid on every row, never NaN
    mask = pd.Series(["yes", "", ""], index=idx)  # object dtype, NOT bool
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_cfg(), md(prices), so)

    # A float-dtype (non-NaN) mask must also raise, not be silently coerced.
    mask_float = pd.Series([1.0, 0.0, 0.0], index=idx)
    so_float = StrategyOutput(target_weights=weights, rebalance_mask=mask_float)
    with pytest.raises(DataIntegrityError):
        run_backtest(_cfg(), md(prices), so_float)

    # Sanity: a genuine dtype=bool mask must NOT raise.
    mask_bool = pd.Series([False, False, False], index=idx, dtype=bool)
    so_bool = StrategyOutput(target_weights=weights, rebalance_mask=mask_bool)
    run_backtest(_cfg(), md(prices), so_bool)  # must not raise
