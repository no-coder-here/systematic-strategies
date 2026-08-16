"""§18.1 — anti-lookahead: E, E2, E3, E4.

Config (E, E2, E3): initial_capital=1_000_000, frequency="1d", fee_bps=0,
slippage_bps=0, funding_mode="disabled", compute_counterfactual=False,
single symbol, target weight 1.0, annualization_factor=365 (immaterial,
stated because it has no default).
"""

import numpy as np
import pandas as pd
import pytest

from backtest.engine import _select_execution_price_frame, _step_period, run_backtest
from backtest.models import BacktestConfig, MarketData, StrategyOutput

from helpers import dates, mask_series, md, single_symbol_frame


def _config(execution_mode="next_open", execution_lag=1):
    return BacktestConfig(
        initial_capital=1_000_000,
        frequency="1d",
        fee_bps=0,
        slippage_bps=0,
        execution_mode=execution_mode,
        execution_lag=execution_lag,
        funding_mode="disabled",
        annualization_factor=365,
        compute_counterfactual=False,
    )


def test_E_execution_lag_1_final_nav():
    idx = dates(6)
    open_ = [100, 100, 100, 200, 200, 200]
    prices = single_symbol_frame(idx, open_)
    weights = single_symbol_frame(idx, [np.nan, np.nan, 1.0, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(execution_lag=1), md(prices), so)
    assert res.equity_curve.iloc[-1] == pytest.approx(1_000_000.0, rel=1e-12)


def test_E_execution_lag_0_final_nav():
    """# LOWER-LEVEL HELPER - not a production config (execution_lag=0 is forbidden
    by §4.2 in a production BacktestConfig; this test exercises §6.0 Steps 1-12
    directly via _step_period, per §18.0.2)."""
    idx = dates(6)
    open_ = [100, 100, 100, 200, 200, 200]
    quantity_prev = np.array([0.0])
    NAV_pre = 1_000_000.0
    symbols = ["A"]
    for i in range(5):
        rebalance = i == 2
        w_target = np.array([1.0]) if rebalance else None
        P_i = np.array([float(open_[i])])
        P_ip1 = np.array([float(open_[i + 1])])
        r = _step_period(
            i=i,
            rebalance=rebalance,
            w_target=w_target,
            quantity_prev=quantity_prev,
            P_i=P_i,
            P_ip1=P_ip1,
            NAV_pre=NAV_pre,
            fee_bps=0.0,
            slippage_bps=0.0,
            symbols=symbols,
            timestamp_i=idx[i],
            timestamp_ip1=idx[i + 1],
        )
        quantity_prev = r.quantity
        NAV_pre = r.nav_end
    assert NAV_pre == pytest.approx(2_000_000.0, rel=1e-12)


def test_E2_execution_mode_discrimination():
    """BD-2 — §4.2's execution price series. A single MarketData object
    carries BOTH genuinely different open and close series; the SAME object
    is fed to both runs below. If the engine ignored `execution_mode` and
    always read (say) `open`, both runs would land on the SAME final NAV
    (2_000_000.0) and this test would go red on the `next_close` assertion.
    """
    idx = dates(6)
    weights = single_symbol_frame(idx, [np.nan, np.nan, 1.0, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)

    open_ = [100, 100, 100, 100, 200, 200]
    close_ = [100, 100, 100, 200, 200, 200]
    data = MarketData(open=single_symbol_frame(idx, open_), close=single_symbol_frame(idx, close_))

    res_open = run_backtest(_config(execution_mode="next_open", execution_lag=1), data, so)
    assert res_open.equity_curve.iloc[-1] == pytest.approx(2_000_000.0, rel=1e-12)

    res_close = run_backtest(_config(execution_mode="next_close", execution_lag=1), data, so)
    assert res_close.equity_curve.iloc[-1] == pytest.approx(1_000_000.0, rel=1e-12)


def test_select_execution_price_frame_unit():
    """§4.2 — `_select_execution_price_frame` is an explicit, separately
    tested function: P = open if next_open, P = close if next_close, and an
    unknown mode raises ConfigError."""
    from backtest.models import ConfigError

    idx = dates(3)
    open_df = single_symbol_frame(idx, [1.0, 2.0, 3.0])
    close_df = single_symbol_frame(idx, [4.0, 5.0, 6.0])
    data = MarketData(open=open_df, close=close_df)

    assert _select_execution_price_frame(data, "next_open") is open_df
    assert _select_execution_price_frame(data, "next_close") is close_df
    with pytest.raises(ConfigError):
        _select_execution_price_frame(data, "prev_close")


def test_market_data_mismatched_index_or_columns_raises():
    """BD-2 — MarketData.open and MarketData.close MUST share an identical
    index and identical columns, else DataIntegrityError."""
    from backtest.models import DataIntegrityError

    idx = dates(3)
    idx_other = dates(3, freq="1D") + pd.Timedelta(hours=1)
    open_df = single_symbol_frame(idx, [1.0, 2.0, 3.0])
    close_mismatched_index = single_symbol_frame(idx_other, [1.0, 2.0, 3.0])
    weights = single_symbol_frame(idx, [np.nan] * 3)
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)

    with pytest.raises(DataIntegrityError):
        run_backtest(_config(), MarketData(open=open_df, close=close_mismatched_index), so)

    close_mismatched_cols = pd.DataFrame({"B": [1.0, 2.0, 3.0]}, index=idx)
    with pytest.raises(DataIntegrityError):
        run_backtest(_config(), MarketData(open=open_df, close=close_mismatched_cols), so)


def test_E3_execution_lag_0_lower_level_helper():
    """# LOWER-LEVEL HELPER - not a production config (§4.2, §18.0.2)."""
    idx = dates(7)
    open_ = [100, 100, 100, 200, 400, 400, 400]
    quantity_prev = np.array([0.0])
    NAV_pre = 1_000_000.0
    symbols = ["A"]
    for i in range(6):
        rebalance = i == 2
        w_target = np.array([1.0]) if rebalance else None
        r = _step_period(
            i=i,
            rebalance=rebalance,
            w_target=w_target,
            quantity_prev=quantity_prev,
            P_i=np.array([float(open_[i])]),
            P_ip1=np.array([float(open_[i + 1])]),
            NAV_pre=NAV_pre,
            fee_bps=0.0,
            slippage_bps=0.0,
            symbols=symbols,
            timestamp_i=idx[i],
            timestamp_ip1=idx[i + 1],
        )
        quantity_prev = r.quantity
        NAV_pre = r.nav_end
    assert NAV_pre == pytest.approx(4_000_000.0, rel=1e-12)


def test_E3_execution_lag_1_final_nav():
    idx = dates(7)
    open_ = [100, 100, 100, 200, 400, 400, 400]
    prices = single_symbol_frame(idx, open_)
    weights = single_symbol_frame(idx, [np.nan, np.nan, 1.0] + [np.nan] * 4)
    mask = mask_series(idx, [2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(execution_lag=1), md(prices), so)
    assert res.equity_curve.iloc[-1] == pytest.approx(2_000_000.0, rel=1e-12)


def test_E3_execution_lag_2_final_nav():
    idx = dates(7)
    open_ = [100, 100, 100, 200, 400, 400, 400]
    prices = single_symbol_frame(idx, open_)
    weights = single_symbol_frame(idx, [np.nan, np.nan, 1.0] + [np.nan] * 4)
    mask = mask_series(idx, [2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(execution_lag=2), md(prices), so)
    assert res.equity_curve.iloc[-1] == pytest.approx(1_000_000.0, rel=1e-12)


def test_E4_later_price_perturbation_leaves_earlier_periods_bit_identical():
    """B3: E4 carries no explicit config header in the contract; its assertion
    holds under any config, so it reuses E's fully-specified config block."""
    idx = dates(6)
    open_ = [100.0, 105.0, 98.0, 200.0, 210.0, 220.0]
    prices = single_symbol_frame(idx, open_)
    weights = single_symbol_frame(idx, [np.nan, np.nan, 1.0, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _config(execution_lag=1)

    res_base = run_backtest(cfg, md(prices), so)

    # Perturb P[5] (the last bar), strictly after every P[i+1] used by periods
    # 0..3 (which only ever read P[0..4]). Only period 4 (spanning P[4]->P[5])
    # may legitimately differ.
    open_perturbed = list(open_)
    open_perturbed[5] = 999.0
    prices_perturbed = single_symbol_frame(idx, open_perturbed)
    res_perturbed = run_backtest(cfg, md(prices_perturbed), so)

    for i in range(4):
        assert res_base.net_return.iloc[i] == res_perturbed.net_return.iloc[i]
    for k in range(5):
        assert res_base.equity_curve.iloc[k] == res_perturbed.equity_curve.iloc[k]

    # Sanity: the perturbation must actually be visible somewhere, else this
    # test would pass vacuously (e.g. under a broken engine that ignores P[5]
    # entirely). Period 4 must differ.
    assert res_base.net_return.iloc[4] != res_perturbed.net_return.iloc[4]
