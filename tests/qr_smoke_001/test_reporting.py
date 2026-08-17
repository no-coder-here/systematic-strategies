"""spec §4.9 — reporting hazards (W22, W15). `cagr`/`calmar` MUST be
suppressed or footnoted in every report; `total_return` is presented instead.
Reported Sharpe/Sortino are zero-risk-free-rate figures.
"""
from __future__ import annotations

import math

from experiments.qr_smoke_001.run_all import summarize


def test_report_summary_never_surfaces_cagr_or_calmar(window_a, window_b1, window_b2):
    for run in (window_a, window_b1, window_b2):
        summary = summarize(run)
        assert "cagr" not in summary
        assert "calmar" not in summary
        assert "total_return" in summary
        assert "cagr_footnote" in summary  # explicit footnote per spec §4.9


def test_engine_metrics_dict_still_contains_cagr_calmar_but_we_dont_report_them(window_a):
    """The engine's own `metrics` dict DOES compute `cagr`/`calmar`
    (contract §12 requires it); QR-SMOKE-001's REPORT simply never surfaces
    them (spec §4.9), which is a reporting choice, not an engine change."""
    m = window_a.result.metrics
    assert "cagr" in m
    assert "calmar" in m


def test_sharpe_and_sortino_are_zero_risk_free_figures(window_a, window_b1, window_b2):
    for run in (window_a, window_b1, window_b2):
        assert run.config.risk_free_per_period == 0.0
        assert run.config.mar_per_period == 0.0


def test_cagr_can_raise_overflow_at_af_8760_on_a_short_sample():
    """contract §12.5 / spec §4.9 (B5) — demonstrates the hazard the
    suppression rule protects against, on a deliberately SHORT synthetic
    sample at af=8760 (Window A/B1/B2 are all long enough that `cagr` does
    NOT actually overflow for them -- reported, not overclaimed)."""
    equity = [1_000_000.0, 1_100_000.0]  # n_periods = 1
    af = 8760.0
    n_periods = 1
    growth = equity[-1] / equity[0]
    try:
        cagr = growth ** (af / n_periods) - 1
        overflowed = not math.isfinite(cagr)
    except OverflowError:
        overflowed = True
    assert overflowed, "expected the short-sample/af=8760 cagr computation to overflow or be non-finite"


def test_real_runs_cagr_does_not_actually_overflow_but_is_still_suppressed(window_a, window_b1, window_b2):
    """Window A/B1/B2 have enough periods that `cagr` happens to stay
    finite; the suppression in `run_all.summarize` applies regardless
    (spec §4.9 does not condition suppression on whether overflow actually
    occurs for THIS run)."""
    for run in (window_a, window_b1, window_b2):
        cagr = run.result.metrics["cagr"]
        assert math.isfinite(cagr)
        summary = summarize(run)
        assert "cagr" not in summary
