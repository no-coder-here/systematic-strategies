"""§18.5 — Funding: F1, F2, F4, F5, F6, F7, F8, F9, F10, F12, F13, F14, F15,
F16, F17, F18, F19, F20, F21, F22(a)(b)(c).

Config (all): initial_capital=1_000_000, execution_mode="next_open",
execution_lag=1, fee_bps=0, slippage_bps=0, funding_mode="required",
funding_notional_basis="period_start", compute_counterfactual=False,
annualization_factor=365. `frequency` and any override stated per test.
"""

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import (
    BacktestConfig,
    FundingCoverage,
    FundingDataError,
    FundingEvent,
    DataIntegrityError,
    StrategyOutput,
)

from helpers import dates, mask_series, md, md_both, single_symbol_frame


def _config(frequency="1d", funding_notional_basis="period_start", **overrides):
    kwargs = dict(
        initial_capital=1_000_000,
        frequency=frequency,
        execution_mode="next_open",
        execution_lag=1,
        fee_bps=0,
        slippage_bps=0,
        funding_mode="required",
        funding_notional_basis=funding_notional_basis,
        compute_counterfactual=False,
        annualization_factor=365,
    )
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


def _long_one_symbol_fixture(n_bars, freq="1D"):
    idx = dates(n_bars, freq=freq)
    prices = single_symbol_frame(idx, [100.0] * n_bars)
    weights = single_symbol_frame(idx, [1.0] + [np.nan] * (n_bars - 1))
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    return idx, prices, so


def test_F1_24_hourly_events_aggregate_inside_one_1d_bar():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1D")
    # Period 1 spans [T_1, T_2) = 24 hours; put 24 hourly events of rate 0.001 each.
    hour_starts = pd.date_range(idx[1], periods=24, freq="1h", tz="UTC")
    events = [FundingEvent(timestamp=t, symbol="A", funding_rate=0.001, notional_price=None) for t in hour_starts]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(hours=1), source_venue="t")]
    cfg = _config(frequency="1d")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    q = res.quantity["A"].iloc[1]
    expected = -sum(q * 100.0 * 0.001 for _ in range(24))
    assert res.funding_pnl_cash.iloc[1] == pytest.approx(expected, rel=1e-12)


def test_F2_boundary_event_counted_once_in_later_period():
    """BT-2 — rebuilt fixture.

    The original fixture put a boundary event at T_1 while the position
    only existed from period 1 onward, so period 0 held ZERO quantity: no
    charge could ever land there under ANY window convention (open, closed,
    right-inclusive or not), so the old `funding_pnl_cash.iloc[0] == 0.0`
    assertion could not discriminate a right-inclusive-window defect.

    Here the position is established at bar 0 (executes at i=1, ENTERING),
    so quantity is nonzero in BOTH period 1 (spanning [T1, T2)) and period 2
    (spanning [T2, T3)). A single event sits exactly on the T2 boundary.
    Correct half-open semantics charge it to period 2 ONLY: period 1 must be
    a genuine zero (nonzero exposure, zero events in its window), not a
    zero-by-vacuity. A right-inclusive-window defect (event at the boundary
    also consumed by the period ENDING there) would double-charge it into
    period 1 too, making `funding_pnl_cash.iloc[1] != 0.0` and this test go
    red.
    """
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0] * 4)
    weights = single_symbol_frame(idx, [1.0] + [np.nan] * 3)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    # Event exactly at T_2 (boundary between period 1 and period 2), while a
    # nonzero position is already held through BOTH periods.
    events = [FundingEvent(timestamp=idx[2], symbol="A", funding_rate=0.01, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[3],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _config(frequency="1d")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    assert res.quantity["A"].iloc[1] != 0.0  # exposure already exists in period 1
    assert res.quantity["A"].iloc[2] != 0.0  # and continues into period 2
    assert res.funding_pnl_cash.iloc[1] == 0.0  # EXACT — genuine zero, no events in [T1, T2)
    assert res.funding_pnl_cash.iloc[2] != 0.0  # the boundary event lands here only


def test_F4_missing_funding_data_for_funding_accruing_period_raises():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1D")
    cfg = _config(frequency="1d")
    with pytest.raises(FundingDataError):
        run_backtest(cfg, md(prices), so, funding_events=[], funding_coverage=[])


def test_F5_funding_disabled_exactly_zero():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1D")
    cfg = _config(frequency="1d", funding_mode="disabled", funding_notional_basis=None)
    res = run_backtest(cfg, md(prices), so)
    assert (res.funding_pnl_cash.to_numpy() == 0.0).all()
    assert res.funding_modelled is False
    assert res.funding_notional_basis == "not_modelled"


def test_F6_funding_sign_convention():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0] * 3)
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.01, notional_price=None)]

    # Long + positive rate -> negative funding PnL.
    weights_long = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    so_long = StrategyOutput(target_weights=weights_long, rebalance_mask=mask_series(idx, [0]))
    res_long = run_backtest(_config(), md(prices), so_long, funding_events=events, funding_coverage=coverage)
    assert res_long.funding_pnl_cash.iloc[1] < 0.0

    # Short + positive rate -> positive funding PnL.
    weights_short = single_symbol_frame(idx, [-1.0, np.nan, np.nan])
    so_short = StrategyOutput(target_weights=weights_short, rebalance_mask=mask_series(idx, [0]))
    res_short = run_backtest(_config(), md(prices), so_short, funding_events=events, funding_coverage=coverage)
    assert res_short.funding_pnl_cash.iloc[1] > 0.0


def test_F7_irregular_event_spacing_within_tolerance_aggregates_correctly():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1D")
    events = [
        FundingEvent(timestamp=idx[1] + pd.Timedelta(hours=1), symbol="A", funding_rate=0.001, notional_price=None),
        FundingEvent(timestamp=idx[1] + pd.Timedelta(hours=5), symbol="A", funding_rate=0.002, notional_price=None),
        FundingEvent(timestamp=idx[1] + pd.Timedelta(hours=20), symbol="A", funding_rate=-0.0005, notional_price=None),
    ]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(hours=20), source_venue="t")]
    cfg = _config(frequency="1d")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    q = res.quantity["A"].iloc[1]
    expected = -(q * 100.0 * 0.001 + q * 100.0 * 0.002 + q * 100.0 * -0.0005)
    assert res.funding_pnl_cash.iloc[1] == pytest.approx(expected, rel=1e-12)


def test_F8_next_open_vs_next_close_produce_different_funding_windows():
    idx = dates(3, freq="1D")
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    # One 10x rate hourly event right at the open-mode boundary (T_1 for next_open)
    # vs the close-mode boundary (T_1 + delta for next_close): these windows differ.
    event_ts = idx[1] + pd.Timedelta(hours=12)
    events = [FundingEvent(timestamp=event_ts, symbol="A", funding_rate=0.05, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[2] + pd.Timedelta(days=1),
                                 max_funding_gap=pd.Timedelta(days=2), source_venue="t")]

    # BD-23 — this test genuinely needs BOTH `open` and `close` populated
    # with the SAME prices: it is testing the funding-window discrimination
    # between execution modes, not §4.2 price-series selection itself, so
    # it uses the explicit non-poisoned constructor rather than `md()`.
    data = md_both(prices)

    cfg_open = _config(frequency="1d", execution_mode="next_open")
    res_open = run_backtest(cfg_open, data, so, funding_events=events, funding_coverage=coverage)

    cfg_close = _config(frequency="1d", execution_mode="next_close")
    res_close = run_backtest(cfg_close, data, so, funding_events=events, funding_coverage=coverage)

    assert res_open.funding_pnl_cash.iloc[1] != pytest.approx(res_close.funding_pnl_cash.iloc[1])


def test_F9_complete_8h_stream_max_gap_8h_does_not_raise():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1h")
    event_times = pd.date_range(idx[0], idx[-1], freq="8h", tz="UTC")
    events = [FundingEvent(timestamp=t, symbol="A", funding_rate=0.0001, notional_price=None) for t in event_times]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[-1],
                                 max_funding_gap=pd.Timedelta(hours=8), source_venue="t")]
    cfg = _config(frequency="1h")
    run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)  # must not raise


def test_F10_events_before_T0_or_at_after_Tn1_excluded_and_counted():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1D")
    events = [
        FundingEvent(timestamp=idx[0] - pd.Timedelta(days=5), symbol="A", funding_rate=0.001, notional_price=None),
        FundingEvent(timestamp=idx[2] + pd.Timedelta(days=5), symbol="A", funding_rate=0.001, notional_price=None),
        FundingEvent(timestamp=idx[2], symbol="A", funding_rate=0.001, notional_price=None),  # at T_{n-1}: excluded
        FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.001, notional_price=None),  # in-range
    ]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _config(frequency="1d")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    assert res.funding_events_excluded == 3


def test_F12_funding_valued_on_post_trade_quantity_on_a_rebalance_period():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    # Rebalance at bar0 (from flat 0 to +1); the executing period i=1 is itself
    # a rebalance period, and funding there must use the NEW post-trade quantity.
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=-0.01, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _config(frequency="1d")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    q_post_trade = res.quantity["A"].iloc[1]
    assert q_post_trade != 0.0
    expected = -(q_post_trade * 100.0 * -0.01)
    assert res.funding_pnl_cash.iloc[1] == pytest.approx(expected, rel=1e-12)


def test_F13_gap_exceeding_max_funding_gap_raises():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1h")
    events = [FundingEvent(timestamp=idx[0], symbol="A", funding_rate=0.0001, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[-1],
                                 max_funding_gap=pd.Timedelta(hours=1), source_venue="t")]
    cfg = _config(frequency="1h")
    with pytest.raises(FundingDataError):
        run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)


def test_F14_symbol_with_no_funding_accruing_period_needs_no_data():
    idx = dates(3, freq="1D")
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [50.0, 50.0, 50.0]}, index=idx)
    # Only A is ever traded; B is never named -> always INACTIVE -> no funding data needed.
    weights = pd.DataFrame({"A": [1.0, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.0, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _config(frequency="1d")
    run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)  # must not raise


def test_F15_events_only_outside_coverage_window_raises():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1h")
    events = [
        FundingEvent(timestamp=pd.Timestamp("2025-01-01", tz="UTC"), symbol="A", funding_rate=0.0001, notional_price=None),
        FundingEvent(timestamp=pd.Timestamp("2027-01-01", tz="UTC"), symbol="A", funding_rate=0.0001, notional_price=None),
    ]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[-1],
                                 max_funding_gap=pd.Timedelta(hours=1), source_venue="t")]
    cfg = _config(frequency="1h")
    with pytest.raises(FundingDataError):
        run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)


def test_F16_two_disjoint_non_touching_coverage_records_do_not_raise():
    # Non-contiguous EXPOSURE: long over period i=1 (uses record 1), flat over
    # periods i=2,3 (INACTIVE, needs no coverage), long again over period i=4
    # (uses record 2). The gap between the two coverage records falls entirely
    # within the INACTIVE stretch, so it is never reached.
    idx = dates(6, freq="1h")
    prices = single_symbol_frame(idx, [100.0] * 6)
    weights = single_symbol_frame(idx, [1.0, 0.0, np.nan, 1.0, np.nan, np.nan])
    mask = mask_series(idx, [0, 1, 3])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [
        FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.0001, notional_price=None),
        FundingEvent(timestamp=idx[4], symbol="A", funding_rate=0.0001, notional_price=None),
    ]
    coverage = [
        FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[2],
                         max_funding_gap=pd.Timedelta(hours=1), source_venue="t"),
        FundingCoverage(symbol="A", coverage_start=idx[3], coverage_end=idx[-1],
                         max_funding_gap=pd.Timedelta(hours=1), source_venue="t"),
    ]
    cfg = _config(frequency="1h")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)  # must not raise
    assert res.quantity["A"].iloc[2] == 0.0  # confirms the INACTIVE gap actually occurred
    assert res.quantity["A"].iloc[4] != 0.0


def test_F17_gap_exactly_equal_to_max_funding_gap_accepted():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1h")
    events = [FundingEvent(timestamp=idx[0], symbol="A", funding_rate=0.0001, notional_price=None),
              FundingEvent(timestamp=idx[-1], symbol="A", funding_rate=0.0001, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[-1],
                                 max_funding_gap=idx[-1] - idx[0], source_venue="t")]
    cfg = _config(frequency="1h")
    run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)  # must not raise


def test_F18_event_price_basis_missing_notional_price_raises():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1D")
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.001, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _config(frequency="1d", funding_notional_basis="event_price")
    with pytest.raises(FundingDataError):
        run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)


def test_F19_period_start_basis_ignores_invalid_notional_price():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1D")
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.001, notional_price=-999.0)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _config(frequency="1d", funding_notional_basis="period_start")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)  # must not raise
    q = res.quantity["A"].iloc[1]
    expected = -(q * 100.0 * 0.001)  # uses P[i], NOT the invalid notional_price
    assert res.funding_pnl_cash.iloc[1] == pytest.approx(expected, rel=1e-12)


def test_F20_gap_tolerance_suspicious_flag_without_raising():
    idx, prices, so = _long_one_symbol_fixture(3, freq="1h")
    events = [FundingEvent(timestamp=t, symbol="A", funding_rate=0.0001, notional_price=None)
              for t in pd.date_range(idx[0], idx[-1], freq="1h", tz="UTC")]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[-1],
                                 max_funding_gap=pd.Timedelta(hours=8), source_venue="t")]
    cfg = _config(frequency="1h")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    assert res.funding_gap_tolerance_suspicious is True


def test_F21_touching_or_overlapping_coverage_records_raise():
    idx = dates(3, freq="1D")
    coverage_touching = [
        FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[1],
                         max_funding_gap=pd.Timedelta(days=1), source_venue="t"),
        FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                         max_funding_gap=pd.Timedelta(days=1), source_venue="t"),
    ]
    idx2, prices, so = _long_one_symbol_fixture(3, freq="1D")
    cfg = _config(frequency="1d")
    with pytest.raises(DataIntegrityError):
        run_backtest(cfg, md(prices), so, funding_events=[], funding_coverage=coverage_touching)


def _f22_fixture():
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 60.0])
    weights = single_symbol_frame(idx, [1.0, -4.0, np.nan, np.nan])
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", execution_mode="next_open", execution_lag=1,
        fee_bps=1500, slippage_bps=1500, funding_mode="required", funding_notional_basis="period_start",
        compute_counterfactual=False, annualization_factor=365,
    )
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.0, notional_price=None)]
    return idx, prices, so, cfg, events


def test_F22a_cost_stage_ruin_before_unreached_interval_does_not_raise():
    idx, prices, so, cfg, events = _f22_fixture()
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)  # must not raise


def test_F22b_run_completes_cost_stage_ruin():
    idx, prices, so, cfg, events = _f22_fixture()
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    assert res.ruined is True
    assert res.ruin_stage == "cost"


def test_F22c_reached_uncovered_interval_raises():
    idx, prices, so, cfg, events = _f22_fixture()
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[1],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    with pytest.raises(FundingDataError):
        run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
