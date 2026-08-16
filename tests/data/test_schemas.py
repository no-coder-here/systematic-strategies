"""D§4.1 — fixed OHLCV schema, mandatory per-row source-attribution columns."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from data.schemas import OHLCV_COLUMNS, RESERVED_SOURCE_TYPES, assert_ohlcv_schema, assert_source_type_allowed, empty_ohlcv_frame


def _full_row(ts):
    return {
        "timestamp": ts, "symbol": "BTC", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
        "volume": 1.0, "trade_count": 1, "native_traded": True,
        "source_venue": "Hyperliquid", "native_or_proxy": "native",
        "source_type": "hyperliquid_candle", "dataset_id": "ds1",
    }


def _df(rows):
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    df["trade_count"] = df["trade_count"].astype("int64")
    df["native_traded"] = df["native_traded"].astype("bool")
    for c in ("source_venue", "native_or_proxy", "source_type", "dataset_id"):
        df[c] = df[c].astype("string")
    return df[OHLCV_COLUMNS]


def test_row_level_attribution_columns_are_mandatory_M23():
    """D§4.1 (M23) — source_venue/native_or_proxy/source_type/dataset_id are
    MANDATORY per-row columns, in the FIXED schema prefix.
    """
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    df = _df([_full_row(ts)])
    assert_ohlcv_schema(df)  # does not raise
    for col in ("source_venue", "native_or_proxy", "source_type", "dataset_id"):
        assert col in OHLCV_COLUMNS
        assert col in df.columns


def test_dropping_attribution_columns_fails_schema_assertion_M23():
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    df = _df([_full_row(ts)]).drop(columns=["source_type", "dataset_id"])
    with pytest.raises(DataIntegrityError):
        assert_ohlcv_schema(df)


def test_empty_frame_still_carries_all_attribution_columns():
    df = empty_ohlcv_frame()
    for col in ("source_venue", "native_or_proxy", "source_type", "dataset_id"):
        assert col in df.columns


def test_reserved_source_type_refused_at_schema_assertion_M26():
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    row = _full_row(ts)
    row["source_type"] = "external_proxy"
    df = _df([row])
    with pytest.raises(DataIntegrityError):
        assert_ohlcv_schema(df)


def test_assert_source_type_allowed_direct():
    assert_source_type_allowed("hyperliquid_candle")  # does not raise
    with pytest.raises(DataIntegrityError):
        assert_source_type_allowed("external_proxy")
    assert "external_proxy" in RESERVED_SOURCE_TYPES
