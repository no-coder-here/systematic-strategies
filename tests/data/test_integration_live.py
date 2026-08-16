"""D§11.3 — live integration validation (network-gated).

Marked `@pytest.mark.integration`; skipped by default (see root conftest.py's
`--run-integration` option), never required by the unit suite. Talks ONLY to
the public, read-only Hyperliquid `info` endpoint. Places no orders, requests
no credentials.

Run explicitly with:
    pytest tests/data/test_integration_live.py --run-integration -q -s
"""

from __future__ import annotations

import pandas as pd
import pytest

from data.hyperliquid.client import HyperliquidClient
from data.hyperliquid.provider import HyperliquidProvider

pytestmark = pytest.mark.integration


def _report_ohlcv_1h(client: HyperliquidClient, coin: str) -> dict:
    now = pd.Timestamp.utcnow()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    start = now - pd.Timedelta(days=250)  # comfortably beyond the ~208-day 1h depth (F2)
    provider = HyperliquidProvider(client=client)
    df = provider.get_ohlcv([coin], "1h", start, now)

    ts = df["timestamp"]
    gaps = []
    if len(ts) >= 2:
        diffs = ts.diff().dropna()
        expected = pd.Timedelta(hours=1)
        bad = diffs[diffs != expected]
        gaps = [(ts.iloc[i - 1], ts.iloc[i]) for i in diffs.index if diffs.loc[i] != expected]
    dup_count = int(df.duplicated(subset=["timestamp"]).sum())

    return {
        "coin": coin,
        "first_timestamp": ts.iloc[0] if len(ts) else None,
        "last_timestamp": ts.iloc[-1] if len(ts) else None,
        "bar_count": len(df),
        "gap_count": len(gaps),
        "gaps_sample": gaps[:5],
        "duplicate_count": dup_count,
        "timezone": str(ts.dtype.tz) if len(ts) else None,
    }


def _report_funding(client: HyperliquidClient, coin: str) -> dict:
    now = pd.Timestamp.utcnow()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    start = now - pd.Timedelta(days=30)
    provider = HyperliquidProvider(client=client)
    df = provider.get_funding([coin], start, now)
    coverage = provider.get_funding_coverage([coin], start, now)

    ts = df["timestamp"]
    spacings = ts.diff().dropna()
    stats = {}
    if len(spacings):
        stats = {
            "min": spacings.min(),
            "median": spacings.median(),
            "max": spacings.max(),
            "modal": spacings.mode().iloc[0] if not spacings.mode().empty else None,
        }

    return {
        "coin": coin,
        "first_event": ts.iloc[0] if len(ts) else None,
        "last_event": ts.iloc[-1] if len(ts) else None,
        "event_count": len(df),
        "spacing_stats": stats,
        "coverage_segments": [
            (c.coverage_start, c.coverage_end) for c in coverage
        ],
        "coverage_segment_count": len(coverage),
    }


@pytest.mark.parametrize("coin", ["BTC", "ETH"])
def test_live_1h_ohlcv_report(coin):
    client = HyperliquidClient()
    report = _report_ohlcv_1h(client, coin)
    print(f"\n[D§11.3] {coin} 1h OHLCV: {report}")
    assert report["bar_count"] > 0
    assert report["timezone"] == "UTC"
    assert report["duplicate_count"] == 0


@pytest.mark.parametrize("coin", ["BTC", "ETH"])
def test_live_funding_report(coin):
    client = HyperliquidClient()
    report = _report_funding(client, coin)
    print(f"\n[D§11.3] {coin} funding: {report}")
    assert report["event_count"] > 0


@pytest.mark.parametrize("coin", ["BTC", "ETH"])
def test_live_4h_and_1d_generation_and_offline_reload(tmp_path, coin):
    client = HyperliquidClient()
    provider = HyperliquidProvider(client=client)
    now = pd.Timestamp.utcnow()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    start = now - pd.Timedelta(days=10)

    for frequency in ("4h", "1d"):
        df = provider.get_ohlcv([coin], frequency, start, now)
        assert len(df) > 0

        from data import storage
        from data.provenance import HyperliquidDatasetProvenance, PROCESSING_VERSION

        prov = HyperliquidDatasetProvenance(
            dataset_id=storage.ohlcv_dataset_id(frequency, coin),
            source_venue="Hyperliquid",
            source_type="ohlcv",
            native_or_proxy="native",
            retrieved_at=now,
            start_timestamp=df["timestamp"].iloc[0],
            end_timestamp=df["timestamp"].iloc[-1],
            symbols=(coin,),
            frequency=frequency,
            processing_version=PROCESSING_VERSION,
            endpoint="candleSnapshot",
            request_windows=(),
            api_response_count=len(df),
            code_version=None,
        )
        storage.write_ohlcv_parquet(tmp_path, frequency, coin, df, prov)

        offline_provider = HyperliquidProvider(offline=True, storage_base_dir=tmp_path)
        reloaded = offline_provider.get_ohlcv([coin], frequency, start, now)
        assert len(reloaded) == len(df)
        print(f"\n[D§11.3] {coin} {frequency}: generated {len(df)} bars, offline reload OK")
