"""§18.6 — Metrics: M1-M6.

This is a PURE-FUNCTION unit test of metrics.py, not an engine run.
Config: annualization_factor=365, risk_free_per_period=0, mar_per_period=0.

M7 is deleted and MUST NOT be reintroduced.
"""

import math

import numpy as np
import pytest

from backtest.metrics import compute_metrics

from helpers import TOL_METRIC

NET_RETURN = np.array([0.010, -0.005, 0.020, -0.015, 0.000, 0.008, -0.012, 0.006])

EQUITY_CURVE = np.array(
    [
        1_000_000.0,
        1_010_000.0,
        1_004_950.0,
        1_025_049.0,
        1_009_673.265,
        1_009_673.265,
        1_017_750.65112,
        1_005_537.64330656,
        1_011_570.8691663994,
    ]
)


def _metrics():
    turnover = np.zeros_like(NET_RETURN)
    return compute_metrics(NET_RETURN, EQUITY_CURVE, turnover, af=365.0, risk_free_per_period=0.0, mar_per_period=0.0)


def test_M1_sharpe():
    m = _metrics()
    assert m["sharpe"] == pytest.approx(2.4269554394174677, **TOL_METRIC)


def test_M2_sortino():
    m = _metrics()
    assert m["sortino"] == pytest.approx(4.0835189363529718, **TOL_METRIC)


def test_M3_degenerate_cases_return_nan_not_zero_not_exception():
    zero_turnover = np.array([0.0])
    # n_periods < 2 -> every dispersion-based metric is nan, computed without exception.
    m = compute_metrics(np.array([0.01]), np.array([1_000_000.0, 1_010_000.0]), zero_turnover, af=365.0)
    assert math.isnan(m["annualized_volatility"])
    assert math.isnan(m["sharpe"])
    assert math.isnan(m["downside_dev_ann"])
    assert math.isnan(m["sortino"])

    # annualized_volatility == 0 -> sharpe nan (constant returns).
    m2 = compute_metrics(np.array([0.01, 0.01, 0.01]), np.array([1e6, 1.01e6, 1.0201e6, 1.030301e6]),
                          np.zeros(3), af=365.0)
    assert m2["annualized_volatility"] == 0.0
    assert math.isnan(m2["sharpe"])

    # downside_dev_ann == 0 -> sortino nan (no losing periods relative to MAR).
    # §18.0.1 classifies M3 EXACT on the `isnan` predicate alone.
    assert m2["downside_dev_ann"] == 0.0
    assert math.isnan(m2["sortino"])

    # max_drawdown == 0 -> calmar nan (monotonically non-decreasing equity).
    m3 = compute_metrics(np.array([0.01, 0.02]), np.array([1e6, 1.01e6, 1.0302e6]), np.zeros(2), af=365.0)
    assert m3["max_drawdown"] == 0.0
    assert math.isnan(m3["calmar"])


def test_M4_cagr_differs_from_arithmetic_annualization():
    # BD-21 — §17 "metrics, drag statistics" row: rtol=1e-10, atol=1e-12,
    # tightened from `rel=1e-9` on every assertion below.
    m = _metrics()
    arithmetic = float(np.mean(NET_RETURN) * 365.0)
    assert arithmetic == pytest.approx(0.5475, **TOL_METRIC)
    assert m["cagr"] == pytest.approx(0.6902729275701369, **TOL_METRIC)
    assert abs(m["cagr"] - arithmetic) / abs(arithmetic) > 0.20  # ~26% apart, guards a silent swap


def test_M5_total_return_cagr_max_drawdown():
    # BD-21 — §17 metrics row, tightened from `rel=1e-9` (cagr, max_drawdown).
    m = _metrics()
    assert m["total_return"] == pytest.approx(0.011570869166399378, **TOL_METRIC)
    assert m["cagr"] == pytest.approx(0.6902729275701369, **TOL_METRIC)
    assert m["max_drawdown"] == pytest.approx(-0.019034560000000034, **TOL_METRIC)


def test_M6_max_drawdown_captures_first_period_loss():
    net_return = np.array([-0.02, 0.05])
    equity = np.array([1_000_000.0, 980_000.0, 1_029_000.0])
    m = compute_metrics(net_return, equity, np.zeros(2), af=365.0)
    assert m["max_drawdown"] < -0.019


def test_BD4_cagr_overflow_emits_no_warning():
    """BD-4 — a short, high-frequency sample can overflow `cagr` to `inf`
    (§21 B5, accepted spec debt: the `inf` itself is not converted to
    `nan`). The exponentiation MUST be inside the suppressed
    `np.errstate(all='ignore')` block so this does not escape as an
    uncaught `RuntimeWarning` under a `-W error` run."""
    import warnings

    net_return = np.array([1.0, 1.0])
    equity = np.array([1_000_000.0, 2_000_000.0, 4_000_000.0])
    turnover = np.zeros(2)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        m = compute_metrics(net_return, equity, turnover, af=8760.0)
    assert math.isinf(m["cagr"])
    assert m["cagr"] > 0
