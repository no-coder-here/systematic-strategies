"""spec §4.1 — manual path verification, with the §4.1.1 per-field
EXACT/TOLERANCE classification applied field-by-field, and the mandatory
identity (D) asserted on every non-ruin period of Window A.

The reconstruction (`experiments/qr_smoke_001/reconstruction.py`) is
INDEPENDENT per spec §4.1 BD8/W21 — it does not import `engine.py`,
`costs.py`, `metrics.py`, the strategy module, or the harness's price-frame
construction; see that module's docstring.

Selected periods (real periods from the actual Window A run, spec §4.1):
    i=51  — case 1 (0 -> long transition) AND case 4 (entry period that is
            both a rebalance execution point and funding-accruing:
            pre-trade quantity 0, post-trade quantity != 0, >=1 funding
            event in-period) AND case 5 (carries transaction costs)
    i=52  — case 2 (long -> long HELD period; quantity[i] == quantity[i-1])
    i=53  — case 3 (long -> 0 transition), also carries transaction costs
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from experiments.qr_smoke_001 import pipeline, reconstruction

# §17 / §4.1.1 tolerance constants, re-derived per field class (not
# pattern-matched from a neighbouring line).
TOL_EQUITY = dict(rel=1e-12, abs=1e-9)  # NAV_*, asset_pnl_cash, funding_pnl_cash (USD)
TOL_DECOMP = dict(rel=1e-12, abs=1e-15)  # decomposition identity (D)
TOL_WEIGHT = dict(rel=1e-12, abs=1e-15)  # w_pre (nonzero), quantity (nonzero)
TOL_TURNOVER = dict(rel=1e-12, abs=1e-15)  # turnover (nonzero), fee/slippage cost, fee_basis_notional
TOL_RETURN = dict(rel=1e-12, abs=1e-15)  # net_return, component returns

SELECTED = {"entry_case1_case4_case5": 51, "held_case2": 52, "exit_case3_case5": 53}

# `manual_periods` is now a SESSION-scoped fixture shared across test modules
# (see tests/qr_smoke_001/conftest.py) so the full-window reconstruction runs
# exactly once for both this module's full-path assertion (W1) and
# test_vacuity.py's M20 funding-boundary coverage.


def test_selected_cases_satisfy_their_preconditions(manual_periods):
    p51, p52, p53 = manual_periods[51], manual_periods[52], manual_periods[53]

    # case 1: 0 -> long transition
    assert p51.rebalance_decision is True
    assert p51.quantity_pre == 0.0
    assert p51.quantity_post != 0.0

    # case 4 (W10): entry period, both a rebalance execution point AND
    # funding-accruing. A HELD period cannot satisfy this (quantity[i] ==
    # quantity[i-1] there), which is exactly why i=51 (an entry), not i=52
    # (a hold), is used for this case.
    assert p51.n_funding_events >= 1

    # case 5: carries transaction costs
    assert p51.fee_cost > 0.0
    assert p51.slippage_cost > 0.0

    # case 2: long -> long HELD period
    assert p52.rebalance_decision is False
    assert p52.quantity_pre != 0.0
    assert p52.quantity_post == p52.quantity_pre  # exact carry-forward (§5.1)
    assert p52.turnover == 0.0
    assert p52.fee_cost == 0.0

    # case 3: long -> 0 transition
    assert p53.rebalance_decision is True
    assert p53.quantity_pre != 0.0
    assert p53.quantity_post == 0.0
    assert p53.fee_cost > 0.0  # also case 5


def test_manual_report_table(manual_periods, window_a):
    """Prints the required §4.1 report table for the 3 selected periods
    (covering all 5 required cases) and reports the mandatory fields."""
    lines = []
    for label, i in SELECTED.items():
        p = manual_periods[i]
        lines.append(
            f"{label} (i={i}): signal_obs_ts={p.signal_observation_ts} sma={p.sma_value:.4f} "
            f"signal={p.signal_value} target_w={p.target_weight} rebalance={p.rebalance_decision} "
            f"exec_ts={p.execution_ts} exec_price={p.execution_price} w_pre={p.w_pre:.6f} "
            f"q_pre={p.quantity_pre:.6f} trade={p.trade:.6f} turnover={p.turnover:.6f} "
            f"fee_cost={p.fee_cost:.4f} slippage_cost={p.slippage_cost:.4f} "
            f"fee_basis_notional={p.fee_basis_notional:.2f} NAV_pre={p.nav_pre:.4f} "
            f"NAV_after_cost={p.nav_after_cost:.4f} q_post={p.quantity_post:.6f} "
            f"asset_pnl_cash={p.asset_pnl_cash:.4f} funding_pnl_cash={p.funding_pnl_cash:.6f} "
            f"NAV_end={p.nav_end:.4f} net_return={p.net_return:.8f} gross_return={p.gross_return:.8f} "
            f"fee_return={p.fee_return:.8f} slippage_return={p.slippage_return:.8f} "
            f"funding_return={p.funding_return:.8f}"
        )
    report = "\n".join(lines)
    print("\n" + report)
    assert len(lines) == 3


@pytest.mark.parametrize("i", [51, 52, 53])
def test_manual_matches_engine_per_field_classification(manual_periods, window_a, i):
    """§4.1.1 — per-field EXACT/TOLERANCE, re-derived per field, comparing
    the INDEPENDENT reconstruction against the actual engine BacktestResult
    for the same real Window A period."""
    p = manual_periods[i]
    r = window_a.result
    ts = window_a.frame_index[i]

    # -- EXACT class ---------------------------------------------------
    assert bool(r.rebalance_flag.iloc[i]) == p.rebalance_decision  # discrete boolean state
    if p.rebalance_decision:
        # "signal value" is EXACT (§4.1.1): a 1-ulp SMA disagreement would
        # surface here as a boolean flip. target_weight is the literal
        # 1.0/0.0 mapped from the signal (spec §1.2).
        expected_target = 1.0 if p.signal_value else 0.0
        assert float(r.resolved_target_weights["BTC"].iloc[i]) == expected_target
        assert p.target_weight == expected_target
    # execution price: same stored double, read from the same source frame.
    # D4 fix (v1.1 repair cycle 2): compare against the ENGINE-FED frame
    # (`window_a.frame_md.open`, the actual MarketData handed to
    # `run_backtest`), not `window_a.raw_open` (the pre-slice, parquet-
    # derived series) -- a shift/mutation applied only when constructing
    # `frame_md` (M11, `pipeline.py`'s `slice_evaluated_frame`) would
    # otherwise be invisible to this per-field assertion.
    engine_price = window_a.frame_md.open["BTC"].loc[ts]
    assert engine_price == p.execution_price

    # turnover / quantity / w_pre on the ZERO branch: EXACT (assigned literal).
    if p.turnover == 0.0:
        assert float(r.turnover.iloc[i]) == 0.0
    if p.quantity_pre == 0.0:
        # previous period's post-trade quantity, carried forward — checked
        # via this period's own pre_trade_weights zero-branch instead, since
        # "quantity_pre" is not itself a result-surface field.
        assert float(r.pre_trade_weights["BTC"].iloc[i]) == 0.0

    # -- TOLERANCE class -------------------------------------------------
    assert float(r.pre_trade_weights["BTC"].iloc[i]) == pytest.approx(p.w_pre, **TOL_WEIGHT)
    assert float(r.quantity["BTC"].iloc[i]) == pytest.approx(p.quantity_post, **TOL_WEIGHT)
    assert float(r.turnover.iloc[i]) == pytest.approx(p.turnover, **TOL_TURNOVER)
    assert float(r.fee_cost.iloc[i]) == pytest.approx(p.fee_cost, **TOL_TURNOVER)
    assert float(r.slippage_cost.iloc[i]) == pytest.approx(p.slippage_cost, **TOL_TURNOVER)
    assert float(r.fee_basis_notional.iloc[i]) == pytest.approx(p.fee_basis_notional, **TOL_TURNOVER)
    assert float(r.asset_pnl_cash.iloc[i]) == pytest.approx(p.asset_pnl_cash, **TOL_EQUITY)
    assert float(r.funding_pnl_cash.iloc[i]) == pytest.approx(p.funding_pnl_cash, **TOL_EQUITY)
    assert float(r.net_return.iloc[i]) == pytest.approx(p.net_return, **TOL_RETURN)
    assert float(r.gross_return.iloc[i]) == pytest.approx(p.gross_return, **TOL_RETURN)
    assert float(r.fee_return.iloc[i]) == pytest.approx(p.fee_return, **TOL_RETURN)
    assert float(r.slippage_return.iloc[i]) == pytest.approx(p.slippage_return, **TOL_RETURN)
    assert float(r.funding_return.iloc[i]) == pytest.approx(p.funding_return, **TOL_RETURN)

    # NAV_pre[i] is equity_curve[i] (same stored ledger value, contract §8).
    assert float(r.equity_curve.iloc[i]) == pytest.approx(p.nav_pre, **TOL_EQUITY)
    # NAV_end[i] is equity_curve[i+1].
    assert float(r.equity_curve.iloc[i + 1]) == pytest.approx(p.nav_end, **TOL_EQUITY)
    # NAV_after_cost[i] = equity_curve[i] - fee_cost[i] - slippage_cost[i] (§10).
    engine_nav_after_cost = float(r.equity_curve.iloc[i]) - float(r.fee_cost.iloc[i]) - float(r.slippage_cost.iloc[i])
    assert engine_nav_after_cost == pytest.approx(p.nav_after_cost, **TOL_EQUITY)


def test_full_path_engine_matches_reconstruction_every_period_every_field(manual_periods, window_a):
    """spec §4.1 v1.1 (W1, MANDATORY FULL-PATH ASSERTION) — the engine-vs-
    reconstruction comparison MUST be asserted over EVERY period and EVERY
    field of Window A, not just the 3 selected-for-reporting indices. In
    v1.0's implementation the comparison ran over all 4511 periods but was
    asserted at exactly 3, leaving 4508 unasserted — a funding-boundary
    mutation moving equity on 3973/4511 periods passed the entire suite.
    This test closes that gap: every field listed in spec §4.1's mandatory
    list, over every period, per the §4.1.1 EXACT/TOLERANCE classification.
    This ALSO closes M3 (SMA window 100->99 in the reconstruction), M19
    (dropping funding_pnl_cash from the reconstruction's Step 9) and the
    funding-boundary gap (M20, §4.3.1) -- any of those mutations diverges the
    reconstruction from the engine on a large fraction of periods, which
    this whole-path comparison now catches regardless of WHICH 3 indices a
    human happened to select for the printed report.
    """
    r = window_a.result
    n = len(manual_periods)
    assert n == len(r.net_return) == len(window_a.frame_index) - 1

    frame_open = window_a.frame_md.open["BTC"].to_numpy()

    # -- EXACT class, every period --------------------------------------
    rebal_recon = np.array([p.rebalance_decision for p in manual_periods], dtype=bool)
    np.testing.assert_array_equal(
        r.rebalance_flag.to_numpy()[:n], rebal_recon,
        err_msg="rebalance_flag (EXACT boolean) mismatch over the full Window A path",
    )

    exec_price_recon = np.array([p.execution_price for p in manual_periods], dtype=float)
    np.testing.assert_array_equal(
        frame_open[:n], exec_price_recon,
        err_msg="execution price (EXACT, same stored double) mismatch over the full Window A path",
    )

    engine_target_weight = r.resolved_target_weights["BTC"].to_numpy()[:n]
    for i in range(n):
        if rebal_recon[i]:
            # target weight is EXACT (literal 1.0/0.0) only where a
            # rebalance decision was actually made (spec §1.2/§4.1.1).
            assert engine_target_weight[i] == manual_periods[i].target_weight, (
                f"target_weight mismatch at rebalance period i={i}"
            )

    # -- TOLERANCE class, every period (§4.1.1 / §17 rtol=1e-12) ----------
    def _arr(attr):
        return np.array([getattr(p, attr) for p in manual_periods], dtype=float)

    np.testing.assert_allclose(r.turnover.to_numpy()[:n], _arr("turnover"), rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(r.fee_cost.to_numpy()[:n], _arr("fee_cost"), rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(r.slippage_cost.to_numpy()[:n], _arr("slippage_cost"), rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(
        r.fee_basis_notional.to_numpy()[:n], _arr("fee_basis_notional"), rtol=1e-12, atol=1e-15
    )
    np.testing.assert_allclose(
        r.quantity["BTC"].to_numpy()[:n], _arr("quantity_post"), rtol=1e-12, atol=1e-15
    )
    np.testing.assert_allclose(
        r.pre_trade_weights["BTC"].to_numpy()[:n], _arr("w_pre"), rtol=1e-12, atol=1e-15
    )
    np.testing.assert_allclose(r.asset_pnl_cash.to_numpy()[:n], _arr("asset_pnl_cash"), rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(
        r.funding_pnl_cash.to_numpy()[:n], _arr("funding_pnl_cash"), rtol=1e-12, atol=1e-9
    )
    # NAV_end[i] == equity_curve[i+1] (same ledger relationship as the
    # single-period test above, applied over the whole path).
    np.testing.assert_allclose(
        r.equity_curve.to_numpy()[1 : n + 1], _arr("nav_end"), rtol=1e-12, atol=1e-9
    )
    np.testing.assert_allclose(r.net_return.to_numpy()[:n], _arr("net_return"), rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(r.gross_return.to_numpy()[:n], _arr("gross_return"), rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(r.fee_return.to_numpy()[:n], _arr("fee_return"), rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(
        r.slippage_return.to_numpy()[:n], _arr("slippage_return"), rtol=1e-12, atol=1e-15
    )
    np.testing.assert_allclose(
        r.funding_return.to_numpy()[:n], _arr("funding_return"), rtol=1e-12, atol=1e-15
    )


def test_decomposition_identity_D_every_non_ruin_period(window_a):
    """contract §6.1 (D) — MUST be asserted on EVERY non-ruin period of
    Window A, at §17 rtol=1e-12, atol=1e-15. Window A does not ruin (BTC
    long-or-flat with realistic costs cannot plausibly ruin — verified:
    `window_a.result.ruined is False`), so every period qualifies.
    """
    r = window_a.result
    assert r.ruined is False
    lhs = (
        r.gross_return.to_numpy()
        + r.fee_return.to_numpy()
        + r.slippage_return.to_numpy()
        + r.funding_return.to_numpy()
    )
    rhs = r.net_return.to_numpy()
    np.testing.assert_allclose(lhs, rhs, rtol=1e-12, atol=1e-15)


def test_funding_sign_convention_empirically_confirmed(manual_periods, window_a):
    """contract §7.3 — `funding_rate > 0 => longs pay shorts`, so a LONG
    position with a POSITIVE summed rate MUST produce NEGATIVE
    `funding_pnl_cash`. spec §4.3.1 / D1 fix (v1.1 repair cycle 2): this MUST
    be checked against the ENGINE's own `funding_pnl_cash`
    (`window_a.result.funding_pnl_cash`), not merely the reconstruction's —
    a reconstruction-only check guards the reconstruction's sign against
    itself and cannot detect an adapter- or engine-level sign defect (M16 /
    M5). The summed rate is read directly from the raw funding parquet via
    `reconstruction.load_raw_funding`, which does NOT go through
    `experiments.qr_smoke_001.pipeline`'s `HyperliquidProvider` adapter, so a
    sign flip introduced ONLY in that adapter (M16) cannot be
    self-consistently invisible here.

    Checked at period i=51 (real Window A data): the raw funding record at
    2026-01-27T03:00:00.012Z carries `funding_rate=+0.000013`, and the
    ENGINE reports a long position (`quantity != 0`) at i=51.
    """
    p51 = manual_periods[51]
    T_i = p51.execution_ts
    T_ip1 = T_i + pd.Timedelta(hours=1)

    raw_events = reconstruction.load_raw_funding(
        "data", "BTC", pd.Timestamp("2026-01-27T02:59:59", tz="UTC"), pd.Timestamp("2026-01-27T04:00:00", tz="UTC")
    )
    in_period = raw_events.loc[
        (raw_events["timestamp"] >= T_i) & (raw_events["timestamp"] < T_ip1)
    ]
    assert len(in_period) == 1
    summed_rate = float(in_period["funding_rate"].sum())
    assert summed_rate > 0.0  # empirically positive for this real event

    r = window_a.result
    engine_quantity_post = float(r.quantity["BTC"].iloc[51])
    assert engine_quantity_post > 0.0  # the ENGINE (not the reconstruction) is long at i=51

    engine_funding_pnl = float(r.funding_pnl_cash.iloc[51])
    # contract §7.3, checked on the ENGINE's own output: for a nonzero
    # position, sign(engine_funding_pnl) MUST oppose sign(summed_rate).
    assert (engine_funding_pnl < 0.0) == (summed_rate > 0.0)
    assert (engine_funding_pnl > 0.0) == (summed_rate < 0.0)
    assert engine_funding_pnl < 0.0  # concretely, for this positive-rate real event


def test_reconstruction_can_disagree_not_a_tautology():
    """Guards against the anti-pattern spec §4.1 explicitly forbids:
    'printing engine outputs and asserting they equal themselves is NOT
    manual verification.' Proves the reconstruction is a SEPARATE
    computation capable of disagreeing, by deliberately perturbing one
    reconstructed input and confirming the two paths then diverge.
    """
    raw_start, raw_end = pipeline.eval_window_to_raw_window(
        pipeline.WINDOW_A_EVAL_START, pipeline.WINDOW_A_EVAL_END
    )
    raw = reconstruction.load_raw_ohlcv(
        "data", "hyperliquid", "BTC", raw_start, raw_end + pd.Timedelta(hours=1)
    )
    raw_sig = reconstruction.compute_sma_and_signal(raw, 100)
    funding = reconstruction.load_raw_funding(
        "data", "BTC", pd.Timestamp("2023-05-01", tz="UTC"), pd.Timestamp("2026-08-20", tz="UTC")
    )
    cfg_correct = reconstruction.ManualConfig(
        execution_lag=1, fee_bps=4.5, slippage_bps=1.0,
        funding_notional_basis="period_start", initial_capital=1_000_000.0,
    )
    cfg_wrong = reconstruction.ManualConfig(
        execution_lag=1, fee_bps=999.0, slippage_bps=1.0,  # deliberately wrong fee
        funding_notional_basis="period_start", initial_capital=1_000_000.0,
    )
    correct = reconstruction.reconstruct_path(raw_sig, funding, 99, cfg_correct)
    wrong = reconstruction.reconstruct_path(raw_sig, funding, 99, cfg_wrong)
    assert correct[51].fee_cost != wrong[51].fee_cost
    assert correct[51].nav_end != wrong[51].nav_end
