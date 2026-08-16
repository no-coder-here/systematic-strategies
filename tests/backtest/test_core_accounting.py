"""§18.2 — Core accounting: A, B, C, D, G, H, I, J, L, M, N, P, P2.

Config (all): initial_capital=1_000_000, frequency="1d", execution_mode="next_open",
execution_lag=1, funding_mode="disabled", compute_counterfactual=False,
annualization_factor=365. fee_bps / slippage_bps stated per test.
"""

import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, StrategyOutput

from helpers import dates, mask_series, md, single_symbol_frame


def _config(fee_bps, slippage_bps):
    return BacktestConfig(
        initial_capital=1_000_000,
        frequency="1d",
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        execution_mode="next_open",
        execution_lag=1,
        funding_mode="disabled",
        annualization_factor=365,
        compute_counterfactual=False,
    )


def test_A_zero_targets_zero_pnl_fees_turnover():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [0.0, 0.0, 0.0])
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert (res.net_return.to_numpy() == 0.0).all()
    assert (res.fee_cost.to_numpy() == 0.0).all()
    assert (res.turnover.to_numpy() == 0.0).all()


def test_B_long_position_gain():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 110.0, 121.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert res.equity_curve.tolist() == pytest.approx([1e6, 1e6, 1.1e6], rel=1e-12)
    assert res.metrics["total_return"] == pytest.approx(0.1, rel=1e-12)


def test_C_short_position_loss():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 110.0, 121.0])
    weights = single_symbol_frame(idx, [-1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert res.equity_curve.tolist() == pytest.approx([1e6, 1e6, 0.9e6], rel=1e-12)
    assert res.metrics["total_return"] == pytest.approx(-0.1, rel=1e-12)


def test_D_two_symbol_market_neutral():
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 110.0, 110.0], "B": [50.0, 50.0, 55.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.5, np.nan, np.nan], "B": [-0.5, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert res.metrics["total_return"] == pytest.approx(-0.05, rel=1e-12)


def test_G_turnover_zero_to_long_is_one():
    # execution_lag=1 requires n >= 3 for a bar-0 rebalance to land inside the
    # tradeable range [execution_lag, n-2]; the execution point is i=1.
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert res.turnover.iloc[1] == pytest.approx(1.0, rel=1e-12)


def test_H_turnover_long_to_short_is_two():
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, -1.0, np.nan, np.nan])
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    # i=1 executes bar0's +1 (turnover 1); i=2 executes bar1's -1 (turnover 2).
    assert res.turnover.iloc[1] == pytest.approx(1.0, rel=1e-12)
    assert res.turnover.iloc[2] == pytest.approx(2.0, rel=1e-12)


def _turnover_two_at_nav_pre_1e6(fee_bps, slippage_bps):
    """A single simultaneous two-symbol rebalance from a flat book gives
    turnover=2 realised entirely at NAV_pre == initial_capital == 1_000_000
    exactly (no prior period touched NAV), matching the contract's pinned
    "Turnover 2 at NAV_pre = 1e6" fixtures for I and J."""
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.0, np.nan, np.nan], "B": [-1.0, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        initial_capital=1_000_000,
        frequency="1d",
        execution_mode="next_open",
        execution_lag=1,
        funding_mode="disabled",
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        compute_counterfactual=False,
        annualization_factor=365,
    )
    return run_backtest(cfg, md(prices), so)


def test_I_fee_cost():
    res = _turnover_two_at_nav_pre_1e6(fee_bps=10, slippage_bps=0)
    assert res.turnover.iloc[1] == pytest.approx(2.0, rel=1e-12)
    assert res.fee_cost.iloc[1] == pytest.approx(2000.0, rel=1e-12)


def test_J_slippage_cost():
    res = _turnover_two_at_nav_pre_1e6(fee_bps=0, slippage_bps=10)
    assert res.turnover.iloc[1] == pytest.approx(2.0, rel=1e-12)
    assert res.slippage_cost.iloc[1] == pytest.approx(2000.0, rel=1e-12)


def test_L_gross_exposure():
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.6, np.nan, np.nan], "B": [-0.4, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert res.gross_exposure.iloc[1] == pytest.approx(1.0, rel=1e-12)


def test_M_net_exposure():
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.6, np.nan, np.nan], "B": [-0.4, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert res.net_exposure.iloc[1] == pytest.approx(0.2, rel=1e-12)


def test_N_gross_leverage():
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.5, np.nan, np.nan], "B": [-1.5, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(0, 0), md(prices), so)
    assert res.gross_leverage.iloc[1] == pytest.approx(3.0, rel=1e-12)


def _r1_fixture():
    """Reused §18.3 R1 fixture for P/P2: a rebalance followed by six
    non-rebalance bars under trending prices."""
    idx = dates(8)
    prices = single_symbol_frame(idx, [100.0, 105.0, 110.0, 108.0, 112.0, 115.0, 120.0, 125.0])
    weights = single_symbol_frame(idx, [1.0] + [np.nan] * 7)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        initial_capital=1_000_000,
        frequency="1d",
        execution_mode="next_open",
        execution_lag=1,
        funding_mode="disabled",
        fee_bps=10,
        slippage_bps=10,
        compute_counterfactual=False,
        annualization_factor=365,
    )
    return cfg, prices, so


def test_P_determinism_two_runs_exact():
    cfg, prices, so = _r1_fixture()
    res1 = run_backtest(cfg, md(prices), so)
    res2 = run_backtest(cfg, md(prices), so)
    assert res1.equity_curve.tolist() == res2.equity_curve.tolist()
    assert res1.net_return.tolist() == res2.net_return.tolist()
    assert res1.turnover.tolist() == res2.turnover.tolist()


def test_P2_determinism_across_process_and_hashseed():
    import os
    import pathlib

    cfg, prices, so = _r1_fixture()
    res1 = run_backtest(cfg, md(prices), so)

    src_path = str(pathlib.Path(__file__).resolve().parents[2] / "src")
    script = (
        "import sys; sys.path.insert(0, " + repr(src_path) + "); "
        "import pandas as pd, numpy as np; "
        "from backtest.engine import run_backtest; "
        "from backtest.models import BacktestConfig, MarketData, StrategyOutput; "
        "idx = pd.date_range('2026-01-01', periods=8, freq='1D', tz='UTC'); "
        "prices = pd.DataFrame({'A': [100.0,105.0,110.0,108.0,112.0,115.0,120.0,125.0]}, index=idx); "
        "weights = pd.DataFrame({'A': [1.0] + [np.nan]*7}, index=idx); "
        "mask = pd.Series([True] + [False]*7, index=idx); "
        "so = StrategyOutput(target_weights=weights, rebalance_mask=mask); "
        "cfg = BacktestConfig(initial_capital=1_000_000, frequency='1d', execution_mode='next_open', "
        "execution_lag=1, funding_mode='disabled', fee_bps=10, slippage_bps=10, "
        "compute_counterfactual=False, annualization_factor=365); "
        "res = run_backtest(cfg, MarketData(open=prices, close=prices), so); "
        "print(repr(res.equity_curve.tolist()))"
    )

    for hashseed in ("0", "1234"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hashseed
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
        assert out.returncode == 0, out.stderr
        other_equity = eval(out.stdout.strip())
        assert other_equity == res1.equity_curve.tolist()
