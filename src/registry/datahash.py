"""R§7.3/R§20.8.4 — dataset content hashing, method id `"col-buffer-v2"`.

Hashes the *values* of a dataframe, not the parquet file bytes, so that a
newer `pyarrow` rewriting byte-identical data does not manufacture a new
experiment (R§7.3 rationale). Rows are taken in `(timestamp, symbol)` order;
columns are taken in sorted name order; each column's name (UTF-8) then its
raw value buffer are fed into one running `sha256`.

**R§20.8.4 — method id bump, `col-buffer-v1` -> `col-buffer-v2`.** v1.1's
`col-buffer-v1` implementation silently extended R§7.3's literal three-case
table (datetime/float/string) to also cover plain integer and boolean
columns, which the real OHLCV frames this method hashes
(`src/data/schemas.py:OHLCV_COLUMNS`: `trade_count` is int64,
`native_traded` is bool) actually carry. That extension was the right
encoding choice but redefined `col-buffer-v1` while keeping its id, which
R§19 D11 forbids ("a future method needs a new id, not a redefinition").
`col-buffer-v2` is the honest form: the SAME encoding, under a NEW id, with
the full column-type vocabulary now pinned normatively (R§20.8.4):

| column dtype | encoding |
|---|---|
| datetime (tz-aware -> UTC first) | `int64` nanoseconds, little-endian |
| float | `float64`, little-endian |
| integer (non-bool) | `int64`, little-endian |
| bool | one byte per value, `0x00`/`0x01` |
| string / object | `uint32` little-endian length prefix + UTF-8 bytes, per value |

Every `content_hash` value computed under `col-buffer-v1` is invalid under
`col-buffer-v2` and vice versa (different ids -> different digests are
EXPECTED, never compared) -- this is why R§20.10 regenerates the registry.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

__all__ = ["CONTENT_HASH_METHOD", "hash_dataframe_content"]

CONTENT_HASH_METHOD = "col-buffer-v2"


def _column_buffer(series: pd.Series) -> bytes:
    if pd.api.types.is_datetime64_any_dtype(series):
        if getattr(series.dt, "tz", None) is not None:
            values = series.dt.tz_convert("UTC")
        else:
            values = series
        arr = values.astype("int64").to_numpy()
        return arr.astype("<i8").tobytes()
    if pd.api.types.is_bool_dtype(series):
        arr = series.to_numpy(dtype="bool").astype(np.uint8)
        return arr.tobytes()
    if pd.api.types.is_float_dtype(series):
        arr = series.to_numpy(dtype="float64").astype("<f8")
        return arr.tobytes()
    if pd.api.types.is_integer_dtype(series):
        arr = series.to_numpy(dtype="int64").astype("<i8")
        return arr.tobytes()
    # string / object columns: UTF-8 bytes, each value prefixed by its
    # 4-byte (uint32) little-endian length (R§20.8.4).
    buf = bytearray()
    for v in series.tolist():
        b = str(v).encode("utf-8")
        buf += len(b).to_bytes(4, "little", signed=False)
        buf += b
    return bytes(buf)


def hash_dataframe_content(df: pd.DataFrame) -> str:
    """R§7.3/R§20.8.4 `col-buffer-v2`. `df` MUST contain `timestamp` and
    `symbol` columns (every dataset this method applies to — OHLCV and
    funding — has both)."""
    if "timestamp" not in df.columns or "symbol" not in df.columns:
        raise ValueError("col-buffer-v2 requires 'timestamp' and 'symbol' columns (R§7.3)")
    # `kind="mergesort"` — a stable sort, so ties (impossible here since
    # (timestamp, symbol) is a natural key, but pinned for determinism
    # regardless) never depend on the sort algorithm's internal tie-breaking.
    sorted_df = df.sort_values(["timestamp", "symbol"], kind="mergesort").reset_index(drop=True)
    hasher = hashlib.sha256()
    for col_name in sorted(sorted_df.columns):
        hasher.update(col_name.encode("utf-8"))
        hasher.update(_column_buffer(sorted_df[col_name]))
    return hasher.hexdigest()
