"""§18.7 — Counterfactual: CF1, CF2, CF2c, CF3, CF4, CF5, CF6, CF7, CF8, CF9,
CF10, CF11.

Config (all): initial_capital=1_000_000, frequency="1d", execution_mode="next_open",
execution_lag=1, annualization_factor=365, compute_counterfactual=True.
Costs and funding per fixture.
"""

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import (
    BacktestConfig,
    FundingCoverage,
    FundingEvent,
    InvalidPriceError,
    StrategyOutput,
)

from helpers import TOL_EQUITY, TOL_METRIC, TOL_WEIGHT, dates, mask_series, md, single_symbol_frame


def _base_config(**overrides):
    kwargs = dict(
        initial_capital=1_000_000,
        frequency="1d",
        execution_mode="next_open",
        execution_lag=1,
        annualization_factor=365,
        compute_counterfactual=True,
    )
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


def _cf2a():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 110.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=10, slippage_bps=10, funding_mode="disabled")
    return run_backtest(cfg, md(prices), so)


def _cf2b():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=-0.01, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _base_config(fee_bps=0, slippage_bps=0, funding_mode="required", funding_notional_basis="period_start")
    return run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)


def _cf3():
    idx = dates(4)
    prices = pd.DataFrame({"A": [100.0, 110.0, 121.0, 133.1], "B": [50.0, 45.0, 40.0, 36.0]}, index=idx)
    weights = pd.DataFrame({"A": [0.6, 0.6, np.nan, np.nan], "B": [-0.4, -0.4, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=50, slippage_bps=50, funding_mode="disabled")
    return run_backtest(cfg, md(prices), so)


def _cf7():
    idx = dates(5)
    prices = single_symbol_frame(idx, [100.0, 100.0, 60.0, 60.0, 60.0])
    weights = single_symbol_frame(idx, [2.5, np.nan, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [
        FundingEvent(timestamp=idx[1], symbol="A", funding_rate=-0.09, notional_price=None),
        FundingEvent(timestamp=idx[2], symbol="A", funding_rate=-0.09, notional_price=None),
        FundingEvent(timestamp=idx[3], symbol="A", funding_rate=-0.09, notional_price=None),
    ]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[4],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _base_config(fee_bps=0, slippage_bps=0, funding_mode="required", funding_notional_basis="period_start")
    return run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)


def test_CF1_counterfactual_equals_actual_at_zero_cost():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 105.0, 98.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=0, slippage_bps=0, funding_mode="disabled")
    res = run_backtest(cfg, md(prices), so)
    assert res.counterfactual_gross_equity.tolist() == res.equity_curve.tolist()  # EXACT


def test_CF2_total_drag_return_values_and_signs():
    # BD-21 — `total_drag_return` is a DRAG STATISTIC (§9.2), governed by
    # §17's "metrics, drag statistics" row: rtol=1e-10, atol=1e-12. The
    # previous `rel=1e-6` (CF2a) / `rel=1e-9` (CF2b) were both looser than
    # this row mandates.
    res_a = _cf2a()
    assert res_a.total_drag_return == pytest.approx(0.0022, **TOL_METRIC)
    assert res_a.total_drag_return > 0.0
    assert res_a.drag_comparable is True
    assert res_a.counterfactual_status == "COMPLETED"

    res_b = _cf2b()
    assert res_b.total_drag_return == pytest.approx(-0.01, **TOL_METRIC)
    assert res_b.total_drag_return < 0.0
    assert res_b.drag_comparable is True
    assert res_b.counterfactual_status == "COMPLETED"


def test_CF2c_cf3_total_drag_return_discriminates_naive_summation():
    res = _cf3()
    # BD-21 — §17 metrics/drag row, tightened from `rel=1e-9`.
    assert res.total_drag_return == pytest.approx(0.013116888888888845, **TOL_METRIC)
    naive = 0.010804828973843059
    assert abs(res.total_drag_return - naive) / abs(naive) > 0.15  # 17.63% miss


def test_CF3_cumprod_gross_return_not_equal_counterfactual_equity():
    res = _cf3()
    gross = res.gross_return.to_numpy()
    cumprod_equity = 1_000_000.0 * np.cumprod(1.0 + gross)
    # Both are USD ledger/equity values -- §17 "equity reconstruction check"
    # row (rtol=1e-12, atol=1e-9 USD), tightened from `rel=1e-9`.
    assert cumprod_equity[-1] == pytest.approx(1_213_651.1951710260, **TOL_EQUITY)
    assert res.counterfactual_gross_equity.iloc[-1] == pytest.approx(1_214_888.888888889, **TOL_EQUITY)
    # This is a qualitative "these two paths genuinely differ" check, not a
    # §17-classified numeric equality -- retained at a looser tolerance is
    # fine for a `!=`, but tightening it further only makes the inequality
    # easier to satisfy, so it is tightened too for consistency.
    assert cumprod_equity[-1] != pytest.approx(res.counterfactual_gross_equity.iloc[-1], **TOL_EQUITY)


def test_CF4_hand_computed_three_period_counterfactual():
    """CF4 — each pinned literal is asserted at the §17 row that actually
    governs ITS quantity, re-derived individually (BD-21) rather than
    applying one blanket tolerance to the whole fixture:

    - `equity_curve` / `counterfactual_gross_equity`: USD ledger values ->
      "equity reconstruction check" row (rtol=1e-12, atol=1e-9 USD).
    - `gross_return[0]`: an ASSIGNED literal (no active position at period
      0, §5.3/§6.7.2) -> EXACT.
    - `gross_return[1]`: W-3 (round-4 audit) -- §18.0.1 classifies CF4
      TOLERANCE | multi-step. `gross_return[1]` is a TWO-SYMBOL sum of
      products (`quantity_A*(P_A[2]-P_A[1]) + quantity_B*(P_B[2]-P_B[1])`)
      divided by `NAV_pre` -- not "exactly representable and reached by a
      single arithmetic path" (§17's EXACT carve-out), and `0.1034` is not
      itself exactly representable in binary. The previous EXACT assertion
      here was a round-3 defect (the sixth consecutive round with this
      defect class) -> "reconstructed / derived weights" row (rtol=1e-12,
      atol=1e-15), same as `gross_return[2]` below.
    - `gross_return[2]`: a full-double-precision literal -> "reconstructed /
      derived weights" row (rtol=1e-12, atol=1e-15).
    - `total_return` / `counterfactual_total_return`: metrics fields ->
      "metrics, drag statistics" row (rtol=1e-10, atol=1e-12).
    """
    res = _cf3()
    equity = res.equity_curve.tolist()
    expected_equity = [1_000_000.0, 1_000_000.0, 1_093_400.0, 1_201_772.0]
    for actual, expected in zip(equity, expected_equity):
        assert actual == pytest.approx(expected, **TOL_EQUITY)
    assert res.counterfactual_gross_equity.iloc[2] == pytest.approx(1_104_444.4444444445, **TOL_EQUITY)
    assert res.counterfactual_gross_equity.iloc[3] == pytest.approx(1_214_888.888888889, **TOL_EQUITY)

    assert res.gross_return.iloc[0] == 0.0  # EXACT — assigned literal, no active position
    # W-3 — TOLERANCE, not EXACT (§18.0.1: CF4 is "TOLERANCE | multi-step");
    # a two-symbol sum of products / NAV, not a single arithmetic path.
    assert res.gross_return.iloc[1] == pytest.approx(0.1034, **TOL_WEIGHT)
    assert res.gross_return.iloc[2] == pytest.approx(0.09991951710261567, **TOL_WEIGHT)

    assert res.metrics["total_return"] == pytest.approx(0.20177200000000006, **TOL_METRIC)
    assert res.counterfactual_total_return == pytest.approx(0.21488888888888891, **TOL_METRIC)


def test_CF5_counterfactual_respects_timing_and_mask():
    res = _cf3()
    assert res.counterfactual_gross_equity.iloc[0] == 1_000_000.0
    assert res.counterfactual_gross_equity.iloc[1] == 1_000_000.0  # flat before lag-1 execution
    assert len(res.counterfactual_gross_equity) == len(res.equity_curve)


def test_CF6_actual_ruins_counterfactual_survives():
    """BT-4 — rebuilt fixture.

    The original fixture reused X1, where ruin lands on the FINAL period, so
    BOTH paths keep exactly 3 equity rows and §18.7 CF6's actual requirement
    ("counterfactual retains its full length; actual truncates") was never
    exercised -- the test even asserted `len(equity_curve) == 3` for BOTH.

    This fixture (7 bars, `fee_bps = slippage_bps = 1500`, `W = [1.0, -4.0]`,
    the X9 cost-stage-ruin pattern extended with extra trailing bars) gives
    a genuine length divergence: the actual path cost-stage-ruins at period
    2 (4 equity rows), while the zero-cost counterfactual never ruins there
    and runs the full 6 periods (7 equity rows).
    """
    idx = dates(7)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 60.0, 60.0, 60.0, 60.0])
    weights = single_symbol_frame(idx, [1.0, -4.0, np.nan, np.nan, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=1500, slippage_bps=1500, funding_mode="disabled")
    res = run_backtest(cfg, md(prices), so)
    assert res.ruined is True
    assert res.ruin_stage == "cost"
    assert res.counterfactual_status == "COMPLETED"
    assert len(res.equity_curve) == 4  # actual truncates at the cost-stage ruin
    assert len(res.counterfactual_gross_equity) == 7  # counterfactual retains full length
    assert res.drag_comparable is False
    assert res.total_drag_return is None
    assert res.cagr_drag is None


def test_CF7_counterfactual_ruins_actual_survives():
    res = _cf7()
    assert res.ruined is False
    assert len(res.equity_curve) == 5
    # BD-21 — equity ledger values -> §17 row 1 (rtol=1e-12, atol=1e-9 USD),
    # tightened from `rel=1e-9`.
    assert res.equity_curve.tolist() == pytest.approx(
        [1_000_000, 1_000_000, 225_000.0, 360_000.0, 495_000.0], **TOL_EQUITY
    )
    assert res.counterfactual_ruined is True
    assert res.counterfactual_status == "RUINED"
    assert res.counterfactual_gross_equity.tolist() == pytest.approx([1_000_000, 1_000_000, 0.0], **TOL_EQUITY)
    assert res.total_drag_return is None
    assert res.cagr_drag is None
    assert res.drag_comparable is False


def test_CF8_isolation_invariant_actual_bit_identical_across_compute_counterfactual():
    idx, prices, so = None, None, None
    idx = dates(5)
    prices = single_symbol_frame(idx, [100.0, 100.0, 60.0, 60.0, 60.0])
    weights = single_symbol_frame(idx, [2.5, np.nan, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [
        FundingEvent(timestamp=idx[1], symbol="A", funding_rate=-0.09, notional_price=None),
        FundingEvent(timestamp=idx[2], symbol="A", funding_rate=-0.09, notional_price=None),
        FundingEvent(timestamp=idx[3], symbol="A", funding_rate=-0.09, notional_price=None),
    ]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[4],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]

    cfg_true = _base_config(fee_bps=0, slippage_bps=0, funding_mode="required",
                             funding_notional_basis="period_start", compute_counterfactual=True)
    cfg_false = dataclasses_replace(cfg_true, compute_counterfactual=False)

    res_true = run_backtest(cfg_true, md(prices), so, funding_events=events, funding_coverage=coverage)
    res_false = run_backtest(cfg_false, md(prices), so, funding_events=events, funding_coverage=coverage)

    assert res_true.equity_curve.tolist() == res_false.equity_curve.tolist()
    assert res_true.net_return.tolist() == res_false.net_return.tolist()
    assert res_true.ruined == res_false.ruined
    assert res_false.counterfactual_status == "NOT_COMPUTED"


def dataclasses_replace(cfg, **kwargs):
    import dataclasses

    return dataclasses.replace(cfg, **kwargs)


def test_CF9_exception_isolation_counterfactual_failed_does_not_affect_actual():
    # X9-style cost-stage ruin for the ACTUAL path at period i=2 (mask at bars
    # 0 and 1, huge fee+slippage): Step 5 is never reached there, so the
    # actual path never validates P[3..]. The zero-cost COUNTERFACTUAL never
    # hits that cost-stage ruin (NAV_after_cost == NAV_pre always at zero
    # cost), holds the position established at i=2 onward, and at period i=7
    # needs P[7], which is invalid (0.0) -> InvalidPriceError inside the
    # counterfactual-only barrier.
    idx = dates(11)
    prices_vals = [100.0] * 11
    prices_df = single_symbol_frame(idx, prices_vals)
    prices_df.loc[idx[7], "A"] = 0.0  # invalid while the CF still holds a position there

    weights = single_symbol_frame(idx, [1.0, -4.0] + [np.nan] * 9)
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=1500, slippage_bps=1500, funding_mode="disabled")

    res = run_backtest(cfg, md(prices_df), so)
    assert res.ruined is True
    assert res.ruin_stage == "cost"

    cfg_false = dataclasses_replace(cfg, compute_counterfactual=False)
    res_false = run_backtest(cfg_false, md(prices_df), so)

    assert res.equity_curve.tolist() == res_false.equity_curve.tolist()
    assert res.net_return.tolist() == res_false.net_return.tolist()
    assert res.counterfactual_status == "FAILED"
    assert res.counterfactual_error is not None
    assert res.drag_comparable is False


def test_CF10_different_length_paths_not_drag_comparable():
    res = _cf7()  # counterfactual ruins after 2 periods, actual completes 4
    assert res.drag_comparable is False
    assert res.total_drag_return is None
    assert res.cagr_drag is None


def test_BD13_counterfactual_leverage_breach_fires():
    """BD-13 (§9.4) — `counterfactual_leverage_breach` is never asserted
    anywhere (`= None` survives). §9.4: §6.8's tripwire applies to the
    counterfactual too, reported as `counterfactual_leverage_breach`.
    """
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [100.0, 100.0, 100.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.5, np.nan, np.nan], "B": [-1.5, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=10, slippage_bps=10, funding_mode="disabled", max_gross_leverage=2.0)
    res = run_backtest(cfg, md(prices), so)
    assert res.leverage_breach is True
    assert res.counterfactual_leverage_breach is True

    cfg_no_breach = _base_config(fee_bps=10, slippage_bps=10, funding_mode="disabled", max_gross_leverage=5.0)
    res_no_breach = run_backtest(cfg_no_breach, md(prices), so)
    assert res_no_breach.leverage_breach is False
    assert res_no_breach.counterfactual_leverage_breach is False


# ---------------------------------------------------------------------------
# W-1 (round-4 audit) — §9.2's drag-comparability rule is
#
#     drag_comparable = (
#         cf_status == "COMPLETED"
#         and not actual.ruined
#         and cf_n_periods is not None
#         and cf_n_periods == actual.n_periods
#     )
#
# CF6/CF7/CF10 each violate MULTIPLE of these terms simultaneously, so
# deleting any ONE term individually still leaves the OTHER violated term(s)
# blocking `drag_comparable` -- none of the existing fixtures isolates a
# single precondition. The two tests below each isolate exactly ONE
# precondition: every OTHER precondition holds in that fixture, so deleting
# any clause OTHER than the targeted one leaves the test's assertions
# unchanged, and deleting the targeted clause alone flips `drag_comparable`
# to (wrongly) `True`.
# ---------------------------------------------------------------------------


def test_W1_drag_comparable_isolates_not_actual_ruined():
    """Isolates the `not actual.ruined` clause.

    Auditor-supplied fixture (§18.8 X9's price/target pattern, with
    `compute_counterfactual=True`): the ACTUAL path cost-stage-ruins on its
    FINAL period, so both the actual and the zero-cost counterfactual paths
    run exactly 3 periods -- `cf_status == "COMPLETED"` and
    `cf_n_periods == actual.n_periods` both hold; ONLY `not actual.ruined`
    is violated (`actual.ruined is True`).
    """
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 60.0])
    weights = single_symbol_frame(idx, [1.0, -4.0, np.nan, np.nan])
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=1500, slippage_bps=1500, funding_mode="disabled")
    res = run_backtest(cfg, md(prices), so)

    assert res.ruined is True
    assert res.counterfactual_status == "COMPLETED"
    assert len(res.equity_curve) - 1 == len(res.counterfactual_gross_equity) - 1 == 3
    # Correct behaviour: `not actual.ruined` blocks comparability.
    assert res.drag_comparable is False
    assert res.total_drag_return is None
    assert res.cagr_drag is None
    # Mutation-proof (recorded separately): deleting ONLY `not actual.ruined`
    # from engine.py's `drag_comparable` expression flips this fixture to
    # `drag_comparable == True`, `total_drag_return == 2.6`,
    # `cagr_drag == 3.0791014845685793e+50` -- while deleting either OTHER
    # clause leaves this fixture's result unchanged (both other clauses
    # already hold here).


def test_W1_drag_comparable_isolates_cf_status_completed():
    """Isolates the `cf_status == "COMPLETED"` clause.

    The counterfactual (zero fee/slippage, funding always disabled per
    §9.1) is sized purely off `NAV_pre` (no cost deduction shrinks its
    exposure first), so an identical leveraged price move produces a
    LARGER dollar swing on the counterfactual than on the actual path.
    Here a large short (`target = -10.0`) combined with a price rise from
    100 -> 112 ruins the counterfactual (`NAV_end <= 0`) on the fixture's
    OWN final period -- giving it `cf_n_periods == actual.n_periods` (both
    equal `n_periods_max`, per the same "ruin lands on the final period"
    trick §18.8 X9/X1 rely on) -- while the ACTUAL path receives a funding
    inflow the counterfactual never gets (§9.1: `funding_pnl_cash = 0` for
    all `i` on the counterfactual path), which is calibrated to push its
    own NAV back above zero at that same period. So: `not actual.ruined`
    holds, `cf_n_periods == actual.n_periods` holds; ONLY
    `cf_status == "COMPLETED"` is violated (`cf_status == "RUINED"`).
    """
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 100.0, 112.0])
    weights = single_symbol_frame(idx, [-10.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.025, notional_price=None)]
    coverage = [FundingCoverage(symbol="A", coverage_start=idx[1], coverage_end=idx[2],
                                 max_funding_gap=pd.Timedelta(days=1), source_venue="t")]
    cfg = _base_config(fee_bps=0, slippage_bps=0, funding_mode="required", funding_notional_basis="period_start")
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)

    assert res.ruined is False
    assert res.counterfactual_status == "RUINED"
    assert len(res.equity_curve) - 1 == len(res.counterfactual_gross_equity) - 1 == 2
    # Correct behaviour: `cf_status == "COMPLETED"` blocks comparability.
    assert res.drag_comparable is False
    assert res.total_drag_return is None
    assert res.cagr_drag is None
    # Mutation-proof (recorded separately): deleting ONLY
    # `cf_status == "COMPLETED"` from engine.py's `drag_comparable`
    # expression flips this fixture to `drag_comparable == True`,
    # `total_drag_return == -0.050000000000000044`, `cagr_drag == 0.0` --
    # while deleting either OTHER clause leaves this fixture's result
    # unchanged (both other clauses already hold here).


def test_W1_drag_comparable_third_precondition_is_structurally_implied():
    """SPEC ESCALATION (documented, not silently resolved) -- the remaining
    two terms, `cf_n_periods is not None` and
    `cf_n_periods == actual.n_periods`, cannot be independently isolated by
    ANY fixture reachable through `run_backtest()`, because they are
    PROVABLY implied by the other two terms in the CURRENT engine:

    - both `_simulate()` calls (actual and counterfactual) inside a single
      `run_backtest()` invocation always receive the SAME `n` (hence the
      SAME `n_periods_max = n - 1`) -- see `engine.py`'s two `_simulate(n=n,
      ...)` call sites.
    - `_simulate()`'s loop breaks ONLY on ruin; otherwise it runs the full
      `n_periods_max` iterations. So `actual.ruined == False` IMPLIES
      `actual.n_periods == n_periods_max`, and `cf.ruined == False`
      (`cf_status == "COMPLETED"`) IMPLIES `cf.n_periods == n_periods_max`,
      for the SAME `n_periods_max`.
    - Therefore `cf_status == "COMPLETED" and not actual.ruined` ALREADY
      forces `cf_n_periods == actual.n_periods == n_periods_max`: there is
      no reachable state where the first two clauses hold and the third/
      fourth do not.

    This is verified empirically here (not merely asserted): on BOTH
    isolating fixtures above, `cf_n_periods == actual.n_periods` already
    holds whenever `cf_status == "COMPLETED"` and `not actual.ruined` are
    both true, and the round-3/round-4 audits independently found that
    mutating `cf_n_periods == actual.n_periods` away leaves the full 154-
    (now larger) test suite green under EVERY fixture tried -- consistent
    with it being unreachable, not merely untested. The clause is correctly
    KEPT (defensive, per the round-3 ruling on §9.2 precondition 3) as
    protection against a FUTURE engine change that lets the two `_simulate`
    calls diverge in `n`; it is not a defect in the current engine, and no
    fixture under the current architecture can prove otherwise.
    """
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 105.0, 98.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=0, slippage_bps=0, funding_mode="disabled")
    res = run_backtest(cfg, md(prices), so)

    assert res.ruined is False
    assert res.counterfactual_status == "COMPLETED"
    # The invariant this test documents: whenever both hold, the lengths
    # are ALREADY forced equal (see docstring) -- there is nothing left for
    # the third/fourth clause to independently block.
    assert (len(res.equity_curve) - 1) == (len(res.counterfactual_gross_equity) - 1)
    assert res.drag_comparable is True


def test_CF11_actual_path_data_integrity_error_propagates_not_failed():
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, -50.0, 100.0])  # invalid negative price the ACTUAL path reads
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = _base_config(fee_bps=0, slippage_bps=0, funding_mode="disabled")
    with pytest.raises(InvalidPriceError):
        run_backtest(cfg, md(prices), so)
