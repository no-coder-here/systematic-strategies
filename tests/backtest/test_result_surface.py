"""§10 result-surface ruling — `target_weights` (as supplied).

The platform owner's ruling on the §10 row-count inconsistency: the
explicit per-field qualifier "(as supplied)" governs over the group
header's "n_periods rows". `result.target_weights` MUST be the strategy's
supplied frame, passed through UNMODIFIED (bar-indexed, values exactly as
given, NaNs preserved, no 0.0 filling). The resolved, execution-indexed
frame is exposed separately as `resolved_target_weights`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, MarketData, StrategyOutput

from helpers import TOL_EQUITY, TOL_METRIC, dates, mask_series, md, single_symbol_frame


def _config(**overrides):
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


def test_target_weights_is_the_supplied_frame_unmodified():
    idx = dates(6)
    prices = single_symbol_frame(idx, [100.0, 105.0, 110.0, 108.0, 112.0, 115.0])
    # Bar-indexed (n=6 rows), NaN on non-rebalance bars, exactly as supplied.
    supplied = single_symbol_frame(idx, [1.0, np.nan, np.nan, -0.5, np.nan, np.nan])
    mask = mask_series(idx, [0, 3])
    so = StrategyOutput(target_weights=supplied, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)

    # Identity / exact pass-through: same object, same shape, same NaNs, no
    # 0.0-filling anywhere.
    assert res.target_weights is supplied
    assert len(res.target_weights) == len(idx)  # bar-indexed, n rows -- NOT n_periods
    pd.testing.assert_frame_equal(res.target_weights, supplied)
    assert res.target_weights.isna().sum().sum() == supplied.isna().sum().sum()

    # `resolved_target_weights` is the DIFFERENT, execution-indexed,
    # n_periods-row frame (NaN on non-rebalance execution periods).
    assert len(res.resolved_target_weights) == len(res.net_return)  # n_periods rows
    assert not res.resolved_target_weights.equals(res.target_weights)

    # BD-14 — VALUES, not just shape/inequality. `rebalance_flag` is True at
    # i=1 (t=0) and i=4 (t=3); every other row is NaN (§10: "NaN on
    # non-rebalance rows"). This discriminates
    # `target_used_rows.append(np.zeros(n_symbols))` (always-zero mutation).
    resolved = res.resolved_target_weights["A"]
    assert np.isnan(resolved.iloc[0])
    assert resolved.iloc[1] == 1.0
    assert np.isnan(resolved.iloc[2])
    assert np.isnan(resolved.iloc[3])
    assert resolved.iloc[4] == -0.5


def test_target_weights_passthrough_preserves_nan_no_zero_filling():
    idx = dates(4)
    prices = pd.DataFrame({"A": [100.0] * 4, "B": [50.0] * 4}, index=idx)
    supplied = pd.DataFrame({"A": [1.0, np.nan, np.nan, np.nan]}, index=idx)  # B never named at all
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=supplied, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)

    # B is entirely absent from the supplied frame -- passthrough must NOT
    # invent a 0.0-filled B column.
    assert "B" not in res.target_weights.columns
    assert list(res.target_weights.columns) == ["A"]
    assert res.target_weights["A"].iloc[1:].isna().all()


def test_BD8_notional_equals_quantity_times_P_i_not_P_ip1():
    """BD-8 (§5.6/§10) — `notional` is NEVER value-asserted anywhere in the
    suite. A genuine `P[i+1]` lookahead in this reported field
    (`notional_i[act] = quantity_i[act] * P_ip1[act]`) is otherwise
    invisible. Prices move between P[i] and P[i+1] specifically so the two
    candidate formulas diverge sharply.
    """
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 110.0, 120.0], "B": [50.0, 55.0, 60.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.6, np.nan, np.nan], "B": [-0.4, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)

    q_A = res.quantity["A"].iloc[1]
    q_B = res.quantity["B"].iloc[1]
    correct_A = q_A * 110.0  # P[1] -- correct, §5.6
    correct_B = q_B * 55.0
    lookahead_A = q_A * 120.0  # P[2] -- rejected, a lookahead
    assert res.notional["A"].iloc[1] == pytest.approx(correct_A, **TOL_EQUITY)
    assert res.notional["B"].iloc[1] == pytest.approx(correct_B, **TOL_EQUITY)
    assert res.notional["A"].iloc[1] != pytest.approx(lookahead_A, rel=1e-6)

    # §5.6 zero branch: assigned literal 0.0 (INACTIVE symbol never held).
    idx2 = dates(3)
    prices2 = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [50.0, 50.0, 50.0]}, index=idx2)
    weights2 = pd.DataFrame({"A": [1.0, np.nan, np.nan]}, index=idx2)  # B never named
    mask2 = mask_series(idx2, [0])
    so2 = StrategyOutput(target_weights=weights2, rebalance_mask=mask2)
    res2 = run_backtest(_config(), md(prices2), so2)
    assert (res2.notional["B"].to_numpy() == 0.0).all()  # EXACT — assigned literal


def test_BD9_period_and_equity_timestamps_equal_execution_instant_both_modes():
    """BD-9 (§8/§4.3/§10) — result-index timestamps are untested under
    `next_close`. Relabelling `equity_curve`/per-period series from `T_k`
    (the execution instant) to the raw bar label is invisible without this
    test: under `next_open`, `T_k == t_k` so the two coincide by
    coincidence, but under `next_close`, `T_k == t_k + delta != t_k`. §4.3
    warns this exact class "misattributes funding by a full bar".
    """
    idx = dates(4)
    delta = pd.Timedelta(days=1)
    data = MarketData(open=single_symbol_frame(idx, [100.0] * 4), close=single_symbol_frame(idx, [100.0] * 4))
    weights = single_symbol_frame(idx, [np.nan] * 4)
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)

    res_open = run_backtest(_config(execution_mode="next_open"), data, so)
    assert list(res_open.equity_curve.index) == list(idx)  # T_k == t_k under next_open
    assert list(res_open.net_return.index) == list(idx[:-1])
    assert list(res_open.turnover.index) == list(idx[:-1])

    res_close = run_backtest(_config(execution_mode="next_close"), data, so)
    expected_close = [t + delta for t in idx]
    assert list(res_close.equity_curve.index) == expected_close  # T_k == t_k + delta
    assert list(res_close.net_return.index) == expected_close[:-1]
    assert list(res_close.turnover.index) == expected_close[:-1]
    # Sanity: the two modes' timestamps must actually differ (else this test
    # would pass vacuously under a broken engine that ignores execution_mode).
    assert list(res_close.equity_curve.index) != list(res_open.equity_curve.index)


def test_BD10_fee_basis_notional_equals_turnover_times_nav_pre():
    """BD-10 (§6.2/§10) — `fee_basis_notional` is never value-asserted."""
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.0, np.nan, np.nan], "B": [-1.0, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(fee_bps=10, slippage_bps=10), md(prices), so)

    turnover = res.turnover.iloc[1]
    nav_pre = res.equity_curve.iloc[1]
    assert turnover == pytest.approx(2.0, rel=1e-12)
    assert nav_pre == 1_000_000.0
    # Single clean arithmetic path on exactly-representable inputs -- EXACT.
    assert res.fee_basis_notional.iloc[1] == turnover * nav_pre
    assert res.fee_basis_notional.iloc[1] == 2_000_000.0


def test_BD11_avg_turnover_and_annualized_turnover():
    """BD-11 (§12) — `avg_turnover` / `annualized_turnover` are never
    value-asserted (`avg_turnover * sqrt(af)` — a wrong exponent on `af` --
    would survive without this test)."""
    idx = dates(5)
    prices = single_symbol_frame(idx, [100.0] * 5)
    weights = single_symbol_frame(idx, [1.0, -1.0, 1.0, np.nan, np.nan])
    mask = mask_series(idx, [0, 1, 2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(annualization_factor=365), md(prices), so)

    turnover = res.turnover.to_numpy()
    expected_avg = float(np.mean(turnover))
    assert expected_avg > 0.0  # sanity: fixture actually has nonzero turnover
    assert res.metrics["avg_turnover"] == pytest.approx(expected_avg, **TOL_METRIC)
    assert res.metrics["annualized_turnover"] == pytest.approx(expected_avg * 365.0, **TOL_METRIC)
    # §12's formula is `mean(turnover) * af`, NOT `mean(turnover) * sqrt(af)`.
    assert res.metrics["annualized_turnover"] != pytest.approx(
        expected_avg * (365.0 ** 0.5), rel=1e-6
    )


def test_BD12_liquidation_modelled_always_false_and_in_repr():
    """BD-12 (§14/§10) — `liquidation_modelled` is never asserted anywhere,
    including `__repr__`. §14: always `False`."""
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan, np.nan])
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)

    assert res.liquidation_modelled is False
    assert "liquidation_modelled=False" in repr(res)
