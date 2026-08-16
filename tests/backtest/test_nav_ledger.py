"""§18.4 — NAV ledger consistency: N1t, N2t, N3t, N5t, N6t, N7t.

Config (all): initial_capital=1_000_000, frequency="1d", execution_mode="next_open",
execution_lag=1, compute_counterfactual=False, annualization_factor=365.
Cost and funding settings per test.
"""

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig, FundingCoverage, FundingEvent, StrategyOutput

from helpers import TOL_DECOMP, TOL_EQUITY, TOL_WEIGHT, dates, mask_series, md, single_symbol_frame


def test_N1t_funding_alone_changes_nav_and_next_period_w_pre_matches():
    """N1t (§18.4): hand-computed case where funding alone changes NAV
    (flat prices -> `asset_pnl_cash == 0`); the NEXT period's `w_pre`
    (`res.pre_trade_weights`, BD-7) reflects the funding-adjusted
    `NAV_pre` denominator.

    Fixture: 4 bars, single symbol, target 1.0 at bar 0 (execution i=1).
    One funding event at T_1, rate -0.02 (long receives).

        NAV_pre[1]        = 1_000_000.0                    (no fee/slippage)
        quantity[1]       = 1.0 * 1_000_000 / 100 = 10_000.0
        funding_pnl[1]    = -(quantity[1]*P[1] * -0.02) = -(1_000_000*-0.02) = +20_000.0
        NAV_end[1]        = 1_000_000 + 0 + 20_000 = 1_020_000.0   (== equity_curve[2])
        period 2 is a non-rebalance HELD period: quantity[2] = quantity[1] (carry).
        w_pre[2] = quantity[1] * P[2] / NAV_pre[2] = 10_000*100 / 1_020_000
                 = 0.9803921568627451

    Must fail if funding is omitted from NAV_end: NAV_pre[2] would then be
    1_000_000.0 and w_pre[2] would be exactly 1.0 instead -- clearly
    discriminated. Also discriminates `pre_trade_rows.append(np.zeros(...))`
    (BD-7): the hand-computed value here is nonzero.
    """
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [FundingEvent(timestamp=idx[1], symbol="A", funding_rate=-0.02, notional_price=None)]
    coverage = [
        FundingCoverage(
            symbol="A", coverage_start=idx[1], coverage_end=idx[3],
            max_funding_gap=pd.Timedelta(days=2), source_venue="test",
        )
    ]
    cfg = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", execution_mode="next_open", execution_lag=1,
        fee_bps=0, slippage_bps=0, funding_mode="required", funding_notional_basis="period_start",
        compute_counterfactual=False, annualization_factor=365,
    )
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)
    # Flat prices -> asset_pnl_cash == 0, so funding alone must move NAV_end.
    assert res.asset_pnl_cash.iloc[1] == 0.0
    assert res.funding_pnl_cash.iloc[1] == pytest.approx(20_000.0, rel=1e-12)  # -(1e6*-0.02)
    assert res.equity_curve.iloc[2] == pytest.approx(1_020_000.0, **TOL_EQUITY)
    # An engine that omits funding from NAV_end would report equity[2]==1e6.
    assert res.equity_curve.iloc[2] != pytest.approx(1_000_000.0, rel=1e-9)

    w_pre_2_hand = 10_000.0 * 100.0 / 1_020_000.0
    assert w_pre_2_hand == pytest.approx(0.9803921568627451, rel=1e-12)
    assert res.pre_trade_weights["A"].iloc[2] == pytest.approx(w_pre_2_hand, **TOL_WEIGHT)
    # If funding were omitted from NAV_end, w_pre[2] would be exactly 1.0.
    assert res.pre_trade_weights["A"].iloc[2] != pytest.approx(1.0, rel=1e-9)


def test_N2t_fees_and_slippage_alone_change_nav():
    """N2t (§18.4): hand-computed case where fees/slippage alone change NAV;
    the NEXT period's `w_pre` (`res.pre_trade_weights`, BD-7) is asserted
    against a literal hand computation, not merely shape.

    Fixture: 4 bars, single symbol, target 1.0 at bar 0 (execution i=1),
    `fee_bps = slippage_bps = 10`, funding disabled.

        turnover[1]       = 1.0 ,  fee_cost[1] = slippage_cost[1] = 1_000.0
        NAV_after_cost[1] = 1_000_000 - 1_000 - 1_000 = 998_000.0
        quantity[1]       = 1.0 * 998_000 / 100 = 9_980.0
        NAV_end[1]        = 998_000.0  (flat prices, no funding: asset_pnl = 0)
        period 2 is HELD, non-rebalance: quantity[2] = quantity[1] (carry).
        w_pre[2] = quantity[1] * P[2] / NAV_pre[2] = 9_980*100 / 998_000 = 1.0

    (This equals 1.0 exactly because, under flat prices with no funding,
    `w_pre` at the following period is mathematically identical to
    `w_held` at the rebalance point regardless of the fee/slippage bps
    actually applied -- §5.1/§5.2's Step-1/Step-6 pairing guarantees the
    ratio's numerator and denominator scale together. This assertion still
    discriminates `pre_trade_rows.append(np.zeros(...))` (BD-7): the
    correct value 1.0 is nonzero.)
    """
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", execution_mode="next_open", execution_lag=1,
        fee_bps=10, slippage_bps=10, funding_mode="disabled",
        compute_counterfactual=False, annualization_factor=365,
    )
    res = run_backtest(cfg, md(prices), so)
    assert res.asset_pnl_cash.iloc[1] == 0.0
    assert res.equity_curve.iloc[2] == pytest.approx(998_000.0, **TOL_EQUITY)

    w_pre_2_hand = 9_980.0 * 100.0 / 998_000.0
    assert w_pre_2_hand == pytest.approx(1.0, rel=1e-12)
    assert res.pre_trade_weights["A"].iloc[2] == pytest.approx(w_pre_2_hand, **TOL_WEIGHT)


def _n3t_fixture():
    """§18.4 N3t/N5t/N7t fixture.

    BD-6 — prices are deliberately NOT round numbers. The original
    (100.0, 102.0, ...) fixture happens to round-trip bitwise under
    `equity[k] * (1 + net_return[k]) == equity[k+1]` at all 4 periods, so
    N3t/N5t could never discriminate an engine that maintains a SECOND NAV
    path (§8-forbidden reconstruction from `net_return` instead of
    carrying the Step-9 ledger value). These prices (independently
    verified by direct simulation) do NOT round-trip bitwise at period
    index 3 -- see the assertion immediately below, which pins that
    non-round-tripping property IN the fixture itself so a future edit to
    these prices cannot silently make the fixture go inert again.
    """
    idx = dates(5)
    prices = pd.DataFrame(
        {
            "A": [100.377903, 99.980878, 101.92035, 102.241599, 100.6117],
            "B": [50.545345, 52.561865, 54.076693, 52.94699, 50.974657],
        },
        index=idx,
    )
    weights = pd.DataFrame(
        {"A": [0.7, np.nan, 0.3, np.nan, np.nan], "B": [-0.3, np.nan, -0.5, np.nan, np.nan]},
        index=idx,
    )
    mask = mask_series(idx, [0, 2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    events = [
        FundingEvent(timestamp=idx[1], symbol="A", funding_rate=0.001, notional_price=None),
        FundingEvent(timestamp=idx[2], symbol="A", funding_rate=-0.0005, notional_price=None),
        FundingEvent(timestamp=idx[3], symbol="B", funding_rate=0.0002, notional_price=None),
    ]
    coverage = [
        FundingCoverage(symbol="A", coverage_start=idx[0], coverage_end=idx[4],
                         max_funding_gap=pd.Timedelta(days=4), source_venue="test"),
        FundingCoverage(symbol="B", coverage_start=idx[0], coverage_end=idx[4],
                         max_funding_gap=pd.Timedelta(days=4), source_venue="test"),
    ]
    cfg = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", execution_mode="next_open", execution_lag=1,
        fee_bps=5, slippage_bps=3, funding_mode="required", funding_notional_basis="period_start",
        compute_counterfactual=False, annualization_factor=365,
    )
    res = run_backtest(cfg, md(prices), so, funding_events=events, funding_coverage=coverage)

    # BD-6 (point 2) — pin the fixture's discriminating property IN the
    # builder itself: at least one period must NOT round-trip bitwise under
    # `equity[k] * (1 + net_return[k]) == equity[k+1]`. If a future edit to
    # the prices above makes every period round-trip bitwise again, N3t and
    # N5t silently lose their ability to catch a second-NAV-path mutation
    # (§8-forbidden) -- so THIS assertion must fail loudly first, before
    # N3t/N5t ever run.
    equity = res.equity_curve.to_numpy()
    net_return = res.net_return.to_numpy()
    non_round_tripping = [
        i for i in range(len(net_return))
        if equity[i] * (1.0 + net_return[i]) != equity[i + 1]
    ]
    assert non_round_tripping, (
        "the N3t/N5t fixture round-trips bitwise at every period -- it can "
        "no longer discriminate a second-NAV-path (reconstruction) mutation "
        "(BD-6). Re-derive the fixture with prices that do not round-trip."
    )
    return res


def test_N3t_single_normative_nav_path():
    """N3t (§18.4, EXACT, W-A). Two mandated assertions:

    1. `equity_curve[i]` IS the same stored value as `NAV_pre[i]` (§8) --
       NOT merely the `net_return` recomputation below, which a mutated
       engine maintaining a SECOND NAV path (reconstructing `equity_curve`
       from `net_return` instead of carrying the Step-9 ledger's `NAV_end`)
       can also satisfy whenever the reconstruction happens to round-trip
       bitwise. This is checked by independently reconstructing the
       ledger's `NAV_pre`/`NAV_end` chain via the SAME §6.0 Step-2/Step-9
       arithmetic the engine's internal ledger uses, using only the
       already-independent component series `fee_cost`, `slippage_cost`,
       `asset_pnl_cash`, `funding_pnl_cash` (none of which is touched by a
       mutation confined to the `equity` list's carry) -- NOT by comparing
       `equity_curve` against itself.
    2. `net_return[i] == equity_curve[i+1]/equity_curve[i] - 1` bitwise.

    Must fail if the engine maintains a second NAV path: the fixture is
    built (in `_n3t_fixture`) so that `equity[k]*(1+net_return[k])` does
    NOT equal `equity[k+1]` bitwise at period index 3, so a mutation that
    reconstructs the `equity` list from `net_return` instead of carrying
    `NAV_end` diverges from the independent reconstruction below at that
    period.
    """
    res = _n3t_fixture()
    equity = res.equity_curve.to_numpy()
    net_return = res.net_return.to_numpy()
    fee = res.fee_cost.to_numpy()
    slip = res.slippage_cost.to_numpy()
    asset_pnl = res.asset_pnl_cash.to_numpy()
    funding_pnl = res.funding_pnl_cash.to_numpy()

    nav_pre_independent = equity[0]
    for i in range(len(net_return)):
        # equity_curve[i] IS the same stored value as NAV_pre[i] (§8, W-A) --
        # verified against an INDEPENDENT reconstruction of the ledger, not
        # against equity_curve itself.
        assert equity[i] == nav_pre_independent  # EXACT
        nav_after_cost = nav_pre_independent - fee[i] - slip[i]
        nav_end = nav_after_cost + asset_pnl[i] + funding_pnl[i]
        assert equity[i + 1] == nav_end  # EXACT — equity_curve[i+1] IS NAV_end[i]

        recomputed = equity[i + 1] / equity[i] - 1.0
        assert net_return[i] == recomputed  # EXACT — same stored doubles, single arithmetic path

        nav_pre_independent = nav_end


def test_N5t_equity_reconstruction_check_is_tolerance_not_bitwise():
    res = _n3t_fixture()
    equity = res.equity_curve.to_numpy()
    net_return = res.net_return.to_numpy()
    # W-A / §17 row 1 — this reconstruction is a VALIDATION CHECK, never a
    # definition, and MUST be asserted at tolerance, not bitwise: the N3t
    # fixture is built precisely so this reconstruction does NOT round-trip
    # bitwise at period index 3 (see `_n3t_fixture`'s own guard above).
    for i in range(len(net_return)):
        reconstructed = equity[i] * (1.0 + net_return[i])
        assert reconstructed == pytest.approx(equity[i + 1], **TOL_EQUITY)


def test_N6t_nav_after_cost_matches_linear_formula():
    """BT-5 — rebuilt fixture.

    The original test never ran the engine: it called `costs.fee_cost` /
    `costs.slippage_cost` directly and reconstructed `NAV_after_cost` by
    hand, so it could not distinguish the correct Step-2 formula from the
    §20.1-REJECTED self-consistent solve actually implemented in
    `engine.py`. §18.4 N6t states a full `BacktestConfig`, implying an
    engine run: this version derives `NAV_after_cost` from an ACTUAL
    `run_backtest()` result per §10 (`NAV_after_cost[i] == equity_curve[i]
    - fee_cost[i] - slippage_cost[i]`), pinning `NAV_pre = 1_000_000`,
    `turnover = 4` -> `NAV_after_cost == 960_000.0`, and confirms the
    rejected solve `1_000_000 / 1.04 = 961_538.46153846150264` fails.
    """
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0, 100.0, 100.0], "B": [50.0, 50.0, 50.0]}, index=idx)
    weights = pd.DataFrame({"A": [2.0, np.nan, np.nan], "B": [-2.0, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    cfg = BacktestConfig(
        initial_capital=1_000_000, frequency="1d", execution_mode="next_open", execution_lag=1,
        fee_bps=50, slippage_bps=50, funding_mode="disabled",
        compute_counterfactual=False, annualization_factor=365,
    )
    res = run_backtest(cfg, md(prices), so)

    nav_pre = res.equity_curve.iloc[1]
    assert nav_pre == 1_000_000.0  # EXACT — same stored value as NAV_pre[1] (§8)
    assert res.turnover.iloc[1] == pytest.approx(4.0, rel=1e-12)

    nav_after_cost = res.equity_curve.iloc[1] - res.fee_cost.iloc[1] - res.slippage_cost.iloc[1]
    assert nav_after_cost == pytest.approx(960_000.0, rel=1e-12, abs=1e-9)

    # The rejected self-consistent solve differs materially and must fail.
    rejected = nav_pre / 1.04
    assert rejected == pytest.approx(961_538.46153846150264, rel=1e-12)
    assert abs(nav_after_cost - rejected) > 1000.0
    assert nav_after_cost != pytest.approx(rejected, rel=1e-9)

    # NAV_after_cost is not directly exposed on the result surface -- it is
    # only ever CONSUMED, at Step 6, to size the executed quantity
    # (`quantity[i,j] = w_target[i,j] * NAV_after_cost[i] / P[i,j]`). The
    # subtraction-based `nav_after_cost` above is a §10-derived quantity
    # that does not depend on which formula the engine used internally, so
    # it alone cannot discriminate a Step-2 defect. This assertion on the
    # ACTUAL sized quantity closes that gap: under the correct formula
    # quantity["A"] == 19_200.0; under the rejected self-consistent solve it
    # would be 19_230.76923076923 instead.
    # BD-21 — quantity is a derived (Step-6-sized) value -> §17 "reconstructed
    # / derived weights, exposures" row (rtol=1e-12, atol=1e-15), tightened
    # from `rel=1e-9`.
    assert res.quantity["A"].iloc[1] == pytest.approx(19_200.0, **TOL_WEIGHT)
    rejected_quantity_A = 2.0 * rejected / 100.0
    assert res.quantity["A"].iloc[1] != pytest.approx(rejected_quantity_A, rel=1e-9)


def test_N7t_decomposition_identity_holds_on_all_non_ruin_periods():
    res = _n3t_fixture()
    gross = res.gross_return.to_numpy()
    fee = res.fee_return.to_numpy()
    slip = res.slippage_return.to_numpy()
    funding = res.funding_return.to_numpy()
    net = res.net_return.to_numpy()
    for i in range(len(net)):
        total = gross[i] + fee[i] + slip[i] + funding[i]
        # S-5 — decomposition identity (D), §17 row 2 -> `TOL_DECOMP`
        # (rtol=1e-12, atol=1e-15), spelled via the shared constant instead
        # of inline.
        assert total == pytest.approx(net[i], **TOL_DECOMP)
