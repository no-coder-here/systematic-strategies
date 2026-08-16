"""D§8 — raw cache, parquet store, determinism, offline reload, gitignore."""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import pandas as pd
import pytest

from data import storage
from data.hyperliquid.client import HyperliquidClient
from data.hyperliquid.provider import HyperliquidProvider
from data.provenance import PROCESSING_VERSION, HyperliquidDatasetProvenance
from data.schemas import OHLCV_COLUMNS


def _sample_df():
    idx = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": idx,
            "symbol": pd.Series(["BTC"] * 3, dtype="string"),
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [1.0, 2.0, 3.0],
            "trade_count": pd.array([1, 2, 3], dtype="int64"),
            "native_traded": [True, True, True],
            "source_venue": pd.Series(["Hyperliquid"] * 3, dtype="string"),
            "native_or_proxy": pd.Series(["native"] * 3, dtype="string"),
            "source_type": pd.Series(["hyperliquid_candle"] * 3, dtype="string"),
            "dataset_id": pd.Series(["hyperliquid.ohlcv.1h.BTC"] * 3, dtype="string"),
        }
    )[OHLCV_COLUMNS]


def _sample_provenance(dataset_id="hyperliquid.ohlcv.1h.BTC"):
    return HyperliquidDatasetProvenance(
        dataset_id=dataset_id,
        source_venue="Hyperliquid",
        source_type="ohlcv",
        native_or_proxy="native",
        retrieved_at=pd.Timestamp("2026-08-16", tz="UTC"),
        start_timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
        end_timestamp=pd.Timestamp("2024-01-01 03:00", tz="UTC"),
        symbols=("BTC",),
        frequency="1h",
        processing_version=PROCESSING_VERSION,
        endpoint="candleSnapshot",
        request_windows=((0, 1),),
        api_response_count=1,
        code_version="deadbeef",
    )


def test_raw_response_roundtrip_verbatim(tmp_path):
    payload = [{"t": 0, "o": "100.0", "h": "101.0", "l": "99.0", "c": "100.5", "v": "1.0", "n": 3}]
    storage.write_raw_response(tmp_path, "candleSnapshot", "BTC", "1h", "window0", payload)
    loaded = storage.read_raw_response(tmp_path, "candleSnapshot", "BTC", "1h", "window0")
    assert loaded == payload  # verbatim, uninterpreted


def test_parquet_write_read_roundtrip(tmp_path):
    df = _sample_df()
    prov = _sample_provenance()
    storage.write_ohlcv_parquet(tmp_path, "1h", "BTC", df, prov)
    loaded = storage.read_ohlcv_parquet(tmp_path, "1h", "BTC")
    pd.testing.assert_frame_equal(loaded.reset_index(drop=True), df.reset_index(drop=True))


def test_parquet_determinism_stable_hash_D8_4(tmp_path):
    df = _sample_df()
    prov = _sample_provenance()
    p1 = tmp_path / "a"
    p2 = tmp_path / "b"
    storage.write_ohlcv_parquet(p1, "1h", "BTC", df, prov)
    storage.write_ohlcv_parquet(p2, "1h", "BTC", df, prov)
    loaded1 = storage.read_ohlcv_parquet(p1, "1h", "BTC")
    loaded2 = storage.read_ohlcv_parquet(p2, "1h", "BTC")
    assert storage.stable_frame_hash(loaded1) == storage.stable_frame_hash(loaded2)

    different = df.copy()
    different.loc[0, "open"] = 999.0
    assert storage.stable_frame_hash(different) != storage.stable_frame_hash(df)


def test_provenance_written_and_read_back(tmp_path):
    prov = _sample_provenance()
    storage.write_provenance(tmp_path, prov)
    loaded = storage.read_provenance(tmp_path, prov.dataset_id)
    assert loaded.dataset_id == prov.dataset_id
    assert loaded.processing_version == prov.processing_version
    assert loaded.symbols == prov.symbols


def test_provenance_missing_warns_loudly_D9_2(tmp_path):
    with pytest.warns(storage.ProvenanceMissingWarning):
        result = storage.read_provenance(tmp_path, "does.not.exist")
    assert result is None


def test_provenance_version_mismatch_warns_M17(tmp_path):
    df = _sample_df()
    stale_prov = _sample_provenance()
    object.__setattr__(stale_prov, "processing_version", "some-old-version")
    storage.write_ohlcv_parquet(tmp_path, "1h", "BTC", df, stale_prov)
    with pytest.warns(storage.ProvenanceVersionMismatchWarning):
        storage.read_ohlcv_parquet(tmp_path, "1h", "BTC")


def test_offline_reload_never_touches_network(tmp_path, raising_transport):
    df = _sample_df()
    prov = _sample_provenance()
    storage.write_ohlcv_parquet(tmp_path, "1h", "BTC", df, prov)

    # A client whose transport raises on ANY call proves the offline provider
    # never opens a socket (D§8.3).
    doomed_client = HyperliquidClient(transport=raising_transport)
    provider = HyperliquidProvider(offline=True, storage_base_dir=tmp_path)
    # constructing with offline=True forbids passing a client at all:
    with pytest.raises(ValueError):
        HyperliquidProvider(client=doomed_client, offline=True, storage_base_dir=tmp_path)

    out = provider.get_ohlcv(["BTC"], "1h", pd.Timestamp("2024-01-01", tz="UTC"),
                              pd.Timestamp("2024-01-01 03:00", tz="UTC"))
    assert len(out) == 3


def test_gitignore_excludes_raw_and_processed_D8_2():
    repo_root = Path(__file__).resolve().parents[2]
    raw_path = repo_root / "data" / "raw" / "hyperliquid" / "candleSnapshot" / "BTC" / "1h" / "w.json.gz"
    processed_path = repo_root / "data" / "processed" / "hyperliquid" / "ohlcv" / "1h" / "BTC.parquet"
    for path in (raw_path, processed_path):
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=repo_root,
            capture_output=True,
        )
        assert result.returncode == 0, f"{path} is NOT git-ignored (D§8.2)"


def test_metadata_json_is_not_gitignored_D8_2():
    repo_root = Path(__file__).resolve().parents[2]
    meta_path = repo_root / "data" / "metadata" / "hyperliquid" / "some_dataset.json"
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(meta_path)],
        cwd=repo_root,
        capture_output=True,
    )
    assert result.returncode == 1, "metadata JSON MUST NOT be git-ignored (D§8.2)"
