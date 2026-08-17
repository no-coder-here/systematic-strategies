"""spec §2.2 (window boundaries), §2.3 (frame slicing), §2.4 (handoff),
§4.7 (warm-up boundary test)."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from data.base import to_engine_frame
from experiments.qr_smoke_001 import pipeline
from strategies.qr_smoke_001 import compute_sma


# ---------------------------------------------------------------------------
# spec §2.2 — pinned window boundaries (resolved per pipeline.py's flagged
# ambiguity: evaluated-frame timestamps/counts are authoritative).
# ---------------------------------------------------------------------------


def test_window_a_evaluated_frame_matches_pinned_boundaries(window_a):
    assert window_a.frame_index[0] == pipeline.WINDOW_A_EVAL_START
    assert window_a.frame_index[-1] == pipeline.WINDOW_A_EVAL_END
    assert len(window_a.frame_index) == 4512


def test_window_b1_full_binance_history_bar_count(window_b1):
    assert window_b1.raw_index[0] == pipeline.WINDOW_B1_RAW_START
    assert window_b1.raw_index[-1] == pipeline.WINDOW_B1_RAW_END
    assert len(window_b1.raw_index) == 57696
    assert window_b1.result.funding_modelled is False
    assert window_b1.result.funding_notional_basis == "not_modelled"


def test_window_b2_evaluated_frame_matches_pinned_boundaries(window_b2):
    assert window_b2.frame_index[0] == pipeline.WINDOW_B2_EVAL_START
    assert window_b2.frame_index[-1] == pipeline.WINDOW_B2_EVAL_END
    assert window_b2.result.funding_modelled is True
    assert window_b2.result.funding_notional_basis == "period_start"


def test_window_b2_funding_coverage_window_rule(window_b2):
    """spec §2.2 — a SINGLE FundingCoverage record must cover [T_0, T_last]
    strictly wider on both sides."""
    rec = window_b2.covering_funding_record
    assert rec is not None
    assert rec.coverage_start < window_b2.frame_index[0]
    assert rec.coverage_end > window_b2.frame_index[-1]


def test_window_a_funding_coverage_window_rule(window_a):
    rec = window_a.covering_funding_record
    assert rec is not None
    assert rec.coverage_start < window_a.frame_index[0]
    assert rec.coverage_end > window_a.frame_index[-1]


# ---------------------------------------------------------------------------
# spec §4.7 — warm-up boundary test (BD6).
# ---------------------------------------------------------------------------


def test_warmup_boundary_frame_index0_equals_raw_index99(window_a):
    assert window_a.frame_index[0] == window_a.raw_index[99]


def test_warmup_boundary_frame_length(window_a):
    assert len(window_a.frame_index) == len(window_a.raw_index) - 99


def test_warmup_boundary_sma_tolerance(window_a):
    sma_full = compute_sma(window_a.raw_close)
    computed = sma_full.loc[window_a.frame_index[0]]
    expected = window_a.raw_close.iloc[0:100].mean()
    assert computed == pytest.approx(expected, rel=1e-12, abs=1e-15)


def test_warmup_boundary_no_nan_in_frame(window_a):
    sma_full = compute_sma(window_a.raw_close)
    assert window_a.raw_close.loc[window_a.frame_index].notna().all()
    assert sma_full.loc[window_a.frame_index].notna().all()


def test_warmup_boundary_bar_98_is_not_fully_defined(window_a):
    """spec §4.7 v1.1 (W2) — the bar immediately BEFORE the frame start
    (raw position 98) MUST NOT have a fully-defined SMA. Without this,
    §4.7 is INERT against M4 (`min_periods` 100 -> 1): measured, all four
    of the assertions above hold UNCHANGED under `min_periods=1`, because
    every *retained* bar still has a full 100-observation window. This
    assertion is what makes M4 actually break this test."""
    sma_full = compute_sma(window_a.raw_close)
    assert pd.isna(sma_full.iloc[98]), (
        "raw position 98 (one bar before the frame start) must have an "
        "undefined SMA100 -- if this is not NaN, §4.7 cannot discriminate M4"
    )


# ---------------------------------------------------------------------------
# spec §2.4 — handoff via `to_engine_frame(..., policy="raise")`.
# ---------------------------------------------------------------------------


def test_to_engine_frame_raise_policy_rejects_a_grid_gap():
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    idx_with_gap = idx.delete(2)  # drop the 3rd bar -> a genuine grid gap
    rows = []
    for ts in idx_with_gap:
        rows.append({"timestamp": ts, "symbol": "BTC", "open": 100.0, "close": 100.0})
    df = pd.DataFrame(rows)
    with pytest.raises(DataIntegrityError):
        to_engine_frame(df, "1h", policy="raise")


def test_to_engine_frame_raise_policy_succeeds_on_regular_grid():
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    rows = [{"timestamp": ts, "symbol": "BTC", "open": 100.0, "close": 100.0} for ts in idx]
    df = pd.DataFrame(rows)
    md = to_engine_frame(df, "1h", policy="raise")
    assert len(md.open) == 5
