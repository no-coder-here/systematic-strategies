"""D§4.1, D§5.1 — column/dtype definitions and structural validators.

D§2.1: MUST NOT import `src/data/hyperliquid/**`.
"""

from __future__ import annotations

import pandas as pd

from backtest.models import DataIntegrityError

__all__ = [
    "OHLCV_COLUMNS",
    "FUNDING_COLUMNS",
    "OHLCV_SOURCE_TYPES",
    "RESERVED_SOURCE_TYPES",
    "empty_ohlcv_frame",
    "empty_funding_frame",
    "assert_ohlcv_schema",
    "assert_funding_schema",
    "assert_source_type_allowed",
]

# D§4.1 (v1.2, REVISED) — column order and dtypes are FIXED and asserted. The
# last four columns (`source_venue`, `native_or_proxy`, `source_type`,
# `dataset_id`) are MANDATORY per-observation source attribution (D§15) and
# MUST be present on every row — they are what makes a mixed-provenance
# (Hyperliquid-native + Binance-proxy) series auditable at the row level.
#
# `is_aggregated` (bool) is an ADDITIONAL, OPTIONAL trailing column beyond the
# fixed D§4.1 prefix. AMBIGUITY FLAGGED (see final report): D§4.5 requires
# `source_type` to "record native vs aggregated", but D§4.1's revised table
# separately pins `source_type` to the D§15.1 enum ("hyperliquid_candle" /
# "hyperliquid_node_trades" / "hyperliquid_node_fills" / "asset_ctxs_oracle_px"
# / "external_proxy"), which has no "aggregated" member — two instructions,
# one column, incompatible vocabularies. Resolution taken here: `source_type`
# carries the D§15.1 provenance-SOURCE enum (unchanged whether a bar was
# fetched natively at its own frequency or aggregated from native 1h candles,
# since both derive from the same official candleSnapshot / A-priority source
# per D§14.1); the native-vs-aggregated distinction D§4.5 separately asks for
# is carried by the additional `is_aggregated` column, consistent with this
# layer's pre-existing (v1.0) reading that D§4.1 fixes a required PREFIX and
# permits additional trailing columns.
OHLCV_COLUMNS = [
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "native_traded",
    "source_venue",
    "native_or_proxy",
    "source_type",
    "dataset_id",
]

_OHLCV_DTYPES = {
    "symbol": "string",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "trade_count": "int64",
    "native_traded": "bool",
    "source_venue": "string",
    "native_or_proxy": "string",
    "source_type": "string",
    "dataset_id": "string",
}

# D§15.1 — the OHLCV-relevant subset of the enum ("asset_ctxs_oracle_px" is
# funding-only, D§5.5, and MUST NOT appear on an OHLCV row).
# "binance_um_kline" is NOT literally listed in D§15.1 (that enum predates
# AMENDMENT B). AMBIGUITY FLAGGED (see final report): D§15.1's
# `"external_proxy"` is explicitly RESERVED and refused (D§15.2.5, M26);
# Binance is a *named, reviewed* proxy source (D§16.3), not the generic
# placeholder D§15.2.5 refuses. Reading taken: `"binance_um_kline"` extends
# the enum's own `venue_datatype` naming convention as the concrete value
# AMENDMENT B actually needs; `"external_proxy"` remains reserved/refused
# exactly as specified.
OHLCV_SOURCE_TYPES = frozenset(
    {
        "hyperliquid_candle",
        "hyperliquid_node_trades",
        "hyperliquid_node_fills",
        "binance_um_kline",
    }
)

# D§15.1 / D§15.2.5 (M26) — MUST NEVER be produced by QR-DATA-001.
RESERVED_SOURCE_TYPES = frozenset({"external_proxy"})


def assert_source_type_allowed(source_type) -> None:
    """D§15.2.5 (M26) — refuses emission of a reserved `source_type`.

    Raises immediately (not merely a `ValidationReport` warning) because
    D§15.2.5 says the layer "MUST refuse to emit `external_proxy`" — this
    function IS that refusal, exercised at the point of attempted emission
    (schema assertion), not discovered later by an optional validation pass.
    """
    if source_type in RESERVED_SOURCE_TYPES:
        raise DataIntegrityError(
            f"source_type={source_type!r} is RESERVED and MUST NOT be emitted (D§15.1/D§15.2.5, M26)"
        )

FUNDING_COLUMNS = [
    "timestamp",
    "symbol",
    "funding_rate",
    "premium",
    "notional_price",
]

_FUNDING_DTYPES = {
    "symbol": "string",
    "funding_rate": "float64",
    "premium": "float64",
    "notional_price": "float64",
}


def empty_ohlcv_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series([], dtype="datetime64[ns, UTC]"),
            "symbol": pd.Series([], dtype="string"),
            "open": pd.Series([], dtype="float64"),
            "high": pd.Series([], dtype="float64"),
            "low": pd.Series([], dtype="float64"),
            "close": pd.Series([], dtype="float64"),
            "volume": pd.Series([], dtype="float64"),
            "trade_count": pd.Series([], dtype="int64"),
            "native_traded": pd.Series([], dtype="bool"),
            "source_venue": pd.Series([], dtype="string"),
            "native_or_proxy": pd.Series([], dtype="string"),
            "source_type": pd.Series([], dtype="string"),
            "dataset_id": pd.Series([], dtype="string"),
        }
    )[OHLCV_COLUMNS]


def empty_funding_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.Series([], dtype="datetime64[ns, UTC]"),
            "symbol": pd.Series([], dtype="string"),
            "funding_rate": pd.Series([], dtype="float64"),
            "premium": pd.Series([], dtype="float64"),
            "notional_price": pd.Series([], dtype="float64"),
        }
    )[FUNDING_COLUMNS]


def _assert_utc_timestamp_column(df: pd.DataFrame, label: str) -> None:
    dtype = df["timestamp"].dtype
    if not isinstance(dtype, pd.DatetimeTZDtype) or str(dtype.tz) != "UTC":
        raise DataIntegrityError(
            f"{label} column 'timestamp' must be tz-aware UTC (D§3.1.1), got dtype={dtype!r}"
        )


def assert_ohlcv_schema(df: pd.DataFrame) -> None:
    """D§4.1 — the first len(OHLCV_COLUMNS) columns MUST be exactly
    OHLCV_COLUMNS, in order, with fixed dtypes. Additional trailing columns
    (e.g. `is_aggregated`, D§4.5) are permitted.

    Also enforces D§15.2.5 (M26): any row with `source_type ==
    "external_proxy"` raises immediately — the refusal is exercised here, at
    the point of emission, not left to an optional later validation pass.
    """
    cols = list(df.columns)
    if cols[: len(OHLCV_COLUMNS)] != OHLCV_COLUMNS:
        raise DataIntegrityError(
            f"OHLCV frame column order/set mismatch (D§4.1): expected prefix {OHLCV_COLUMNS}, got {cols}"
        )
    for col, dtype in _OHLCV_DTYPES.items():
        if str(df[col].dtype) != dtype:
            raise DataIntegrityError(
                f"OHLCV column {col!r} dtype mismatch (D§4.1): expected {dtype}, got {df[col].dtype}"
            )
    _assert_utc_timestamp_column(df, "OHLCV")
    if len(df) and df["source_type"].isin(RESERVED_SOURCE_TYPES).any():
        bad = df.loc[df["source_type"].isin(RESERVED_SOURCE_TYPES), "source_type"].unique().tolist()
        raise DataIntegrityError(
            f"OHLCV frame contains RESERVED source_type value(s) {bad} (D§15.1/D§15.2.5, M26)"
        )


def assert_funding_schema(df: pd.DataFrame) -> None:
    cols = list(df.columns)
    if cols[: len(FUNDING_COLUMNS)] != FUNDING_COLUMNS:
        raise DataIntegrityError(
            f"Funding frame column order/set mismatch (D§5.1): expected prefix {FUNDING_COLUMNS}, got {cols}"
        )
    for col, dtype in _FUNDING_DTYPES.items():
        if str(df[col].dtype) != dtype:
            raise DataIntegrityError(
                f"Funding column {col!r} dtype mismatch (D§5.1): expected {dtype}, got {df[col].dtype}"
            )
    _assert_utc_timestamp_column(df, "Funding")
