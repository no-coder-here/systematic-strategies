"""D§6 — universe, survivorship, point-in-time membership, rename non-splicing."""

from __future__ import annotations

import pandas as pd
import pytest

from data.base import SymbolMeta
from data.hyperliquid.client import HyperliquidClient
from data.hyperliquid.provider import HyperliquidProvider
from data.universe import KNOWN_RENAME_CANDIDATES, detect_rename_candidates, filter_universe_asof, is_member

from conftest import DAY_MS, candle

DAY0 = pd.Timestamp("2024-01-01", tz="UTC")
DAY0_MS = int(DAY0.timestamp() * 1000)


def _meta_universe():
    return {
        "universe": [
            {"szDecimals": 5, "name": "BTC", "maxLeverage": 40},
            {"szDecimals": 1, "name": "MATIC", "maxLeverage": 20, "isDelisted": True},
            {"szDecimals": 0, "name": "POL", "maxLeverage": 5},
            {"szDecimals": 3, "name": "kPEPE", "maxLeverage": 10},
        ]
    }


def test_universe_includes_delisted_assets_D6_1(multi_client):
    client, transport = multi_client(meta=_meta_universe())
    provider = HyperliquidProvider(client=client)
    snap = provider.get_universe(infer_native_range=False)
    assert "MATIC" in snap.symbols
    assert snap.symbols["MATIC"].is_delisted is True


def test_universe_survivorship_not_filtered_M10(multi_client):
    # M10 — a survivorship-biased implementation would filter is_delisted==False.
    client, transport = multi_client(meta=_meta_universe())
    provider = HyperliquidProvider(client=client)
    snap = provider.get_universe(infer_native_range=False)
    names = set(snap.symbols.keys())
    assert names == {"BTC", "MATIC", "POL", "kPEPE"}


def test_survivorship_safe_always_false_D6_3_M11(multi_client):
    client, transport = multi_client(meta=_meta_universe())
    provider = HyperliquidProvider(client=client)
    snap = provider.get_universe(infer_native_range=False)
    assert snap.provenance.survivorship_safe is False


def test_k_prefix_unit_multiplier_F7(multi_client):
    client, transport = multi_client(meta=_meta_universe())
    provider = HyperliquidProvider(client=client)
    snap = provider.get_universe(infer_native_range=False)
    assert snap.symbols["kPEPE"].unit_multiplier == 1000
    assert snap.symbols["BTC"].unit_multiplier == 1


def test_asset_index_is_array_position_F6(multi_client):
    client, transport = multi_client(meta=_meta_universe())
    provider = HyperliquidProvider(client=client)
    snap = provider.get_universe(infer_native_range=False)
    assert snap.symbols["BTC"].asset_index == 0
    assert snap.symbols["MATIC"].asset_index == 1
    assert snap.symbols["POL"].asset_index == 2
    assert snap.symbols["kPEPE"].asset_index == 3


def test_no_rename_splice_M12():
    # D§6.4 (M12) — MATIC and POL are recorded as distinct symbols; a
    # candidate rename is advisory only, never spliced/merged.
    symbols = {
        "MATIC": SymbolMeta("MATIC", 1, 1, 20, True, 1,
                             first_native_bar=pd.Timestamp("2021-01-01", tz="UTC"),
                             last_native_bar=pd.Timestamp("2024-09-01", tz="UTC")),
        "POL": SymbolMeta("POL", 2, 0, 5, False, 1,
                           first_native_bar=pd.Timestamp("2024-09-11", tz="UTC")),
    }

    class _FakeSnap:
        def __init__(self, symbols):
            self.symbols = symbols

    candidates = detect_rename_candidates(_FakeSnap(symbols))
    assert ("MATIC", "POL") in candidates
    # advisory only: both symbols remain present, independently, in `symbols`.
    assert "MATIC" in symbols and "POL" in symbols
    assert symbols["MATIC"].symbol == "MATIC"
    assert symbols["POL"].symbol == "POL"


def test_no_heuristic_lineage_by_name_similarity_M45():
    """D§16.3.3/D§6.4 (M45) — `detect_rename_candidates` MUST NOT infer a
    rename/migration from symbol name similarity. Here `FOO` (delisted) and
    `FOOX` (live) are textually similar (FOOX starts with FOO) but are NOT a
    reviewed F8 pair -- a fuzzy/heuristic implementation would flag this
    pair; the correct implementation returns nothing for it.
    """
    symbols = {
        "FOO": SymbolMeta("FOO", 1, 1, 20, True, 1,
                          first_native_bar=pd.Timestamp("2021-01-01", tz="UTC"),
                          last_native_bar=pd.Timestamp("2024-09-01", tz="UTC")),
        "FOOX": SymbolMeta("FOOX", 2, 0, 5, False, 1,
                           first_native_bar=pd.Timestamp("2024-09-11", tz="UTC")),
    }

    class _FakeSnap:
        def __init__(self, symbols):
            self.symbols = symbols

    candidates = detect_rename_candidates(_FakeSnap(symbols))
    assert ("FOO", "FOOX") not in candidates
    assert candidates == []


def test_ohlcv_fetch_never_splices_matic_into_pol_M12():
    # D§6.4 (M12) — requesting MATIC's OHLCV MUST return MATIC's own data,
    # never POL's (and vice versa), even though both are per F8 a candidate
    # rename pair. This discriminates a symbol-resolution splice defect.
    import json

    day0_ms = DAY0_MS
    matic_candle = candle(day0_ms, 100, 105, 95, 102, 10.0, 5, "1d", coin="MATIC")
    pol_candle = candle(day0_ms, 900, 905, 895, 902, 10.0, 5, "1d", coin="POL")

    def transport(body: bytes) -> bytes:
        payload = json.loads(body)
        req = payload["req"]
        coin = req["coin"]
        by_coin = {"MATIC": [matic_candle], "POL": [pol_candle]}
        return json.dumps(by_coin.get(coin, [])).encode()

    client = HyperliquidClient(transport=transport, max_retries=2, backoff_base_seconds=0.0)
    provider = HyperliquidProvider(client=client)

    matic_df = provider.get_ohlcv(["MATIC"], "1d", DAY0, DAY0 + pd.Timedelta(days=1))
    pol_df = provider.get_ohlcv(["POL"], "1d", DAY0, DAY0 + pd.Timedelta(days=1))

    assert matic_df["open"].iloc[0] == 100.0  # MATIC's own price, not POL's (902/900)
    assert pol_df["open"].iloc[0] == 900.0  # POL's own price, not MATIC's (100)
    assert matic_df["symbol"].iloc[0] == "MATIC"
    assert pol_df["symbol"].iloc[0] == "POL"


def test_known_rename_candidates_are_the_confirmed_F8_set():
    assert set(KNOWN_RENAME_CANDIDATES) == {("MATIC", "POL"), ("RNDR", "RENDER"), ("FTM", "S")}


def test_point_in_time_membership_D6_2():
    meta = SymbolMeta(
        "MATIC", 1, 1, 20, True, 1,
        first_native_bar=pd.Timestamp("2021-01-01", tz="UTC"),
        last_native_bar=pd.Timestamp("2024-09-01", tz="UTC"),
    )
    assert is_member(meta, pd.Timestamp("2022-01-01", tz="UTC")) is True
    assert is_member(meta, pd.Timestamp("2020-01-01", tz="UTC")) is False  # before listing
    assert is_member(meta, pd.Timestamp("2025-01-01", tz="UTC")) is False  # after delisting


def test_still_listed_symbol_has_no_upper_bound():
    meta = SymbolMeta("BTC", 0, 5, 40, False, 1, first_native_bar=pd.Timestamp("2020-08-19", tz="UTC"))
    assert is_member(meta, pd.Timestamp("2099-01-01", tz="UTC")) is True


def test_filter_universe_asof_excludes_not_yet_members(multi_client):
    client, transport = multi_client(meta=_meta_universe())
    provider = HyperliquidProvider(client=client)
    snap = provider.get_universe(infer_native_range=False)
    # inject explicit first_native_bar values for this test since
    # infer_native_range=False leaves them None
    from dataclasses import replace as dc_replace

    symbols = dict(snap.symbols)
    symbols["BTC"] = dc_replace(symbols["BTC"], first_native_bar=pd.Timestamp("2020-08-19", tz="UTC"))
    symbols["POL"] = dc_replace(symbols["POL"], first_native_bar=pd.Timestamp("2024-09-11", tz="UTC"))
    from dataclasses import replace as snap_replace

    snap2 = snap_replace(snap, symbols=symbols)
    filtered = filter_universe_asof(snap2, pd.Timestamp("2023-01-01", tz="UTC"))
    assert "BTC" in filtered.symbols
    assert "POL" not in filtered.symbols  # not yet listed at this as_of
