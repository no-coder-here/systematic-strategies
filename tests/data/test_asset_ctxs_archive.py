"""D§5.5.1 (v1.3 DECISION 4) — `asset_ctxs` compact streaming extraction:
incremental refresh, peak-footprint, alignment validation. Every test here
uses a MOCKED `segment_fetcher`; none ever calls `s3_segment_fetcher` or
touches the network/AWS credentials.
"""

from __future__ import annotations

import pandas as pd
import pytest

from data.hyperliquid.asset_ctxs_archive import (
    AssetCtxsHighWaterMark,
    archive_dates_in_range,
    build_alignment_report,
    compact_parquet_path,
    read_high_water_mark,
    run_incremental_extraction,
    write_high_water_mark,
)


def _ctx(t, coin, oracle_px):
    """`t`: an ISO-8601 string (e.g. '2024-01-01T00:04:00') -- converted here
    to a tz-aware UTC `pd.Timestamp`, one of the two forms `_parse_row`
    accepts (D§3.1.1: never a naive timestamp)."""
    return {"time": pd.Timestamp(t, tz="UTC"), "coin": coin, "oracle_px": oracle_px}


def _funding_events(rows):
    df = pd.DataFrame(rows)
    df["symbol"] = df["symbol"].astype("string")
    return df[["timestamp", "symbol"]]


def _mock_fetcher(segments: dict):
    """`segments`: {date_str: [row_dict, ...]}. Returns a callable matching
    `SegmentFetcher`, recording which dates were actually fetched.
    """
    calls = []

    def fetcher(date):
        calls.append(date)
        return iter(segments.get(date, []))

    fetcher.calls = calls
    return fetcher


# ---------------------------------------------------------------------------
# Segment enumeration
# ---------------------------------------------------------------------------


def test_archive_dates_in_range_inclusive():
    dates = archive_dates_in_range("2024-01-01", "2024-01-03")
    assert dates == ["2024-01-01", "2024-01-02", "2024-01-03"]


def test_archive_dates_in_range_rejects_inverted_range():
    with pytest.raises(ValueError):
        archive_dates_in_range("2024-01-05", "2024-01-01")


# ---------------------------------------------------------------------------
# High-water mark persistence
# ---------------------------------------------------------------------------


def test_high_water_mark_roundtrip(tmp_path):
    hwm = AssetCtxsHighWaterMark(last_processed_date="2024-01-02", processed_dates=("2024-01-01", "2024-01-02"),
                                  total_rows=42)
    write_high_water_mark(tmp_path, hwm)
    reloaded = read_high_water_mark(tmp_path)
    assert reloaded == hwm


def test_high_water_mark_empty_when_absent(tmp_path):
    hwm = read_high_water_mark(tmp_path)
    assert hwm.last_processed_date is None
    assert hwm.processed_dates == ()
    assert hwm.total_rows == 0


# ---------------------------------------------------------------------------
# Basic extraction correctness (including cross-day boundary carry-forward)
# ---------------------------------------------------------------------------


def test_basic_extraction_single_day(tmp_path):
    events = _funding_events([{"timestamp": pd.Timestamp("2024-01-01 00:05:00", tz="UTC"), "symbol": "BTC"}])
    segments = {
        "2024-01-01": [
            _ctx("2024-01-01T00:03:00", "BTC", 100.0),
            _ctx("2024-01-01T00:04:00", "BTC", 101.0),
        ],
    }
    fetcher = _mock_fetcher(segments)
    result = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-01", segment_fetcher=fetcher)
    assert result.dates_processed == ("2024-01-01",)
    assert result.rows_appended == 1
    assert result.skipped_no_op is False
    df = pd.read_parquet(compact_parquet_path(tmp_path))
    assert len(df) == 1
    assert df.iloc[0]["oracle_price"] == pytest.approx(101.0)


def test_cross_day_boundary_event_uses_previous_day_carried_row(tmp_path):
    """An event at 00:01 on day 2 (within the 2-min gap tolerance of day 1's
    LAST row, 23:59:30 on day 1) MUST resolve using the carried-forward
    `last_row_by_symbol` state, not be left unpriced merely because the
    answering row lives in a DIFFERENT day's segment.
    """
    events = _funding_events([{"timestamp": pd.Timestamp("2024-01-02 00:01:00", tz="UTC"), "symbol": "BTC"}])
    segments = {
        "2024-01-01": [_ctx("2024-01-01T23:59:30", "BTC", 555.0)],
        "2024-01-02": [],  # nothing on day 2 itself
    }
    fetcher = _mock_fetcher(segments)
    result = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-02", segment_fetcher=fetcher)
    assert set(result.dates_processed) == {"2024-01-01", "2024-01-02"}
    df = pd.read_parquet(compact_parquet_path(tmp_path))
    assert len(df) == 1
    assert df.iloc[0]["oracle_price"] == pytest.approx(555.0)


def test_event_beyond_gap_tolerance_left_unpriced(tmp_path):
    events = _funding_events([{"timestamp": pd.Timestamp("2024-01-01 00:10:00", tz="UTC"), "symbol": "BTC"}])
    segments = {"2024-01-01": [_ctx("2024-01-01T00:00:00", "BTC", 100.0)]}  # 10 min before -- beyond 2min tolerance
    fetcher = _mock_fetcher(segments)
    result = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-01", segment_fetcher=fetcher)
    df = pd.read_parquet(compact_parquet_path(tmp_path))
    assert len(df) == 1
    assert pd.isna(df.iloc[0]["oracle_price"])


# ---------------------------------------------------------------------------
# D§5.5.1 rule 6 (M46) — incremental refresh is a NO-OP over an
# already-processed range: the fetcher must NOT be called again, and no
# duplicate rows are appended.
# ---------------------------------------------------------------------------


def test_incremental_refresh_is_a_noop_over_processed_range_M46(tmp_path):
    events = _funding_events(
        [
            {"timestamp": pd.Timestamp("2024-01-01 00:05:00", tz="UTC"), "symbol": "BTC"},
            {"timestamp": pd.Timestamp("2024-01-02 00:05:00", tz="UTC"), "symbol": "BTC"},
        ]
    )
    segments = {
        "2024-01-01": [_ctx("2024-01-01T00:04:00", "BTC", 100.0)],
        "2024-01-02": [_ctx("2024-01-02T00:04:00", "BTC", 200.0)],
    }
    fetcher = _mock_fetcher(segments)
    first = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-02", segment_fetcher=fetcher)
    assert first.skipped_no_op is False
    assert fetcher.calls == ["2024-01-01", "2024-01-02"]
    assert first.total_rows == 2

    # Refresh over the SAME (already fully-processed) range.
    second = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-02", segment_fetcher=fetcher)
    assert second.skipped_no_op is True
    assert second.dates_processed == ()
    assert second.rows_appended == 0
    assert fetcher.calls == ["2024-01-01", "2024-01-02"]  # NOT called again -- length unchanged

    df = pd.read_parquet(compact_parquet_path(tmp_path))
    assert len(df) == 2  # no duplicate rows appended


def test_incremental_refresh_only_fetches_new_dates_M46(tmp_path):
    """A refresh whose range PARTIALLY overlaps the processed history only
    fetches the genuinely NEW dates -- never re-fetches already-processed
    ones.
    """
    events = _funding_events(
        [
            {"timestamp": pd.Timestamp("2024-01-01 00:05:00", tz="UTC"), "symbol": "BTC"},
            {"timestamp": pd.Timestamp("2024-01-02 00:05:00", tz="UTC"), "symbol": "BTC"},
            {"timestamp": pd.Timestamp("2024-01-03 00:05:00", tz="UTC"), "symbol": "BTC"},
        ]
    )
    segments = {
        "2024-01-01": [_ctx("2024-01-01T00:04:00", "BTC", 100.0)],
        "2024-01-02": [_ctx("2024-01-02T00:04:00", "BTC", 200.0)],
        "2024-01-03": [_ctx("2024-01-03T00:04:00", "BTC", 300.0)],
    }
    fetcher = _mock_fetcher(segments)
    run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-01", segment_fetcher=fetcher)
    assert fetcher.calls == ["2024-01-01"]

    result = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-03", segment_fetcher=fetcher)
    # Only 2024-01-02 and 2024-01-03 are new; 2024-01-01 MUST NOT be re-fetched.
    assert result.dates_processed == ("2024-01-02", "2024-01-03")
    assert fetcher.calls == ["2024-01-01", "2024-01-02", "2024-01-03"]

    df = pd.read_parquet(compact_parquet_path(tmp_path))
    assert len(df) == 3


# ---------------------------------------------------------------------------
# D§5.5.1 rules 2/3 (M47) — peak local raw footprint stays at ~one segment;
# no raw is ever retained on disk after extraction.
# ---------------------------------------------------------------------------


def test_no_raw_asset_ctxs_ever_written_to_disk_M47(tmp_path):
    events = _funding_events([{"timestamp": pd.Timestamp("2024-01-01 00:05:00", tz="UTC"), "symbol": "BTC"}])
    segments = {"2024-01-01": [_ctx("2024-01-01T00:04:00", "BTC", 100.0)]}
    fetcher = _mock_fetcher(segments)
    run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-01", segment_fetcher=fetcher)
    raw_root = tmp_path / "raw"
    assert not raw_root.exists()  # never created -- nothing durable is ever written for the raw segment


def test_generator_segment_fetcher_never_materialized_as_a_list_M47():
    """The orchestration MUST consume `segment_fetcher`'s return value as a
    stream (it works correctly even when the fetcher returns a
    single-use, non-restartable generator) -- a version that tried to
    iterate it twice (e.g. once to "measure size", once to process) would
    silently see an EMPTY second pass and lose data.
    """
    import tempfile

    events = _funding_events([{"timestamp": pd.Timestamp("2024-01-01 00:05:00", tz="UTC"), "symbol": "BTC"}])
    consumed_once = {"done": False}

    def fetcher(date):
        assert not consumed_once["done"], "segment_fetcher generator was consumed more than once"
        consumed_once["done"] = True
        yield _ctx("2024-01-01T00:04:00", "BTC", 100.0)

    with tempfile.TemporaryDirectory() as d:
        result = run_incremental_extraction(d, events, "2024-01-01", "2024-01-01", segment_fetcher=fetcher)
        assert result.rows_appended == 1


# ---------------------------------------------------------------------------
# D§5.5.1 rule 7 (M48) — alignment validation: priced/unpriced counts, and
# the event→ctx offset distribution.
# ---------------------------------------------------------------------------


def test_alignment_report_counts_priced_and_unpriced(tmp_path):
    events = _funding_events(
        [
            {"timestamp": pd.Timestamp("2024-01-01 00:05:00", tz="UTC"), "symbol": "BTC"},  # priced (1 min gap)
            {"timestamp": pd.Timestamp("2024-01-01 00:20:00", tz="UTC"), "symbol": "BTC"},  # unpriced (no row within tol)
        ]
    )
    segments = {"2024-01-01": [_ctx("2024-01-01T00:04:00", "BTC", 100.0)]}
    fetcher = _mock_fetcher(segments)
    result = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-01", segment_fetcher=fetcher)
    df = pd.read_parquet(compact_parquet_path(tmp_path))

    report = build_alignment_report(df, events, event_ctx_offsets=result.event_ctx_offsets)
    assert report.n_events == 2
    assert report.n_priced == 1
    assert report.n_unpriced == 1
    assert report.gap_tolerance_seconds == 120.0


def test_alignment_report_offset_distribution_within_gap_tolerance_M48(tmp_path):
    events = _funding_events(
        [{"timestamp": pd.Timestamp("2024-01-01 00:05:30", tz="UTC"), "symbol": "BTC"}],
    )
    segments = {"2024-01-01": [_ctx("2024-01-01T00:04:00", "BTC", 100.0)]}  # 90s before
    fetcher = _mock_fetcher(segments)
    result = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-01", segment_fetcher=fetcher)
    df = pd.read_parquet(compact_parquet_path(tmp_path))

    report = build_alignment_report(df, events, event_ctx_offsets=result.event_ctx_offsets)
    assert report.n_priced == 1
    assert report.max_offset_seconds == pytest.approx(90.0)
    assert report.max_offset_seconds <= report.gap_tolerance_seconds


def test_alignment_report_never_silently_leaves_event_unpriced_past_gap_M48():
    """D§5.5.1 rule 7 (M48) — an event beyond the 2-minute gap MUST show up
    as UNPRICED in the alignment report, never silently counted as priced.
    This directly discriminates a defect that forward-fills past the gap
    (which would report n_unpriced=0 here, incorrectly).
    """
    import tempfile

    events = _funding_events([{"timestamp": pd.Timestamp("2024-01-01 00:10:00", tz="UTC"), "symbol": "BTC"}])
    segments = {"2024-01-01": [_ctx("2024-01-01T00:00:00", "BTC", 100.0)]}  # 10 min before -- beyond tolerance
    fetcher = _mock_fetcher(segments)
    with tempfile.TemporaryDirectory() as d:
        result = run_incremental_extraction(d, events, "2024-01-01", "2024-01-01", segment_fetcher=fetcher)
        df = pd.read_parquet(compact_parquet_path(d))
        report = build_alignment_report(df, events, event_ctx_offsets=result.event_ctx_offsets)
    assert report.n_unpriced == 1
    assert report.n_priced == 0


# ---------------------------------------------------------------------------
# Audit finding D3 — the SEPARATE-CALL cross-day case.
#
# `test_cross_day_boundary_event_uses_previous_day_carried_row` above processes
# both days in ONE call, so `last_row_by_symbol` never crosses a call boundary.
# But D§5.5.1 rule 6 mandates incremental refresh — one call per new day — which
# is exactly when the (previously function-local) carry state was silently lost,
# leaving a near-midnight event unpriced that the single-call path priced fine.
# A defect visible only in the realistic usage pattern.
# ---------------------------------------------------------------------------


def test_D3_carry_state_survives_across_separate_incremental_calls(tmp_path):
    events = _funding_events([{"timestamp": pd.Timestamp("2024-01-02 00:01:00", tz="UTC"), "symbol": "BTC"}])

    # Call 1: day 1 only. Its last row (23:59:30) is the ONLY thing that can
    # price the day-2 event, and day 1 carries no events of its own.
    f1 = _mock_fetcher({"2024-01-01": [_ctx("2024-01-01T23:59:30", "BTC", 555.0)]})
    r1 = run_incremental_extraction(tmp_path, events, "2024-01-01", "2024-01-01", segment_fetcher=f1)
    assert r1.dates_processed == ("2024-01-01",)

    # self-guard: the carry state must actually have been persisted, else the
    # assertion below could pass for the wrong reason.
    hwm = read_high_water_mark(tmp_path)
    assert "BTC" in hwm.carry_rows, "carry state not persisted — test would be inert"

    # Call 2: day 2, which contains no ctx rows at all.
    f2 = _mock_fetcher({"2024-01-02": []})
    run_incremental_extraction(tmp_path, events, "2024-01-02", "2024-01-02", segment_fetcher=f2)

    df = pd.read_parquet(compact_parquet_path(tmp_path))
    row = df[df["symbol"] == "BTC"].iloc[0]
    assert row["oracle_price"] == pytest.approx(555.0), (
        "near-midnight event lost its price across a call boundary (audit D3)"
    )


def test_D3_separate_calls_match_single_call_result(tmp_path):
    """Two-call and one-call processing of the same days must agree."""
    events = _funding_events([{"timestamp": pd.Timestamp("2024-01-02 00:01:00", tz="UTC"), "symbol": "BTC"}])
    segs = {"2024-01-01": [_ctx("2024-01-01T23:59:30", "BTC", 555.0)], "2024-01-02": []}

    one = tmp_path / "one"; one.mkdir()
    run_incremental_extraction(one, events, "2024-01-01", "2024-01-02", segment_fetcher=_mock_fetcher(segs))
    df_one = pd.read_parquet(compact_parquet_path(one))

    two = tmp_path / "two"; two.mkdir()
    run_incremental_extraction(two, events, "2024-01-01", "2024-01-01", segment_fetcher=_mock_fetcher(segs))
    run_incremental_extraction(two, events, "2024-01-02", "2024-01-02", segment_fetcher=_mock_fetcher(segs))
    df_two = pd.read_parquet(compact_parquet_path(two))

    pd.testing.assert_frame_equal(
        df_one.sort_values(["symbol", "timestamp"]).reset_index(drop=True),
        df_two.sort_values(["symbol", "timestamp"]).reset_index(drop=True),
    )
