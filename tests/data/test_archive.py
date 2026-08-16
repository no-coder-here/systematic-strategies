"""D§14 — Hyperliquid archival reconstruction: VALIDATION-ONLY.

Every test here uses MOCKED trade/fill records. Nothing in this file (or in
`src/data/hyperliquid/archive.py`) ever touches the Requester-Pays archive
buckets (F9) — the full backfill is CANCELLED (D§14.0) and this module is
retained purely for the fills-vs-trades (D§14.3) and hash-non-uniqueness
(D§14.4) traps, plus bounded D§14.5 overlap-validation machinery.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from data.hyperliquid.archive import (
    OverlapReport,
    assert_not_quote_derived,
    compare_ohlcv_overlap,
    deduplicate_trade_records,
    merge_with_official_priority,
    reduce_fills_to_trades,
    trades_to_ohlcv,
    verify_hourly_coverage,
)


def _fill(tid, side, px, sz, t):
    return {"tid": tid, "side": side, "px": px, "sz": sz, "time": t}


T0 = pd.Timestamp("2025-06-01 10:00:00", tz="UTC")


# ---------------------------------------------------------------------------
# D§14.3 (M19) — fills-vs-trades double-counting trap.
# ---------------------------------------------------------------------------


def test_reduce_fills_to_trades_keeps_one_side_per_pair():
    fills = [
        _fill("t1", "maker", 100.0, 5.0, T0),
        _fill("t1", "taker", 100.0, 5.0, T0),
        _fill("t2", "maker", 101.0, 3.0, T0 + pd.Timedelta(seconds=1)),
        _fill("t2", "taker", 101.0, 3.0, T0 + pd.Timedelta(seconds=1)),
    ]
    trades = reduce_fills_to_trades(fills)
    assert len(trades) == 2
    assert sum(t["sz"] for t in trades) == 8.0  # NOT 16.0 (M19's double-count signature)


def test_reduce_fills_to_trades_rejects_incomplete_pair():
    fills = [_fill("t1", "maker", 100.0, 5.0, T0)]  # missing counterparty
    with pytest.raises(DataIntegrityError):
        reduce_fills_to_trades(fills)


def test_reduce_fills_to_trades_rejects_mismatched_sz():
    fills = [
        _fill("t1", "maker", 100.0, 5.0, T0),
        _fill("t1", "taker", 100.0, 4.999, T0),  # disagreement -- not the same trade
    ]
    with pytest.raises(DataIntegrityError):
        reduce_fills_to_trades(fills)


def test_fills_aggregated_without_reduction_would_double_volume_M19():
    """Demonstrates the M19 mutation DIRECTLY: aggregating raw (unreduced)
    fills produces exactly 2x the volume of the reduced trades.
    """
    fills = [
        _fill("t1", "maker", 100.0, 5.0, T0),
        _fill("t1", "taker", 100.0, 5.0, T0),
        _fill("t2", "maker", 101.0, 3.0, T0 + pd.Timedelta(minutes=1)),
        _fill("t2", "taker", 101.0, 3.0, T0 + pd.Timedelta(minutes=1)),
    ]
    reduced = reduce_fills_to_trades(fills)
    reduced_volume = sum(t["sz"] for t in reduced)
    unreduced_volume = sum(f["sz"] for f in fills)
    assert unreduced_volume == 2 * reduced_volume


# ---------------------------------------------------------------------------
# D§14.4 (M20) — hash non-uniqueness dedup trap.
# ---------------------------------------------------------------------------


def test_deduplicate_on_hash_alone_is_prohibited():
    records = [{"hash": "0xabc", "px": 100.0}]
    with pytest.raises(ValueError):
        deduplicate_trade_records(records, key_fields=["hash"])


def test_deduplicate_collapses_true_byte_identical_duplicate():
    rec = {"hash": "0xabc", "time": 1, "px": 100.0, "sz": 1.0}
    records = [dict(rec), dict(rec)]  # boundary re-emission: byte-identical
    out = deduplicate_trade_records(records, key_fields=["hash", "time", "px", "sz"])
    assert len(out) == 1


def test_deduplicate_on_hash_alone_would_collapse_distinct_trades_M20():
    """The precise M20 defect: two DIFFERENT trades sharing one `hash`
    (F11: hash is a TRANSACTION hash spanning multiple trades) MUST NOT be
    collapsed. Using a fine-grained key correctly keeps both; using `hash`
    alone (prohibited above) would have silently understated volume.
    """
    trade_a = {"hash": "0xabc", "time": 1, "px": 100.0, "sz": 1.0}
    trade_b = {"hash": "0xabc", "time": 2, "px": 101.0, "sz": 2.0}  # same hash, DIFFERENT trade
    out = deduplicate_trade_records([trade_a, trade_b], key_fields=["hash", "time", "px", "sz"])
    assert len(out) == 2  # both retained -- NOT collapsed by shared hash


def test_deduplicate_raises_on_non_identical_collision():
    records = [
        {"hash": "0xabc", "time": 1, "px": 100.0, "sz": 1.0},
        {"hash": "0xabc", "time": 1, "px": 999.0, "sz": 1.0},  # same key fields, DIFFERENT px
    ]
    with pytest.raises(DataIntegrityError):
        deduplicate_trade_records(records, key_fields=["hash", "time"])


# ---------------------------------------------------------------------------
# trades_to_ohlcv + D§3.1.1 naive-timestamp refusal.
# ---------------------------------------------------------------------------


def test_trades_to_ohlcv_aggregates_into_buckets():
    trades = [
        {"time": T0, "px": 100.0, "sz": 1.0},
        {"time": T0 + pd.Timedelta(minutes=30), "px": 105.0, "sz": 2.0},
        {"time": T0 + pd.Timedelta(minutes=59), "px": 102.0, "sz": 1.0},
    ]
    df = trades_to_ohlcv(trades, "BTC", pd.Timedelta(hours=1), "hyperliquid_node_trades")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["open"] == 100.0
    assert row["close"] == 102.0
    assert row["high"] == 105.0
    assert row["low"] == 100.0
    assert row["volume"] == 4.0
    assert row["trade_count"] == 3


def test_trades_to_ohlcv_rejects_naive_timestamp_D3_1_1():
    trades = [{"time": pd.Timestamp("2025-06-01 10:00:00"), "px": 100.0, "sz": 1.0}]  # naive
    with pytest.raises(DataIntegrityError):
        trades_to_ohlcv(trades, "BTC", pd.Timedelta(hours=1), "hyperliquid_node_trades")


def test_trades_to_ohlcv_rejects_non_archive_source_type():
    with pytest.raises(ValueError):
        trades_to_ohlcv([], "BTC", pd.Timedelta(hours=1), "hyperliquid_candle")


# ---------------------------------------------------------------------------
# D§14.5 (BLOCKING) — overlap validation: per-field agreement DISTRIBUTIONS.
# ---------------------------------------------------------------------------


def test_compare_ohlcv_overlap_reports_percentile_distributions_not_pass_fail():
    idx = pd.date_range("2025-06-01", periods=5, freq="1h", tz="UTC")
    official = pd.DataFrame(
        {"timestamp": idx, "open": [100.0] * 5, "high": [101.0] * 5, "low": [99.0] * 5,
         "close": [100.5] * 5, "volume": [10.0] * 5}
    )
    reconstructed = official.copy()
    reconstructed["volume"] = official["volume"] * 2.0  # the D§14.3 signature

    report = compare_ohlcv_overlap(reconstructed, official, "BTC")
    assert isinstance(report, OverlapReport)
    assert report.n_overlapping_bars == 5
    # every percentile of the volume RATIO should be exactly 2.0 -- the
    # unmistakable D§14.3 fills-doubling signature, surfaced explicitly
    # rather than buried in a generic aggregate diff number.
    for p, v in report.volume_ratio_percentiles.items():
        assert v == pytest.approx(2.0)
    for p, v in report.field_relative_diff_percentiles["open"].items():
        assert v == pytest.approx(0.0)


def test_compare_ohlcv_overlap_reports_missing_bars():
    idx = pd.date_range("2025-06-01", periods=3, freq="1h", tz="UTC")
    official = pd.DataFrame(
        {"timestamp": idx, "open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3, "close": [1.0] * 3, "volume": [1.0] * 3}
    )
    reconstructed = official.iloc[:2]  # missing the last official bar
    report = compare_ohlcv_overlap(reconstructed, official, "BTC")
    assert report.missing_bars_in_reconstruction == 1


# ---------------------------------------------------------------------------
# D§14.4 (M28) — per-HOUR coverage, not per-day.
# ---------------------------------------------------------------------------


def test_verify_hourly_coverage_detects_missing_hour_M28():
    start = pd.Timestamp("2025-06-01 00:00", tz="UTC")
    end = pd.Timestamp("2025-06-01 04:00", tz="UTC")
    have = [start, start + pd.Timedelta(hours=1), start + pd.Timedelta(hours=3)]  # hour 2 missing
    missing = verify_hourly_coverage(have, start, end)
    assert missing == [start + pd.Timedelta(hours=2)]


def test_verify_hourly_coverage_reports_nothing_when_complete():
    start = pd.Timestamp("2025-06-01 00:00", tz="UTC")
    end = pd.Timestamp("2025-06-01 03:00", tz="UTC")
    have = list(pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left"))
    assert verify_hourly_coverage(have, start, end) == []


# ---------------------------------------------------------------------------
# D§14.7 (M21) — quote-derived source rejection.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source_kind", ["l2_book", "mid_px", "asset_ctxs_mid_px"])
def test_quote_derived_sources_rejected_M21(source_kind):
    with pytest.raises(DataIntegrityError):
        assert_not_quote_derived(source_kind)


def test_non_quote_source_not_rejected():
    assert_not_quote_derived("hyperliquid_node_trades")  # does not raise


# ---------------------------------------------------------------------------
# D§14.1 (M22) — official candles ALWAYS win over reconstruction in overlap.
# ---------------------------------------------------------------------------


def test_official_wins_over_reconstructed_in_overlap_M22():
    idx = pd.date_range("2025-06-01", periods=3, freq="1h", tz="UTC")
    official = pd.DataFrame({"timestamp": idx, "open": [1.0, 2.0, 3.0], "volume": [10.0, 20.0, 30.0]})
    reconstructed = pd.DataFrame({"timestamp": idx, "open": [999.0, 999.0, 999.0], "volume": [999.0, 999.0, 999.0]})

    merged = merge_with_official_priority(reconstructed, official)
    merged = merged.set_index("timestamp")
    for ts in idx:
        assert merged.loc[ts, "open"] == official.set_index("timestamp").loc[ts, "open"]
        assert merged.loc[ts, "volume"] != 999.0


def test_reconstructed_fills_gaps_official_does_not_cover():
    idx_official = pd.date_range("2025-06-01 01:00", periods=2, freq="1h", tz="UTC")
    idx_extra = pd.date_range("2025-06-01 00:00", periods=1, freq="1h", tz="UTC")
    official = pd.DataFrame({"timestamp": idx_official, "open": [1.0, 2.0], "volume": [10.0, 20.0]})
    reconstructed = pd.DataFrame(
        {"timestamp": idx_extra.append(idx_official), "open": [0.5, 999.0, 999.0], "volume": [5.0, 999.0, 999.0]}
    )
    merged = merge_with_official_priority(reconstructed, official).set_index("timestamp")
    assert merged.loc[idx_extra[0], "open"] == 0.5  # reconstructed fills the gap official doesn't cover
    assert merged.loc[idx_official[0], "open"] == 1.0  # official still wins where both exist


def test_verify_hourly_coverage_a_missing_hour_treated_as_present_would_be_a_defect_M28():
    """A buggy implementation that ALWAYS returns `[]` regardless of real
    coverage (i.e. "treats a missing hour as present") must be caught by
    `test_verify_hourly_coverage_detects_missing_hour_M28` above, NOT
    silently accepted. This test pins the contract that the function's
    return value is genuinely a function of its input (not a constant),
    which is what makes the mutation table's RED requirement meaningful.
    """
    start = pd.Timestamp("2025-06-01 00:00", tz="UTC")
    end = pd.Timestamp("2025-06-01 02:00", tz="UTC")
    complete = list(pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left"))
    incomplete = complete[:-1]
    assert verify_hourly_coverage(complete, start, end) != verify_hourly_coverage(incomplete, start, end)
