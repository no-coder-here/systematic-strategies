"""D§4.5 — shared, venue-agnostic aggregation-from-1h logic.

Both `HyperliquidProvider` (native 4h/1d fallback) and `BinanceUMProvider`
(D§16.4: 1h is the ONLY canonical stored frequency; 4h/1d are ALWAYS derived)
need the identical D§4.5 aggregation rule. Sharing one implementation means
M15 ("emit a partial 4h bucket from incomplete 1h bars") cannot silently pass
for one venue while failing for the other.

D§2.1: MUST NOT import `src/data/hyperliquid/**` or `src/data/binance/**`.
This module takes an already-fetched, single-symbol 1h `pd.DataFrame` (D§4.1
core columns) and returns aggregated bars for the requested bucket frequency;
it does no fetching itself.
"""

from __future__ import annotations

import pandas as pd

from backtest.models import DataIntegrityError

from .base import FREQUENCY_DELTA

__all__ = ["aggregate_ohlcv_1h_to"]


def aggregate_ohlcv_1h_to(
    df_1h: pd.DataFrame, frequency: str, window_start: pd.Timestamp, window_end: pd.Timestamp
) -> pd.DataFrame:
    """D§4.5 — aggregates a native 1h OHLCV frame (single symbol, D§4.1 core
    columns: timestamp/symbol/open/high/low/close/volume/trade_count/
    native_traded) into `frequency` buckets over `[window_start, window_end)`.

    A partial bucket (missing ANY constituent 1h bar) MUST NOT be emitted
    (M15) — this is the single most load-bearing invariant in this module.

    Aggregation rule (D§4.5): open=first, high=max, low=min, close=last,
    volume=sum, trade_count=sum, native_traded=any(constituent native_traded),
    left-labelled bucket start.

    Returns a frame with columns [timestamp, symbol, open, high, low, close,
    volume, trade_count, native_traded] ONLY — attribution columns
    (source_venue/native_or_proxy/source_type/dataset_id) and the
    `is_aggregated` flag are the CALLER's responsibility to stamp on
    afterward, since this function is venue-agnostic and has no opinion on
    provenance.
    """
    if frequency not in FREQUENCY_DELTA:
        raise DataIntegrityError(f"unsupported frequency {frequency!r} (D§4)")
    if frequency == "1h":
        raise ValueError("aggregate_ohlcv_1h_to is only for bucket frequencies > 1h")

    bucket_delta = FREQUENCY_DELTA[frequency]
    one_h = FREQUENCY_DELTA["1h"]
    bars_per_bucket = int(bucket_delta / one_h)
    if bars_per_bucket * one_h != bucket_delta:
        raise DataIntegrityError(f"frequency {frequency!r} is not an exact multiple of 1h (D§4.5)")

    empty_cols = ["timestamp", "symbol", "open", "high", "low", "close", "volume", "trade_count", "native_traded"]
    if df_1h.empty:
        return pd.DataFrame(columns=empty_cols)

    symbols = df_1h["symbol"].unique()
    if len(symbols) != 1:
        raise DataIntegrityError("aggregate_ohlcv_1h_to expects a SINGLE-symbol frame")
    symbol = symbols[0]

    idx = df_1h.set_index("timestamp").sort_index()

    # D§4.5 / audit D1 — buckets are anchored to a FIXED UTC epoch grid, NOT to
    # the caller's `window_start`. Anchoring to `window_start` made the derived
    # series a function of the query: identical 1h input produced
    # [00:00,04:00,08:00) for window_start=00:00 but [01:00,05:00) for
    # window_start=01:00, i.e. two different, non-comparable "4h" series with
    # different closes. Since 1h is the sole canonical stored frequency
    # (D§16.4), aggregation is the ONLY path to 4h/1d, so any non-aligned query
    # silently corrupted every derived bar. The epoch grid makes bucket
    # boundaries a property of the DATA, independent of how it was asked for.
    # 4h and 1d both divide 86400s exactly, so this coincides with UTC-midnight
    # alignment.
    _EPOCH = pd.Timestamp(0, tz="UTC")
    _offset = (window_start - _EPOCH) % bucket_delta
    bucket_start = window_start if _offset == pd.Timedelta(0) else window_start + (bucket_delta - _offset)

    rows = []
    while bucket_start + bucket_delta <= window_end:
        bucket_index = pd.date_range(bucket_start, periods=bars_per_bucket, freq=one_h, tz="UTC")
        if all(ts in idx.index for ts in bucket_index):
            sub = idx.loc[bucket_index]
            rows.append(
                {
                    "timestamp": bucket_start,
                    "symbol": symbol,
                    "open": float(sub["open"].iloc[0]),
                    "high": float(sub["high"].max()),
                    "low": float(sub["low"].min()),
                    "close": float(sub["close"].iloc[-1]),
                    "volume": float(sub["volume"].sum()),
                    "trade_count": int(sub["trade_count"].sum()),
                    "native_traded": bool(sub["native_traded"].any()),
                }
            )
        # else: PARTIAL bucket -- MUST NOT be emitted (D§4.5, M15).
        bucket_start = bucket_start + bucket_delta

    if not rows:
        return pd.DataFrame(columns=empty_cols)
    out = pd.DataFrame(rows)
    out["symbol"] = out["symbol"].astype("string")
    out["trade_count"] = out["trade_count"].astype("int64")
    out["native_traded"] = out["native_traded"].astype("bool")
    return out[empty_cols]
