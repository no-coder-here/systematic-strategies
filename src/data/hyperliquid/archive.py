"""D§14 — Hyperliquid archival OHLCV reconstruction: **VALIDATION-ONLY**
(D§14.0, demoted by AMENDMENT B). The full `node_trades`/`node_fills`/
`node_fills_by_block` backfill is CANCELLED and MUST NOT be performed — no
code in this module ever downloads from the Requester-Pays archive buckets
(F9); every test against it uses MOCKED trade/fill records.

What this module retains, and why (D§14.0):
    1. Reconstruction code/design, for bounded VALIDATION of Hyperliquid
       data semantics (never a prerequisite for normal research, never on
       any default research path).
    2. The fills-vs-trades double-counting trap (D§14.3) and the `hash`
       non-uniqueness trap (D§14.4), which still apply to any bounded
       sample anyone draws later.
    3. The D§14.5 overlap-validation machinery (per-field agreement rates
       and relative-difference distributions, NOT a single pass/fail).

Reconstructed bars produced here carry `source_type` in
`{"hyperliquid_node_trades", "hyperliquid_node_fills"}` (D§15.1) and
`native_or_proxy="native"` (official archive data) — but per D§14.1, source
A (genuinely-traded `candleSnapshot` bars) ALWAYS wins where both exist;
reconstruction is retained only for validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from backtest.models import DataIntegrityError

from ..base import ensure_utc_timestamp

__all__ = [
    "reduce_fills_to_trades",
    "deduplicate_trade_records",
    "trades_to_ohlcv",
    "OverlapReport",
    "compare_ohlcv_overlap",
    "verify_hourly_coverage",
    "assert_not_quote_derived",
    "merge_with_official_priority",
    "REJECTED_QUOTE_SOURCES",
]

# D§14.7 — quote data (L2 book snapshots, `asset_ctxs.mid_px`) is REJECTED as
# an OHLCV source: no traded volume, no trade count. Reaches further back
# than any trade archive, which is exactly why the temptation must be
# refused in writing (M21).
REJECTED_QUOTE_SOURCES = frozenset({"l2_book", "mid_px", "asset_ctxs_mid_px"})


def assert_not_quote_derived(source_kind: str) -> None:
    """D§14.7 (M21) — refuses to promote a quote-derived source into OHLCV.
    Splicing quote-derived OHLC onto traded OHLC changes the price-formation
    process mid-series without changing the column names — rejected
    outright, not down-weighted or flagged.
    """
    if source_kind in REJECTED_QUOTE_SOURCES:
        raise DataIntegrityError(
            f"D§14.7: {source_kind!r} is quote-derived (no traded volume/trade count) and is "
            "REJECTED as an OHLCV source, never promoted"
        )


def verify_hourly_coverage(available_hours: Sequence[pd.Timestamp], expected_start: pd.Timestamp,
                            expected_end: pd.Timestamp) -> list:
    """D§14.4 — "Hourly archive files MUST be checked for ... missing hours
    — coverage MUST be verified PER HOUR, not per day, because
    `node_fills_by_block` is streamed from a non-validating node whose
    downtime would appear as a silent hole."

    Returns the sorted list of missing hourly timestamps in
    `[expected_start, expected_end)`. Does NOT raise — the caller decides
    whether a missing hour is fatal; but critically, a missing hour is
    reported as MISSING (never silently treated as present, M28).
    """
    have = set(available_hours)
    expected = pd.date_range(expected_start, expected_end, freq="1h", tz="UTC", inclusive="left")
    return sorted(t for t in expected if t not in have)


# ---------------------------------------------------------------------------
# D§14.3 (BLOCKING) — fills are not trades: a trade generates TWO fills
# (maker + taker). Summing `sz` over fills DOUBLES volume and trade count
# relative to a `node_trades`-derived or `candleSnapshot` bar (M19).
# ---------------------------------------------------------------------------


def reduce_fills_to_trades(fills: Sequence[dict], trade_id_field: str = "tid") -> list:
    """D§14.3 (BLOCKING, M19) — reduces a list of FILL records (one row per
    COUNTERPARTY, i.e. two rows per trade: maker + taker) to one row per
    TRADE, by grouping on `trade_id_field` and keeping exactly one side.

    Each group MUST contain exactly 2 fills (maker+taker) with EQUAL `sz`
    (the same trade, seen from both sides) — anything else is a blocking
    defect (a genuinely malformed/incomplete archive sample), not something
    to silently average or sum away.

    This is the function whose ABSENCE (i.e. summing fills directly without
    calling this first) is D§14.3's "single most dangerous defect available
    in this amendment": it would silently double volume in the fills era
    while the `node_trades` era (already one-row-per-trade, F11) stays 1x —
    a regime break in the volume series exactly at the source seam.
    """
    groups: dict = {}
    for fill in fills:
        groups.setdefault(fill[trade_id_field], []).append(fill)

    trades = []
    for tid, group in groups.items():
        if len(group) != 2:
            raise DataIntegrityError(
                f"D§14.3: trade_id={tid!r} has {len(group)} fill(s), expected exactly 2 (maker+taker); "
                "reducing fills to trades requires a complete counterparty pair"
            )
        a, b = group
        if a["sz"] != b["sz"]:
            raise DataIntegrityError(
                f"D§14.3: trade_id={tid!r} maker/taker fills disagree on sz ({a['sz']} != {b['sz']}) "
                "-- not the same trade seen from both sides"
            )
        # Keep exactly ONE side (arbitrary but deterministic: the first).
        trades.append(dict(a))
    return trades


# ---------------------------------------------------------------------------
# D§14.4 (BLOCKING) — `hash` is a TRANSACTION hash spanning MULTIPLE trades
# (F11) and MUST NOT be used alone as a trade identity (M20).
# ---------------------------------------------------------------------------


def deduplicate_trade_records(records: Sequence[dict], key_fields: Sequence[str]) -> list:
    """D§14.4 (BLOCKING, M20) — deduplicates trade records on a
    CALLER-SUPPLIED compound key (never `hash` alone). Two records sharing
    the key are only collapsed if they are BYTE-IDENTICAL on every field;
    anything else (including two DIFFERENT trades that happen to share the
    same `hash`, F11) raises rather than silently collapsing distinct
    trades — which would understate volume.
    """
    if "hash" in key_fields and len(key_fields) == 1:
        raise ValueError(
            "D§14.4: deduplicating on 'hash' ALONE is prohibited (hash is a transaction hash "
            "spanning multiple trades, F11) — supply a compound key that is actually unique"
        )
    seen: dict = {}
    out = []
    for rec in records:
        key = tuple(rec.get(f) for f in key_fields)
        if key in seen:
            if seen[key] != rec:
                raise DataIntegrityError(
                    f"D§14.4: collision on key {key!r} between NON-identical records "
                    f"{seen[key]!r} != {rec!r} — a real duplicate must be byte-identical, "
                    "this is a distinct trade wrongly sharing a key"
                )
            continue  # true byte-identical duplicate (e.g. hour-boundary re-emission).
        seen[key] = rec
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# D§14.1/D§14.2 — trade-record -> OHLCV bucket aggregation.
# ---------------------------------------------------------------------------


def trades_to_ohlcv(trades: Sequence[dict], symbol: str, interval: pd.Timedelta, source_type: str) -> pd.DataFrame:
    """Aggregates already-reduced-to-trades, already-deduplicated records
    (each `{time (naive ns per F11, or tz-aware pd.Timestamp), px, sz}`) into
    left-labelled OHLCV buckets of width `interval`.

    F11: `node_trades`' `time` field is NAIVE nanosecond-precision with NO
    documented timezone (`2025-03-22T10:48:33.216798262`, no `Z`). This
    function REFUSES a naive timestamp (consistent with D§3.1.1) UNLESS the
    caller has already made an explicit, documented UTC assertion and
    converted it to tz-aware UTC before calling — silently localizing here
    would repeat the exact defect D§3.1.1/M14 forbids.
    """
    if source_type not in ("hyperliquid_node_trades", "hyperliquid_node_fills"):
        raise ValueError(f"trades_to_ohlcv source_type must be an archive-reconstruction type, got {source_type!r}")
    if not trades:
        return pd.DataFrame(columns=["timestamp", "symbol", "open", "high", "low", "close", "volume", "trade_count"])

    rows = []
    for t in trades:
        ts = ensure_utc_timestamp(t["time"])
        rows.append({"time": ts, "px": float(t["px"]), "sz": float(t["sz"])})
    df = pd.DataFrame(rows).sort_values("time", kind="mergesort")
    df["bucket"] = df["time"].dt.floor(interval)

    out = df.groupby("bucket").agg(
        open=("px", "first"),
        high=("px", "max"),
        low=("px", "min"),
        close=("px", "last"),
        volume=("sz", "sum"),
        trade_count=("sz", "count"),
    ).reset_index()
    out = out.rename(columns={"bucket": "timestamp"})
    out["symbol"] = symbol
    out["symbol"] = out["symbol"].astype("string")
    out["trade_count"] = out["trade_count"].astype("int64")
    return out[["timestamp", "symbol", "open", "high", "low", "close", "volume", "trade_count"]]


# ---------------------------------------------------------------------------
# D§14.5 (BLOCKING) — overlap validation against official candles. Per-field
# agreement rates and relative-difference DISTRIBUTIONS, NOT a single
# pass/fail.
# ---------------------------------------------------------------------------


def merge_with_official_priority(reconstructed: pd.DataFrame, official: pd.DataFrame) -> pd.DataFrame:
    """D§14.1 (M22) — "Where A [official candleSnapshot] and B [archive
    reconstruction] both exist for an interval, A wins and B is retained
    only for validation." This is the enforcement of that priority rule at
    merge time: for any timestamp present in BOTH, the OFFICIAL row is kept
    verbatim; reconstructed rows are used ONLY to fill timestamps official
    does not cover at all.
    """
    if official.empty:
        return reconstructed.copy()
    off = official.set_index("timestamp")
    rec = reconstructed.set_index("timestamp") if not reconstructed.empty else reconstructed
    only_reconstructed = rec.index.difference(off.index) if not reconstructed.empty else []
    parts = [off]
    if len(only_reconstructed):
        parts.append(rec.loc[only_reconstructed])
    merged = pd.concat(parts).sort_index()
    merged.index.name = "timestamp"
    return merged.reset_index()


@dataclass(frozen=True)
class OverlapReport:
    symbol: str
    n_overlapping_bars: int
    field_relative_diff_percentiles: dict  # {"open": {...}, ..., "volume": {...}}
    volume_ratio_percentiles: dict  # reconstructed_volume / official_volume, catches the 2x D§14.3 signature
    missing_bars_in_reconstruction: int
    missing_bars_in_official: int


_PERCENTILES = (1, 5, 25, 50, 75, 95, 99)


def compare_ohlcv_overlap(reconstructed: pd.DataFrame, official: pd.DataFrame, symbol: str) -> OverlapReport:
    """D§14.5 (BLOCKING) — compares reconstructed vs official candles on
    open/high/low/close/volume, bar count and gaps. Reports per-field
    RELATIVE DIFFERENCE percentiles (not a single pass/fail); an explicit,
    separate `volume_ratio_percentiles` surfaces D§14.3's ~2x signature
    directly rather than burying it inside a generic "diff" number.
    """
    rec = reconstructed.set_index("timestamp").sort_index()
    off = official.set_index("timestamp").sort_index()
    common = rec.index.intersection(off.index)

    field_pcts = {}
    for field in ("open", "high", "low", "close"):
        if len(common) == 0:
            field_pcts[field] = {p: None for p in _PERCENTILES}
            continue
        diff = (rec.loc[common, field] - off.loc[common, field]).abs() / off.loc[common, field].abs()
        field_pcts[field] = {p: float(diff.quantile(p / 100.0)) for p in _PERCENTILES}

    if len(common) == 0:
        vol_ratio_pcts = {p: None for p in _PERCENTILES}
    else:
        ratio = rec.loc[common, "volume"] / off.loc[common, "volume"].replace(0, math.nan)
        ratio = ratio.dropna()
        vol_ratio_pcts = {p: (float(ratio.quantile(p / 100.0)) if len(ratio) else None) for p in _PERCENTILES}

    return OverlapReport(
        symbol=symbol,
        n_overlapping_bars=int(len(common)),
        field_relative_diff_percentiles=field_pcts,
        volume_ratio_percentiles=vol_ratio_pcts,
        missing_bars_in_reconstruction=int(len(off.index.difference(rec.index))),
        missing_bars_in_official=int(len(rec.index.difference(off.index))),
    )
