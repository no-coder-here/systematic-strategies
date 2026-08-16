"""D§3.1.1, D§4.6, D§6.3 — base.py unit tests."""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from backtest.models import DataIntegrityError, UniverseProvenance
from data.base import (
    MAX_FUNDING_GAP,
    SymbolMeta,
    UniverseSnapshot,
    ensure_utc_timestamp,
    to_engine_frame,
)


def test_max_funding_gap_pinned_90_minutes():
    # D§5.4 — MUST be exactly 90 minutes, never inferred.
    assert MAX_FUNDING_GAP == pd.Timedelta(minutes=90)


def test_ensure_utc_timestamp_rejects_naive():
    # D§3.1.1 (M14) — naive timestamps are REJECTED, never localized.
    naive = datetime.datetime(2026, 1, 1, 0, 0, 0)
    with pytest.raises(DataIntegrityError, match="naive timestamp"):
        ensure_utc_timestamp(naive)


def test_ensure_utc_timestamp_converts_non_utc_tz():
    ny_ts = pd.Timestamp("2026-01-01 00:00:00", tz="America/New_York")
    out = ensure_utc_timestamp(ny_ts)
    assert str(out.tz) == "UTC"
    assert out == ny_ts.tz_convert("UTC")


def test_ensure_utc_timestamp_passthrough_already_utc():
    utc_ts = pd.Timestamp("2026-01-01", tz="UTC")
    assert ensure_utc_timestamp(utc_ts) == utc_ts


def test_universe_snapshot_rejects_duplicate_symbol_dict_keys_is_moot_but_construction_ok():
    # dict keys are inherently unique; this exercises normal construction.
    meta = {
        "BTC": SymbolMeta("BTC", 0, 5, 40, False, 1),
    }
    snap = UniverseSnapshot(
        retrieved_at=pd.Timestamp.utcnow().tz_localize("UTC") if pd.Timestamp.utcnow().tzinfo is None else pd.Timestamp.utcnow(),
        venue="Hyperliquid",
        symbols=meta,
        provenance=UniverseProvenance(
            universe_source="hyperliquid.info.meta",
            universe_asof_policy="point_in_time_inferred_from_first_last_native_trade",
            listing_data_source="inferred_from_candle_activity",
            survivorship_safe=False,
        ),
    )
    assert snap.symbols["BTC"].symbol == "BTC"


def test_universe_snapshot_rejects_survivorship_safe_true():
    # D§6.3 (M11) — survivorship_safe MUST NOT be True.
    meta = {"BTC": SymbolMeta("BTC", 0, 5, 40, False, 1)}
    with pytest.raises(DataIntegrityError, match="survivorship_safe"):
        UniverseSnapshot(
            retrieved_at=pd.Timestamp("2026-01-01", tz="UTC"),
            venue="Hyperliquid",
            symbols=meta,
            provenance=UniverseProvenance(
                universe_source="hyperliquid.info.meta",
                universe_asof_policy="point_in_time_inferred_from_first_last_native_trade",
                listing_data_source="inferred_from_candle_activity",
                survivorship_safe=True,
            ),
        )


def _ohlcv_row(ts, symbol, price=100.0):
    return {
        "timestamp": ts,
        "symbol": symbol,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price,
        "volume": 1.0,
        "trade_count": 1,
        "native_traded": True,
    }


def _frame(rows):
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    return df


def test_to_engine_frame_raise_policy_regular_grid_ok():
    idx = pd.date_range("2026-01-01", periods=4, freq="1h", tz="UTC")
    rows = [_ohlcv_row(t, "BTC") for t in idx]
    df = _frame(rows)
    md = to_engine_frame(df, "1h", policy="raise")
    assert list(md.open.index) == list(idx)
    assert list(md.open.columns) == ["BTC"]


def test_to_engine_frame_raise_policy_detects_grid_gap():
    # D§4.6 — a whole-market-missing timestamp must raise under "raise".
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    idx_with_gap = idx.delete(2)  # drop the 3rd bar entirely (no symbol has it)
    rows = [_ohlcv_row(t, "BTC") for t in idx_with_gap]
    df = _frame(rows)
    with pytest.raises(DataIntegrityError, match="grid gap"):
        to_engine_frame(df, "1h", policy="raise")


def test_to_engine_frame_segment_policy_splits_at_gap():
    idx = pd.date_range("2026-01-01", periods=6, freq="1h", tz="UTC")
    idx_with_gap = idx.delete(3)
    rows = [_ohlcv_row(t, "BTC") for t in idx_with_gap]
    df = _frame(rows)
    segments = to_engine_frame(df, "1h", policy="segment")
    assert len(segments) == 2
    assert len(segments[0].open) == 3
    assert len(segments[1].open) == 2


def test_to_engine_frame_reindex_nan_policy_inserts_nan_row():
    idx = pd.date_range("2026-01-01", periods=5, freq="1h", tz="UTC")
    idx_with_gap = idx.delete(2)
    rows = [_ohlcv_row(t, "BTC") for t in idx_with_gap]
    df = _frame(rows)
    md = to_engine_frame(df, "1h", policy="reindex_nan")
    assert len(md.open) == 5
    assert md.open["BTC"].isna().sum() == 1


def test_to_engine_frame_no_ffill_policy_exists():
    # D§4.6 — there is no "ffill" policy and one MUST NOT be added.
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    rows = [_ohlcv_row(t, "BTC") for t in idx]
    df = _frame(rows)
    with pytest.raises(ValueError, match="unknown to_engine_frame policy"):
        to_engine_frame(df, "1h", policy="ffill")
