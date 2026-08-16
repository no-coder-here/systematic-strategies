"""D§5.5.1 — `asset_ctxs` compact streaming extraction, AUTHORIZED by v1.3
DECISION 4.

    fetch one raw segment -> extract required fields at required timestamps
      -> append to compact processed Parquet -> DISCARD that raw input -> next

This module builds on `oracle.py`'s per-event resolution logic
(`AssetCtxsRow`, `GAP_TOLERANCE`, `_resolve`) and adds:

    1. A REAL segment fetcher (`s3_segment_fetcher`) that streams ONE day's
       `s3://hyperliquid-archive/asset_ctxs/[date].csv.lz4` object via
       `aws s3 cp ... - --request-payer requester | lz4 -d -c`, parsed
       row-by-row (never materializing the whole file, D§5.5.1 rule 3).
       Calling it performs a REAL, BILLED (requester-pays) network fetch and
       requires AWS credentials -- see the module-level `CostWarning` docs
       below and the implementation report for why this function exists but
       was NOT invoked against the live bucket by the implementing agent.
    2. A persisted high-water mark (`AssetCtxsHighWaterMark`) so a refresh
       over an already-processed date range is a NO-OP, never a duplicate
       append (D§5.5.1 rule 6, M46).
    3. Per-day orchestration (`run_incremental_extraction`) that carries
       forward only O(symbols) state (the single most-recent-so-far row per
       symbol) across day boundaries -- never the full archive, and never
       even one full day beyond the day currently being processed (M47).
    4. An alignment report (`build_alignment_report`, D§5.5.1 rule 7):
       events priced vs. unpriced, and the event→ctx offset distribution.

REAL NETWORK / REAL COST WARNING
---------------------------------
`s3_segment_fetcher` (and therefore `run_incremental_extraction` when called
WITHOUT an injected `segment_fetcher=...` override) performs a REAL S3
Requester-Pays GET against `s3://hyperliquid-archive/asset_ctxs/`, which:
    - requires real AWS credentials to be present and configured, and
    - is BILLED to that AWS account (~8.24 MB/day, F18) -- real money.
Per CLAUDE.md's Work Order Autonomy escalation list ("credentials or private
data are required"; "live trading or funds could be affected"), invoking
this against the real bucket is a decision the IMPLEMENTING AGENT does not
make unilaterally, even though DECISION 4 authorizes the *pipeline design*.
Every test in this module's test suite passes an explicit, MOCKED
`segment_fetcher` and never imports/calls `s3_segment_fetcher` itself.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

import pandas as pd

from ..base import ensure_utc_timestamp
from .oracle import GAP_TOLERANCE, AssetCtxsRow, _parse_row, _resolve

__all__ = [
    "AssetCtxsHighWaterMark",
    "ExtractionResult",
    "AlignmentReport",
    "high_water_mark_path",
    "read_high_water_mark",
    "write_high_water_mark",
    "compact_parquet_path",
    "archive_dates_in_range",
    "s3_segment_fetcher",
    "run_incremental_extraction",
    "build_alignment_report",
]

# D§1.1 F12 -- one raw segment per calendar day: `[date].csv.lz4`.
_SEGMENT_DATE_FMT = "%Y-%m-%d"


# ---------------------------------------------------------------------------
# High-water mark persistence (D§5.5.1 rule 6, M46)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetCtxsHighWaterMark:
    last_processed_date: Optional[str]  # "YYYY-MM-DD", inclusive; None iff nothing processed yet
    processed_dates: tuple  # tuple[str, ...] of every date ever successfully processed
    total_rows: int  # total compact rows persisted so far
    # Audit finding D3 -- the last seen ctx row PER SYMBOL, carried across CALLS.
    # `_process_day` carries this between days within one call, but it used to be
    # function-local, so the realistic incremental usage (D§5.5.1 rule 6: one call
    # per new day) silently dropped it at every call boundary. A funding event
    # just after midnight, within GAP_TOLERANCE of the previous day's last row,
    # was then left unpriced -- while the same two days processed in a SINGLE call
    # priced it correctly. The only cross-day test did exactly that, masking it.
    # {symbol: {"time": iso8601, "oracle_px": float}}
    carry_rows: dict = field(default_factory=dict)

    def to_json_dict(self) -> dict:
        return {
            "last_processed_date": self.last_processed_date,
            "processed_dates": list(self.processed_dates),
            "total_rows": self.total_rows,
            "carry_rows": {k: dict(v) for k, v in self.carry_rows.items()},
        }

    @staticmethod
    def from_json_dict(d: dict) -> "AssetCtxsHighWaterMark":
        return AssetCtxsHighWaterMark(
            last_processed_date=d.get("last_processed_date"),
            processed_dates=tuple(d.get("processed_dates", [])),
            total_rows=d.get("total_rows", 0),
            carry_rows=dict(d.get("carry_rows", {})),
        )

    @staticmethod
    def empty() -> "AssetCtxsHighWaterMark":
        return AssetCtxsHighWaterMark(
            last_processed_date=None, processed_dates=(), total_rows=0, carry_rows={}
        )

    def to_carry_state(self) -> dict:
        """Rehydrates `{symbol: AssetCtxsRow}` for `_process_day`."""
        return {
            sym: AssetCtxsRow(time=pd.Timestamp(v["time"]), coin=sym, oracle_px=float(v["oracle_px"]))
            for sym, v in self.carry_rows.items()
        }

    @staticmethod
    def carry_state_to_json(last_row_by_symbol: dict) -> dict:
        return {
            sym: {"time": row.time.isoformat(), "oracle_px": float(row.oracle_px)}
            for sym, row in last_row_by_symbol.items()
            if row is not None
        }


def high_water_mark_path(base_dir) -> Path:
    return Path(base_dir) / "metadata" / "hyperliquid" / "asset_ctxs_high_water_mark.json"


def read_high_water_mark(base_dir) -> AssetCtxsHighWaterMark:
    path = high_water_mark_path(base_dir)
    if not path.exists():
        return AssetCtxsHighWaterMark.empty()
    with open(path, encoding="utf-8") as f:
        return AssetCtxsHighWaterMark.from_json_dict(json.load(f))


def write_high_water_mark(base_dir, hwm: AssetCtxsHighWaterMark) -> Path:
    path = high_water_mark_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hwm.to_json_dict(), f, indent=2, sort_keys=True)
    return path


def compact_parquet_path(base_dir) -> Path:
    """D§5.5.1 -- the SINGLE compact processed artifact, `{timestamp, symbol,
    oracle_price}`, spanning every date ever processed. Not one file per day
    -- this IS the "compact processed Parquet" the pipeline appends to.
    """
    return Path(base_dir) / "processed" / "hyperliquid" / "asset_ctxs_oracle" / "compact.parquet"


# ---------------------------------------------------------------------------
# Segment enumeration
# ---------------------------------------------------------------------------


def archive_dates_in_range(start_date: str, end_date: str) -> list:
    """Inclusive list of `YYYY-MM-DD` date strings, one per archive segment
    (D§1.1 F12: one file per calendar day)."""
    start = pd.Timestamp(start_date, tz="UTC").normalize()
    end = pd.Timestamp(end_date, tz="UTC").normalize()
    if end < start:
        raise ValueError(f"end_date {end_date!r} is before start_date {start_date!r}")
    return [d.strftime(_SEGMENT_DATE_FMT) for d in pd.date_range(start, end, freq="1D", tz="UTC")]


# ---------------------------------------------------------------------------
# REAL segment fetcher -- REQUESTER-PAYS, REQUIRES AWS CREDENTIALS.
# See the module docstring's "REAL NETWORK / REAL COST WARNING".
# ---------------------------------------------------------------------------


def s3_segment_fetcher(date: str) -> Iterator[dict]:
    """Streams ONE day's raw `asset_ctxs` segment
    (`s3://hyperliquid-archive/asset_ctxs/{date}.csv.lz4`) as an iterator of
    row dicts, WITHOUT ever writing the object to local disk and WITHOUT
    materializing the whole file in memory: `aws s3 cp ... -` streams to a
    pipe, `lz4 -d -c` decompresses that pipe, and `csv.DictReader` parses it
    row-by-row. Peak local raw footprint is bounded by pipe buffering only
    (D§5.5.1 rule 2/3, M47) -- nothing durable is ever created.

    REQUIRES `--request-payer requester` (D§14.6/F9): this is a REAL,
    BILLED network call. Every unit test in this package injects a mocked
    `segment_fetcher` instead of calling this function.
    """
    url = f"s3://hyperliquid-archive/asset_ctxs/{date}.csv.lz4"
    aws_proc = subprocess.Popen(
        ["aws", "s3", "cp", url, "-", "--request-payer", "requester"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    lz4_proc = subprocess.Popen(
        ["lz4", "-d", "-c"],
        stdin=aws_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    aws_proc.stdout.close()  # allow aws_proc to receive SIGPIPE if lz4_proc exits early
    try:
        text_stream = io.TextIOWrapper(lz4_proc.stdout, encoding="utf-8", newline="")
        reader = csv.DictReader(text_stream)
        yield from reader
    finally:
        lz4_proc.wait()
        aws_proc.wait()
        if lz4_proc.returncode != 0:
            err = lz4_proc.stderr.read().decode(errors="replace") if lz4_proc.stderr else ""
            raise RuntimeError(f"lz4 decompression of {url!r} failed (rc={lz4_proc.returncode}): {err}")
        if aws_proc.returncode != 0:
            err = aws_proc.stderr.read().decode(errors="replace") if aws_proc.stderr else ""
            raise RuntimeError(f"aws s3 cp {url!r} failed (rc={aws_proc.returncode}): {err}")


SegmentFetcher = Callable[[str], Iterable[dict]]


# ---------------------------------------------------------------------------
# Per-day resolution (carries O(symbols) state across day boundaries, M47)
# ---------------------------------------------------------------------------


def _process_day(
    rows_iter: Iterable[dict],
    pending_today: dict,
    last_row_by_symbol: dict,
) -> dict:
    """Resolves every event in `pending_today` (mutated: emptied as events
    resolve) against `rows_iter` (ONE day's rows), carrying forward
    `last_row_by_symbol` (mutated in place) for the NEXT day's near-midnight
    events. Returns `{(symbol, event_ts): (price_or_None, ctx_time_or_None)}`
    for every event resolved THIS call.

    Bounded state: `last_row_by_symbol` holds at most one row per symbol
    (~200 symbols) -- never the day's full row set, and never any other
    day's rows (M47).
    """
    results: dict = {}
    for raw in rows_iter:
        row = _parse_row(raw)
        sym = row.coin
        if sym not in pending_today or not pending_today[sym]:
            last_row_by_symbol[sym] = row
            continue
        while pending_today[sym] and pending_today[sym][0] < row.time:
            event_ts = pending_today[sym].pop(0)
            prior = last_row_by_symbol.get(sym)
            results[(sym, event_ts)] = (_resolve(prior, event_ts), prior.time if prior is not None else None)
        if pending_today[sym] and pending_today[sym][0] == row.time:
            event_ts = pending_today[sym].pop(0)
            results[(sym, event_ts)] = (row.oracle_px, row.time)
        last_row_by_symbol[sym] = row

    # Anything still pending at day-end resolves against whatever was last
    # seen (possibly nothing, possibly a row from an EARLIER day carried
    # forward) -- this day will never be revisited, so it must be resolved
    # now, not deferred.
    for sym in list(pending_today.keys()):
        for event_ts in pending_today[sym]:
            prior = last_row_by_symbol.get(sym)
            results[(sym, event_ts)] = (_resolve(prior, event_ts), prior.time if prior is not None else None)
        pending_today[sym] = []
    return results


# ---------------------------------------------------------------------------
# Incremental orchestration (D§5.5.1 rules 2, 6; M46, M47)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionResult:
    dates_processed: tuple  # tuple[str, ...] -- NEW dates processed THIS call (empty iff no-op)
    rows_appended: int  # compact rows appended THIS call
    total_rows: int  # total compact rows after this call
    high_water_mark: Optional[str]  # last_processed_date after this call
    skipped_no_op: bool  # True iff the requested range was already fully covered
    event_ctx_offsets: tuple = ()  # tuple[pd.Timedelta, ...] -- event_ts - ctx_time, priced events THIS call only


def run_incremental_extraction(
    base_dir,
    funding_events: pd.DataFrame,
    start_date: str,
    end_date: str,
    segment_fetcher: SegmentFetcher = s3_segment_fetcher,
) -> ExtractionResult:
    """D§5.5.1 -- the strictly incremental pipeline:

        fetch one raw segment -> extract -> append to compact Parquet
          -> DISCARD that raw input -> next

    `funding_events` MUST have `timestamp` (tz-aware UTC) and `symbol`
    columns (a normalized funding frame, D§5.1). Only events falling within
    a date THIS CALL actually processes are resolved and appended; events
    outside `[start_date, end_date]` are ignored (a separate call covers
    them, each incrementally, via the persisted high-water mark).

    A refresh over an ALREADY-PROCESSED range is a NO-OP (M46): if every
    date in `[start_date, end_date]` is `<=` the persisted
    `last_processed_date`, `segment_fetcher` is NEVER called and the compact
    parquet / high-water mark are left untouched.
    """
    hwm = read_high_water_mark(base_dir)
    all_dates = archive_dates_in_range(start_date, end_date)

    if hwm.last_processed_date is not None:
        new_dates = [d for d in all_dates if d > hwm.last_processed_date]
    else:
        new_dates = all_dates

    if not new_dates:
        # D§5.5.1 rule 6 (M46) -- genuinely a no-op: fetcher never called,
        # compact parquet / HWM never touched.
        return ExtractionResult(
            dates_processed=(),
            rows_appended=0,
            total_rows=hwm.total_rows,
            high_water_mark=hwm.last_processed_date,
            skipped_no_op=True,
        )

    events_by_day: dict = {}
    for _, r in funding_events.iterrows():
        day_key = r["timestamp"].strftime(_SEGMENT_DATE_FMT)
        events_by_day.setdefault(day_key, {}).setdefault(r["symbol"], []).append(r["timestamp"])
    for day_key in events_by_day:
        for sym in events_by_day[day_key]:
            events_by_day[day_key][sym].sort()

    # Audit D3 -- resume the per-symbol carry state persisted by the PREVIOUS
    # call, so a near-midnight event on the first new day can still resolve
    # against the last row of the last already-processed day.
    last_row_by_symbol: dict = hwm.to_carry_state()
    all_results: dict = {}
    processed_dates: list = []

    for date in new_dates:
        pending_today = {sym: list(ts_list) for sym, ts_list in events_by_day.get(date, {}).items()}
        rows_iter = segment_fetcher(date)  # ONE segment fetched, streamed, then discarded (M47)
        day_results = _process_day(rows_iter, pending_today, last_row_by_symbol)
        all_results.update(day_results)
        processed_dates.append(date)
        # last_row_by_symbol now carries ONLY the single most-recent row per
        # symbol into the next iteration -- this day's raw rows are already
        # out of scope (the generator is exhausted and not retained).

    compact_rows = []
    offsets = []
    for (symbol, event_ts), (price, ctx_time) in all_results.items():
        compact_rows.append({"timestamp": event_ts, "symbol": symbol, "oracle_price": price})
        if price is not None and ctx_time is not None:
            offsets.append(event_ts - ctx_time)

    new_df = pd.DataFrame(compact_rows)
    if len(new_df):
        new_df["symbol"] = new_df["symbol"].astype("string")
        new_df["oracle_price"] = new_df["oracle_price"].astype("float64")
        new_df = new_df.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)

    path = compact_parquet_path(base_dir)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True) if len(new_df) else existing
    else:
        combined = new_df
    if len(combined):
        combined = combined.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(path, index=False)

    new_hwm = AssetCtxsHighWaterMark(
        last_processed_date=processed_dates[-1],
        processed_dates=tuple(sorted(set(hwm.processed_dates) | set(processed_dates))),
        total_rows=len(combined),
        # Audit D3 -- persist carry state for the NEXT call. Bounded at one row
        # per symbol (~200), never a day's row set.
        carry_rows=AssetCtxsHighWaterMark.carry_state_to_json(last_row_by_symbol),
    )
    write_high_water_mark(base_dir, new_hwm)

    return ExtractionResult(
        dates_processed=tuple(processed_dates),
        rows_appended=len(new_df),
        total_rows=len(combined),
        high_water_mark=new_hwm.last_processed_date,
        skipped_no_op=False,
        event_ctx_offsets=tuple(offsets),
    )


# ---------------------------------------------------------------------------
# D§5.5.1 rule 7 -- alignment validation report.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlignmentReport:
    n_events: int
    n_priced: int
    n_unpriced: int
    offset_seconds_percentiles: dict  # {1,5,25,50,75,95,99: float}, event_ts - ctx_time in seconds
    max_offset_seconds: Optional[float]
    gap_tolerance_seconds: float


_PERCENTILES = (1, 5, 25, 50, 75, 95, 99)


def build_alignment_report(compact_df: pd.DataFrame, funding_events: pd.DataFrame,
                            event_ctx_offsets: Optional[Iterable[pd.Timedelta]] = None) -> AlignmentReport:
    """D§5.5.1 rule 7 -- validates that every funding event that SHOULD carry
    a price has one, and reports the count priced/unpriced plus the
    event→ctx offset distribution (in seconds). `event_ctx_offsets`, if
    given (e.g. from `ExtractionResult.event_ctx_offsets`), is used directly
    for the offset distribution; otherwise offsets cannot be recovered from
    `compact_df` alone (it deliberately does not persist `ctx_time`, only
    `oracle_price` -- D§5.5.1's approved compact shape) and the distribution
    is reported empty.
    """
    merged = funding_events.merge(
        compact_df[["timestamp", "symbol", "oracle_price"]], on=["timestamp", "symbol"], how="left",
    )
    n_events = len(merged)
    priced_mask = merged["oracle_price"].notna()
    n_priced = int(priced_mask.sum())
    n_unpriced = n_events - n_priced

    offsets_seconds: list = []
    if event_ctx_offsets is not None:
        offsets_seconds = [abs(o.total_seconds()) for o in event_ctx_offsets]

    if offsets_seconds:
        s = pd.Series(offsets_seconds)
        pct = {p: float(s.quantile(p / 100.0)) for p in _PERCENTILES}
        max_offset = float(s.max())
    else:
        pct = {p: None for p in _PERCENTILES}
        max_offset = None

    return AlignmentReport(
        n_events=n_events,
        n_priced=n_priced,
        n_unpriced=n_unpriced,
        offset_seconds_percentiles=pct,
        max_offset_seconds=max_offset,
        gap_tolerance_seconds=GAP_TOLERANCE.total_seconds(),
    )
