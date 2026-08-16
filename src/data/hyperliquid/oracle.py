"""D§5.5 / D§5.5.1 — `event_price` funding basis via `asset_ctxs` oracle
prices, AMENDMENT A (revised) + AMENDMENT B's compact-artifact policy.

STATUS (v1.3 DECISION 4): the `asset_ctxs` bulk download / compact streaming
extraction pipeline is now AUTHORIZED and IMPLEMENTED end-to-end in
`asset_ctxs_archive.py` (real segment fetcher, persisted high-water mark,
incremental refresh, alignment reporting). THIS module (`oracle.py`)
remains the pure per-event resolution logic and is unit-tested against
MOCKED per-minute rows only, exactly as before -- it never opens a network
connection itself; `asset_ctxs_archive.py` is where the real (streamed,
never-fully-materialized, requester-pays) segment fetching lives, gated
behind an explicitly-injected `segment_fetcher` in every test. The default
funding basis throughout this layer remains `"period_start"`
(`notional_price = NaN`) until a specific research run explicitly wires in
the compact `asset_ctxs_oracle` artifact produced by
`asset_ctxs_archive.run_incremental_extraction`.

D§5.5.1 rules enforced here:
    1. Raw `asset_ctxs` rows are never retained after extraction — this
       module's public entry point streams an iterator and returns ONLY the
       compact `{timestamp, symbol, oracle_price}` result, never the full
       per-minute row set (M41).
    2. Extraction MUST stream per file/row; it MUST NOT materialize the
       whole archive (this module never buffers more than the funding
       events dict plus the compact output).
    3. Never interpolated, never forward-filled across a gap exceeding 2
       minutes (D§5.5 rule 2, M27) — beyond that, the event is left
       UNPRICED (`NaN`), which under §7.6 makes the frozen ENGINE raise
       `FundingDataError` rather than silently mis-price; this layer's job
       is only to refuse to fabricate a price, not to raise itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

from ..base import ensure_utc_timestamp

__all__ = ["AssetCtxsRow", "extract_oracle_prices_for_events", "GAP_TOLERANCE"]

# D§5.5 rule 2 — "never forward-filled across a gap exceeding 2 minutes".
GAP_TOLERANCE = pd.Timedelta(minutes=2)


@dataclass(frozen=True)
class AssetCtxsRow:
    """One minute-resolution `asset_ctxs` record (F12 columns, oracle-price
    subset only — this layer has no use for `open_interest`/`premium`/etc.).
    """

    time: pd.Timestamp  # minute-resolution, tz-aware UTC
    coin: str
    oracle_px: float


def _parse_row(raw: dict) -> AssetCtxsRow:
    """Parses one raw `asset_ctxs` CSV row (as a dict with at least `time`,
    `coin`, `oracle_px` keys — the real archive's `time` field format is not
    pinned by any verified fact in D§1.1/D§1.2, so this accepts either an
    epoch-ms int/str or an already-tz-aware `pd.Timestamp`, and REJECTS a
    naive timestamp per D§3.1.1, never silently localizing it).
    """
    t = raw["time"]
    if isinstance(t, pd.Timestamp):
        ts = ensure_utc_timestamp(t)
    else:
        # epoch milliseconds, per F12's stated 1-minute granularity.
        ts = ensure_utc_timestamp(pd.Timestamp(int(t), unit="ms", tz="UTC"))
    return AssetCtxsRow(time=ts, coin=str(raw["coin"]), oracle_px=float(raw["oracle_px"]))


def extract_oracle_prices_for_events(
    asset_ctxs_stream: Iterable[dict],
    funding_events: pd.DataFrame,
) -> pd.DataFrame:
    """D§5.5/D§5.5.1 — streams `asset_ctxs_stream` (an iterable of raw row
    dicts — a MOCK in every test here, since the real archive download is
    not authorized) and joins each `funding_events` row to the oracle price
    at the CONTAINING minute: the last `asset_ctxs` row with
    `ctx_minute <= event_timestamp`, per symbol.

    `funding_events` MUST have `timestamp` (tz-aware UTC) and `symbol`
    columns (i.e. a normalized funding frame, D§5.1 — `notional_price` is
    ignored if present).

    Returns the COMPACT artifact `{timestamp, symbol, oracle_price}` ONLY —
    one row per funding event, `oracle_price` is `NaN` where no `asset_ctxs`
    row within `GAP_TOLERANCE` (2 minutes) precedes the event (M27: never
    forward-filled across a wider gap).

    This function does NOT retain the full `asset_ctxs_stream` content
    (M41): it keeps only, per symbol, the single most-recent-so-far row
    needed to answer the NEXT event in timestamp order — memory use is
    O(events + symbols), never O(archive rows).
    """
    if funding_events.empty:
        return pd.DataFrame(columns=["timestamp", "symbol", "oracle_price"])

    # Group events per symbol, sorted by time, so a single forward pass over
    # the (assumed time-ordered) asset_ctxs stream can serve all of them
    # without buffering the stream.
    events_by_symbol: dict = {}
    for symbol, g in funding_events.groupby("symbol", sort=False):
        events_by_symbol[symbol] = sorted(g["timestamp"].tolist())

    # last-seen oracle row per symbol as we scan forward (bounded state,
    # never the full row history -- M41).
    last_row_by_symbol: dict = {}
    results: dict = {}  # (symbol, event_ts) -> price or None

    pending = {sym: list(ts_list) for sym, ts_list in events_by_symbol.items()}

    for raw in asset_ctxs_stream:
        row = _parse_row(raw)
        sym = row.coin
        if sym not in pending or not pending[sym]:
            last_row_by_symbol[sym] = row
            continue
        # Resolve any pending events for this symbol whose timestamp has now
        # been passed (row.time > event_ts means we've moved past it; the
        # LAST row with ctx_minute <= event_ts is the answer).
        while pending[sym] and pending[sym][0] < row.time:
            event_ts = pending[sym].pop(0)
            prior = last_row_by_symbol.get(sym)
            results[(sym, event_ts)] = _resolve(prior, event_ts)
        if pending[sym] and pending[sym][0] == row.time:
            event_ts = pending[sym].pop(0)
            results[(sym, event_ts)] = row.oracle_px
        last_row_by_symbol[sym] = row

    # Anything still pending ran off the end of the stream: resolve against
    # the last-seen row (if within tolerance) or leave unpriced.
    for sym, remaining in pending.items():
        for event_ts in remaining:
            prior = last_row_by_symbol.get(sym)
            results[(sym, event_ts)] = _resolve(prior, event_ts)

    out_rows = []
    for symbol, ts_list in events_by_symbol.items():
        for ts in ts_list:
            out_rows.append({"timestamp": ts, "symbol": symbol, "oracle_price": results.get((symbol, ts))})
    out = pd.DataFrame(out_rows)
    out["symbol"] = out["symbol"].astype("string")
    out["oracle_price"] = out["oracle_price"].astype("float64")
    return out.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)


def _resolve(prior: Optional[AssetCtxsRow], event_ts: pd.Timestamp) -> Optional[float]:
    if prior is None:
        return None
    gap = event_ts - prior.time
    if gap < pd.Timedelta(0):
        return None  # prior row is AFTER the event -- not a valid "containing minute" match.
    if gap > GAP_TOLERANCE:
        return None  # D§5.5 rule 2 (M27) — never forward-filled across a >2-minute gap.
    return prior.oracle_px
