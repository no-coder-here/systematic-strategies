"""QR-PREP-001 P§1 — Hyperliquid native 1h persistence + incremental refresh.

Implements `docs/qr_prep_001_spec.md` P§1 ONLY (P§2-P§5 are out of scope for
this module; do not extend it to cover them). This module owns the
"accreting archive" WRITE semantics on top of the existing,
frozen-contract-compliant `HyperliquidProvider.get_ohlcv` / `.get_universe`
path (`src/data/hyperliquid/provider.py`, D§2.2/D§4/D§6). It adds NO new HTTP
client and NO new `candleSnapshot` request logic; `provider.py` remains the
SOLE `candleSnapshot` caller (P§1.2) — this module MUST NOT import
`node_trades`/`node_fills`/`node_fills_by_block`/L2 book/`asset_ctxs` code
(P§1.7's source-restriction test enforces this by static AST scan, mirroring
`tests/data/test_layering.py`'s D§2.1 methodology).

Why an accreting archive (P§1.3)
---------------------------------
`candleSnapshot` serves a ROLLING ~209-day window (`docs/data_contract.md`
D§1 F1/F2): a plain "refresh = re-fetch and overwrite" pipeline would let the
persisted dataset's start silently walk FORWARD in time every time it is
refreshed, permanently losing history that can never be recovered from the
venue again. The persisted 1h OHLCV parquet is therefore treated as an
accreting archive, never a mirror of the current API response:

    1. load the existing persisted frame for the symbol, if any
    2. fetch the currently-available window from the live API
       (`HyperliquidProvider.get_ohlcv`, unchanged)
    3. UNION keyed `(symbol, timestamp)` -- EXISTING ROWS WIN on conflict;
       a value collision on an already-persisted key is COUNTED, never
       silently overwritten (`accrete_ohlcv`)
    4. REFUSE the write (raise, leave the prior parquet file untouched) if
       the union would reduce the symbol's row count, or move its
       `min(timestamp)` LATER than what is already persisted
       (`_assert_no_regression`)

Point 4 is enforced TWICE, independently, so the failure mode is impossible
rather than merely unlikely (P§1.3.4's own wording): `accrete_ohlcv` is
structurally incapable of dropping an existing row (every existing row is
carried into the union unconditionally; only genuinely new keys are added),
and `_assert_no_regression` is a second, independent check of the same
invariant against the ACTUAL computed union, run unconditionally before
every write, so a future bug in the union logic cannot silently defeat the
guarantee.

Entry point (P§1.5)
--------------------
`ingest_symbol_1h()` is the ONE entry point, with two modes SELECTED
AUTOMATICALLY per symbol:

    - FULL BACKFILL: nothing is persisted yet for the symbol (or
      `force_full_backfill=True`). Fetches from `_FULL_BACKFILL_ANCHOR`
      (predates any known Hyperliquid history) to `now` -- the bounded,
      backwards-walking `candleSnapshot` pagination inside `provider.py`
      (D§4.2) naturally stops wherever the venue's retained window actually
      begins; the anchor is NEVER trusted as "the true start of history".
    - REFRESH: a persisted frame already exists. Fetches only from
      `last_persisted_timestamp - 3 bars` to `now` (P§1.5's own wording),
      then unions per the rule above. Safe to call repeatedly: a refresh
      that finds no new bars and no conflicting values leaves the persisted
      parquet file BYTE-FOR-BYTE UNTOUCHED (it is simply not rewritten,
      rather than rewritten to identical content) -- a true content no-op,
      per P§1.5's explicit requirement and P§1.7's idempotency test. The
      provenance sidecar IS still refreshed on every call so `retrieved_at`
      reflects the latest fetch attempt (P§1.4), independently of whether
      the parquet payload itself changed.

Never persisting an unclosed bar (repair-cycle-1 fix, D1)
-----------------------------------------------------------
Every fetched frame is filtered by `_drop_unclosed_bars()` BEFORE accretion:
a bar is only eligible to be persisted once its full hour has fully elapsed
(`timestamp + 1h <= now`). `candleSnapshot` always includes the current,
in-progress bar with partial volume/trade_count/close; persisting it would
let P§1.3's existing-wins rule freeze that partial value forever once a
later refresh re-fetches the now-completed bar under the same
`(symbol, timestamp)` key -- existing-wins is correct and stays unchanged,
so the only fix is to never let an unclosed value become "existing" in the
first place. A conflict detected AFTER this fix is therefore a genuine venue
anomaly, not a routine artifact of refresh timing, and callers (the ingest
script) surface a nonzero conflict count prominently rather than as routine
progress output.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from backtest.models import DataIntegrityError

from .. import storage
from ..provenance import HyperliquidDatasetProvenance, PROCESSING_VERSION, current_code_version
from .provider import HyperliquidProvider

__all__ = [
    "AccretionRegressionError",
    "SymbolIngestResult",
    "TrailingBarRepairResult",
    "ReconciledBar",
    "BarReconciliationResult",
    "accrete_ohlcv",
    "ingest_symbol_1h",
    "repair_trailing_unclosed_bar",
    "reconcile_conflicting_bars",
]

FREQUENCY = "1h"
_ONE_BAR = pd.Timedelta(hours=1)

# Predates any known Hyperliquid history (matches `provider.py`'s own
# `_NATIVE_ANCHOR`); a full backfill requests from here so that the bounded,
# backwards-walking pagination (D§4.2) naturally stops wherever the venue's
# retained rolling window actually begins -- it is NOT trusted as "the true
# start of history" (that would repeat F1's own trap one level up).
_FULL_BACKFILL_ANCHOR = pd.Timestamp("2019-01-01", tz="UTC")

# Columns compared to detect a conflicting value at an already-persisted key
# (P§1.3.3). `dataset_id`/`source_venue`/`native_or_proxy`/`source_type` are
# deliberately EXCLUDED: those are constant per (frequency, symbol) and never
# meaningfully "conflict" the way a re-quoted OHLCV value would.
_COMPARE_COLS = ("open", "high", "low", "close", "volume", "trade_count", "native_traded")


class AccretionRegressionError(DataIntegrityError):
    """P§1.3.4 (blocking) -- raised, REFUSING the write and leaving the prior
    persisted parquet file untouched, when accreting a freshly fetched frame
    onto the existing persisted frame would reduce a symbol's row count or
    move its `min(timestamp)` LATER than what is already on disk. This is
    exactly the defect that would silently walk native history forward and
    invalidate a sealed OOS window (methodology M§9.9); it MUST be
    impossible, not merely unlikely.
    """


@dataclass(frozen=True)
class SymbolIngestResult:
    """Per-symbol outcome of one `ingest_symbol_1h()` call. Feeds directly
    into the P§1.6 summary artifact (`start`, `end`, `rows`, `conflicts`).
    """

    symbol: str
    mode: str  # "full_backfill" | "refresh"
    rows: int
    conflicts: int
    start: Optional[pd.Timestamp]  # None iff `rows == 0` (symbol has no native history at all)
    end: Optional[pd.Timestamp]
    retrieved_at: pd.Timestamp
    parquet_written: bool  # False on a true content no-op refresh (P§1.5)


def _scalar_equal(a, b) -> bool:
    try:
        a_na = pd.isna(a)
        b_na = pd.isna(b)
        if a_na and b_na:
            return True
        if a_na or b_na:
            return False
    except (TypeError, ValueError):
        pass
    return a == b


def accrete_ohlcv(existing: Optional[pd.DataFrame], fetched: pd.DataFrame) -> "tuple[pd.DataFrame, int]":
    """P§1.3 -- union `existing` and `fetched` keyed `(symbol, timestamp)`.

    EXISTING rows win on conflict, unconditionally: every row already in
    `existing` is carried into the result AS-IS; only keys present in
    `fetched` but ABSENT from `existing` are added. This is what makes the
    "existing rows can never be dropped or altered" guarantee structural
    rather than merely a matter of getting a merge rule right.

    Returns `(union_frame, conflict_count)`. A "conflict" is a key present in
    BOTH frames whose OHLCV/`native_traded` values (`_COMPARE_COLS`) differ;
    an identical overlapping bar is not counted as a conflict (nothing was at
    risk of being silently overwritten -- the two sources already agree).
    """
    if existing is None or len(existing) == 0:
        out = fetched.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
        return out, 0
    if fetched is None or len(fetched) == 0:
        out = existing.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
        return out, 0

    existing_keys = pd.MultiIndex.from_arrays([existing["symbol"], existing["timestamp"]])
    fetched_keys = pd.MultiIndex.from_arrays([fetched["symbol"], fetched["timestamp"]])

    if existing_keys.duplicated().any():
        raise DataIntegrityError("accrete_ohlcv: duplicate (symbol, timestamp) key within `existing` frame")
    if fetched_keys.duplicated().any():
        raise DataIntegrityError("accrete_ohlcv: duplicate (symbol, timestamp) key within `fetched` frame")

    existing_by_key = existing.set_index(existing_keys)
    fetched_by_key = fetched.set_index(fetched_keys)

    common_keys = existing_by_key.index.intersection(fetched_by_key.index)
    conflicts = 0
    for key in common_keys:
        e_row = existing_by_key.loc[key]
        f_row = fetched_by_key.loc[key]
        if not all(_scalar_equal(e_row[c], f_row[c]) for c in _COMPARE_COLS):
            conflicts += 1

    new_only_mask = ~fetched_by_key.index.isin(existing_by_key.index)
    new_rows = fetched_by_key.loc[new_only_mask]

    union = pd.concat([existing_by_key, new_rows], ignore_index=True)
    union = union.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    return union, conflicts


def _assert_no_regression(existing: Optional[pd.DataFrame], union: pd.DataFrame, symbol: str) -> None:
    """P§1.3.4 -- independent guard, checked immediately before every write,
    IN ADDITION TO (not instead of) `accrete_ohlcv`'s structural
    existing-rows-always-retained property. Both must hold for the
    regression to be impossible rather than merely unlikely.
    """
    if existing is None or len(existing) == 0:
        return
    existing_rows = len(existing)
    union_rows = len(union)
    if union_rows < existing_rows:
        raise AccretionRegressionError(
            f"{symbol}: accretion would REDUCE row count {existing_rows} -> {union_rows} "
            "(P§1.3.4); refusing write, prior file left untouched."
        )
    existing_min = existing["timestamp"].min()
    union_min = union["timestamp"].min()
    if union_min > existing_min:
        raise AccretionRegressionError(
            f"{symbol}: accretion would move min(timestamp) LATER {existing_min} -> {union_min} "
            "(P§1.3.4); refusing write, prior file left untouched."
        )


def _try_read_existing(base_dir, symbol: str) -> Optional[pd.DataFrame]:
    path = storage.ohlcv_parquet_path(base_dir, FREQUENCY, symbol)
    if not path.exists():
        return None
    return storage.read_ohlcv_parquet(base_dir, FREQUENCY, symbol, check_provenance=False)


def _build_provenance(
    symbol: str,
    union: pd.DataFrame,
    fetch_start: pd.Timestamp,
    now: pd.Timestamp,
    conflicts: int,
    parquet_written: bool,
) -> HyperliquidDatasetProvenance:
    accretion_note = (
        "This dataset is an ACCRETING ARCHIVE of a rolling source "
        "(candleSnapshot serves ~209 days, docs/data_contract.md D§1 F1/F2), "
        "NOT a mirror of the current API response, and therefore may extend "
        "earlier than any single API response. Existing persisted rows win "
        f"on conflict (P§1.3); conflicts_this_run={conflicts}; "
        f"parquet_rewritten_this_run={parquet_written}."
    )
    return HyperliquidDatasetProvenance(
        dataset_id=storage.ohlcv_dataset_id(FREQUENCY, symbol),
        source_venue="Hyperliquid",
        source_type="ohlcv",
        native_or_proxy="native",
        retrieved_at=now,
        start_timestamp=union["timestamp"].min(),
        end_timestamp=union["timestamp"].max(),
        symbols=(symbol,),
        frequency=FREQUENCY,
        processing_version=PROCESSING_VERSION,
        endpoint="candleSnapshot",
        request_windows=((int(fetch_start.timestamp() * 1000), int(now.timestamp() * 1000)),),
        api_response_count=1,
        code_version=current_code_version(),
        notes=accretion_note,
    )


def _drop_unclosed_bars(fetched: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Repair-cycle-1 fix (D1) -- a bar is CLOSED only once its full hour has
    fully elapsed (`timestamp + 1h <= now`). `candleSnapshot` happily returns
    the CURRENT, in-progress bar with partial volume/trade_count/close; if
    that row were persisted, P§1.3's existing-wins rule (which MUST stay, to
    protect the accreting archive) would freeze the partial value forever the
    next time a refresh re-fetches the now-completed bar under the same
    `(symbol, timestamp)` key. Never persist a bar that has not closed --
    dropped here, unconditionally, before accretion ever sees it.
    """
    if len(fetched) == 0:
        return fetched
    closed_mask = fetched["timestamp"] + _ONE_BAR <= now
    return fetched.loc[closed_mask].reset_index(drop=True)


def ingest_symbol_1h(
    provider: HyperliquidProvider,
    base_dir,
    symbol: str,
    *,
    force_full_backfill: bool = False,
    now: Optional[pd.Timestamp] = None,
) -> SymbolIngestResult:
    """P§1.5 -- the ONE entry point, two modes chosen automatically (see
    module docstring). Uses `provider.get_ohlcv` unchanged (P§1.2); this
    function's own job is exclusively the accreting-archive WRITE path.
    """
    if now is None:
        now = pd.Timestamp.utcnow()
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
    elif now.tzinfo is None:
        raise DataIntegrityError("`now` must be tz-aware UTC (D§3.1.1)")

    existing = _try_read_existing(base_dir, symbol)

    if existing is None or len(existing) == 0 or force_full_backfill:
        mode = "full_backfill"
        fetch_start = _FULL_BACKFILL_ANCHOR
    else:
        mode = "refresh"
        # P§1.5: "fetch from last_persisted_timestamp - 3 bars to now".
        fetch_start = existing["timestamp"].max() - 3 * _ONE_BAR

    fetched = provider.get_ohlcv([symbol], FREQUENCY, fetch_start, now)
    if len(fetched):
        fetched = fetched.loc[fetched["symbol"] == symbol].reset_index(drop=True)
        fetched = _drop_unclosed_bars(fetched, now)

    union, conflicts = accrete_ohlcv(existing, fetched)
    _assert_no_regression(existing, union, symbol)

    if len(union) == 0:
        # Genuinely no native history exists yet for this symbol (e.g. a
        # brand-new listing with no traded bars, or a symbol whose entire
        # history is pre-listing backfill quarantined by D§4.4) AND nothing
        # was persisted before either. Nothing to persist; nothing is
        # written. This is NOT the P§1.3.4 regression case (there is no
        # prior file to regress against).
        return SymbolIngestResult(
            symbol=symbol,
            mode=mode,
            rows=0,
            conflicts=conflicts,
            start=None,
            end=None,
            retrieved_at=now,
            parquet_written=False,
        )

    if existing is None or len(existing) == 0:
        parquet_written = True
    else:
        existing_sorted = existing.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
        parquet_written = storage.stable_frame_hash(existing_sorted) != storage.stable_frame_hash(union)

    provenance = _build_provenance(symbol, union, fetch_start, now, conflicts, parquet_written)

    if parquet_written:
        storage.write_ohlcv_parquet(base_dir, FREQUENCY, symbol, union, provenance)
    else:
        # P§1.5's no-op requirement / P§1.7's idempotency test: the parquet
        # payload is IDENTICAL, so it is left byte-for-byte untouched rather
        # than rewritten to the same content. The provenance sidecar is
        # still refreshed so `retrieved_at` (P§1.4) reflects this fetch
        # attempt.
        storage.write_provenance(base_dir, provenance)

    return SymbolIngestResult(
        symbol=symbol,
        mode=mode,
        rows=len(union),
        conflicts=conflicts,
        start=union["timestamp"].min(),
        end=union["timestamp"].max(),
        retrieved_at=now,
        parquet_written=parquet_written,
    )


# ---------------------------------------------------------------------------
# D2 repair (QR-PREP-001 repair-cycle-1) -- explicit, narrow correction of a
# bar that was persisted WHILE UNCLOSED under the pre-D1-fix code, and can
# therefore never self-correct under P§1.3's (correct, and unchanged)
# existing-wins rule.
#
# Scope is provably narrow:
#   - at most ONE row per symbol per call is ever eligible: the TRAILING
#     (max-timestamp) row of the CURRENTLY persisted frame. No interior or
#     older row is ever considered. This is not an arbitrary restriction: of
#     any historical write, only its trailing bar could ever have been
#     unclosed at write time (every earlier bar in that same fetch is, by
#     construction, older and therefore already closed).
#   - the row is only replaced if a genuine CONFLICT is detected (a fresh
#     `candleSnapshot` value at that exact timestamp disagrees with what is
#     persisted, by the same `_COMPARE_COLS` rule `accrete_ohlcv` itself
#     uses) -- an agreeing re-fetch changes nothing.
#   - the replacement value is only accepted if it is now provably CLOSED
#     (`timestamp + 1h <= now`); a still-open replacement is refused, so this
#     path can never trade one unclosed value for another.
#
# This function is NEVER called by `ingest_symbol_1h` or by any normal
# refresh path (`scripts/hyperliquid_ohlcv_ingest.py` does not import it). It
# must be explicitly invoked per symbol, by name
# (`scripts/hyperliquid_repair_stale_trailing_bar.py`, which itself refuses
# to run without an explicit, non-empty `--symbols` list -- there is no
# "repair everything" default). This is deliberately NOT a general
# overwrite/force mode: it cannot touch any row other than the single
# trailing row it identifies, and it never changes row count or
# `min(timestamp)` (`_assert_no_regression` is still run, as an independent
# check, even though this path never adds/removes a key by construction).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrailingBarRepairResult:
    """Outcome of one `repair_trailing_unclosed_bar()` call."""

    symbol: str
    repaired: bool
    reason: str  # human-readable outcome, always set, even when repaired=True ("repaired")
    timestamp: Optional[pd.Timestamp]  # the trailing bar's timestamp; None iff no existing data at all
    old_row: Optional[dict]  # _COMPARE_COLS -> persisted value, only set when repaired=True
    new_row: Optional[dict]  # _COMPARE_COLS -> replacement value, only set when repaired=True


def repair_trailing_unclosed_bar(
    provider: HyperliquidProvider,
    base_dir,
    symbol: str,
    *,
    now: Optional[pd.Timestamp] = None,
) -> TrailingBarRepairResult:
    """QR-PREP-001 D2 -- explicit, narrow repair. See module-level comment
    immediately above for the full eligibility rule. MUST be invoked
    explicitly, per symbol; never wired into `ingest_symbol_1h` or any
    refresh loop.
    """
    if now is None:
        now = pd.Timestamp.utcnow()
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
    elif now.tzinfo is None:
        raise DataIntegrityError("`now` must be tz-aware UTC (D§3.1.1)")

    existing = _try_read_existing(base_dir, symbol)
    if existing is None or len(existing) == 0:
        return TrailingBarRepairResult(symbol, False, "no_existing_data", None, None, None)

    existing_sorted = existing.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    trailing_ts = existing_sorted["timestamp"].max()
    trailing_row = existing_sorted.loc[existing_sorted["timestamp"] == trailing_ts].iloc[0]

    fetched = provider.get_ohlcv([symbol], FREQUENCY, trailing_ts, now)
    if len(fetched):
        fetched = fetched.loc[fetched["symbol"] == symbol].reset_index(drop=True)
        fetched_at_ts = fetched.loc[fetched["timestamp"] == trailing_ts]
    else:
        fetched_at_ts = fetched

    if len(fetched_at_ts) == 0:
        return TrailingBarRepairResult(symbol, False, "fetch_returned_no_data_for_trailing_bar", trailing_ts, None, None)

    fresh_row = fetched_at_ts.iloc[0]

    if trailing_ts + _ONE_BAR > now:
        # Refuse: never replace one unclosed value with another.
        return TrailingBarRepairResult(
            symbol, False, "fetched_bar_not_yet_closed_refusing_repair", trailing_ts, None, None
        )

    conflict = not all(_scalar_equal(trailing_row[c], fresh_row[c]) for c in _COMPARE_COLS)
    if not conflict:
        return TrailingBarRepairResult(symbol, False, "no_conflict_nothing_to_repair", trailing_ts, None, None)

    old_row_dict = {c: trailing_row[c] for c in _COMPARE_COLS}
    new_row_dict = {c: fresh_row[c] for c in _COMPARE_COLS}

    repaired_frame = existing_sorted.copy()
    row_mask = repaired_frame["timestamp"] == trailing_ts
    for c in _COMPARE_COLS:
        repaired_frame.loc[row_mask, c] = fresh_row[c]

    # Independent guard (same one every ordinary write goes through): proves
    # this repair never changes row count or min(timestamp), even though it
    # only ever touches one already-existing row's values by construction.
    _assert_no_regression(existing_sorted, repaired_frame, symbol)

    repair_note = (
        f"QR-PREP-001 D2 explicit repair: row (symbol={symbol}, timestamp={trailing_ts}) "
        f"replaced -- old={old_row_dict}; new={new_row_dict}. This row was persisted while "
        "unclosed under pre-D1-fix ingest code and could never self-correct under P§1.3's "
        "existing-wins rule. Applied via repair_trailing_unclosed_bar(), never via a normal "
        "refresh."
    )
    provenance = _build_provenance(symbol, repaired_frame, trailing_ts, now, conflicts=1, parquet_written=True)
    provenance = dataclasses.replace(
        provenance,
        notes=f"{provenance.notes} {repair_note}" if provenance.notes else repair_note,
    )

    storage.write_ohlcv_parquet(base_dir, FREQUENCY, symbol, repaired_frame, provenance)

    return TrailingBarRepairResult(symbol, True, "repaired", trailing_ts, old_row_dict, new_row_dict)


# ---------------------------------------------------------------------------
# Bar reconciliation (QR-PREP-001 repair-cycle-2, D3) -- explicit, narrow
# correction of ANY conflicting persisted bar, not just the trailing one.
#
# Why this exists in addition to `repair_trailing_unclosed_bar` (D2): D2's
# repair path can only ever inspect and replace the TRAILING (max-timestamp)
# row of the currently persisted frame. A bar that was written while unclosed
# during an EARLIER ingest pass, and then buried underneath later-appended
# newer bars by subsequent refreshes, is an INTERIOR row by the time anyone
# looks at it again -- D2 can never reach it, no matter how many times it is
# invoked. This function removes that "trailing-only" restriction while
# keeping every other D2 safety property:
#
#   - it may replace MULTIPLE rows per symbol per call (any number of
#     persisted keys may conflict against a fresh full-window fetch), but
#     ONLY rows that already exist in the persisted frame -- it can never add
#     a new key or remove one (row count is invariant by construction: only
#     values at EXISTING keys are ever mutated in place);
#   - a row is only replaced if a genuine CONFLICT is detected (the same
#     `_COMPARE_COLS` rule `accrete_ohlcv` itself uses) between the persisted
#     value and a freshly fetched `candleSnapshot` value at that exact
#     timestamp -- an agreeing re-fetch changes nothing;
#   - the freshly fetched frame is passed through `_drop_unclosed_bars()`
#     BEFORE any comparison happens (the same D1 guard `ingest_symbol_1h`
#     uses), so an unclosed live value can never even enter the comparison,
#     let alone be written -- this path can never trade a closed persisted
#     value for an unclosed one;
#   - `_assert_no_regression` is still run, as an independent check, even
#     though this path never adds/removes a key by construction (same
#     belt-and-braces posture as D2);
#   - a conflicting fresh bar is NEVER treated as authoritative if it looks
#     like a ROLLING-WINDOW LEFT-EDGE PLACEHOLDER rather than a genuine
#     re-quote (repair-cycle-3 fix): `candleSnapshot` serves a rolling ~209
#     day window (D§1 F1/F2); as a bar's timestamp approaches the window's
#     retreating left edge, the venue has been observed to first degrade it
#     to `volume == 0 and trade_count == 0` before it disappears from the
#     response entirely on a later call. If the PERSISTED bar shows genuine
#     trading (`volume != 0 or trade_count != 0`) and the FRESH bar reports
#     that flat zero-volume/zero-trade shape, the persisted value is kept
#     and the bar is counted as REFUSED (`BarReconciliationResult.refused`),
#     never silently overwritten with a placeholder. The fully-vanished case
#     (fresh bar absent from the response altogether) needs no extra guard:
#     it is already structurally excluded, since `common_keys` is the
#     intersection of persisted and fetched keys, so an absent key is never
#     even a replacement candidate.
#
# This function is NEVER called by `ingest_symbol_1h`, `accrete_ohlcv`, or any
# normal refresh path, and NEVER called by `repair_trailing_unclosed_bar`.
# `scripts/hyperliquid_ohlcv_ingest.py` does not import it. It must be
# explicitly invoked per symbol, by name
# (`scripts/hyperliquid_reconcile_conflicting_bars.py`, which itself refuses
# to run without an explicit, non-empty `--symbols` list -- there is no
# "repair everything" default, mirroring D2). This is deliberately NOT a
# general overwrite/force mode: P§1.3's existing-wins rule is completely
# unchanged for every ordinary write; this is a separate, deliberately
# narrow, explicitly-invoked reconciliation path that only ever mutates
# VALUES at keys that already exist, never row count or `min(timestamp)`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciledBar:
    """One replaced `(symbol, timestamp)` bar, old and new `_COMPARE_COLS` values."""

    timestamp: pd.Timestamp
    old_row: dict
    new_row: dict


@dataclass(frozen=True)
class BarReconciliationResult:
    """Outcome of one `reconcile_conflicting_bars()` call."""

    symbol: str
    reconciled: "tuple[ReconciledBar, ...]"
    rows_checked: int  # number of overlapping (existing, freshly-fetched-and-closed) keys examined
    reason: str  # human-readable outcome, always set
    # repair-cycle-3 fix -- conflicting bars REFUSED because the fresh value
    # looked like a rolling-window left-edge placeholder (persisted bar shows
    # genuine trading, fresh bar reports volume==0 and trade_count==0)
    # rather than a genuine re-quote. A nonzero count here is information
    # about window degradation, not a routine outcome, and MUST be surfaced
    # by every caller (never silently dropped). Defaults to `()` so existing
    # call sites that construct this dataclass positionally are unaffected.
    refused: "tuple[ReconciledBar, ...]" = ()


def _is_left_edge_placeholder_downgrade(e_row, f_row) -> bool:
    """repair-cycle-3 fix -- True iff replacing `e_row` (persisted) with
    `f_row` (freshly fetched) would trade a bar that shows GENUINE trading
    for a rolling-window LEFT-EDGE PLACEHOLDER, rather than a genuine
    re-quote.

    `candleSnapshot` serves a rolling ~209 day window (D§1 F1/F2). As a
    persisted bar's timestamp nears the window's retreating left edge, the
    venue has been observed to first report it back as a flat
    `volume == 0, trade_count == 0` placeholder (and open/high/low/close
    collapsed to a single value) before it disappears from the response
    entirely on some later call. Treating that placeholder as authoritative
    would destroy real, already-persisted trading history.

    Only the PERSISTED side is checked for "genuine trading" (`volume != 0`
    or `trade_count != 0`) -- a persisted bar that is ITSELF a legitimate
    zero-volume no-trade hour (`native_traded=False`, `open==close`) and a
    fresh re-fetch that agrees is not even a candidate here, since an
    agreeing overlap is filtered out by the caller before this predicate is
    ever consulted (nothing was at risk).
    """
    existing_shows_trading = not (
        _scalar_equal(e_row["volume"], 0) and _scalar_equal(e_row["trade_count"], 0)
    )
    fetched_is_placeholder = _scalar_equal(f_row["volume"], 0) and _scalar_equal(f_row["trade_count"], 0)
    return bool(existing_shows_trading and fetched_is_placeholder)


def reconcile_conflicting_bars(
    provider: HyperliquidProvider,
    base_dir,
    symbol: str,
    *,
    now: Optional[pd.Timestamp] = None,
) -> BarReconciliationResult:
    """QR-PREP-001 D3 -- explicit, narrow reconciliation of ANY conflicting
    persisted bar (interior or trailing), not just the trailing row D2 is
    limited to. See the module-level comment immediately above for the full
    eligibility rule. MUST be invoked explicitly, per symbol; never wired
    into `ingest_symbol_1h`, `repair_trailing_unclosed_bar`, or any refresh
    loop.
    """
    if now is None:
        now = pd.Timestamp.utcnow()
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
    elif now.tzinfo is None:
        raise DataIntegrityError("`now` must be tz-aware UTC (D§3.1.1)")

    existing = _try_read_existing(base_dir, symbol)
    if existing is None or len(existing) == 0:
        return BarReconciliationResult(symbol, (), 0, "no_existing_data")

    existing_sorted = existing.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)

    # Fetch the WHOLE currently-available rolling window (same anchor as a
    # full backfill), not merely the trailing few bars -- an interior
    # conflict can sit anywhere within that window, buried under later rows.
    fetched = provider.get_ohlcv([symbol], FREQUENCY, _FULL_BACKFILL_ANCHOR, now)
    if len(fetched):
        fetched = fetched.loc[fetched["symbol"] == symbol].reset_index(drop=True)
        # D1 guard, reused verbatim: an unclosed live value must never even
        # enter the comparison below, let alone be written.
        fetched = _drop_unclosed_bars(fetched, now)

    if fetched is None or len(fetched) == 0:
        return BarReconciliationResult(symbol, (), 0, "fetch_returned_no_data")

    existing_keys = pd.MultiIndex.from_arrays([existing_sorted["symbol"], existing_sorted["timestamp"]])
    fetched_keys = pd.MultiIndex.from_arrays([fetched["symbol"], fetched["timestamp"]])

    if existing_keys.duplicated().any():
        raise DataIntegrityError("reconcile_conflicting_bars: duplicate (symbol, timestamp) key within existing frame")
    if fetched_keys.duplicated().any():
        raise DataIntegrityError("reconcile_conflicting_bars: duplicate (symbol, timestamp) key within fetched frame")

    existing_by_key = existing_sorted.set_index(existing_keys)
    fetched_by_key = fetched.set_index(fetched_keys)

    common_keys = existing_by_key.index.intersection(fetched_by_key.index)

    repaired_frame = existing_sorted.copy()
    reconciled: list = []
    refused: list = []
    for key in common_keys:
        e_row = existing_by_key.loc[key]
        f_row = fetched_by_key.loc[key]
        if all(_scalar_equal(e_row[c], f_row[c]) for c in _COMPARE_COLS):
            continue  # agreeing overlap -- nothing was at risk, not a conflict

        ts = key[1]
        # Defensive re-check of the same invariant `_drop_unclosed_bars`
        # already enforces on `fetched` -- belt-and-braces, never trade a
        # closed persisted value for an unclosed one.
        if ts + _ONE_BAR > now:
            continue

        old_row_dict = {c: e_row[c] for c in _COMPARE_COLS}
        new_row_dict = {c: f_row[c] for c in _COMPARE_COLS}

        # repair-cycle-3 fix -- REFUSE, never apply, a fresh value that looks
        # like a rolling-window left-edge placeholder rather than a genuine
        # re-quote. The persisted value wins; the occurrence is counted and
        # surfaced via `BarReconciliationResult.refused`, never silently
        # skipped.
        if _is_left_edge_placeholder_downgrade(e_row, f_row):
            refused.append(ReconciledBar(timestamp=ts, old_row=old_row_dict, new_row=new_row_dict))
            continue

        row_mask = repaired_frame["timestamp"] == ts
        for c in _COMPARE_COLS:
            repaired_frame.loc[row_mask, c] = f_row[c]

        reconciled.append(ReconciledBar(timestamp=ts, old_row=old_row_dict, new_row=new_row_dict))

    rows_checked = len(common_keys)
    refused_tuple = tuple(refused)

    if not reconciled:
        reason = "refused_placeholder_downgrade" if refused_tuple else "no_conflicts_found"
        return BarReconciliationResult(symbol, (), rows_checked, reason, refused=refused_tuple)

    # Independent guard (same one every ordinary write and D2 repair goes
    # through): proves this reconciliation never changes row count or
    # min(timestamp), even though it only ever mutates values at
    # already-existing keys by construction.
    _assert_no_regression(existing_sorted, repaired_frame, symbol)
    if len(repaired_frame) != len(existing_sorted):
        raise AccretionRegressionError(
            f"{symbol}: reconcile_conflicting_bars would change row count "
            f"{len(existing_sorted)} -> {len(repaired_frame)}; refusing write, prior file left untouched."
        )

    reconciled_tuple = tuple(reconciled)
    refused_note = (
        f" {len(refused_tuple)} additional row(s) were REFUSED (rolling-window left-edge placeholder "
        "downgrade -- persisted genuine-trading value kept, fresh zero-volume/zero-trade value rejected): "
        + "; ".join(
            f"(symbol={symbol}, timestamp={rb.timestamp}) old={rb.old_row} new={rb.new_row}"
            for rb in refused_tuple
        )
        if refused_tuple
        else ""
    )
    repair_note = (
        f"QR-PREP-001 D3 explicit bar reconciliation: {len(reconciled_tuple)} row(s) replaced -- "
        + "; ".join(
            f"(symbol={symbol}, timestamp={rb.timestamp}) old={rb.old_row} new={rb.new_row}"
            for rb in reconciled_tuple
        )
        + ". These rows were persisted while unclosed under pre-D1-fix ingest code, then buried as "
        "interior rows by later refreshes, and could never self-correct under P§1.3's existing-wins "
        "rule nor be reached by the trailing-only D2 repair. Applied via "
        "reconcile_conflicting_bars(), never via a normal refresh."
        + refused_note
    )
    provenance = _build_provenance(
        symbol, repaired_frame, _FULL_BACKFILL_ANCHOR, now, conflicts=len(reconciled_tuple), parquet_written=True
    )
    provenance = dataclasses.replace(
        provenance,
        notes=f"{provenance.notes} {repair_note}" if provenance.notes else repair_note,
    )

    storage.write_ohlcv_parquet(base_dir, FREQUENCY, symbol, repaired_frame, provenance)

    return BarReconciliationResult(symbol, reconciled_tuple, rows_checked, "reconciled", refused=refused_tuple)
