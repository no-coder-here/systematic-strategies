"""D§9 — provenance construction, engine-narrowing, universe provenance."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import DataIntegrityError, DatasetProvenance, UniverseProvenance
from data.provenance import (
    BinanceDatasetProvenance,
    HyperliquidDatasetProvenance,
    PROCESSING_VERSION,
    build_universe_provenance,
)


def _prov(**overrides):
    base = dict(
        dataset_id="hyperliquid.ohlcv.1h.BTC",
        source_venue="Hyperliquid",
        source_type="ohlcv",
        native_or_proxy="native",
        retrieved_at=pd.Timestamp("2026-08-16", tz="UTC"),
        start_timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
        end_timestamp=pd.Timestamp("2024-01-02", tz="UTC"),
        symbols=("BTC",),
        frequency="1h",
        processing_version=PROCESSING_VERSION,
        endpoint="candleSnapshot",
        request_windows=((0, 1),),
        api_response_count=1,
        code_version="deadbeef",
    )
    base.update(overrides)
    return HyperliquidDatasetProvenance(**base)


def test_processing_version_present_D9_M17():
    prov = _prov()
    assert prov.processing_version == PROCESSING_VERSION
    assert prov.processing_version  # non-empty


def test_to_engine_provenance_shape():
    prov = _prov()
    engine_prov = prov.to_engine_provenance()
    assert isinstance(engine_prov, DatasetProvenance)
    assert engine_prov.source_venue == "Hyperliquid"
    assert engine_prov.native_or_proxy == "native"
    assert engine_prov.dataset_id == "hyperliquid.ohlcv.1h.BTC"
    assert engine_prov.processing_version == PROCESSING_VERSION
    assert engine_prov.time_range == (prov.start_timestamp, prov.end_timestamp)
    assert engine_prov.is_complete


def test_native_or_proxy_must_be_native():
    with pytest.raises(ValueError, match="native"):
        _prov(native_or_proxy="proxy")


def test_json_roundtrip():
    prov = _prov()
    d = prov.to_json_dict()
    restored = HyperliquidDatasetProvenance.from_json_dict(d)
    assert restored == prov


def test_universe_provenance_survivorship_safe_false_D6_3():
    prov = build_universe_provenance()
    assert isinstance(prov, UniverseProvenance)
    assert prov.survivorship_safe is False
    assert prov.universe_source == "hyperliquid.info.meta"
    assert prov.listing_data_source == "inferred_from_candle_activity"


# ---------------------------------------------------------------------------
# D§16.1 / D§9.1 — BinanceDatasetProvenance: native_or_proxy PINNED "proxy"
# (M29), proxy_for required, engine narrowing sets uses_proxy_data-enabling
# native_or_proxy="proxy" + non-empty proxy_for.
# ---------------------------------------------------------------------------


def _binance_prov(**overrides):
    base = dict(
        dataset_id="binance.ohlcv.1h.BTC",
        source_venue="Binance",
        native_or_proxy="proxy",
        proxy_for="Hyperliquid",
        retrieved_at=pd.Timestamp("2026-08-16", tz="UTC"),
        start_timestamp=pd.Timestamp("2020-01-01", tz="UTC"),
        end_timestamp=pd.Timestamp("2024-01-02", tz="UTC"),
        hl_symbol="BTC",
        binance_symbol="BTCUSDT",
        hl_unit_multiplier=1,
        venue_unit_multiplier=1,
        processing_version=PROCESSING_VERSION,
        code_version="deadbeef",
    )
    base.update(overrides)
    return BinanceDatasetProvenance(**base)


def test_binance_provenance_native_or_proxy_pinned_proxy_M29():
    with pytest.raises(ValueError, match="proxy"):
        _binance_prov(native_or_proxy="native")


def test_binance_provenance_source_venue_pinned_binance():
    with pytest.raises(ValueError):
        _binance_prov(source_venue="Hyperliquid")


def test_binance_provenance_requires_proxy_for():
    with pytest.raises(ValueError):
        _binance_prov(proxy_for="")


def test_binance_provenance_to_engine_provenance_shape():
    prov = _binance_prov()
    engine_prov = prov.to_engine_provenance()
    assert isinstance(engine_prov, DatasetProvenance)
    assert engine_prov.source_venue == "Binance"
    assert engine_prov.native_or_proxy == "proxy"
    assert engine_prov.proxy_for == "Hyperliquid"
    assert engine_prov.is_complete
    # D§9 (M17) -- processing_version MUST survive the narrowing to the
    # engine-facing shape for the Binance path too, not just Hyperliquid's
    # (test_to_engine_provenance_shape above only covered the latter).
    assert engine_prov.processing_version == PROCESSING_VERSION


def test_binance_provenance_engine_native_or_proxy_without_proxy_for_would_raise():
    """Sanity-check on the FROZEN engine dataclass itself (§13.1.4): a
    `native_or_proxy=="proxy"` DatasetProvenance with empty `proxy_for`
    raises at construction — this is what makes
    `BinanceDatasetProvenance.__post_init__`'s own `proxy_for` requirement
    non-redundant defense-in-depth rather than decorative.
    """
    with pytest.raises(DataIntegrityError):
        DatasetProvenance(native_or_proxy="proxy", proxy_for="")


def test_binance_provenance_json_roundtrip():
    prov = _binance_prov(checksum_manifest_entries=({"symbol": "BTCUSDT", "month": "2024-01", "sha256": "a" * 64, "rows": 1},))
    d = prov.to_json_dict()
    restored = BinanceDatasetProvenance.from_json_dict(d)
    assert restored == prov
