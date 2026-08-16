"""D§2.2 — `HyperliquidProvider(MarketDataProvider)`.

Owns: normalization pipeline (D§3/D§4/D§5), backfill quarantine (D§4.4),
native/aggregated 4h/1d generation (D§4.5), funding coverage construction
(D§5.3/D§5.4/D§5.6), missing-data classification (D§7), and (v1.2) the
per-row D§15 source-attribution columns, optional `event_price` funding
basis via `asset_ctxs` oracle prices (D§5.5/D§5.5.1), and raw-response
archival wiring (D§8.1, fixing a self-reported v1.0 gap).
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from backtest.models import DataIntegrityError, FundingCoverage

from .. import storage
from ..aggregation import aggregate_ohlcv_1h_to
from ..base import (
    FREQUENCY_DELTA,
    MAX_CANDLES_PER_REQ,
    MAX_FUNDING_GAP,
    MAX_FUNDING_RECORDS_PER_REQ,
    MarketDataProvider,
    MissingDataClassification,
    SymbolMeta,
    UniverseSnapshot,
    ensure_utc_timestamp,
)
from ..provenance import build_universe_provenance
from ..schemas import assert_funding_schema, assert_ohlcv_schema, empty_funding_frame, empty_ohlcv_frame
from .client import HyperliquidAPIError, HyperliquidClient

__all__ = ["HyperliquidProvider"]

_NATIVE_ANCHOR = pd.Timestamp("2019-01-01", tz="UTC")  # earlier than any known HL history (F2)

# D§15.1 — the source_type this provider ever emits for candleSnapshot-sourced
# OHLCV (native or aggregated: both derive from the same A-priority source,
# D§14.1; see schemas.py's docstring for why "native vs aggregated" is instead
# carried by the separate `is_aggregated` column).
_SOURCE_TYPE_CANDLE = "hyperliquid_candle"


def _empty_ohlcv_with_flag() -> pd.DataFrame:
    out = empty_ohlcv_frame()
    out["is_aggregated"] = pd.Series([], dtype="bool")
    return out


class HyperliquidProvider(MarketDataProvider):
    def __init__(
        self,
        client: Optional[HyperliquidClient] = None,
        offline: bool = False,
        storage_base_dir=None,
        archive_raw_responses: bool = False,
    ):
        if offline and client is not None:
            raise ValueError("offline=True providers MUST NOT be given a live client (D§8.3)")
        self._offline = offline
        self._client = None if offline else (client or HyperliquidClient())
        self._storage_base_dir = storage_base_dir
        self._first_native_1d_cache: dict = {}
        # D§8.1 — verbatim raw-response archival. Previously implemented in
        # storage.py but never CALLED by the provider's live fetch path (a
        # self-reported v1.0 gap). Opt-in via `archive_raw_responses=True`
        # AND a `storage_base_dir` (there is nowhere to write it otherwise);
        # left off by default so existing offline/mock-heavy tests are
        # unaffected unless they opt in.
        self._archive_raw_responses = archive_raw_responses and storage_base_dir is not None

    @property
    def venue(self) -> str:
        return "Hyperliquid"

    # ------------------------------------------------------------------
    # D§6 — universe
    # ------------------------------------------------------------------

    def get_universe(
        self,
        as_of: Optional[pd.Timestamp] = None,
        symbols: Optional[Sequence[str]] = None,
        infer_native_range: bool = True,
    ) -> UniverseSnapshot:
        if self._offline:
            raise DataIntegrityError("get_universe() requires network access; provider constructed offline=True (D§8.3)")

        meta = self._client.fetch_meta()
        universe_list = meta["universe"]
        names = [u["name"] for u in universe_list]
        if len(names) != len(set(names)):
            raise DataIntegrityError("duplicate symbol names in hyperliquid meta.universe (D§3.3.4)")

        retrieved_at = ensure_utc_timestamp(pd.Timestamp.utcnow())
        wanted = set(symbols) if symbols is not None else None

        symbol_meta: dict = {}
        for idx, u in enumerate(universe_list):
            name = u["name"]
            if wanted is not None and name not in wanted:
                continue
            is_delisted = bool(u.get("isDelisted", False))
            first_native = last_native = None
            if infer_native_range:
                first_native, last_native = self._infer_native_range(name, is_delisted)
            symbol_meta[name] = SymbolMeta(
                symbol=name,
                asset_index=idx,
                sz_decimals=u["szDecimals"],
                max_leverage=u["maxLeverage"],
                is_delisted=is_delisted,
                unit_multiplier=1000 if name.startswith("k") else 1,
                first_native_bar=first_native,
                last_native_bar=last_native,
            )

        snapshot = UniverseSnapshot(
            retrieved_at=retrieved_at,
            venue="Hyperliquid",
            symbols=symbol_meta,
            provenance=build_universe_provenance(),
        )
        if as_of is not None:
            from ..universe import filter_universe_asof

            snapshot = filter_universe_asof(snapshot, ensure_utc_timestamp(as_of))
        return snapshot

    def _infer_native_range(self, symbol: str, is_delisted: bool):
        """D§6.2 — `listed_at`/`delisted_at`, inferred from 1d trading activity
        (D§4.4's `first_native` MUST be computed from the 1d series).
        """
        anchor_ms = int(_NATIVE_ANCHOR.timestamp() * 1000)
        now_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
        bar_ms = int(FREQUENCY_DELTA["1d"].total_seconds() * 1000)
        raw = self._client.fetch_candles_paginated(symbol, "1d", anchor_ms, now_ms, bar_ms, MAX_CANDLES_PER_REQ)
        first_native = None
        last_native = None
        for bar in raw:
            native = not (float(bar["v"]) == 0.0 and bar["n"] == 0)
            if native:
                t = ensure_utc_timestamp(pd.Timestamp(int(bar["t"]), unit="ms", tz="UTC"))
                if first_native is None:
                    first_native = t
                last_native = t
        if not is_delisted:
            last_native = None
        return first_native, last_native

    def _get_first_native_1d(self, symbol: str) -> Optional[pd.Timestamp]:
        """D§4.4 — `first_native[symbol]`, cached per provider instance."""
        if symbol in self._first_native_1d_cache:
            return self._first_native_1d_cache[symbol]
        first_native, _ = self._infer_native_range(symbol, is_delisted=False)
        self._first_native_1d_cache[symbol] = first_native
        return first_native

    # ------------------------------------------------------------------
    # D§4 — OHLCV
    # ------------------------------------------------------------------

    def get_ohlcv(self, symbols: Sequence[str], frequency: str, start, end) -> pd.DataFrame:
        if frequency not in FREQUENCY_DELTA:
            raise DataIntegrityError(f"unsupported frequency {frequency!r} (D§4)")
        start = ensure_utc_timestamp(start)
        end = ensure_utc_timestamp(end)
        frames = [self._get_symbol_ohlcv(symbol, frequency, start, end) for symbol in symbols]
        if not frames or all(len(f) == 0 for f in frames):
            return _empty_ohlcv_with_flag()
        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
        assert_ohlcv_schema(df)
        return df

    def _get_symbol_ohlcv(self, symbol: str, frequency: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if self._offline:
            df = storage.read_ohlcv_parquet(self._storage_base_dir, frequency, symbol)
            mask = (df["timestamp"] >= start) & (df["timestamp"] < end)
            df = df.loc[mask].reset_index(drop=True)
            if "is_aggregated" not in df.columns:
                df["is_aggregated"] = False
            return df

        dataset_id = storage.ohlcv_dataset_id(frequency, symbol)
        bar_ms = int(FREQUENCY_DELTA[frequency].total_seconds() * 1000)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        raw_native = self._client.fetch_candles_paginated(
            symbol, frequency, start_ms, end_ms, bar_ms, MAX_CANDLES_PER_REQ
        )
        if self._archive_raw_responses:
            storage.write_raw_response(
                self._storage_base_dir, "candleSnapshot", symbol, frequency,
                f"{start_ms}-{end_ms}", raw_native,
            )
        native_df = self._raw_candles_to_df(raw_native, symbol, dataset_id=dataset_id)

        # D§4.5 — aggregation fallback from native 1h, native preferred. Only
        # attempted when native at this frequency doesn't reach back to `start`.
        if frequency != "1h":
            if len(native_df) == 0:
                native_df = self._aggregate_from_1h(symbol, frequency, start, end)
            elif native_df["timestamp"].min() > start:
                gap_end = native_df["timestamp"].min()
                agg_df = self._aggregate_from_1h(symbol, frequency, start, gap_end)
                if len(agg_df):
                    native_df = pd.concat([agg_df, native_df], ignore_index=True)

        if len(native_df) == 0:
            return _empty_ohlcv_with_flag()

        first_native_1d = self._get_first_native_1d(symbol)
        if first_native_1d is not None:
            # D§4.4 — leading-run backfill quarantine, cutoff from the 1d series,
            # applied consistently regardless of the requested frequency.
            native_df = native_df.loc[native_df["timestamp"] >= first_native_1d].reset_index(drop=True)
        else:
            native_df = native_df.iloc[0:0].reset_index(drop=True)

        # D§3.1.2 — bars are left-labelled and HALF-OPEN ([start, end)); the
        # venue's `candleSnapshot` endTime is INCLUSIVE, so a bar exactly at
        # `end` (e.g. a still-forming bucket when `end == now`) can come back
        # from the API and MUST be excluded here for consistency with the
        # offline-cache read path (storage-backed reads already filter this
        # way) and with the half-open convention used throughout this layer.
        native_df = native_df.loc[
            (native_df["timestamp"] >= start) & (native_df["timestamp"] < end)
        ].reset_index(drop=True)

        return native_df

    def _raw_candles_to_df(self, raw: list, symbol: str, dataset_id: str) -> pd.DataFrame:
        rows = []
        for bar in raw:
            t_ms = int(bar["t"])
            T_ms = bar.get("T")
            interval = bar.get("i")
            delta = FREQUENCY_DELTA.get(interval)
            if delta is not None and T_ms is not None:
                expected_T = t_ms + int(delta.total_seconds() * 1000) - 1
                if int(T_ms) != expected_T:
                    ts_dbg = ensure_utc_timestamp(pd.Timestamp(t_ms, unit="ms", tz="UTC"))
                    raise DataIntegrityError(
                        f"{symbol} @ {ts_dbg}: bar 'T' violates D§3.1.2 (T == t+delta-1ms): "
                        f"t={t_ms}, T={T_ms}, expected_T={expected_T}"
                    )
            ts = ensure_utc_timestamp(pd.Timestamp(t_ms, unit="ms", tz="UTC"))
            o, h, l, c = float(bar["o"]), float(bar["h"]), float(bar["l"]), float(bar["c"])
            v = float(bar["v"])
            n = int(bar["n"])
            _check_ohlc_validity(symbol, ts, o, h, l, c, v, n)
            native_traded = not (v == 0.0 and n == 0)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": symbol,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                    "trade_count": n,
                    "native_traded": native_traded,
                    "source_venue": "Hyperliquid",
                    "native_or_proxy": "native",
                    "source_type": _SOURCE_TYPE_CANDLE,
                    "dataset_id": dataset_id,
                    "is_aggregated": False,
                }
            )
        if not rows:
            return _empty_ohlcv_with_flag()
        df = pd.DataFrame(rows)
        df["symbol"] = df["symbol"].astype("string")
        df["trade_count"] = df["trade_count"].astype("int64")
        df["native_traded"] = df["native_traded"].astype("bool")
        df["source_venue"] = df["source_venue"].astype("string")
        df["native_or_proxy"] = df["native_or_proxy"].astype("string")
        df["source_type"] = df["source_type"].astype("string")
        df["dataset_id"] = df["dataset_id"].astype("string")
        df["is_aggregated"] = df["is_aggregated"].astype("bool")

        dup_mask = df.duplicated(subset=["timestamp"], keep=False)
        if dup_mask.any():
            for ts_val in df.loc[dup_mask, "timestamp"].unique():
                group = df.loc[df["timestamp"] == ts_val, ["open", "high", "low", "close", "volume", "trade_count"]]
                if len(group.drop_duplicates()) > 1:
                    raise DataIntegrityError(
                        f"{symbol}: unequal duplicate OHLCV rows at {ts_val!r} (D§4.2.4)"
                    )
            df = df.drop_duplicates(subset=["timestamp"], keep="first")

        return df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

    def _aggregate_from_1h(self, symbol: str, frequency: str, window_start: pd.Timestamp,
                            window_end: pd.Timestamp) -> pd.DataFrame:
        """D§4.5 — aggregation from native 1h, delegating the bucket math to
        the shared, venue-agnostic `aggregate_ohlcv_1h_to` (also used by
        `BinanceUMProvider`, so M15 cannot silently pass for one venue while
        failing for the other).
        """
        one_h = FREQUENCY_DELTA["1h"]
        start_ms = int(window_start.timestamp() * 1000)
        end_ms = int(window_end.timestamp() * 1000)
        bar_ms = int(one_h.total_seconds() * 1000)
        raw_1h = self._client.fetch_candles_paginated(symbol, "1h", start_ms, end_ms, bar_ms, MAX_CANDLES_PER_REQ)
        if self._archive_raw_responses:
            storage.write_raw_response(
                self._storage_base_dir, "candleSnapshot", symbol, "1h",
                f"agg-{start_ms}-{end_ms}", raw_1h,
            )
        dataset_id = storage.ohlcv_dataset_id("1h", symbol)
        df_1h = self._raw_candles_to_df(raw_1h, symbol, dataset_id=dataset_id)

        if df_1h.empty:
            return _empty_ohlcv_with_flag()

        agg = aggregate_ohlcv_1h_to(
            df_1h[["timestamp", "symbol", "open", "high", "low", "close", "volume", "trade_count", "native_traded"]],
            frequency, window_start, window_end,
        )
        if agg.empty:
            return _empty_ohlcv_with_flag()

        agg_dataset_id = storage.ohlcv_dataset_id(frequency, symbol)
        agg["source_venue"] = "Hyperliquid"
        agg["native_or_proxy"] = "native"
        agg["source_type"] = _SOURCE_TYPE_CANDLE
        agg["dataset_id"] = agg_dataset_id
        agg["is_aggregated"] = True
        agg["source_venue"] = agg["source_venue"].astype("string")
        agg["native_or_proxy"] = agg["native_or_proxy"].astype("string")
        agg["source_type"] = agg["source_type"].astype("string")
        agg["dataset_id"] = agg["dataset_id"].astype("string")
        return agg[list(empty_ohlcv_frame().columns) + ["is_aggregated"]]

    # ------------------------------------------------------------------
    # D§5 — Funding
    # ------------------------------------------------------------------

    def get_funding(self, symbols: Sequence[str], start, end, oracle_price_lookup=None) -> pd.DataFrame:
        """`oracle_price_lookup`, if given, is a callable
        `(symbol, event_timestamp) -> Optional[float]` used to populate
        `notional_price` under `funding_notional_basis="event_price"`
        (D§5.5). Default (`None`) leaves `notional_price` NaN throughout —
        `funding_notional_basis="period_start"` — because the `asset_ctxs`
        archive download is NOT AUTHORIZED by this work order (D§5.5.1 rule
        2); see `src/data/hyperliquid/oracle.py` for the extraction path,
        implemented and unit-tested against MOCKED data only.
        """
        start = ensure_utc_timestamp(start)
        end = ensure_utc_timestamp(end)
        frames = [self._get_symbol_funding(symbol, start, end, oracle_price_lookup) for symbol in symbols]
        if not frames or all(len(f) == 0 for f in frames):
            return empty_funding_frame()
        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
        assert_funding_schema(df)
        return df

    def _get_symbol_funding(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
                             oracle_price_lookup=None) -> pd.DataFrame:
        if self._offline:
            df = storage.read_funding_parquet(self._storage_base_dir, symbol)
            mask = (df["timestamp"] >= start) & (df["timestamp"] < end)
            return df.loc[mask].reset_index(drop=True)

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        raw = self._client.fetch_funding_paginated(symbol, start_ms, end_ms, MAX_FUNDING_RECORDS_PER_REQ)
        if self._archive_raw_responses:
            storage.write_raw_response(
                self._storage_base_dir, "fundingHistory", symbol, "event",
                f"{start_ms}-{end_ms}", raw,
            )

        rows = []
        for ev in raw:
            t_ms = int(ev["time"])
            # D§3.1.3 — native millisecond precision, NEVER rounded/floored/
            # snapped to the hour (M7). Jitter is real (F5).
            ts = ensure_utc_timestamp(pd.Timestamp(t_ms, unit="ms", tz="UTC"))
            rate = float(ev["fundingRate"])
            premium_raw = ev.get("premium")
            premium = float(premium_raw) if premium_raw is not None else float("nan")
            if not math.isfinite(rate):
                raise DataIntegrityError(f"{symbol} @ {ts}: non-finite funding_rate={rate}")
            notional_price = np.nan
            if oracle_price_lookup is not None:
                # D§5.5 — "event_price" basis: joined via caller-supplied
                # oracle price lookup (never fabricated from candle closes).
                looked_up = oracle_price_lookup(symbol, ts)
                notional_price = float(looked_up) if looked_up is not None else np.nan
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": symbol,
                    # D§5.1 — used EXACTLY as returned: not annualized, not
                    # rescaled, not multiplied by a period count (M18).
                    "funding_rate": rate,
                    "premium": premium,
                    "notional_price": notional_price,
                }
            )
        if not rows:
            return empty_funding_frame()
        df = pd.DataFrame(rows)
        df["symbol"] = df["symbol"].astype("string")
        df = df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
        # `fundingHistory`'s endTime is INCLUSIVE (confirmed against the live
        # API and current docs); apply the same half-open [start, end)
        # convention used everywhere else in this layer, and match the
        # offline-cache read path exactly.
        df = df.loc[(df["timestamp"] >= start) & (df["timestamp"] < end)].reset_index(drop=True)
        return df

    def get_funding_coverage(self, symbols: Sequence[str], start, end) -> list:
        start = ensure_utc_timestamp(start)
        end = ensure_utc_timestamp(end)
        coverage: list = []
        for symbol in symbols:
            df = self._get_symbol_funding(symbol, start, end)
            coverage.extend(_build_coverage_for_symbol(symbol, df))
        return coverage

    # ------------------------------------------------------------------
    # D§7 — missing-data classification
    # ------------------------------------------------------------------

    def classify_missing_window(
        self,
        symbol_meta: SymbolMeta,
        window_start: pd.Timestamp,
        window_end: pd.Timestamp,
        beyond_retention: bool = False,
    ) -> MissingDataClassification:
        """D§7 — classifies an EMPTY/short response for a window that is
        otherwise a well-formed API result (never called on a transport
        exception; `API_FAILURE` is reserved for those and is never returned
        here, M13).
        """
        if symbol_meta.first_native_bar is not None and window_end <= symbol_meta.first_native_bar:
            return MissingDataClassification.NOT_YET_LISTED
        if (
            symbol_meta.is_delisted
            and symbol_meta.last_native_bar is not None
            and window_start >= symbol_meta.last_native_bar
        ):
            return MissingDataClassification.DELISTED
        if beyond_retention:
            return MissingDataClassification.BEYOND_RETENTION
        return MissingDataClassification.VENUE_GAP


def _check_ohlc_validity(symbol, ts, o, h, l, c, v, n) -> None:
    """D§4.3 — blocking per-bar checks."""
    for name, val in (("open", o), ("high", h), ("low", l), ("close", c)):
        if not math.isfinite(val) or val <= 0:
            raise DataIntegrityError(f"{symbol} @ {ts}: malformed OHLC field {name}={val} (D§4.3)")
    if not (h >= max(o, c)):
        raise DataIntegrityError(f"{symbol} @ {ts}: high={h} < max(open,close)={max(o, c)} (D§4.3)")
    if not (l <= min(o, c)):
        raise DataIntegrityError(f"{symbol} @ {ts}: low={l} > min(open,close)={min(o, c)} (D§4.3)")
    if not (h >= l):
        raise DataIntegrityError(f"{symbol} @ {ts}: high={h} < low={l} (D§4.3)")
    if not (math.isfinite(v) and v >= 0):
        raise DataIntegrityError(f"{symbol} @ {ts}: negative or non-finite volume={v} (D§4.3)")
    if n < 0:
        raise DataIntegrityError(f"{symbol} @ {ts}: negative trade_count={n} (D§4.3)")


def _build_coverage_for_symbol(symbol: str, funding_df: pd.DataFrame) -> list:
    """D§5.3/D§5.4/D§5.6 — coverage derived from ACTUALLY RETRIEVED events,
    never from the requested window (M8). Split into disjoint, non-touching
    records wherever observed spacing exceeds `MAX_FUNDING_GAP` (M6, M9).
    """
    if funding_df.empty:
        return []
    events = funding_df["timestamp"].tolist()
    segments = []
    seg_start = events[0]
    prev = events[0]
    for t in events[1:]:
        if t - prev > MAX_FUNDING_GAP:
            segments.append((seg_start, prev))
            seg_start = t
        prev = t
    segments.append((seg_start, prev))
    return [
        FundingCoverage(
            symbol=symbol,
            coverage_start=s,
            coverage_end=e,
            max_funding_gap=MAX_FUNDING_GAP,
            source_venue="Hyperliquid",
        )
        for s, e in segments
    ]
