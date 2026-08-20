"""D§9 — provenance construction and (de)serialization.

D§2.1: MUST NOT import `src/data/hyperliquid/**`.

The frozen engine-facing `DatasetProvenance` / `UniverseProvenance` dataclasses
(`backtest.models`, §13.1/§13.2) do not carry every field D§9.1 requires a
processed dataset to record (`symbols`, `frequency`, `endpoint`,
`request_windows`, `api_response_count`, `code_version`,
`excluded_backfill_bars`, `coverage_segments`). This is bridged, not violated:
`HyperliquidDatasetProvenance` below is the FULL data-layer-native record
(serialized verbatim to the D§8.1 metadata sidecar); `to_engine_provenance()`
derives the narrower `DatasetProvenance` shape that D§9.1's closing sentence
requires be "accepted by the engine unchanged". See the implementation report
for why this is a bridging decision, not a spec conflict.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional, Sequence

import pandas as pd

from backtest.models import DatasetProvenance, UniverseProvenance

__all__ = [
    "PROCESSING_VERSION",
    "HyperliquidDatasetProvenance",
    "BinanceDatasetProvenance",
    "build_universe_provenance",
    "current_code_version",
]

# D§9.2 — bumped whenever the normalization/quarantine/aggregation logic in
# this layer changes in a way that could alter previously-written processed
# datasets. Bumped for AMENDMENT A+B (v1.2): the D§4.1 OHLCV schema itself
# changed (four new MANDATORY per-row attribution columns), which would make
# any v1.0-processed parquet fail `assert_ohlcv_schema` on reload — a loud,
# unmissable failure rather than the schema silently drifting. Bumped again
# for v1.3 DECISION 3: `BinanceDatasetProvenance.unit_multiplier` split into
# `hl_unit_multiplier`/`venue_unit_multiplier` (D§16.3.4) — `from_json_dict`
# stays backward-compatible with pre-v1.3 sidecars (both derived from the old
# single field), but the bump makes the schema change loud rather than silent.
PROCESSING_VERSION = "qr-data-001-v1.3"


def current_code_version() -> Optional[str]:
    """Best-effort git SHA (D§9.1 `code_version`); None if unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if out.returncode == 0:
        sha = out.stdout.strip()
        return sha or None
    return None


def _ts_to_iso(ts: Optional[pd.Timestamp]) -> Optional[str]:
    if ts is None:
        return None
    return pd.Timestamp(ts).isoformat()


def _iso_to_ts(s: Optional[str]) -> Optional[pd.Timestamp]:
    if s is None:
        return None
    return pd.Timestamp(s)


@dataclass(frozen=True)
class HyperliquidDatasetProvenance:
    """D§9.1 — the full data-layer-native provenance record for one processed
    dataset (one symbol x frequency for OHLCV, one symbol for funding).
    """

    dataset_id: str
    source_venue: str
    source_type: str  # "ohlcv" | "funding_rate"
    native_or_proxy: str  # always "native" in QR-DATA-001
    retrieved_at: pd.Timestamp
    start_timestamp: pd.Timestamp
    end_timestamp: pd.Timestamp
    symbols: tuple
    frequency: Optional[str]  # OHLCV only, None for funding
    processing_version: str
    endpoint: str
    request_windows: tuple  # tuple[tuple[int, int], ...] of (start_ms, end_ms) requested
    api_response_count: int
    code_version: Optional[str]
    excluded_backfill_bars: dict = field(default_factory=dict)  # symbol -> {"count", "start", "end"}
    coverage_segments: tuple = ()  # funding only; tuple of dicts, else empty
    # QR-PREP-001 P§1.4 — free-text notes appended to the engine-facing
    # `DatasetProvenance.notes` (e.g. the accreting-archive disclosure P§1.4
    # requires). Optional/backward-compatible: absent in pre-P§1 sidecars,
    # which `from_json_dict` reads back as `None`.
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if self.native_or_proxy != "native":
            raise ValueError(
                f"QR-DATA-001 supports native_or_proxy='native' only (D§9.1), got {self.native_or_proxy!r}"
            )
        if self.source_type not in ("ohlcv", "funding_rate"):
            raise ValueError(f"source_type must be 'ohlcv' or 'funding_rate', got {self.source_type!r}")

    def to_engine_provenance(self) -> DatasetProvenance:
        """D§9.1 closing sentence — "MUST be emittable as a DatasetProvenance
        (§13.1) accepted by the engine unchanged."
        """
        notes = (
            f"endpoint={self.endpoint}; frequency={self.frequency}; "
            f"api_response_count={self.api_response_count}; code_version={self.code_version}; "
            f"excluded_backfill_bars={self.excluded_backfill_bars}; "
            f"coverage_segments={self.coverage_segments}"
        )
        if self.notes:
            notes = f"{notes}; {self.notes}"
        return DatasetProvenance(
            source_venue=self.source_venue,
            field_type=self.source_type,
            time_range=(self.start_timestamp, self.end_timestamp),
            native_or_proxy=self.native_or_proxy,
            dataset_id=self.dataset_id,
            processing_version=self.processing_version,
            retrieval_date=pd.Timestamp(self.retrieved_at).date(),
            symbol_mapping=",".join(self.symbols),
            notes=notes,
        )

    def to_json_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "source_venue": self.source_venue,
            "source_type": self.source_type,
            "native_or_proxy": self.native_or_proxy,
            "retrieved_at": _ts_to_iso(self.retrieved_at),
            "start_timestamp": _ts_to_iso(self.start_timestamp),
            "end_timestamp": _ts_to_iso(self.end_timestamp),
            "symbols": list(self.symbols),
            "frequency": self.frequency,
            "processing_version": self.processing_version,
            "endpoint": self.endpoint,
            "request_windows": [list(w) for w in self.request_windows],
            "api_response_count": self.api_response_count,
            "code_version": self.code_version,
            "excluded_backfill_bars": self.excluded_backfill_bars,
            "coverage_segments": list(self.coverage_segments),
            "notes": self.notes,
        }

    @staticmethod
    def from_json_dict(d: dict) -> "HyperliquidDatasetProvenance":
        return HyperliquidDatasetProvenance(
            dataset_id=d["dataset_id"],
            source_venue=d["source_venue"],
            source_type=d["source_type"],
            native_or_proxy=d["native_or_proxy"],
            retrieved_at=_iso_to_ts(d["retrieved_at"]),
            start_timestamp=_iso_to_ts(d["start_timestamp"]),
            end_timestamp=_iso_to_ts(d["end_timestamp"]),
            symbols=tuple(d["symbols"]),
            frequency=d.get("frequency"),
            processing_version=d["processing_version"],
            endpoint=d["endpoint"],
            request_windows=tuple(tuple(w) for w in d.get("request_windows", [])),
            api_response_count=d.get("api_response_count", 0),
            code_version=d.get("code_version"),
            excluded_backfill_bars=d.get("excluded_backfill_bars", {}),
            coverage_segments=tuple(d.get("coverage_segments", [])),
            notes=d.get("notes"),
        )


@dataclass(frozen=True)
class BinanceDatasetProvenance:
    """D§9.1 / D§16 — the full data-layer-native provenance record for one
    processed Binance USDⓈ-M perpetual OHLCV dataset (one symbol, 1h canonical).

    `native_or_proxy` is PINNED to `"proxy"` (D§16.1, M29): Binance rows MUST
    NEVER be labelled `native_or_proxy="native"` or `source_venue="Hyperliquid"`.
    `proxy_for` records what the proxy is intended to represent (CLAUDE.md
    provenance minimum), fixed to `"Hyperliquid"` (D§16.1's execution venue).
    """

    dataset_id: str
    source_venue: str  # "Binance"
    native_or_proxy: str  # always "proxy"
    proxy_for: str  # "Hyperliquid" — what the proxy is intended to represent
    retrieved_at: pd.Timestamp
    start_timestamp: pd.Timestamp
    end_timestamp: pd.Timestamp
    hl_symbol: str
    binance_symbol: str
    hl_unit_multiplier: int  # D§16.3.4 (v1.3 DECISION 3) -- tokens per 1 HL contract
    venue_unit_multiplier: int  # D§16.3.4 (v1.3 DECISION 3) -- tokens per 1 Binance contract
    processing_version: str
    code_version: Optional[str]
    checksum_manifest_entries: tuple = ()  # tuple of dicts, D§16.6.3
    excluded_backfill_bars: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_venue != "Binance":
            raise ValueError(f"BinanceDatasetProvenance.source_venue must be 'Binance', got {self.source_venue!r}")
        if self.native_or_proxy != "proxy":
            raise ValueError(
                f"BinanceDatasetProvenance.native_or_proxy MUST be 'proxy' (D§16.1, M29), got {self.native_or_proxy!r}"
            )
        if not self.proxy_for:
            raise ValueError("BinanceDatasetProvenance.proxy_for MUST be non-empty when native_or_proxy=='proxy'")

    def to_engine_provenance(self) -> DatasetProvenance:
        return DatasetProvenance(
            source_venue=self.source_venue,
            field_type="ohlcv",
            time_range=(self.start_timestamp, self.end_timestamp),
            native_or_proxy=self.native_or_proxy,
            proxy_for=self.proxy_for,
            dataset_id=self.dataset_id,
            processing_version=self.processing_version,
            retrieval_date=pd.Timestamp(self.retrieved_at).date(),
            symbol_mapping=(
                f"{self.hl_symbol}->{self.binance_symbol} "
                f"(hl_unit_multiplier={self.hl_unit_multiplier}, venue_unit_multiplier={self.venue_unit_multiplier})"
            ),
            notes=(
                f"code_version={self.code_version}; "
                f"checksum_manifest_entries={len(self.checksum_manifest_entries)}; "
                f"excluded_backfill_bars={self.excluded_backfill_bars}"
            ),
        )

    def to_json_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "source_venue": self.source_venue,
            "native_or_proxy": self.native_or_proxy,
            "proxy_for": self.proxy_for,
            "retrieved_at": _ts_to_iso(self.retrieved_at),
            "start_timestamp": _ts_to_iso(self.start_timestamp),
            "end_timestamp": _ts_to_iso(self.end_timestamp),
            "hl_symbol": self.hl_symbol,
            "binance_symbol": self.binance_symbol,
            "hl_unit_multiplier": self.hl_unit_multiplier,
            "venue_unit_multiplier": self.venue_unit_multiplier,
            "processing_version": self.processing_version,
            "code_version": self.code_version,
            "checksum_manifest_entries": list(self.checksum_manifest_entries),
            "excluded_backfill_bars": self.excluded_backfill_bars,
        }

    @staticmethod
    def from_json_dict(d: dict) -> "BinanceDatasetProvenance":
        # D§9.2 -- backward-compatible read of a PRE-v1.3 sidecar that only
        # carried a single ambiguous `unit_multiplier`. For every entry ever
        # written under the old schema, hl_unit_multiplier == venue_unit_multiplier
        # == that value (BTC/ETH/... at 1, kPEPE-style at 1000) -- see
        # symbol_map.py's v1.3 DECISION 3 migration. New sidecars always carry
        # both fields explicitly.
        legacy_um = d.get("unit_multiplier")
        return BinanceDatasetProvenance(
            dataset_id=d["dataset_id"],
            source_venue=d["source_venue"],
            native_or_proxy=d["native_or_proxy"],
            proxy_for=d["proxy_for"],
            retrieved_at=_iso_to_ts(d["retrieved_at"]),
            start_timestamp=_iso_to_ts(d["start_timestamp"]),
            end_timestamp=_iso_to_ts(d["end_timestamp"]),
            hl_symbol=d["hl_symbol"],
            binance_symbol=d["binance_symbol"],
            hl_unit_multiplier=d.get("hl_unit_multiplier", legacy_um),
            venue_unit_multiplier=d.get("venue_unit_multiplier", legacy_um),
            processing_version=d["processing_version"],
            code_version=d.get("code_version"),
            checksum_manifest_entries=tuple(d.get("checksum_manifest_entries", [])),
            excluded_backfill_bars=d.get("excluded_backfill_bars", {}),
        )


def build_universe_provenance(notes: Optional[str] = None) -> UniverseProvenance:
    """D§6.3 — pins §13.2's universe provenance fields.

    `survivorship_safe` MUST be `False` and MUST NOT be set `True` (D§6.3):
    although `meta` retains delisted assets, we cannot demonstrate it retains
    every asset ever listed.
    """
    return UniverseProvenance(
        universe_source="hyperliquid.info.meta",
        universe_asof_policy="point_in_time_inferred_from_first_last_native_trade",
        listing_data_source="inferred_from_candle_activity",
        survivorship_safe=False,
        notes=notes,
    )
