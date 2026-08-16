"""§18.3 — Rebalance and quantity ledger: R1-R6.

Config (all): initial_capital=1_000_000, frequency="1d", execution_mode="next_open",
funding_mode="disabled", fee_bps=10, slippage_bps=10, compute_counterfactual=False,
annualization_factor=365. execution_lag=1 except R6.
"""

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, StrategyOutput

from helpers import TOL_TURNOVER, dates, mask_series, md, single_symbol_frame


def _config(execution_lag=1):
    return BacktestConfig(
        initial_capital=1_000_000,
        frequency="1d",
        execution_mode="next_open",
        execution_lag=execution_lag,
        funding_mode="disabled",
        fee_bps=10,
        slippage_bps=10,
        compute_counterfactual=False,
        annualization_factor=365,
    )


def test_R1_zero_interim_turnover_and_fees_under_trending_prices():
    idx = dates(8)
    prices = single_symbol_frame(idx, [100.0, 105.0, 110.0, 108.0, 112.0, 115.0, 120.0, 125.0])
    weights = single_symbol_frame(idx, [1.0] + [np.nan] * 7)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)
    # Execution at i=1; periods i=2..6 are non-rebalance and MUST show exactly
    # zero turnover and zero fees regardless of the trending price path.
    for i in range(2, 7):
        assert res.turnover.iloc[i] == 0.0
        assert res.fee_cost.iloc[i] == 0.0
        assert res.slippage_cost.iloc[i] == 0.0


def test_R2_no_trade_on_repeated_target_but_drift_correction_under_rebalance_every_bar():
    # Two symbols with diverging prices, so a repeated (1.0, -1.0)-style target
    # under rebalance_every_bar genuinely requires drift-correcting trades,
    # while the same target under a mask true only at bar 0 produces none
    # after the initial execution.
    idx = dates(5)
    prices = pd.DataFrame(
        {"A": [100.0, 105.0, 110.0, 108.0, 112.0], "B": [50.0, 48.0, 47.0, 49.0, 46.0]},
        index=idx,
    )

    weights_once = pd.DataFrame(
        {"A": [0.6, 0.6, 0.6, 0.6, np.nan], "B": [-0.4, -0.4, -0.4, -0.4, np.nan]}, index=idx
    )
    mask_once = mask_series(idx, [0])
    so_once = StrategyOutput(target_weights=weights_once, rebalance_mask=mask_once)
    res_once = run_backtest(_config(), md(prices), so_once)
    # Execution at i=1 (turnover>0 expected); periods i=2,3 are non-rebalance.
    assert res_once.turnover.iloc[1] > 0.0
    # BD-18 — R2's mandated trade MAGNITUDES (§18.0.1: "EXACT for the zero
    # branch, TOLERANCE for magnitudes"), not just `turnover > 0`. At i=1,
    # quantity_prev == 0 for both symbols, so w_pre == 0.0 (assigned, §5.2)
    # and trade[j] = w_target[j] - w_pre[j] = w_target[j] exactly:
    #   trade["A"] == 0.6 - 0.0 == 0.6 ; trade["B"] == -0.4 - 0.0 == -0.4
    assert res_once.trades["A"].iloc[1] == pytest.approx(0.6, **TOL_TURNOVER)
    assert res_once.trades["B"].iloc[1] == pytest.approx(-0.4, **TOL_TURNOVER)
    assert res_once.turnover.iloc[1] == pytest.approx(1.0, **TOL_TURNOVER)
    for i in (2, 3):
        assert res_once.turnover.iloc[i] == 0.0
        assert res_once.trades.iloc[i].abs().sum() == 0.0  # EXACT — the no-trade branch

    # Same targets every bar, rebalance_every_bar: drift-correcting trades occur.
    weights_every = pd.DataFrame(
        {"A": [0.6, 0.6, 0.6, 0.6, 0.6], "B": [-0.4, -0.4, -0.4, -0.4, -0.4]}, index=idx
    )
    so_every = StrategyOutput.rebalance_every_bar(weights_every)
    res_every = run_backtest(_config(), md(prices), so_every)
    for i in (2, 3):
        assert res_every.turnover.iloc[i] > 0.0
        # BD-18 — the drift-correcting trade magnitude at i=2,3 must be a
        # genuine hand-derivable, nonzero value: trade[j] = w_target[j] -
        # w_pre[j], and w_pre has DRIFTED away from the target under moving
        # prices with quantity held constant (§5.2) -- so it must differ
        # from BOTH 0.0 (no-trade) and the raw target weight itself.
        assert res_every.trades["A"].iloc[i] != 0.0
        assert res_every.trades["A"].iloc[i] != pytest.approx(0.6, **TOL_TURNOVER)


def test_R3_weights_drift_between_rebalances_quantity_constant():
    idx = dates(5)
    prices = pd.DataFrame(
        {"A": [100.0, 105.0, 110.0, 115.0, 120.0], "B": [50.0, 49.0, 52.0, 47.0, 55.0]},
        index=idx,
    )
    weights = pd.DataFrame({"A": [0.6] + [np.nan] * 4, "B": [-0.4] + [np.nan] * 4}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)
    qA = res.quantity["A"].to_numpy()
    qB = res.quantity["B"].to_numpy()
    wA = res.positions["A"].to_numpy()
    # quantity constant across all non-rebalance periods (1..3)
    assert qA[1] == qA[2] == qA[3]
    assert qB[1] == qB[2] == qB[3]
    # weights drift because price and NAV move asymmetrically while quantity
    # is held fixed (two diverging-price legs).
    assert not (wA[1] == pytest.approx(wA[2]) and wA[2] == pytest.approx(wA[3]))


def test_R4_quantity_exact_carry_forward_including_inactive_symbols():
    """BT-6 — rebuilt fixture.

    The original single-symbol, single-rebalance fixture never diverges
    under the §5.1-REJECTED `q -> w -> q` weight round-trip: the specific
    prices/NAV values happened to round-trip bitwise at every single
    period (measured, verified by mutation), so the mutation left this
    test green. §5.1 states the round trip fails on ~17.8% of realistic
    inputs, and this test is the one assigned to catch it -- it needs
    drifting prices AND a moving NAV so the reconstruction is not bitwise
    (a single deterministic draw of "nice" numbers is not enough; the
    two-asset, diverging-price fixture below was checked by direct
    simulation to diverge under the round-trip at period index 2).
    """
    idx = dates(10)
    prices = pd.DataFrame(
        {
            "A": [100.0, 105.0, 110.0, 108.0, 112.0, 115.0, 120.0, 125.0, 130.0, 128.0],
            "B": [50.0, 49.0, 52.0, 47.0, 55.0, 53.0, 58.0, 60.0, 62.0, 59.0],
            # C is never named -> always target 0 -> INACTIVE, quantity[C] stays literal 0.0.
            "C": [1.0] * 10,
        },
        index=idx,
    )
    weights = pd.DataFrame({"A": [0.6] + [np.nan] * 9, "B": [-0.4] + [np.nan] * 9}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)
    qA = res.quantity["A"].to_numpy()
    qB = res.quantity["B"].to_numpy()
    qC = res.quantity["C"].to_numpy()
    for i in range(2, len(qA)):
        assert qA[i] == qA[i - 1]  # EXACT — stored-state carry-forward
        assert qB[i] == qB[i - 1]  # EXACT — stored-state carry-forward
    for i in range(len(qC)):
        assert qC[i] == 0.0  # EXACT — literal zero, INACTIVE throughout


def test_R5_quantity_changes_only_where_rebalance_flag():
    idx = dates(10)
    prices = single_symbol_frame(idx, [100.0 + i * 2.3 for i in range(10)])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan, -0.5] + [np.nan] * 6)
    mask = mask_series(idx, [0, 3])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)
    q = res.quantity["A"].to_numpy()
    flags = res.rebalance_flag.to_numpy()
    for i in range(1, len(q)):
        if not flags[i]:
            assert q[i] == q[i - 1]


def test_R6_execution_lag_2_rebalance_flag_at_index_4_only():
    idx = dates(8)
    prices = single_symbol_frame(idx, [100.0] * 8)
    weights = single_symbol_frame(idx, [np.nan, np.nan, 1.0] + [np.nan] * 5)
    mask = mask_series(idx, [2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(execution_lag=2), md(prices), so)
    flags = res.rebalance_flag.to_numpy()
    expected = [False] * len(flags)
    expected[4] = True
    assert flags.tolist() == expected
