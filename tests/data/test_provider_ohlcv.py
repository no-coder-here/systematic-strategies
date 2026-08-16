"""D§3, D§4 — HyperliquidProvider.get_ohlcv() normalization pipeline.

Covers D§11.1 adversarial fixtures: a naive timestamp, a zero-volume prefix,
an interior zero-volume bar, unsorted responses, malformed OHLC.
"""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from backtest.models import DataIntegrityError
from data.hyperliquid.provider import HyperliquidProvider

from conftest import DAY_MS, HOUR_MS, candle

DAY0 = pd.Timestamp("2024-01-01", tz="UTC")
DAY0_MS = int(DAY0.timestamp() * 1000)


def _day(n: int) -> int:
    return DAY0_MS + n * DAY_MS


def _hour(n: int) -> int:
    return DAY0_MS + n * HOUR_MS


def _make_provider(multi_client, candles_1d=None, candles_1h=None, candles_4h=None):
    client, transport = multi_client(
        candles={"1d": candles_1d or {}, "1h": candles_1h or {}, "4h": candles_4h or {}}
    )
    provider = HyperliquidProvider(client=client)
    return provider, transport


def test_backfill_leading_run_excluded_D4_4(multi_client):
    # D§4.4 (M2) — 3 backfilled (v=0,n=0) 1d bars, THEN native trading starts.
    candles_1d = {
        _day(0): candle(_day(0), 100, 101, 99, 100, 0.0, 0, "1d"),
        _day(1): candle(_day(1), 100, 101, 99, 100, 0.0, 0, "1d"),
        _day(2): candle(_day(2), 100, 101, 99, 100, 0.0, 0, "1d"),
        _day(3): candle(_day(3), 100, 105, 99, 103, 50.0, 20, "1d"),  # first_native
        _day(4): candle(_day(4), 103, 108, 101, 106, 60.0, 25, "1d"),
    }
    # 1h bars covering day 0 (pre-listing backfill, should be EXCLUDED) through day 4
    candles_1h = {}
    for h in range(0, 5 * 24):
        candles_1h[_hour(h)] = candle(_hour(h), 100, 101, 99, 100, 0.0 if h < 3 * 24 else 5.0,
                                       0 if h < 3 * 24 else 3, "1h")

    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h=candles_1h)
    df = provider.get_ohlcv(["BTC"], "1h", DAY0, DAY0 + pd.Timedelta(days=5))

    first_native_1d = pd.Timestamp(_day(3), unit="ms", tz="UTC")
    assert df["timestamp"].min() == first_native_1d
    assert (df["timestamp"] < first_native_1d).sum() == 0


def test_interior_zero_volume_bar_retained_D4_4(multi_client):
    # D§4.4 (M3) — an interior zero-volume bar AFTER first_native is a
    # genuine illiquid bar and MUST be retained (flagged), never dropped.
    candles_1d = {
        _day(0): candle(_day(0), 100, 105, 99, 103, 50.0, 20, "1d"),  # native from day 0
    }
    candles_1h = {
        _hour(0): candle(_hour(0), 100, 101, 99, 100, 5.0, 3, "1h"),
        _hour(1): candle(_hour(1), 100, 101, 99, 100, 0.0, 0, "1h"),  # interior illiquid bar
        _hour(2): candle(_hour(2), 100, 101, 99, 100, 5.0, 3, "1h"),
    }
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h=candles_1h)
    df = provider.get_ohlcv(["BTC"], "1h", DAY0, DAY0 + pd.Timedelta(hours=3))

    assert len(df) == 3  # all three bars retained
    interior = df[df["timestamp"] == pd.Timestamp(_hour(1), unit="ms", tz="UTC")]
    assert len(interior) == 1
    assert interior["native_traded"].iloc[0] == False  # noqa: E712 -- flagged, not dropped
    assert interior["volume"].iloc[0] == 0.0


def test_no_forward_fill_D4_6(multi_client):
    # D§4.6 (M4) — a genuine gap in the raw series MUST remain a gap, never
    # be filled with a repeated/synthetic value.
    candles_1d = {_day(0): candle(_day(0), 100, 105, 99, 103, 50.0, 20, "1d")}
    candles_1h = {
        _hour(0): candle(_hour(0), 100, 101, 99, 100, 5.0, 3, "1h"),
        # _hour(1) intentionally MISSING -- a real venue gap
        _hour(2): candle(_hour(2), 200, 201, 199, 200, 5.0, 3, "1h"),
    }
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h=candles_1h)
    df = provider.get_ohlcv(["BTC"], "1h", DAY0, DAY0 + pd.Timedelta(hours=3))

    assert len(df) == 2  # NOT 3 -- no synthetic bar was inserted for the gap
    assert set(df["timestamp"]) == {
        pd.Timestamp(_hour(0), unit="ms", tz="UTC"),
        pd.Timestamp(_hour(2), unit="ms", tz="UTC"),
    }


def test_malformed_ohlc_blocking_D4_3(multi_client):
    candles_1d = {_day(0): candle(_day(0), 100, 105, 99, 103, 50.0, 20, "1d")}
    bad_bar = candle(_hour(0), 100, 90, 99, 100, 5.0, 3, "1h")  # high < low
    candles_1h = {_hour(0): bad_bar}
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h=candles_1h)
    with pytest.raises(DataIntegrityError, match="D§4.3"):
        provider.get_ohlcv(["BTC"], "1h", DAY0, DAY0 + pd.Timedelta(hours=1))


def test_negative_volume_blocking_D4_3(multi_client):
    candles_1d = {_day(0): candle(_day(0), 100, 105, 99, 103, 50.0, 20, "1d")}
    bad_bar = candle(_hour(0), 100, 101, 99, 100, -5.0, 3, "1h")
    candles_1h = {_hour(0): bad_bar}
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h=candles_1h)
    with pytest.raises(DataIntegrityError, match="D§4.3"):
        provider.get_ohlcv(["BTC"], "1h", DAY0, DAY0 + pd.Timedelta(hours=1))


def test_t_field_violation_raises_D3_1_2(multi_client):
    candles_1d = {_day(0): candle(_day(0), 100, 105, 99, 103, 50.0, 20, "1d")}
    bad_bar = candle(_hour(0), 100, 101, 99, 100, 5.0, 3, "1h")
    bad_bar["T"] = bad_bar["t"] + 999  # violates T == t + delta - 1ms
    candles_1h = {_hour(0): bad_bar}
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h=candles_1h)
    with pytest.raises(DataIntegrityError, match="D§3.1.2"):
        provider.get_ohlcv(["BTC"], "1h", DAY0, DAY0 + pd.Timedelta(hours=1))


def test_naive_timestamp_rejected_D3_1_1(multi_client):
    candles_1d = {_day(0): candle(_day(0), 100, 105, 99, 103, 50.0, 20, "1d")}
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h={})
    naive = datetime.datetime(2024, 1, 1)
    with pytest.raises(DataIntegrityError, match="naive timestamp"):
        provider.get_ohlcv(["BTC"], "1h", naive, DAY0 + pd.Timedelta(hours=1))
    assert len(transport.calls) == 0  # rejected BEFORE any network call


def test_unsorted_raw_response_is_sorted(multi_client):
    # D§11.1 — an out-of-order response must still be normalized to sorted order.
    candles_1d = {_day(0): candle(_day(0), 100, 105, 99, 103, 50.0, 20, "1d")}

    class ReorderingTransport:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def __call__(self, body):
            import json

            payload = json.loads(body)
            self.calls.append(payload)
            if payload["type"] == "candleSnapshot" and payload["req"]["interval"] == "1h":
                bars = [
                    candle(_hour(2), 300, 301, 299, 300, 5.0, 3, "1h"),
                    candle(_hour(0), 100, 101, 99, 100, 5.0, 3, "1h"),
                    candle(_hour(1), 200, 201, 199, 200, 5.0, 3, "1h"),
                ]
                return json.dumps(bars).encode()
            return json.dumps(self.inner._route(payload)).encode()

    client, transport = multi_client(candles={"1d": candles_1d, "1h": {}, "4h": {}})
    from data.hyperliquid.client import HyperliquidClient

    reordering = ReorderingTransport(transport)
    client2 = HyperliquidClient(transport=reordering, max_retries=2, backoff_base_seconds=0.0)
    provider = HyperliquidProvider(client=client2)

    df = provider.get_ohlcv(["BTC"], "1h", DAY0, DAY0 + pd.Timedelta(hours=3))
    assert list(df["timestamp"]) == sorted(df["timestamp"])


def test_full_4h_bucket_aggregated_from_1h_D4_5(multi_client):
    # D§4.5 — native 4h absent entirely; a FULL 4h bucket (4 constituent 1h
    # bars, all present) MUST be aggregated and tagged "aggregated".
    candles_1d = {_day(0): candle(_day(0), 100, 108, 95, 106, 50.0, 20, "1d")}
    candles_1h = {
        _hour(0): candle(_hour(0), 100, 102, 99, 101, 1.0, 1, "1h"),
        _hour(1): candle(_hour(1), 101, 105, 100, 104, 2.0, 2, "1h"),
        _hour(2): candle(_hour(2), 104, 106, 103, 105, 3.0, 3, "1h"),
        _hour(3): candle(_hour(3), 105, 108, 95, 106, 4.0, 4, "1h"),
    }
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h=candles_1h, candles_4h={})
    df = provider.get_ohlcv(["BTC"], "4h", DAY0, DAY0 + pd.Timedelta(hours=4))

    assert len(df) == 1
    row = df.iloc[0]
    assert row["source_type"] == "hyperliquid_candle"
    assert row["is_aggregated"] == True  # noqa: E712 — D§4.5 native-vs-aggregated flag
    assert row["open"] == 100.0  # first 1h open
    assert row["close"] == 106.0  # last 1h close
    assert row["high"] == 108.0  # max high
    assert row["low"] == 95.0  # min low
    assert row["volume"] == 10.0  # sum
    assert row["trade_count"] == 10  # sum


def test_partial_4h_bucket_not_emitted_D4_5_M15(multi_client):
    # D§4.5 (M15) — only 3 of 4 constituent 1h bars present -> the bucket
    # MUST NOT be emitted at all (never a partial aggregate).
    candles_1d = {_day(0): candle(_day(0), 100, 108, 95, 106, 50.0, 20, "1d")}
    candles_1h = {
        _hour(0): candle(_hour(0), 100, 102, 99, 101, 1.0, 1, "1h"),
        _hour(1): candle(_hour(1), 101, 105, 100, 104, 2.0, 2, "1h"),
        # _hour(2) missing
        _hour(3): candle(_hour(3), 105, 108, 95, 106, 4.0, 4, "1h"),
    }
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h=candles_1h, candles_4h={})
    df = provider.get_ohlcv(["BTC"], "4h", DAY0, DAY0 + pd.Timedelta(hours=4))
    assert len(df) == 0


def test_native_4h_preferred_over_aggregation(multi_client):
    # D§4.5 — native candleSnapshot at the requested interval is preferred;
    # aggregation is a fallback, not used when native data is present.
    candles_1d = {_day(0): candle(_day(0), 100, 110, 90, 105, 50.0, 20, "1d")}
    candles_4h = {_hour(0): candle(_hour(0), 999, 999, 999, 999, 999.0, 999, "4h")}
    provider, transport = _make_provider(multi_client, candles_1d=candles_1d, candles_1h={}, candles_4h=candles_4h)
    df = provider.get_ohlcv(["BTC"], "4h", DAY0, DAY0 + pd.Timedelta(hours=4))
    assert len(df) == 1
    assert df.iloc[0]["source_type"] == "hyperliquid_candle"
    assert df.iloc[0]["is_aggregated"] == False  # noqa: E712 — native, not aggregated
    assert df.iloc[0]["open"] == 999.0


# ---------------------------------------------------------------------------
# D§8.1 — raw-response archival wiring (fixes a self-reported v1.0 gap:
# `storage.write_raw_response` existed but was never called by the
# provider's LIVE fetch path).
# ---------------------------------------------------------------------------


def test_raw_response_archived_when_opted_in(multi_client, tmp_path):
    from data import storage

    candles_1d = {_day(0): candle(_day(0), 100, 101, 99, 100.5, 5.0, 3, "1d")}
    client, transport = multi_client(candles={"1d": candles_1d, "1h": {}, "4h": {}})
    provider = HyperliquidProvider(client=client, storage_base_dir=tmp_path, archive_raw_responses=True)
    provider.get_ohlcv(["BTC"], "1d", DAY0, DAY0 + pd.Timedelta(days=1))

    raw_root = tmp_path / "raw" / "hyperliquid" / "candleSnapshot" / "BTC" / "1d"
    assert raw_root.exists()
    files = list(raw_root.glob("*.json.gz"))
    assert len(files) >= 1


def test_raw_response_not_archived_by_default(multi_client, tmp_path):
    candles_1d = {_day(0): candle(_day(0), 100, 101, 99, 100.5, 5.0, 3, "1d")}
    client, transport = multi_client(candles={"1d": candles_1d, "1h": {}, "4h": {}})
    provider = HyperliquidProvider(client=client, storage_base_dir=tmp_path)  # archive_raw_responses defaults False
    provider.get_ohlcv(["BTC"], "1d", DAY0, DAY0 + pd.Timedelta(days=1))
    assert not (tmp_path / "raw").exists()
