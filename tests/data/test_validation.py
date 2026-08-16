"""D§10 — ValidationReport is data, never a print statement."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import FundingCoverage
from data.base import MAX_FUNDING_GAP
from data.schemas import OHLCV_COLUMNS, FUNDING_COLUMNS
from data.validation import validate_funding, validate_ohlcv


def _ohlcv_df(rows):
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    df["trade_count"] = df["trade_count"].astype("int64")
    df["native_traded"] = df["native_traded"].astype("bool")
    df["source_venue"] = df["source_venue"].astype("string")
    df["native_or_proxy"] = df["native_or_proxy"].astype("string")
    df["source_type"] = df["source_type"].astype("string")
    df["dataset_id"] = df["dataset_id"].astype("string")
    return df[OHLCV_COLUMNS]


def _row(ts, symbol="BTC", o=100.0, h=101.0, l=99.0, c=100.0, v=1.0, n=1, native_traded=True,
         source_venue="Hyperliquid", native_or_proxy="native", source_type="hyperliquid_candle",
         dataset_id="hyperliquid.ohlcv.1d.BTC"):
    return {
        "timestamp": ts, "symbol": symbol, "open": o, "high": h, "low": l, "close": c,
        "volume": v, "trade_count": n, "native_traded": native_traded,
        "source_venue": source_venue, "native_or_proxy": native_or_proxy,
        "source_type": source_type, "dataset_id": dataset_id,
    }


def test_report_is_a_dataclass_not_a_print():
    df = _ohlcv_df([_row(pd.Timestamp("2024-01-01", tz="UTC"))])
    report = validate_ohlcv(df)
    assert hasattr(report, "findings")
    assert hasattr(report, "status")
    assert report.status == "ok"


def test_duplicate_key_detected():
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    df = _ohlcv_df([_row(ts), _row(ts)])
    report = validate_ohlcv(df)
    assert report.status == "failed"
    assert "DUPLICATE_KEY" in report.counts


def test_non_monotonic_timestamp_detected():
    t0 = pd.Timestamp("2024-01-01 01:00", tz="UTC")
    t1 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    df = _ohlcv_df([_row(t0), _row(t1)])
    report = validate_ohlcv(df)
    assert "NON_MONOTONIC_TIMESTAMP" in report.counts


def test_naive_timestamp_detected():
    df = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2024-01-01")],
            "symbol": pd.Series(["BTC"], dtype="string"),
            "open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0],
            "volume": [1.0], "trade_count": pd.array([1], dtype="int64"),
            "native_traded": [True],
            "source_venue": pd.Series(["Hyperliquid"], dtype="string"),
            "native_or_proxy": pd.Series(["native"], dtype="string"),
            "source_type": pd.Series(["hyperliquid_candle"], dtype="string"),
            "dataset_id": pd.Series(["hyperliquid.ohlcv.1d.BTC"], dtype="string"),
        }
    )[OHLCV_COLUMNS]
    report = validate_ohlcv(df)
    assert "NAIVE_TIMESTAMP" in report.counts
    assert report.status == "failed"


def test_malformed_ohlc_high_below_low_detected():
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    df = _ohlcv_df([_row(ts, h=90.0, l=99.0)])
    report = validate_ohlcv(df)
    assert "MALFORMED_OHLC" in report.counts


def test_negative_volume_detected():
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    df = _ohlcv_df([_row(ts, v=-1.0)])
    report = validate_ohlcv(df)
    assert "NEGATIVE_OR_NONFINITE_VOLUME" in report.counts


def test_backfill_prefix_present_flagged():
    idx = pd.date_range("2024-01-01", periods=3, freq="1d", tz="UTC")
    rows = [
        _row(idx[0], v=0.0, n=0, native_traded=False),
        _row(idx[1], v=0.0, n=0, native_traded=False),
        _row(idx[2], v=10.0, n=5, native_traded=True),
    ]
    df = _ohlcv_df(rows)
    report = validate_ohlcv(df)
    assert "BACKFILL_PREFIX_PRESENT" in report.counts
    assert report.status == "warnings"  # warning, not error


def test_spot_style_symbol_detected():
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    df = _ohlcv_df([_row(ts, symbol="BTC/USDC")])
    report = validate_ohlcv(df)
    assert "SPOT_STYLE_SYMBOL" in report.counts


# -- funding ----------------------------------------------------------------


def _funding_df(rows):
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    return df[FUNDING_COLUMNS]


def _frow(ts, symbol="BTC", rate=0.0001, premium=0.0002, notional=None):
    return {"timestamp": ts, "symbol": symbol, "funding_rate": rate, "premium": premium, "notional_price": notional}


def test_funding_gap_exceeded_detected():
    t0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    t1 = t0 + MAX_FUNDING_GAP + pd.Timedelta(minutes=1)
    df = _funding_df([_frow(t0), _frow(t1)])
    report = validate_funding(df)
    assert "FUNDING_GAP_EXCEEDED" in report.counts


def test_funding_gap_at_exactly_max_not_flagged():
    t0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    t1 = t0 + MAX_FUNDING_GAP
    df = _funding_df([_frow(t0), _frow(t1)])
    report = validate_funding(df)
    assert "FUNDING_GAP_EXCEEDED" not in report.counts


def test_implausible_rate_advisory():
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    df = _funding_df([_frow(t0, rate=0.02)])
    report = validate_funding(df)
    assert "IMPLAUSIBLE_RATE" in report.counts
    assert report.status == "warnings"


def test_non_finite_rate_error():
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    df = _funding_df([_frow(t0, rate=float("nan"))])
    report = validate_funding(df)
    assert "NON_FINITE_RATE" in report.counts
    assert report.status == "failed"


def test_false_coverage_detected_D5_6():
    t0 = pd.Timestamp("2024-01-01 02:00", tz="UTC")
    t1 = pd.Timestamp("2024-01-01 03:00", tz="UTC")
    df = _funding_df([_frow(t0), _frow(t1)])
    # coverage falsely claims a WIDER span than actually retrieved
    coverage = [
        FundingCoverage(
            symbol="BTC",
            coverage_start=pd.Timestamp("2024-01-01 00:00", tz="UTC"),
            coverage_end=pd.Timestamp("2024-01-01 05:00", tz="UTC"),
            max_funding_gap=MAX_FUNDING_GAP,
            source_venue="Hyperliquid",
        )
    ]
    report = validate_funding(df, coverage=coverage)
    assert "FALSE_COVERAGE" in report.counts


def test_coverage_not_disjoint_detected():
    t0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    t1 = pd.Timestamp("2024-01-01 05:00", tz="UTC")
    df = _funding_df([_frow(t0), _frow(t1)])
    coverage = [
        FundingCoverage(symbol="BTC", coverage_start=t0, coverage_end=pd.Timestamp("2024-01-01 03:00", tz="UTC"),
                         max_funding_gap=MAX_FUNDING_GAP, source_venue="Hyperliquid"),
        FundingCoverage(symbol="BTC", coverage_start=pd.Timestamp("2024-01-01 02:00", tz="UTC"), coverage_end=t1,
                         max_funding_gap=MAX_FUNDING_GAP, source_venue="Hyperliquid"),
    ]
    report = validate_funding(df, coverage=coverage)
    assert "COVERAGE_NOT_DISJOINT" in report.counts
