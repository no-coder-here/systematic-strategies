"""Unit tests for `strategies/qr_smoke_001/strategy.py` (spec §1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from strategies.qr_smoke_001 import SMA_WINDOW, compute_sma, compute_signal, compute_target_weights
from strategies.qr_smoke_001.strategy import build_strategy_output_for_frame


def _series(n: int, values=None) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    if values is None:
        values = np.arange(1, n + 1, dtype=float)
    return pd.Series(values, index=idx)


def test_sma_window_is_100_and_frozen():
    assert SMA_WINDOW == 100


def test_sma_partial_window_is_nan_no_backfill():
    s = _series(150)
    sma = compute_sma(s)
    assert sma.iloc[:99].isna().all()
    assert sma.iloc[99:].notna().all()


def test_sma_value_matches_windowed_mean():
    s = _series(150)
    sma = compute_sma(s)
    expected = s.iloc[50:150].mean()
    assert sma.iloc[149] == pytest.approx(expected, rel=1e-12)


def test_signal_strict_inequality_equal_case_is_false():
    idx = pd.date_range("2026-01-01", periods=1, freq="1h", tz="UTC")
    close = pd.Series([100.0], index=idx)
    sma = pd.Series([100.0], index=idx)
    sig = compute_signal(close, sma)
    assert bool(sig.iloc[0]) is False


def test_signal_true_above_false_below():
    idx = pd.date_range("2026-01-01", periods=2, freq="1h", tz="UTC")
    close = pd.Series([101.0, 99.0], index=idx)
    sma = pd.Series([100.0, 100.0], index=idx)
    sig = compute_signal(close, sma)
    assert list(sig) == [True, False]


def test_target_weights_are_literal_0_or_1_only():
    s = _series(150)
    w = compute_target_weights(s, "BTC")
    values = set(w["BTC"].dropna().unique())
    assert values <= {0.0, 1.0}


def test_build_strategy_output_first_row_unconditional_rebalance():
    s = _series(150)
    frame_index = s.index[99:]
    out = build_strategy_output_for_frame(s, frame_index, "BTC")
    assert bool(out.rebalance_mask.iloc[0]) is True


def test_build_strategy_output_rejects_partial_window_frame():
    s = _series(150)
    frame_index = s.index[50:]  # includes rows with undefined (NaN) SMA
    with pytest.raises(DataIntegrityError):
        build_strategy_output_for_frame(s, frame_index, "BTC")


def test_build_strategy_output_rebalance_mask_reflects_signal_changes():
    """The mask must be `True` on row 0 unconditionally (spec §1.3) and,
    for every subsequent row, `True` iff the target weight CHANGED from the
    previous row — never inferred from price directly. This is checked
    against an INDEPENDENTLY computed signal series (via `compute_signal`
    on the same raw closes), not hand-derived SMA arithmetic, so the test
    verifies the WIRING (slicing + `rebalance_on_change` application), which
    is exactly the part `build_strategy_output_for_frame` adds beyond
    `compute_signal`/`compute_target_weights` themselves.
    """
    rng = np.random.default_rng(42)
    idx = pd.date_range("2026-01-01", periods=250, freq="1h", tz="UTC")
    prices = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.02, size=250))
    s = pd.Series(prices, index=idx)
    frame_index = idx[99:]

    out = build_strategy_output_for_frame(s, frame_index, "BTC")
    expected_signal = compute_signal(s).loc[frame_index]

    expected_mask = [True] + [
        bool(expected_signal.iloc[k]) != bool(expected_signal.iloc[k - 1])
        for k in range(1, len(expected_signal))
    ]
    assert list(out.rebalance_mask) == expected_mask
    # Sanity: this random fixture must contain BOTH at least one True and
    # one False rebalance decision after row 0, else the comparison above
    # would be vacuously satisfied by an all-True or all-False mask.
    assert True in expected_mask[1:]
    assert False in expected_mask[1:]
