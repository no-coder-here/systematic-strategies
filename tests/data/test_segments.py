"""D§15.2 — segment manifests for mixed-provenance OHLCV series."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from data.schemas import OHLCV_COLUMNS
from data.segments import (
    SourceSegment,
    assert_segments_agree_with_rows,
    build_segment_manifest,
    frame_uses_proxy_data,
)


def _row(ts, symbol, source_venue, native_or_proxy, source_type, dataset_id):
    return {
        "timestamp": ts, "symbol": symbol, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
        "volume": 1.0, "trade_count": 1, "native_traded": True,
        "source_venue": source_venue, "native_or_proxy": native_or_proxy,
        "source_type": source_type, "dataset_id": dataset_id,
    }


def _df(rows):
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    df["trade_count"] = df["trade_count"].astype("int64")
    df["native_traded"] = df["native_traded"].astype("bool")
    for c in ("source_venue", "native_or_proxy", "source_type", "dataset_id"):
        df[c] = df[c].astype("string")
    return df[OHLCV_COLUMNS]


IDX = pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC")


def test_single_segment_all_same_source():
    rows = [_row(t, "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds1") for t in IDX]
    df = _df(rows)
    segments = build_segment_manifest(df)
    assert len(segments) == 1
    assert segments[0].start_timestamp == IDX[0]
    assert segments[0].end_timestamp == IDX[-1]


def test_two_segments_explicit_transition_D15_2_1():
    rows = [
        _row(IDX[0], "BTC", "Binance", "proxy", "binance_um_kline", "ds_bn"),
        _row(IDX[1], "BTC", "Binance", "proxy", "binance_um_kline", "ds_bn"),
        _row(IDX[2], "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds_hl"),
        _row(IDX[3], "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds_hl"),
    ]
    df = _df(rows)
    segments = build_segment_manifest(df)
    assert len(segments) == 2
    assert segments[0].end_timestamp == IDX[1]
    assert segments[1].start_timestamp == IDX[2]  # EXPLICIT transition, not implied


def test_segments_agree_with_rows_D15_2_2():
    rows = [_row(t, "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds1") for t in IDX]
    df = _df(rows)
    segments = build_segment_manifest(df)
    assert_segments_agree_with_rows(df, segments)  # does not raise


def test_manifest_disagreement_with_rows_raises_M24():
    rows = [_row(t, "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds1") for t in IDX]
    df = _df(rows)
    segments = build_segment_manifest(df)
    # Tamper with the MANIFEST so it disagrees with the (unchanged) rows.
    tampered = (
        SourceSegment(
            source_venue="Binance",  # LIE: rows say Hyperliquid
            native_or_proxy="proxy",
            source_type="binance_um_kline",
            dataset_id="ds1",
            start_timestamp=segments[0].start_timestamp,
            end_timestamp=segments[0].end_timestamp,
        ),
    )
    with pytest.raises(DataIntegrityError):
        assert_segments_agree_with_rows(df, tampered)


def test_overlapping_segments_rejected_D15_2_1():
    seg_a = SourceSegment("Hyperliquid", "native", "hyperliquid_candle", "ds1", IDX[0], IDX[2])
    seg_b = SourceSegment("Hyperliquid", "native", "hyperliquid_candle", "ds1", IDX[1], IDX[3])  # overlaps seg_a
    rows = [_row(t, "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds1") for t in IDX]
    df = _df(rows)
    with pytest.raises(DataIntegrityError):
        assert_segments_agree_with_rows(df, (seg_a, seg_b))


def test_external_proxy_refused_at_segment_construction_M26():
    with pytest.raises(DataIntegrityError):
        SourceSegment(
            source_venue="SomeOtherVenue",
            native_or_proxy="proxy",
            source_type="external_proxy",
            dataset_id="ds",
            start_timestamp=IDX[0],
            end_timestamp=IDX[1],
        )


def test_frame_uses_proxy_data_true_when_any_row_is_proxy():
    rows = [
        _row(IDX[0], "BTC", "Binance", "proxy", "binance_um_kline", "ds_bn"),
        _row(IDX[1], "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds_hl"),
    ]
    assert frame_uses_proxy_data(_df(rows)) is True


def test_frame_uses_proxy_data_false_when_all_native():
    rows = [_row(t, "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds1") for t in IDX]
    assert frame_uses_proxy_data(_df(rows)) is False


def test_build_segment_manifest_requires_sorted_frame():
    rows = [_row(t, "BTC", "Hyperliquid", "native", "hyperliquid_candle", "ds1") for t in IDX]
    rows[0], rows[1] = rows[1], rows[0]  # unsort
    df = pd.DataFrame(rows)[OHLCV_COLUMNS]
    with pytest.raises(DataIntegrityError):
        build_segment_manifest(df)


# ---------------------------------------------------------------------------
# Audit finding D4 — `build_segment_manifest`'s own D§15.2.1 overlap guard was a
# SURVIVING MUTATION: deleting the loop broke zero tests. The only overlap test
# exercised `assert_segments_agree_with_rows` via a hand-built adversarial
# manifest, never `build_segment_manifest`, so its guard was untested.
#
# The branch IS reachable: two consecutive rows sharing a timestamp but carrying
# different provenance make segment B start exactly where segment A ends. That is
# D§15.2.4's prohibited "mixing venues within a SINGLE bar" — real protection,
# so it is tested here rather than deleted as dead code.
# ---------------------------------------------------------------------------


def test_D4_same_timestamp_different_provenance_rejected_by_builder():
    ts = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    df = _df([
        _row(ts, "BTC", "binance", "proxy", "binance_um_kline", "b1"),
        # same bar timestamp, different source => segments would touch at `ts`
        _row(ts, "BTC", "hyperliquid", "native", "hyperliquid_candle", "h1"),
    ])
    with pytest.raises(DataIntegrityError, match="overlap|contig"):
        build_segment_manifest(df)


def test_D4_overlap_guard_is_reachable_and_not_dead_code():
    """Self-guard: proves the builder's own guard fires, so the branch cannot
    silently become unreachable again if the grouping algorithm changes."""
    ts = pd.Timestamp("2024-06-01 12:00", tz="UTC")
    later = ts + pd.Timedelta(hours=1)
    df = _df([
        _row(ts, "ETH", "binance", "proxy", "binance_um_kline", "b1"),
        _row(ts, "ETH", "binance", "proxy", "binance_um_kline", "b2"),  # dataset_id changes ON the same ts
        _row(later, "ETH", "binance", "proxy", "binance_um_kline", "b2"),
    ])
    with pytest.raises(DataIntegrityError):
        build_segment_manifest(df)


def test_D4_clean_transition_at_distinct_timestamps_still_builds():
    """Control: a legitimate bar-boundary transition must still succeed."""
    t0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
    df = _df([
        _row(t0, "BTC", "binance", "proxy", "binance_um_kline", "b1"),
        _row(t0 + pd.Timedelta(hours=1), "BTC", "hyperliquid", "native", "hyperliquid_candle", "h1"),
    ])
    segs = build_segment_manifest(df)
    assert len(segs) == 2
    assert segs[1].start_timestamp > segs[0].end_timestamp
