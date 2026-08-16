"""D§15.2 — segment manifests for mixed-provenance OHLCV series.

D§2.1: MUST NOT import `src/data/hyperliquid/**` or `src/data/binance/**`.
Operates purely on already-normalized `pd.DataFrame`s carrying the D§4.1
per-row source-attribution columns.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.models import DataIntegrityError

from .schemas import assert_source_type_allowed

__all__ = [
    "SourceSegment",
    "build_segment_manifest",
    "assert_segments_agree_with_rows",
    "frame_uses_proxy_data",
    "splice_with_explicit_seam",
]


@dataclass(frozen=True)
class SourceSegment:
    """D§15.2 — one contiguous, single-provenance run within a dataset."""

    source_venue: str
    native_or_proxy: str
    source_type: str
    dataset_id: str
    start_timestamp: pd.Timestamp
    end_timestamp: pd.Timestamp

    def __post_init__(self) -> None:
        assert_source_type_allowed(self.source_type)
        if self.end_timestamp < self.start_timestamp:
            raise DataIntegrityError(
                f"SourceSegment end_timestamp {self.end_timestamp} < start_timestamp {self.start_timestamp} (D§15.2)"
            )


def build_segment_manifest(df: pd.DataFrame) -> tuple:
    """D§15.2 — builds an ordered, contiguous, non-overlapping segment
    manifest for a SINGLE symbol's sorted OHLCV frame, by grouping
    consecutive rows sharing identical (source_venue, native_or_proxy,
    source_type, dataset_id).

    Rule 1 (D§15.2.1): segments are contiguous and non-overlapping; the
    transition timestamp is explicit (the next segment's `start_timestamp`),
    never implied by ordering.
    """
    if df.empty:
        return ()
    if not df["timestamp"].is_monotonic_increasing:
        raise DataIntegrityError("build_segment_manifest requires a timestamp-sorted frame (D§15.2.1)")

    segments = []
    keys = list(zip(df["source_venue"], df["native_or_proxy"], df["source_type"], df["dataset_id"]))
    seg_start_pos = 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or keys[i] != keys[seg_start_pos]:
            venue, proxy, stype, dsid = keys[seg_start_pos]
            segments.append(
                SourceSegment(
                    source_venue=venue,
                    native_or_proxy=proxy,
                    source_type=stype,
                    dataset_id=dsid,
                    start_timestamp=df["timestamp"].iloc[seg_start_pos],
                    end_timestamp=df["timestamp"].iloc[i - 1],
                )
            )
            seg_start_pos = i

    # D§15.2.1 — defensive re-assertion: contiguous & non-overlapping.
    for a, b in zip(segments, segments[1:]):
        if b.start_timestamp <= a.end_timestamp:
            raise DataIntegrityError(
                f"segment manifest overlap/non-contiguity (D§15.2.1): "
                f"[{a.start_timestamp},{a.end_timestamp}] then [{b.start_timestamp},{b.end_timestamp}]"
            )
    return tuple(segments)


def assert_segments_agree_with_rows(df: pd.DataFrame, segments: tuple) -> None:
    """D§15.2.2 — every row's own source_* columns MUST agree with the
    segment manifest entry covering it. Row-level and manifest-level
    provenance are two independent records of the same fact; disagreement
    means one of them is a lie, and this MUST be caught, not tolerated.
    """
    if df.empty:
        return
    ordered = sorted(segments, key=lambda s: s.start_timestamp)
    for _, row in df.iterrows():
        t = row["timestamp"]
        covering = [s for s in ordered if s.start_timestamp <= t <= s.end_timestamp]
        if not covering:
            raise DataIntegrityError(f"row at {t} is not covered by any segment in the manifest (D§15.2.2)")
        if len(covering) > 1:
            raise DataIntegrityError(f"row at {t} is covered by MORE THAN ONE segment (D§15.2.1 overlap)")
        seg = covering[0]
        row_tuple = (row["source_venue"], row["native_or_proxy"], row["source_type"], row["dataset_id"])
        seg_tuple = (seg.source_venue, seg.native_or_proxy, seg.source_type, seg.dataset_id)
        if row_tuple != seg_tuple:
            raise DataIntegrityError(
                f"row at {t} source_* {row_tuple} disagrees with covering segment {seg_tuple} (D§15.2.2)"
            )


def splice_with_explicit_seam(first_df: pd.DataFrame, second_df: pd.DataFrame, seam_timestamp: pd.Timestamp,
                               opt_in: bool = False) -> pd.DataFrame:
    """D§16.7 (M40) — intra-symbol venue splicing (e.g. Binance-proxy prefix
    then Hyperliquid-native) is DISABLED BY DEFAULT and requires EXPLICIT
    opt-in (`opt_in=True`). When enabled, the seam bar (the FIRST bar of
    `second_df`, i.e. the first bar whose close-to-close return spans the
    venue transition) MUST be flagged via a `seam_bar` column so that seam
    return can be excluded from any downstream return series — never
    silently left indistinguishable from an ordinary bar.

    `first_df` MUST end strictly before `seam_timestamp`; `second_df` MUST
    start at-or-after it — contiguity/non-overlap is asserted, never assumed.
    """
    if not opt_in:
        raise DataIntegrityError(
            "D§16.7: intra-symbol venue splicing is DISABLED by default. "
            "Pass opt_in=True to explicitly enable it (a reviewed decision, never a default)."
        )
    if len(first_df) and first_df["timestamp"].max() >= seam_timestamp:
        raise DataIntegrityError("splice_with_explicit_seam: first_df extends at/past seam_timestamp (D§16.7)")
    if len(second_df) and second_df["timestamp"].min() < seam_timestamp:
        raise DataIntegrityError("splice_with_explicit_seam: second_df starts before seam_timestamp (D§16.7)")

    a = first_df.copy()
    b = second_df.copy()
    a["seam_bar"] = False
    b["seam_bar"] = False
    if len(b):
        b.iloc[0, b.columns.get_loc("seam_bar")] = True
    return pd.concat([a, b], ignore_index=True)


def frame_uses_proxy_data(df: pd.DataFrame) -> bool:
    """D§15.2.3 — True iff any row has `native_or_proxy == 'proxy'`. Callers
    building an engine-facing `DatasetProvenance`/`BacktestResult` use this to
    ensure `uses_proxy_data` is surfaced (§13.1 obligation 3).
    """
    if df.empty:
        return False
    return bool((df["native_or_proxy"] == "proxy").any())
