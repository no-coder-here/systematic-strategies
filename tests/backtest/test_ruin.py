"""§18.8 — Ruin: X1-X11 (X8a, X8b separately).

Config (all three fixtures, B4 accepted debt — header textually said "both"
but covers X1, X9, X11): initial_capital=1_000_000, frequency="1d",
execution_mode="next_open", execution_lag=1, funding_mode="disabled",
annualization_factor=365, compute_counterfactual=False, 1 symbol.
"""

import math

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import AccountingError, BacktestConfig, StrategyOutput

from helpers import TOL_DECOMP, TOL_EQUITY, dates, mask_series, md, single_symbol_frame


def _config(fee_bps, slippage_bps, max_gross_leverage=None):
    return BacktestConfig(
        initial_capital=1_000_000,
        frequency="1d",
        execution_mode="next_open",
        execution_lag=1,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        funding_mode="disabled",
        annualization_factor=365,
        compute_counterfactual=False,
        max_gross_leverage=max_gross_leverage,
    )


def _x1():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 60.0])
    weights = single_symbol_frame(idx, [3.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    return run_backtest(_config(10, 10), md(prices), so), idx


def _x9():
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 60.0])
    weights = single_symbol_frame(idx, [1.0, -4.0, np.nan, np.nan])
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    return run_backtest(_config(1500, 1500), md(prices), so), idx


def _x11(target=-1.0):
    idx = dates(3)
    prices = single_symbol_frame(idx, [5e-324, 5e-324, 1e-300])
    weights = single_symbol_frame(idx, [target, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    return _config(0, 0), prices, so


def _assert_ruin_equity(actual: list, expected: list) -> None:
    """§18.0.1 — X1/X9 equity is TOLERANCE except `equity[-1] == 0.0`, which
    is EXACT (the ruin floor, an assigned literal per §6.7.2)."""
    assert actual[:-1] == pytest.approx(expected[:-1], rel=1e-12)
    assert actual[-1] == expected[-1] == 0.0  # EXACT


def test_X1_pinned_pnl_stage_ruin():
    res, idx = _x1()
    _assert_ruin_equity(res.equity_curve.tolist(), [1_000_000.0, 1_000_000.0, 0.0])
    assert res.net_return.tolist() == pytest.approx([0.0, -1.0], rel=1e-12)
    assert res.ruined is True
    assert res.ruin_stage == "pnl"
    assert res.ruin_timestamp == idx[2]
    # S-6 — `uncapped_ruin_return`/`ruin_decomposition_residual` are the
    # same (D) group §17 governs ("decomposition identity (D),
    # ruin_decomposition_residual" row): rtol=1e-12, atol=1e-15. The
    # previous `rel=1e-14` on the line below was not a §17 row (tighter than
    # any row mandates and not re-derived from one).
    assert res.uncapped_ruin_return == pytest.approx(-1.1988000000000000878, **TOL_DECOMP)
    assert res.ruin_decomposition_residual == pytest.approx(-0.19880000000000008775, **TOL_DECOMP)
    assert res.metrics["total_return"] == -1.0
    assert res.metrics["max_drawdown"] == -1.0
    assert res.metrics["cagr"] == -1.0
    assert res.metrics["calmar"] == -1.0

    assert res.turnover.iloc[1] == pytest.approx(3.0, rel=1e-12)
    assert res.fee_cost.iloc[1] == pytest.approx(3000.0, rel=1e-12)
    assert res.slippage_cost.iloc[1] == pytest.approx(3000.0, rel=1e-12)
    assert res.quantity["A"].iloc[1] == pytest.approx(29820.0, rel=1e-12)
    assert res.positions["A"].iloc[1] == pytest.approx(3.0, rel=1e-12)
    # BD-21 — asset_pnl_cash is a USD cash value -> §17 row 1 (equity
    # reconstruction check: rtol=1e-12, atol=1e-9), tightened from `rel=1e-9`.
    assert res.asset_pnl_cash.iloc[1] == pytest.approx(-1_192_800.0, **TOL_EQUITY)
    assert res.funding_pnl_cash.iloc[1] == 0.0


def test_X2_series_truncated_not_padded():
    res, idx = _x1()
    # ruin at period index 1 (0-based)
    ruin_period = 1
    assert len(res.net_return) == ruin_period + 1
    assert len(res.equity_curve) == ruin_period + 2


# W-2 — §18.8 X3 states "no `inf` ANYWHERE; `NaN` ONLY in the four fields
# §6.7.2 permits", and §6.7.2's table is declared "exhaustive over the §10
# per-period surface". The 8-Series subset previously checked left every
# per-symbol FRAME (`quantity`, `notional`, `positions`, `pre_trade_weights`,
# `trades`) and several scalar Class-A Series (`turnover`, `fee_cost`,
# `slippage_cost`, `fee_basis_notional`, `gross_exposure`, `net_exposure`,
# `gross_leverage`) entirely unswept -- a `notional = inf` or
# `pre_trade_weights = nan` injection at the cost-ruin terminal row survived
# the full 154-test suite. The four fields genuinely permitted `NaN` (ONLY at
# a cost-stage terminal row, per §6.7.2's table) are exactly
# `asset_pnl_cash`, `funding_pnl_cash`, `gross_return`, `funding_return` --
# every other field enumerated below is §6.7.2 CLASS A/B and MUST be finite
# (no `inf`, no `NaN`) even at the ruin period.

_X3_SERIES_FIELDS = (
    "net_return", "equity_curve", "gross_return", "fee_return", "slippage_return",
    "funding_return", "asset_pnl_cash", "funding_pnl_cash",
    "turnover", "fee_cost", "slippage_cost", "fee_basis_notional",
    "gross_exposure", "net_exposure", "gross_leverage",
)
_X3_FRAME_FIELDS = ("quantity", "notional", "positions", "pre_trade_weights", "trades")
_X3_NAN_PERMITTED_FIELDS = ("asset_pnl_cash", "funding_pnl_cash", "gross_return", "funding_return")


def _x3_all_numeric_arrays(res):
    """Yields (field_name, 1-D numpy array) for every Series AND every
    per-symbol FRAME column on the §10 result surface (§6.7.2's "exhaustive"
    per-period table), flattening frames so every symbol column is swept."""
    for name in _X3_SERIES_FIELDS:
        yield name, getattr(res, name).to_numpy()
    for name in _X3_FRAME_FIELDS:
        frame = getattr(res, name)
        for col in frame.columns:
            yield f"{name}[{col!r}]", frame[col].to_numpy()


def test_X3_no_inf_anywhere_nan_only_where_permitted():
    res1, _ = _x1()
    # X1 is a pnl-stage ruin: no `inf` and no `NaN` ANYWHERE on the full
    # §10 per-period surface (Series AND per-symbol frames).
    for field, arr in _x3_all_numeric_arrays(res1):
        assert not np.isinf(arr).any(), f"inf in {field!r} (X1, pnl-stage ruin)"
        assert not np.isnan(arr).any(), f"unexpected NaN in {field!r} (X1, pnl-stage ruin)"

    res9, _ = _x9()
    terminal = len(res9.net_return) - 1
    # X9 is a cost-stage ruin: no `inf` ANYWHERE; `NaN` permitted ONLY at the
    # terminal row, and ONLY in the four §6.7.2-permitted Class-C fields.
    for field, arr in _x3_all_numeric_arrays(res9):
        assert not np.isinf(arr).any(), f"inf in {field!r} (X9, cost-stage ruin)"
    for name in _X3_NAN_PERMITTED_FIELDS:
        series = getattr(res9, name)
        assert np.isnan(series.iloc[terminal]), f"{name!r} MUST be NaN at the cost-stage terminal row (§6.7.2)"
        assert not np.isnan(series.to_numpy()[:terminal]).any(), f"unexpected NaN in {name!r} before the terminal row"
    for field, arr in _x3_all_numeric_arrays(res9):
        if field in _X3_NAN_PERMITTED_FIELDS:
            continue
        assert not np.isnan(arr).any(), f"unexpected NaN in {field!r} (§6.7.2 CLASS A/B, must be finite)"


def test_X4_terminal_metrics_all_exactly_negative_one():
    for res, _ in (_x1(), _x9()):
        assert res.metrics["total_return"] == -1.0
        assert res.metrics["max_drawdown"] == -1.0
        assert res.metrics["cagr"] == -1.0
        assert res.metrics["calmar"] == -1.0


def test_X5_ruin_at_period_0_n_periods_1_dispersion_metrics_nan():
    """# LOWER-LEVEL HELPER - not a production config (B1: with execution_lag>=1
    a production config cannot rebalance at period 0, since NAV_end[0] =
    initial_capital > 0 always. Exercised directly via _step_period, per §18.0.2."""
    from backtest.engine import _step_period
    from backtest.metrics import compute_metrics

    idx = dates(2)
    # A genuine period-0 cost-stage ruin: huge fee+slippage wipes NAV_after_cost.
    r2 = _step_period(
        i=0,
        rebalance=True,
        w_target=np.array([1.0]),
        quantity_prev=np.array([0.0]),
        P_i=np.array([100.0]),
        P_ip1=np.array([100.0]),
        NAV_pre=1_000_000.0,
        fee_bps=6000.0,
        slippage_bps=6000.0,
        symbols=["A"],
        timestamp_i=idx[0],
        timestamp_ip1=idx[1],
    )
    assert r2.ruin_stage == "cost"
    net_return = np.array([r2.net_return])
    equity_curve = np.array([1_000_000.0, 0.0])
    m = compute_metrics(net_return, equity_curve, np.array([r2.turnover]), af=365.0)
    assert m["total_return"] == -1.0
    assert math.isnan(m["sharpe"])
    assert math.isnan(m["sortino"])
    assert math.isnan(m["annualized_volatility"])


def test_X6_ruin_decomposition_residual_and_cost_stage_two_component_sum():
    res1, _ = _x1()
    # S-6/S-5 — both assertions target the SAME (D)-group quantity
    # (`ruin_decomposition_residual`), so both MUST use the SAME §17 row
    # ("decomposition identity (D)"): rtol=1e-12, atol=1e-15 (`TOL_DECOMP`).
    # The previous `rel=1e-3` on the first line was not a §17 row at all
    # (far looser than the row that governs this quantity); it is retained
    # here (re-derived at the correct tolerance) only to document that the
    # rounded literal `-0.1988` and the true value
    # `-0.19880000000000008775` are close but NOT bitwise equal.
    assert res1.ruin_decomposition_residual == pytest.approx(-0.1988, **TOL_DECOMP)
    assert res1.ruin_decomposition_residual == pytest.approx(-0.19880000000000008775, **TOL_DECOMP)

    res9, _ = _x9()
    assert res9.ruin_decomposition_residual == pytest.approx(-0.5, rel=1e-12)
    terminal = len(res9.net_return) - 1
    two_component_sum = res9.fee_return.iloc[terminal] + res9.slippage_return.iloc[terminal]
    assert two_component_sum == pytest.approx(res9.uncapped_ruin_return, **TOL_DECOMP)
    assert two_component_sum == pytest.approx(-1.5, rel=1e-12)


def test_X7_ruined_true_appears_in_repr():
    res, _ = _x1()
    assert "ruined=True" in repr(res)


def test_X8a_near_ruin_finiteness():
    """BT-3 — genuine near-ruin.

    The original fixture (`P = [100, 100, 100.00001]`, weight 1.0) gives
    `NAV_end = 1_000_000.1` — an ordinary +0.00001% period, nowhere near
    ruin; the inline comment claiming otherwise was false. This fixture
    uses a leveraged short (`target = -10.0`) and a calibrated price move
    that drives `NAV_end` to ~1e-6 (positive, non-ruin, but as close to the
    ruin floor as floating point comfortably allows without crossing it).
    """
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 109.99999999999])
    weights = single_symbol_frame(idx, [-10.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert not np.isinf(res.equity_curve.to_numpy()).any()
    assert not np.isnan(res.equity_curve.to_numpy()).any()
    assert res.ruined is False
    # Genuinely NEAR ruin: strictly positive, but tiny relative to NAV_pre.
    assert 0.0 < res.equity_curve.iloc[-1] < 1.0
    assert res.net_return.iloc[-1] < -0.9999


def test_X8b_leverage_breach_fires():
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.5, np.nan, np.nan], "B": [-1.5, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0, max_gross_leverage=2.0), md(prices), so)
    assert res.leverage_breach is True
    assert len(res.leverage_breach_timestamps) >= 1

    res_no_breach = run_backtest(_config(0, 0, max_gross_leverage=5.0), md(prices), so)
    assert res_no_breach.leverage_breach is False


def test_BD16_leverage_tripwire_boundary_is_strict_greater_than():
    """BD-16 (§6.8) — the tripwire is STRICT `>`, not `>=`. A period with
    `gross_exposure == max_gross_leverage` exactly must NOT breach; a hair
    above it must. `>` vs `>=` at the comparison site is otherwise untested
    (a fixture whose `gross_exposure` lands exactly on the boundary is
    required -- X8b's fixture never does).
    """
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.6, np.nan, np.nan], "B": [-0.4, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)

    res_exact = run_backtest(_config(0, 0, max_gross_leverage=1.0), md(prices), so)
    assert res_exact.gross_exposure.iloc[1] == pytest.approx(1.0, rel=1e-12)
    assert res_exact.leverage_breach is False  # exactly AT the limit -> no breach

    res_above = run_backtest(_config(0, 0, max_gross_leverage=1.0 - 1e-9), md(prices), so)
    assert res_above.leverage_breach is True  # a hair above the limit -> breach


def test_X9_pinned_cost_stage_ruin():
    res, idx = _x9()
    _assert_ruin_equity(res.equity_curve.tolist(), [1_000_000.0, 1_000_000.0, 700_000.0, 0.0])
    assert res.net_return.tolist() == pytest.approx([0.0, -0.3, -1.0], rel=1e-12)
    assert res.ruined is True
    assert res.ruin_stage == "cost"
    assert res.ruin_timestamp == idx[3]
    assert res.uncapped_ruin_return == pytest.approx(-1.5, rel=1e-12)
    assert res.ruin_decomposition_residual == pytest.approx(-0.5, rel=1e-12)
    terminal = 2
    assert res.fee_return.iloc[terminal] + res.slippage_return.iloc[terminal] == pytest.approx(-1.5, rel=1e-12)

    assert res.turnover.iloc[terminal] == pytest.approx(5.0, rel=1e-12)
    assert res.fee_cost.iloc[terminal] == pytest.approx(525_000.0, rel=1e-12)
    assert res.slippage_cost.iloc[terminal] == pytest.approx(525_000.0, rel=1e-12)
    assert res.quantity["A"].iloc[terminal] == 7000.0  # EXACT — pre-trade quantity (§6.7.1)
    assert res.positions["A"].iloc[terminal] == pytest.approx(1.0, rel=1e-12)  # equals w_pre
    assert np.isnan(res.asset_pnl_cash.iloc[terminal])
    assert np.isnan(res.funding_pnl_cash.iloc[terminal])
    assert np.isnan(res.gross_return.iloc[terminal])
    assert np.isnan(res.funding_return.iloc[terminal])

    assert res.metrics["total_return"] == -1.0
    assert res.metrics["max_drawdown"] == -1.0
    assert res.metrics["cagr"] == -1.0
    assert res.metrics["calmar"] == -1.0


def test_X10_ruin_stage_and_terminal_position_convention():
    res1, _ = _x1()
    res9, _ = _x9()
    assert res1.ruin_stage == "pnl"
    assert res9.ruin_stage == "cost"
    assert res1.terminal_position_convention == "pre_ruin_state"
    assert res9.terminal_position_convention == "pre_ruin_state"


def test_X11_non_finite_nav_is_not_ruin_raises_accounting_error():
    cfg, prices, so = _x11(target=-1.0)
    with pytest.raises(AccountingError):
        run_backtest(cfg, md(prices), so)

    cfg2, prices2, so2 = _x11(target=1.0)  # NAV_end = +inf branch
    with pytest.raises(AccountingError):
        run_backtest(cfg2, md(prices2), so2)

    # X1 and X9 must be bit-for-bit unchanged by the guard ordering (§18.0.1
    # classifies X1/X9 equity TOLERANCE except the EXACT ruin-floor element).
    res1, _ = _x1()
    res9, _ = _x9()
    _assert_ruin_equity(res1.equity_curve.tolist(), [1_000_000.0, 1_000_000.0, 0.0])
    _assert_ruin_equity(res9.equity_curve.tolist(), [1_000_000.0, 1_000_000.0, 700_000.0, 0.0])
