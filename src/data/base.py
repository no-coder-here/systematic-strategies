"""D§2 — `MarketDataProvider` ABC and normalized schema constants.

D§2.1 (NORMATIVE, testable): this module MUST NOT import `src/data/hyperliquid/**`.
It MAY import `src/backtest/**` — the dependency direction restriction runs only
one way (`src/backtest/**` MUST NOT import `src/data/**`); D§9.1 requires this
layer's provenance to be "emittable as a DatasetProvenance ... accepted by the
engine unchanged", and D§5.3 requires `FundingCoverage` objects "consumable
unchanged by the frozen engine" — both are only achievable by reusing the
frozen dataclasses directly rather than re-declaring parallel, possibly-drifting
shapes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional, Sequence

import pandas as pd

from backtest.models import (
    DataIntegrityError,
    DatasetProvenance,  # noqa: F401 - re-exported for convenience / type hints
    FundingCoverage,
    MarketData,
    UniverseProvenance,  # noqa: F401 - re-exported for convenience / type hints
)

__all__ = [
    "MAX_FUNDING_GAP",
    "MAX_CANDLES_PER_REQ",
    "MAX_FUNDING_RECORDS_PER_REQ",
    "FREQUENCY_DELTA",
    "ensure_utc_timestamp",
    "MissingDataClassification",
    "SymbolMeta",
    "UniverseSnapshot",
    "MarketDataProvider",
    "to_engine_frame",
]

# ---------------------------------------------------------------------------
# D§5.4 — PINNED. Never widened (see docs/data_contract.md D§5.4 for the full
# justification of why 1h and 8h are both wrong). Declared, never inferred.
# ---------------------------------------------------------------------------
MAX_FUNDING_GAP = pd.Timedelta(minutes=90)

# ---------------------------------------------------------------------------
# D§4.2 (F1) — self-imposed request window size. Empirically the venue's true
# hard cap on `candleSnapshot` is 5001 bars regardless of requested window
# (re-verified 2026-08-16 against the live API for BTC/ETH at 1h; see the
# implementation report). MAX_CANDLES_PER_REQ is deliberately set BELOW that
# measured cap so our own windowing can never itself trigger truncation.
# ---------------------------------------------------------------------------
MAX_CANDLES_PER_REQ = 5000

# ---------------------------------------------------------------------------
# D§5.2 (F4) — venue hard cap on `fundingHistory` records per request.
# ---------------------------------------------------------------------------
MAX_FUNDING_RECORDS_PER_REQ = 500

FREQUENCY_DELTA = {
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
}


def ensure_utc_timestamp(ts) -> pd.Timestamp:
    """D§3.1.1 — naive timestamps are REJECTED, never localized.

    Non-UTC tz-aware timestamps are converted to UTC (the original offset is
    not separately recorded by this helper; callers that need to preserve it
    must do so before calling this function).
    """
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise DataIntegrityError(
            f"naive timestamp {ts!r} rejected (D§3.1.1): tz-aware UTC required, never localized"
        )
    return t.tz_convert("UTC")


class MissingDataClassification(str, Enum):
    """D§7 — NORMATIVE classification of an empty/short response.

    `API_FAILURE` is reserved for actual raised exceptions. It is NEVER returned
    by a classifier function; an exception during fetch MUST propagate, never
    be silently reclassified as one of the other four values (D§7, M13).
    """

    NOT_YET_LISTED = "NOT_YET_LISTED"
    DELISTED = "DELISTED"
    BEYOND_RETENTION = "BEYOND_RETENTION"
    VENUE_GAP = "VENUE_GAP"
    API_FAILURE = "API_FAILURE"


@dataclass(frozen=True)
class SymbolMeta:
    """D§3.3.2, D§6.1 — per-symbol metadata."""

    symbol: str
    asset_index: int
    sz_decimals: int
    max_leverage: int
    is_delisted: bool
    unit_multiplier: int  # 1000 for k-prefixed names (F7), else 1
    first_native_bar: Optional[pd.Timestamp] = None  # D§6.2 listed_at (1d granularity)
    last_native_bar: Optional[pd.Timestamp] = None  # D§6.2 delisted_at, iff is_delisted


@dataclass(frozen=True)
class UniverseSnapshot:
    """D§6.1 — universe snapshot. MUST include delisted assets (F6)."""

    retrieved_at: pd.Timestamp
    venue: str
    symbols: dict  # name -> SymbolMeta
    provenance: UniverseProvenance

    def __post_init__(self) -> None:
        names = list(self.symbols.keys())
        if len(names) != len(set(names)):
            raise DataIntegrityError("duplicate symbol names in UniverseSnapshot (D§3.3.4)")
        if self.provenance is not None and self.provenance.survivorship_safe is not False:
            # D§6.3 — survivorship_safe MUST be False, MUST NOT be set True.
            raise DataIntegrityError(
                f"UniverseSnapshot.provenance.survivorship_safe MUST be False (D§6.3), "
                f"got {self.provenance.survivorship_safe!r}"
            )


class MarketDataProvider(ABC):
    """D§2.2 — provider-abstracted market data ABC.

    A future `BinanceProvider` MUST be implementable against this ABC without
    changing it — but MUST NOT be written in QR-DATA-001.
    """

    @property
    @abstractmethod
    def venue(self) -> str: ...

    @abstractmethod
    def get_universe(self, as_of: Optional[pd.Timestamp] = None) -> UniverseSnapshot: ...

    @abstractmethod
    def get_ohlcv(
        self, symbols: Sequence[str], frequency: str, start: pd.Timestamp, end: pd.Timestamp
    ) -> pd.DataFrame: ...

    @abstractmethod
    def get_funding(
        self, symbols: Sequence[str], start: pd.Timestamp, end: pd.Timestamp
    ) -> pd.DataFrame: ...

    @abstractmethod
    def get_funding_coverage(
        self, symbols: Sequence[str], start: pd.Timestamp, end: pd.Timestamp
    ) -> list: ...


# ---------------------------------------------------------------------------
# D§4.6 — gaps and the engine's regular-grid requirement.
# ---------------------------------------------------------------------------


def _union_grid_gaps(union_index: pd.DatetimeIndex, delta: pd.Timedelta):
    """Returns the sorted list of expected-grid timestamps missing from
    `union_index` (the union of ALL symbols' present timestamps), between its
    min and max. A timestamp missing here means NO symbol has any bar at all
    for that instant — a genuine market-wide hole, not an individual symbol's
    pre-listing / post-delisting absence (that is a per-symbol §5.3 concern,
    not a grid concern owned by this layer).
    """
    if len(union_index) == 0:
        return []
    full = pd.date_range(union_index.min(), union_index.max(), freq=delta, tz="UTC")
    missing = full.difference(union_index)
    return list(missing)


def to_engine_frame(df: pd.DataFrame, frequency: str, policy: str = "raise"):
    """D§4.6 — `to_engine_frame(df, frequency, policy=...)`.

    Returns the `MarketData`-ready open/close frames (§4.2 handoff). `df` is a
    normalized OHLCV frame (D§4.1 schema: long format, one row per
    (symbol, timestamp)).

    Policies:
        "raise" (default)  -- any missing bar on the expected grid ->
                               DataIntegrityError naming the first offending pair.
        "segment"           -- returns an ordered list of maximal contiguous
                               `MarketData` segments, each internally regular.
        "reindex_nan"       -- reindexes onto the full grid inserting NaN.

    There is NO "ffill" policy and one MUST NOT be added (D§4.6).

    Interpretation note (flagged ambiguity, see implementation report): the
    contract's "expected grid" is read here as the grid implied by the UNION of
    all symbols' present timestamps (a bar entirely absent for every symbol is
    a genuine venue-wide gap), not as "every symbol must have a bar at every
    timestamp" (that is §5.3's per-symbol activity concern, handled by the
    engine, not this layer).
    """
    if frequency not in FREQUENCY_DELTA:
        raise DataIntegrityError(f"unsupported frequency {frequency!r} (D§4)")
    if policy not in ("raise", "segment", "reindex_nan"):
        raise ValueError(f"unknown to_engine_frame policy {policy!r}")

    delta = FREQUENCY_DELTA[frequency]
    open_wide = df.pivot(index="timestamp", columns="symbol", values="open").sort_index()
    close_wide = df.pivot(index="timestamp", columns="symbol", values="close").sort_index()
    union_index = open_wide.index

    if policy == "raise":
        missing = _union_grid_gaps(union_index, delta)
        if missing:
            first_missing = missing[0]
            prior = union_index[union_index < first_missing]
            prev_ts = prior.max() if len(prior) else None
            raise DataIntegrityError(
                f"grid gap (D§4.6): missing bar at {first_missing!r} "
                f"(preceding present timestamp: {prev_ts!r}, expected spacing {delta!r})"
            )
        return MarketData(open=open_wide, close=close_wide)

    if policy == "reindex_nan":
        if len(union_index) == 0:
            return MarketData(open=open_wide, close=close_wide)
        full = pd.date_range(union_index.min(), union_index.max(), freq=delta, tz="UTC")
        return MarketData(open=open_wide.reindex(full), close=close_wide.reindex(full))

    # policy == "segment"
    if len(union_index) == 0:
        return []
    segments = []
    seg_start_pos = 0
    positions = list(range(len(union_index)))
    for i in positions[1:]:
        if union_index[i] - union_index[i - 1] != delta:
            segments.append((seg_start_pos, i - 1))
            seg_start_pos = i
    segments.append((seg_start_pos, len(union_index) - 1))
    result = []
    for a, b in segments:
        idx = union_index[a : b + 1]
        result.append(MarketData(open=open_wide.loc[idx], close=close_wide.loc[idx]))
    return result
