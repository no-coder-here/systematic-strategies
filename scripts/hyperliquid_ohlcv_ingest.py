#!/usr/bin/env python3
"""QR-PREP-001 P§1 — Hyperliquid native 1h OHLCV accreting-archive ingest.

Implements `docs/qr_prep_001_spec.md` P§1.1-P§1.6 ONLY. One-off/repeatable
operational entry point; adds NO new logic beyond orchestration (universe
scan + per-symbol loop + P§1.6 summary artifact) — all normalization,
`candleSnapshot` pagination and accreting-archive write semantics live in
the unit-tested production code (`data.hyperliquid.provider.HyperliquidProvider`,
`data.hyperliquid.ingest.ingest_symbol_1h`).

Universe (P§1.1): ALL symbols returned by `HyperliquidProvider.get_universe()`,
DELISTED included. Frequency `1h` ONLY (4h/1d stay derived, D§4.5).

Mode selection (P§1.5) is automatic per symbol, inside `ingest_symbol_1h`:
FULL BACKFILL for a symbol with nothing persisted yet, REFRESH otherwise.
`--force-full-backfill` forces backfill mode for every symbol regardless
(e.g. a deliberate from-scratch rebuild).

Usage:
    python scripts/hyperliquid_ohlcv_ingest.py [--data-dir DIR] [--limit N]
        [--symbols BTC,ETH,...] [--force-full-backfill]
        [--min-interval-seconds SEC]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from data.hyperliquid.client import HyperliquidClient  # noqa: E402
from data.hyperliquid.ingest import ingest_symbol_1h  # noqa: E402
from data.hyperliquid.provider import HyperliquidProvider  # noqa: E402
from data.rate_limit import RateLimiter  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    ap.add_argument("--limit", type=int, default=None, help="ingest only the first N universe symbols (debug)")
    ap.add_argument("--symbols", default=None, help="comma-separated explicit symbol list, overrides universe scan")
    ap.add_argument("--force-full-backfill", action="store_true",
                    help="force FULL BACKFILL mode for every symbol, even if already persisted")
    ap.add_argument("--min-interval-seconds", type=float, default=0.08,
                    help="RateLimiter pacing between requests to the public info endpoint")
    args = ap.parse_args()

    rate_limiter = RateLimiter(args.min_interval_seconds)
    client = HyperliquidClient(
        rate_limiter=rate_limiter, max_retries=4, backoff_base_seconds=0.5, timeout_seconds=30.0
    )
    provider = HyperliquidProvider(client=client)

    print("[universe] fetching Hyperliquid universe (P§1.1: ALL symbols, delisted included)...")
    # infer_native_range=False: avoids a redundant 1d fetch per symbol here --
    # `ingest_symbol_1h` -> `provider.get_ohlcv` computes (and caches) the
    # same D§4.4 first_native_1d cutoff itself, per symbol, exactly once.
    universe = provider.get_universe(infer_native_range=False)
    symbols = sorted(universe.symbols.keys())
    if args.symbols:
        wanted = {s.strip() for s in args.symbols.split(",") if s.strip()}
        symbols = [s for s in symbols if s in wanted]
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"[universe] {len(symbols)} symbols to ingest (1h only, P§1.1).")

    results: dict = {}
    errors: dict = {}
    t0 = time.time()
    for i, symbol in enumerate(symbols, start=1):
        try:
            r = ingest_symbol_1h(provider, args.data_dir, symbol, force_full_backfill=args.force_full_backfill)
            results[symbol] = {
                "mode": r.mode,
                "rows": r.rows,
                "conflicts": r.conflicts,
                "start": str(r.start) if r.start is not None else None,
                "end": str(r.end) if r.end is not None else None,
                "parquet_written": r.parquet_written,
            }
            if r.conflicts > 0:
                # Loud on purpose (repair-cycle-1, D1): once unclosed bars can
                # never be persisted, a nonzero conflict count is a genuine
                # venue anomaly, not routine refresh noise -- surface it
                # immediately rather than folding it into periodic progress
                # output only.
                print(
                    f"[{i}/{len(symbols)}] {symbol}: *** CONFLICT *** "
                    f"conflicts={r.conflicts} mode={r.mode} rows={r.rows} written={r.parquet_written}"
                )
            elif i % 20 == 0 or i == len(symbols):
                print(
                    f"[{i}/{len(symbols)}] {symbol}: mode={r.mode} rows={r.rows} "
                    f"conflicts={r.conflicts} written={r.parquet_written}"
                )
        except Exception as exc:  # noqa: BLE001 - reported per-symbol, never swallowed silently
            errors[symbol] = f"{type(exc).__name__}: {exc}"
            print(f"[{i}/{len(symbols)}] {symbol}: ERROR {exc}")

    elapsed = time.time() - t0
    total_rows = sum(r["rows"] for r in results.values())
    total_conflicts = sum(r["conflicts"] for r in results.values())
    starts = [r["start"] for r in results.values() if r["start"] is not None]
    ends = [r["end"] for r in results.values() if r["end"] is not None]

    summary = {
        "symbols_attempted": len(symbols),
        "symbols_succeeded": len(results),
        "symbols_failed": len(errors),
        "total_rows": total_rows,
        "total_conflicts": total_conflicts,
        "earliest_start": min(starts) if starts else None,
        "latest_end": max(ends) if ends else None,
        "elapsed_seconds": elapsed,
        "errors": errors,
    }
    # P§1.6 — per-symbol: start, end, rows, conflicts.
    per_symbol = {
        sym: {"start": r["start"], "end": r["end"], "rows": r["rows"], "conflicts": r["conflicts"]}
        for sym, r in results.items()
    }
    summary_path = os.path.join(args.data_dir, "metadata", "hyperliquid", "_ingest_summary.json")
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump({"summary": summary, "per_symbol": per_symbol}, f, indent=2, sort_keys=True)

    print(
        f"\n[done] {len(results)}/{len(symbols)} symbols succeeded, {total_rows:,} rows, "
        f"{total_conflicts} conflicts, {elapsed:.1f}s elapsed."
    )
    if total_conflicts > 0:
        # Loud on purpose (D1): after the unclosed-bar fix, every conflict is
        # a genuine venue anomaly on an already-persisted key, never routine
        # refresh noise -- this MUST NOT be silent.
        conflicted_symbols = {sym: r["conflicts"] for sym, r in results.items() if r["conflicts"] > 0}
        print(
            f"[done] *** WARNING: {total_conflicts} CONFLICT(S) across "
            f"{len(conflicted_symbols)} symbol(s) -- existing persisted values were "
            "preserved (P§1.3), but this indicates a genuine disagreement with the "
            "venue and should be investigated: " + str(conflicted_symbols)
        )
    if errors:
        print(f"[done] {len(errors)} symbols FAILED: {list(errors.keys())}")
    print(f"[done] summary written to {summary_path}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
