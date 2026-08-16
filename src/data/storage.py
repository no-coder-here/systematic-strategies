"""D§8 — raw cache + parquet store; offline reload.

D§2.1: MUST NOT import `src/data/hyperliquid/**`.

Layout (D§8.1):
    data/raw/hyperliquid/<endpoint>/<symbol>/<interval>/<window>.json.gz
    data/processed/hyperliquid/ohlcv/<frequency>/<symbol>.parquet
    data/processed/hyperliquid/funding/<symbol>.parquet
    data/metadata/hyperliquid/<dataset_id>.json
"""

from __future__ import annotations

import gzip
import hashlib
import json
import warnings
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .provenance import PROCESSING_VERSION, BinanceDatasetProvenance, HyperliquidDatasetProvenance
from .schemas import assert_funding_schema, assert_ohlcv_schema

__all__ = [
    "ProvenanceMissingWarning",
    "ProvenanceVersionMismatchWarning",
    "raw_response_path",
    "write_raw_response",
    "read_raw_response",
    "ohlcv_parquet_path",
    "funding_parquet_path",
    "metadata_path",
    "write_ohlcv_parquet",
    "read_ohlcv_parquet",
    "write_funding_parquet",
    "read_funding_parquet",
    "write_provenance",
    "read_provenance",
    "stable_frame_hash",
    "ohlcv_dataset_id",
    "funding_dataset_id",
    "binance_ohlcv_dataset_id",
    "binance_ohlcv_parquet_path",
    "binance_metadata_path",
    "write_binance_provenance",
    "read_binance_provenance",
    "write_binance_ohlcv_parquet",
    "read_binance_ohlcv_parquet",
]


class ProvenanceMissingWarning(UserWarning):
    """D§9.2 — a processed dataset was loaded with no provenance sidecar."""


class ProvenanceVersionMismatchWarning(UserWarning):
    """D§9.2 — a processed dataset's provenance `processing_version` does not
    match the currently running code's `PROCESSING_VERSION`.
    """


def ohlcv_dataset_id(frequency: str, symbol: str) -> str:
    return f"hyperliquid.ohlcv.{frequency}.{symbol}"


def funding_dataset_id(symbol: str) -> str:
    return f"hyperliquid.funding.{symbol}"


def binance_ohlcv_dataset_id(hl_symbol: str) -> str:
    """D§16.4 — 1h is the ONLY canonical stored Binance frequency; there is
    deliberately no `{frequency}` component in this dataset id (unlike
    Hyperliquid's), since no other frequency is ever separately stored.
    """
    return f"binance.ohlcv.1h.{hl_symbol}"


# ---------------------------------------------------------------------------
# D§8.1 paths
# ---------------------------------------------------------------------------


def raw_response_path(base_dir, endpoint: str, symbol: str, interval: str, window_label: str) -> Path:
    return Path(base_dir) / "raw" / "hyperliquid" / endpoint / symbol / interval / f"{window_label}.json.gz"


def ohlcv_parquet_path(base_dir, frequency: str, symbol: str) -> Path:
    return Path(base_dir) / "processed" / "hyperliquid" / "ohlcv" / frequency / f"{symbol}.parquet"


def funding_parquet_path(base_dir, symbol: str) -> Path:
    return Path(base_dir) / "processed" / "hyperliquid" / "funding" / f"{symbol}.parquet"


def metadata_path(base_dir, dataset_id: str) -> Path:
    return Path(base_dir) / "metadata" / "hyperliquid" / f"{dataset_id}.json"


# ---------------------------------------------------------------------------
# D§8.1 — raw verbatim cache
# ---------------------------------------------------------------------------


def write_raw_response(base_dir, endpoint: str, symbol: str, interval: str, window_label: str, payload: Any) -> Path:
    """Stores the raw API response body VERBATIM, uninterpreted (D§8.1)."""
    path = raw_response_path(base_dir, endpoint, symbol, interval, window_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def read_raw_response(base_dir, endpoint: str, symbol: str, interval: str, window_label: str) -> Any:
    path = raw_response_path(base_dir, endpoint, symbol, interval, window_label)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# D§8.4 — determinism helper
# ---------------------------------------------------------------------------


def stable_frame_hash(df: pd.DataFrame) -> str:
    """D§8.4 — a stable hash of the VALUE payload, independent of any embedded
    wall-clock write metadata a serializer (e.g. parquet) might add.

    Two writes of the same normalized frame MUST produce the same hash. Parquet
    file bytes themselves are NOT compared (pyarrow embeds a `created_by` /
    per-write metadata blob), per D§8.4's explicit instruction.
    """
    canon = df.reset_index(drop=True)
    h = hashlib.sha256()
    for col in canon.columns:
        series = canon[col]
        h.update(str(col).encode())
        if pd.api.types.is_datetime64_any_dtype(series):
            h.update(series.view("int64").to_numpy().tobytes())
        elif pd.api.types.is_bool_dtype(series):
            h.update(series.astype("int8").to_numpy().tobytes())
        elif pd.api.types.is_float_dtype(series):
            h.update(series.to_numpy().tobytes())
        elif pd.api.types.is_integer_dtype(series):
            h.update(series.to_numpy().tobytes())
        else:
            h.update("\x1f".join(series.astype(str).tolist()).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# D§8.1 / D§9.2 — processed parquet + provenance sidecar
# ---------------------------------------------------------------------------


def write_provenance(base_dir, provenance: HyperliquidDatasetProvenance) -> Path:
    path = metadata_path(base_dir, provenance.dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(provenance.to_json_dict(), f, indent=2, sort_keys=True)
    return path


def read_provenance(base_dir, dataset_id: str) -> Optional[HyperliquidDatasetProvenance]:
    path = metadata_path(base_dir, dataset_id)
    if not path.exists():
        warnings.warn(
            f"provenance sidecar missing for dataset_id={dataset_id!r} (D§9.2): "
            "a processed dataset written without complete provenance is a defect.",
            ProvenanceMissingWarning,
            stacklevel=2,
        )
        return None
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    prov = HyperliquidDatasetProvenance.from_json_dict(d)
    if prov.processing_version != PROCESSING_VERSION:
        warnings.warn(
            f"processing_version mismatch for dataset_id={dataset_id!r}: "
            f"cached={prov.processing_version!r} running={PROCESSING_VERSION!r} (D§9.2) — "
            "loading MUST NOT silently proceed as if current.",
            ProvenanceVersionMismatchWarning,
            stacklevel=2,
        )
    return prov


def write_ohlcv_parquet(base_dir, frequency: str, symbol: str, df: pd.DataFrame,
                         provenance: HyperliquidDatasetProvenance) -> Path:
    assert_ohlcv_schema(df)
    path = ohlcv_parquet_path(base_dir, frequency, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    write_provenance(base_dir, provenance)
    return path


def read_ohlcv_parquet(base_dir, frequency: str, symbol: str, check_provenance: bool = True) -> pd.DataFrame:
    path = ohlcv_parquet_path(base_dir, frequency, symbol)
    df = pd.read_parquet(path)
    if check_provenance:
        read_provenance(base_dir, ohlcv_dataset_id(frequency, symbol))
    return df


def write_funding_parquet(base_dir, symbol: str, df: pd.DataFrame,
                           provenance: HyperliquidDatasetProvenance) -> Path:
    assert_funding_schema(df)
    path = funding_parquet_path(base_dir, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    write_provenance(base_dir, provenance)
    return path


def read_funding_parquet(base_dir, symbol: str, check_provenance: bool = True) -> pd.DataFrame:
    path = funding_parquet_path(base_dir, symbol)
    df = pd.read_parquet(path)
    if check_provenance:
        read_provenance(base_dir, funding_dataset_id(symbol))
    return df


# ---------------------------------------------------------------------------
# D§16 — Binance canonical 1h storage (D§16.4: single canonical frequency;
# 4h/1d are ALWAYS derived, never separately stored here).
#
#     data/processed/binance/ohlcv/1h/<hl_symbol>.parquet
#     data/metadata/binance/<dataset_id>.json
# ---------------------------------------------------------------------------


def binance_ohlcv_parquet_path(base_dir, hl_symbol: str) -> Path:
    return Path(base_dir) / "processed" / "binance" / "ohlcv" / "1h" / f"{hl_symbol}.parquet"


def binance_metadata_path(base_dir, dataset_id: str) -> Path:
    return Path(base_dir) / "metadata" / "binance" / f"{dataset_id}.json"


def write_binance_provenance(base_dir, provenance: BinanceDatasetProvenance) -> Path:
    path = binance_metadata_path(base_dir, provenance.dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(provenance.to_json_dict(), f, indent=2, sort_keys=True)
    return path


def read_binance_provenance(base_dir, dataset_id: str) -> Optional[BinanceDatasetProvenance]:
    path = binance_metadata_path(base_dir, dataset_id)
    if not path.exists():
        warnings.warn(
            f"Binance provenance sidecar missing for dataset_id={dataset_id!r} (D§9.2)",
            ProvenanceMissingWarning,
            stacklevel=2,
        )
        return None
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    prov = BinanceDatasetProvenance.from_json_dict(d)
    if prov.processing_version != PROCESSING_VERSION:
        warnings.warn(
            f"processing_version mismatch for Binance dataset_id={dataset_id!r}: "
            f"cached={prov.processing_version!r} running={PROCESSING_VERSION!r} (D§9.2)",
            ProvenanceVersionMismatchWarning,
            stacklevel=2,
        )
    return prov


def write_binance_ohlcv_parquet(base_dir, hl_symbol: str, df: pd.DataFrame,
                                 provenance: BinanceDatasetProvenance) -> Path:
    core_cols = [c for c in df.columns if c != "is_aggregated"]
    assert_ohlcv_schema(df[core_cols])
    path = binance_ohlcv_parquet_path(base_dir, hl_symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    write_binance_provenance(base_dir, provenance)
    return path


def read_binance_ohlcv_parquet(base_dir, hl_symbol: str, check_provenance: bool = True) -> pd.DataFrame:
    path = binance_ohlcv_parquet_path(base_dir, hl_symbol)
    df = pd.read_parquet(path)
    if check_provenance:
        read_binance_provenance(base_dir, binance_ohlcv_dataset_id(hl_symbol))
    return df
