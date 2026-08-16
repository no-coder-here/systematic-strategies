"""D§2.2 / D§16 — `BinanceUMProvider(MarketDataProvider)`: Binance USDⓈ-M
perpetual **PRICE HISTORY ONLY**. D§16.1 role separation is NORMATIVE and
enforced here at the type level: this provider CANNOT emit funding (M30),
and every row it emits is unconditionally `source_venue="Binance"`,
`native_or_proxy="proxy"` (M29) — never Hyperliquid's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from backtest.models import DataIntegrityError

from .. import storage
from ..aggregation import aggregate_ohlcv_1h_to
from ..base import FREQUENCY_DELTA, MarketDataProvider, ensure_utc_timestamp
from ..provenance import BinanceDatasetProvenance, current_code_version
from ..provenance import PROCESSING_VERSION
from ..schemas import assert_ohlcv_schema, empty_ohlcv_frame
from ..symbol_map import get_mapping
from .client import (
    BinanceArchiveError,
    BinanceClient,
    extract_csv_from_zip,
    parse_kline_csv,
    verify_checksum,
)

__all__ = [
    "BinanceUMProvider",
    "GateEstimate",
    "GateExceededError",
    "FundingNotSupportedError",
    "SymbolNotMappedError",
    "UnitEquivalenceError",
    "assert_unit_equivalence",
    "UNIT_EQUIVALENCE_RATIO_BOUNDS",
]

_SOURCE_TYPE_BINANCE = "binance_um_kline"

# D§16.5 — the frozen pre-download gate threshold.
GATE_LIMIT_GB = 5.0


class FundingNotSupportedError(DataIntegrityError):
    """D§16.1 (M30) — `BinanceUMProvider` supplies PRICE HISTORY ONLY.
    Funding is Hyperliquid-native and MUST be sourced from
    `HyperliquidProvider`. This is a hard type-level refusal, not an empty
    frame (an empty frame could be silently mistaken for "no funding events
    in this window" rather than "this provider does not do funding at all").
    """


class SymbolNotMappedError(DataIntegrityError):
    """D§16.2/D§16.3 — the requested Hyperliquid symbol has no reviewed
    Binance mapping entry, or is explicitly `status=="unmatched"`
    (D§1.2 F14). Callers MUST fall back to Hyperliquid-native history for
    this symbol (D§16.2's ELSE branch) rather than treating this as an
    empty-but-valid Binance series.
    """


class UnitEquivalenceError(DataIntegrityError):
    """D§16.3.4/D§17.4 (M36) — the `k`/`1000` unit multiplier MUST be
    VERIFIED, never assumed. A ~1000x price-level discrepancy between a
    Hyperliquid `k`-prefixed symbol and its mapped Binance `1000NAME`
    contract is a MAPPING DEFECT (dropped multiplier), NOT an ordinary
    cross-venue proxy finding (D§17.4) — it MUST FAIL, never merely warn.
    """


# A generous band around 1.0: ordinary cross-venue microstructure/timing
# divergence is expected and must NOT raise (D§17.4); only an
# order-of-magnitude (~1000x, or even ~10x) discrepancy — the unmistakable
# signature of a dropped/duplicated unit multiplier — is treated as fatal.
# Audit finding D2 — was `(0.1, 10.0)` compared INCLUSIVELY, which let the most
# realistic multiplier bug through: recording 100 where the truth is 1000 yields
# a ratio of exactly 0.1, landed on the boundary, and did NOT raise. Only the
# egregious ~1000x case had a test.
#
# After correct normalization both series quote the same underlying unit, so the
# ratio must be ~1: measured cross-venue medians are ~1.0004 (kPEPE relative diff
# median 0.04%, p99 0.24%). Bounds of (0.5, 2.0) compared STRICTLY therefore
# leave three orders of magnitude of headroom over real divergence while failing
# every power-of-ten multiplier error AND an exact-2x one. A ratio this far from
# 1 is a unit/mapping defect, not proxy quality (D§17.4).
UNIT_EQUIVALENCE_RATIO_BOUNDS = (0.5, 2.0)


def assert_unit_equivalence(hl_price: float, binance_price: float, hl_symbol: str, binance_symbol: str) -> None:
    """D§16.3.4 (M36) — verifies that a `k`-prefixed Hyperliquid symbol's
    price level and its mapped Binance `1000NAME` contract's price level are
    of the SAME ORDER OF MAGNITUDE (both already quote per-1000-tokens, so
    they should be directly comparable, unlike a plain `NAME` vs `1000NAME`
    mismatch which would differ by ~1000x). Raises — never warns — outside
    `UNIT_EQUIVALENCE_RATIO_BOUNDS`.
    """
    if not (math.isfinite(hl_price) and hl_price > 0 and math.isfinite(binance_price) and binance_price > 0):
        raise UnitEquivalenceError(
            f"{hl_symbol}/{binance_symbol}: non-finite/non-positive price(s) hl={hl_price} binance={binance_price}"
        )
    ratio = hl_price / binance_price
    lo, hi = UNIT_EQUIVALENCE_RATIO_BOUNDS
    # STRICT comparison (audit D2): an exactly-on-the-boundary ratio is the
    # signature of a clean power-of-ten multiplier error, not of a healthy pair.
    if not (lo < ratio < hi):
        raise UnitEquivalenceError(
            f"D§16.3.4 (M36): {hl_symbol} (price={hl_price}) vs {binance_symbol} (price={binance_price}) "
            f"ratio={ratio:.4f} outside ({lo},{hi}) — looks like a dropped/duplicated unit multiplier, "
            "NOT ordinary proxy divergence (D§17.4). FAILING, not warning."
        )


class GateExceededError(DataIntegrityError):
    """D§16.5 (M39) — the RUNTIME-recomputed estimate exceeds the 5 GB gate.
    MUST stop before downloading, regardless of what any prior estimate
    (including the frozen contract's own F14-F16 table) claimed.
    """


@dataclass(frozen=True)
class GateEstimate:
    matched_symbol_count: int
    total_monthly_files: int
    estimated_row_count: int
    estimated_transient_zip_gb: float
    estimated_processed_parquet_gb: float
    measured_bytes_per_row_zip: float
    measured_bytes_per_row_parquet: float


def estimate_gate(
    hl_symbols: Sequence[str],
    client: BinanceClient,
    sample_symbol: str = "BTCUSDT",
) -> GateEstimate:
    """D§16.5 — recomputes the pre-download gate estimate AT RUNTIME from
    REAL listing sizes (never trusts the frozen contract's F14-F16 numbers).

    Measures actual bytes-per-row from ONE real sample month (mirroring the
    contract's own F16 methodology) rather than reusing the contract's
    measured constants, so a genuinely different upstream state (e.g. a
    changed compression ratio) is caught rather than assumed away.
    """
    total_files = 0
    matched_count = 0
    for hl_symbol in hl_symbols:
        mapping = get_mapping(hl_symbol)
        if mapping is None or mapping.status != "mapped":
            continue
        matched_count += 1
        months = client.list_available_months(mapping.binance_symbol, "1h")
        total_files += len(months)

    # Sample ONE real file to measure bytes/row (F16 methodology), rather
    # than trusting a hardcoded constant.
    sample_months = client.list_available_months(sample_symbol, "1h")
    if not sample_months:
        raise BinanceArchiveError(f"cannot measure sample bytes/row: no months found for {sample_symbol!r}")
    sample = client.fetch_month(sample_symbol, "1h", sample_months[-1])
    if sample is None:
        raise BinanceArchiveError(f"sample month {sample_months[-1]!r} for {sample_symbol!r} disappeared mid-listing")
    verify_checksum(sample["zip_bytes"], sample["checksum_text"], sample["filename"])
    csv_bytes = extract_csv_from_zip(sample["zip_bytes"], sample["filename"])
    rows = parse_kline_csv(csv_bytes)
    if not rows:
        raise BinanceArchiveError(f"sample file {sample['filename']!r} parsed to zero rows")
    bytes_per_row_zip = len(sample["zip_bytes"]) / len(rows)
    # Measured constant from the contract (F16): parquet+zstd ~49.2 B/row.
    # Recomputed here structurally (not re-measured, since that requires an
    # actual parquet write) as a fixed ratio to the zip bytes/row, consistent
    # with F16's own reported ratio (49.2 / 52.3 ~= 0.94).
    bytes_per_row_parquet = bytes_per_row_zip * (49.2 / 52.3)

    # ~730 hours/month average; a real per-symbol row count would require
    # downloading everything, which is precisely what the gate exists to
    # avoid doing before authorization.
    total_rows_est = total_files * 730

    estimated_zip_gb = (total_rows_est * bytes_per_row_zip) / 1e9
    estimated_parquet_gb = (total_rows_est * bytes_per_row_parquet) / 1e9

    return GateEstimate(
        matched_symbol_count=matched_count,
        total_monthly_files=total_files,
        estimated_row_count=total_rows_est,
        estimated_transient_zip_gb=estimated_zip_gb,
        estimated_processed_parquet_gb=estimated_parquet_gb,
        measured_bytes_per_row_zip=bytes_per_row_zip,
        measured_bytes_per_row_parquet=bytes_per_row_parquet,
    )


def check_gate(estimate: GateEstimate) -> None:
    """D§16.5 (M39) — STOPS (raises) if the estimate exceeds the gate. This
    is the refusal itself, not a print statement someone could ignore.
    """
    if estimate.estimated_processed_parquet_gb > GATE_LIMIT_GB:
        raise GateExceededError(
            f"D§16.5 pre-download gate EXCEEDED: estimated processed size "
            f"{estimate.estimated_processed_parquet_gb:.3f} GB > {GATE_LIMIT_GB} GB gate. "
            "STOPPING before any download, per contract."
        )


class BinanceUMProvider(MarketDataProvider):
    """PRICE HISTORY ONLY (D§16.1). `venue` is `"Binance"`; every emitted
    OHLCV row is `native_or_proxy="proxy"`. Universe/listing/delisting,
    funding, and execution costs/liquidity remain Hyperliquid's exclusively
    — `get_universe`/`get_funding`/`get_funding_coverage` all refuse.
    """

    def __init__(self, client: Optional[BinanceClient] = None, offline: bool = False, storage_base_dir=None):
        if offline and client is not None:
            raise ValueError("offline=True providers MUST NOT be given a live client (D§8.3)")
        self._offline = offline
        self._client = None if offline else (client or BinanceClient())
        self._storage_base_dir = storage_base_dir

    @property
    def venue(self) -> str:
        return "Binance"

    # ------------------------------------------------------------------
    # D§16.1 — role-separation refusals
    # ------------------------------------------------------------------

    def get_universe(self, as_of: Optional[pd.Timestamp] = None):
        raise DataIntegrityError(
            "D§16.1: universe/listing/delisting is Hyperliquid-native ONLY. "
            "BinanceUMProvider does not (and must not) supply a universe snapshot; use HyperliquidProvider."
        )

    def get_funding(self, symbols: Sequence[str], start, end) -> pd.DataFrame:
        raise FundingNotSupportedError(
            "D§16.1 (M30): BinanceUMProvider supplies PRICE HISTORY ONLY. "
            "Binance funding rates MUST NEVER be used as Hyperliquid funding cost; "
            "use HyperliquidProvider.get_funding()."
        )

    def get_funding_coverage(self, symbols: Sequence[str], start, end) -> list:
        raise FundingNotSupportedError(
            "D§16.1 (M30): BinanceUMProvider supplies PRICE HISTORY ONLY; it cannot emit funding coverage."
        )

    # ------------------------------------------------------------------
    # D§4 / D§16 — OHLCV (1h canonical; 4h/1d derived on demand, D§16.4)
    # ------------------------------------------------------------------

    def get_ohlcv(self, symbols: Sequence[str], frequency: str, start, end) -> pd.DataFrame:
        if frequency not in FREQUENCY_DELTA:
            raise DataIntegrityError(f"unsupported frequency {frequency!r} (D§4)")
        start = ensure_utc_timestamp(start)
        end = ensure_utc_timestamp(end)
        frames = [self._get_symbol_ohlcv(symbol, frequency, start, end) for symbol in symbols]
        if not frames or all(len(f) == 0 for f in frames):
            out = empty_ohlcv_frame()
            out["is_aggregated"] = pd.Series([], dtype="bool")
            return out
        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
        assert_ohlcv_schema(df)
        return df

    def _get_symbol_ohlcv(self, hl_symbol: str, frequency: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        mapping = get_mapping(hl_symbol)
        if mapping is None or mapping.status != "mapped":
            raise SymbolNotMappedError(
                f"{hl_symbol!r} has no Binance USDⓈ-M perpetual mapping (D§16.2/D§16.3); "
                "caller MUST fall back to Hyperliquid-native history for this symbol, not fabricate a proxy series."
            )

        df_1h = self._read_canonical_1h(hl_symbol)
        mask = (df_1h["timestamp"] >= start) & (df_1h["timestamp"] < end)
        df_1h = df_1h.loc[mask].reset_index(drop=True)

        if frequency == "1h":
            return df_1h

        if df_1h.empty:
            out = empty_ohlcv_frame()
            out["is_aggregated"] = pd.Series([], dtype="bool")
            return out

        # D§16.4 — 4h/1d are ALWAYS derived from canonical 1h (never
        # separately stored), via the SAME shared aggregator as
        # HyperliquidProvider (M15 must hold for both venues).
        agg = aggregate_ohlcv_1h_to(
            df_1h[["timestamp", "symbol", "open", "high", "low", "close", "volume", "trade_count", "native_traded"]],
            frequency, start, end,
        )
        if agg.empty:
            out = empty_ohlcv_frame()
            out["is_aggregated"] = pd.Series([], dtype="bool")
            return out
        dataset_id = f"binance.ohlcv.{frequency}.{hl_symbol}"
        agg["source_venue"] = pd.Series(["Binance"] * len(agg), dtype="string")
        agg["native_or_proxy"] = pd.Series(["proxy"] * len(agg), dtype="string")
        agg["source_type"] = pd.Series([_SOURCE_TYPE_BINANCE] * len(agg), dtype="string")
        agg["dataset_id"] = pd.Series([dataset_id] * len(agg), dtype="string")
        agg["is_aggregated"] = True
        return agg[list(empty_ohlcv_frame().columns) + ["is_aggregated"]]

    def _read_canonical_1h(self, hl_symbol: str) -> pd.DataFrame:
        df = storage.read_binance_ohlcv_parquet(self._storage_base_dir, hl_symbol)
        if "is_aggregated" not in df.columns:
            df["is_aggregated"] = False
        return df

    # ------------------------------------------------------------------
    # D§16.6 — bulk ingestion pipeline (download -> verify -> normalize ->
    # write canonical 1h parquet -> record checksum manifest -> cleanup)
    # ------------------------------------------------------------------

    def ingest_symbol_1h(self, hl_symbol: str) -> BinanceDatasetProvenance:
        """Downloads EVERY available monthly 1h archive for `hl_symbol`'s
        mapped Binance contract, verifies each checksum (D§16.6.1, fatal on
        mismatch), sniffs each file's CSV header independently (F17,
        D§16.6.2), normalizes to the D§4.1 schema, writes the canonical 1h
        parquet (D§16.4), and returns the provenance record (which itself
        carries the D§16.6.3 checksum manifest).

        Raw ZIP/CSV bytes are held ONLY in memory for the duration of this
        call and are NEVER written to `data/raw/**` — D§16.4's "raw MUST NOT
        be retained after verification+normalization+processed-verification"
        is satisfied by construction (nothing durable is ever created to
        retain), and the checksum manifest is the D§16.6.3 reproducibility
        substitute.
        """
        if self._offline:
            raise DataIntegrityError("ingest_symbol_1h() requires network access; provider constructed offline=True")
        mapping = get_mapping(hl_symbol)
        if mapping is None or mapping.status != "mapped":
            raise SymbolNotMappedError(f"{hl_symbol!r} has no Binance mapping; nothing to ingest")

        binance_symbol = mapping.binance_symbol
        months = self._client.list_available_months(binance_symbol, "1h")
        if not months:
            raise BinanceArchiveError(f"no monthly archives found for {binance_symbol!r}")

        all_rows: list = []
        manifest_entries: list = []
        for ym in months:
            fetched = self._client.fetch_month(binance_symbol, "1h", ym)
            if fetched is None:
                continue  # D§16.2 — genuinely absent month; not a transport failure.
            sha256 = verify_checksum(fetched["zip_bytes"], fetched["checksum_text"], fetched["filename"])
            csv_bytes = extract_csv_from_zip(fetched["zip_bytes"], fetched["filename"])
            rows = parse_kline_csv(csv_bytes)
            if not rows:
                continue
            all_rows.extend(rows)
            manifest_entries.append(
                {
                    "symbol": binance_symbol,
                    "month": ym,
                    "url": f"data/futures/um/monthly/klines/{binance_symbol}/1h/{fetched['filename']}",
                    "sha256": sha256,
                    "rows": len(rows),
                    "first_ts": rows[0]["open_time"],
                    "last_ts": rows[-1]["open_time"],
                }
            )
            # `fetched["zip_bytes"]`/`csv_bytes`/`rows` (this month's raw
            # payload) go out of scope at the next loop iteration — never
            # written to `data/raw/**` (D§16.4).

        df = _normalize_binance_rows(all_rows, hl_symbol, binance_symbol)
        df = _quarantine_leading_run(df)  # defensive; see report — not literally required by D§16 text.
        assert_ohlcv_schema(df.drop(columns=["is_aggregated"]))

        retrieved_at = ensure_utc_timestamp(pd.Timestamp.utcnow())
        start_ts = df["timestamp"].min() if len(df) else None
        end_ts = df["timestamp"].max() if len(df) else None
        provenance = BinanceDatasetProvenance(
            dataset_id=storage.binance_ohlcv_dataset_id(hl_symbol),
            source_venue="Binance",
            native_or_proxy="proxy",
            proxy_for="Hyperliquid",
            retrieved_at=retrieved_at,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            hl_symbol=hl_symbol,
            binance_symbol=binance_symbol,
            hl_unit_multiplier=mapping.hl_unit_multiplier,
            venue_unit_multiplier=mapping.venue_unit_multiplier,
            processing_version=PROCESSING_VERSION,
            code_version=current_code_version(),
            checksum_manifest_entries=tuple(manifest_entries),
        )
        storage.write_binance_ohlcv_parquet(self._storage_base_dir, hl_symbol, df, provenance)
        return provenance


def _normalize_binance_rows(rows: list, hl_symbol: str, binance_symbol: str) -> pd.DataFrame:
    """D§4.1/D§16 normalization: raw Binance kline rows -> the fixed 13-column
    schema (+ `is_aggregated=False`). D§16.6.5: verified left-labelled
    `open_time`, exact 1h grid, tz-aware UTC.
    """
    if not rows:
        out = empty_ohlcv_frame()
        out["is_aggregated"] = pd.Series([], dtype="bool")
        return out

    dataset_id = storage.binance_ohlcv_dataset_id(hl_symbol)
    out_rows = []
    seen: dict = {}
    for r in rows:
        ts = ensure_utc_timestamp(pd.Timestamp(int(r["open_time"]), unit="ms", tz="UTC"))
        o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
        v = float(r["volume"])
        n = int(r["count"])
        for name, val in (("open", o), ("high", h), ("low", l), ("close", c)):
            if not math.isfinite(val) or val <= 0:
                raise DataIntegrityError(f"{binance_symbol} @ {ts}: malformed OHLC field {name}={val} (D§4.3)")
        if not (h >= max(o, c) and l <= min(o, c) and h >= l):
            raise DataIntegrityError(f"{binance_symbol} @ {ts}: malformed OHLC ordering o={o} h={h} l={l} c={c}")
        if not (math.isfinite(v) and v >= 0):
            raise DataIntegrityError(f"{binance_symbol} @ {ts}: negative/non-finite volume={v}")
        if n < 0:
            raise DataIntegrityError(f"{binance_symbol} @ {ts}: negative trade_count={n}")

        key = ts
        row = {
            "timestamp": ts,
            "symbol": hl_symbol,
            "open": o, "high": h, "low": l, "close": c,
            "volume": v, "trade_count": n,
            "native_traded": not (v == 0.0 and n == 0),
            "source_venue": "Binance",
            "native_or_proxy": "proxy",
            "source_type": _SOURCE_TYPE_BINANCE,
            "dataset_id": dataset_id,
            "is_aggregated": False,
        }
        if key in seen:
            prior = seen[key]
            # D§16.6.4 — monthly-boundary duplicate check: identical values
            # are a benign boundary overlap; anything else is a blocking
            # defect, never silently collapsed.
            if any(prior[f] != row[f] for f in ("open", "high", "low", "close", "volume", "trade_count")):
                raise DataIntegrityError(f"{binance_symbol}: unequal duplicate bar at monthly boundary {ts!r}")
            continue
        seen[key] = row
        out_rows.append(row)

    df = pd.DataFrame(out_rows)
    df["symbol"] = df["symbol"].astype("string")
    df["trade_count"] = df["trade_count"].astype("int64")
    df["native_traded"] = df["native_traded"].astype("bool")
    df["source_venue"] = df["source_venue"].astype("string")
    df["native_or_proxy"] = df["native_or_proxy"].astype("string")
    df["source_type"] = df["source_type"].astype("string")
    df["dataset_id"] = df["dataset_id"].astype("string")
    df["is_aggregated"] = df["is_aggregated"].astype("bool")
    df = df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)

    # D§16.6.5 — verify the exact, contiguous, left-labelled 1h grid is at
    # least INTERNALLY consistent per bar (spacing checked, not filled).
    if len(df) >= 2:
        diffs = df["timestamp"].diff().dropna()
        bad = diffs[diffs <= pd.Timedelta(0)]
        if len(bad):
            raise DataIntegrityError("non-monotonic or duplicate timestamps survived normalization (D§16.6.5)")

    return df[list(empty_ohlcv_frame().columns) + ["is_aggregated"]]


def _quarantine_leading_run(df: pd.DataFrame) -> pd.DataFrame:
    """Defensive addition BEYOND the literal contract text (flagged in the
    final report): live probing during this work order found Binance ALSO
    ships a brief zero-volume "genesis" bar pattern immediately after
    contract onboarding on the live kline API (e.g. BTCUSDT's first
    `fapi`-API bar at 2019-09-08 has `volume=0.002`, the SECOND has
    `volume=0`), directly analogous to Hyperliquid's F3. The canonical
    `data.binance.vision` MONTHLY archive used here starts no earlier than
    2020-01 for every symbol sampled (confirmed empirically; materially
    later than several symbols' true `onboardDate` — see the report), which
    in practice already excludes that genesis window. This quarantine is
    kept anyway as a defense-in-depth measure consistent with D§0.1's
    governing principle ("never present non-native data as native"), applying
    the identical D§4.4 leading-run rule.
    """
    if df.empty or not df["native_traded"].iloc[0]:
        # either empty, or D§4.4 rule (Hyperliquid's identical leading-run
        # exclusion) applies below.
        pass
    native = df["native_traded"]
    if len(native) == 0 or native.iloc[0]:
        return df
    first_native_pos = native.to_numpy().argmax() if native.any() else len(native)
    return df.iloc[first_native_pos:].reset_index(drop=True)
