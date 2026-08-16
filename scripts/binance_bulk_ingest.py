#!/usr/bin/env python3
"""D§16.5/D§16.6 — one-off operational script performing the AUTHORIZED
Binance USDⓈ-M perpetual 1h bulk ingestion for every mapped Hyperliquid
symbol (D§16.3's symbol_map.py table).

Recomputes the D§16.5 pre-download gate AT RUNTIME (never trusts the frozen
contract's F14-F16 estimate) and STOPS if it is exceeded. Uses the SAME
production `BinanceUMProvider.ingest_symbol_1h` path that is unit-tested in
`tests/data/test_binance_provider.py` — this script adds no new logic, only
orchestration (symbol list + concurrency + a summary report).

Usage:
    python scripts/binance_bulk_ingest.py [--data-dir DIR] [--workers N] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from data.binance.client import BinanceArchiveError, BinanceClient  # noqa: E402
from data.binance.provider import BinanceUMProvider, check_gate, estimate_gate  # noqa: E402
from data.rate_limit import RateLimiter  # noqa: E402
from data.symbol_map import mapped_symbols  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=None, help="ingest only the first N mapped symbols (debug)")
    args = ap.parse_args()

    symbols = list(mapped_symbols())
    if args.limit:
        symbols = symbols[: args.limit]

    print(f"[gate] recomputing D§16.5 gate at runtime for {len(symbols)} mapped symbols...")
    client = BinanceClient(rate_limiter=RateLimiter(0.02), max_retries=4, backoff_base_seconds=0.5)
    t0 = time.time()
    estimate = estimate_gate(symbols, client, sample_symbol="BTCUSDT")
    print(
        f"[gate] matched={estimate.matched_symbol_count} files={estimate.total_monthly_files} "
        f"est_rows={estimate.estimated_row_count:,} "
        f"est_zip_gb={estimate.estimated_transient_zip_gb:.4f} "
        f"est_parquet_gb={estimate.estimated_processed_parquet_gb:.4f} "
        f"(measured in {time.time()-t0:.1f}s)"
    )
    check_gate(estimate)
    print(f"[gate] PASSED ({estimate.estimated_processed_parquet_gb:.4f} GB <= 5 GB) -- proceeding.")

    results = {}
    errors = {}

    def ingest_one(hl_symbol: str):
        worker_client = BinanceClient(
            rate_limiter=client._rate_limiter, max_retries=4, backoff_base_seconds=0.5, timeout_seconds=30.0
        )
        provider = BinanceUMProvider(client=worker_client, storage_base_dir=args.data_dir)
        prov = provider.ingest_symbol_1h(hl_symbol)
        return hl_symbol, prov

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(ingest_one, s): s for s in symbols}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            done += 1
            try:
                hl_symbol, prov = fut.result()
                n_rows = sum(e["rows"] for e in prov.checksum_manifest_entries)
                results[hl_symbol] = {
                    "binance_symbol": prov.binance_symbol,
                    "rows": n_rows,
                    "files": len(prov.checksum_manifest_entries),
                    "start": str(prov.start_timestamp),
                    "end": str(prov.end_timestamp),
                }
                if done % 20 == 0 or done == len(symbols):
                    print(f"[{done}/{len(symbols)}] {sym}: {n_rows} rows, {len(prov.checksum_manifest_entries)} files")
            except BinanceArchiveError as exc:
                errors[sym] = str(exc)
                print(f"[{done}/{len(symbols)}] {sym}: ERROR {exc}")

    elapsed = time.time() - t0
    total_rows = sum(r["rows"] for r in results.values())
    total_files = sum(r["files"] for r in results.values())

    summary = {
        "symbols_attempted": len(symbols),
        "symbols_succeeded": len(results),
        "symbols_failed": len(errors),
        "total_rows": total_rows,
        "total_files": total_files,
        "elapsed_seconds": elapsed,
        "errors": errors,
    }
    summary_path = os.path.join(args.data_dir, "metadata", "binance", "_ingest_summary.json")
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump({"summary": summary, "per_symbol": results}, f, indent=2, sort_keys=True)

    print(f"\n[done] {len(results)}/{len(symbols)} symbols succeeded, {total_rows:,} rows, "
          f"{total_files} monthly files, {elapsed:.1f}s elapsed.")
    if errors:
        print(f"[done] {len(errors)} symbols FAILED: {list(errors.keys())}")
    print(f"[done] summary written to {summary_path}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
