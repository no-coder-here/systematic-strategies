#!/usr/bin/env python3
"""QR-PREP-001 repair-cycle-2, D3 — explicit, narrow reconciliation of ANY
conflicting bar (interior or trailing) in a symbol's persisted 1h archive,
against a fresh full-window `candleSnapshot` fetch.

Why this exists in addition to `hyperliquid_repair_stale_trailing_bar.py`
(D2): D2 can only ever repair the TRAILING (max-timestamp) row of the
currently persisted frame. A bar written while unclosed during an EARLIER
ingest pass, then buried as an interior row by later refreshes appending
newer bars, is permanently unreachable by D2 no matter how many times it is
invoked.

This is NOT a general overwrite/force mode and is NEVER invoked by
`scripts/hyperliquid_ohlcv_ingest.py` or `scripts/hyperliquid_repair_stale_trailing_bar.py`,
or any normal refresh. It:
  - MUST be explicitly invoked, per symbol, via a non-empty `--symbols` list
    (there is no "repair everything" default -- an empty/missing list is a
    hard error);
  - can only ever replace VALUES at keys that already exist in the persisted
    frame -- it never adds or removes a row, and never changes row count or
    `min(timestamp)` (`_assert_no_regression` enforces this independently on
    every call, exactly as it does for D2 and for every normal write);
  - only replaces a row if a genuine conflict is detected against a fresh
    `candleSnapshot` fetch AND the fresh value is now provably CLOSED
    (`timestamp + 1h <= now`) — never trades a closed value for an unclosed
    one;
  - prints exactly which `(symbol, timestamp)` rows were replaced, with the
    old and new values, and writes the same to a JSON report for the run
    record -- a SEPARATE report file from D2's `_d2_repair_report.json`,
    which this script never reads or overwrites;
  - NEVER treats a fresh value as authoritative if it looks like a
    rolling-window left-edge placeholder (persisted bar shows genuine
    trading, fresh bar reports `volume==0` and `trade_count==0`) rather than
    a genuine re-quote (repair-cycle-3 fix) -- the persisted value wins, and
    every such refusal is printed and written to the report as a distinct
    `refused` count/list, never silently dropped. A nonzero count is
    information about rolling-window degradation, not an error.

Usage:
    python scripts/hyperliquid_reconcile_conflicting_bars.py --symbols BTC,ETH,...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from data.hyperliquid.client import HyperliquidClient  # noqa: E402
from data.hyperliquid.ingest import reconcile_conflicting_bars  # noqa: E402
from data.hyperliquid.provider import HyperliquidProvider  # noqa: E402
from data.rate_limit import RateLimiter  # noqa: E402


def _jsonable(value):
    try:
        import pandas as pd

        if isinstance(value, pd.Timestamp):
            return value.isoformat()
    except Exception:  # noqa: BLE001
        pass
    # `old_row`/`new_row` values come from a mixed-dtype DataFrame row, so
    # numeric/bool fields (`trade_count`, `native_traded`, ...) are numpy
    # scalar types (e.g. numpy.int64, numpy.bool_) which json.dumps cannot
    # serialize directly -- unwrap to the native Python type via `.item()`.
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            pass
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    ap.add_argument(
        "--symbols",
        required=True,
        help=(
            "REQUIRED, comma-separated, explicit symbol list. There is deliberately no "
            "'repair everything' default (QR-PREP-001 D3): this tool must be narrowly, "
            "explicitly targeted at named symbols, never run implicitly."
        ),
    )
    ap.add_argument("--min-interval-seconds", type=float, default=0.08)
    ap.add_argument(
        "--report-path",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "metadata", "hyperliquid",
            "_bar_reconciliation_report.json",
        ),
        help="Separate from D2's _d2_repair_report.json; this script never reads or overwrites that file.",
    )
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("[error] --symbols must name at least one symbol; refusing to run with an empty list (QR-PREP-001 D3).")
        return 2

    rate_limiter = RateLimiter(args.min_interval_seconds)
    client = HyperliquidClient(
        rate_limiter=rate_limiter, max_retries=4, backoff_base_seconds=0.5, timeout_seconds=30.0
    )
    provider = HyperliquidProvider(client=client)

    reconciled_out: dict = {}
    refused_out: dict = {}
    skipped: dict = {}
    errors: dict = {}
    t0 = time.time()
    for i, symbol in enumerate(symbols, start=1):
        try:
            r = reconcile_conflicting_bars(provider, args.data_dir, symbol)
            if r.refused:
                refused_out[symbol] = {
                    "rows_checked": r.rows_checked,
                    "bars": [
                        {
                            "timestamp": _jsonable(rb.timestamp),
                            "old": {k: _jsonable(v) for k, v in rb.old_row.items()},
                            "new": {k: _jsonable(v) for k, v in rb.new_row.items()},
                        }
                        for rb in r.refused
                    ],
                }
                print(
                    f"[{i}/{len(symbols)}] REFUSED (placeholder downgrade) {symbol}: "
                    f"{len(r.refused)} bar(s) kept as persisted (rows_checked={r.rows_checked})"
                )
                for rb in r.refused:
                    print(f"    {symbol} @ {rb.timestamp}: persisted kept={rb.old_row} rejected_fresh={rb.new_row}")
            if r.reconciled:
                reconciled_out[symbol] = {
                    "rows_checked": r.rows_checked,
                    "bars": [
                        {
                            "timestamp": _jsonable(rb.timestamp),
                            "old": {k: _jsonable(v) for k, v in rb.old_row.items()},
                            "new": {k: _jsonable(v) for k, v in rb.new_row.items()},
                        }
                        for rb in r.reconciled
                    ],
                }
                print(
                    f"[{i}/{len(symbols)}] RECONCILED {symbol}: {len(r.reconciled)} bar(s) "
                    f"(rows_checked={r.rows_checked})"
                )
                for rb in r.reconciled:
                    print(f"    {symbol} @ {rb.timestamp}: old={rb.old_row} new={rb.new_row}")
            if not r.reconciled and not r.refused:
                skipped[symbol] = {"reason": r.reason, "rows_checked": r.rows_checked}
                print(f"[{i}/{len(symbols)}] skipped {symbol}: {r.reason} (rows_checked={r.rows_checked})")
        except Exception as exc:  # noqa: BLE001 - reported per-symbol, never swallowed silently
            errors[symbol] = f"{type(exc).__name__}: {exc}"
            print(f"[{i}/{len(symbols)}] ERROR {symbol}: {exc}")

    elapsed = time.time() - t0
    total_bars_reconciled = sum(len(v["bars"]) for v in reconciled_out.values())
    total_bars_refused = sum(len(v["bars"]) for v in refused_out.values())
    report = {
        "symbols_requested": symbols,
        "reconciled": reconciled_out,
        "refused": refused_out,
        "skipped": skipped,
        "errors": errors,
        "total_bars_reconciled": total_bars_reconciled,
        "total_bars_refused": total_bars_refused,
        "elapsed_seconds": elapsed,
    }
    os.makedirs(os.path.dirname(args.report_path), exist_ok=True)
    with open(args.report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(
        f"\n[done] {len(reconciled_out)}/{len(symbols)} symbols had bars reconciled "
        f"({total_bars_reconciled} bar(s) total), {len(refused_out)}/{len(symbols)} symbols had "
        f"bars REFUSED as placeholder downgrades ({total_bars_refused} bar(s) total; the persisted "
        "value was kept in every case), "
        f"{len(skipped)} skipped (nothing to do), "
        f"{len(errors)} errors, {elapsed:.1f}s elapsed."
    )
    if reconciled_out:
        print("[done] reconciled bars (symbol @ timestamp):")
        for sym, info in reconciled_out.items():
            for bar in info["bars"]:
                print(f"  {sym} @ {bar['timestamp']}")
    if refused_out:
        print("[done] REFUSED placeholder-downgrade bars (symbol @ timestamp) -- persisted value kept:")
        for sym, info in refused_out.items():
            for bar in info["bars"]:
                print(f"  {sym} @ {bar['timestamp']}")
    print(f"[done] report written to {args.report_path}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
