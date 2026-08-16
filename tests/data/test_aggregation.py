"""D§4.5 — shared, venue-agnostic aggregation-from-1h logic.

Exercised directly here (unit-level, both a Hyperliquid-shaped and a
Binance-shaped 1h frame use the exact same function) as well as indirectly
via `tests/data/test_provider_ohlcv.py` (Hyperliquid) and
`tests/data/test_binance_provider.py` (Binance) — this is what makes M15
("emit a partial 4h bucket from incomplete 1h bars") a single shared
invariant rather than two independently-fallible implementations.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from data.aggregation import aggregate_ohlcv_1h_to


def _bar(t, o, h, l, c, v, n, native=True):
    return {
        "timestamp": t, "symbol": "BTC", "open": o, "high": h, "low": l, "close": c,
        "volume": v, "trade_count": n, "native_traded": native,
    }


IDX = pd.date_range("2024-01-01", periods=8, freq="1h", tz="UTC")


def test_full_4h_bucket_aggregated():
    rows = [
        _bar(IDX[0], 100, 102, 99, 101, 1.0, 1),
        _bar(IDX[1], 101, 105, 100, 104, 2.0, 2),
        _bar(IDX[2], 104, 106, 103, 105, 3.0, 3),
        _bar(IDX[3], 105, 108, 95, 106, 4.0, 4),
    ]
    df_1h = pd.DataFrame(rows)
    out = aggregate_ohlcv_1h_to(df_1h, "4h", IDX[0], IDX[0] + pd.Timedelta(hours=4))
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 100
    assert row["close"] == 106
    assert row["high"] == 108
    assert row["low"] == 95
    assert row["volume"] == 10.0
    assert row["trade_count"] == 10


def test_partial_bucket_not_emitted_M15():
    rows = [
        _bar(IDX[0], 100, 102, 99, 101, 1.0, 1),
        _bar(IDX[1], 101, 105, 100, 104, 2.0, 2),
        # IDX[2] missing -- only 3 of 4 constituent bars present
        _bar(IDX[3], 105, 108, 95, 106, 4.0, 4),
    ]
    df_1h = pd.DataFrame(rows)
    out = aggregate_ohlcv_1h_to(df_1h, "4h", IDX[0], IDX[0] + pd.Timedelta(hours=4))
    assert len(out) == 0


def test_1d_bucket_requires_24_bars():
    idx24 = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")
    rows = [_bar(t, 100, 101, 99, 100, 1.0, 1) for t in idx24]
    df_1h = pd.DataFrame(rows)
    out = aggregate_ohlcv_1h_to(df_1h, "1d", idx24[0], idx24[0] + pd.Timedelta(days=1))
    assert len(out) == 1
    assert out.iloc[0]["volume"] == 24.0


def test_empty_input_returns_empty():
    empty = pd.DataFrame(columns=["timestamp", "symbol", "open", "high", "low", "close", "volume", "trade_count", "native_traded"])
    out = aggregate_ohlcv_1h_to(empty, "4h", IDX[0], IDX[0] + pd.Timedelta(hours=4))
    assert len(out) == 0


def test_rejects_1h_as_bucket_frequency():
    df_1h = pd.DataFrame([_bar(IDX[0], 1, 1, 1, 1, 1.0, 1)])
    with pytest.raises(ValueError):
        aggregate_ohlcv_1h_to(df_1h, "1h", IDX[0], IDX[1])


def test_rejects_multi_symbol_frame():
    rows = [_bar(IDX[0], 1, 1, 1, 1, 1.0, 1)]
    rows.append({**rows[0], "symbol": "ETH"})
    df_1h = pd.DataFrame(rows)
    with pytest.raises(DataIntegrityError):
        aggregate_ohlcv_1h_to(df_1h, "4h", IDX[0], IDX[0] + pd.Timedelta(hours=4))


def test_native_traded_any_of_constituents():
    rows = [
        _bar(IDX[0], 100, 100, 100, 100, 0.0, 0, native=False),
        _bar(IDX[1], 100, 100, 100, 100, 0.0, 0, native=False),
        _bar(IDX[2], 100, 100, 100, 100, 5.0, 3, native=True),
        _bar(IDX[3], 100, 100, 100, 100, 0.0, 0, native=False),
    ]
    df_1h = pd.DataFrame(rows)
    out = aggregate_ohlcv_1h_to(df_1h, "4h", IDX[0], IDX[0] + pd.Timedelta(hours=4))
    assert bool(out.iloc[0]["native_traded"]) is True


# ---------------------------------------------------------------------------
# Audit finding D1 — bucket boundaries are a property of the DATA, anchored to
# a fixed UTC epoch grid, NEVER a function of the caller's query window.
#
# The original implementation anchored on `window_start`, so identical 1h input
# produced [00:00,04:00,08:00) for window_start=00:00 but [01:00,05:00) for
# window_start=01:00 — two different, non-comparable "4h" series with different
# closes. Because 1h is the sole canonical stored frequency (D§16.4),
# aggregation is the ONLY path to 4h/1d, so this corrupted every derived bar for
# any query that did not happen to start on a grid boundary.
#
# Every pre-existing test in this module passed window_start=IDX[0] (UTC
# midnight), which is exactly why the defect survived a green suite. These tests
# deliberately use MISALIGNED window starts.
# ---------------------------------------------------------------------------

_DAY = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")
_DAY_END = _DAY[-1] + pd.Timedelta(hours=1)


def _day_frame():
    # close == hour-of-day, so a mis-anchored bucket is immediately visible in
    # the aggregated `close` and cannot be confused with a correct one.
    return pd.DataFrame([_bar(t, i, i, i, i, 1.0, 1) for i, t in enumerate(_DAY)])


@pytest.mark.parametrize("start_hour", [1, 2, 3, 5, 7])
def test_D1_bucket_boundaries_independent_of_query_window(start_hour):
    """Misaligned queries MUST still land on the epoch grid (00,04,08,12,16,20)."""
    df = _day_frame()
    out = aggregate_ohlcv_1h_to(df, "4h", _DAY[start_hour], _DAY_END)
    hours = [t.hour for t in out["timestamp"]]
    assert all(h % 4 == 0 for h in hours), f"off-grid buckets {hours} for start_hour={start_hour}"
    # and every emitted bucket must close on its own last constituent hour
    for _, r in out.iterrows():
        assert r["close"] == r["timestamp"].hour + 3


def test_D1_overlapping_buckets_identical_across_different_query_windows():
    """Two different query windows must agree bar-for-bar where they overlap."""
    df = _day_frame()
    a = aggregate_ohlcv_1h_to(df, "4h", _DAY[0], _DAY_END).set_index("timestamp")
    b = aggregate_ohlcv_1h_to(df, "4h", _DAY[3], _DAY_END).set_index("timestamp")
    shared = a.index.intersection(b.index)
    # self-guard: the test is only meaningful if the two windows actually overlap
    assert len(shared) >= 3, "fixture no longer produces overlapping buckets — test would be inert"
    pd.testing.assert_frame_equal(a.loc[shared], b.loc[shared])


def test_D1_daily_buckets_anchor_to_utc_midnight_not_query_start():
    df = _day_frame()
    out = aggregate_ohlcv_1h_to(df, "1d", _DAY[5], _DAY_END)
    # a 1d bucket anchored at 05:00 would be partial and must not be emitted;
    # the only complete UTC-midnight day starts before the query, so nothing is emitted
    assert out.empty or all(t.hour == 0 for t in out["timestamp"])
