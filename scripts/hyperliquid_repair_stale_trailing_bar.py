#!/usr/bin/env python3
"""QR-PREP-001 repair-cycle-1, D2 — explicit, narrow repair for the trailing
bar of a symbol's persisted 1h archive when it was written WHILE UNCLOSED
under the pre-D1-fix `ingest_symbol_1h` code path, and can therefore never
self-correct under P§1.3's (correct, unchanged) existing-wins rule.

This is NOT a general overwrite/force mode and is NEVER invoked by
`scripts/hyperliquid_ohlcv_ingest.py` or any normal refresh. It:
  - MUST be explicitly invoked, per symbol, via a non-empty `--symbols` list
    (there is no "repair everything" default -- an empty/missing list is a
    hard error);
  - can only ever repair AT MOST ONE row per symbol: the TRAILING
    (max-timestamp) row of the currently persisted frame (see
    `data.hyperliquid.ingest.repair_trailing_unclosed_bar` for why only the
    trailing row is ever eligible);
  - only replaces that row if a genuine conflict is detected against a fresh
    `candleSnapshot` fetch AND the fresh value is now provably CLOSED
    (never trades one unclosed value for another);
  - prints exactly which `(symbol, timestamp)` rows were replaced, with the
    old and new values, and writes the same to a JSON report for the run
    record.

Usage:
    python scripts/hyperliquid_repair_stale_trailing_bar.py --symbols BTC,ETH,...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from data.hyperliquid.client import HyperliquidClient  # noqa: E402
from data.hyperliquid.ingest import repair_trailing_unclosed_bar  # noqa: E402
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
            "'repair everything' default (QR-PREP-001 D2): this tool must be narrowly, "
            "explicitly targeted at named symbols, never run implicitly."
        ),
    )
    ap.add_argument("--min-interval-seconds", type=float, default=0.08)
    ap.add_argument(
        "--report-path",
        default=os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "metadata", "hyperliquid",
            "_d2_repair_report.json",
        ),
    )
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("[error] --symbols must name at least one symbol; refusing to run with an empty list (QR-PREP-001 D2).")
        return 2

    rate_limiter = RateLimiter(args.min_interval_seconds)
    client = HyperliquidClient(
        rate_limiter=rate_limiter, max_retries=4, backoff_base_seconds=0.5, timeout_seconds=30.0
    )
    provider = HyperliquidProvider(client=client)

    repaired: dict = {}
    skipped: dict = {}
    errors: dict = {}
    t0 = time.time()
    for i, symbol in enumerate(symbols, start=1):
        try:
            r = repair_trailing_unclosed_bar(provider, args.data_dir, symbol)
            if r.repaired:
                repaired[symbol] = {
                    "timestamp": _jsonable(r.timestamp),
                    "old": {k: _jsonable(v) for k, v in r.old_row.items()},
                    "new": {k: _jsonable(v) for k, v in r.new_row.items()},
                }
                print(f"[{i}/{len(symbols)}] REPAIRED {symbol} @ {r.timestamp}: old={r.old_row} new={r.new_row}")
            else:
                skipped[symbol] = {"reason": r.reason, "timestamp": _jsonable(r.timestamp)}
                print(f"[{i}/{len(symbols)}] skipped {symbol}: {r.reason} (timestamp={r.timestamp})")
        except Exception as exc:  # noqa: BLE001 - reported per-symbol, never swallowed silently
            errors[symbol] = f"{type(exc).__name__}: {exc}"
            print(f"[{i}/{len(symbols)}] ERROR {symbol}: {exc}")

    elapsed = time.time() - t0
    report = {
        "symbols_requested": symbols,
        "repaired": repaired,
        "skipped": skipped,
        "errors": errors,
        "elapsed_seconds": elapsed,
    }
    os.makedirs(os.path.dirname(args.report_path), exist_ok=True)
    with open(args.report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(
        f"\n[done] {len(repaired)} repaired, {len(skipped)} skipped (nothing to do), "
        f"{len(errors)} errors, out of {len(symbols)} requested symbols, {elapsed:.1f}s elapsed."
    )
    if repaired:
        print("[done] repaired rows (symbol @ timestamp):")
        for sym, info in repaired.items():
            print(f"  {sym} @ {info['timestamp']}")
    print(f"[done] report written to {args.report_path}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
