"""§18.9 — Symbol activity, boundaries, missing data: S1-S7, V1-V3, T1-T5,
U1-U3, D4t.

Config (all): initial_capital=1_000_000, frequency="1d", execution_mode="next_open",
execution_lag=1, fee_bps=0, slippage_bps=0, funding_mode="disabled",
compute_counterfactual=False, annualization_factor=365.
"""

import numpy as np
import pandas as pd
import pytest

from backtest.engine import _step_period, _validate_grid, execution_instant, run_backtest
from backtest.models import (
    BacktestConfig,
    ConfigError,
    DataIntegrityError,
    InvalidPriceError,
    MissingPriceError,
    StrategyOutput,
)

from helpers import TOL_EQUITY, TOL_WEIGHT, dates, mask_series, md, single_symbol_frame


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


def test_S1_staggered_listing_completes_and_matches_single_symbol_hand_computation():
    """S1 (§18.9, TOLERANCE for the value): staggered listing. §18.9
    requires the LITERAL hand-computed single-symbol value (BD-17) — a
    second engine run over the "A alone" fixture is not a hand computation:
    a shared systematic error (e.g. a Step-7 sign flip, or a bug that
    corrupts `asset_pnl_cash` identically in both runs) would cancel and
    this test would still pass. Values below are computed directly from
    the fixture's own known `quantity` and `P`, independent of any second
    `run_backtest()` call.
    """
    idx = dates(6)
    # B has no price for the first half; the strategy never names B, weight 0.
    b_prices = [np.nan, np.nan, np.nan, 50.0, 51.0, 52.0]
    prices = pd.DataFrame({"A": [100.0, 102.0, 104.0, 106.0, 108.0, 110.0], "B": b_prices}, index=idx)
    weights = pd.DataFrame({"A": [1.0] + [np.nan] * 5}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)

    assert np.isfinite(res.equity_curve.to_numpy()).all()

    # Hand computation (no second engine run, BD-17). Execution at i=1
    # (lag=1, mask True at bar 0), fee_bps=slippage_bps=0:
    #   quantity[1] = 1.0 * NAV_after_cost[1] / P[1] = 1_000_000 / 102
    #               = 9_803.921568627451
    #   asset_pnl_cash[0] = 0.0            (no position held over period 0)
    #   asset_pnl_cash[i] = quantity[1] * (P[i+1] - P[i])   for i = 1..4,
    #   with P = [100, 102, 104, 106, 108, 110] -- each consecutive A-leg
    #   move is +2.0, so every period's PnL is identical:
    #   9_803.921568627451 * 2.0 = 19_607.843137254902
    quantity_1 = 1.0 * 1_000_000.0 / 102.0
    assert quantity_1 == pytest.approx(9_803.921568627451, **TOL_WEIGHT)
    hand_asset_pnl = [0.0] + [quantity_1 * 2.0] * 4
    assert hand_asset_pnl == pytest.approx(
        [0.0, 19_607.843137254902, 19_607.843137254902, 19_607.843137254902, 19_607.843137254902],
        **TOL_EQUITY,
    )
    # Must fail if inactive symbols (B) are neutralised by `0 * NaN` --
    # B's price is NaN for periods 0-2, so a naive `0 * NaN` contribution
    # would poison this sum with NaN instead of leaving it at the hand
    # computation above.
    assert res.asset_pnl_cash.tolist() == pytest.approx(hand_asset_pnl, **TOL_EQUITY)


def test_S2_delisting_missing_next_price_raises_missing_price_error():
    # W-4 — §5.5 states EVERY price-validity violation, including a missing
    # (NaN) price, "raises InvalidPriceError". `MissingPriceError` MUST
    # therefore be a SUBCLASS of `InvalidPriceError` (models.py), not merely
    # a sibling under `DataIntegrityError`: a downstream
    # `except InvalidPriceError:` handler must still catch it. This is
    # asserted directly (runtime subclass check) rather than only relying on
    # a `pytest.raises(MissingPriceError)` succeeding, since that alone
    # cannot discriminate `MissingPriceError(DataIntegrityError)` from
    # `MissingPriceError(InvalidPriceError)` -- both would satisfy a bare
    # `pytest.raises(MissingPriceError)`.
    assert issubclass(MissingPriceError, InvalidPriceError)

    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 105.0, np.nan])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(MissingPriceError):
        run_backtest(_config(), md(prices), so)
    # The SAME fixture MUST also satisfy the broader `except InvalidPriceError:`
    # -- the actual defect class this closes (a mutation that reparents
    # `MissingPriceError` under `DataIntegrityError` instead) leaves the line
    # above green but reddens this one.
    with pytest.raises(InvalidPriceError):
        run_backtest(_config(), md(prices), so)


def test_S3_closing_position_at_invalid_execution_price_raises():
    """BT-1 — LOWER-LEVEL HELPER, deliberately.

    Proof that a full `run_backtest()` fixture cannot discriminate this
    defect: whenever `quantity[i-1] != 0` (a precondition for EXITING at
    period i), the IMMEDIATELY PRECEDING period i-1 was classified
    ENTERING/HELD and its own Step 5 already validated `P[i]` as ITS
    `P[i+1]` (§6.0's price-validation invariant: "the set Step 5 validates
    is exactly the set Step 7 reads"). That earlier check always fires
    first in a full simulation loop, so an invalid P[i] at a genuine EXITING
    period is unreachable via `run_backtest()` -- the only way to exercise
    EXITING's OWN Step-0 `needs_p_i` check in isolation is to construct the
    period directly via `_step_period`, per §18.0.2's precedent for
    same-instant / isolated-period accounting checks.

    Without EXITING in Step 0's `needs_p_i` set, this fixture's invalid
    `P_i = 0.0` would never be validated: Step 1 would silently compute
    `w_pre = 0.0` from the invalid price (not NaN, so no accidental
    downstream propagation) and turnover 0.0 instead of raising -- a
    silently WRONG (not merely absent) result.
    """
    quantity_prev = np.array([100.0])  # a pre-existing, nonzero position
    w_target = np.array([0.0])  # closing it: target 0 -> EXITING
    P_i = np.array([0.0])  # invalid execution price
    P_ip1 = np.array([100.0])  # irrelevant: EXITING never reads P[i+1]
    idx = dates(2)
    with pytest.raises(InvalidPriceError):
        _step_period(
            i=0,
            rebalance=True,
            w_target=w_target,
            quantity_prev=quantity_prev,
            P_i=P_i,
            P_ip1=P_ip1,
            NAV_pre=1_000_000.0,
            fee_bps=0.0,
            slippage_bps=0.0,
            symbols=["A"],
            timestamp_i=idx[0],
            timestamp_ip1=idx[1],
        )


def test_BD19_S3_production_path_invalid_price_at_closing_period_raises():
    """BD-19 — §18.9 S3 has no PRODUCTION-path test (the existing
    `test_S3_...` above is legitimately kept as a LOWER-LEVEL HELPER test,
    sanctioned by §21 B1's precedent for accounting states that are
    unreachable in production isolation). This test adds the production
    (`run_backtest()`) counterpart: `P = [100, 100, 0.0, 100]`, mask at
    bars 0 and 1, `w = [1.0, 0.0, ...]`.

    Mechanically: bar 0 -> ENTERING at i=1 (target 1.0); bar 1 -> EXITING
    at i=2 (target 0.0, closing the position). `P[2] = 0.0` is invalid.
    Because §6.0's price-validation invariant makes period i=1's OWN Step 5
    (`P[i+1]` for ENTERING/HELD) validate `P[2]` BEFORE the simulation loop
    ever reaches period i=2's Step 0, the exception is raised one iteration
    "earlier" than the isolated EXITING check above -- but it is the SAME
    invalid price (`P[2] = 0.0`), and `InvalidPriceError` genuinely
    propagates out of `run_backtest()` for this fixture end-to-end. This is
    the production-path proof that closing a position at an invalid price
    can never silently succeed.
    """
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 0.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, 0.0, np.nan, np.nan])
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(InvalidPriceError):
        run_backtest(_config(), md(prices), so)


def test_S4_exiting_symbol_does_not_require_next_price():
    idx = dates(4)
    prices = single_symbol_frame(idx, [100.0, 100.0, 105.0, np.nan])
    # Target 0 at bar1 (t=1), so execution at i=2 EXITS: needs only P[2] (valid),
    # not P[3] (absent) -- must complete without raising.
    weights = single_symbol_frame(idx, [1.0, 0.0, np.nan, np.nan])
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)
    assert res.quantity["A"].iloc[2] == 0.0
    assert res.symbol_state["A"].iloc[2] == "EXITING"


def test_S5_symbol_state_matches_all_four_states():
    # execution_lag=1: bar-t rebalance executes at i=t+1. With mask True at
    # bars 1 and 3: enter executes at i=2, exit executes at i=4.
    idx = dates(6)
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 105.0, 108.0, 110.0])
    weights = single_symbol_frame(idx, [np.nan, 1.0, np.nan, 0.0, np.nan, np.nan])
    mask = mask_series(idx, [1, 3])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)
    states = res.symbol_state["A"].tolist()
    assert states[0] == "INACTIVE"  # i=0, t=-1
    assert states[1] == "INACTIVE"  # i=1, t=0, mask[0]=False
    assert states[2] == "ENTERING"  # i=2, t=1, mask[1]=True, q_prev=0 -> will_hold
    assert states[3] == "HELD"  # i=3, t=2, mask[2]=False, q_prev!=0, carried
    assert states[4] == "EXITING"  # i=4, t=3, mask[3]=True, target=0


def test_S6_inactive_symbols_with_bad_prices_proceed_silently_and_contribute_zero():
    idx = dates(3)
    prices = pd.DataFrame(
        {"A": [100.0, 100.0, 100.0], "B": [np.nan, 0.0, -5.0]}, index=idx
    )
    weights = pd.DataFrame({"A": [1.0, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)  # must not raise
    assert (res.quantity["B"].to_numpy() == 0.0).all()
    assert (res.notional["B"].to_numpy() == 0.0).all()


def test_S7_exit_unnamed_materialises_zero_else_raises():
    idx = dates(4)
    prices = pd.DataFrame({"A": [100.0] * 4, "B": [50.0] * 4}, index=idx)
    # bar0 establishes A and B (both named); bar1 (the next rebalance bar)
    # names only A -> B is a held symbol left unnamed at that rebalance bar.
    weights_raw = pd.DataFrame(
        {"A": [1.0, 1.0, np.nan, np.nan], "B": [1.0, np.nan, np.nan, np.nan]}, index=idx
    )
    mask = mask_series(idx, [0, 1])

    so_without = StrategyOutput(target_weights=weights_raw, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_config(), md(prices), so_without)

    so_with = StrategyOutput.rebalance_on_dates(weights_raw, dates=[idx[0], idx[1]], exit_unnamed=True)
    res = run_backtest(_config(), md(prices), so_with)
    assert res.quantity["B"].iloc[2] == 0.0


def test_V1_zero_price_on_symbol_in_use_raises():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, 0.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(InvalidPriceError):
        run_backtest(_config(), md(prices), so)


def test_V2_negative_price_on_symbol_in_use_raises():
    idx = dates(3)
    prices = single_symbol_frame(idx, [100.0, -50.0, 100.0])
    weights = single_symbol_frame(idx, [1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(InvalidPriceError):
        run_backtest(_config(), md(prices), so)


def test_V3_denormal_price_passes_validity_but_yields_accounting_error():
    from backtest.models import AccountingError

    idx = dates(3)
    prices = single_symbol_frame(idx, [5e-324, 5e-324, 1e-300])
    weights = single_symbol_frame(idx, [-1.0, np.nan, np.nan])
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(AccountingError):
        run_backtest(_config(), md(prices), so)


def test_T1_unexecuted_rebalance_no_trade_no_crash_recorded():
    idx = dates(5)
    prices = single_symbol_frame(idx, [100.0] * 5)
    # mask True at the LAST bar: t=4, execution i=4+1=5 > n-2=3 -> unexecuted.
    weights = single_symbol_frame(idx, [np.nan] * 4 + [1.0])
    mask = mask_series(idx, [4])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)  # must not raise
    assert idx[4] in res.unexecuted_rebalances
    assert (res.turnover.to_numpy() == 0.0).all()


def test_T2_terminal_bar_lengths_and_no_nan_equity():
    idx = dates(5)
    prices = single_symbol_frame(idx, [100.0, 101.0, 99.0, 102.0, 103.0])
    weights = single_symbol_frame(idx, [1.0] + [np.nan] * 4)
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)
    n = len(idx)
    assert len(res.net_return) == n - 1
    assert len(res.equity_curve) == n
    assert not np.isnan(res.equity_curve.to_numpy()).any()


def test_T3_two_bar_backtest_one_period_one_bar_raises_configerror():
    idx = dates(2)
    prices = single_symbol_frame(idx, [100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan, np.nan])
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)
    assert len(res.net_return) == 1

    idx1 = dates(1)
    prices1 = single_symbol_frame(idx1, [100.0])
    weights1 = single_symbol_frame(idx1, [np.nan])
    mask1 = mask_series(idx1, [])
    so1 = StrategyOutput(target_weights=weights1, rebalance_mask=mask1)
    with pytest.raises(ConfigError):
        run_backtest(_config(), md(prices1), so1)


def test_T4_execution_instant_matches_contract_table():
    delta = pd.Timedelta(days=1)
    table = [
        (pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-01-02", tz="UTC")),
        (pd.Timestamp("2026-01-02", tz="UTC"), pd.Timestamp("2026-01-02", tz="UTC"), pd.Timestamp("2026-01-03", tz="UTC")),
        (pd.Timestamp("2026-01-03", tz="UTC"), pd.Timestamp("2026-01-03", tz="UTC"), pd.Timestamp("2026-01-04", tz="UTC")),
    ]
    for t_i, expected_open, expected_close in table:
        assert execution_instant(t_i, "next_open", delta) == expected_open
        assert execution_instant(t_i, "next_close", delta) == expected_close


def test_T5_regular_grid_disagreeing_with_frequency_raises():
    idx = pd.date_range("2026-01-01", periods=4, freq="4h", tz="UTC")  # regular, but not "1d"
    prices = single_symbol_frame(idx, [100.0, 100.0, 100.0, 100.0])
    weights = single_symbol_frame(idx, [np.nan] * 4)
    mask = mask_series(idx, [])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_config(frequency="1d"), md(prices), so)


def test_U1_nonzero_quantity_absent_from_target_row_raises():
    idx = dates(4)
    prices = pd.DataFrame({"A": [100.0] * 4, "B": [50.0] * 4}, index=idx)
    # A held after bar0; bar1's rebalance row NAMES the A column (it is
    # present, not omitted) but leaves its value NaN -- §5.4 forbids both
    # "absent" and "present-but-NaN" for a symbol with nonzero prior quantity.
    weights = pd.DataFrame({"A": [1.0, np.nan, np.nan, np.nan]}, index=idx)
    weights["B"] = [np.nan, 1.0, np.nan, np.nan]
    mask = mask_series(idx, [0, 1])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    with pytest.raises(DataIntegrityError):
        run_backtest(_config(), md(prices), so)


def test_U2_symbol_entering_mid_sample_proceeds_silently():
    idx = dates(5)
    prices = pd.DataFrame(
        {"A": [100.0] * 5, "B": [np.nan, np.nan, 50.0, 51.0, 52.0]}, index=idx
    )
    # Every rebalance-bar row must supply a finite value for every named
    # column (§3): A must be restated at bar 2 alongside B's first target.
    weights = pd.DataFrame({"A": [1.0, np.nan, 1.0, np.nan, np.nan], "B": [0.0, np.nan, 1.0, np.nan, np.nan]}, index=idx)
    mask = mask_series(idx, [0, 2])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)  # must not raise
    assert res.quantity["B"].iloc[3] != 0.0


def test_U3_symbol_absent_from_target_columns_with_zero_quantity_treated_as_zero():
    idx = dates(3)
    prices = pd.DataFrame({"A": [100.0] * 3, "B": [50.0] * 3}, index=idx)
    weights = pd.DataFrame({"A": [1.0, np.nan, np.nan]}, index=idx)  # B never appears at all
    mask = mask_series(idx, [0])
    so = StrategyOutput(target_weights=weights, rebalance_mask=mask)
    res = run_backtest(_config(), md(prices), so)  # must not raise
    assert (res.quantity["B"].to_numpy() == 0.0).all()
    assert (res.trades["B"].to_numpy() == 0.0).all()


def test_D4t_irregular_bar_grid_raises_naming_pair_and_delta():
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-01-01", tz="UTC"),
            pd.Timestamp("2026-01-02", tz="UTC"),
            pd.Timestamp("2026-01-04", tz="UTC"),  # irregular gap
        ]
    )
    with pytest.raises(DataIntegrityError) as excinfo:
        _validate_grid(idx, pd.Timedelta(days=1))
    # §18.9 D4t / §2.1 — the message MUST name the offending timestamp pair
    # and the expected Δ, not just raise the right type.
    message = str(excinfo.value)
    assert "2026-01-02" in message
    assert "2026-01-04" in message
    assert "1 day" in message or "Timedelta" in message or str(pd.Timedelta(days=1)) in message
